"""共享：在工程根下按路径顺序遍历 ``*.java``（跳过构建目录），上限 ``max_files`` 条。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from jdtls_lsp.java_grep import SKIP_DIR_PARTS


def iter_java_source_paths(project_root: Path, *, max_files: int) -> Iterator[Path]:
    """
    仅统计**未跳过**的路径计入 ``max_files``；与 ``line_patterns`` / ``rest_http`` 扫描语义一致。
    """
    root = project_root.resolve()
    n = 0
    for p in sorted(root.rglob("*.java")):
        if any(x in p.parts for x in SKIP_DIR_PARTS):
            continue
        n += 1
        if n > max_files:
            break
        yield p
