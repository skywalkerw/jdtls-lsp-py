"""step5 / CLI ``--table-callchains-up``：编排表名 → ServiceImpl 锚点 → ``jdtls_lsp.callchain.trace_call_chain_sync``；摘要走 ``callchain.format``。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jdtls_lsp.callchain import summarize_trace_up_json, trace_call_chain_sync
from jdtls_lsp.java_grep import SKIP_DIR_PARTS

if TYPE_CHECKING:
    from jdtls_lsp.client import LSPClient
from jdtls_lsp.logutil import get_logger

_log = get_logger("reverse_design.table_callchains_up")

_PHYSICAL_TABLE = re.compile(r"^[a-z][a-z0-9_]*$")
_PUBLIC_LINE = re.compile(r"^\s+public\s+")


def _skip_path(p: Path) -> bool:
    return any(x in p.parts for x in SKIP_DIR_PARTS)


def snake_table_to_entity_class(table: str) -> str:
    """``monitor_data`` → ``MonitorData``。"""
    parts = [x for x in table.split("_") if x]
    return "".join(x[:1].upper() + x[1:] for x in parts)


def physical_tables_from_canonical(canonical_tables: list[str]) -> list[str]:
    """仅保留蛇形物理表名，去重（大小写不敏感）。"""
    seen: set[str] = set()
    out: list[str] = []
    for t in canonical_tables:
        if not _PHYSICAL_TABLE.match(t):
            continue
        k = t.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


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
    for p in sorted(root.rglob("*ServiceImpl.java")):
        if _skip_path(p):
            continue
        rel = str(p).replace("\\", "/")
        if "/src/main/java/" not in rel or "/test/" in rel:
            continue
        scanned += 1
        if scanned > max_scan:
            break
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
    rest = sorted((p for p in paths if p.name != prefer), key=lambda x: str(x))
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


def safe_table_filename(table: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", table.strip())[:80].strip("_")
    return s or "table"


def run_table_callchains_up(
    project_root: Path,
    manifest: dict[str, Any],
    data_dir: Path,
    *,
    jdtls_path: Path | None,
    max_depth: int,
    max_java_scan: int = 12_000,
    lsp_client: LSPClient | None = None,
    output_root: Path | None = None,
) -> dict[str, Any]:
    """
    编排：manifest 蛇形表 → ``resolve_service_anchor_for_table`` → ``callchain.trace_call_chain_sync``
    （``output_format="markdown"``，与 ``callchain.format.format_callchain_markdown`` 一致），
    用 ``callchain.summarize_trace_up_json`` 写汇总行（支持从 Markdown 内嵌 JSON 解析）。

    若传入 ``lsp_client``，各表复用该连接（由调用方 ``create_client`` / ``shutdown``）。
    ``data_dir``：``callchain-up-table-*.md``（Markdown，文末含完整 JSON）；``output_root``：``table-callchains-summary.json``。
    """
    root = project_root.resolve()
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    out_root = (output_root.resolve() if output_root is not None else data_dir.parent.resolve())
    out_root.mkdir(parents=True, exist_ok=True)

    canonical = manifest.get("canonicalTables")
    if not isinstance(canonical, list):
        return {"error": "tables-manifest 缺少 canonicalTables", "results": [], "skipped": []}

    tables = physical_tables_from_canonical([str(x) for x in canonical])
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for table in tables:
        anchor = resolve_service_anchor_for_table(root, table, max_java_scan=max_java_scan)
        if not anchor.get("ok"):
            skipped.append({"table": table, "reason": str(anchor.get("reason", "unknown"))})
            _log.warning("table callchain: skip table=%s reason=%s", table, anchor.get("reason"))
            continue

        cls = str(anchor["className"])
        meth = str(anchor["methodName"])
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

        safe_tbl = safe_table_filename(table)
        fn = data_dir / f"callchain-up-table-{safe_tbl}.md"
        rel_name = str(fn.relative_to(data_dir.parent)) if fn.is_relative_to(data_dir.parent) else str(fn)

        row: dict[str, Any] = {
            "table": table,
            "className": cls,
            "methodName": meth,
            "implFile": anchor.get("implFile"),
            "entityName": anchor.get("entityName"),
            "outputFile": rel_name,
        }

        if raw.startswith("错误:"):
            row["jdtlsError"] = raw[:2000]
            results.append(row)
            _log.warning("table callchain: jdtls error table=%s detail=%s", table, raw[:400])
            continue

        fn.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
        row["summary"] = summarize_trace_up_json(raw)
        results.append(row)
        _log.info(
            "table callchain: ok table=%s anchor=%s.%s chains=%s",
            table,
            cls,
            meth,
            row.get("summary", {}).get("chainCount"),
        )

    summary_path = out_root / "table-callchains-summary.json"
    payload = {
        "projectRoot": str(root),
        "tablesAttempted": tables,
        "resolved": [r for r in results if "jdtlsError" not in r],
        "withErrors": [r for r in results if "jdtlsError" in r],
        "skipped": skipped,
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "summaryFile": str(summary_path.relative_to(out_root))
        if summary_path.is_relative_to(out_root)
        else str(summary_path),
        "resolvedCount": len(payload["resolved"]),
        "errorCount": len(payload["withErrors"]),
        "skippedCount": len(skipped),
        "results": results,
    }
