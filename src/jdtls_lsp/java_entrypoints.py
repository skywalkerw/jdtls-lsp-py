"""Static scan for common Java / Spring entry patterns (no LSP)."""

from __future__ import annotations

import re
from pathlib import Path

from jdtls_lsp.java_grep import SKIP_DIR_PARTS

__all__ = ["scan_java_entrypoints"]

_MAIN = re.compile(r"\bpublic\s+static\s+void\s+main\s*\(\s*String\s*\[\s*\w*\s*\]\s*\w*\s*\)")
_SPRING_BOOT_APP = re.compile(r"@SpringBootApplication\b")
_WEB_APP_INIT = re.compile(r"\bWebApplicationInitializer\b")


def scan_java_entrypoints(project_root: Path, *, max_files: int = 30_000) -> list[dict[str, object]]:
    """
    Walk ``*.java`` under ``project_root`` (skipping build dirs) and record likely entry points.

    Returns list of ``{"kind", "file", "line", "preview"}`` sorted by path then line.
    """
    root = project_root.resolve()
    out: list[dict[str, object]] = []
    n = 0
    for p in sorted(root.rglob("*.java")):
        if any(x in p.parts for x in SKIP_DIR_PARTS):
            continue
        n += 1
        if n > max_files:
            break
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = str(p)
        for i, line in enumerate(lines, start=1):
            kinds: list[str] = []
            if _MAIN.search(line):
                kinds.append("main")
            if _SPRING_BOOT_APP.search(line):
                kinds.append("spring_boot_application")
            if _WEB_APP_INIT.search(line):
                kinds.append("web_application_initializer")
            for k in kinds:
                out.append(
                    {
                        "kind": k,
                        "file": rel,
                        "line": i,
                        "preview": line.strip()[:240],
                    }
                )
    out.sort(key=lambda x: (str(x["file"]), int(x["line"]), str(x["kind"])))
    return out
