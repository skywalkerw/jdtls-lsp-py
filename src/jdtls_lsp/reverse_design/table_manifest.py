"""step3：数据库表清单与轻量抽取（用户清单为准 + @Table / SQL 字面量 / MyBatis XML）。"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jdtls_lsp.java_grep import SKIP_DIR_PARTS
from jdtls_lsp.logutil import get_logger
from jdtls_lsp.reverse_design.java_enclosing_method import java_enclosing_method_at_line

_log = get_logger("reverse_design.table_manifest")

# @Table( … ) 而非 @TableField
_TABLE_ANN = re.compile(
    r"@Table\s*\(\s*(?:name\s*=\s*)?[\"']([^\"']+)[\"']",
    re.MULTILINE,
)


def extract_jpa_table_names_from_java(text: str) -> list[str]:
    """从源码中提取 ``@Table( … name = '…' … )`` 的物理表名（不含 ``@TableField``）。"""
    names: list[str] = []
    for m in _TABLE_ANN.finditer(text):
        raw = m.group(1).strip()
        if "." in raw:
            raw = raw.split(".")[-1]
        names.append(raw)
    return names


_STRING_DOUBLE = re.compile(r"\"(?:[^\"\\]|\\.)*\"")
_STRING_SINGLE = re.compile(r"'(?:[^'\\]|\\.)*'")

_SQL_HINT = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM)\b", re.I)
_SQL_TABLE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][\w]*)",
    re.I,
)

_MYBATIS_TABLE_ATTR = re.compile(r"\btable\s*=\s*[\"']([^\"']+)[\"']", re.I)

_PHYSICAL_TABLE_TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")
_PASCAL_JAVA_TYPE = re.compile(r"^[A-Z][a-zA-Z0-9]+$")

_SQL_RESERVED = frozenset(
    {
        "DUAL",
        "WHERE",
        "GROUP",
        "ORDER",
        "HAVING",
        "LIMIT",
        "OFFSET",
        "SET",
        "VALUES",
        "SELECT",
        "DISTINCT",
        "INNER",
        "LEFT",
        "RIGHT",
        "OUTER",
        "FULL",
        "CROSS",
        "ON",
        "AS",
        "AND",
        "OR",
        "NOT",
        "NULL",
        "TRUE",
        "FALSE",
        "CASE",
        "WHEN",
        "THEN",
        "ELSE",
        "END",
        "BY",
        "ASC",
        "DESC",
        "UNION",
        "ALL",
        "BETWEEN",
        "LIKE",
        "IN",
        "EXISTS",
        "INTO",
        "FROM",
        "JOIN",
        "UPDATE",
    }
)


def normalize_table_token_to_physical(name: str) -> str:
    """将 manifest/代码中出现的表名字符串统一为**蛇形物理表键**（小写），便于 ``MonitorData`` 与 ``monitor_data`` 同组。

    - 已是蛇形 ``[a-z][a-z0-9_]*`` → 转小写。
    - **PascalCase**（实体/JPQL 类名）→ 驼峰转蛇形后再小写。
    - 其它情况 → ``str`` 小写（兜底归并）。
    """
    t = str(name).strip()
    if not t:
        return t
    if _PHYSICAL_TABLE_TOKEN.match(t):
        return t.lower()
    if _PASCAL_JAVA_TYPE.match(t):
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", t)
        s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
        snake = s2.lower()
        if _PHYSICAL_TABLE_TOKEN.match(snake):
            return snake
    return t.lower()


def _skip_path(p: Path) -> bool:
    return any(x in p.parts for x in SKIP_DIR_PARTS)


def load_user_tables_from_file(path: Path) -> list[str]:
    """每行一表；``#`` 起为注释；行内 ``#`` 后为注释。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    out: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line[: line.index("#")].strip()
        if not line:
            continue
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            out.append(line)
    return out


def parse_user_tables_inline(s: str) -> list[str]:
    parts = [x.strip() for x in s.replace("，", ",").split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        k = p.casefold()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _merge_user_lists(file_tables: list[str], inline: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in file_tables + inline:
        k = t.casefold()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def _tables_in_sql_fragment(s: str) -> list[str]:
    if not _SQL_HINT.search(s):
        return []
    found: list[str] = []
    for m in _SQL_TABLE.finditer(s):
        t = m.group(1)
        if t.upper() in _SQL_RESERVED:
            continue
        found.append(t)
    return found


def _collect_java_paths(root: Path, max_files: int) -> list[Path]:
    paths = sorted(root.rglob("*.java"))
    out: list[Path] = []
    for p in paths:
        if _skip_path(p):
            continue
        out.append(p)
        if len(out) >= max_files:
            break
    return out


def _collect_xml_paths(root: Path, max_files: int) -> list[Path]:
    paths = sorted(root.rglob("*.xml"))
    out: list[Path] = []
    for p in paths:
        if _skip_path(p):
            continue
        if "/test/" in str(p).replace("\\", "/") or p.name.startswith("."):
            continue
        out.append(p)
        if len(out) >= max_files:
            break
    return out


def _scan_java_file(fp: Path, rel: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        if "@TableField" in line:
            pass
        if "@Table(" in line and "@TableField" not in line:
            for m in _TABLE_ANN.finditer(line):
                raw = m.group(1).strip()
                if "." in raw:
                    raw = raw.split(".")[-1]
                hits.append(
                    {
                        "table": raw,
                        "source": "entity_annotation",
                        "confidence": "high",
                        "file": rel,
                        "line": line_no,
                        "snippet": line.strip()[:240],
                    }
                )
        for sm in list(_STRING_DOUBLE.finditer(line)) + list(_STRING_SINGLE.finditer(line)):
            lit = sm.group(0)[1:-1]
            lit_unesc = bytes(lit, "utf-8").decode("unicode_escape", errors="replace")
            for tbl in _tables_in_sql_fragment(lit_unesc):
                m_line, m_name = java_enclosing_method_at_line(lines, line_no)
                row: dict[str, Any] = {
                    "table": tbl,
                    "source": "jdbc_sql_literal",
                    "confidence": "medium",
                    "file": rel,
                    "line": line_no,
                    "snippet": line.strip()[:240],
                    "javaMethod": m_name,
                    "javaMethodLine": m_line,
                }
                hits.append(row)
    return hits


def _scan_xml_file(fp: Path, rel: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    if text.startswith("\ufeff"):
        text = text[1:]
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in _MYBATIS_TABLE_ATTR.finditer(line):
            hits.append(
                {
                    "table": m.group(1).strip(),
                    "source": "mybatis_xml",
                    "confidence": "high",
                    "file": rel,
                    "line": line_no,
                    "snippet": line.strip()[:240],
                }
            )
        for sm in _STRING_DOUBLE.finditer(line):
            lit = sm.group(0)[1:-1]
            for tbl in _tables_in_sql_fragment(lit):
                hits.append(
                    {
                        "table": tbl,
                        "source": "mybatis_sql_literal",
                        "confidence": "medium",
                        "file": rel,
                        "line": line_no,
                        "snippet": line.strip()[:240],
                    }
                )
    return hits


def build_table_manifest(
    project_root: Path,
    *,
    user_tables: list[str] | None = None,
    tables_file: Path | None = None,
    tables_inline: str = "",
    strict_tables_only: bool = False,
    max_java_files: int = 8_000,
    max_xml_files: int = 2_000,
) -> dict[str, Any]:
    """
    合并 **用户表清单**（权威规范名）与 **代码轻量抽取**。

    - ``canonicalTables``：用户给定顺序（文件 + 行内 ``--tables``）；若均未提供则为抽取到的表名排序。
    - ``extractedHits``：全部命中（含 source/confidence）；``jdbc_sql_literal`` 另含 ``javaMethod`` / ``javaMethodLine``（包围方法简单名与签名行号）。
    - ``anchorsByTable``：按规范表名聚合命中（键与 ``canonicalTables`` 对齐，大小写不敏感归并）。
    - ``anchorsByPhysicalTable``：按**物理蛇形表键**聚合（``monitor_data`` 与 ``MonitorData`` 等同组）；同 ``file``+``line``+``source`` 只保留一条。
    - ``canonicalPhysicalTables``：由 ``canonicalTables`` 去重得到的物理表键列表（顺序保留）。
    - ``unresolvedTables``：用户清单中有、但 **无任何命中** 的表。
    - ``extractedOnly``：有命中但 **不在用户清单** 中的表（``strict_tables_only`` 时仍计算但可从展示侧隐藏）。
    """
    root = project_root.resolve()
    if not root.exists():
        return {"error": f"项目路径不存在: {project_root}"}

    ut: list[str] = []
    if tables_file is not None:
        p = Path(tables_file).expanduser()
        if not p.is_file():
            return {"error": f"表清单文件不存在: {p}"}
        ut.extend(load_user_tables_from_file(p))
    ut = _merge_user_lists(ut, parse_user_tables_inline(tables_inline))

    canonical_norm: dict[str, str] = {}
    for t in ut:
        canonical_norm[t.casefold()] = t

    extracted_hits: list[dict[str, Any]] = []
    java_files = _collect_java_paths(root, max_java_files)
    for fp in java_files:
        try:
            rel = str(fp.relative_to(root))
        except ValueError:
            rel = str(fp)
        extracted_hits.extend(_scan_java_file(fp, rel))

    xml_files = _collect_xml_paths(root, max_xml_files)
    for fp in xml_files:
        try:
            rel = str(fp.relative_to(root))
        except ValueError:
            rel = str(fp)
        extracted_hits.extend(_scan_xml_file(fp, rel))

    tables_from_extract: set[str] = set()
    for h in extracted_hits:
        tables_from_extract.add(h["table"])

    if not ut:
        canonical_list = sorted(tables_from_extract, key=lambda x: x.casefold())
        for t in canonical_list:
            canonical_norm.setdefault(t.casefold(), t)
    else:
        canonical_list = ut

    anchors_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for h in extracted_hits:
        raw_t = str(h["table"])
        key = canonical_norm.get(raw_t.casefold(), raw_t)
        row = {k: v for k, v in h.items() if k != "table"}
        row["tableAsFound"] = raw_t
        anchors_by_table[key].append(row)

    anchors_by_physical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_phys_key: set[tuple[str, str, int, str]] = set()
    for h in extracted_hits:
        raw_t = str(h["table"])
        phys = normalize_table_token_to_physical(raw_t)
        f = str(h.get("file", ""))
        line_no = int(h.get("line", 0) or 0)
        src = str(h.get("source", ""))
        dk = (phys, f.replace("\\", "/"), line_no, src)
        if dk in seen_phys_key:
            continue
        seen_phys_key.add(dk)
        prow = {k: v for k, v in h.items() if k != "table"}
        prow["tableAsFound"] = raw_t
        prow["physicalTable"] = phys
        anchors_by_physical[phys].append(prow)

    canonical_physical: list[str] = []
    seen_pc: set[str] = set()
    for t in canonical_list:
        p = normalize_table_token_to_physical(t)
        if p.casefold() not in seen_pc:
            seen_pc.add(p.casefold())
            canonical_physical.append(p)

    hit_norms = {str(h["table"]).casefold() for h in extracted_hits}
    if ut:
        unresolved = [t for t in ut if t.casefold() not in hit_norms]
    else:
        unresolved = []

    extracted_only_names: list[str] = []
    if ut:
        user_norms = {t.casefold() for t in ut}
        extracted_only_names = sorted(
            {str(h["table"]) for h in extracted_hits if str(h["table"]).casefold() not in user_norms},
            key=lambda x: x.casefold(),
        )

    out: dict[str, Any] = {
        "projectRoot": str(root),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "userTablesFile": str(Path(tables_file).resolve()) if tables_file else None,
        "tablesInline": tables_inline.strip() or None,
        "strictTablesOnly": strict_tables_only,
        "canonicalTables": canonical_list,
        "canonicalPhysicalTables": canonical_physical,
        "extractedHitCount": len(extracted_hits),
        "javaFilesScanned": len(java_files),
        "xmlFilesScanned": len(xml_files),
        "extractedHits": extracted_hits,
        "anchorsByTable": {k: v for k, v in sorted(anchors_by_table.items(), key=lambda x: x[0].casefold())},
        "anchorsByPhysicalTable": {
            k: v for k, v in sorted(anchors_by_physical.items(), key=lambda x: x[0].casefold())
        },
        "unresolvedTables": unresolved,
        "extractedOnly": [] if strict_tables_only else extracted_only_names,
    }

    _log.info(
        "table manifest: canonical=%s physicalGroups=%s hits=%s unresolved=%s extractedOnly=%s",
        len(canonical_list),
        len(anchors_by_physical),
        len(extracted_hits),
        len(unresolved),
        len(extracted_only_names),
    )
    return out
