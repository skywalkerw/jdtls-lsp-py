"""step4：向下调用链编排。

1. **entrypoints**：来自 ``entry_scan.scan_java_entrypoints``（``file`` + ``line``）→
   ``trace_outgoing_subgraph_sync`` 的 ``file_path`` / ``line`` 分支。

2. **REST（rest-map）**：对 ``rest_map["endpoints"]`` 逐项用 **Controller 类名 + 处理器方法名** 调用
   ``trace_outgoing_subgraph_sync``（与 CLI ``callchain-down`` 同源）。接口上的 outgoing 由
   ``callchain.trace`` 内 ``textDocument/implementation`` 兜底进入实现类，**不再**用文件名猜测 ``*ServiceImpl``。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jdtls_lsp.callchain import summarize_trace_down_json, trace_outgoing_subgraph_sync
from jdtls_lsp.callchain.format import apply_rest_map_anchor_to_downchain_markdown
from jdtls_lsp.logutil import get_logger

if TYPE_CHECKING:
    from jdtls_lsp.client import LSPClient

_log = get_logger("reverse_design.entrypoint_callchain_down")

_ENTRYPOINT_DOWN_ROOT_DIRNAME = "callchain-down-entrypoints"
_REST_DOWN_ROOT_DIRNAME = "callchain-down-rest"


def _safe_file_dirname(rel_file: str) -> str:
    s = (rel_file or "").strip().replace("\\", "/")
    s = re.sub(r"[^\w\-.]+", "_", s)
    s = s.strip("_")[:160]
    return s or "unknown"


def safe_controller_dirname(controller_fqcn: str) -> str:
    """REST Controller 全限定名 → 安全目录名（与 ``safe_table_filename`` 同类规则）。"""
    s = re.sub(r"[^\w\-.]+", "_", (controller_fqcn or "").strip())[:120].strip("_")
    return s or "controller"


def stable_rest_endpoint_hit_id(
    http_method: str,
    path: str,
    controller_fqcn: str,
    handler_method: str,
    *,
    slug: str = "",
    source_file: str = "",
    source_line: int = 0,
) -> str:
    """
    **稳定** REST 端点编号：由 ``rest-map`` 语义派生，同一端点在多次 bundle 中保持不变（``re-`` + 16 位 hex）。
    """
    rel = str(source_file).replace("\\", "/").strip()
    payload = (
        f"{str(http_method).strip().upper()}|{str(path).strip()}|"
        f"{str(controller_fqcn).strip()}|{str(handler_method).strip()}|"
        f"{str(slug)}|{rel}|{int(source_line)}"
    ).encode("utf-8")
    return "re-" + hashlib.sha256(payload).hexdigest()[:16]


def endpoint_slug(ep: dict[str, Any]) -> str:
    """生成用于文件名的短标签：``GET_api_foo_bar``。"""
    hm = str(ep.get("httpMethod", "X")).strip().upper() or "X"
    path = str(ep.get("path", "") or "").strip()
    if not path or path == "/":
        body = "root"
    else:
        body = path.strip("/").replace("/", "_")
    body = re.sub(r"[^\w\-]+", "_", body).strip("_")[:72]
    if not body:
        body = "root"
    s = f"{hm}_{body}"
    return s[:100] if len(s) > 100 else s


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


def run_rest_callchain_down(
    project_root: Path,
    rest_map: dict[str, Any],
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
    """
    对 ``rest_map["endpoints"]`` 逐项用 **Controller 全限定类名 + 处理器方法名** 调用
    ``trace_outgoing_subgraph_sync``（与 CLI ``callchain-down --class --method`` 一致）。

    追踪进入 Service 实现类依赖 ``callchain.trace`` 内对接口/abstract 的 ``implementation`` 兜底，**不**再按约定猜测 ``*ServiceImpl`` 路径。
    """
    root = project_root.resolve()
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    out_root = (output_root.resolve() if output_root is not None else data_dir.parent.resolve())
    out_root.mkdir(parents=True, exist_ok=True)

    eps = rest_map.get("endpoints")
    if not isinstance(eps, list):
        return {"error": "rest-map 缺少 endpoints 数组", "results": []}

    raw_list: list[dict[str, Any]] = [x for x in eps if isinstance(x, dict)]
    if max_endpoints and max_endpoints > 0:
        raw_list = raw_list[: int(max_endpoints)]

    slug_counts: dict[str, int] = {}
    results: list[dict[str, Any]] = []

    for ep in raw_list:
        cls = (ep.get("className") or ep.get("simpleClassName") or "").strip()
        meth = (ep.get("methodName") or "").strip()
        path = str(ep.get("path", ""))
        http_m = str(ep.get("httpMethod", ""))
        base_slug = endpoint_slug(ep)
        n = slug_counts.get(base_slug, 0) + 1
        slug_counts[base_slug] = n
        slug = base_slug if n == 1 else f"{base_slug}_{n}"

        row: dict[str, Any] = {
            "httpMethod": http_m,
            "path": path,
            "className": cls,
            "methodName": meth,
            "slug": slug,
        }

        ep_file = str(ep.get("file", "") or "").replace("\\", "/")
        ep_line = int(ep.get("line") or 0)
        re_id = stable_rest_endpoint_hit_id(
            http_m,
            path,
            cls,
            meth,
            slug=slug,
            source_file=ep_file,
            source_line=ep_line,
        )
        row["restHitId"] = re_id
        rest_anchor: dict[str, Any] = {
            "restHitId": re_id,
            "httpMethod": http_m,
            "path": path,
            "slug": slug,
            "controllerClassName": cls,
            "handlerMethodName": meth,
        }
        if ep_file:
            rest_anchor["restMapFile"] = ep_file
        if ep_line >= 1:
            rest_anchor["restMapLine"] = ep_line
        ann = ep.get("annotation")
        if ann:
            rest_anchor["annotation"] = ann

        if not cls or not meth:
            row["restMapAnchor"] = rest_anchor
            row["jdtlsError"] = "错误: rest-map 端点缺少 className 或 methodName"
            results.append(row)
            _log.warning("rest down: skip endpoint missing class/method path=%s", path)
            continue

        safe_ctrl = safe_controller_dirname(cls)
        ep_out_dir = data_dir / _REST_DOWN_ROOT_DIRNAME / safe_ctrl
        ep_out_dir.mkdir(parents=True, exist_ok=True)

        row["anchorClassName"] = cls
        row["anchorResolution"] = "rest_controller"

        _log.info(
            "rest down: start %s %s -> %s.%s",
            http_m,
            path,
            cls,
            meth,
        )
        raw = trace_outgoing_subgraph_sync(
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
            max_nodes=max_nodes,
            max_branches=max_branches,
            output_format="markdown",
        )

        fn = ep_out_dir / f"callchain-down-rest-{slug}.md"
        rel_out = str(fn.relative_to(data_dir.parent)) if fn.is_relative_to(data_dir.parent) else str(fn)
        row["outputFile"] = rel_out
        row["outputSubdir"] = f"data/{_REST_DOWN_ROOT_DIRNAME}/{safe_ctrl}/"

        rest_anchor["anchorClassName"] = row["anchorClassName"]
        rest_anchor["anchorMethodName"] = meth
        rest_anchor["anchorResolution"] = row["anchorResolution"]
        row["restMapAnchor"] = rest_anchor

        if isinstance(raw, str) and raw.startswith("错误:"):
            row["jdtlsError"] = raw[:2000]
            results.append(row)
            _log.warning("rest down: failed %s %s detail=%s", http_m, path, raw[:350])
            continue

        raw = apply_rest_map_anchor_to_downchain_markdown(raw, rest_anchor)
        fn.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
        row["summary"] = summarize_trace_down_json(raw)
        results.append(row)
        _log.info(
            "rest down: ok %s %s nodes=%s",
            http_m,
            path,
            row.get("summary", {}).get("nodeCount"),
        )

    resolved_rows = [r for r in results if "jdtlsError" not in r]
    error_rows = [r for r in results if "jdtlsError" in r]

    def _by_controller(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        d: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            c = str(r.get("className") or "").strip()
            k = safe_controller_dirname(c) if c else "_unknown"
            d.setdefault(k, []).append(r)
        return dict(sorted(d.items(), key=lambda x: x[0]))

    resolved_by_ctrl = _by_controller(resolved_rows)
    errors_by_ctrl = _by_controller(error_rows)

    summary_path = out_root / "rest-callchain-down-summary.json"
    payload = {
        "projectRoot": str(root),
        "endpointsTotal": len(eps) if isinstance(eps, list) else 0,
        "endpointsProcessed": len(raw_list),
        "maxEndpointsCap": max_endpoints if max_endpoints and max_endpoints > 0 else None,
        "options": {
            "restCallchainSubdirPattern": f"data/{_REST_DOWN_ROOT_DIRNAME}/<controller_fqcn>/",
            "restHitIdNote": "每条结果 restHitId = SHA-256(httpMethod|path|controller|handler|slug|restMapFile|restMapLine) 前 16 hex，前缀 re-；与 rest-map 端点一一对应可追溯",
            "summaryGrouping": "resolvedByController / withErrorsByController 的键与 data/callchain-down-rest/<Controller FQCN>/ 目录名一致",
            "anchorNote": "锚点为 rest-map 中的 Controller 处理器方法；进入 Service 实现类依赖 callchain trace 的 implementation 兜底（非路径猜测 ServiceImpl）",
        },
        "resolvedByController": resolved_by_ctrl,
        "withErrorsByController": errors_by_ctrl,
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    res_n = sum(len(v) for v in resolved_by_ctrl.values())
    err_n = sum(len(v) for v in errors_by_ctrl.values())
    return {
        "summaryFile": str(summary_path.relative_to(out_root))
        if summary_path.is_relative_to(out_root)
        else str(summary_path),
        "resolvedCount": res_n,
        "errorCount": err_n,
        "results": results,
    }


__all__ = [
    "endpoint_slug",
    "run_entrypoint_callchain_down",
    "run_rest_callchain_down",
    "safe_controller_dirname",
    "stable_rest_endpoint_hit_id",
]
