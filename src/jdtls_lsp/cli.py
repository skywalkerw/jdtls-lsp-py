"""CLI: jdtls-lsp analyze ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jdtls_lsp.analyze import OPERATIONS, analyze_sync


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jdtls-lsp", description="JDTLS LSP wrapper (Python, LiteClaw-compatible)")
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

    args = p.parse_args(argv)
    if args.cmd != "analyze":
        return 1

    out = analyze_sync(
        args.project,
        args.operation,
        file_path=args.file,
        line=args.line,
        character=args.char,
        query=args.query,
        jdtls_path=args.jdtls,
    )
    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if not out.startswith("错误:") else 2


if __name__ == "__main__":
    raise SystemExit(main())
