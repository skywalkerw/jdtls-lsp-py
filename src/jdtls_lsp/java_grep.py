"""Search ``*.java`` under a project tree by plain-text needles (ripgrep or Python fallback).

Reusable by ``callchain`` and future CLI commands; no LSP dependency.
"""

from __future__ import annotations

__all__ = [
    "SKIP_DIR_PARTS",
    "METH_LIKE_LINE",
    "keyword_search_variants",
    "line_matches_text_needles",
    "score_grep_hit",
    "grep_java_via_ripgrep",
    "grep_java_walk",
    "grep_java_keyword_hits",
    "sort_grep_hits_by_score",
    "scan_method_line_candidates",
]

import json
import re
import subprocess
from pathlib import Path

SKIP_DIR_PARTS = frozenset(
    {"target", "build", ".git", "node_modules", "dist", "out", ".gradle"}
)

# Method-like line after a grep hit (annotation/string) to try LSP resolution.
METH_LIKE_LINE = re.compile(r"^\s*(public|private|protected|static)\s+.+\([^)]*\)")


def keyword_search_variants(q: str) -> list[str]:
    """Normalize query to search needles: trimmed original only (no case/identifier transforms)."""
    q = q.strip()
    if not q:
        return []
    return [q]


def line_matches_text_needles(line: str, needles: list[str]) -> bool:
    ll = line.lower()
    for n in needles:
        if not n:
            continue
        if n in line or n.lower() in ll:
            return True
    return False


def score_grep_hit(path: Path, line_text: str) -> int:
    """Heuristic: prefer Service/Controller hits and method-like lines over class declarations."""
    s = str(path)
    t = line_text.strip()
    score = 0
    if "ServiceImpl" in s or "Controller" in s:
        score += 25
    elif "Service" in s and "Impl" not in s:
        score += 12
    if "Repository" in s:
        score += 8
    if t.startswith(("public ", "private ", "protected ")) and "(" in t:
        score += 15
    elif t.startswith(("public ", "private ", "protected ")):
        score += 6
    if "@" in t:
        score += 5
    if re.match(r"^\s*(public|private|protected|static)\s+class\s+", t):
        score -= 20
    if "interface " in t and "{" not in t[:80]:
        score -= 10
    return score


def grep_java_via_ripgrep(project_root: Path, needles: list[str]) -> list[tuple[Path, int, str]]:
    """Use ``rg --json`` when available; excludes common build dirs."""
    all_hits: list[tuple[Path, int, str]] = []
    seen: set[tuple[str, int]] = set()
    for needle in needles:
        if not needle:
            continue
        try:
            r = subprocess.run(
                [
                    "rg",
                    "--json",
                    "-n",
                    "--glob",
                    "*.java",
                    "-F",
                    "--glob",
                    "!**/target/**",
                    "--glob",
                    "!**/build/**",
                    needle,
                    str(project_root),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        for raw_line in (r.stdout or "").splitlines():
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "match":
                continue
            d = obj.get("data") or {}
            path_obj = d.get("path") or {}
            path_text = path_obj.get("text") if isinstance(path_obj, dict) else None
            line_no = d.get("line_number")
            lines_obj = d.get("lines") or {}
            line_txt = lines_obj.get("text", "") if isinstance(lines_obj, dict) else ""
            if not path_text or line_no is None:
                continue
            key = (path_text, int(line_no))
            if key in seen:
                continue
            seen.add(key)
            all_hits.append((Path(path_text), int(line_no), line_txt))
            if len(all_hits) >= 200:
                return all_hits
    return all_hits


def grep_java_walk(project_root: Path, needles: list[str]) -> list[tuple[Path, int, str]]:
    """Scan ``*.java`` with Python when ``rg`` is unavailable."""
    hits: list[tuple[Path, int, str]] = []
    for p in project_root.rglob("*.java"):
        if any(x in p.parts for x in SKIP_DIR_PARTS):
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, start=1):
            if line_matches_text_needles(line, needles):
                hits.append((p, i, line))
                if len(hits) >= 200:
                    return hits
    return hits


def grep_java_keyword_hits(project_root: Path, needles: list[str]) -> list[tuple[Path, int, str]]:
    """Collect (path, 1-based line, line text) for each needle; prefer ripgrep."""
    if not needles:
        return []
    rg = grep_java_via_ripgrep(project_root, needles)
    if rg:
        return rg
    return grep_java_walk(project_root, needles)


def sort_grep_hits_by_score(hits: list[tuple[Path, int, str]]) -> None:
    """In-place sort: higher ``score_grep_hit`` first, then path string."""
    hits.sort(key=lambda h: (-score_grep_hit(h[0], h[2]), str(h[0])))


def scan_method_line_candidates(
    hit_line: int,
    lines: list[str],
    forward: int = 220,
    backward: int = 40,
    meth_like: re.Pattern[str] | None = None,
) -> list[int]:
    """From a grep line, return 1-based line numbers to try: hit line, then method-like lines down/up."""
    pat = meth_like or METH_LIKE_LINE
    n = len(lines)
    hi = hit_line - 1
    out: list[int] = []
    seen: set[int] = set()

    def add(ln: int) -> None:
        if 1 <= ln <= n and ln not in seen:
            seen.add(ln)
            out.append(ln)

    add(hit_line)
    for j in range(hi + 1, min(n, hi + forward)):
        if pat.search(lines[j]):
            add(j + 1)
        if len(out) >= 36:
            break
    for j in range(hi - 1, max(-1, hi - backward), -1):
        if pat.search(lines[j]):
            add(j + 1)
    return out
