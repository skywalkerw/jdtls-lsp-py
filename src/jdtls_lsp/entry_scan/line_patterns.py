"""非 HTTP 静态入口：按行匹配 ``entry_scan.java_entry_patterns``。

并额外支持 Spring MVC ``@Controller``：把该文件中所有 ``public`` 方法也作为 entrypoints，
供后续 ``entrypoint-callchain-down`` 向下调用链从统一的起点发起。
"""

from __future__ import annotations

import re
from pathlib import Path

from jdtls_lsp.entry_scan._java_walk import iter_java_source_paths
from jdtls_lsp.entry_scan.java_entry_patterns import ENTRYPOINT_LINE_PATTERNS

__all__ = ["scan_java_entrypoints"]

_REST_CONTROLLER = re.compile(r"@(?:[\w.]+\.)*RestController\b")
# 仅 ``@Controller`` / ``@org.springframework.stereotype.Controller``（不含 RestController）
_STEREOTYPE_CONTROLLER = re.compile(r"@(?:[\w.]+\.)*Controller\b")

# 单行方法签名（含可选注解前缀），提取方法名；用 return-type 存在来规避 constructor。
_PUBLIC_METHOD_LINE = re.compile(
    r"^\s*(?:@\w+\s+)*public\s+"
    r"(?:(?:static|final|default|abstract|synchronized|volatile|transient|native|strictfp|sealed|non-sealed)\s+)*"
    r"[\w<>,?\s\[\]]+\s+(\w+)\s*\(",
)


def scan_java_entrypoints(project_root: Path, *, max_files: int = 30_000) -> list[dict[str, object]]:
    """
    Walk ``*.java`` under ``project_root`` (skipping build dirs) and record likely entry points.

    Returns list of ``{"kind", "file", "line", "preview"}`` sorted by path then line then kind.
    """
    root = project_root.resolve()
    out: list[dict[str, object]] = []
    seen: set[tuple[str, int, str]] = set()
    for p in iter_java_source_paths(root, max_files=max_files):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = str(p)
        blob = "\n".join(lines)
        has_rest = _REST_CONTROLLER.search(blob) is not None
        is_controller = has_rest or (_STEREOTYPE_CONTROLLER.search(blob) is not None and not has_rest)
        for i, line in enumerate(lines, start=1):
            preview = line.strip()[:240]
            if is_controller:
                if _PUBLIC_METHOD_LINE.search(line):
                    key = (rel, i, "controller_public_method")
                    if key not in seen:
                        seen.add(key)
                        out.append(
                            {
                                "kind": "controller_public_method",
                                "file": rel,
                                "line": i,
                                "preview": preview,
                            }
                        )
            for kind, rx in ENTRYPOINT_LINE_PATTERNS:
                if rx.search(line):
                    key = (rel, i, kind)
                    if key in seen:
                        continue
                    seen.add(key)
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
