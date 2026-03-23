"""Java analyze operations (aligned with LiteClaw java-analyze.ts + lsp/index.ts). Default: full JSON, no truncation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from liteclaw_lsp.client import LSPClient, create_client

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
        return f"错误: 未知操作 {operation}，支持 {', '.join(sorted(OPERATIONS))}"

    root_path = Path(project_path).resolve()
    if not root_path.exists():
        return f"错误: 项目路径不存在 {project_path}"

    client = create_client(project_path, jdtls_path=jdtls_path)
    root = client.root

    try:
        result: Any = None
        if op == "documentSymbol":
            if not file_path or not str(file_path).strip():
                return "错误: documentSymbol 需要 file_path"
            fp = str(file_path).strip()
            abs_path = (Path(root) / fp).resolve() if not Path(fp).is_absolute() else Path(fp).resolve()
            if not abs_path.exists() or abs_path.suffix.lower() != ".java":
                return f"错误: 文件不存在或不是 .java 文件: {file_path}"
            client.open_file(str(abs_path))
            uri = abs_path.as_uri()
            result = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
            result = _norm_list(result)

        elif op == "workspaceSymbol":
            q = (query or "").strip()
            result = _workspace_symbol_with_retry(client, q)

        elif op in (
            "definition",
            "references",
            "hover",
            "implementation",
            "incomingCalls",
            "outgoingCalls",
        ):
            if not file_path or not str(file_path).strip():
                return f"错误: {op} 需要 file_path"
            fp = str(file_path).strip()
            abs_path = (Path(root) / fp).resolve() if not Path(fp).is_absolute() else Path(fp).resolve()
            if not abs_path.exists():
                return f"错误: 文件不存在: {file_path}"
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
            return f"无结果: {op}"

        text = json.dumps(result, ensure_ascii=False, indent=2)

        if op == "documentSymbol" and file_path:
            return f"[file: {file_path}]\n{text}"
        return text
    finally:
        client.shutdown()


__all__ = ["analyze_sync", "OPERATIONS"]
