"""step8 主入口：聚合 step1–step3（扫描），可选 step4–step6（调用链与业务摘要），写入 ``design/``。

编排 **step1–8**（见 ``需求.md``），目录布局见 ``run_design_bundle``。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jdtls_lsp.callchain import trace_call_chain_sync
from jdtls_lsp.client import create_client
from jdtls_lsp.reverse_design.entrypoint_callchain_down import run_entrypoint_callchain_down
from jdtls_lsp.entry_scan import scan_java_entrypoints, scan_rest_map
from jdtls_lsp.reverse_design.batch_symbols_by_package import batch_symbols_by_package
from jdtls_lsp.reverse_design.scan_modules import scan_modules
from jdtls_lsp.reverse_design.table_callchain_up import run_table_callchain_up
from jdtls_lsp.business_summary import format_business_md, merge_key_methods_from_downchain_files
from jdtls_lsp.reverse_design.table_manifest import build_table_manifest
from jdtls_lsp.logutil import get_logger

_log = get_logger("reverse_design.bundle")


def _safe_query_filename(q: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", q.strip())[:72].strip("_")
    return s or "query"


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mermaid_rest_preview(rest: dict[str, Any], out_path: Path, *, cap: int = 48) -> None:
    eps = rest.get("endpoints") if isinstance(rest.get("endpoints"), list) else []
    lines = ["flowchart LR", "  classDef ep fill:#e8f4fc,stroke:#0366d6"]
    for i, ep in enumerate(eps[:cap]):
        if not isinstance(ep, dict):
            continue
        hm = str(ep.get("httpMethod", "?"))
        p = str(ep.get("path", ""))[:56].replace('"', "'")
        nid = f"e{i}"
        label = f"{hm} {p}".strip() or hm
        lines.append(f'  {nid}["{label}"]:::ep')
    if len(eps) > cap:
        lines.append(f'  cap["… +{len(eps) - cap} more"]:::ep')
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def run_design_bundle(
    project_path: str,
    output_dir: Path,
    *,
    queries: list[str] | None = None,
    skip_symbols: bool = False,
    skip_callchain: bool = False,
    skip_rest_map: bool = False,
    skip_scan: bool = False,
    jdtls_path: Path | None = None,
    glob_pattern: str = "**/src/main/java/**/*.java",
    max_symbol_files: int = 200,
    max_rest_map_files: int = 8_000,
    callchain_max_depth: int = 20,
    tables_file: Path | None = None,
    tables_inline: str = "",
    strict_tables_only: bool = False,
    skip_table_manifest: bool = False,
    max_table_java_files: int = 8_000,
    max_table_xml_files: int = 2_000,
    table_callchain_up: bool = False,
    max_table_callchain_java_scan: int = 12_000,
    table_callchain_up_extra: bool = False,
    max_table_up_extra_anchors: int = 24,
    entrypoint_callchain_down: bool = False,
    max_rest_down_endpoints: int = 0,
    rest_down_max_depth: int = 16,
    rest_down_max_nodes: int = 500,
    rest_down_max_branches: int = 48,
    business_summary: bool = False,
) -> dict[str, Any]:
    """
    **step8** 编排：生成层次化目录（与 ``需求.md`` 八步对齐）。

    - **step1**：``modules.json``、``symbols-by-package.json``（可 skip）
    - **step2**：``rest-map.json`` + ``graphs/rest-map.mmd``（可 skip）
    - **step3**：``tables-manifest.json``（可 skip）
    - **step5′**：``queries`` 非空时各关键字 ``callchain-up`` → ``data/callchain-up-*.md``
    - **step5**：``table_callchain_up``（CLI ``--table-callchain-up``）→ ``data/callchain-up-table/<物理表>/callchain-up-table-*.md``、``table-callchain-summary.json``；可选 ``table_callchain_up_extra``（CLI ``--table-callchain-up-extra``）同时打开 JDBC 字符串与 MyBatis Mapper 两类额外起点（``max_table_up_extra_anchors`` 对二者共用上限）
    - **step4**：``entrypoint_callchain_down``（CLI ``--entrypoint-callchain-down``）→ ``data/callchain-down-entrypoints/.../callchain-down-entrypoints-*.md``、``entrypoint-callchain-down-summary.json``
    - **step6**：``business_summary`` → 根目录 ``business.md``（合并各向下链 ``keyMethods``）
    - **step8**：根目录 ``index.md`` + 返回摘要 dict（stdout 由 CLI 打印）

    **step7**（补全实现细节）不在此函数内执行，需 ``analyze`` / 单点 callchain / IDE。

    返回摘要 dict；若项目路径无效则含 ``error`` 字符串。
    """
    root = Path(project_path).resolve()
    if not root.exists():
        _log.warning("reverse-design bundle: project path missing %s", project_path)
        return {"error": f"项目路径不存在: {project_path}"}

    queries = [q.strip() for q in (queries or []) if q.strip()]
    output_dir = output_dir.resolve()
    data = output_dir / "data"
    graphs = output_dir / "graphs"
    data.mkdir(parents=True, exist_ok=True)
    graphs.mkdir(parents=True, exist_ok=True)

    _log.info(
        "reverse-design bundle start project=%s output=%s skip_scan=%s skip_rest_map=%s "
        "skip_symbols=%s skip_table_manifest=%s skip_callchain=%s table_callchain_up=%s "
        "entrypoint_callchain_down=%s business_summary=%s queries=%s "
        "max_symbol_files=%s max_rest_map_files=%s tables_file=%s strict_tables=%s",
        root,
        output_dir,
        skip_scan,
        skip_rest_map,
        skip_symbols,
        skip_table_manifest,
        skip_callchain,
        table_callchain_up,
        entrypoint_callchain_down,
        business_summary,
        queries,
        max_symbol_files,
        max_rest_map_files,
        tables_file,
        strict_tables_only,
    )

    summary: dict[str, Any] = {
        "projectRoot": str(root),
        "outputDir": str(output_dir),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "artifacts": [],
        "warnings": [],
    }

    if not skip_scan:
        _log.info("reverse-design bundle: [step1] running scan_modules")
        mod = scan_modules(root)
        p = data / "modules.json"
        _write_json(p, mod)
        summary["artifacts"].append(str(p.relative_to(output_dir)))
        summary["moduleCount"] = len(mod.get("modules") or [])
        _log.info(
            "reverse-design bundle: [step1] done path=%s buildSystem=%s moduleCount=%s",
            p.relative_to(output_dir),
            mod.get("buildSystem"),
            summary["moduleCount"],
        )

    rest_map_for_down: dict[str, Any] | None = None

    if not skip_rest_map and not entrypoint_callchain_down:
        _log.info("reverse-design bundle: [step2] running rest_map max_files=%s", max_rest_map_files)
        rest = scan_rest_map(root, max_files=max_rest_map_files)
        rest_map_for_down = rest
        p = data / "rest-map.json"
        _write_json(p, rest)
        summary["artifacts"].append(str(p.relative_to(output_dir)))
        summary["endpointCount"] = rest.get("endpointCount", 0)
        _mermaid_rest_preview(rest, graphs / "rest-map.mmd")
        summary["artifacts"].append(str((graphs / "rest-map.mmd").relative_to(output_dir)))
        _log.info(
            "reverse-design bundle: [step2] done endpoints=%s mmd=%s",
            summary["endpointCount"],
            (graphs / "rest-map.mmd").relative_to(output_dir),
        )
    # entrypoint-callchain-down 不依赖 rest-map；因此跳过生成/读取 rest-map。

    manifest_for_callchain: dict[str, Any] | None = None

    if not skip_table_manifest:
        _log.info(
            "reverse-design bundle: [step3] running table_manifest java_max=%s xml_max=%s",
            max_table_java_files,
            max_table_xml_files,
        )
        tm_path = tables_file.resolve() if tables_file else None
        tm = build_table_manifest(
            root,
            tables_file=tm_path,
            tables_inline=tables_inline,
            strict_tables_only=strict_tables_only,
            max_java_files=max_table_java_files,
            max_xml_files=max_table_xml_files,
        )
        if tm.get("error"):
            summary["warnings"].append(tm["error"])
            _log.warning("reverse-design bundle: [step3] table_manifest failed %s", tm["error"])
        else:
            manifest_for_callchain = tm
            p = data / "tables-manifest.json"
            _write_json(p, tm)
            summary["artifacts"].append(str(p.relative_to(output_dir)))
            summary["tableManifest"] = {
                "canonicalCount": len(tm.get("canonicalTables") or []),
                "hitCount": tm.get("extractedHitCount", 0),
                "unresolvedCount": len(tm.get("unresolvedTables") or []),
                "extractedOnlyCount": len(tm.get("extractedOnly") or []),
            }
            if tm.get("unresolvedTables"):
                summary["warnings"].append(
                    "tables-manifest: unresolvedTables="
                    + ",".join(str(x) for x in tm["unresolvedTables"][:12])
                    + ("…" if len(tm["unresolvedTables"]) > 12 else "")
                )
            _log.info(
                "reverse-design bundle: [step3] done path=%s canonical=%s hits=%s unresolved=%s",
                p.relative_to(output_dir),
                len(tm.get("canonicalTables") or []),
                tm.get("extractedHitCount", 0),
                len(tm.get("unresolvedTables") or []),
            )
    elif table_callchain_up:
        tm_existing = data / "tables-manifest.json"
        if tm_existing.is_file():
            try:
                manifest_for_callchain = json.loads(tm_existing.read_text(encoding="utf-8"))
                _log.info(
                    "reverse-design bundle: table_callchain_up 使用已有 %s",
                    tm_existing.relative_to(output_dir),
                )
            except (OSError, json.JSONDecodeError) as e:
                summary["warnings"].append(f"table-callchain-up: 无法读取 tables-manifest.json: {e}")
        else:
            summary["warnings"].append(
                "table-callchain-up: 已 --skip-table-manifest 且缺少 data/tables-manifest.json，跳过按表调用链"
            )

    if not skip_symbols:
        _log.info(
            "reverse-design bundle: [step1 补充] running symbols glob=%s max_files=%s",
            glob_pattern,
            max_symbol_files,
        )
        sym = batch_symbols_by_package(
            str(root),
            jdtls_path=jdtls_path,
            glob_pattern=glob_pattern,
            max_files=max_symbol_files,
        )
        if sym.get("error"):
            summary["warnings"].append(sym["error"])
            _log.warning("reverse-design bundle: [step1 补充] symbols failed %s", sym["error"])
        else:
            p = data / "symbols-by-package.json"
            _write_json(p, sym)
            summary["artifacts"].append(str(p.relative_to(output_dir)))
            summary["packageCount"] = sym.get("packageCount", 0)
            if sym.get("errors"):
                summary["warnings"].extend(str(e) for e in sym["errors"][:10])
            _log.info(
                "reverse-design bundle: [step1 补充] done packages=%s files=%s symbol_errors=%s",
                sym.get("packageCount", 0),
                sym.get("fileCount", 0),
                len(sym.get("errors") or []),
            )

    will_queries = not skip_callchain and bool(queries)
    will_table = bool(table_callchain_up and not skip_callchain and manifest_for_callchain)
    will_rest = bool(entrypoint_callchain_down and not skip_callchain)

    shared_lsp = None
    try:
        if will_queries or will_table or will_rest:
            _log.info(
                "reverse-design bundle: [step4/step5] shared JDTLS client for callchain phases (one JVM)"
            )
            shared_lsp = create_client(str(root), jdtls_path=jdtls_path)

        if will_queries:
            summary["callchainQueries"] = []
            _log.info(
                "reverse-design bundle: [step5′ 关键字向上] running callchain-up for %d query/queries depth=%s",
                len(queries),
                callchain_max_depth,
            )
            for q in queries:
                _log.info("reverse-design bundle: callchain-up start query=%r", q)
                raw = trace_call_chain_sync(
                    str(root),
                    None,
                    None,
                    file_path=None,
                    line=None,
                    character=None,
                    symbol_query=q,
                    jdtls_path=jdtls_path,
                    lsp_client=shared_lsp,
                    max_depth=callchain_max_depth,
                    output_format="markdown",
                )
                fn = data / f"callchain-up-{_safe_query_filename(q)}.md"
                if raw.startswith("错误:"):
                    summary["warnings"].append(f"callchain-up {q!r}: {raw[:240]}")
                    _log.warning("reverse-design bundle: callchain-up failed query=%r detail=%s", q, raw[:300])
                    continue
                fn.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
                summary["artifacts"].append(str(fn.relative_to(output_dir)))
                summary["callchainQueries"].append({"query": q, "file": str(fn.relative_to(output_dir))})
                _log.info(
                    "reverse-design bundle: callchain-up done query=%r out=%s bytes=%s",
                    q,
                    fn.relative_to(output_dir),
                    fn.stat().st_size,
                )

        if will_table:
            _log.info(
                "reverse-design bundle: [step5 按表向上] table_callchain_up depth=%s max_impl_scan=%s extra=%s cap_extra=%s",
                callchain_max_depth,
                max_table_callchain_java_scan,
                bool(table_callchain_up_extra),
                int(max_table_up_extra_anchors),
            )
            extra_anchors = bool(table_callchain_up_extra)
            cap_extra = int(max_table_up_extra_anchors)
            tc = run_table_callchain_up(
                root,
                manifest_for_callchain,
                data,
                jdtls_path=jdtls_path,
                max_depth=callchain_max_depth,
                max_java_scan=max_table_callchain_java_scan,
                lsp_client=shared_lsp,
                output_root=output_dir,
                table_up_sql_literal=extra_anchors,
                table_up_mybatis_mapper=extra_anchors,
                max_table_up_sql_anchors=cap_extra,
                max_table_up_mybatis_anchors=cap_extra,
            )
            if tc.get("error"):
                summary["warnings"].append(f"table-callchain-up: {tc['error']}")
                _log.warning("reverse-design bundle: table_callchain_up failed %s", tc.get("error"))
            else:
                summary["tableCallchainUp"] = {
                    "summaryFile": tc.get("summaryFile"),
                    "resolvedCount": tc.get("resolvedCount", 0),
                    "errorCount": tc.get("errorCount", 0),
                    "skippedCount": tc.get("skippedCount", 0),
                }
                sf = tc.get("summaryFile")
                if sf:
                    summary["artifacts"].append(sf)
                for r in tc.get("results") or []:
                    of = r.get("outputFile")
                    if of and "jdtlsError" not in r and of not in summary["artifacts"]:
                        summary["artifacts"].append(of)
                for r in tc.get("results") or []:
                    if r.get("jdtlsError"):
                        summary["warnings"].append(
                            f"table-callchain-up {r.get('table')!r}: {str(r['jdtlsError'])[:200]}"
                        )
                for sk in tc.get("skipped") or []:
                    summary["warnings"].append(
                        f"table-callchain-up 跳过 {sk.get('table')!r}: {sk.get('reason', '')[:180]}"
                    )
                _log.info(
                    "reverse-design bundle: table_callchain_up done resolved=%s errors=%s skipped=%s",
                    tc.get("resolvedCount"),
                    tc.get("errorCount"),
                    tc.get("skippedCount"),
                )

        if will_rest:
            # 注意：此参数在 CLI 里已被重命名为 entrypoint-callchain-down；这里沿用原变量名以减少改动。
            cap = max_rest_down_endpoints if max_rest_down_endpoints and max_rest_down_endpoints > 0 else 0
            _log.info(
                "reverse-design bundle: [step4 entrypoints 向下] entrypoint_callchain_down cap=%s depth=%s nodes=%s branches=%s",
                cap or "all",
                rest_down_max_depth,
                rest_down_max_nodes,
                rest_down_max_branches,
            )

            # entrypoints 扫描本身是无 JDTLS 的静态过程；这里用 max_rest_map_files 作为扫描 .java 文件上限复用参数。
            eps = scan_java_entrypoints(root, max_files=max_rest_map_files)
            rd = run_entrypoint_callchain_down(
                root,
                eps,
                data,
                jdtls_path=jdtls_path,
                max_endpoints=cap,
                max_depth=rest_down_max_depth,
                max_nodes=rest_down_max_nodes,
                max_branches=rest_down_max_branches,
                lsp_client=shared_lsp,
                output_root=output_dir,
            )

            summary["entrypointCallchainDown"] = {
                "summaryFile": rd.get("summaryFile"),
                "resolvedCount": rd.get("resolvedCount", 0),
                "errorCount": rd.get("errorCount", 0),
            }
            sf = rd.get("summaryFile")
            if sf:
                summary["artifacts"].append(sf)

            for r in rd.get("results") or []:
                of = r.get("outputFile")
                if of and "jdtlsError" not in r and of not in summary["artifacts"]:
                    summary["artifacts"].append(of)
            for r in rd.get("results") or []:
                if r.get("jdtlsError"):
                    summary["warnings"].append(
                        "entrypoint-callchain-down "
                        f"{r.get('kind')!s} {r.get('file')!s}:{r.get('line')!s}: "
                        f"{str(r['jdtlsError'])[:200]}"
                    )
            _log.info(
                "reverse-design bundle: entrypoint_callchain_down done resolved=%s errors=%s",
                rd.get("resolvedCount"),
                rd.get("errorCount"),
            )
    finally:
        if shared_lsp is not None:
            shared_lsp.shutdown()

    if table_callchain_up_extra and not table_callchain_up:
        summary["warnings"].append(
            "table-callchain-up-extra: 未指定 --table-callchain-up，已忽略额外 JDBC/MyBatis 锚点"
        )

    if table_callchain_up and skip_callchain:
        summary["warnings"].append("table-callchain-up: 与 --skip-callchain 同时指定，已跳过按表调用链")

    if entrypoint_callchain_down and skip_callchain:
        summary["warnings"].append(
            "entrypoint-callchain-down: 与 --skip-callchain 同时指定，已跳过 entrypoints 向下链"
        )

    if business_summary:
        merged_km, n_down_reports = merge_key_methods_from_downchain_files(data, root)
        bmd = format_business_md(root, merged_km)
        (output_dir / "business.md").write_text(bmd, encoding="utf-8")
        summary["businessSummary"] = {
            "file": "business.md",
            "mergedCount": len(merged_km),
            "downchainReportFilesRead": n_down_reports,
        }
        _log.info(
            "reverse-design bundle: [step6] business.md merged=%s from_downchain_reports=%s",
            len(merged_km),
            n_down_reports,
        )

    def _ordered_artifact_paths(artifacts: list[str]) -> list[str]:
        """稳定顺序：根目录汇总 → data/ 明细 → graphs/ → 其余。"""
        root_f = sorted(p for p in artifacts if "/" not in p)
        data_f = sorted(p for p in artifacts if p.startswith("data/"))
        graph_f = sorted(p for p in artifacts if p.startswith("graphs/"))
        seen = set(root_f + data_f + graph_f)
        other = [p for p in artifacts if p not in seen]
        return [*root_f, *data_f, *graph_f, *other]

    listed = ["index.md"]
    if business_summary:
        listed.append("business.md")
    listed += _ordered_artifact_paths(summary["artifacts"])

    def _artifact_index_blocks(paths: list[str]) -> list[str]:
        """按根目录 / data / graphs 分组列出产物。"""
        root_files = [p for p in paths if "/" not in p]
        data_files = sorted(p for p in paths if p.startswith("data/"))
        graph_files = sorted(p for p in paths if p.startswith("graphs/"))
        out: list[str] = []
        if root_files:
            out.extend(["### 根目录（索引与汇总）", ""])
            out.extend(f"- `{p}`" for p in root_files)
            out.append("")
        if data_files:
            out.extend(["### data/（扫描与调用链明细）", ""])
            out.extend(f"- `{p}`" for p in data_files)
            out.append("")
        if graph_files:
            out.extend(["### graphs/", ""])
            out.extend(f"- `{p}`" for p in graph_files)
            out.append("")
        if not root_files and not data_files and not graph_files:
            out.extend(["_（无）_", ""])
        return out

    lines = [
        "# Reverse design bundle",
        "",
        f"- **projectRoot**: `{root}`",
        f"- **generatedAt**: `{summary['generatedAt']}`",
        "",
        "## 八步流程（与 `需求.md` 对齐）",
        "",
        "| step | 说明 | 本目录中典型产物 |",
        "| --- | --- | --- |",
        "| step1 | 工程概要 | `data/modules.json`、`data/symbols-by-package.json` |",
        "| step2 | REST 清单 | `data/rest-map.json`、`graphs/rest-map.mmd` |",
        "| step3 | 数据库表清单 | `data/tables-manifest.json` |",
        "| step4 | entrypoints 向下调用链 | `data/callchain-down-entrypoints/<safe_entrypoint_file>/callchain-down-entrypoints-*.md`、`entrypoint-callchain-down-summary.json` |",
        "| step5 | 每表向上调用链 | `data/callchain-up-table/<表>/callchain-up-table-*.md`、`table-callchain-summary.json`（`resolvedByPhysicalTable`，键同目录）；可选 `--table-callchain-up-extra` 另含同目录下 `*-sql-NN` / `*-mapper-NN`；关键字向上见 `callchain-up-*.md` |",
        "| step6 | 关键业务位置 | 向下链报告（`.md` 文末 JSON）内字段；`business.md`（若生成） |",
        "| step7 | 补全实现细节 | **须另外**用 `jdtls-lsp analyze` / `callchain-up` / `callchain-down` 或 IDE |",
        "| step8 | 汇总（本文件） | `index.md` + 运行结束时的 stdout 摘要 JSON |",
        "",
        "## 产物布局",
        "",
        "- **输出根目录**：`index.md`（**step8**）、可选 `business.md`（**step6**）、REST/按表 **汇总 JSON**（`*-summary.json`）。",
        "- **`data/`**：**step1–3** 扫描结果，以及 **step4–5** 各条 **callchain** 明细（默认 **Markdown**，文末嵌入完整 JSON）。",
        "- **`graphs/`**：**step2** Mermaid 等可视化草稿。",
        "",
        "## Artifacts",
        "",
    ]
    lines.extend(_artifact_index_blocks(listed))
    if summary.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for w in summary["warnings"]:
            lines.append(f"- {w}")
    lines.extend(
        [
            "",
            "## 设计约定（与 step 编号）",
            "",
            "- **step2 / REST 外向锚点**：读 `data/rest-map.json`，用 **类#方法 + 行** 作为 **step4** `callchain-down` 或 **step7** `analyze` 入口。",
            "- **step4**：`reverse-design bundle --entrypoint-callchain-down` 对每个 entrypoint 跑 `callchain-down` → `data/callchain-down-entrypoints/<safe_entrypoint_file>/callchain-down-entrypoints-*.md`（Markdown，文末嵌入完整 JSON），汇总 `entrypoint-callchain-down-summary.json`；与同次 bundle 内 **step5 / step5′** 共用一次 JDTLS。",
            "- **step3 / 数据库锚点**：`data/tables-manifest.json`；**用户表清单**（`--tables-file` / `--tables`）；`unresolvedTables` 提示动态 SQL 等。",
            "- **step5**：`--table-callchain-up` → `data/callchain-up-table/<物理表>/callchain-up-table-*.md`、`table-callchain-summary.json`（按物理表分组，键同目录）；`--table-callchain-up-extra`（须同开）→ 同目录下 JDBC/MyBatis 额外链。**step5′**：`--queries` 为关键字 `callchain-up`（非按表）。",
            "- **step7**：单点 **`jdtls-lsp callchain-up` / `callchain-down` / `analyze`**（`references`、`callHierarchy` 等）。",
            "- **step1 补充**：`symbols-by-package.json` 为轻量索引，非调用链主线。",
            "- **step6**：`--business-summary` → `business.md`；向下链报告（`.md` 文末 JSON）含 `keyMethods`、`businessCandidate` 等。",
            "",
            "## Next steps（偏 step7）",
            "",
            "- 用 IDE 或 `jdtls-lsp analyze` 对单点做 `references` / `callHierarchy` 深挖。",
            "- `data/rest-map.json` 为启发式扫描，与运行时路由请以应用配置为准。",
            "",
        ]
    )
    (output_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary["artifacts"] = listed

    _log.info(
        "reverse-design bundle: [step8] done output=%s artifacts=%s warnings=%s",
        output_dir,
        len(listed),
        len(summary.get("warnings") or []),
    )

    return summary
