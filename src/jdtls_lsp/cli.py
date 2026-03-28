"""CLI: jdtls-lsp analyze ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jdtls_lsp.analyze import OPERATIONS, analyze_sync
from jdtls_lsp.callchain import trace_call_chain_sync, trace_outgoing_subgraph_sync
from jdtls_lsp.java_entrypoints import scan_java_entrypoints
from jdtls_lsp.java_grep import java_grep_report
from jdtls_lsp.logutil import setup_logging


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
        help="静态扫描 Java/Spring 常见入口（main、@SpringBootApplication 等，无需 JDTLS）",
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

    args = p.parse_args(argv)
    if args.log_level:
        setup_logging(args.log_level)
    elif args.verbose >= 2:
        setup_logging("DEBUG")
    elif args.verbose == 1:
        setup_logging("INFO")
    else:
        setup_logging()

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
    else:
        return 1

    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if not out.startswith("错误:") else 2


if __name__ == "__main__":
    raise SystemExit(main())
