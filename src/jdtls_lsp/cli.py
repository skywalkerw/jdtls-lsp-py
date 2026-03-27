"""CLI: jdtls-lsp analyze ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jdtls_lsp.analyze import OPERATIONS, analyze_sync
from jdtls_lsp.callchain import trace_call_chain_sync
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
    ap.add_argument("--query", "-q", help="workspaceSymbol 查询字符串")
    ap.add_argument(
        "--jdtls",
        type=Path,
        default=None,
        help="JDTLS 安装目录，默认 ./jdtls（当前目录）或环境变量 LITECLAW_JDTLS_PATH，最后回退 ~/jdtls",
    )

    cp = sub.add_parser(
        "callchain",
        help="向上查调用链（入口三选一：类+方法 / 文件+行 / 关键字）",
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
        help="workspace/symbol 关键字，解析到方法后作为起点（与 --class/--method、--file/--line 互斥）",
    )
    cp.add_argument("--max-depth", type=int, default=20, help="向上追踪最大深度，默认 20")
    cp.add_argument(
        "--grep-workers",
        type=int,
        default=None,
        help="关键字命中多文件时并行分析线程数（默认 min(8, 文件数)，可用环境变量 JDTLS_LSP_GREP_WORKERS）",
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
    elif args.cmd == "callchain":
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
        )
    else:
        return 1

    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if not out.startswith("错误:") else 2


if __name__ == "__main__":
    raise SystemExit(main())
