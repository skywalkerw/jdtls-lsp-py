"""调用链报告输出：将 trace 产出的 JSON 转为 Markdown（向上 / 向下）。

属于 ``jdtls_lsp.callchain`` 包，与 ``trace`` 子模块（LSP 包装追踪）解耦；可在此扩展 HTML、Mermaid 等格式。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

_STOP_REASON_LABEL: dict[str, str] = {
    "rest_endpoint": "REST 接口（检测到 @RequestMapping 等）",
    "message_listener": "消息消费者（@KafkaListener / @RabbitListener 等，见节点 listenerMarkers）",
    "scheduled_task": "定时任务（@Scheduled / Quartz execute / @XxlJob 等）",
    "async_method": "异步方法（Spring `@Async`，见节点 asyncMarkers）",
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


def _ascii_line_downchain_node(node: dict[str, Any]) -> str:
    """单行：`Class.method(…)  # File.java:line`（与向下 ASCII 图示例一致）。"""
    c = _short_class_name(str(node.get("class", "?")))
    m = _compact_method_signature(str(node.get("method", "")))
    loc = _short_file_loc(str(node.get("file", "")), node.get("line"))
    return f"{c}.{m}  # {loc}"


def _ascii_tree_for_downchain_graph(
    start_key: str | None,
    nodes: dict[str, Any],
    edges: list[Any],
) -> str:
    """
    自起点沿 **出边** 展开为 ASCII 树（多子节点用 ``├──``/``└──``；与单链 ``_ascii_tree_for_chain`` 的缩进风格一致）。
    """
    if not isinstance(nodes, dict) or not nodes:
        return "(空图)"

    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if isinstance(e, dict):
            a, b = e.get("from"), e.get("to")
            if isinstance(a, str) and isinstance(b, str) and b in nodes:
                adj[a].append(b)
    for k in adj:
        adj[k] = sorted(set(adj[k]))

    sk: str | None = start_key if isinstance(start_key, str) and start_key in nodes else None
    if sk is None:
        incoming: dict[str, int] = defaultdict(int)
        for e in edges:
            if isinstance(e, dict) and isinstance(e.get("to"), str):
                incoming[e["to"]] += 1
        roots = [k for k in nodes if incoming.get(k, 0) == 0]
        if len(roots) == 1:
            sk = roots[0]
        elif len(roots) > 1:
            sk = sorted(roots)[0]
        else:
            sk = sorted(nodes.keys())[0]

    def walk(key: str, prefix: str, is_last: bool, is_root: bool, stack: list[str]) -> list[str]:
        if key in stack:
            n = nodes.get(key)
            lbl = _ascii_line_downchain_node(n) if isinstance(n, dict) else str(key)
            branch = "└── " if is_last else "├── "
            if is_root:
                return [f"{lbl}  (cycle)"]
            return [f"{prefix}{branch}{lbl}  (cycle)"]

        n = nodes.get(key)
        if not isinstance(n, dict):
            return [f"{prefix}(missing node `{key}`)"]

        text = _ascii_line_downchain_node(n)
        if is_root:
            out = [text]
            child_prefix = "    "
        else:
            branch = "└── " if is_last else "├── "
            out = [f"{prefix}{branch}{text}"]
            child_prefix = prefix + ("    " if is_last else "│   ")

        chs = adj.get(key, [])
        stack_next = stack + [key]
        for i, ck in enumerate(chs):
            out.extend(walk(ck, child_prefix, i == len(chs) - 1, False, stack_next))
        return out

    return "\n".join(walk(sk, "", True, True, []))


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
    """向上「重点（边界入口）」汇总行：`1. **链 1、2** — …`；body 内仍以 `_short_node_line_compact` 开头以保持与向下一致。"""
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

    if sr == "message_listener":
        markers = [str(x) for x in (top.get("listenerMarkers") or []) if str(x).strip()]
        mkey: tuple[Any, ...] = ("msg", sr, tuple(markers), top_s)
        mtxt = ", ".join(markers) if markers else "消息监听"
        return mkey, " · ".join([top_s, f"**消息/队列** `{mtxt}`", f"终止 `{sr}`"])
    if sr == "scheduled_task":
        markers = [str(x) for x in (top.get("scheduledMarkers") or []) if str(x).strip()]
        skey: tuple[Any, ...] = ("sched", sr, tuple(markers), top_s)
        stxt = ", ".join(markers) if markers else "定时任务"
        return skey, " · ".join([top_s, f"**定时** `{stxt}`", f"终止 `{sr}`"])
    if sr == "async_method":
        markers = [str(x) for x in (top.get("asyncMarkers") or []) if str(x).strip()]
        akey: tuple[Any, ...] = ("async", sr, tuple(markers), top_s)
        atxt = ", ".join(markers) if markers else "@Async"
        return akey, " · ".join([top_s, f"**异步** `{atxt}`", f"终止 `{sr}`"])

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
    """向上链「重点（边界入口）」汇总行：每条链一行；紧凑方法行 + REST + 终止码；grep 多起点合并规则不变。"""
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


def _ma_bullet(label: str, val: Any) -> str | None:
    if val is None or val == "":
        return None
    return f"- **{label}**: `{val}`"


def manifest_anchor_markdown_lines(ma: dict[str, Any]) -> list[str]:
    """``query.manifestAnchor`` → Markdown 列表行（与 ``tables-manifest`` / step5 追溯一致）。"""
    pairs: list[tuple[str, str]] = [
        ("manifestHitId", "manifestHitId"),
        ("physicalTable", "physicalTable"),
        ("anchorKind", "anchorKind"),
        ("manifestSource", "manifestSource"),
        ("implFile", "ServiceImpl 文件"),
        ("className", "className"),
        ("methodName", "methodName"),
        ("repositoryType", "repositoryType"),
        ("implRank", "implRank"),
        ("entityFile", "entityFile"),
        ("entityDeclarationLine", "entityDeclarationLine"),
        ("entityDeclarationCharacter", "entityDeclarationCharacter"),
        ("entityName", "entityName"),
        ("entityPathSource", "entityPathSource"),
        ("sqlLiteralHitFile", "sqlLiteralHitFile"),
        ("sqlLiteralHitLine", "sqlLiteralHitLine"),
        ("javaMethod", "javaMethod"),
        ("javaMethodLine", "javaMethodLine"),
        ("tableAsFound", "tableAsFound"),
        ("confidence", "confidence"),
        ("callchainAnchorMode", "callchainAnchorMode"),
        ("resolvedCallchainClassName", "resolvedCallchainClassName"),
        ("resolvedCallchainMethodName", "resolvedCallchainMethodName"),
        ("xmlFile", "xmlFile"),
        ("xmlLine", "xmlLine"),
        ("mapperNamespace", "mapperNamespace"),
        ("mapperStatementId", "mapperStatementId"),
        ("javaMapperFile", "javaMapperFile"),
        ("javaMapperLine", "javaMapperLine"),
        ("snippet", "snippet"),
    ]
    out: list[str] = []
    for key, label in pairs:
        if key not in ma:
            continue
        ln = _ma_bullet(label, ma[key])
        if ln:
            out.append(ln)
    return out


def rest_map_anchor_markdown_lines(ra: dict[str, Any]) -> list[str]:
    """``query.restMapAnchor`` → Markdown 列表行（与 ``rest-map.json`` / step4 追溯一致）。"""
    pairs: list[tuple[str, str]] = [
        ("restHitId", "restHitId"),
        ("httpMethod", "httpMethod"),
        ("path", "path"),
        ("slug", "slug（产物文件名标签）"),
        ("controllerClassName", "controllerClassName（rest-map）"),
        ("handlerMethodName", "handlerMethodName（rest-map）"),
        ("restMapFile", "restMapFile"),
        ("restMapLine", "restMapLine"),
        ("annotation", "annotation"),
        ("anchorClassName", "anchorClassName（callchain-down 起点）"),
        ("anchorMethodName", "anchorMethodName"),
        ("anchorResolution", "anchorResolution"),
    ]
    out: list[str] = []
    for key, label in pairs:
        if key not in ra:
            continue
        ln = _ma_bullet(label, ra[key])
        if ln:
            out.append(ln)
    return out


def apply_rest_map_anchor_to_downchain_markdown(raw: str, rest_map_anchor: dict[str, Any]) -> str:
    """
    将 **rest-map 追溯块** 写入 callchain-down Markdown：解析嵌入 JSON，设置 ``query.restMapAnchor`` 后重排板。
    """
    try:
        obj = extract_trace_payload_dict(raw)
    except (ValueError, json.JSONDecodeError):
        return raw
    q = obj.get("query")
    if not isinstance(q, dict):
        q = {}
    q = {**q, "restMapAnchor": dict(rest_map_anchor)}
    obj["query"] = q
    return format_downchain_markdown(obj)


def apply_manifest_anchor_to_callchain_markdown(raw: str, manifest_anchor: dict[str, Any]) -> str:
    """
    将 **tables-manifest 追溯块** 写入 callchain-up Markdown：解析嵌入 JSON，设置 ``query.manifestAnchor`` 后重排板。
    解析失败时返回原文。
    """
    try:
        obj = extract_trace_payload_dict(raw)
    except (ValueError, json.JSONDecodeError):
        return raw
    q = obj.get("query")
    if not isinstance(q, dict):
        q = {}
    q = {**q, "manifestAnchor": dict(manifest_anchor)}
    obj["query"] = q
    return format_callchain_markdown(obj)


def format_callchain_markdown(payload: dict[str, Any]) -> str:
    """
    将 callchain-up 的 JSON 结构体转为 Markdown：查询、说明、统计、重点（边界入口）、各链展开、文末嵌入 JSON。
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

    ma = q.get("manifestAnchor")
    if isinstance(ma, dict) and ma:
        query_lines.append("")
        query_lines.append("### Manifest 锚点（tables-manifest 追溯）")
        query_lines.extend(manifest_anchor_markdown_lines(ma))

    entry_blocks = _markdown_up_entry_sections(chains)
    entry_md = "\n\n".join(entry_blocks) if entry_blocks else "_（无可用链）_"

    lines: list[str] = [
        "# Java 调用链（callchain-up）",
        "",
        "## 查询",
        "",
        *query_lines,
        "",
        "## 说明",
        "",
        "自起点沿 LSP **`callHierarchy/incomingCalls`** 向上追踪，直到 REST、消息监听、`@Scheduled`、`@Async`、abstract、无上游、检测到环或达到 `max_depth`（监听/调度/异步/REST 由注解与签名启发式识别）。",
        "",
        "## 统计",
        "",
        f"- **链数**: {count}",
        "- **方向**: 被调方法 → 调用者 → …",
        "",
        "## 重点（边界入口）",
        "",
        entry_md,
        "",
        "## 各链（展开）",
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
        gsl = ch.get("grepSourceLine", "")
        gtitle = f" · grep `{gsf}:{gsl}`" if gsf else ""
        lines.append(f"### 链 {idx + 1}")
        lines.append("")
        lines.append(f"**终止** `{sr}`（{label}）{gtitle}")
        lines.append("")
        lines.append("```")
        lines.append(_ascii_tree_for_chain([x for x in nodes if isinstance(x, dict)]))
        lines.append("```")
        lines.append("")
        top: dict[str, Any] = nodes[-1] if nodes and isinstance(nodes[-1], dict) else {}
        je = ch.get("jdtlsError")
        if isinstance(je, str) and je.strip() and sr == "jdtls_error":
            lines.append("**JDTLS 错误**（incomingCalls，链到此为止）：")
            lines.append("")
            lines.append(f"```\n{je.strip()[:900]}\n```")
            lines.append("")
        te = ch.get("topEntry")
        if isinstance(te, dict) and te:
            lm = te.get("listenerMarkers")
            sm = te.get("scheduledMarkers")
            az = te.get("asyncMarkers")
            has_rest = bool(
                _rest_endpoint_display(te)
                or te.get("restSummary")
                or te.get("restPath")
                or te.get("httpMethod")
                or te.get("classBasePath")
                or te.get("javadoc")
            )
            if isinstance(lm, list) and lm:
                lines.append(f"- **监听**: `{', '.join(str(x) for x in lm)}`")
                lines.append("")
            elif isinstance(sm, list) and sm:
                lines.append(f"- **调度**: `{', '.join(str(x) for x in sm)}`")
                lines.append("")
            elif isinstance(az, list) and az:
                lines.append(f"- **@Async 注解**: `{', '.join(str(x) for x in az)}`")
                lines.append("")
            elif has_rest or top.get("isRest"):
                ep = _rest_endpoint_display(te)
                if ep:
                    lines.append(f"- **REST**: `{ep}`")
                rs = te.get("restSummary")
                cb = te.get("classBasePath")
                if isinstance(cb, str) and cb.strip() and not (isinstance(rs, str) and rs.strip()):
                    lines.append(f"- **类级 base**: `{cb.strip()}`")
                jd = te.get("javadoc")
                if isinstance(jd, str) and jd.strip():
                    lines.append("- **JavaDoc**:")
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
    """向下调用子图 JSON → Markdown（查询、说明、统计、重点（下游终点）、ASCII 树等细则、文末嵌入 JSON）。"""
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

    start_key = payload.get("startKey")
    ascii_tree = _ascii_tree_for_downchain_graph(
        start_key if isinstance(start_key, str) else None,
        nodes,
        edges,
    )

    def _lines_for_bucket(kind: str, title: str, hint: str) -> list[str]:
        keys = buckets.get(kind, [])
        out: list[str] = [f"### {title}", ""]
        if hint.strip():
            out.extend([hint, ""])
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
        "# Java 向下调用子图（callchain-down）",
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
    if q.get("mergeBeanGetSet"):
        lines.append("- **mergeBeanGetSet**: `true`")

    ra = q.get("restMapAnchor")
    if isinstance(ra, dict) and ra:
        lines.append("")
        lines.append("### REST 映射锚点（rest-map）")
        lines.extend(rest_map_anchor_markdown_lines(ra))

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- **遍历**: 自起点沿 LSP **`outgoingCalls`** BFS；**ASCII 树**与 JSON 的 `startKey`、`nodes`、`edges` 一致。",
            "- **Markdown / JSON**: 简单 get/set/is 叶节点在正文里常按文件汇总；**完整节点仍以文末 JSON `nodes` 为准**。",
            "",
        ]
    )

    stat_bits: list[str] = [
        f"- **stopReason**: `{sr}` — {_DOWN_STOP_LABEL.get(sr, sr)}",
        (
            f"- **规模**: nodes={stats.get('nodeCount', len(nodes))} · edges={stats.get('edgeCount', len(edges))} · "
            f"expanded={stats.get('expandedCount', '')}"
        ),
        (
            f"- **上限**: maxDepth={stats.get('maxDepth')} · maxNodes={stats.get('maxNodes')} · "
            f"maxBranches={stats.get('maxBranches')}"
        ),
    ]
    impl_fb = int(stats.get("implementationFallbackEdges") or 0)
    if impl_fb > 0:
        stat_bits.append(
            f"- **implementation 兜底边**（`outgoingCalls` 为空时经 `textDocument/implementation`）: {impl_fb}"
        )
    if jerr:
        stat_bits.append(f"- **JDTLS 错误**: {len(jerr)} 条（见 JSON `jdtlsErrors`）")
    lines.extend(["", "## 统计", ""] + stat_bits)

    other_keys = [k for k in buckets.get("other", []) if k in leaf_keys]
    accessor_keys = [k for k in other_keys if isinstance(nodes.get(k), dict) and _is_simple_accessor_leaf(nodes[k])]
    accessor_set = set(accessor_keys)
    other_non_accessor = sorted([k for k in other_keys if k not in accessor_set])

    lines.extend(
        [
            "",
            "## 重点（下游终点）",
            "",
            "_按类名/包路径/方法名启发式归类（**非 AST**）；「（叶）」= 本子图中无出边。同一节点只出现在一个主分类。_",
            "",
        ]
    )
    lines.extend(_lines_for_bucket("database", "数据库", ""))
    lines.extend(_lines_for_bucket("middleware", "中间件", ""))
    lines.extend(_lines_for_bucket("external_api", "第三方 HTTP", ""))

    if accessor_keys:
        lines.extend(
            [
                "### 简单 accessor 叶节点（正文汇总）",
                "",
                f"_共 **{len(accessor_keys)}** 个；按源文件汇总；**完整键名见 JSON** `nodes`。_",
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
                "### 其他叶节点",
                "",
                "_非上述三类且非简单 get/set/is；子图中无出边。_",
                "",
            ]
        )
        cap_o = 40
        for j, nk in enumerate(other_non_accessor[:cap_o], start=1):
            n = nodes.get(nk) if isinstance(nodes.get(nk), dict) else {}
            if n:
                lines.append(_md_numbered_compact_only(j, n))
            else:
                lines.append(f"{j}. `{nk}`")
        if len(other_non_accessor) > cap_o:
            lines.append(f"{min(len(other_non_accessor), cap_o) + 1}. … 其余 **{len(other_non_accessor) - cap_o}** 条略（见 `nodes`）")
        lines.append("")

    lines.extend(
        [
            "## ASCII 树（展开）",
            "",
            "_沿出边向下；多分支用 ``├──`` / ``└──``。_",
            "",
            "```text",
            ascii_tree,
            "```",
            "",
        ]
    )

    merged_nodes_keys: list[str] = []
    for nk, n in nodes.items():
        if not isinstance(n, dict):
            continue
        mba = n.get("mergedBeanAccessors")
        if isinstance(mba, dict) and mba:
            merged_nodes_keys.append(nk)

    if merged_nodes_keys:
        lines.extend(
            [
                "## JavaBean（get/set/is 合并）",
                "",
                "_`--merge-bean-get-set`：折叠简单 accessor 叶节点，在调用者上标注 bean 的 set/get/is。_",
                "",
            ]
        )
        cap = 60
        merged_nodes_keys_sorted = sorted(
            merged_nodes_keys,
            key=lambda k: (
                str(nodes.get(k, {}).get("class", "")),
                str(nodes.get(k, {}).get("method", "")),
                str(nodes.get(k, {}).get("file", "")),
                int(nodes.get(k, {}).get("line", 0) or 0),
            ),
        )
        for i, nk in enumerate(merged_nodes_keys_sorted[:cap], start=1):
            n = nodes.get(nk) if isinstance(nodes.get(nk), dict) else {}
            disp = _short_node_line_compact(n)
            lines.append(f"{i}. {disp}")
            mba = n.get("mergedBeanAccessors")
            if not isinstance(mba, dict):
                continue
            for bean_cls in sorted(mba.keys(), key=lambda x: str(x)):
                b = mba.get(bean_cls) if isinstance(mba.get(bean_cls), dict) else {}
                setters = b.get("setters") if isinstance(b.get("setters"), list) else []
                getters = b.get("getters") if isinstance(b.get("getters"), list) else []
                isters = b.get("isters") if isinstance(b.get("isters"), list) else []
                if not setters and not getters and not isters:
                    continue
                parts: list[str] = []
                if setters:
                    s0 = ", ".join(str(x) for x in setters[:10])
                    if len(setters) > 10:
                        s0 += ", …"
                    parts.append(f"set=[{s0}]")
                if getters:
                    g0 = ", ".join(str(x) for x in getters[:10])
                    if len(getters) > 10:
                        g0 += ", …"
                    parts.append(f"get=[{g0}]")
                if isters:
                    i0 = ", ".join(str(x) for x in isters[:10])
                    if len(isters) > 10:
                        i0 += ", …"
                    parts.append(f"is=[{i0}]")
                lines.append(f"   {bean_cls}: " + ", ".join(parts))
            lines.append("")
        if len(merged_nodes_keys_sorted) > cap:
            lines.append(f"… 共 {len(merged_nodes_keys_sorted)} 个调用者节点，已截断显示前 {cap} 个。")

    km = payload.get("keyMethods")
    if isinstance(km, list) and km:
        lines.extend(
            [
                "## 关键业务候选（step6）",
                "",
                "_启发式评分；字段见 JSON `businessScore` / `businessCandidate` / `businessSignals`。_",
                "",
            ]
        )
        cap_km = 28
        for j, r in enumerate(km[:cap_km], start=1):
            if not isinstance(r, dict):
                continue
            c = str(r.get("class", "?"))
            m = str(r.get("method", ""))
            if len(m) > 96:
                m = m[:93] + "..."
            f = str(r.get("file", ""))
            ln = r.get("line", "")
            sc = r.get("score", "")
            sig = r.get("signals") if isinstance(r.get("signals"), list) else []
            sig_s = ", ".join(str(x) for x in sig[:8])
            lines.append(f"{j}. `{c}.{m}` `{f}:{ln}` — **score** `{sc}` — {sig_s}")
        if len(km) > cap_km:
            lines.append("")
            lines.append(f"… 共 **{len(km)}** 条，余下见 JSON ``keyMethods``。")
        lines.append("")

    lines.extend(
        [
            "## 边（from → to，前 200）",
            "",
        ]
    )
    for i, e in enumerate(edges[:200]):
        if not isinstance(e, dict):
            continue
        suf = "（实现类兜底）" if e.get("syntheticImplementation") else ""
        lines.append(f"{i + 1}. `{e.get('from', '')}` → `{e.get('to', '')}`{suf}")
    if len(edges) > 200:
        lines.append(f"… 共 {len(edges)} 条，仅显示前 200 条")
    lines.extend(["", "## 原始 JSON", "", "```json", json.dumps(payload, ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)


_TRACE_MD_JSON_SECTION = "## 原始 JSON"


def extract_trace_payload_dict(raw: str) -> dict[str, Any]:
    """
    解析 **纯 JSON** 的调用链报告，或 **Markdown** 报告末尾 ``## 原始 JSON`` 代码块内的嵌入 JSON
    （与 ``format_downchain_markdown`` / ``format_callchain_markdown`` 输出一致）。

    用于 design 默认落盘 Markdown 时，仍能从同一文件恢复结构化 ``payload``（摘要、``keyMethods`` 合并等）。
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("empty trace output")
    if s.startswith("错误:"):
        raise ValueError(s[:800])
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    pos = s.rfind(_TRACE_MD_JSON_SECTION)
    chunk = s[pos:] if pos >= 0 else s
    fence = chunk.find("```json")
    if fence < 0:
        raise ValueError("no embedded ```json block (expected section 原始 JSON)")
    nl = chunk.find("\n", fence)
    if nl < 0:
        raise ValueError("malformed json fence")
    body_start = nl + 1
    fence_end = chunk.find("```", body_start)
    if fence_end < 0:
        raise ValueError("unclosed ```json fence")
    js = chunk[body_start:fence_end].strip()
    obj2 = json.loads(js)
    if not isinstance(obj2, dict):
        raise ValueError("embedded payload is not a JSON object")
    return obj2


def summarize_trace_down_json(raw: str) -> dict[str, Any]:
    """
    从 **callchain-down** 的 JSON 文本 **或** 同内容的 Markdown（含嵌入 JSON）提取简短摘要；
    失败时含 ``error`` 键。
    """
    raw = raw.strip()
    if raw.startswith("错误:"):
        return {"error": raw[:800]}
    try:
        obj = extract_trace_payload_dict(raw)
    except (ValueError, json.JSONDecodeError) as e:
        return {"error": f"parse: {e}"}
    stats = obj.get("stats") if isinstance(obj.get("stats"), dict) else {}
    out_d: dict[str, Any] = {
        "nodeCount": stats.get("nodeCount"),
        "edgeCount": stats.get("edgeCount"),
        "implementationFallbackEdges": stats.get("implementationFallbackEdges"),
        "stopReason": obj.get("stopReason"),
        "jdtlsErrors": obj.get("jdtlsErrors") or [],
    }
    qd = obj.get("query") if isinstance(obj.get("query"), dict) else {}
    rma = qd.get("restMapAnchor") if isinstance(qd.get("restMapAnchor"), dict) else {}
    rid = rma.get("restHitId")
    if rid:
        out_d["restHitId"] = rid
    return out_d


def summarize_trace_up_json(raw: str) -> dict[str, Any]:
    """
    从 **callchain-up** 的 JSON 文本 **或** Markdown（含嵌入 JSON）提取简短摘要；失败时含 ``error`` 键。
    """
    raw = raw.strip()
    if raw.startswith("错误:"):
        return {"error": raw[:500]}
    try:
        obj = extract_trace_payload_dict(raw)
    except (ValueError, json.JSONDecodeError) as e:
        return {"error": f"parse: {e}"}
    chains = obj.get("chains")
    n = len(chains) if isinstance(chains, list) else 0
    tops: list[dict[str, Any]] = []
    if isinstance(chains, list):
        for c in chains[:12]:
            if not isinstance(c, dict):
                continue
            ch = c.get("chain")
            if isinstance(ch, list) and ch:
                top = ch[-1]
                if isinstance(top, dict):
                    tops.append(
                        {
                            "stopReason": c.get("stopReason"),
                            "class": top.get("class"),
                            "method": (top.get("method") or "")[:120],
                            "isRest": top.get("isRest"),
                        }
                    )
    out: dict[str, Any] = {
        "chainCount": obj.get("chainCount", n),
        "sampleTops": tops,
    }
    qsum = obj.get("query") if isinstance(obj.get("query"), dict) else {}
    mas = qsum.get("manifestAnchor") if isinstance(qsum.get("manifestAnchor"), dict) else {}
    hid = mas.get("manifestHitId")
    if hid:
        out["manifestHitId"] = hid
    return out


__all__ = [
    "apply_manifest_anchor_to_callchain_markdown",
    "apply_rest_map_anchor_to_downchain_markdown",
    "extract_trace_payload_dict",
    "format_callchain_markdown",
    "format_downchain_markdown",
    "manifest_anchor_markdown_lines",
    "rest_map_anchor_markdown_lines",
    "summarize_trace_down_json",
    "summarize_trace_up_json",
]
