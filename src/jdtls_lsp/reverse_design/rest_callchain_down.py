"""**历史实现**：从 ``rest-map`` 编排 REST 端点 → ``jdtls_lsp.callchain.trace_outgoing_subgraph_sync``。

bundle 的 step4 已切换为 ``entrypoint_callchain_down``（CLI ``--entrypoint-callchain-down``）。
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

_log = get_logger("reverse_design.rest_callchain_down")


def _candidate_impl_simple_names(handler_simple: str) -> list[str]:
    """由处理器简单类名推导可能的 ``*ServiceImpl`` 简单名（去重保序）。"""
    s = handler_simple
    out: list[str] = []
    if s.endswith("Controller") and len(s) > len("Controller") and s != "Controller":
        out.append(s[: -len("Controller")] + "ServiceImpl")
    for suf in ("Api", "Resource", "Endpoint", "Handler", "Rest"):
        if s.endswith(suf) and len(s) > len(suf):
            out.append(s[: -len(suf)] + "ServiceImpl")
    out.append(s + "ServiceImpl")
    seen: set[str] = set()
    deduped: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def _candidate_service_impl_packages(pkg: str) -> list[str]:
    """由处理器所在包推导若干 ``….service.impl`` 候选包。"""
    parts = pkg.split(".")
    out: list[str] = []
    if len(parts) >= 2:
        out.append(".".join(parts[:-1] + ["service", "impl"]))
    for i in range(len(parts) - 1, 0, -1):
        out.append(".".join(parts[:i] + ["service", "impl"]))
    seen: set[str] = set()
    deduped: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def infer_service_impl_fqcn(project_root: Path, controller_fqcn: str) -> str | None:
    """
    Spring 常见约定：``com.foo.controller.XxxController`` → ``com.foo.service.impl.XxxServiceImpl``。

    兼容 **非** ``*Controller`` 类名或 **非** ``…controller…`` 包：在同模块 ``service.impl`` 下按
    ``XxxApi`` / ``XxxResource`` 等后缀推导 ``XxxServiceImpl``，若对应 ``*.java`` 存在则返回。

    若对应 ``src/main/java/.../XxxServiceImpl.java`` 存在则返回全限定名，否则 ``None``。
    用于避免从 Web 入口出发时 outgoing 落在 **Service 接口** 上导致 BFS 无法进入实现类与 Repository。
    """
    fq = (controller_fqcn or "").strip()
    parts = fq.split(".")
    if len(parts) < 2:
        return None
    simple = parts[-1]
    pkg = ".".join(parts[:-1])
    root = project_root.resolve()

    def _exists(candidate: str) -> bool:
        rel = Path("src/main/java") / (candidate.replace(".", "/") + ".java")
        return (root / rel).is_file()

    # A) 经典 …controller.XxxController → …service.impl.XxxServiceImpl
    if len(parts) >= 4 and parts[-2] == "controller":
        if simple.endswith("Controller") and simple != "Controller":
            base = simple[: -len("Controller")]
            if base:
                impl_simple = f"{base}ServiceImpl"
                candidate_pkg = ".".join(parts[:-2]) + ".service.impl"
                candidate = f"{candidate_pkg}.{impl_simple}"
                if _exists(candidate):
                    return candidate

    # B) 任意包/类名：在候选 ``service.impl`` 包下尝试若干 ``*ServiceImpl``
    for impl_simple in _candidate_impl_simple_names(simple):
        for cand_pkg in _candidate_service_impl_packages(pkg):
            candidate = f"{cand_pkg}.{impl_simple}"
            if _exists(candidate):
                return candidate
    return None


def safe_controller_dirname(controller_fqcn: str) -> str:
    """REST Controller 全限定名 → 安全目录名（与 ``safe_table_filename`` 同类规则）。"""
    s = re.sub(r"[^\w\-.]+", "_", (controller_fqcn or "").strip())[:120].strip("_")
    return s or "controller"


# 按 Controller 分子目录：data/callchain-down-rest/<safe_fqcn>/callchain-down-rest-*.md
_REST_DOWN_ROOT_DIRNAME = "callchain-down-rest"


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
    **稳定** REST 端点编号：由 ``rest-map`` 语义（方法、路径、Controller、处理器、产物 slug、可选源文件行）派生，
    同一端点在多次 bundle 中保持不变（``re-`` + 16 位 hex）。
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
    编排：对 ``rest_map["endpoints"]`` 逐项决策锚点类/方法，调用 ``callchain.trace_outgoing_subgraph_sync``
    （``output_format="markdown"``，与 ``callchain.format.format_downchain_markdown`` 一致），
    用 ``callchain.summarize_trace_down_json`` 写汇总行（支持从 Markdown 内嵌 JSON 解析）。

    ``max_endpoints``：``<=0`` 表示处理全部；否则只处理前 N 条（按 JSON 顺序）。
    若传入 ``lsp_client``，各端点复用该连接（由调用方 ``create_client`` / ``shutdown``）。

    ``data_dir``：各端点 ``data/callchain-down-rest/<Controller FQCN>/callchain-down-rest-*.md``（Markdown，文末含完整 JSON）；``output_root``：``rest-callchain-down-summary.json``。
    成功写入的报告在 ``query.restMapAnchor`` 中含 **restHitId**（``re-`` + 16 hex）及 rest-map 追溯字段；``rest-callchain-down-summary.json`` 按 **Controller FQCN** 分组：``resolvedByController`` / ``withErrorsByController``（键同 ``data/callchain-down-rest/<Controller>/``）；每条 result 亦含 ``restHitId`` / ``restMapAnchor``。
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

        fn = ep_out_dir / f"callchain-down-rest-{slug}.md"
        rel_out = str(fn.relative_to(data_dir.parent)) if fn.is_relative_to(data_dir.parent) else str(fn)
        row["outputFile"] = rel_out
        row["outputSubdir"] = f"data/{_REST_DOWN_ROOT_DIRNAME}/{safe_ctrl}/"

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

        rest_anchor["anchorClassName"] = row["anchorClassName"]
        rest_anchor["anchorMethodName"] = meth
        rest_anchor["anchorResolution"] = row["anchorResolution"]
        rcn = row.get("restControllerClassName")
        if rcn:
            rest_anchor["restControllerClassName"] = rcn
        row["restMapAnchor"] = rest_anchor

        if raw.startswith("错误:"):
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
            cls = str(r.get("className") or "").strip()
            k = safe_controller_dirname(cls) if cls else "_unknown"
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


__all__ = ["run_rest_callchain_down"]

