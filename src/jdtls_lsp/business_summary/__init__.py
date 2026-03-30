"""**step6**：向下调用子图上的关键业务方法启发式标权，产出 ``business.md`` 等 **业务摘要**。

与 ``jdtls_lsp.reverse_design`` **平级**的包 ``jdtls_lsp.business_summary``，与 CLI ``--business-summary`` / ``run_design_bundle(..., business_summary=...)`` 命名一致。由 ``reverse_design.bundle`` 编排调用。便于后续拆分子模块或扩展输出。对外 API 见 ``__all__``。

依据 REVERSE_ENGINEERING_DESIGN §1.3：在 Controller 与持久化边界之间优先 Service；
结合 @Transactional、上游入度、子图内是否可达数据库型节点等可组合信号。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from jdtls_lsp.callchain.format import (
    _classify_downstream_sink,
    _is_simple_accessor_leaf,
    extract_trace_payload_dict,
)
from jdtls_lsp.java_javadoc import extract_javadoc_above_method

# --- 文件缓存（@Transactional 等） ---


class _SourceLineCache:
    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()
        self._lines: dict[str, list[str]] = {}

    def lines(self, rel_file: str) -> list[str]:
        key = rel_file.replace("\\", "/")
        if key in self._lines:
            return self._lines[key]
        p = (self._root / key).resolve()
        try:
            if p.is_file() and p.is_relative_to(self._root):
                self._lines[key] = p.read_text(encoding="utf-8").splitlines()
            else:
                self._lines[key] = []
        except OSError:
            self._lines[key] = []
        return self._lines[key]


def _method_window_has_transactional(lines: list[str], line1: int) -> bool:
    """line1 为 1-based（与节点 ``line`` 一致）。"""
    i = line1 - 1
    if i < 0:
        return False
    lo = max(0, i - 120)
    win = "\n".join(lines[lo : i + 1])
    return bool(re.search(r"@Transactional\b", win))


def _is_controller_layer(node: dict[str, Any]) -> bool:
    if node.get("isRest"):
        return True
    fp = str(node.get("file", "")).lower().replace("\\", "/")
    if "/controller/" in fp or fp.endswith("controller.java"):
        return True
    cls = str(node.get("class", ""))
    simple = cls.rsplit(".", 1)[-1] if cls else ""
    if cls.endswith("Controller") or ".controller." in cls.lower():
        return True
    if any(seg in fp for seg in ("/web/", "/api/", "/rest/", "/resource/")):
        if simple.endswith(("Resource", "Rest", "Api", "Endpoint", "Handler")):
            return True
    return False


def _is_persistence_sink(node: dict[str, Any]) -> bool:
    return _classify_downstream_sink(node) == "database"


def _is_service_layer(node: dict[str, Any]) -> bool:
    if _is_persistence_sink(node):
        return False
    fp = str(node.get("file", "")).lower().replace("\\", "/")
    cls = str(node.get("class", ""))
    simple = cls.rsplit(".", 1)[-1] if cls else ""
    if "/service/" in fp or "service.impl" in fp or "service/" in fp:
        return True
    if simple.endswith("ServiceImpl"):
        return True
    if simple.endswith("Service") and not simple.endswith("Client") and "Service" in simple:
        return True
    return False


def _downchain_root_key(nodes: dict[str, Any], edges: list[Any]) -> str | None:
    tos: set[str] = set()
    for e in edges:
        if isinstance(e, dict) and isinstance(e.get("to"), str):
            tos.add(e["to"])
    roots = [k for k in nodes if k not in tos]
    return roots[0] if roots else None


def _depth_from_root(root_key: str | None, edges: list[Any]) -> dict[str, int]:
    if not root_key:
        return {}
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if not isinstance(e, dict):
            continue
        a, b = e.get("from"), e.get("to")
        if isinstance(a, str) and isinstance(b, str):
            adj[a].append(b)
    depth: dict[str, int] = {root_key: 0}
    q: deque[str] = deque([root_key])
    while q:
        u = q.popleft()
        du = depth[u]
        for v in adj.get(u, []):
            if v not in depth:
                depth[v] = du + 1
                q.append(v)
    return depth


def _reachable_persistence(
    start: str,
    nodes: dict[str, Any],
    edges: list[Any],
) -> set[str]:
    """从 start 沿出边可达、且为 database 分类的节点键。"""
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if not isinstance(e, dict):
            continue
        a, b = e.get("from"), e.get("to")
        if isinstance(a, str) and isinstance(b, str):
            adj[a].append(b)
    seen: set[str] = set()
    out: set[str] = set()
    dq: deque[str] = deque([start])
    seen.add(start)
    while dq:
        u = dq.popleft()
        n = nodes.get(u) if isinstance(nodes.get(u), dict) else {}
        if isinstance(n, dict) and _is_persistence_sink(n):
            out.add(u)
        for v in adj.get(u, []):
            if v not in seen:
                seen.add(v)
                dq.append(v)
    return out


def _in_degree(edges: list[Any]) -> dict[str, int]:
    inc: dict[str, int] = defaultdict(int)
    for e in edges:
        if isinstance(e, dict) and isinstance(e.get("to"), str):
            inc[e["to"]] += 1
    return inc


def annotate_downchain_business(payload: dict[str, Any], project_root: Path | None = None) -> dict[str, Any]:
    """
    就地扩展 ``payload``：为每个 ``nodes[*]`` 写入 ``businessScore``、``businessCandidate``、``businessSignals``；
    顶层写入 ``keyMethods``（按分数降序；**不含** ``javadoc`` 字段，避免与 ``business.md`` 生成时源码解析重复）、
    ``businessPhase`` = ``"step6"``。

    若 ``projectRoot`` 未传入，从 ``payload['query']['projectRoot']`` 读取。
    """
    q = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    root_s = str(q.get("projectRoot", "")).strip()
    if project_root is None:
        project_root = Path(root_s) if root_s else Path()
    else:
        project_root = Path(project_root).resolve()

    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), dict) else {}
    edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    if not nodes:
        payload["keyMethods"] = []
        payload["businessPhase"] = "step6"
        return payload

    root_key = _downchain_root_key(nodes, edges)
    depths = _depth_from_root(root_key, edges)
    inc = _in_degree(edges)
    cache = _SourceLineCache(project_root) if root_s else _SourceLineCache(Path("."))

    reach_db: dict[str, bool] = {}
    for nk in nodes:
        pers = _reachable_persistence(nk, nodes, edges)
        reach_db[nk] = len(pers) > 0

    key_rows: list[dict[str, Any]] = []

    for nk, raw in nodes.items():
        if not isinstance(raw, dict):
            continue
        n = raw
        signals: list[str] = []
        score = 0

        if _is_persistence_sink(n) or _is_controller_layer(n):
            n["businessScore"] = 0
            n["businessCandidate"] = False
            n["businessSignals"] = []
            continue

        if _is_simple_accessor_leaf(n):
            n["businessScore"] = 0
            n["businessCandidate"] = False
            n["businessSignals"] = ["accessor_skipped"]
            continue

        if _is_service_layer(n):
            score += 2
            signals.append("service_layer")

        if inc.get(nk, 0) >= 2:
            score += min(3, inc[nk])
            signals.append(f"in_degree_{inc[nk]}")

        if reach_db.get(nk):
            score += 3
            signals.append("reaches_persistence_subgraph")

        rel = str(n.get("file", ""))
        line1 = int(n.get("line", 0) or 0)
        if rel and line1 > 0 and cache.lines(rel) and _method_window_has_transactional(cache.lines(rel), line1):
            score += 2
            signals.append("transactional")

        d0 = depths.get(nk, -1)
        if d0 >= 1:
            score += 1
            signals.append("below_root")

        n["businessScore"] = score
        n["businessSignals"] = signals
        n["businessCandidate"] = score >= 4

        if n["businessCandidate"]:
            key_rows.append(
                {
                    "nodeKey": nk,
                    "class": n.get("class", ""),
                    "method": n.get("method", ""),
                    "file": n.get("file", ""),
                    "line": n.get("line", ""),
                    "score": score,
                    "signals": list(signals),
                }
            )

    key_rows.sort(key=lambda r: (-int(r.get("score", 0)), str(r.get("class", "")), str(r.get("method", ""))))
    payload["keyMethods"] = key_rows
    payload["businessPhase"] = "step6"
    return payload


def merge_key_methods_from_downchain_files(
    data_dir: Path,
    project_root: Path,
) -> tuple[list[dict[str, Any]], int]:
    """
    读取向下链报告（Markdown 文末 JSON 或纯 JSON），合并 ``keyMethods``。

    兼容两类产物：

    - **REST（历史）**：``callchain-down-rest-*.md`` / ``*.json``（含 ``data/callchain-down-rest/<Controller>/``）
    - **entrypoints（新）**：``callchain-down-entrypoints-*.md`` / ``*.json``（含 ``data/callchain-down-entrypoints/.../``）

    用 ``callchain.format.extract_trace_payload_dict`` 恢复 payload，并按 class+method+file 去重保留最高分。
    返回 (合并列表, 读取文件数)。
    """
    data_dir = data_dir.resolve()
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    nfiles = 0
    seen: set[Path] = set()
    candidates: list[Path] = []
    for pat in ("callchain-down-rest-*.md", "callchain-down-entrypoints-*.md"):
        for p in sorted(data_dir.rglob(pat)):
            if p.is_file():
                candidates.append(p)
                seen.add(p)
    for pat in ("callchain-down-rest-*.json", "callchain-down-entrypoints-*.json"):
        for p in sorted(data_dir.rglob(pat)):
            if p.is_file() and p not in seen:
                candidates.append(p)
    for path in candidates:
        nfiles += 1
        try:
            text = path.read_text(encoding="utf-8")
            obj = extract_trace_payload_dict(text)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if "keyMethods" not in obj and isinstance(obj.get("nodes"), dict):
            annotate_downchain_business(obj, project_root)
        try:
            src_label = str(path.relative_to(data_dir))
        except ValueError:
            src_label = path.name
        for km in obj.get("keyMethods") or []:
            if not isinstance(km, dict):
                continue
            ck = (
                str(km.get("class", "")),
                str(km.get("method", "")),
                str(km.get("file", "")),
            )
            sc = int(km.get("score", 0) or 0)
            old = best.get(ck)
            if old is None or sc > int(old.get("score", 0) or 0):
                row = dict(km)
                row["sourceFiles"] = [src_label]
                best[ck] = row
            elif old is not None and sc == int(old.get("score", 0) or 0):
                sf = old.get("sourceFiles")
                if isinstance(sf, list) and src_label not in sf:
                    sf.append(src_label)
    merged = sorted(best.values(), key=lambda r: (-int(r.get("score", 0)), str(r.get("class", ""))))
    return merged, nfiles


def format_business_md(
    project_root: Path,
    key_methods: list[dict[str, Any]],
    *,
    title: str = "关键业务候选（step6）",
    include_javadoc: bool = True,
) -> str:
    """供 ``business.md`` 与人读的聚合视图；默认根据 ``file`` + ``line`` 从源码解析方法 Javadoc。"""
    cache = _SourceLineCache(project_root) if include_javadoc else None
    lines = [
        f"# {title}",
        "",
        f"- **projectRoot**: `{project_root.resolve()}`",
        "- **说明**: 由各向下子图报告（`.md` 文末 JSON 或纯 `.json`；REST 或 entrypoints）的 `keyMethods` 合并去重；"
        "启发式见 `jdtls_lsp.business_summary` 包（step6）；与 `--business-summary` 对应。",
        "- **Javadoc**: 使用 ``jdtls_lsp.java_javadoc.extract_javadoc_above_method``，在 ``projectRoot`` 下按 ``file``+``line`` 解析 ``/** … */``。",
        "",
        "## 合并列表（按 score 降序）",
        "",
    ]
    if not key_methods:
        lines.append(
            "_（无候选；需先产出 ``data/callchain-down-rest/.../callchain-down-rest-*.md`` 或 ``data/callchain-down-entrypoints/.../callchain-down-entrypoints-*.md``（或同 pattern 的 ``*.json``）且节点满足标权阈值）_"
        )
        lines.append("")
        return "\n".join(lines)

    for i, r in enumerate(key_methods, start=1):
        c = str(r.get("class", ""))
        m = str(r.get("method", ""))
        f = str(r.get("file", ""))
        ln = r.get("line", "")
        sc = r.get("score", "")
        sig = r.get("signals") if isinstance(r.get("signals"), list) else []
        sig_s = ", ".join(str(x) for x in sig)
        src = r.get("sourceFiles") if isinstance(r.get("sourceFiles"), list) else []
        src_s = ", ".join(str(x) for x in src[:6])
        if len(src) > 6:
            src_s += ", …"
        jd = ""
        if include_javadoc and cache:
            rel = str(f).strip()
            try:
                ln_i = int(ln) if ln is not None and str(ln).strip() != "" else 0
            except (TypeError, ValueError):
                ln_i = 0
            if rel and ln_i > 0:
                ls = cache.lines(rel)
                if ls:
                    jx = extract_javadoc_above_method(ls, ln_i)
                    jd = (jx or "").strip()
        lines.append(f"{i}. **`{c}.{m[:80]}{'…' if len(str(m)) > 80 else ''}`** — `{f}:{ln}` — score=`{sc}` — {sig_s}")
        if src_s:
            lines.append(f"   - 来源: {src_s}")
        if include_javadoc and jd:
            lines.append("   - **Javadoc**:")
            lines.append("")
            lines.append("     ```text")
            for jd_line in jd.split("\n"):
                lines.append(f"     {jd_line}")
            lines.append("     ```")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "annotate_downchain_business",
    "extract_javadoc_above_method",
    "format_business_md",
    "merge_key_methods_from_downchain_files",
]
