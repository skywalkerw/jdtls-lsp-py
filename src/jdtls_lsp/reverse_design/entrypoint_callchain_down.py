"""step4 替代：按 entrypoints 向下展开调用链。

起点来自 ``entry_scan.scan_java_entrypoints``：
- 包含控制器（``@Controller`` / ``@RestController``）的所有 ``public`` 方法；
- 以及 ``java_entry_patterns`` 里识别到的其他典型入口行（如 main、消息监听、定时等）。

本阶段不依赖 REST ``rest-map``；只要提供 ``file`` + ``line``，就用
``callchain.trace_outgoing_subgraph_sync`` 的 ``file_path`` / ``line`` 分支来定位 LSP 方法符号。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jdtls_lsp.callchain import summarize_trace_down_json, trace_outgoing_subgraph_sync
from jdtls_lsp.logutil import get_logger

if TYPE_CHECKING:
    from jdtls_lsp.client import LSPClient

_log = get_logger("reverse_design.entrypoint_callchain_down")

_ENTRYPOINT_DOWN_ROOT_DIRNAME = "callchain-down-entrypoints"


def _safe_file_dirname(rel_file: str) -> str:
    s = (rel_file or "").strip().replace("\\", "/")
    s = re.sub(r"[^\w\-.]+", "_", s)
    s = s.strip("_")[:160]
    return s or "unknown"


def run_entrypoint_callchain_down(
    project_root: Path,
    entrypoints: list[dict[str, Any]],
    data_dir: Path,
    *,
    jdtls_path: Path | None,
    max_endpoints: int,
    max_depth: int,
    max_nodes: int,
    max_branches: int,
    lsp_client: LSPClient | None = None,
    output_root: Path | None = None,
) -> dict[str, Any]:
    """对 entrypoints 逐项执行 callchain-down（向下 BFS）。"""

    root = project_root.resolve()
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    out_root = (output_root.resolve() if output_root is not None else data_dir.parent.resolve())
    out_root.mkdir(parents=True, exist_ok=True)

    raw_list: list[dict[str, Any]] = [x for x in entrypoints if isinstance(x, dict)]
    if max_endpoints and max_endpoints > 0:
        raw_list = raw_list[: int(max_endpoints)]

    results: list[dict[str, Any]] = []

    _log.info(
        "entrypoint down: start entrypoints=%s processed=%s depth=%s nodes=%s branches=%s",
        len(entrypoints),
        len(raw_list),
        max_depth,
        max_nodes,
        max_branches,
    )

    for idx, ep in enumerate(raw_list):
        file = str(ep.get("file", "") or "").strip()
        kind = str(ep.get("kind", "") or "").strip() or "entrypoint"
        line_v = ep.get("line")
        try:
            line = int(line_v) if line_v is not None else 0
        except (TypeError, ValueError):
            line = 0
        if not file or line < 1:
            row = {
                "entrypointIndex": idx,
                "kind": kind,
                "file": file,
                "line": line_v,
                "jdtlsError": "错误: entrypoint 缺少有效 file/line",
            }
            results.append(row)
            continue

        safe_dir = _safe_file_dirname(file)
        ep_out_dir = data_dir / _ENTRYPOINT_DOWN_ROOT_DIRNAME / safe_dir
        ep_out_dir.mkdir(parents=True, exist_ok=True)

        slug = re.sub(r"[^\w\-]+", "_", kind)[:64]
        fn = ep_out_dir / f"callchain-down-entrypoints-{slug}-{line}-{idx:04d}.md"

        _log.info("entrypoint down: %s %s:%s", kind, file, line)
        raw = trace_outgoing_subgraph_sync(
            str(root),
            class_name=None,
            method_name=None,
            file_path=file,
            line=line,
            character=1,
            symbol_query=None,
            jdtls_path=jdtls_path,
            lsp_client=lsp_client,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_branches=max_branches,
            output_format="markdown",
        )

        rel_out = str(fn.relative_to(data_dir.parent)) if fn.is_relative_to(data_dir.parent) else str(fn)

        row: dict[str, Any] = {
            "entrypointIndex": idx,
            "kind": kind,
            "file": file,
            "line": line,
            "outputFile": rel_out,
            "outputSubdir": f"data/{_ENTRYPOINT_DOWN_ROOT_DIRNAME}/{safe_dir}/",
        }

        if isinstance(raw, str) and raw.startswith("错误:"):
            row["jdtlsError"] = raw[:2000]
            results.append(row)
            _log.warning("entrypoint down: failed %s %s:%s", kind, file, line)
            continue

        fn.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
        row["summary"] = summarize_trace_down_json(raw)
        results.append(row)

    resolved_rows = [r for r in results if "jdtlsError" not in r]
    error_rows = [r for r in results if "jdtlsError" in r]

    summary_path = out_root / "entrypoint-callchain-down-summary.json"
    payload = {
        "projectRoot": str(root),
        "entrypointsTotal": len(entrypoints),
        "entrypointsProcessed": len(raw_list),
        "maxEndpointsCap": max_endpoints if max_endpoints and max_endpoints > 0 else None,
        "options": {
            "entrypointCallchainSubdirPattern": f"data/{_ENTRYPOINT_DOWN_ROOT_DIRNAME}/<safe_entrypoint_file>/",
        },
        "resolvedCount": len(resolved_rows),
        "errorCount": len(error_rows),
        "results": results,
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "summaryFile": str(summary_path.relative_to(out_root)) if summary_path.is_relative_to(out_root) else str(summary_path),
        "resolvedCount": len(resolved_rows),
        "errorCount": len(error_rows),
        "results": results,
    }


__all__ = ["run_entrypoint_callchain_down"]

