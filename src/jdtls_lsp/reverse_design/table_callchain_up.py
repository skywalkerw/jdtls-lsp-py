"""step5 / CLI ``--table-callchain-up``：表名 → ServiceImpl / @Entity /（可选）JDBC 字符串 SQL 行 / MyBatis Mapper 方法 → ``trace_call_chain_sync``。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jdtls_lsp.callchain import summarize_trace_up_json, trace_call_chain_sync
from jdtls_lsp.callchain.format import apply_manifest_anchor_to_callchain_markdown
from jdtls_lsp.java_grep import SKIP_DIR_PARTS, java_scan_roots, walk_files_matching
from jdtls_lsp.reverse_design.java_enclosing_method import java_enclosing_method_at_line
from jdtls_lsp.reverse_design.mybatis_mapper_link import resolve_mapper_java_method_from_xml_line
from jdtls_lsp.reverse_design.scan_java_top_level_types import scan_java_top_level_types
from jdtls_lsp.reverse_design.table_manifest import (
    extract_jpa_table_names_from_java,
    normalize_table_token_to_physical,
)

if TYPE_CHECKING:
    from jdtls_lsp.client import LSPClient

from jdtls_lsp.logutil import get_logger

_log = get_logger("reverse_design.table_callchain_up")

_PHYSICAL_TABLE = re.compile(r"^[a-z][a-z0-9_]*$")
_PASCAL_JAVA_TYPE = re.compile(r"^[A-Z][a-zA-Z0-9]+$")
_PUBLIC_LINE = re.compile(r"^\s+public\s+")


def _skip_path(p: Path) -> bool:
    return any(x in p.parts for x in SKIP_DIR_PARTS)


def snake_table_to_entity_class(table: str) -> str:
    """``monitor_data`` → ``MonitorData``。"""
    parts = [x for x in table.split("_") if x]
    return "".join(x[:1].upper() + x[1:] for x in parts)


def _pascal_type_name_to_physical_table(name: str) -> str | None:
    """``AlertRecord`` → ``alert_record``（与常见 Java 实体 / 表名约定一致）。"""
    if not _PASCAL_JAVA_TYPE.match(name):
        return None
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    snake = s2.lower()
    return snake if _PHYSICAL_TABLE.match(snake) else None


def physical_tables_from_canonical(canonical_tables: list[str]) -> list[str]:
    """得到按表跑 step5 的**蛇形物理表名**列表，去重（大小写不敏感）。

    - 已是 ``snake_case`` 的项直接纳入。
    - **PascalCase 类名**（如 manifest 里与蛇形并列的 ``AlertRecord``）会映射为蛇形后纳入，
      与 ``tables-manifest.json`` 中 ``entity_annotation`` 所绑定的物理表对齐，避免「清单里有、step5 没跑」。
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in canonical_tables:
        ts = str(raw).strip()
        candidates: list[str] = []
        if _PHYSICAL_TABLE.match(ts):
            candidates.append(ts)
        derived = _pascal_type_name_to_physical_table(ts)
        if derived:
            candidates.append(derived)
        for c in candidates:
            k = c.casefold()
            if k not in seen:
                seen.add(k)
                out.append(c)
    return out


def _manifest_rows_for_physical_table(manifest: dict[str, Any], physical: str) -> list[dict[str, Any]]:
    """优先 ``anchorsByPhysicalTable``（已按物理表归组、去重）；否则从 ``extractedHits`` 按归一化表名匹配。"""
    phy_cf = physical.casefold()
    abp = manifest.get("anchorsByPhysicalTable")
    if isinstance(abp, dict):
        for k, rows in abp.items():
            if str(k).casefold() != phy_cf:
                continue
            if not isinstance(rows, list):
                return []
            out: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                d = dict(row)
                d["table"] = str(row.get("physicalTable", k))
                out.append(d)
            return out
    eh = manifest.get("extractedHits")
    if not isinstance(eh, list):
        return []
    out2: list[dict[str, Any]] = []
    for h in eh:
        if not isinstance(h, dict):
            continue
        if normalize_table_token_to_physical(str(h.get("table", ""))).casefold() != phy_cf:
            continue
        out2.append(dict(h))
    return out2


def stable_manifest_hit_id(
    physical_table: str,
    source: str,
    rel_file: str,
    line: int,
    *,
    extra: str = "",
) -> str:
    """
    **稳定**锚点编号：由物理表、manifest 来源、文件相对路径、命中行及可选 ``extra`` 派生，
    同一 ``tables-manifest`` 命中在多次 bundle 中保持不变（``ma-`` + 16 位 hex）。
    """
    rel = str(rel_file).replace("\\", "/").strip()
    payload = f"{physical_table.casefold()}|{source}|{rel}|{int(line)}|{extra}".encode("utf-8")
    return "ma-" + hashlib.sha256(payload).hexdigest()[:16]


def _first_manifest_row_by_source(
    manifest: dict[str, Any],
    physical: str,
    source: str,
) -> dict[str, Any] | None:
    for row in _manifest_rows_for_physical_table(manifest, physical):
        if row.get("source") == source:
            return row
    return None


def _java_file_top_level_fqcn(project_root: Path, rel: str) -> str | None:
    """从 ``*.java`` 解析 ``package`` + 首个顶层类型 → 全限定名（含 ``interface``）。"""
    path = (project_root / rel.replace("\\", "/")).resolve()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if text.startswith("\ufeff"):
        text = text[1:]
    pkg_m = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.MULTILINE)
    pkg = (pkg_m.group(1).strip() if pkg_m else "") or ""
    types = scan_java_top_level_types(text)
    if not types:
        return None
    name = str(types[0].get("name", "")).strip()
    if not name:
        return None
    return f"{pkg}.{name}" if pkg else name


def _jdbc_hit_method_simple_name(hit: dict[str, Any]) -> str:
    raw = hit.get("javaMethod")
    s = str(raw).strip() if raw is not None else ""
    if not s or s.startswith("_line"):
        return ""
    return s


def _trace_callchain_up_jdbc_anchor(
    project_root: Path,
    rel: str,
    sql_line: int,
    *,
    java_method: str,
    java_method_line: int,
    jdtls_path: Path | None,
    lsp_client: LSPClient | None,
    max_depth: int,
    fqcn: str | None = None,
) -> tuple[str, str]:
    """
    JDBC 命中行在注解字符串内时常无法 ``prepareCallHierarchy``。
    优先 **类全名 + 包围方法简单名**；失败则 **方法签名行**；再失败则 **SQL 命中行**。

    返回 ``(raw_markdown_or_error, anchor_mode)``，``anchor_mode`` 为
    ``class_method`` | ``file_line_method_signature`` | ``file_line_sql_hit``。
    """
    root_s = str(project_root)
    resolved_fqcn = fqcn if fqcn else _java_file_top_level_fqcn(project_root, rel)

    if resolved_fqcn and java_method:
        raw = trace_call_chain_sync(
            root_s,
            resolved_fqcn,
            java_method,
            file_path=None,
            line=None,
            character=None,
            symbol_query=None,
            jdtls_path=jdtls_path,
            lsp_client=lsp_client,
            max_depth=max_depth,
            output_format="markdown",
        )
        if not raw.startswith("错误:"):
            return raw, "class_method"

    if java_method_line >= 1:
        raw = trace_call_chain_sync(
            root_s,
            None,
            None,
            file_path=rel,
            line=java_method_line,
            character=1,
            symbol_query=None,
            jdtls_path=jdtls_path,
            lsp_client=lsp_client,
            max_depth=max_depth,
            output_format="markdown",
        )
        if not raw.startswith("错误:"):
            return raw, "file_line_method_signature"

    raw = trace_call_chain_sync(
        root_s,
        None,
        None,
        file_path=rel,
        line=sql_line,
        character=1,
        symbol_query=None,
        jdtls_path=jdtls_path,
        lsp_client=lsp_client,
        max_depth=max_depth,
        output_format="markdown",
    )
    return raw, "file_line_sql_hit"


def _dedupe_jdbc_hits_by_java_method(project_root: Path, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 ``*.java`` 内多条 SQL 字符串命中若属同一方法，只保留**行号最小**的一条（代表该方法内最前出现的表引用）。"""
    by_file: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        f = str(h.get("file", "")).replace("\\", "/")
        by_file.setdefault(f, []).append(h)
    out: list[dict[str, Any]] = []
    root = project_root.resolve()
    for rel, group in sorted(by_file.items()):
        path = root / rel
        if not path.is_file():
            out.extend(group)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            out.extend(group)
            continue
        if text.startswith("\ufeff"):
            text = text[1:]
        lines = text.splitlines()
        best_by_bucket: dict[tuple[str, int, str], dict[str, Any]] = {}
        for h in group:
            ln = int(h.get("line") or 0)
            if ln < 1:
                continue
            mline, mname = java_enclosing_method_at_line(lines, ln)
            bucket = (rel, mline, mname)
            prev = best_by_bucket.get(bucket)
            if prev is None or ln < int(prev.get("line") or 999999):
                best_by_bucket[bucket] = h
        out.extend(best_by_bucket.values())
    out.sort(key=lambda x: (str(x.get("file")), int(x.get("line") or 0)))
    return out


def _collect_jdbc_sql_literal_java_hits(
    manifest: dict[str, Any],
    physical: str,
    *,
    project_root: Path | None = None,
    dedupe_by_method: bool = True,
) -> list[dict[str, Any]]:
    """``jdbc_sql_literal`` 且 ``*.java``；默认按**包含 SQL 的 Java 方法**去重。"""
    rows = _manifest_rows_for_physical_table(manifest, physical)
    out: list[dict[str, Any]] = []
    for h in rows:
        if h.get("source") != "jdbc_sql_literal":
            continue
        f = h.get("file")
        if not isinstance(f, str) or not f.endswith(".java"):
            continue
        out.append(h)
    out.sort(key=lambda x: (str(x.get("file")), int(x.get("line") or 0)))
    if dedupe_by_method and project_root is not None and out:
        return _dedupe_jdbc_hits_by_java_method(project_root, out)
    return out


def _collect_mybatis_xml_hits(
    manifest: dict[str, Any],
    physical: str,
    *,
    project_root: Path | None = None,
    dedupe_by_java_method: bool = True,
) -> list[dict[str, Any]]:
    """``mybatis_xml`` / ``mybatis_sql_literal`` 且 ``*.xml``；可选按解析后的 **Mapper 接口方法**去重。"""
    rows = _manifest_rows_for_physical_table(manifest, physical)
    raw: list[dict[str, Any]] = []
    seen_line: set[tuple[str, int]] = set()
    for h in rows:
        if h.get("source") not in ("mybatis_xml", "mybatis_sql_literal"):
            continue
        f = h.get("file")
        if not isinstance(f, str) or not f.endswith(".xml"):
            continue
        ln = int(h.get("line") or 0)
        key = (f.replace("\\", "/"), ln)
        if key in seen_line:
            continue
        seen_line.add(key)
        raw.append(h)
    raw.sort(key=lambda x: (str(x.get("file")), int(x.get("line") or 0)))
    if not dedupe_by_java_method or project_root is None:
        return raw
    resolved_buckets: dict[tuple[str, int, str], dict[str, Any]] = {}
    for h in raw:
        xrel = str(h.get("file", "")).replace("\\", "/")
        xln = int(h.get("line") or 0)
        if xln < 1:
            continue
        r = resolve_mapper_java_method_from_xml_line(project_root, xrel, xln)
        if not r.get("ok"):
            urk = (xrel, xln, "__unresolved__")
            if urk not in resolved_buckets:
                resolved_buckets[urk] = h
            continue
        jf = str(r["javaFile"])
        jl = int(r["line"])
        sid = str(r.get("mapperStatementId", ""))
        bk = (jf, jl, sid)
        prev = resolved_buckets.get(bk)
        if prev is None or xln < int(prev.get("line") or 999999):
            resolved_buckets[bk] = h
    out = list(resolved_buckets.values())
    out.sort(key=lambda x: (str(x.get("file")), int(x.get("line") or 0)))
    return out


def _entity_annotation_java_path_from_manifest(manifest: dict[str, Any] | None, physical_table: str) -> str | None:
    """从 ``anchorsByPhysicalTable`` / ``anchorsByTable`` / ``extractedHits`` 取 ``entity_annotation`` 的 ``.java`` 路径（物理表键）。"""
    if not manifest or not isinstance(manifest, dict):
        return None
    phy_cf = physical_table.casefold()
    abp = manifest.get("anchorsByPhysicalTable")
    if isinstance(abp, dict):
        for key, rows in abp.items():
            if str(key).casefold() != phy_cf:
                continue
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("source") != "entity_annotation":
                    continue
                f = row.get("file")
                if isinstance(f, str) and f.endswith(".java"):
                    return f.replace("\\", "/")
            break
    abt = manifest.get("anchorsByTable")
    if isinstance(abt, dict):
        for key, rows in abt.items():
            if normalize_table_token_to_physical(str(key)).casefold() != phy_cf:
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("source") != "entity_annotation":
                    continue
                f = row.get("file")
                if isinstance(f, str) and f.endswith(".java"):
                    return f.replace("\\", "/")
    hits = manifest.get("extractedHits")
    if isinstance(hits, list):
        for row in hits:
            if not isinstance(row, dict):
                continue
            if normalize_table_token_to_physical(str(row.get("table", ""))).casefold() != phy_cf:
                continue
            if row.get("source") != "entity_annotation":
                continue
            f = row.get("file")
            if isinstance(f, str) and f.endswith(".java"):
                return f.replace("\\", "/")
    return None


def _repo_field_name(text: str, repo_simple: str) -> str | None:
    m = re.search(
        rf"(?:private|protected)\s+(?:final\s+)?{re.escape(repo_simple)}\s+(\w+)\s*;",
        text,
    )
    return m.group(1) if m else None


def _method_name_from_public_block(lines: list[str], pub_line: int) -> str | None:
    chunk: list[str] = []
    for k in range(pub_line, min(pub_line + 16, len(lines))):
        chunk.append(lines[k])
        if "{" in lines[k]:
            break
    text = " ".join(x.strip() for x in chunk)
    m = re.search(
        r"\bpublic\s+(?:static\s+)?(?:final\s+)?(?:[\w.<>,?\[\]]+\s+)+(\w+)\s*\(",
        text,
    )
    if not m:
        return None
    name = m.group(1)
    if name in ("if", "for", "while", "switch", "try", "catch"):
        return None
    return name


def _score_repo_line(line: str, field: str) -> int:
    if f"{field}." not in line:
        return -1
    s = line.strip()
    if s.startswith("//") or s.startswith("*"):
        return -1
    score = 5
    if ".save(" in line or ".saveAll(" in line:
        score += 100
    elif ".delete" in line or ".remove(" in line:
        score += 80
    elif ".insert" in line or ".update" in line or ".execute" in line:
        score += 70
    elif ".find" in line or ".get" in line or ".query" in line or ".count" in line or ".exists" in line:
        score += 20
    return score


def _pick_method_for_field(lines: list[str], field: str, class_stem: str) -> tuple[str | None, str | None]:
    """返回 (methodName, reason_if_none)。"""
    ranked: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        sc = _score_repo_line(line, field)
        if sc >= 0:
            ranked.append((sc, i))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    for _sc, i in ranked:
        for j in range(i, -1, -1):
            if not _PUBLIC_LINE.match(lines[j]):
                continue
            name = _method_name_from_public_block(lines, j)
            if not name or name == class_stem:
                continue
            return name, None
    return None, "未找到包含该 Repository 字段调用的 public 方法"


def _collect_impls_using_repo(root: Path, repo_simple: str, *, max_scan: int) -> list[Path]:
    found: list[Path] = []
    scanned = 0
    for base in java_scan_roots(root):
        for p in sorted(walk_files_matching(base, "*ServiceImpl.java")):
            if _skip_path(p):
                continue
            rel = str(p).replace("\\", "/")
            if "/src/main/java/" not in rel or "/test/" in rel:
                continue
            scanned += 1
            if scanned > max_scan:
                return found
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if repo_simple not in text:
                continue
            found.append(p)
    return found


def _order_impls(paths: list[Path], entity: str) -> list[Path]:
    prefer = f"{entity}ServiceImpl.java"
    first = [p for p in paths if p.name == prefer]
    rest = sorted((p for p in paths if p.name != prefer), key=str)
    return first + rest


def resolve_service_anchor_for_table(
    project_root: Path,
    table: str,
    *,
    max_java_scan: int = 12_000,
) -> dict[str, Any]:
    """
    为蛇形表名解析 **ServiceImpl#method** 锚点。

    成功时: ``ok`` True, 含 ``className``, ``methodName``, ``implFile``, ``entityName``, ``repositoryType``, ``fieldName``。
    失败时: ``ok`` False, 含 ``reason``。
    """
    root = project_root.resolve()
    entity = snake_table_to_entity_class(table)
    if not entity:
        return {"ok": False, "table": table, "reason": "无法从表名推导实体类名"}
    repo_simple = f"{entity}Repository"
    impls = _collect_impls_using_repo(root, repo_simple, max_scan=max_java_scan)
    if not impls:
        return {
            "ok": False,
            "table": table,
            "entityName": entity,
            "repositoryType": repo_simple,
            "reason": f"未找到注入 {repo_simple} 的 *ServiceImpl.java（已扫至多 {max_java_scan} 个候选路径）",
        }
    ordered = _order_impls(impls, entity)
    errors: list[str] = []
    for p in ordered:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errors.append(f"{p}: {e}")
            continue
        field = _repo_field_name(text, repo_simple)
        if not field:
            errors.append(f"{p}: 无 {repo_simple} 字段声明")
            continue
        lines = text.splitlines()
        class_stem = p.stem
        meth, r = _pick_method_for_field(lines, field, class_stem)
        if meth:
            rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
            return {
                "ok": True,
                "table": table,
                "entityName": entity,
                "repositoryType": repo_simple,
                "fieldName": field,
                "className": class_stem,
                "methodName": meth,
                "implFile": rel,
                "implRank": ordered.index(p),
            }
        errors.append(f"{p}: {r or '无可用方法'}")
    return {
        "ok": False,
        "table": table,
        "entityName": entity,
        "repositoryType": repo_simple,
        "reason": "；".join(errors[:6]) + ("…" if len(errors) > 6 else ""),
    }


_JAVA_CLASS_DECL = re.compile(r"\bclass\s+([A-Za-z_]\w*)\b")


def _java_file_top_level_class_simple_name(text: str) -> str | None:
    m = _JAVA_CLASS_DECL.search(text)
    return m.group(1) if m else None


def _entity_source_matches_physical_table(text: str, table: str, expected_simple: str) -> bool:
    if not re.search(r"@Entity\b", text):
        return False
    simple = _java_file_top_level_class_simple_name(text)
    if simple != expected_simple:
        return False
    names = extract_jpa_table_names_from_java(text)
    if not names:
        return True
    tcf = table.casefold()
    return any(n.casefold() == tcf for n in names)


def _class_simple_name_declaration_line_char(lines: list[str], simple: str) -> tuple[int, int] | None:
    """``public class Foo`` 中 ``Foo`` 的起始位置：1-based 行号、1-based 列（与 CLI ``--line`` / ``--character`` 一致）。"""
    pat = re.compile(rf"\bclass\s+({re.escape(simple)})\b")
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        m = pat.search(line)
        if m:
            return (i, m.start(1) + 1)
    return None


def _collect_entity_java_paths(root: Path, simple: str, *, max_scan: int) -> list[Path]:
    found: list[Path] = []
    scanned = 0
    for base in java_scan_roots(root):
        for p in sorted(walk_files_matching(base, f"{simple}.java")):
            if _skip_path(p):
                continue
            rel = str(p).replace("\\", "/")
            if "/src/main/java/" not in rel or "/test/" in rel:
                continue
            scanned += 1
            if scanned > max_scan:
                return found
            found.append(p)
    return found


def resolve_entity_anchor_for_table(
    project_root: Path,
    table: str,
    *,
    max_java_scan: int = 12_000,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    为蛇形表名解析 **JPA 实体类声明** 上的位置，供 ``trace_call_chain_sync(..., file_path=, line=, character=)`` 作**类型侧**向上调用链入口。

    优先使用 ``tables-manifest`` 中已抽取的 ``entity_annotation`` 文件路径（与 step3 一致），再回退磁盘 ``**/{Entity}.java`` 扫描。

    条件：含 ``@Entity``、顶层类名与表推导名一致；若存在 ``@Table(name=…)`` 则须与 ``table`` 大小写不敏感一致；无显式 ``@Table`` 时仅校验类名与 ``@Entity``。
    """
    root = project_root.resolve()
    entity = snake_table_to_entity_class(table)
    if not entity:
        return {"ok": False, "table": table, "reason": "无法从表名推导实体类名"}

    paths: list[Path] = []
    seen_resolved: set[str] = set()
    manifest_entity_path: Path | None = None
    rel_m = _entity_annotation_java_path_from_manifest(manifest, table)
    if rel_m:
        pm = (root / rel_m).resolve()
        if pm.is_file():
            paths.append(pm)
            seen_resolved.add(str(pm))
            manifest_entity_path = pm
    for p in _collect_entity_java_paths(root, entity, max_scan=max_java_scan):
        rp = str(p.resolve())
        if rp in seen_resolved:
            continue
        paths.append(p)
        seen_resolved.add(rp)

    if not paths:
        return {
            "ok": False,
            "table": table,
            "entityName": entity,
            "reason": f"未找到实体源文件（manifest entity_annotation 与 main/java 下 {entity}.java 均未命中，扫描上限 {max_java_scan}）",
        }
    errors: list[str] = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errors.append(f"{p}: {e}")
            continue
        if text.startswith("\ufeff"):
            text = text[1:]
        if not _entity_source_matches_physical_table(text, table, entity):
            errors.append(f"{p}: 非与本表绑定的 @Entity（@Table 名或类名不匹配）")
            continue
        lines = text.splitlines()
        pos = _class_simple_name_declaration_line_char(lines, entity)
        if not pos:
            errors.append(f"{p}: 未解析到 class {entity} 声明行")
            continue
        line1, char1 = pos
        try:
            rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        except ValueError:
            rel = str(p)
        return {
            "ok": True,
            "table": table,
            "entityName": entity,
            "entityFile": rel.replace("\\", "/"),
            "line": line1,
            "character": char1,
            "entityPathSource": (
                "manifest"
                if manifest_entity_path is not None and p.resolve() == manifest_entity_path.resolve()
                else "scan"
            ),
        }
    return {
        "ok": False,
        "table": table,
        "entityName": entity,
        "reason": "；".join(errors[:6]) + ("…" if len(errors) > 6 else ""),
    }


def safe_table_filename(table: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", table.strip())[:80].strip("_")
    return s or "table"


# 按物理表分子目录：data/callchain-up-table/<safe_physical>/callchain-up-table-*.md
_TABLE_UP_ROOT_DIRNAME = "callchain-up-table"


def run_table_callchain_up(
    project_root: Path,
    manifest: dict[str, Any],
    data_dir: Path,
    *,
    jdtls_path: Path | None,
    max_depth: int,
    max_java_scan: int = 12_000,
    lsp_client: LSPClient | None = None,
    output_root: Path | None = None,
    table_up_sql_literal: bool = False,
    table_up_mybatis_mapper: bool = False,
    max_table_up_sql_anchors: int = 24,
    max_table_up_mybatis_anchors: int = 24,
) -> dict[str, Any]:
    """
    编排：蛇形物理表 → **ServiceImpl** + **@Entity 类声明**；可选 **manifest 中 JDBC 字符串 SQL 行**（``jdbc_sql_literal`` 的 ``*.java``）、
    **MyBatis XML → Mapper 接口方法**（``mybatis_xml`` / ``mybatis_sql_literal`` 的 ``*.xml``）。

    JDBC 额外锚点：优先 **顶层类型全名 + manifest 中包围方法简单名**（``javaMethod``）；失败再试 **``javaMethodLine``** 上的 ``file_path``+行；仍失败才回退 **SQL 字面量命中行**（注解字符串内常无法 ``prepareCallHierarchy``）。
    MyBatis：由 XML 行解析 ``namespace`` + statement ``id``，再定位接口 Java 方法。

    仅当 **ServiceImpl 与 Entity 两类核心锚点均失败** 时记入 ``skipped``；可选锚点不影响是否跳过整张表。
    产物：``data/callchain-up-table/<物理表安全名>/callchain-up-table-<表>.md``、同目录下 ``…-entity.md``、可选 ``…-sql-NN.md``、``…-mapper-NN.md``。

    若传入 ``lsp_client``，各表复用该连接（由调用方 ``create_client`` / ``shutdown``）。
    ``data_dir``：通常为 ``design/data``；表级报告写在 ``data_dir / callchain-up-table / <表>/``；``output_root``：``table-callchain-summary.json``。

    成功写入的 Markdown 与文末 JSON 的 ``query.manifestAnchor`` 含 **manifestHitId**（``ma-`` + 16 位 hex，由物理表、来源、文件、行等稳定派生）及 manifest 追溯字段；``table-callchain-summary.json`` 按 **物理表** 分组（``resolvedByPhysicalTable`` / ``withErrorsByPhysicalTable``，键同 ``data/callchain-up-table/<表>/``）；每条 result 亦含 ``manifestHitId`` / ``manifestAnchor``。
    """
    root = project_root.resolve()
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    out_root = (output_root.resolve() if output_root is not None else data_dir.parent.resolve())
    out_root.mkdir(parents=True, exist_ok=True)

    canonical = manifest.get("canonicalTables")
    if not isinstance(canonical, list):
        return {"error": "tables-manifest 缺少 canonicalTables", "results": [], "skipped": []}

    cp = manifest.get("canonicalPhysicalTables")
    if isinstance(cp, list) and len(cp) > 0:
        tables = [str(x).strip() for x in cp if str(x).strip()]
    else:
        tables = physical_tables_from_canonical([str(x) for x in canonical])
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for table in tables:
        safe_tbl = safe_table_filename(table)
        table_out_dir = data_dir / _TABLE_UP_ROOT_DIRNAME / safe_tbl
        table_out_dir.mkdir(parents=True, exist_ok=True)
        svc = resolve_service_anchor_for_table(root, table, max_java_scan=max_java_scan)
        ent = resolve_entity_anchor_for_table(
            root, table, max_java_scan=max_java_scan, manifest=manifest
        )

        if not svc.get("ok") and not ent.get("ok"):
            reason = (
                f"ServiceImpl: {svc.get('reason', 'unknown')}; "
                f"Entity: {ent.get('reason', 'unknown')}"
            )
            skipped.append({"table": table, "reason": reason})
            _log.warning("table callchain: skip table=%s reason=%s", table, reason)
            continue

        if svc.get("ok"):
            cls = str(svc["className"])
            meth = str(svc["methodName"])
            impl_rel = str(svc.get("implFile", "")).replace("\\", "/")
            svc_mid = stable_manifest_hit_id(
                table, "service_impl_heuristic", impl_rel, 0, extra=f"{cls}.{meth}"
            )
            svc_anchor: dict[str, Any] = {
                "manifestHitId": svc_mid,
                "physicalTable": table,
                "anchorKind": "serviceImpl",
                "manifestSource": "service_impl_heuristic",
                "implFile": impl_rel,
                "className": cls,
                "methodName": meth,
                "repositoryType": svc.get("repositoryType"),
                "entityName": svc.get("entityName"),
                "implRank": svc.get("implRank"),
            }
            raw = trace_call_chain_sync(
                str(root),
                cls,
                meth,
                file_path=None,
                line=None,
                character=None,
                symbol_query=None,
                jdtls_path=jdtls_path,
                lsp_client=lsp_client,
                max_depth=max_depth,
                output_format="markdown",
            )

            fn = table_out_dir / f"callchain-up-table-{safe_tbl}.md"
            rel_name = str(fn.relative_to(data_dir.parent)) if fn.is_relative_to(data_dir.parent) else str(fn)

            row: dict[str, Any] = {
                "table": table,
                "anchorKind": "serviceImpl",
                "className": cls,
                "methodName": meth,
                "implFile": svc.get("implFile"),
                "entityName": svc.get("entityName"),
                "manifestHitId": svc_mid,
                "manifestAnchor": svc_anchor,
                "outputFile": rel_name,
            }

            if raw.startswith("错误:"):
                row["jdtlsError"] = raw[:2000]
                results.append(row)
                _log.warning("table callchain: jdtls error table=%s service %s.%s detail=%s", table, cls, meth, raw[:400])
            else:
                raw = apply_manifest_anchor_to_callchain_markdown(raw, svc_anchor)
                fn.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
                row["summary"] = summarize_trace_up_json(raw)
                results.append(row)
                _log.info(
                    "table callchain: ok table=%s anchor=serviceImpl %s.%s chains=%s",
                    table,
                    cls,
                    meth,
                    row.get("summary", {}).get("chainCount"),
                )

        if ent.get("ok"):
            efile = str(ent["entityFile"])
            efile_n = efile.replace("\\", "/")
            eline = int(ent["line"])
            echar = int(ent["character"])
            ename = str(ent["entityName"])
            ent_ann = _first_manifest_row_by_source(manifest, table, "entity_annotation")
            ann_file = str(ent_ann.get("file", "")).replace("\\", "/") if ent_ann else ""
            if ent_ann and ann_file == efile_n:
                ent_mid = stable_manifest_hit_id(
                    table, "entity_annotation", ann_file, int(ent_ann.get("line") or 0)
                )
                ent_msrc = "entity_annotation"
            else:
                ent_mid = stable_manifest_hit_id(table, "entity_class_declaration", efile_n, eline)
                ent_msrc = "entity_class_declaration"
            ent_anchor: dict[str, Any] = {
                "manifestHitId": ent_mid,
                "physicalTable": table,
                "anchorKind": "entity",
                "manifestSource": ent_msrc,
                "entityFile": efile_n,
                "entityDeclarationLine": eline,
                "entityDeclarationCharacter": echar,
                "entityName": ename,
                "entityPathSource": ent.get("entityPathSource"),
            }
            raw_e = trace_call_chain_sync(
                str(root),
                None,
                None,
                file_path=efile,
                line=eline,
                character=echar,
                symbol_query=None,
                jdtls_path=jdtls_path,
                lsp_client=lsp_client,
                max_depth=max_depth,
                output_format="markdown",
            )

            fn_e = table_out_dir / f"callchain-up-table-{safe_tbl}-entity.md"
            rel_e = str(fn_e.relative_to(data_dir.parent)) if fn_e.is_relative_to(data_dir.parent) else str(fn_e)

            row_e: dict[str, Any] = {
                "table": table,
                "anchorKind": "entity",
                "className": ename,
                "methodName": "(class declaration)",
                "entityFile": efile,
                "entityName": ename,
                "entityDeclarationLine": eline,
                "entityDeclarationCharacter": echar,
                "entityPathSource": ent.get("entityPathSource"),
                "manifestHitId": ent_mid,
                "manifestAnchor": ent_anchor,
                "outputFile": rel_e,
            }

            if raw_e.startswith("错误:"):
                row_e["jdtlsError"] = raw_e[:2000]
                results.append(row_e)
                _log.warning(
                    "table callchain: jdtls error table=%s entity %s:%s detail=%s",
                    table,
                    efile,
                    eline,
                    raw_e[:400],
                )
            else:
                raw_e = apply_manifest_anchor_to_callchain_markdown(raw_e, ent_anchor)
                fn_e.write_text(raw_e if raw_e.endswith("\n") else raw_e + "\n", encoding="utf-8")
                row_e["summary"] = summarize_trace_up_json(raw_e)
                results.append(row_e)
                _log.info(
                    "table callchain: ok table=%s anchor=entity %s@%s:%s chains=%s",
                    table,
                    ename,
                    eline,
                    echar,
                    row_e.get("summary", {}).get("chainCount"),
                )

        if table_up_sql_literal:
            sql_hits = _collect_jdbc_sql_literal_java_hits(
                manifest, table, project_root=root, dedupe_by_method=True
            )
            seen_sql: set[tuple[str, int]] = set()
            sql_i = 0
            max_sql = int(max_table_up_sql_anchors)
            for h in sql_hits:
                if max_sql > 0 and sql_i >= max_sql:
                    break
                rel = str(h.get("file", "")).replace("\\", "/")
                ln = int(h.get("line") or 0)
                if ln < 1 or not rel.endswith(".java"):
                    continue
                key = (rel, ln)
                if key in seen_sql:
                    continue
                seen_sql.add(key)
                sql_i += 1
                jm = _jdbc_hit_method_simple_name(h)
                jml = int(h.get("javaMethodLine") or 0)
                fqcn_anchor = _java_file_top_level_fqcn(root, rel)
                raw_s, anchor_mode = _trace_callchain_up_jdbc_anchor(
                    root,
                    rel,
                    ln,
                    java_method=jm,
                    java_method_line=jml,
                    jdtls_path=jdtls_path,
                    lsp_client=lsp_client,
                    max_depth=max_depth,
                    fqcn=fqcn_anchor,
                )
                sql_mid = stable_manifest_hit_id(table, "jdbc_sql_literal", rel, ln)
                jdbc_anchor: dict[str, Any] = {
                    "manifestHitId": sql_mid,
                    "physicalTable": table,
                    "anchorKind": "jdbcSqlLiteral",
                    "manifestSource": "jdbc_sql_literal",
                    "sqlLiteralHitFile": rel,
                    "sqlLiteralHitLine": ln,
                    "javaMethod": h.get("javaMethod"),
                    "javaMethodLine": h.get("javaMethodLine"),
                    "tableAsFound": h.get("tableAsFound"),
                    "confidence": h.get("confidence"),
                    "callchainAnchorMode": anchor_mode,
                }
                if fqcn_anchor:
                    jdbc_anchor["resolvedCallchainClassName"] = fqcn_anchor
                if jm:
                    jdbc_anchor["resolvedCallchainMethodName"] = jm
                snip = h.get("snippet")
                if snip:
                    jdbc_anchor["snippet"] = str(snip)[:240]
                fn_s = table_out_dir / f"callchain-up-table-{safe_tbl}-sql-{sql_i:02d}.md"
                rel_s = str(fn_s.relative_to(data_dir.parent)) if fn_s.is_relative_to(data_dir.parent) else str(fn_s)
                row_s: dict[str, Any] = {
                    "table": table,
                    "anchorKind": "jdbcSqlLiteral",
                    "manifestSource": "jdbc_sql_literal",
                    "sqlHitFile": rel,
                    "sqlHitLine": ln,
                    "javaMethod": h.get("javaMethod"),
                    "javaMethodLine": h.get("javaMethodLine"),
                    "callchainAnchorMode": anchor_mode,
                    "snippet": (str(h.get("snippet", ""))[:300] if h.get("snippet") else None),
                    "manifestHitId": sql_mid,
                    "manifestAnchor": jdbc_anchor,
                    "outputFile": rel_s,
                }
                if fqcn_anchor and jm:
                    row_s["callchainClassName"] = fqcn_anchor
                    row_s["callchainMethodName"] = jm
                if raw_s.startswith("错误:"):
                    row_s["jdtlsError"] = raw_s[:2000]
                    results.append(row_s)
                    _log.warning(
                        "table callchain: jdtls sql anchor table=%s %s sqlLine=%s mode=%s",
                        table,
                        rel,
                        ln,
                        anchor_mode,
                    )
                else:
                    raw_s = apply_manifest_anchor_to_callchain_markdown(raw_s, jdbc_anchor)
                    fn_s.write_text(raw_s if raw_s.endswith("\n") else raw_s + "\n", encoding="utf-8")
                    row_s["summary"] = summarize_trace_up_json(raw_s)
                    results.append(row_s)
                    _log.info(
                        "table callchain: ok table=%s anchor=jdbcSqlLiteral mode=%s %s sqlLine=%s",
                        table,
                        anchor_mode,
                        rel,
                        ln,
                    )

        if table_up_mybatis_mapper:
            xml_hits = _collect_mybatis_xml_hits(
                manifest, table, project_root=root, dedupe_by_java_method=True
            )
            seen_mb: set[tuple[str, int, str]] = set()
            mb_ok = 0
            max_mb = int(max_table_up_mybatis_anchors)
            for h in xml_hits:
                if max_mb > 0 and mb_ok >= max_mb:
                    break
                xrel = str(h.get("file", "")).replace("\\", "/")
                xln = int(h.get("line") or 0)
                if xln < 1 or not xrel.endswith(".xml"):
                    continue
                resolved = resolve_mapper_java_method_from_xml_line(root, xrel, xln)
                mb_src = str(h.get("source", "mybatis_xml"))
                mb_mid = stable_manifest_hit_id(table, mb_src, xrel, xln)
                if not resolved.get("ok"):
                    mb_anchor_fail: dict[str, Any] = {
                        "manifestHitId": mb_mid,
                        "physicalTable": table,
                        "anchorKind": "mybatisMapperMethod",
                        "manifestSource": mb_src,
                        "xmlFile": xrel,
                        "xmlLine": xln,
                    }
                    snip_m = h.get("snippet")
                    if snip_m:
                        mb_anchor_fail["snippet"] = str(snip_m)[:240]
                    row_m: dict[str, Any] = {
                        "table": table,
                        "anchorKind": "mybatisMapperMethod",
                        "manifestSource": mb_src,
                        "xmlFile": xrel,
                        "xmlLine": xln,
                        "snippet": (str(h.get("snippet", ""))[:300] if h.get("snippet") else None),
                        "mapperResolveError": str(resolved.get("reason", "unknown")),
                        "manifestHitId": mb_mid,
                        "manifestAnchor": mb_anchor_fail,
                    }
                    results.append(row_m)
                    _log.warning(
                        "table callchain: mybatis resolve skip table=%s xml=%s:%s %s",
                        table,
                        xrel,
                        xln,
                        row_m["mapperResolveError"],
                    )
                    continue
                jf = str(resolved["javaFile"])
                jl = int(resolved["line"])
                jc = int(resolved["character"])
                stmt_id = str(resolved.get("mapperStatementId", ""))
                dedupe_key = (jf, jl, stmt_id)
                if dedupe_key in seen_mb:
                    continue
                seen_mb.add(dedupe_key)
                mb_ok += 1
                mb_mid_ok = stable_manifest_hit_id(table, mb_src, xrel, xln, extra=stmt_id)
                mb_anchor: dict[str, Any] = {
                    "manifestHitId": mb_mid_ok,
                    "physicalTable": table,
                    "anchorKind": "mybatisMapperMethod",
                    "manifestSource": mb_src,
                    "xmlFile": xrel,
                    "xmlLine": xln,
                    "mapperNamespace": resolved.get("mapperNamespace"),
                    "mapperStatementId": stmt_id,
                    "javaMapperFile": jf.replace("\\", "/"),
                    "javaMapperLine": jl,
                }
                snip_ok = h.get("snippet")
                if snip_ok:
                    mb_anchor["snippet"] = str(snip_ok)[:240]
                raw_m = trace_call_chain_sync(
                    str(root),
                    None,
                    None,
                    file_path=jf,
                    line=jl,
                    character=jc,
                    symbol_query=None,
                    jdtls_path=jdtls_path,
                    lsp_client=lsp_client,
                    max_depth=max_depth,
                    output_format="markdown",
                )
                fn_m = table_out_dir / f"callchain-up-table-{safe_tbl}-mapper-{mb_ok:02d}.md"
                rel_m = str(fn_m.relative_to(data_dir.parent)) if fn_m.is_relative_to(data_dir.parent) else str(fn_m)
                row_mb: dict[str, Any] = {
                    "table": table,
                    "anchorKind": "mybatisMapperMethod",
                    "manifestSource": mb_src,
                    "xmlFile": xrel,
                    "xmlLine": xln,
                    "mapperNamespace": resolved.get("mapperNamespace"),
                    "mapperStatementId": stmt_id,
                    "javaMapperFile": jf,
                    "javaMapperLine": jl,
                    "manifestHitId": mb_mid_ok,
                    "manifestAnchor": mb_anchor,
                    "outputFile": rel_m,
                }
                if raw_m.startswith("错误:"):
                    row_mb["jdtlsError"] = raw_m[:2000]
                    results.append(row_mb)
                    _log.warning("table callchain: jdtls mybatis table=%s %s.%s", table, jf, stmt_id)
                else:
                    raw_m = apply_manifest_anchor_to_callchain_markdown(raw_m, mb_anchor)
                    fn_m.write_text(raw_m if raw_m.endswith("\n") else raw_m + "\n", encoding="utf-8")
                    row_mb["summary"] = summarize_trace_up_json(raw_m)
                    results.append(row_mb)
                    _log.info(
                        "table callchain: ok table=%s anchor=mybatisMapper %s.%s",
                        table,
                        jf,
                        stmt_id,
                    )

    resolved_rows = [r for r in results if "jdtlsError" not in r and "mapperResolveError" not in r]
    error_rows = [r for r in results if "jdtlsError" in r or "mapperResolveError" in r]

    def _by_physical_table(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        d: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            k = str(r.get("table") or "_unknown")
            d.setdefault(k, []).append(r)
        return dict(sorted(d.items(), key=lambda x: x[0]))

    resolved_by_tbl = _by_physical_table(resolved_rows)
    errors_by_tbl = _by_physical_table(error_rows)

    summary_path = out_root / "table-callchain-summary.json"
    payload = {
        "projectRoot": str(root),
        "tablesAttempted": tables,
        "options": {
            "tableUpSqlLiteral": bool(table_up_sql_literal),
            "tableUpMybatisMapper": bool(table_up_mybatis_mapper),
            "maxTableUpSqlAnchors": int(max_table_up_sql_anchors),
            "maxTableUpMybatisAnchors": int(max_table_up_mybatis_anchors),
            "tableCallchainSubdirPattern": f"data/{_TABLE_UP_ROOT_DIRNAME}/<physical_table>/",
            "manifestHitIdNote": "每条结果 manifestHitId = SHA-256(physicalTable|source|relFile|line|extra) 前 16 hex，前缀 ma-；与 tables-manifest 锚点一一对应可追溯",
            "summaryGrouping": "resolvedByPhysicalTable / withErrorsByPhysicalTable 的键与 data/callchain-up-table/<物理表>/ 目录名一致（蛇形物理表名）",
        },
        "resolvedByPhysicalTable": resolved_by_tbl,
        "withErrorsByPhysicalTable": errors_by_tbl,
        "skipped": skipped,
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    res_n = sum(len(v) for v in resolved_by_tbl.values())
    err_n = sum(len(v) for v in errors_by_tbl.values())
    return {
        "summaryFile": str(summary_path.relative_to(out_root))
        if summary_path.is_relative_to(out_root)
        else str(summary_path),
        "resolvedCount": res_n,
        "errorCount": err_n,
        "skippedCount": len(skipped),
        "results": results,
    }


__all__ = [
    "resolve_entity_anchor_for_table",
    "resolve_service_anchor_for_table",
    "run_table_callchain_up",
]

