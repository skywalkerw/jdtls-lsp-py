"""Search ``*.java`` under a project tree by plain-text needles (ripgrep or Python fallback).

Reusable by ``callchain-up`` / CLI ``java-grep``; no LSP dependency.
"""

from __future__ import annotations

__all__ = [
    "SKIP_DIR_PARTS",
    "java_scan_roots",
    "walk_files_matching",
    "walk_files_under_roots",
    "METH_LIKE_LINE",
    "keyword_search_variants",
    "line_matches_text_needles",
    "score_grep_hit",
    "grep_java_via_ripgrep",
    "grep_java_walk",
    "grep_java_keyword_hits",
    "sort_grep_hits_by_score",
    "scan_method_line_candidates",
    "java_grep_report",
]

import fnmatch
import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SKIP_DIR_PARTS = frozenset(
    {"target", "build", ".git", "node_modules", "dist", "out", ".gradle"}
)


def java_scan_roots(project_root: Path) -> list[Path]:
    """
    Maven/Gradle 默认源码根：若工程下存在任意 ``**/src/main`` 目录，则**只**在这些目录下扫描
    （含 ``java`` / ``resources`` / ``kotlin`` 等）；否则退回 ``project_root`` 整树。

    构建产物目录仍由 ``SKIP_DIR_PARTS``（如 ``target``）在遍历时排除。
    """
    root = project_root.resolve()
    if not root.is_dir():
        return [root]
    try:
        mains = sorted({p for p in root.glob("**/src/main") if p.is_dir()})
    except OSError:
        return [root]
    return mains if mains else [root]


def walk_files_matching(base: Path, name_glob: str) -> Iterator[Path]:
    """
    在 ``base`` 下按 ``fnmatch`` 匹配文件名（如 ``*.java``、``*ServiceImpl.java``）。

    使用 ``os.walk(..., followlinks=False)``，且不进入名为 ``SKIP_DIR_PARTS`` 的子目录，
    避免 ``pathlib.rglob`` 误入 ``target``、跟随坏符号链接导致 ``FileNotFoundError``。
    """
    try:
        root = base.resolve()
    except OSError:
        return
    if not root.is_dir():
        return
    try:
        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            try:
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_PARTS]
                for fn in filenames:
                    if fnmatch.fnmatch(fn, name_glob):
                        yield Path(dirpath) / fn
            except (FileNotFoundError, PermissionError, OSError):
                continue
    except (FileNotFoundError, PermissionError, OSError):
        return


def walk_files_under_roots(project_root: Path, name_glob: str) -> Iterator[Path]:
    """等价于对每个 ``java_scan_roots(project_root)`` 调用 ``walk_files_matching``。"""
    for b in java_scan_roots(project_root):
        yield from walk_files_matching(b, name_glob)


# Method-like line after a grep hit (annotation/string) to try LSP resolution.
METH_LIKE_LINE = re.compile(r"^\s*(public|private|protected|static)\s+.+\([^)]*\)")
# ASCII | or fullwidth ｜ — multiple needles in one query string.
_MULTI_KEYWORD_SPLIT = re.compile(r"[|｜]")


def keyword_search_variants(q: str) -> list[str]:
    """Split on ``|`` or ``｜``, trim each part, dedupe (order preserved). No other transforms."""
    q = q.strip()
    if not q:
        return []
    parts = [p.strip() for p in _MULTI_KEYWORD_SPLIT.split(q)]
    parts = [p for p in parts if p]
    if not parts:
        return []
    return list(dict.fromkeys(parts))


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
            scan_paths = [str(p) for p in java_scan_roots(project_root)]
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
                    *scan_paths,
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
    for p in walk_files_under_roots(project_root, "*.java"):
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


def _rel_under_root(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def java_grep_report(
    project_root: Path,
    query: str,
    *,
    sort_by_score: bool = True,
    max_hits: int = 200,
) -> dict[str, Any]:
    """
    在 ``project_root`` 下搜索 ``*.java`` 中与 ``query`` 匹配的文本行（``|`` / ``｜`` 拆成多 needle，与 callchain-up 一致）。

    返回结构化字典，供 CLI ``java-grep --format json`` 或脚本直接使用。
    """
    root = project_root.resolve()
    needles = keyword_search_variants(query)
    if not needles:
        return {
            "projectRoot": str(root),
            "needles": [],
            "hitCount": 0,
            "hits": [],
        }
    hits = grep_java_keyword_hits(root, needles)
    if sort_by_score:
        sort_grep_hits_by_score(hits)
    cap = max(0, max_hits)
    hits = hits[:cap]
    rows: list[dict[str, Any]] = []
    for p, line_no, line_text in hits:
        rows.append(
            {
                "file": _rel_under_root(p, root),
                "line": line_no,
                "text": line_text,
                "score": score_grep_hit(p, line_text),
            }
        )
    return {
        "projectRoot": str(root),
        "needles": needles,
        "hitCount": len(rows),
        "hits": rows,
    }


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
