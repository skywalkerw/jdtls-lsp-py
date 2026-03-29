"""step4 / CLI ``--rest-callchains-down``：编排 REST 端点 → ``jdtls_lsp.callchain.trace_outgoing_subgraph_sync``；摘要走 ``callchain.format``。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jdtls_lsp.callchain import summarize_trace_down_json, trace_outgoing_subgraph_sync
from jdtls_lsp.logutil import get_logger

if TYPE_CHECKING:
    from jdtls_lsp.client import LSPClient

_log = get_logger("reverse_design.rest_callchains_down")


def infer_service_impl_fqcn(project_root: Path, controller_fqcn: str) -> str | None:
    """
    Spring 常见约定：``com.foo.controller.XxxController`` → ``com.foo.service.impl.XxxServiceImpl``。

    若对应 ``src/main/java/.../XxxServiceImpl.java`` 存在则返回全限定名，否则 ``None``。
    用于避免从 Controller 出发时 outgoing 落在 **Service 接口** 上导致 BFS 无法进入实现类与 Repository。
    """
    fq = (controller_fqcn or "").strip()
    parts = fq.split(".")
    if len(parts) < 4 or parts[-2] != "controller":
        return None
    simple = parts[-1]
    if not simple.endswith("Controller") or simple == "Controller":
        return None
    base = simple[: -len("Controller")]
    if not base:
        return None
    impl_simple = f"{base}ServiceImpl"
    pkg_parts = parts[:-2]
    candidate = ".".join(pkg_parts) + ".service.impl." + impl_simple
    rel = Path("src/main/java") / (candidate.replace(".", "/") + ".java")
    p = project_root.resolve() / rel
    return candidate if p.is_file() else None


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


def run_rest_callchains_down(
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
    编排：对 ``rest_map["endpoints"]`` 逐项决策锚点类/方法，调用 ``callchain.trace_outgoing_subgraph_sync``
    （``output_format="markdown"``，与 ``callchain.format.format_downchain_markdown`` 一致），
    用 ``callchain.summarize_trace_down_json`` 写汇总行（支持从 Markdown 内嵌 JSON 解析）。

    ``max_endpoints``：``<=0`` 表示处理全部；否则只处理前 N 条（按 JSON 顺序）。
    若传入 ``lsp_client``，各端点复用该连接（由调用方 ``create_client`` / ``shutdown``）。

    ``data_dir``：各端点 ``callchain-down-rest-*.md``（Markdown，文末含完整 JSON）；``output_root``：``rest-callchains-down-summary.json``。
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

        if not cls or not meth:
            row["jdtlsError"] = "错误: rest-map 端点缺少 className 或 methodName"
            results.append(row)
            _log.warning("rest down: skip endpoint missing class/method path=%s", path)
            continue

        trace_cls, trace_meth = cls, meth
        impl_fqcn = infer_service_impl_fqcn(root, cls)
        if impl_fqcn:
            trace_cls, trace_meth = impl_fqcn, meth
            row["restControllerClassName"] = cls
            row["anchorClassName"] = impl_fqcn
            row["anchorResolution"] = "service_impl_from_controller"
            _log.info(
                "rest down: anchor %s -> %s (service impl)",
                cls,
                impl_fqcn,
            )
        else:
            row["anchorClassName"] = cls
            row["anchorResolution"] = "rest_controller"

        _log.info(
            "rest down: start %s %s -> %s.%s",
            http_m,
            path,
            trace_cls,
            trace_meth,
        )
        raw = trace_outgoing_subgraph_sync(
            str(root),
            trace_cls,
            trace_meth,
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

        fn = data_dir / f"callchain-down-rest-{slug}.md"
        rel_out = str(fn.relative_to(data_dir.parent)) if fn.is_relative_to(data_dir.parent) else str(fn)
        row["outputFile"] = rel_out

        if raw.startswith("错误:") and impl_fqcn:
            _log.warning(
                "rest down: impl anchor failed, fallback controller %s.%s",
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
            row["anchorResolution"] = "rest_controller_fallback_after_impl_error"
            del row["restControllerClassName"]
            row["anchorClassName"] = cls

        if raw.startswith("错误:"):
            row["jdtlsError"] = raw[:2000]
            results.append(row)
            _log.warning("rest down: failed %s %s detail=%s", http_m, path, raw[:350])
            continue

        fn.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
        row["summary"] = summarize_trace_down_json(raw)
        results.append(row)
        _log.info(
            "rest down: ok %s %s nodes=%s",
            http_m,
            path,
            row.get("summary", {}).get("nodeCount"),
        )

    summary_path = out_root / "rest-callchains-down-summary.json"
    payload = {
        "projectRoot": str(root),
        "endpointsTotal": len(eps) if isinstance(eps, list) else 0,
        "endpointsProcessed": len(raw_list),
        "maxEndpointsCap": max_endpoints if max_endpoints and max_endpoints > 0 else None,
        "resolved": [r for r in results if "jdtlsError" not in r],
        "withErrors": [r for r in results if "jdtlsError" in r],
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "summaryFile": str(summary_path.relative_to(out_root))
        if summary_path.is_relative_to(out_root)
        else str(summary_path),
        "resolvedCount": len(payload["resolved"]),
        "errorCount": len(payload["withErrors"]),
        "results": results,
    }
