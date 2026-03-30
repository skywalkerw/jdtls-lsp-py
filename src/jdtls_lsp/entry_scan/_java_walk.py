"""共享：在工程根下按路径顺序遍历 ``*.java``（跳过构建目录），上限 ``max_files`` 条。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from jdtls_lsp.java_grep import SKIP_DIR_PARTS, walk_files_under_roots


def iter_java_source_paths(project_root: Path, *, max_files: int) -> Iterator[Path]:
    """
    仅统计**未跳过**的路径计入 ``max_files``；与 ``line_patterns`` / ``rest_http`` 扫描语义一致。

    若存在 ``**/src/main``，只在这些目录下遍历 ``*.java``（不扫 ``target`` 等构建目录）。
    """
    root = project_root.resolve()
    paths = sorted(walk_files_under_roots(root, "*.java"))
    paths = [p for p in paths if not any(x in p.parts for x in SKIP_DIR_PARTS)]
    n = 0
    for p in paths:
        n += 1
        if n > max_files:
            break
        yield p
