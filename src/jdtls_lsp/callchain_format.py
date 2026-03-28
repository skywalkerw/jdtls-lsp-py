"""调用链报告输出：将 trace 产出的 JSON 转为 Markdown（向上 / 向下）。

与 LSP 追踪逻辑解耦；后续可在本模块或 ``jdtls_lsp.output`` 下扩展 HTML、Mermaid 等格式。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

_STOP_REASON_LABEL: dict[str, str] = {
    "rest_endpoint": "REST 接口（检测到 @RequestMapping 等）",
    "abstract_class": "abstract 类",
    "max_depth": "达到 max_depth",
    "no_incoming": "无上游调用（LSP incomingCalls 为空）",
    "cycle": "检测到调用环",
    "jdtls_error": "JDTLS incomingCalls 内部错误（常见为 NPE，链到此为止）",
}


def _compact_method_signature(meth: str, max_len: int = 72) -> str:
    """方法展示用：去掉返回类型，参数改为 (…)；过长则截断。"""
    raw = str(meth).strip()
    if " : " in raw:
        raw = raw.split(" : ", 1)[0].strip()
    if "(" in raw:
        head = raw[: raw.index("(")].strip()
        return f"{head}(…)"
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 3] + "..."


def _short_class_name(name: str, max_len: int = 48) -> str:
    """仅保留简单类名（去包名），过长再截断。"""
    s = str(name).strip()
    if not s:
        return "?"
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _short_file_loc(file_rel: str, line: Any) -> str:
    """图示中只显示 `文件名:行号`，缩短包路径。"""
    try:
        base = Path(str(file_rel)).name
    except Exception:
        base = str(file_rel).split("/")[-1].split("\\")[-1]
    return f"{base}:{line}"


def _ascii_tree_for_chain(nodes: list[dict[str, Any]]) -> str:
    """Bottom row = 起点方法，向下为各层调用方（向上追踪顺序）。"""
    if not nodes:
        return "(空链)"
    lines: list[str] = []
    n0 = nodes[0]
    lines.append(
        f"{_short_class_name(str(n0.get('class', '?')))}.{_compact_method_signature(str(n0.get('method', '')))}  "
        f"# {_short_file_loc(str(n0.get('file', '')), n0.get('line'))}"
    )
    for i in range(1, len(nodes)):
        n = nodes[i]
        pad = "    " * i
        lines.append(
            f"{pad}└── {_short_class_name(str(n.get('class', '?')))}.{_compact_method_signature(str(n.get('method', '')))}  "
            f"# {_short_file_loc(str(n.get('file', '')), n.get('line'))}"
        )
    return "\n".join(lines)

def _short_node_line(node: dict[str, Any]) -> str:
    """单行可读：类.方法 — 文件:行。"""
    c = str(node.get("class", "?"))
    m = str(node.get("method", "")).strip()
    if len(m) > 88:
        m = m[:85] + "..."
    f = str(node.get("file", ""))
    ln = node.get("line", "")
    return f"`{c}.{m}` — `{f}:{ln}`"


def _short_node_line_compact(node: dict[str, Any]) -> str:
    """类 + 方法(…) + 文件名:行（向上/向下 Markdown 共用）。"""
    c = str(node.get("class", "?"))
    disp = _compact_method_signature(str(node.get("method", "")))
    fp = str(node.get("file", ""))
    try:
        fn = Path(fp).name
    except Exception:
        fn = fp.split("/")[-1].split("\\")[-1] if fp else "?"
    ln = node.get("line", "")
    return f"`{c}.{disp}` `{fn}:{ln}`"


def _md_leaf_badge(is_leaf: bool) -> str:
    """向下链列表：子图中无出边时前缀「（叶）」，与 `_lines_for_bucket` / 其他叶列表一致。"""
    return "（叶）" if is_leaf else ""


def _md_numbered_node_line(index: int, node: dict[str, Any], *, is_leaf: bool = False) -> str:
    """编号 + 可选叶标记 + `_short_node_line_compact`（向下「数据库/中间件/第三方」等分类列表）。"""
    leaf = _md_leaf_badge(is_leaf)
    return f"{index}. {leaf}{_short_node_line_compact(node)}"


def _md_numbered_compact_only(index: int, node: dict[str, Any]) -> str:
    """编号 + 紧凑方法行（无「（叶）」）；用于标题已说明「叶」的小节，避免重复。"""
    return f"{index}. {_short_node_line_compact(node)}"


def _md_up_chain_entry_line(seq: int, chain_nums_s: str, body_after_emdash: str) -> str:
    """向上「调用起点入口」：`1. **链 1、2** — …`；body 内仍以 `_short_node_line_compact` 开头以保持与向下一致。"""
    return f"{seq}. **链 {chain_nums_s}** — {body_after_emdash}"


def _rest_endpoint_display(te: dict[str, Any]) -> str:
    """合并 HTTP 方法与路径，避免 `POST` + `POST /path` 重复。"""
    rs = str(te.get("restSummary") or "").strip()
    rp = str(te.get("restPath") or "").strip()
    hm = str(te.get("httpMethod") or "").strip()
    if rs:
        if hm and rs.upper().startswith(hm.upper() + " "):
            return rs
        if hm and rs.split() and rs.split()[0].upper() in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            return rs
        if hm and rp and not rs.startswith(hm):
            return f"{hm} {rs}".strip()
        return rs
    if hm and rp:
        return f"{hm} {rp}".strip()
    return rp or hm or ""


def _up_entry_merge_key_and_suffix(
    top: dict[str, Any],
    te: dict[str, Any],
    sr: str,
) -> tuple[tuple[Any, ...], str]:
    """
    返回 (合并键, 「—」后的正文)：正文始终以 ``_short_node_line_compact(top)`` 起头，
    再接 **REST** / 终止码，与向下链叶节点行共用同一套方法展示。
    """
    top_s = _short_node_line_compact(top)
    jd_tail = ""
    if isinstance(te.get("javadoc"), str) and str(te.get("javadoc")).strip():
        jd_tail = str(te.get("javadoc")).strip().splitlines()[0][:160]

    if te.get("restSummary") or te.get("restPath") or te.get("httpMethod"):
        ep = _rest_endpoint_display(te)
        key: tuple[Any, ...] = ("rest", sr, ep, top_s, jd_tail)
        parts: list[str] = [top_s]
        if ep:
            parts.append(f"**REST** `{ep}`")
        parts.append(f"终止 `{sr}`")
        if jd_tail:
            parts.append(f"**JavaDoc** {jd_tail}")
        return key, " · ".join(parts)
    if top.get("isRest"):
        key = ("isrest", sr, top_s)
        return key, " · ".join([top_s, "**REST**（路径未解析）", f"终止 `{sr}`"])
    key = ("plain", sr, top_s)
    return key, " · ".join([top_s, f"终止 `{sr}`"])


def _markdown_up_entry_sections(chains: list[Any]) -> list[str]:
    """向上链：每条链一行；根（链号）+ 与向下链相同的紧凑方法行 + REST + 终止码；grep 多起点合并规则不变。"""
    rows: list[dict[str, Any]] = []
    for idx, ch in enumerate(chains):
        if not isinstance(ch, dict):
            continue
        nodes = ch.get("chain")
        if not isinstance(nodes, list) or not nodes:
            continue
        top = nodes[-1]
        if not isinstance(top, dict):
            continue
        te = ch.get("topEntry") if isinstance(ch.get("topEntry"), dict) else {}
        sr = str(ch.get("stopReason", ""))
        key, suffix = _up_entry_merge_key_and_suffix(top, te, sr)
        rows.append({"chain": idx + 1, "key": key, "rest_line": suffix})

    groups: dict[tuple[Any, ...], list[int]] = {}
    order_keys: list[tuple[Any, ...]] = []
    rest_line_by_key: dict[tuple[Any, ...], str] = {}
    for r in rows:
        k = r["key"]
        if k not in groups:
            groups[k] = []
            order_keys.append(k)
            rest_line_by_key[k] = r["rest_line"]
        groups[k].append(r["chain"])

    out: list[str] = []
    for seq, k in enumerate(order_keys, start=1):
        nums = groups[k]
        nums_s = "、".join(str(x) for x in nums)
        out.append(_md_up_chain_entry_line(seq, nums_s, rest_line_by_key[k]))
    return out


def _classify_downstream_sink(node: dict[str, Any]) -> str:
    """
    启发式分类向下链中的「终点」侧节点：数据库 / 中间件 / 第三方 HTTP 客户端 / 其他。
    仅基于类名、路径、方法名，不解析 AST。
    """
    cls = str(node.get("class", ""))
    file = str(node.get("file", "")).lower().replace("\\", "/")
    meth = str(node.get("method", "")).lower()
    blob = f"{cls.lower()} {file} {meth}"

    if "repository" in cls or "/repository/" in file:
        return "database"
    if "mapper" in cls and ("mapper" in file or "mybatis" in file):
        return "database"
    if any(x in blob for x in ("jdbctemplate", "entitymanager", "namedparameterjdbc", "preparedstatement")):
        return "database"
    if any(x in blob for x in (".jpa.", "hibernate", "jpql", "criteriaquery")):
        return "database"
    if cls.endswith("Dao") or "Dao" in cls:
        return "database"

    if any(x in blob for x in ("kafka", "rabbit", "amqp", "redis", "mongotemplate", "springframework.data.mongodb")):
        return "middleware"
    if any(x in cls for x in ("Kafka", "Rabbit", "Redis", "Amqp", "MongoTemplate")):
        return "middleware"

    if any(
        x in blob
        for x in (
            "resttemplate",
            "webclient",
            "feign",
            "openfeign",
            "httpclient",
            "okhttp",
            "retrofit",
            "webmvc.client",
        )
    ):
        return "external_api"
    if "/feign/" in file or "feign" in file:
        return "external_api"

    return "other"


def _method_name_without_return(meth: str) -> str:
    """`getFoo() : String` → `getFoo()`。"""
    m = str(meth).strip()
    if " : " in m:
        m = m.split(" : ", 1)[0].strip()
    return m


def _is_simple_accessor_leaf(node: dict[str, Any]) -> bool:
    """简单 JavaBean 风格 getter/setter/isFoo（叶节点上用于 Markdown 归并）。"""
    m = _method_name_without_return(str(node.get("method", "")))
    return bool(re.match(r"^(get|set|is)[A-Za-z_]\w*\s*\(", m))


def _summarize_accessor_leaves_md(accessor_keys: list[str], nodes: dict[str, Any]) -> list[str]:
    """按文件汇总 getter/setter 叶节点，一行一类。"""
    by_file: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for nk in accessor_keys:
        n = nodes.get(nk) if isinstance(nodes.get(nk), dict) else {}
        f = str(n.get("file", ""))
        c = str(n.get("class", "?"))
        meth = _method_name_without_return(str(n.get("method", "")))
        by_file[f].append((c, meth))

    out: list[str] = []
    for f in sorted(by_file.keys()):
        pairs = by_file[f]
        cls = pairs[0][0] if pairs else "?"
        try:
            fn = Path(f).name
        except Exception:
            fn = f.split("/")[-1].split("\\")[-1] if f else "?"
        out.append(f"`{fn}` · `{cls}`：**{len(pairs)}** 个（get/set/is…）")
    return out


def _collect_downstream_sinks_by_kind(
    nodes: dict[str, Any],
    edges: list[Any],
) -> tuple[dict[str, list[str]], set[str]]:
    """
    返回 {kind: [node_key,...]}，且计算「叶」节点键（在子图中无出边）。
    """
    outgoing_from: set[str] = set()
    for e in edges:
        if isinstance(e, dict) and isinstance(e.get("from"), str):
            outgoing_from.add(e["from"])
    leaf_keys = {k for k in nodes if k not in outgoing_from}

    buckets: dict[str, list[str]] = {"database": [], "middleware": [], "external_api": [], "other": []}
    # 优先：叶节点且分类非 other
    for nk, n in nodes.items():
        if not isinstance(n, dict):
            continue
        kind = _classify_downstream_sink(n)
        buckets[kind].append(nk)

    for k in buckets:
        buckets[k].sort()

    return buckets, leaf_keys


def format_callchain_markdown(payload: dict[str, Any]) -> str:
    """
    将 callchain-up 的 JSON 结构体转为 Markdown：含流程说明、ASCII 图示与嵌入的 JSON 代码块。
    """
    q = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    project = str(q.get("projectRoot", ""))
    mode = str(q.get("mode", "class_method"))
    cname = str(q.get("className", ""))
    mname = str(q.get("methodName", ""))
    count = int(payload.get("chainCount", 0))
    chains = payload.get("chains")
    if not isinstance(chains, list):
        chains = []

    query_lines = [
        f"- **projectRoot**: `{project}`",
        f"- **mode**: `{mode}`",
    ]
    if mode == "class_method":
        query_lines.append(f"- **className**: `{cname}`")
        query_lines.append(f"- **methodName**: `{mname}`")
    elif mode == "file_line":
        query_lines.append(f"- **file**: `{q.get('file', '')}`")
        query_lines.append(f"- **line**: `{q.get('line', '')}`")
        query_lines.append(f"- **character**: `{q.get('character', '')}`")
        query_lines.append(f"- **解析 class**: `{cname}`")
        query_lines.append(f"- **解析 method**: `{mname}`")
    elif mode == "keyword":
        query_lines.append(f"- **keyword**: `{q.get('keyword', '')}`")
        kr = q.get("keywordResolution")
        if isinstance(kr, str) and kr.strip():
            query_lines.append(f"- **keyword 解析方式**: `{kr.strip()}`")
        if q.get("keywordResolution") == "java_text_grep":
            gn = q.get("grepNeedles")
            if isinstance(gn, list) and gn:
                query_lines.append(f"- **全文搜索 needles**: `{gn}`")
            gf = q.get("grepEntryFilters")
            if isinstance(gf, dict) and gf:
                if gf.get("skipInterfaceFiles"):
                    query_lines.append("- **grep 起点过滤**: 跳过「顶层为 interface」的源文件")
                if gf.get("skipRestEntrypoints"):
                    query_lines.append("- **grep 起点过滤**: 跳过起点已是 REST 的方法")
                if gf.get("maxEntryPoints") is not None:
                    query_lines.append(f"- **grep 起点上限**: 最多 `{gf.get('maxEntryPoints')}` 条（按实现类优先排序后截断）")
            if q.get("javaGrepMultiFile"):
                jge = q.get("javaGrepEntries")
                if isinstance(jge, list) and jge:
                    if q.get("javaGrepTraceSequential"):
                        query_lines.append(
                            f"- **多入口串行追踪**: `{len(jge)}` 个起点（单 LSP / 单 JDTLS，避免并发 incomingCalls）"
                        )
                    else:
                        query_lines.append(
                            f"- **多入口**: `{len(jge)}` 个起点，workers=`{q.get('javaGrepParallelWorkers', '')}`"
                        )
                    for ent in jge[:24]:
                        if not isinstance(ent, dict):
                            continue
                        query_lines.append(
                            f"  - `{ent.get('file', '')}` → `{ent.get('className', '')}.{ent.get('methodName', '')}` "
                            f"(line {ent.get('line', '')})"
                        )
                    if len(jge) > 24:
                        query_lines.append(f"  - … 共 {len(jge)} 条")
            if q.get("file"):
                query_lines.append(f"- **grep 命中文件**: `{q.get('file', '')}`")
            if q.get("line"):
                query_lines.append(f"- **grep 命中行**: `{q.get('line', '')}`")
            if q.get("grepHitLine") and q.get("line") and q.get("grepHitLine") != q.get("line"):
                query_lines.append(f"- **grep 原始命中行**: `{q.get('grepHitLine', '')}`")
            mp = q.get("matchedLinePreview")
            if isinstance(mp, str) and mp.strip():
                query_lines.append(f"- **命中行预览**: `{mp.strip()[:120]}`")
        query_lines.append(f"- **解析 class**: `{cname}`")
        query_lines.append(f"- **解析 method**: `{mname}`")
    else:
        query_lines.append(f"- **className**: `{cname}`")
        query_lines.append(f"- **methodName**: `{mname}`")

    entry_blocks = _markdown_up_entry_sections(chains)
    entry_md = "\n\n".join(entry_blocks) if entry_blocks else "_（无可用链）_"

    lines: list[str] = [
        "# Java 调用链（callchain-up）",
        "",
        "## 查询",
        "",
        *query_lines,
        "",
        "## 流程概览",
        "",
        "```",
        "起点方法（实现层）",
        "    └── LSP callHierarchy/incomingCalls 向上取直接调用方",
        "            └── 重复直至 REST / abstract / 无上游 / 环 / max_depth",
        "```",
        "",
        "## 概要说明",
        "",
        "本报告为 **向上** 调用链：从起点方法沿 LSP **incomingCalls** 向上追踪，直至 REST、abstract、无上游、环或深度上限。",
        "**阅读重点**：下一节「调用起点入口」汇总每条链在系统边界上的 **最上游入口**（通常为 HTTP/REST；否则为无上游时的顶层方法）。",
        "",
        f"- **链数**: {count}",
        "- **追踪方向**: 自内向外（被调方法 → 调用者 → …）",
        "",
        "## 调用起点入口（重点）",
        "",
        entry_md,
        "",
        "## 调用链图示",
        "",
    ]

    for idx, ch in enumerate(chains):
        if not isinstance(ch, dict):
            continue
        sr = str(ch.get("stopReason", ""))
        label = _STOP_REASON_LABEL.get(sr, sr)
        nodes = ch.get("chain")
        if not isinstance(nodes, list):
            nodes = []
        gsf = ch.get("grepSourceFile")
        gtitle = f" · grep起点 `{gsf}:{ch.get('grepSourceLine', '')}`" if gsf else ""
        lines.append(f"### {idx + 1}. 链 {idx + 1}（终止: {label}{gtitle}）")
        lines.append("")
        lines.append("```")
        lines.append(_ascii_tree_for_chain([x for x in nodes if isinstance(x, dict)]))
        lines.append("```")
        lines.append("")
        je = ch.get("jdtlsError")
        if isinstance(je, str) and je.strip() and sr == "jdtls_error":
            lines.append("#### JDTLS 错误（incomingCalls）")
            lines.append("")
            lines.append(f"```\n{je.strip()[:900]}\n```")
            lines.append("")
        te = ch.get("topEntry")
        if isinstance(te, dict) and te:
            lines.append("#### 该链 REST / JavaDoc（补充）")
            lines.append("")
            n_sub = 1
            ep = _rest_endpoint_display(te)
            if ep:
                lines.append(f"{n_sub}. **REST**: `{ep}`")
                n_sub += 1
            rs = te.get("restSummary")
            cb = te.get("classBasePath")
            if isinstance(cb, str) and cb.strip() and not (isinstance(rs, str) and rs.strip()):
                lines.append(f"{n_sub}. **类级 base**: `{cb.strip()}`")
                n_sub += 1
            jd = te.get("javadoc")
            if isinstance(jd, str) and jd.strip():
                lines.append(f"{n_sub}. **JavaDoc**:")
                lines.append("")
                for jl in jd.strip().splitlines():
                    lines.append(f"  {jl}")
            lines.append("")

    lines.extend(
        [
            "## 原始 JSON",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)

_DOWN_STOP_LABEL: dict[str, str] = {
    "complete": "遍历结束（队列空）",
    "max_nodes": "达到 max_nodes 上限",
    "branch_cap": "某层 outgoing 超过 max_branches（已截断）",
}


def format_downchain_markdown(payload: dict[str, Any]) -> str:
    """向下调用子图 JSON → Markdown（含概要、终点分类重点）。"""
    q = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    project = str(q.get("projectRoot", ""))
    mode = str(q.get("mode", "class_method"))
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), dict) else {}
    edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    sr = str(payload.get("stopReason", ""))
    jerr = payload.get("jdtlsErrors")
    if not isinstance(jerr, list):
        jerr = []

    buckets, leaf_keys = _collect_downstream_sinks_by_kind(nodes, edges)

    def _lines_for_bucket(kind: str, title: str, hint: str) -> list[str]:
        keys = buckets.get(kind, [])
        out: list[str] = [f"### {title}", "", hint, ""]
        if not keys:
            out.append("_（本图中未匹配到该类启发式规则）_")
            out.append("")
            return out
        for i, nk in enumerate(keys, start=1):
            n = nodes.get(nk) if isinstance(nodes.get(nk), dict) else {}
            if n:
                out.append(_md_numbered_node_line(i, n, is_leaf=nk in leaf_keys))
            else:
                out.append(f"{i}. `{nk}`")
        out.append("")
        return out

    lines: list[str] = [
        "# Java 向下调用子图（outgoing BFS）",
        "",
        "## 概要说明",
        "",
        "本报告为 **向下** 调用子图：自起点方法沿 LSP **outgoingCalls** 做 BFS，收集被调方法及边表。",
        "**阅读重点**：下列「下游终点分类」按启发式将节点归为 **数据库访问**、**中间件**、**第三方/HTTP 客户端**；规则基于类名/包路径/方法名（非 AST），仅供参考。标记「（叶）」表示在该子图中 **无进一步出边** 的节点。",
        "**Markdown 与 JSON**：简单 **get/set/is** 叶节点在 Markdown 中 **按文件汇总条数**，不逐条展开；**末尾 JSON 的 `nodes` 仍为完整节点**，无删减。",
        "",
        "## 查询",
        "",
        f"- **projectRoot**: `{project}`",
        f"- **mode**: `{mode}`",
    ]
    if mode == "class_method":
        lines.append(f"- **className**: `{q.get('className', '')}`")
        lines.append(f"- **methodName**: `{q.get('methodName', '')}`")
    elif mode == "file_line":
        lines.append(f"- **file**: `{q.get('file', '')}`")
        lines.append(f"- **line**: `{q.get('line', '')}`")
    elif mode == "keyword":
        lines.append(f"- **keyword**: `{q.get('keyword', '')}`")

    lines.extend(
        [
            "",
            "## 参数与统计",
            "",
            f"- **stopReason**: {_DOWN_STOP_LABEL.get(sr, sr)} (`{sr}`)",
            f"- **nodes**: {stats.get('nodeCount', len(nodes))}",
            f"- **edges**: {stats.get('edgeCount', len(edges))}",
            f"- **expanded**: {stats.get('expandedCount', '')}",
            f"- **maxDepth / maxNodes / maxBranches**: {stats.get('maxDepth')} / {stats.get('maxNodes')} / {stats.get('maxBranches')}",
        ]
    )
    if jerr:
        lines.append(f"- **JDTLS 错误次数**: {len(jerr)}（详见 JSON `jdtlsErrors`）")

    lines.extend(
        [
            "",
            "## 下游终点分类（重点）",
            "",
            "以下为图中匹配到的节点；同一节点仅出现在其主分类中。",
            "",
        ]
    )
    lines.extend(
        _lines_for_bucket(
            "database",
            "数据库访问（Repository / DAO / JDBC / JPA 等启发式）",
            "_典型：类名或路径含 Repository、Mapper、JdbcTemplate、EntityManager 等。_",
        )
    )
    lines.extend(
        _lines_for_bucket(
            "middleware",
            "中间件（消息、缓存、Mongo 等启发式）",
            "_典型：Kafka、Rabbit、Redis、AMQP、MongoTemplate 等相关命名。_",
        )
    )
    lines.extend(
        _lines_for_bucket(
            "external_api",
            "第三方 / HTTP 客户端（RestTemplate、WebClient、Feign 等启发式）",
            "_典型：RestTemplate、WebClient、OpenFeign、HttpClient、OkHttp 等。_",
        )
    )

    other_keys = [k for k in buckets.get("other", []) if k in leaf_keys]
    accessor_keys = [k for k in other_keys if isinstance(nodes.get(k), dict) and _is_simple_accessor_leaf(nodes[k])]
    accessor_set = set(accessor_keys)
    other_non_accessor = sorted([k for k in other_keys if k not in accessor_set])

    if accessor_keys:
        lines.extend(
            [
                "### 简单 getter/setter 叶节点（Markdown 归并；JSON 仍完整）",
                "",
                f"_共 **{len(accessor_keys)}** 个；按 **源文件** 汇总如下，**不**逐方法列出。详细键名与行列见文末 JSON `nodes`。",
                "",
            ]
        )
        acc_lines = _summarize_accessor_leaves_md(accessor_keys, nodes)
        for j, al in enumerate(acc_lines, start=1):
            lines.append(f"{j}. {al}")
        lines.append("")

    if other_non_accessor:
        lines.extend(
            [
                "### 其他叶节点（非上述三类，且非简单 get/set/is；在本子图中无出边）",
                "",
                "_逐条列出（构造器、业务方法等）；完整见 JSON。_",
                "",
            ]
        )
        cap = 40
        for j, nk in enumerate(other_non_accessor[:cap], start=1):
            n = nodes.get(nk) if isinstance(nodes.get(nk), dict) else {}
            if n:
                lines.append(_md_numbered_compact_only(j, n))
            else:
                lines.append(f"{j}. `{nk}`")
        if len(other_non_accessor) > cap:
            lines.append(f"{min(len(other_non_accessor), cap) + 1}. … 其余 **{len(other_non_accessor) - cap}** 条略（见 `nodes`）")
        lines.append("")

    lines.extend(
        [
            "## 边列表（from → to，前 200 条）",
            "",
        ]
    )
    for i, e in enumerate(edges[:200]):
        if not isinstance(e, dict):
            continue
        lines.append(f"{i + 1}. `{e.get('from', '')}` → `{e.get('to', '')}`")
    if len(edges) > 200:
        lines.append(f"… 共 {len(edges)} 条，仅显示前 200 条")
    lines.extend(["", "## 原始 JSON", "", "```json", json.dumps(payload, ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)


__all__ = [
    "format_callchain_markdown",
    "format_downchain_markdown",
]
