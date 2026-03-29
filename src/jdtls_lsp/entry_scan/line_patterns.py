"""非 HTTP 静态入口：按行匹配 ``entry_scan.java_entry_patterns``。"""

from __future__ import annotations

from pathlib import Path

from jdtls_lsp.entry_scan._java_walk import iter_java_source_paths
from jdtls_lsp.entry_scan.java_entry_patterns import ENTRYPOINT_LINE_PATTERNS

__all__ = ["scan_java_entrypoints"]


def scan_java_entrypoints(project_root: Path, *, max_files: int = 30_000) -> list[dict[str, object]]:
    """
    Walk ``*.java`` under ``project_root`` (skipping build dirs) and record likely entry points.

    Returns list of ``{"kind", "file", "line", "preview"}`` sorted by path then line then kind.
    """
    root = project_root.resolve()
    out: list[dict[str, object]] = []
    for p in iter_java_source_paths(root, max_files=max_files):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = str(p)
        for i, line in enumerate(lines, start=1):
            preview = line.strip()[:240]
            for kind, rx in ENTRYPOINT_LINE_PATTERNS:
                if rx.search(line):
                    out.append(
                        {
                            "kind": kind,
                            "file": rel,
                            "line": i,
                            "preview": preview,
                        }
                    )
    out.sort(key=lambda x: (str(x["file"]), int(x["line"]), str(x["kind"])))
    return out
