"""CLI: jdtls-lsp analyze ..."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from jdtls_lsp.analyze import OPERATIONS, analyze_sync
from jdtls_lsp.callchain import trace_call_chain_sync, trace_outgoing_subgraph_sync
from jdtls_lsp.entry_scan import scan_java_entrypoints, scan_rest_map
from jdtls_lsp.reverse_design.bundle import run_design_bundle
from jdtls_lsp.reverse_design.batch_symbols_by_package import batch_symbols_by_package
from jdtls_lsp.reverse_design.scan_modules import scan_modules
from jdtls_lsp.reverse_design.table_manifest import build_table_manifest
from jdtls_lsp.java_grep import java_grep_report
from jdtls_lsp.logutil import get_logger, setup_logging

_cli_log = get_logger("cli")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jdtls-lsp", description="JDTLS LSP wrapper (Python, LiteClaw-compatible)")
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="日志详细程度：-v 为 INFO，-vv 为 DEBUG（含每条 LSP 请求/响应）",
    )
    p.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default=None,
        help="直接指定日志级别（优先于 -v）。也可用环境变量 JDTLS_LSP_LOG=debug|info|...",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ap = sub.add_parser("analyze", help="Run one Java LSP operation (like lsp_java_analyze)")
    ap.add_argument("project", help="项目根目录或任意路径（会向上解析 Maven/Gradle 根）")
    ap.add_argument(
        "operation",
        choices=sorted(OPERATIONS),
        help="LSP 操作",
    )
    ap.add_argument("--file", "-f", dest="file", help="相对项目根的 .java 路径（documentSymbol / definition 等）")
    ap.add_argument("--line", "-l", type=int, help="行号 1-based")
    ap.add_argument("--char", "-c", type=int, help="列号 1-based")
    ap.add_argument(
        "--query",
        "-q",
        help="workspaceSymbol 查询；可用 | 或 ｜ 拼接多个子串合并结果",
    )
    ap.add_argument(
        "--jdtls",
        type=Path,
        default=None,
        help="JDTLS 安装目录，默认 ./jdtls（当前目录）或环境变量 LITECLAW_JDTLS_PATH，最后回退 ~/jdtls",
    )

    cp = sub.add_parser(
        "callchain-up",
        help="向上查调用链（入口三选一：类+方法 / 文件+行 / 关键字；与 callchain-down 对应）",
    )
    cp.add_argument("project", help="项目根目录或任意路径（会向上解析 Maven/Gradle 根）")
    cp.add_argument(
        "--class",
        "-k",
        dest="class_name",
        default=None,
        help="类名（与 --method 同时使用；支持全限定名或简单类名）",
    )
    cp.add_argument(
        "--method",
        "-m",
        dest="method_name",
        default=None,
        help="方法名，不含参数（与 --class 同时使用）",
    )
    cp.add_argument(
        "--file",
        "-f",
        dest="file_path",
        default=None,
        help="相对项目根或绝对路径的 .java 文件（与 --line 同时使用）",
    )
    cp.add_argument(
        "--line",
        "-l",
        type=int,
        default=None,
        help="1-based 行号（与 --file 同时使用）",
    )
    cp.add_argument(
        "--char",
        "-c",
        type=int,
        default=None,
        help="1-based 列号（可选，与 --file/--line 同时使用，默认 1）",
    )
    cp.add_argument(
        "--query",
        "-q",
        dest="symbol_query",
        default=None,
        help="workspace/symbol 关键字；可用 | 或 ｜ 拼接多个子串一次性搜索（与 --class/--method、--file/--line 互斥）",
    )
    cp.add_argument("--max-depth", type=int, default=20, help="向上追踪最大深度，默认 20")
    cp.add_argument(
        "--grep-workers",
        type=int,
        default=None,
        help="兼容保留；多入口在单 LSP 上串行追踪，本参数不改变调度（可用环境变量 JDTLS_LSP_GREP_WORKERS）",
    )
    cp.add_argument(
        "--grep-skip-interface",
        action="store_true",
        help="关键字 java_text_grep：跳过 interface 源文件中的命中（仅 *.java 顶层 interface 与文件名一致）",
    )
    cp.add_argument(
        "--grep-skip-rest-entry",
        action="store_true",
        help="关键字 java_text_grep：跳过起点本身已是 REST 的方法（多为 Controller 入口）",
    )
    cp.add_argument(
        "--grep-max-entry-points",
        type=int,
        default=None,
        metavar="N",
        help="关键字 java_text_grep：最多保留 N 个起点（先按实现类优先排序再截断）",
    )
    cp.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "markdown"),
        default="markdown",
        help="输出格式：markdown（默认，含图示说明 + 嵌入 JSON）或 json（仅 JSON）",
    )
    cp.add_argument(
        "--jdtls",
        type=Path,
        default=None,
        help="JDTLS 安装目录，默认 ./jdtls（当前目录）或环境变量 LITECLAW_JDTLS_PATH，最后回退 ~/jdtls",
    )

    dp = sub.add_parser(
        "callchain-down",
        help="向下展开调用子图（LSP outgoingCalls BFS；入口与 callchain-up 相同，不支持多文件 grep 多起点）",
    )
    dp.add_argument("project", help="项目根目录或任意路径（会向上解析 Maven/Gradle 根）")
    dp.add_argument("--class", "-k", dest="class_name", default=None, help="类名（与 --method 同时使用）")
    dp.add_argument("--method", "-m", dest="method_name", default=None, help="方法名，不含参数")
    dp.add_argument("--file", "-f", dest="file_path", default=None, help="与 --line 同时使用")
    dp.add_argument("--line", "-l", type=int, default=None, help="1-based 行号")
    dp.add_argument("--char", "-c", type=int, default=None, help="1-based 列号，默认 1")
    dp.add_argument(
        "--query",
        "-q",
        dest="symbol_query",
        default=None,
        help="workspace/symbol 或单段关键字（多文件 grep 多起点不支持）",
    )
    dp.add_argument("--max-depth", type=int, default=8, help="向下 BFS 最大深度，默认 8")
    dp.add_argument("--max-nodes", type=int, default=500, help="最多收录节点数，默认 500")
    dp.add_argument("--max-branches", type=int, default=32, help="每层最多 outgoing 条数，默认 32")
    dp.add_argument(
        "--grep-skip-interface",
        action="store_true",
        help="关键字 grep：跳过 interface 源文件命中（同 callchain-up）",
    )
    dp.add_argument(
        "--grep-skip-rest-entry",
        action="store_true",
        help="关键字 grep：跳过已是 REST 的起点（同 callchain-up）",
    )
    dp.add_argument(
        "--grep-max-entry-points",
        type=int,
        default=None,
        metavar="N",
        help="关键字 grep：最多 N 个起点（同 callchain-up）",
    )
    dp.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "markdown"),
        default="markdown",
        help="输出格式，默认 markdown",
    )
    dp.add_argument("--jdtls", type=Path, default=None, help="JDTLS 安装目录")

    ep = sub.add_parser(
        "entrypoints",
        help="静态入口扫描：main/Spring/Servlet/消息/定时/@Async 等（HTTP 映射见 reverse-design rest-map）",
    )
    ep.add_argument("project", help="项目根目录")
    ep.add_argument("--max-files", type=int, default=30_000, help="最多扫描 .java 文件数，默认 30000")

    jp = sub.add_parser(
        "java-grep",
        help="在工程内 *.java 全文搜索关键字（ripgrep 优先，否则 Python 扫描；无需 JDTLS）",
    )
    jp.add_argument("project", help="项目根目录")
    jp.add_argument(
        "--query",
        "-q",
        required=True,
        help="搜索关键字；可用 | 或 ｜ 拼接多个子串（与 callchain-up 关键字规则一致）",
    )
    jp.add_argument(
        "--no-sort",
        action="store_true",
        help="不按启发式分数排序，保留 ripgrep/扫描的收集顺序",
    )
    jp.add_argument(
        "--max-hits",
        type=int,
        default=200,
        metavar="N",
        help="最多返回命中条数，默认 200（与库内部单次搜索上限一致）",
    )
    jp.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json（默认）或 text（每行 path:line:行内容）",
    )

    reverse_design_p = sub.add_parser(
        "reverse-design",
        help="逆向设计八步（需求.md）：step1→step8；step2 rest-map 为静态入口扫描（与 entrypoints 并列，均无 JDTLS）",
    )
    rdg = reverse_design_p.add_subparsers(dest="reverse_design_cmd", required=True)

    d_scan = rdg.add_parser(
        "scan",
        help="[step1] Maven/Gradle 模块与构建线索扫描（无 JDTLS）",
    )
    d_scan.add_argument("project", help="项目根目录")

    d_rest = rdg.add_parser(
        "rest-map",
        help="[step2·静态入口] Spring REST 映射启发式扫描（与 entrypoints 同属无 JDTLS 入口发现）",
    )
    d_rest.add_argument("project", help="项目根目录")
    d_rest.add_argument("--max-files", type=int, default=8_000, help="最多扫描 .java 文件数")

    d_tbl = rdg.add_parser(
        "db-tables",
        help="[step3] 用户表清单 + 代码轻量抽取 → tables-manifest（无 JDTLS）",
    )
    d_tbl.add_argument("project", help="项目根目录")
    d_tbl.add_argument(
        "--tables-file",
        type=Path,
        default=None,
        help="每行一表，# 为注释；与 --tables 合并，清单内表名为规范名",
    )
    d_tbl.add_argument("--tables", default="", help="逗号分隔表名，与 --tables-file 合并")
    d_tbl.add_argument(
        "--strict-tables-only",
        action="store_true",
        help="不在 JSON 中列出 extractedOnly（仍可做抽取与 anchors）",
    )
    d_tbl.add_argument("--max-java-files", type=int, default=8_000, help="最多扫描 .java 文件数")
    d_tbl.add_argument("--max-xml-files", type=int, default=2_000, help="最多扫描 .xml 文件数（MyBatis 等）")

    d_sym = rdg.add_parser(
        "symbols",
        help="[step1 补充] 轻量扫描顶层类型按包聚合（无 JDTLS）",
    )
    d_sym.add_argument("project", help="项目根目录")
    d_sym.add_argument(
        "--glob",
        default="**/src/main/java/**/*.java",
        help="相对项目根的 glob，默认 **/src/main/java/**/*.java",
    )
    d_sym.add_argument("--max-files", type=int, default=200, help="最多扫描的 .java 文件数")
    d_sym.add_argument("--jdtls", type=Path, default=None, help="JDTLS 目录")

    d_bun = rdg.add_parser(
        "bundle",
        help="[step8 编排] 写 design/；默认 step1–3，可选 step4–6（--entrypoint-callchain-down / --table-callchain-up / --queries / --business-summary）",
    )
    d_bun.add_argument("project", help="项目根目录")
    d_bun.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("design"),
        help="输出目录（step8 根目录），默认 ./design",
    )
    d_bun.add_argument(
        "--queries",
        default="",
        help="[step5′ 关键字向上] 逗号分隔关键字，各跑一次 callchain-up → data/callchain-up-*.md（Markdown，文末 JSON）；空则跳过",
    )
    d_bun.add_argument(
        "--skip-symbols",
        action="store_true",
        help="不跑 [step1 补充] symbols（symbols-by-package.json）",
    )
    d_bun.add_argument(
        "--skip-callchain",
        action="store_true",
        help="跳过 step4/step5/step5′：不跑需 JDTLS 的 callchain-up（--queries / --table-callchain-up）与 callchain-down（--entrypoint-callchain-down）",
    )
    d_bun.add_argument(
        "--table-callchain-up",
        action="store_true",
        dest="table_callchain_up",
        help="[step5] 按表向上：蛇形表 → *ServiceImpl + @Entity；产物 data/callchain-up-table/<物理表>/callchain-up-table-*.md（另含 *-entity.md）",
    )
    d_bun.add_argument(
        "--max-table-callchain-scan",
        type=int,
        default=12_000,
        metavar="N",
        help="按表锚点扫描时最多检查的 *ServiceImpl.java 与同表实体 *.java 路径数，默认 12000",
    )
    d_bun.add_argument(
        "--table-callchain-up-extra",
        action="store_true",
        dest="table_callchain_up_extra",
        help="[step5] 须与 --table-callchain-up 同用：在 data/callchain-up-table/<表>/ 下额外生成 *-sql-NN.md、*-mapper-NN.md",
    )
    d_bun.add_argument(
        "--max-table-up-extra-anchors",
        type=int,
        default=24,
        metavar="N",
        help="与 --table-callchain-up-extra 联用：每张表 SQL 与 MyBatis 各自最多几条起点（0=不限制），默认 24",
    )
    d_bun.add_argument(
        "--entrypoint-callchain-down",
        action="store_true",
        dest="entrypoint_callchain_down",
        help="[step4] 按 entrypoints 向下：entry_scan.scan_java_entrypoints 的每个起点 callchain-down → data/callchain-down-entrypoints/…/callchain-down-entrypoints-*.md",
    )
    d_bun.add_argument(
        "--max-rest-down-endpoints",
        type=int,
        default=0,
        metavar="N",
        help="entrypoint 向下链最多处理起点数（按 scan_java_entrypoints 返回顺序），0 表示不限制",
    )
    d_bun.add_argument(
        "--rest-down-depth",
        type=int,
        default=16,
        help="callchain-down BFS 最大深度，默认 16（与单独深挖时建议一致）",
    )
    d_bun.add_argument(
        "--rest-down-max-nodes",
        type=int,
        default=500,
        help="向下子图最多节点数，默认 500",
    )
    d_bun.add_argument(
        "--rest-down-max-branches",
        type=int,
        default=48,
        help="每层 outgoing 分支上限，默认 48",
    )
    d_bun.add_argument(
        "--business-summary",
        action="store_true",
        help="[step6] 合并向下链报告（callchain-down-rest-* 与 callchain-down-entrypoints-* 的 .md/.json，含历史 data 根下扁平文件）的 keyMethods → business.md",
    )
    d_bun.add_argument("--skip-rest-map", action="store_true", help="跳过 [step2]：不生成 rest-map")
    d_bun.add_argument(
        "--skip-table-manifest",
        action="store_true",
        help="跳过 [step3]：不生成 tables-manifest.json",
    )
    d_bun.add_argument("--skip-scan", action="store_true", help="跳过 [step1] modules：不生成 modules.json")
    d_bun.add_argument(
        "--tables-file",
        type=Path,
        default=None,
        help="用户表清单（每行一表）；与 --tables 合并，作为规范名与 unresolved 基准",
    )
    d_bun.add_argument("--tables", default="", help="逗号分隔表名，与 --tables-file 合并")
    d_bun.add_argument(
        "--strict-tables-only",
        action="store_true",
        help="tables-manifest 中不输出 extractedOnly 列表",
    )
    d_bun.add_argument("--max-table-java-files", type=int, default=8_000)
    d_bun.add_argument("--max-table-xml-files", type=int, default=2_000)
    d_bun.add_argument("--glob", default="**/src/main/java/**/*.java", help="[step1 补充] 轻量扫描 glob")
    d_bun.add_argument("--max-symbol-files", type=int, default=200)
    d_bun.add_argument("--max-rest-files", type=int, default=8_000)
    d_bun.add_argument("--callchain-depth", type=int, default=20)
    d_bun.add_argument("--jdtls", type=Path, default=None)
    d_bun.add_argument(
        "--quiet",
        action="store_true",
        help="TTY 下也不自动打开 INFO 日志；stdout 仅在结束时输出 JSON（适合脚本重定向）",
    )

    args = p.parse_args(argv)
    if args.log_level:
        setup_logging(args.log_level)
    elif args.verbose >= 2:
        setup_logging("DEBUG")
    elif args.verbose == 1:
        setup_logging("INFO")
    else:
        setup_logging()

    # reverse-design bundle（step4/step5 等）会冷启动 JDTLS、可能大量 callchain 请求，耗时常达数分钟；
    # 默认 WARNING 时 stderr 无输出、stdout 仅结束时打印 JSON，易被误认为「卡住」。
    if (
        getattr(args, "cmd", None) == "reverse-design"
        and getattr(args, "reverse_design_cmd", None) == "bundle"
        and not args.log_level
        and args.verbose == 0
        and not (os.environ.get("JDTLS_LSP_LOG") or "").strip()
        and not getattr(args, "quiet", False)
        and sys.stderr.isatty()
    ):
        setup_logging("INFO")

    if args.cmd == "analyze":
        out = analyze_sync(
            args.project,
            args.operation,
            file_path=args.file,
            line=args.line,
            character=args.char,
            query=args.query,
            jdtls_path=args.jdtls,
        )
    elif args.cmd == "callchain-up":
        out = trace_call_chain_sync(
            args.project,
            args.class_name,
            args.method_name,
            file_path=args.file_path,
            line=args.line,
            character=args.char,
            symbol_query=args.symbol_query,
            jdtls_path=args.jdtls,
            max_depth=args.max_depth,
            output_format=args.output_format,
            grep_parallel_workers=args.grep_workers,
            grep_skip_interface=args.grep_skip_interface,
            grep_skip_rest=args.grep_skip_rest_entry,
            grep_max_entry_points=args.grep_max_entry_points,
        )
    elif args.cmd == "callchain-down":
        out = trace_outgoing_subgraph_sync(
            args.project,
            args.class_name,
            args.method_name,
            file_path=args.file_path,
            line=args.line,
            character=args.char,
            symbol_query=args.symbol_query,
            jdtls_path=args.jdtls,
            max_depth=args.max_depth,
            max_nodes=args.max_nodes,
            max_branches=args.max_branches,
            output_format=args.output_format,
            grep_skip_interface=args.grep_skip_interface,
            grep_skip_rest=args.grep_skip_rest_entry,
            grep_max_entry_points=args.grep_max_entry_points,
        )
    elif args.cmd == "entrypoints":
        root = Path(args.project).resolve()
        if not root.exists():
            sys.stdout.write(f"错误: 项目路径不存在 {args.project}\n")
            return 2
        hits = scan_java_entrypoints(root, max_files=args.max_files)
        out = json.dumps(
            {"projectRoot": str(root), "entryCount": len(hits), "entries": hits},
            ensure_ascii=False,
            indent=2,
        )
    elif args.cmd == "java-grep":
        root = Path(args.project).resolve()
        if not root.exists():
            sys.stdout.write(f"错误: 项目路径不存在 {args.project}\n")
            return 2
        payload = java_grep_report(
            root,
            args.query,
            sort_by_score=not args.no_sort,
            max_hits=args.max_hits,
        )
        if not payload["needles"]:
            sys.stdout.write("错误: --query 为空或仅含空白/分隔符\n")
            return 2
        if args.format == "json":
            out = json.dumps(payload, ensure_ascii=False, indent=2)
        else:
            lines: list[str] = []
            for h in payload["hits"]:
                fp = h["file"]
                ln = h["line"]
                txt = str(h["text"]).replace("\n", " ").strip()
                if len(txt) > 200:
                    txt = txt[:197] + "..."
                lines.append(f"{fp}:{ln}:{txt}")
            out = "\n".join(lines)
            if lines:
                out += "\n"
    elif args.cmd == "reverse-design":
        root = Path(args.project).resolve()
        if not root.exists():
            sys.stdout.write(f"错误: 项目路径不存在 {args.project}\n")
            return 2
        dc = args.reverse_design_cmd
        _cli_log.info("cli reverse-design subcommand=%s project=%s", dc, root)
        if dc == "scan":
            out = json.dumps(scan_modules(root), ensure_ascii=False, indent=2)
        elif dc == "rest-map":
            mf = getattr(args, "max_files", 8_000)
            out = json.dumps(scan_rest_map(root, max_files=mf), ensure_ascii=False, indent=2)
        elif dc == "db-tables":
            tf = getattr(args, "tables_file", None)
            tf_res = tf.expanduser().resolve() if tf else None
            res = build_table_manifest(
                root,
                tables_file=tf_res,
                tables_inline=str(getattr(args, "tables", "") or ""),
                strict_tables_only=bool(getattr(args, "strict_tables_only", False)),
                max_java_files=int(getattr(args, "max_java_files", 8_000)),
                max_xml_files=int(getattr(args, "max_xml_files", 2_000)),
            )
            out = res["error"] if res.get("error") else json.dumps(res, ensure_ascii=False, indent=2)
        elif dc == "symbols":
            res = batch_symbols_by_package(
                str(root),
                jdtls_path=getattr(args, "jdtls", None),
                glob_pattern=getattr(args, "glob", "**/src/main/java/**/*.java"),
                max_files=getattr(args, "max_files", 200),
            )
            out = res["error"] if res.get("error") else json.dumps(res, ensure_ascii=False, indent=2)
        elif dc == "bundle":
            qs = [x.strip() for x in str(args.queries).split(",") if x.strip()]
            if sys.stderr.isatty() and not args.quiet:
                sys.stderr.write(
                    "jdtls-lsp reverse-design bundle: 运行中（step1–3 多为本地扫描；"
                    "step4/step5/step5′ 需 JDTLS，可能较慢；step6 --business-summary 通常很快）；"
                    "step7 须另用 analyze/callchain；step8 结束写 index.md 并打印 stdout JSON。"
                    "完成前 stdout 无最终 JSON，进度见下方日志。\n"
                )
                sys.stderr.flush()
            tf_b = getattr(args, "tables_file", None)
            tf_b_res = tf_b.expanduser().resolve() if tf_b else None
            summ = run_design_bundle(
                str(root),
                args.output.resolve(),
                queries=qs,
                skip_symbols=args.skip_symbols,
                skip_callchain=args.skip_callchain,
                skip_rest_map=args.skip_rest_map,
                skip_scan=args.skip_scan,
                skip_table_manifest=getattr(args, "skip_table_manifest", False),
                jdtls_path=args.jdtls,
                glob_pattern=args.glob,
                max_symbol_files=args.max_symbol_files,
                max_rest_map_files=args.max_rest_files,
                callchain_max_depth=args.callchain_depth,
                tables_file=tf_b_res,
                tables_inline=str(getattr(args, "tables", "") or ""),
                strict_tables_only=bool(getattr(args, "strict_tables_only", False)),
                max_table_java_files=int(getattr(args, "max_table_java_files", 8_000)),
                max_table_xml_files=int(getattr(args, "max_table_xml_files", 2_000)),
                table_callchain_up=bool(getattr(args, "table_callchain_up", False)),
                max_table_callchain_java_scan=int(getattr(args, "max_table_callchain_scan", 12_000)),
                table_callchain_up_extra=bool(getattr(args, "table_callchain_up_extra", False)),
                max_table_up_extra_anchors=int(getattr(args, "max_table_up_extra_anchors", 24)),
                entrypoint_callchain_down=bool(getattr(args, "entrypoint_callchain_down", False)),
                max_rest_down_endpoints=int(getattr(args, "max_rest_down_endpoints", 0)),
                rest_down_max_depth=int(getattr(args, "rest_down_depth", 8)),
                rest_down_max_nodes=int(getattr(args, "rest_down_max_nodes", 500)),
                rest_down_max_branches=int(getattr(args, "rest_down_max_branches", 32)),
                business_summary=bool(getattr(args, "business_summary", False)),
            )
            out = summ["error"] if summ.get("error") else json.dumps(summ, ensure_ascii=False, indent=2)
        else:
            return 1
    else:
        return 1

    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if not out.startswith("错误:") else 2


if __name__ == "__main__":
    raise SystemExit(main())
