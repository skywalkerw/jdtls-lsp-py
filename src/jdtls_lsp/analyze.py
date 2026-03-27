"""Java analyze operations (aligned with LiteClaw java-analyze.ts + lsp/index.ts). Default: full JSON, no truncation."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from jdtls_lsp.client import LSPClient, create_client
from jdtls_lsp.java_grep import keyword_search_variants
from jdtls_lsp.logutil import format_payload

_log = logging.getLogger("jdtls_lsp.analyze")

WORKSPACE_SYMBOL_WARMUP_S = 8.0

OPERATIONS = frozenset(
    {
        "documentSymbol",
        "workspaceSymbol",
        "definition",
        "references",
        "hover",
        "implementation",
        "incomingCalls",
        "outgoingCalls",
    }
)


def _norm_list(result: Any) -> list[Any]:
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return [result]


def _workspace_symbol_with_retry(client: LSPClient, query: str) -> list[Any]:
    def search() -> list[Any]:
        try:
            r = client.request("workspace/symbol", {"query": query})
            return _norm_list(r)[:20]
        except Exception:
            return []

    out = search()
    if not out and query.strip():
        time.sleep(WORKSPACE_SYMBOL_WARMUP_S)
        out = search()
    return out


def _merge_workspace_symbol_queries(client: LSPClient, query: str) -> list[Any]:
    """Multiple ``|`` / ``｜``-separated needles; merge ``workspace/symbol`` results with dedupe."""
    variants = keyword_search_variants(query)
    if not variants:
        return []
    seen: set[tuple[Any, ...]] = set()
    merged: list[Any] = []
    for v in variants:
        for s in _workspace_symbol_with_retry(client, v):
            if not isinstance(s, dict):
                continue
            loc = s.get("location") or {}
            uri = loc.get("uri", "") if isinstance(loc, dict) else ""
            rng = loc.get("range") or {} if isinstance(loc, dict) else {}
            st = rng.get("start") or {} if isinstance(rng, dict) else {}
            key = (uri, str(s.get("name")), int(s.get("kind", 0)), int(st.get("line", -1)))
            if key in seen:
                continue
            seen.add(key)
            merged.append(s)
    return merged[:20]


def analyze_sync(
    project_path: str,
    operation: str,
    *,
    file_path: str | None = None,
    line: int | None = None,
    character: int | None = None,
    query: str | None = None,
    jdtls_path: Path | None = None,
) -> str:
    """
    Run one lsp_java_analyze-equivalent operation. Returns JSON string or error message.
    line/character are 1-based (same as LiteClaw tool). Output is not truncated.
    """
    op = operation.strip()
    if op not in OPERATIONS:
        msg = f"错误: 未知操作 {operation}，支持 {', '.join(sorted(OPERATIONS))}"
        _log.warning("%s", msg)
        return msg

    root_path = Path(project_path).resolve()
    if not root_path.exists():
        msg = f"错误: 项目路径不存在 {project_path}"
        _log.warning("%s", msg)
        return msg

    _log.info(
        "analyze_sync operation=%s project=%s file=%s line=%s char=%s query=%s jdtls_path=%s",
        op,
        project_path,
        file_path,
        line,
        character,
        query,
        jdtls_path,
    )

    client = create_client(project_path, jdtls_path=jdtls_path)
    root = client.root

    try:
        result: Any = None
        if op == "documentSymbol":
            if not file_path or not str(file_path).strip():
                msg = "错误: documentSymbol 需要 file_path"
                _log.warning("%s", msg)
                return msg
            fp = str(file_path).strip()
            abs_path = (Path(root) / fp).resolve() if not Path(fp).is_absolute() else Path(fp).resolve()
            if not abs_path.exists() or abs_path.suffix.lower() != ".java":
                msg = f"错误: 文件不存在或不是 .java 文件: {file_path}"
                _log.warning("%s", msg)
                return msg
            client.open_file(str(abs_path))
            uri = abs_path.as_uri()
            result = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
            result = _norm_list(result)

        elif op == "workspaceSymbol":
            q = (query or "").strip()
            result = _merge_workspace_symbol_queries(client, q)

        elif op in (
            "definition",
            "references",
            "hover",
            "implementation",
            "incomingCalls",
            "outgoingCalls",
        ):
            if not file_path or not str(file_path).strip():
                msg = f"错误: {op} 需要 file_path"
                _log.warning("%s", msg)
                return msg
            fp = str(file_path).strip()
            abs_path = (Path(root) / fp).resolve() if not Path(fp).is_absolute() else Path(fp).resolve()
            if not abs_path.exists():
                msg = f"错误: 文件不存在: {file_path}"
                _log.warning("%s", msg)
                return msg
            ln = int(line) if line is not None else 1
            ch = int(character) if character is not None else 1
            line0 = max(0, ln - 1)
            char0 = max(0, ch - 1)
            uri = abs_path.as_uri()
            client.open_file(str(abs_path))

            if op == "definition":
                r = client.request(
                    "textDocument/definition",
                    {"textDocument": {"uri": uri}, "position": {"line": line0, "character": char0}},
                )
                result = _norm_list(r)
            elif op == "references":
                r = client.request(
                    "textDocument/references",
                    {
                        "textDocument": {"uri": uri},
                        "position": {"line": line0, "character": char0},
                        "context": {"includeDeclaration": True},
                    },
                )
                result = _norm_list(r)
            elif op == "hover":
                result = client.request(
                    "textDocument/hover",
                    {"textDocument": {"uri": uri}, "position": {"line": line0, "character": char0}},
                )
            elif op == "implementation":
                r = client.request(
                    "textDocument/implementation",
                    {"textDocument": {"uri": uri}, "position": {"line": line0, "character": char0}},
                )
                result = _norm_list(r)
            elif op == "incomingCalls":
                items = client.request(
                    "textDocument/prepareCallHierarchy",
                    {"textDocument": {"uri": uri}, "position": {"line": line0, "character": char0}},
                )
                arr = _norm_list(items)
                if not arr:
                    result = []
                else:
                    calls = client.request("callHierarchy/incomingCalls", {"item": arr[0]})
                    result = _norm_list(calls)
            elif op == "outgoingCalls":
                items = client.request(
                    "textDocument/prepareCallHierarchy",
                    {"textDocument": {"uri": uri}, "position": {"line": line0, "character": char0}},
                )
                arr = _norm_list(items)
                if not arr:
                    result = []
                else:
                    calls = client.request("callHierarchy/outgoingCalls", {"item": arr[0]})
                    result = _norm_list(calls)

        if isinstance(result, list) and len(result) == 0:
            msg = f"无结果: {op}"
            _log.info("%s", msg)
            return msg

        text = json.dumps(result, ensure_ascii=False, indent=2)

        if op == "documentSymbol" and file_path:
            out = f"[file: {file_path}]\n{text}"
        else:
            out = text
        _log.info("analyze_sync result chars=%s", len(out))
        _log.debug("analyze_sync result=%s", format_payload(out))
        return out
    finally:
        client.shutdown()


__all__ = ["analyze_sync", "OPERATIONS"]
