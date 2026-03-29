"""step1 补充：**batch_symbols_by_package** — 轻量扫描 ``*.java`` 顶层类型并按 **package** 聚合（无 JDTLS）。对应实现阶段 A3。"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from jdtls_lsp.reverse_design.scan_java_top_level_types import scan_java_top_level_types
from jdtls_lsp.java_grep import SKIP_DIR_PARTS
from jdtls_lsp.jdtls import find_project_root
from jdtls_lsp.logutil import get_logger

_log = get_logger("reverse_design.batch_symbols_by_package")


def _symbol_progress_step(file_count: int) -> int:
    return 1 if file_count <= 60 else 10


_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;")


def _package_from_lines(lines: list[str]) -> str:
    for ln in lines[:80]:
        m = _PACKAGE_RE.match(ln)
        if m:
            return m.group(1)
    return ""


def _collect_java_files(root: Path, glob_pattern: str, max_files: int) -> list[Path]:
    if "**" in glob_pattern:
        paths = sorted(root.glob(glob_pattern))
    else:
        paths = sorted(root.glob(glob_pattern))
    out: list[Path] = []
    for p in paths:
        if not p.is_file() or p.suffix.lower() != ".java":
            continue
        if any(x in p.parts for x in SKIP_DIR_PARTS):
            continue
        out.append(p)
        if len(out) >= max_files:
            break
    return out


def batch_symbols_by_package(
    project_path: str,
    *,
    jdtls_path: Path | None = None,
    glob_pattern: str = "**/src/main/java/**/*.java",
    max_files: int = 200,
) -> dict[str, Any]:
    """
    对匹配 ``glob_pattern`` 的 ``*.java`` 做 **轻量扫描**（注释/字符串感知，顶层
    ``class`` / ``interface`` / ``enum`` / ``record``），按 **package** 聚合。

    不启动 JDTLS；``jdtls_path`` 保留仅为与 bundle API 兼容（忽略）。
    精度低于 ``documentSymbol``：不含成员、嵌套类；异常排版可能漏报。
    """
    _ = jdtls_path
    root = Path(project_path).resolve()
    if not root.exists():
        _log.warning("reverse-design symbols: project path missing %s", project_path)
        return {"error": f"项目路径不存在: {project_path}"}

    files = _collect_java_files(root, glob_pattern, max_files)
    _log.info(
        "reverse-design symbols (light) start root=%s glob=%s max_files=%s collected=%s",
        root,
        glob_pattern,
        max_files,
        len(files),
    )
    if not files:
        _log.info("reverse-design symbols: no matching java files")
        return {
            "projectRoot": str(root),
            "globPattern": glob_pattern,
            "fileCount": 0,
            "symbolSource": "light_scan",
            "packages": {},
            "warning": "no matching java files",
        }

    cr = Path(find_project_root(str(root))).resolve()

    by_pkg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    prog_step = _symbol_progress_step(len(files))
    t_all = time.monotonic()

    for idx, fp in enumerate(files, start=1):
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errors.append(f"{fp}: {e}")
            continue
        if text.startswith("\ufeff"):
            text = text[1:]
        pkg = _package_from_lines(text.splitlines())
        try:
            rel = str(fp.relative_to(cr))
        except ValueError:
            rel = str(fp)
        if idx == 1 or idx == len(files) or idx % prog_step == 0:
            _log.info("reverse-design symbols: %s/%s scan → %s", idx, len(files), rel)
        try:
            items = scan_java_top_level_types(text)
        except Exception as e:
            errors.append(f"{rel}: {e}")
            _log.warning("reverse-design symbols: scan failed %s: %s", rel, e)
            continue
        for item in items:
            row = {**item, "package": pkg or "(default)", "file": rel}
            by_pkg[pkg or "(default)"].append(row)

    packages_out = {k: v for k, v in sorted(by_pkg.items(), key=lambda x: x[0])}
    elapsed = time.monotonic() - t_all
    _log.info(
        "reverse-design symbols done packages=%s files=%s errors=%s elapsed=%.2fs (light_scan)",
        len(packages_out),
        len(files),
        len(errors),
        elapsed,
    )
    return {
        "projectRoot": str(cr.resolve()),
        "globPattern": glob_pattern,
        "fileCount": len(files),
        "symbolFileCount": len(files),
        "packageCount": len(packages_out),
        "symbolSource": "light_scan",
        "packages": packages_out,
        "errors": errors[:50],
    }
