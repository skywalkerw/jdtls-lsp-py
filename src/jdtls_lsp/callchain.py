"""Trace incoming Java call chains by class + method until REST endpoint or abstract class."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from jdtls_lsp.client import LSPClient, create_client
from jdtls_lsp.java_grep import (
    grep_java_keyword_hits,
    keyword_search_variants,
    scan_method_line_candidates,
    sort_grep_hits_by_score,
)
from jdtls_lsp.logutil import format_payload

_log = logging.getLogger("jdtls_lsp.callchain")

WORKSPACE_SYMBOL_WARMUP_S = 8.0
MAX_DEPTH_DEFAULT = 20

REST_ANNOTATIONS = (
    "@RequestMapping",
    "@GetMapping",
    "@PostMapping",
    "@PutMapping",
    "@DeleteMapping",
    "@PatchMapping",
)


def _norm_list(result: Any) -> list[Any]:
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return [result]


def _workspace_symbol_with_retry(client: LSPClient, query: str, limit: int = 50) -> list[dict[str, Any]]:
    def search() -> list[dict[str, Any]]:
        try:
            r = client.request("workspace/symbol", {"query": query})
            arr = _norm_list(r)
            return [x for x in arr if isinstance(x, dict)][:limit]
        except Exception:
            return []

    out = search()
    if not out and query.strip():
        time.sleep(WORKSPACE_SYMBOL_WARMUP_S)
        out = search()
    return out


def _uri_to_path(uri: str) -> Path | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path)).resolve()


def _symbol_uri(sym: dict[str, Any]) -> str | None:
    loc = sym.get("location")
    if isinstance(loc, dict):
        uri = loc.get("uri")
        if isinstance(uri, str):
            return uri
    return None


def _symbol_start(sym: dict[str, Any]) -> tuple[int, int]:
    loc = sym.get("location")
    if not isinstance(loc, dict):
        return (0, 0)
    rng = loc.get("range")
    if not isinstance(rng, dict):
        return (0, 0)
    start = rng.get("start")
    if not isinstance(start, dict):
        return (0, 0)
    line = int(start.get("line", 0))
    char = int(start.get("character", 0))
    return (line, char)


def _extract_java_class_name(raw: str) -> str | None:
    m = re.search(r"\b(?:class|interface|enum)\s+([A-Za-z_]\w*)", raw)
    return m.group(1) if m else None


def _find_target_class_symbol(
    client: LSPClient,
    root: Path,
    class_name: str,
) -> dict[str, Any] | None:
    q = class_name.strip()
    if not q:
        return None
    syms = _workspace_symbol_with_retry(client, q, limit=80)
    exact_name = q.split(".")[-1]
    preferred: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    for sym in syms:
        name = str(sym.get("name", ""))
        kind = int(sym.get("kind", 0))
        if kind not in (5, 11):  # class / interface
            continue
        uri = _symbol_uri(sym)
        path = _uri_to_path(uri or "")
        if path is None:
            continue
        if not str(path).endswith(f"{exact_name}.java"):
            fallback.append(sym)
            continue
        if name == exact_name or name.endswith(f".{exact_name}"):
            preferred.append(sym)
        else:
            fallback.append(sym)

    cand = preferred[0] if preferred else (fallback[0] if fallback else None)
    if cand is None:
        return None

    uri = _symbol_uri(cand)
    if uri:
        p = _uri_to_path(uri)
        if p and p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                parsed_name = _extract_java_class_name(text)
                if parsed_name and parsed_name != exact_name and "." not in class_name:
                    return None
            except Exception:
                pass
    return cand


def _find_method_symbol_by_name(
    symbols: list[dict[str, Any]],
    class_name: str,
    method_name: str,
) -> dict[str, Any] | None:
    class_name = class_name.strip().split(".")[-1]

    def walk(arr: list[dict[str, Any]], in_target_class: bool) -> dict[str, Any] | None:
        for s in arr:
            if not isinstance(s, dict):
                continue
            kind = int(s.get("kind", 0))
            name = str(s.get("name", ""))
            children = [x for x in _norm_list(s.get("children")) if isinstance(x, dict)]
            now_in_class = in_target_class
            if kind in (5, 11):
                simple = name.split(".")[-1]
                now_in_class = simple == class_name
            else:
                # Flat SymbolInformation: methods carry containerName instead of nesting under a class node
                container = str(s.get("containerName", "")).strip()
                if container and (
                    container == class_name or container.endswith(f".{class_name}")
                ):
                    now_in_class = True
            if kind == 6 and now_in_class:
                if name == method_name or name.startswith(f"{method_name}("):
                    return s
            hit = walk(children, now_in_class)
            if hit is not None:
                return hit
        return None

    return walk(symbols, False)


def _prepare_item(client: LSPClient, uri: str, line0: int, char0: int) -> dict[str, Any] | None:
    items = client.request(
        "textDocument/prepareCallHierarchy",
        {"textDocument": {"uri": uri}, "position": {"line": line0, "character": char0}},
    )
    arr = [x for x in _norm_list(items) if isinstance(x, dict)]
    return arr[0] if arr else None


def _position_in_range(rng: dict[str, Any], line0: int, char0: int) -> bool:
    st = rng.get("start")
    en = rng.get("end")
    if not isinstance(st, dict) or not isinstance(en, dict):
        return False
    sl, sc = int(st.get("line", 0)), int(st.get("character", 0))
    el, ec = int(en.get("line", 0)), int(en.get("character", 0))
    if line0 < sl or line0 > el:
        return False
    if line0 == sl and char0 < sc:
        return False
    if line0 == el and char0 > ec:
        return False
    return True


def _range_size(rng: dict[str, Any]) -> int:
    st = rng.get("start") or {}
    en = rng.get("end") or {}
    sl, sc = int(st.get("line", 0)), int(st.get("character", 0))
    el, ec = int(en.get("line", 0)), int(en.get("character", 0))
    return (el - sl) * 100000 + (ec - sc)


def _find_methods_containing_position(
    symbols: list[dict[str, Any]], line0: int, char0: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(arr: list[dict[str, Any]]) -> None:
        for s in arr:
            if not isinstance(s, dict):
                continue
            kind = int(s.get("kind", 0))
            children = [x for x in _norm_list(s.get("children")) if isinstance(x, dict)]
            if kind == 6:
                loc = s.get("location")
                if isinstance(loc, dict):
                    rng = loc.get("range")
                    if isinstance(rng, dict) and _position_in_range(rng, line0, char0):
                        out.append(s)
            walk(children)

    walk(symbols)
    return out


def _symbol_start_for_hierarchy(sym: dict[str, Any]) -> tuple[int, int]:
    sel = sym.get("selectionRange")
    if isinstance(sel, dict):
        st = sel.get("start")
        if isinstance(st, dict):
            return int(st.get("line", 0)), int(st.get("character", 0))
    loc = sym.get("location") or {}
    rng = loc.get("range") or {}
    st = rng.get("start") or {}
    return int(st.get("line", 0)), int(st.get("character", 0))


def _resolve_call_hierarchy_item_from_file_line(
    client: LSPClient, uri: str, line0: int, char0: int
) -> dict[str, Any] | None:
    item = _prepare_item(client, uri, line0, char0)
    if item is not None:
        return item
    item = _prepare_item(client, uri, line0, 0)
    if item is not None:
        return item
    ds = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    symbols = [x for x in _norm_list(ds) if isinstance(x, dict)]
    methods = _find_methods_containing_position(symbols, line0, char0)
    if not methods:
        return None
    rng_of = lambda s: (s.get("location") or {}).get("range") or {}
    methods.sort(key=lambda s: _range_size(rng_of(s)) if isinstance(rng_of(s), dict) else 10**9)
    best = methods[0]
    l0, c0 = _symbol_start_for_hierarchy(best)
    return _prepare_item(client, uri, l0, c0)


def _hierarchy_item_position_1based(item: dict[str, Any]) -> tuple[int, int]:
    sel = item.get("selectionRange")
    if isinstance(sel, dict):
        st = sel.get("start")
        if isinstance(st, dict):
            return int(st.get("line", 0)) + 1, int(st.get("character", 0)) + 1
    return 1, 1


def _names_from_hierarchy_item(item: dict[str, Any]) -> tuple[str, str]:
    detail = str(item.get("detail", "")).strip()
    name = str(item.get("name", ""))
    class_name = detail.split(".")[-1] if detail else ""
    if not class_name:
        class_name = "?"
    method_simple = name.split("(")[0].strip() if "(" in name else name.strip()
    return class_name, method_simple


def _java_file_declares_interface_matching_stem(path: Path) -> bool:
    """若编译单元顶层声明 ``interface <文件名>``（与 ``.java`` 主类名一致），视为接口文件。"""
    stem = path.stem
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(rf"\binterface\s+{re.escape(stem)}\b", text))


def _grep_entry_tier(em: dict[str, Any]) -> int:
    """越小越优先：实现类 > Controller > 其它。"""
    f = str(em.get("file", ""))
    if "ServiceImpl" in f or "Impl.java" in f or "/impl/" in f.replace("\\", "/").lower():
        return 0
    if "Controller" in f:
        return 2
    if "Repository" in f:
        return 3
    return 4


def _apply_grep_entry_filters(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    root: Path,
    *,
    skip_interface: bool,
    skip_rest: bool,
    max_entry_points: int | None,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    """过滤 grep 起点；排序后截断 ``max_entry_points``。"""
    extra: dict[str, Any] = {}
    out = list(pairs)
    if skip_interface:
        before = len(out)
        kept: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for em, item in out:
            rel = str(em.get("file", ""))
            abs_p = Path(rel).resolve() if Path(rel).is_absolute() else (root / rel).resolve()
            if _java_file_declares_interface_matching_stem(abs_p):
                continue
            kept.append((em, item))
        out = kept
        extra["grepSkippedInterfaceEntries"] = before - len(out)
    if skip_rest:
        before = len(out)
        kept = []
        for em, item in out:
            node = _node_from_item(item, root)
            if node.get("isRest"):
                continue
            kept.append((em, item))
        out = kept
        extra["grepSkippedRestEntrypoints"] = before - len(out)
    out.sort(key=lambda p: (_grep_entry_tier(p[0]), str(p[0].get("file", ""))))
    if max_entry_points is not None and max_entry_points > 0 and len(out) > max_entry_points:
        extra["grepEntryPointsBeforeCap"] = len(out)
        out = out[:max_entry_points]
        extra["grepMaxEntryPoints"] = max_entry_points
    return out, extra


def _collect_java_grep_entries(
    client: LSPClient,
    root: Path,
    query: str,
    *,
    multi_needle: bool = False,
    grep_skip_interface: bool = False,
    grep_skip_rest: bool = False,
    grep_max_entry_points: int | None = None,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    """
    在 *.java 中全文搜索 needles，对每个 grep 命中尝试解析 call hierarchy 起点。

    - **单关键字**（默认）：每个**文件**至多一个起点（先命中先得分）。
    - **多关键字**（``multi_needle=True``）：跳过「每文件只取一个」，按解析后的
      ``(file, methodLine)`` 去重，同一文件内多个命中可产生多个起点；处理更多 grep 行。
    """
    needles = keyword_search_variants(query)
    if not needles:
        return [], {}
    hits = grep_java_keyword_hits(root, needles)
    if not hits:
        return [], {}
    sort_grep_hits_by_score(hits)
    common: dict[str, Any] = {
        "keywordResolution": "java_text_grep",
        "grepNeedles": needles,
    }
    if multi_needle:
        common["grepMultiNeedle"] = True
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen_files: set[str] = set()
    seen_method_loc: set[tuple[str, int]] = set()
    max_hits = 200 if multi_needle else 40

    for abs_path, grep_line_no, line_text in hits[:max_hits]:
        if not abs_path.is_file():
            continue
        try:
            rel = str(abs_path.relative_to(root))
        except ValueError:
            rel = str(abs_path)
        if not multi_needle and rel in seen_files:
            continue
        try:
            file_lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        client.open_file(str(abs_path))
        uri = abs_path.as_uri()
        candidates = scan_method_line_candidates(grep_line_no, file_lines)
        for attempts, ln in enumerate(candidates):
            if attempts > 50:
                break
            item = _resolve_call_hierarchy_item_from_file_line(client, uri, max(0, ln - 1), 0)
            if item is None:
                continue
            line1, char1 = _hierarchy_item_position_1based(item)
            loc_key = (rel, line1)
            if multi_needle:
                if loc_key in seen_method_loc:
                    break
                seen_method_loc.add(loc_key)
            cname, mname = _names_from_hierarchy_item(item)
            em: dict[str, Any] = {
                "file": rel,
                "line": line1,
                "character": char1,
                "className": cname,
                "methodName": mname,
                "grepHitLine": grep_line_no,
                "matchedLinePreview": line_text.strip()[:240],
            }
            if ln != grep_line_no:
                em["lineAdjustFromGrepHit"] = ln - grep_line_no
            pairs.append((em, item))
            if not multi_needle:
                seen_files.add(rel)
            _log.info("keyword grep resolved %s:%s (grep hit line %s)", rel, line1, grep_line_no)
            break

    if grep_skip_interface or grep_skip_rest or (
        grep_max_entry_points is not None and grep_max_entry_points > 0
    ):
        pairs, filt_extra = _apply_grep_entry_filters(
            pairs,
            root,
            skip_interface=grep_skip_interface,
            skip_rest=grep_skip_rest,
            max_entry_points=grep_max_entry_points,
        )
        common.update(filt_extra)
        common["grepEntryFilters"] = {
            "skipInterfaceFiles": grep_skip_interface,
            "skipRestEntrypoints": grep_skip_rest,
            "maxEntryPoints": grep_max_entry_points,
        }

    return pairs, common


def _effective_grep_workers(n_entries: int, explicit: int | None) -> int:
    if explicit is not None and explicit > 0:
        return min(explicit, n_entries)
    raw = os.environ.get("JDTLS_LSP_GREP_WORKERS", "").strip()
    if raw:
        try:
            w = int(raw)
            if w > 0:
                return min(w, n_entries)
        except ValueError:
            pass
    return min(8, max(1, n_entries))


def _trace_java_grep_entries_parallel(
    client: LSPClient,
    root: Path,
    entries: list[dict[str, Any]],
    max_depth: int,
    max_workers: int,
) -> list[dict[str, Any]]:
    """
    多入口依次向上追踪；**共用**已 ``initialize`` 的 ``client``。

    同一 JDTLS 连接上 **并发** ``callHierarchy/incomingCalls`` 易触发服务端 NPE/内部错误，
    故在单连接模式下 **串行** 处理各入口（``max_workers`` 保留兼容，不参与调度）。
    """
    _ = max_workers  # API 兼容；单 LSP 时串行追踪，不使用 worker 数
    merged: list[dict[str, Any]] = []
    for entry in entries:
        try:
            rel = str(entry["file"])
            abs_path = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
            if not abs_path.is_file():
                _log.warning("grep multi-entry: missing file %s", abs_path)
                continue
            client.open_file(str(abs_path))
            uri = abs_path.as_uri()
            ln = int(entry["line"])
            ch = int(entry.get("character", 1))
            item = _resolve_call_hierarchy_item_from_file_line(
                client, uri, max(0, ln - 1), max(0, ch - 1)
            )
            if item is None:
                _log.warning("grep multi-entry: no call hierarchy at %s:%s", rel, ln)
                continue
            chains: list[dict[str, Any]] = []
            _trace_up_all(
                client=client,
                root=root,
                item=item,
                current_chain=[],
                out_chains=chains,
                seen=set(),
                depth=0,
                max_depth=max(1, int(max_depth)),
            )
            for chn in chains:
                chn["grepSourceFile"] = rel
                chn["grepSourceLine"] = ln
                chn["grepEntryClass"] = entry.get("className")
                chn["grepEntryMethod"] = entry.get("methodName")
            merged.extend(chains)
        except Exception:
            _log.exception("grep multi-entry trace failed for %s", entry.get("file"))
    return merged


def _merge_workspace_symbols(client: LSPClient, variants: list[str], limit: int) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    merged: list[dict[str, Any]] = []
    for v in variants:
        for s in _workspace_symbol_with_retry(client, v, limit=limit):
            if not isinstance(s, dict):
                continue
            uri = _symbol_uri(s) or ""
            loc = s.get("location") or {}
            rng = loc.get("range") or {} if isinstance(loc, dict) else {}
            st = rng.get("start") or {} if isinstance(rng, dict) else {}
            key = (uri, str(s.get("name")), int(s.get("kind", 0)), int(st.get("line", -1)))
            if key in seen:
                continue
            seen.add(key)
            merged.append(s)
    return merged


def _collect_methods_flat(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(arr: list[dict[str, Any]]) -> None:
        for s in arr:
            if not isinstance(s, dict):
                continue
            if int(s.get("kind", 0)) == 6:
                out.append(s)
            walk([x for x in _norm_list(s.get("children")) if isinstance(x, dict)])

    walk(symbols)
    return out


def _hierarchy_from_first_method_in_class_file(client: LSPClient, class_sym: dict[str, Any]) -> dict[str, Any] | None:
    """仅匹配到类/接口时，打开文件取第一个方法作为 call hierarchy 起点。"""
    uri = _symbol_uri(class_sym)
    if not uri:
        return None
    pth = _uri_to_path(uri)
    if pth and pth.exists():
        client.open_file(str(pth))
    ds = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    symbols = [x for x in _norm_list(ds) if isinstance(x, dict)]
    methods = _collect_methods_flat(symbols)
    if not methods:
        return None
    best = methods[0]
    l0, c0 = _symbol_start_for_hierarchy(best)
    item = _prepare_item(client, uri, l0, c0)
    if item is not None:
        return item
    for off in (0, 1, 3):
        item = _prepare_item(client, uri, l0, c0 + off)
        if item is not None:
            return item
    return None


def _resolve_item_from_keyword(
    client: LSPClient,
    query: str,
    root: Path,
    *,
    grep_skip_interface: bool = False,
    grep_skip_rest: bool = False,
    grep_max_entry_points: int | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """
    解析关键字 → call hierarchy item。
    顺序（**单关键字**）：workspace/symbol（含变体）→ 类符号回退取首方法 → 项目内 *.java 全文搜索。

    **多关键字**（``|`` / ``｜`` 拆分后多于一段）：不走 workspace/类回退，直接全文 grep，
    合并各 needle 命中并按「每文件一条」或「多 needle 时每方法一条」收集起点。

    **grep_***：仅影响 ``java_text_grep`` 收集的起点（见 ``_collect_java_grep_entries``）。
    """
    q = query.strip()
    if not q:
        return None, {}
    variants = keyword_search_variants(q)
    multi_needle = len(variants) > 1

    _grep_kw = dict(
        grep_skip_interface=grep_skip_interface,
        grep_skip_rest=grep_skip_rest,
        grep_max_entry_points=grep_max_entry_points,
    )

    if multi_needle:
        pairs, grep_common = _collect_java_grep_entries(client, root, q, multi_needle=True, **_grep_kw)
        if not pairs:
            return None, {}
        if len(pairs) == 1:
            em, item = pairs[0]
            return item, {**grep_common, **em}
        entries_meta = [p[0] for p in pairs]
        return None, {**grep_common, "javaGrepMultiFile": True, "javaGrepEntries": entries_meta}

    syms = _merge_workspace_symbols(client, variants, limit=200)
    methods = [s for s in syms if isinstance(s, dict) and int(s.get("kind", 0)) == 6]
    if not methods:
        methods = [
            s
            for s in syms
            if isinstance(s, dict)
            and int(s.get("kind", 0)) == 6
            and any(v.lower() in str(s.get("name", "")).lower() for v in variants)
        ]

    def sort_key(s: dict[str, Any]) -> tuple[int, int]:
        name = str(s.get("name", ""))
        simple = name.split("(")[0].strip()
        for v in variants:
            if simple == v:
                return (0, 0)
        for v in variants:
            if name.startswith(f"{v}("):
                return (1, 0)
        for v in variants:
            if v.lower() in simple.lower():
                return (2, len(simple))
        return (3, len(simple))

    methods.sort(key=sort_key)
    for s in methods:
        uri = _symbol_uri(s)
        if not uri:
            continue
        pth = _uri_to_path(uri)
        if pth and pth.exists():
            client.open_file(str(pth))
        line0, char0 = _symbol_start(s)
        item = _prepare_item(client, uri, line0, char0)
        if item is not None:
            return item, {"keywordResolution": "workspace_symbol"}
        for off in (1, 3, 8):
            item = _prepare_item(client, uri, line0, char0 + off)
            if item is not None:
                return item, {"keywordResolution": "workspace_symbol"}

    # 仅有类/接口：用任一关键字子串匹配符号名，再取该类第一个方法
    classes = [
        s
        for s in syms
        if isinstance(s, dict)
        and int(s.get("kind", 0)) in (5, 11)
        and any(v.lower() in str(s.get("name", "")).lower() for v in variants)
    ]

    def _class_fallback_key(s: dict[str, Any]) -> tuple[int, int]:
        name = str(s.get("name", ""))
        if "Controller" in name or "ServiceImpl" in name:
            tier = 0
        elif "Service" in name or "Repository" in name:
            tier = 1
        elif name in ("MonitorData",) or name.endswith("DTO"):
            tier = 3
        else:
            tier = 2
        return (tier, -len(name))

    classes.sort(key=_class_fallback_key)
    for cs in classes:
        item = _hierarchy_from_first_method_in_class_file(client, cs)
        if item is not None:
            return item, {"keywordResolution": "workspace_class_first_method"}

    pairs, grep_common = _collect_java_grep_entries(client, root, q, multi_needle=False, **_grep_kw)
    if not pairs:
        return None, {}
    if len(pairs) == 1:
        em, item = pairs[0]
        return item, {**grep_common, **em}
    entries_meta = [p[0] for p in pairs]
    return None, {**grep_common, "javaGrepMultiFile": True, "javaGrepEntries": entries_meta}


def _is_rest_endpoint(file_path: Path, line0: int) -> bool:
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False
    start = max(0, line0 - 8)
    end = min(len(lines), line0 + 3)
    near = "\n".join(lines[start:end])
    has_mapping = any(x in near for x in REST_ANNOTATIONS)
    file_has_controller = "@RestController" in "\n".join(lines) or "@Controller" in "\n".join(lines)
    return has_mapping or (file_has_controller and has_mapping)


def _is_abstract_class(file_path: Path, line0: int) -> bool:
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False
    start = max(0, line0 - 120)
    up = lines[start : line0 + 1]
    for i in range(len(up) - 1, -1, -1):
        s = up[i].strip()
        if " class " in f" {s} " and "abstract class" in s:
            return True
    return False


def _node_key(node: dict[str, Any]) -> str:
    return f"{node.get('file')}:{node.get('line')}:{node.get('character')}:{node.get('method')}"


def _item_selection_key(item: dict[str, Any]) -> tuple[str, int, int]:
    uri = str(item.get("uri", ""))
    sel = item.get("selectionRange") or {}
    st = sel.get("start") if isinstance(sel, dict) else {}
    if not isinstance(st, dict):
        return (uri, -1, -1)
    return (uri, int(st.get("line", 0)), int(st.get("character", 0)))


def _refresh_hierarchy_item(client: LSPClient, item: dict[str, Any]) -> dict[str, Any] | None:
    """Re-run prepareCallHierarchy at the item's selection start (stabilizes LSP item for incomingCalls)."""
    uri = item.get("uri")
    if not isinstance(uri, str):
        return None
    sel = item.get("selectionRange")
    if not isinstance(sel, dict):
        return None
    st = sel.get("start")
    if not isinstance(st, dict):
        return None
    line0 = int(st.get("line", 0))
    char0 = int(st.get("character", 0))
    return _prepare_item(client, uri, line0, char0)


def _incoming_calls_candidates(client: LSPClient, item: dict[str, Any]) -> list[dict[str, Any]]:
    """
    JDTLS sometimes NPEs on callHierarchy/incomingCalls for the raw ``from`` item.
    Try the item as-is, a fresh prepareCallHierarchy at the same anchor, then small column nudges.
    """
    uri = item.get("uri")
    if not isinstance(uri, str):
        return [item]

    seen: set[tuple[str, int, int]] = set()
    out: list[dict[str, Any]] = []

    def add(it: dict[str, Any]) -> None:
        k = _item_selection_key(it)
        if k in seen:
            return
        seen.add(k)
        out.append(it)

    add(item)
    ref = _refresh_hierarchy_item(client, item)
    if ref is not None:
        add(ref)

    sel = item.get("selectionRange")
    if isinstance(sel, dict):
        st = sel.get("start")
        if isinstance(st, dict):
            line0 = int(st.get("line", 0))
            char0 = int(st.get("character", 0))
            for delta in (0, 1, -1, 2, -2, 3, -3, 4, 5):
                prep = _prepare_item(client, uri, line0, max(0, char0 + delta))
                if prep is not None:
                    add(prep)

    return out if out else [item]


def _incoming_calls_with_retry(client: LSPClient, item: dict[str, Any]) -> Any:
    last_err: RuntimeError | None = None
    for cand in _incoming_calls_candidates(client, item):
        try:
            return client.request("callHierarchy/incomingCalls", {"item": cand})
        except RuntimeError as e:
            last_err = e
            _log.debug("incomingCalls retry: %s", e)
    if last_err is not None:
        raise last_err
    raise RuntimeError("incomingCalls: no candidates")


def _node_from_item(item: dict[str, Any], root: Path, default_class: str | None = None) -> dict[str, Any]:
    uri = str(item.get("uri", ""))
    path = _uri_to_path(uri)
    rel = str(path.relative_to(root)) if path and path.is_relative_to(root) else (str(path) if path else uri)
    sel = item.get("selectionRange", {})
    if not isinstance(sel, dict):
        sel = {}
    st = sel.get("start", {})
    if not isinstance(st, dict):
        st = {}
    line0 = int(st.get("line", 0))
    char0 = int(st.get("character", 0))
    name = str(item.get("name", ""))
    detail = str(item.get("detail", "")).strip()
    class_name = default_class or (detail.split(".")[-1] if detail else "")
    if not class_name and path:
        class_name = path.stem
    node = {
        "class": class_name,
        "method": name,
        "file": rel,
        "line": line0 + 1,
        "character": char0 + 1,
        "uri": uri,
    }
    if path and path.exists():
        node["isRest"] = _is_rest_endpoint(path, line0)
        node["isAbstractClass"] = _is_abstract_class(path, line0)
    else:
        node["isRest"] = False
        node["isAbstractClass"] = False
    return node


def _trace_up_all(
    client: LSPClient,
    root: Path,
    item: dict[str, Any],
    current_chain: list[dict[str, Any]],
    out_chains: list[dict[str, Any]],
    seen: set[str],
    depth: int,
    max_depth: int,
) -> None:
    cur_node = _node_from_item(item, root)
    key = _node_key(cur_node)
    if key in seen:
        out_chains.append({"chain": current_chain + [cur_node], "stopReason": "cycle"})
        return
    chain = current_chain + [cur_node]
    if cur_node.get("isRest"):
        out_chains.append({"chain": chain, "stopReason": "rest_endpoint"})
        return
    if cur_node.get("isAbstractClass"):
        out_chains.append({"chain": chain, "stopReason": "abstract_class"})
        return
    if depth >= max_depth:
        out_chains.append({"chain": chain, "stopReason": "max_depth"})
        return

    try:
        calls = _incoming_calls_with_retry(client, item)
    except RuntimeError as e:
        _log.warning("callHierarchy/incomingCalls failed at %s: %s", key, e)
        out_chains.append(
            {
                "chain": chain,
                "stopReason": "jdtls_error",
                "jdtlsError": str(e)[:1200],
            }
        )
        return
    arr = [x for x in _norm_list(calls) if isinstance(x, dict)]
    if not arr:
        out_chains.append({"chain": chain, "stopReason": "no_incoming"})
        return

    next_seen = set(seen)
    next_seen.add(key)
    progressed = False
    for inc in arr:
        frm = inc.get("from")
        if not isinstance(frm, dict):
            continue
        progressed = True
        _trace_up_all(
            client=client,
            root=root,
            item=frm,
            current_chain=chain,
            out_chains=out_chains,
            seen=next_seen,
            depth=depth + 1,
            max_depth=max_depth,
        )
    if not progressed:
        out_chains.append({"chain": chain, "stopReason": "no_incoming"})


_STOP_REASON_LABEL: dict[str, str] = {
    "rest_endpoint": "REST 接口（检测到 @RequestMapping 等）",
    "abstract_class": "abstract 类",
    "max_depth": "达到 max_depth",
    "no_incoming": "无上游调用（LSP incomingCalls 为空）",
    "cycle": "检测到调用环",
    "jdtls_error": "JDTLS incomingCalls 内部错误（常见为 NPE，链到此为止）",
}


def _short_method(name: str, max_len: int = 72) -> str:
    s = str(name).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


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
        f"{_short_class_name(str(n0.get('class', '?')))}.{_short_method(str(n0.get('method', '')))}  "
        f"# {_short_file_loc(str(n0.get('file', '')), n0.get('line'))}"
    )
    for i in range(1, len(nodes)):
        n = nodes[i]
        pad = "    " * i
        lines.append(
            f"{pad}└── {_short_class_name(str(n.get('class', '?')))}.{_short_method(str(n.get('method', '')))}  "
            f"# {_short_file_loc(str(n.get('file', '')), n.get('line'))}"
        )
    return "\n".join(lines)


def _find_method_signature_line(lines: list[str], line0: int) -> int:
    """从 LSP 给出的行号向上找到包含 `public ...(` 的方法签名行（0-based）。"""
    i = min(max(0, line0), len(lines) - 1)
    while i >= 0:
        s = lines[i].strip()
        if not s:
            i -= 1
            continue
        if s.startswith("@") or s.startswith("//") or s.startswith("*"):
            i -= 1
            continue
        if re.match(r"^(public|private|protected|static)", s) and "(" in s:
            return i
        i -= 1
    return min(max(0, line0), len(lines) - 1)


def _collect_method_annotations(lines: list[str], sig_line: int) -> list[str]:
    out: list[str] = []
    i = sig_line - 1
    while i >= 0:
        s = lines[i].strip()
        if s.startswith("@"):
            out.insert(0, lines[i])
            i -= 1
            continue
        if not s:
            i -= 1
            continue
        break
    return out


def _extract_class_base_path(lines: list[str]) -> str:
    """类上 @RequestMapping 的 path（遇 `public class` 即停）。"""
    base = ""
    for line in lines:
        if re.search(r"^\s*public\s+class\s+", line):
            break
        m = re.search(r"@RequestMapping\s*\(\s*[\"']([^\"']+)[\"']", line)
        if m:
            base = m.group(1)
        m = re.search(r"@RequestMapping\s*\(\s*value\s*=\s*[\"']([^\"']+)[\"']", line)
        if m:
            base = m.group(1)
        m = re.search(r"@RequestMapping\s*\(\s*path\s*=\s*[\"']([^\"']+)[\"']", line)
        if m:
            base = m.group(1)
    return base.strip()


def _first_path_in_annotation(s: str) -> str | None:
    m = re.search(r"\(\s*[\"']([^\"']+)[\"']", s)
    if m:
        return m.group(1)
    m = re.search(r"value\s*=\s*[\"']([^\"']+)[\"']", s)
    if m:
        return m.group(1)
    m = re.search(r"path\s*=\s*[\"']([^\"']+)[\"']", s)
    if m:
        return m.group(1)
    if re.search(r"\(\s*\)\s*$", s.strip()):
        return ""
    return None


def _request_method_from_request_mapping(s: str) -> str | None:
    m = re.search(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH)", s)
    if m:
        return m.group(1)
    return None


def _parse_spring_mapping(anns: list[str]) -> tuple[str | None, str]:
    http: str | None = None
    path = ""
    for raw in anns:
        s = raw.strip()
        if "@GetMapping" in s:
            http = "GET"
            path = _first_path_in_annotation(s) or ""
            break
        if "@PostMapping" in s:
            http = "POST"
            path = _first_path_in_annotation(s) or ""
            break
        if "@PutMapping" in s:
            http = "PUT"
            path = _first_path_in_annotation(s) or ""
            break
        if "@DeleteMapping" in s:
            http = "DELETE"
            path = _first_path_in_annotation(s) or ""
            break
        if "@PatchMapping" in s:
            http = "PATCH"
            path = _first_path_in_annotation(s) or ""
            break
        if "@RequestMapping" in s:
            http = _request_method_from_request_mapping(s) or "GET"
            path = _first_path_in_annotation(s) or ""
            break
    return http, path


def _join_rest_paths(base: str, sub: str) -> str:
    base = base.strip().rstrip("/")
    sub = sub.strip()
    if not sub:
        return base if base else ""
    if sub.startswith("/"):
        sub = sub[1:]
    if not base:
        return "/" + sub
    return base + "/" + sub


def _extract_javadoc_before_signature(lines: list[str], sig_line: int) -> str:
    i = sig_line - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i < 0:
        return ""
    while i >= 0 and lines[i].strip().startswith("@"):
        i -= 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i < 0:
        return ""
    if "*/" not in lines[i]:
        return ""
    j = i
    buf: list[str] = []
    while j >= 0:
        buf.insert(0, lines[j])
        if "/**" in lines[j]:
            break
        j -= 1
    raw = "\n".join(buf)
    raw = re.sub(r"/\*\*", "", raw, count=1)
    raw = re.sub(r"\*/\s*$", "", raw.strip())
    body_lines: list[str] = []
    for ln in raw.splitlines():
        ln = re.sub(r"^\s*\*?\s?", "", ln)
        body_lines.append(ln.strip())
    return "\n".join(x for x in body_lines if x).strip()


def extract_top_entry_info(root: Path, node: dict[str, Any]) -> dict[str, Any]:
    """
    解析链最上层节点对应源码：类级 @RequestMapping + 方法级映射、JavaDoc。
    用于 stopReason 为 no_incoming / rest_endpoint 时的入口说明。
    """
    rel = node.get("file")
    if not isinstance(rel, str) or not rel.strip():
        return {}
    fp = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    if not fp.exists():
        return {}
    try:
        lines = fp.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    line0 = int(node.get("line", 1)) - 1
    line0 = max(0, min(line0, len(lines) - 1))
    sig = _find_method_signature_line(lines, line0)
    anns = _collect_method_annotations(lines, sig)
    class_base = _extract_class_base_path(lines)
    http, sub_path = _parse_spring_mapping(anns)
    full_path = _join_rest_paths(class_base, sub_path or "")
    if full_path and not full_path.startswith("/"):
        full_path = "/" + full_path
    javadoc = _extract_javadoc_before_signature(lines, sig)
    out: dict[str, Any] = {}
    if class_base:
        out["classBasePath"] = class_base if class_base.startswith("/") else "/" + class_base.lstrip("/")
    if http:
        out["httpMethod"] = http
    if full_path:
        out["restPath"] = full_path
    if http and full_path:
        out["restSummary"] = f"{http} {full_path}"
    elif full_path:
        out["restSummary"] = full_path
    if javadoc:
        out["javadoc"] = javadoc
    return out


def _enrich_chains_with_top_entry(root: Path, chains: list[dict[str, Any]]) -> None:
    """在已达最上层（无上游或 REST 终止）时，为最上层节点补充 REST 路径与 JavaDoc。"""
    for ch in chains:
        if not isinstance(ch, dict):
            continue
        sr = ch.get("stopReason")
        if sr not in ("no_incoming", "rest_endpoint"):
            continue
        nodes = ch.get("chain")
        if not isinstance(nodes, list) or not nodes:
            continue
        top = nodes[-1]
        if not isinstance(top, dict):
            continue
        info = extract_top_entry_info(root, top)
        if info:
            ch["topEntry"] = info


def format_callchain_markdown(payload: dict[str, Any]) -> str:
    """
    将 callchain 的 JSON 结构体转为 Markdown：含流程说明、ASCII 图示与嵌入的 JSON 代码块。
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
                    for i, ent in enumerate(jge[:24]):
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

    lines: list[str] = [
        "# Java 调用链（callchain）",
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
        "## 概要",
        "",
        f"- **链数**: {count}",
        "- **追踪方向**: 自内向外（被调方法 → 调用者 → …）",
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
        lines.append(f"### 链 {idx + 1}（终止: {label}{gtitle}）")
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
            lines.append("#### 最上层入口（REST / 注释）")
            lines.append("")
            rs = te.get("restSummary")
            if isinstance(rs, str) and rs.strip():
                lines.append(f"- **REST**: `{rs.strip()}`")
            else:
                rp = te.get("restPath")
                hm = te.get("httpMethod")
                if isinstance(rp, str) and rp.strip():
                    lines.append(f"- **路径**: `{rp.strip()}`")
                if isinstance(hm, str) and hm.strip():
                    lines.append(f"- **HTTP**: `{hm.strip()}`")
            cb = te.get("classBasePath")
            if isinstance(cb, str) and cb.strip() and not (isinstance(rs, str) and rs.strip()):
                lines.append(f"- **类级 base**: `{cb.strip()}`")
            jd = te.get("javadoc")
            if isinstance(jd, str) and jd.strip():
                lines.append("- **说明（JavaDoc）**:")
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


def _finalize_callchains(
    root: Path,
    chains: list[dict[str, Any]],
    query_meta: dict[str, Any],
    output_format: Literal["json", "markdown"],
) -> str:
    if not chains:
        return "无结果: callchain"
    _enrich_chains_with_top_entry(root, chains)
    query_meta = {**query_meta, "projectRoot": str(root)}
    payload = {
        "query": query_meta,
        "chainCount": len(chains),
        "chains": chains,
    }
    if output_format == "markdown":
        return format_callchain_markdown(payload)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def trace_call_chain_sync(
    project_path: str,
    class_name: str | None = None,
    method_name: str | None = None,
    *,
    file_path: str | None = None,
    line: int | None = None,
    character: int | None = None,
    symbol_query: str | None = None,
    jdtls_path: Path | None = None,
    max_depth: int = MAX_DEPTH_DEFAULT,
    output_format: Literal["json", "markdown"] = "json",
    grep_parallel_workers: int | None = None,
    grep_skip_interface: bool = False,
    grep_skip_rest: bool = False,
    grep_max_entry_points: int | None = None,
) -> str:
    """
    向上追踪调用链。入口三选一：
    - **类名 + 方法名**（与原先相同）
    - **file_path + line**（相对项目根或绝对路径的 .java，行号 1-based，可选 character 1-based）
    - **symbol_query**（workspace/symbol 关键字，优先匹配方法符号；全文 grep 多入口时串行追踪）

    ``grep_skip_interface`` / ``grep_skip_rest`` / ``grep_max_entry_points``：仅作用于关键字回退到
    ``java_text_grep`` 时的起点列表（见 README）。
    """
    root_path = Path(project_path).resolve()
    if not root_path.exists():
        msg = f"错误: 项目路径不存在 {project_path}"
        _log.warning("%s", msg)
        return msg

    has_cm = bool(class_name and str(class_name).strip()) and bool(method_name and str(method_name).strip())
    has_fl = bool(file_path and str(file_path).strip()) and line is not None
    has_q = bool(symbol_query and str(symbol_query).strip())

    # 「全限定或简单类名.方法名」形式的单关键字 → 按类+方法解析（不依赖 workspace/symbol）
    # 含 ``|`` / ``｜`` 时视为多关键字，不走本分支
    if has_q and not has_cm and not has_fl:
        sq0 = str(symbol_query).strip()
        if "|" not in sq0 and "｜" not in sq0 and "." in sq0:
            left, right = sq0.rsplit(".", 1)
            if left and right and re.match(r"^[A-Za-z_]\w*$", right):
                class_name = left
                method_name = right
                has_cm = True
                has_q = False

    has_cm = bool(class_name and str(class_name).strip()) and bool(method_name and str(method_name).strip())
    has_q = bool(symbol_query and str(symbol_query).strip()) and not has_cm

    if int(has_cm) + int(has_fl) + int(has_q) != 1:
        msg = (
            "错误: 请指定唯一入口：(--class 与 --method)，或 (--file 与 --line)，或 (--query 关键字)"
        )
        _log.warning("%s", msg)
        return msg

    if has_fl and int(line) < 1:
        msg = "错误: --line 须为 >=1 的行号"
        _log.warning("%s", msg)
        return msg

    _log.info(
        "trace_call_chain_sync project=%s mode=cm:%s fl:%s q:%s max_depth=%s",
        project_path,
        has_cm,
        has_fl,
        has_q,
        max_depth,
    )

    client: LSPClient | None = create_client(project_path, jdtls_path=jdtls_path)
    root = Path(client.root).resolve()
    try:
        item: dict[str, Any] | None = None
        query_meta: dict[str, Any] = {"mode": "class_method"}

        if has_cm:
            cls = _find_target_class_symbol(client, root, class_name or "")
            if cls is None:
                msg = f"错误: 未找到类 {class_name}"
                _log.warning("%s", msg)
                return msg
            cls_uri = _symbol_uri(cls)
            if not cls_uri:
                msg = f"错误: 类 {class_name} 缺少位置信息"
                _log.warning("%s", msg)
                return msg
            cls_path = _uri_to_path(cls_uri)
            if cls_path is None or not cls_path.exists():
                msg = f"错误: 类文件不存在 {cls_uri}"
                _log.warning("%s", msg)
                return msg

            client.open_file(str(cls_path))
            ds = client.request("textDocument/documentSymbol", {"textDocument": {"uri": cls_uri}})
            symbols = [x for x in _norm_list(ds) if isinstance(x, dict)]
            ms = _find_method_symbol_by_name(symbols, class_name, method_name)
            if ms is None:
                msg = f"错误: 在类 {class_name} 中未找到方法 {method_name}"
                _log.warning("%s", msg)
                return msg

            sel = ms.get("selectionRange", {})
            if not isinstance(sel, dict):
                sel = {}
            st = sel.get("start", {})
            if not isinstance(st, dict):
                st = {}
            line0 = int(st.get("line", 0))
            char0 = int(st.get("character", 0))
            if not sel or "start" not in sel:
                loc = ms.get("location", {})
                if isinstance(loc, dict):
                    rng = loc.get("range", {})
                    if isinstance(rng, dict):
                        start = rng.get("start", {})
                        if isinstance(start, dict):
                            line0 = int(start.get("line", 0))
                            char0 = int(start.get("character", 0))

            item = _prepare_item(client, cls_uri, line0, char0)
            if item is None:
                msg = f"错误: 无法准备调用层级（{class_name}.{method_name}）"
                _log.warning("%s", msg)
                return msg
            query_meta = {
                "mode": "class_method",
                "className": class_name.strip(),
                "methodName": method_name.strip(),
            }

        elif has_fl:
            fp = str(file_path).strip()
            abs_path = (root / fp).resolve() if not Path(fp).is_absolute() else Path(fp).resolve()
            if not abs_path.exists() or abs_path.suffix.lower() != ".java":
                msg = f"错误: 文件不存在或不是 .java: {file_path}"
                _log.warning("%s", msg)
                return msg
            client.open_file(str(abs_path))
            uri = abs_path.as_uri()
            ln = int(line)
            ch = int(character) if character is not None else 1
            line0 = max(0, ln - 1)
            char0 = max(0, ch - 1)
            item = _resolve_call_hierarchy_item_from_file_line(client, uri, line0, char0)
            if item is None:
                msg = f"错误: 无法在指定位置准备调用层级（{file_path}:{ln}）"
                _log.warning("%s", msg)
                return msg
            cname, mname = _names_from_hierarchy_item(item)
            try:
                file_display = str(abs_path.relative_to(root))
            except ValueError:
                file_display = str(abs_path)
            query_meta = {
                "mode": "file_line",
                "file": file_display,
                "line": ln,
                "character": ch,
                "className": cname,
                "methodName": mname,
            }

        else:
            item, kw_extra = _resolve_item_from_keyword(
                client,
                symbol_query.strip() if symbol_query else "",
                root,
                grep_skip_interface=grep_skip_interface,
                grep_skip_rest=grep_skip_rest,
                grep_max_entry_points=grep_max_entry_points,
            )
            if kw_extra.get("javaGrepMultiFile"):
                entries = kw_extra.get("javaGrepEntries")
                if not isinstance(entries, list) or not entries:
                    msg = (
                        f"错误: 关键字「{symbol_query.strip()}」grep 多入口在过滤后为空"
                        "（可放宽 --grep-skip-interface / --grep-skip-rest 或增大 --grep-max-entry-points）"
                    )
                    _log.warning("%s", msg)
                    return msg
                w = _effective_grep_workers(len(entries), grep_parallel_workers)
                _log.info(
                    "keyword java_text_grep multi-file: %s entries, sequential trace (shared LSP)",
                    len(entries),
                )
                assert client is not None
                chains = _trace_java_grep_entries_parallel(
                    client,
                    root,
                    entries=[e for e in entries if isinstance(e, dict)],
                    max_depth=max_depth,
                    max_workers=w,
                )
                query_meta: dict[str, Any] = {
                    "mode": "keyword",
                    "keyword": symbol_query.strip(),
                    "className": "(multiple files)",
                    "methodName": "(multiple)",
                    "javaGrepParallelWorkers": w,
                    "javaGrepTraceSequential": True,
                }
                query_meta.update(kw_extra)
                out = _finalize_callchains(root, chains, query_meta, output_format)
                if out == "无结果: callchain":
                    _log.info("%s", out)
                    return out
                _log.info("trace_call_chain_sync chainCount=%s result_chars=%s", len(chains), len(out))
                _log.debug("trace_call_chain_sync result=%s", format_payload(out))
                return out

            if item is None:
                msg = (
                    f"错误: 关键字「{symbol_query.strip()}」未解析到可调用的方法符号"
                    "（workspace 索引、类回退与 *.java 全文搜索均无可用位置；"
                    "若使用了 grep 起点过滤，可放宽 --grep-skip-interface / --grep-skip-rest；"
                    "或尝试更具体的关键字，或使用 --class/--method 或 --file/--line）"
                )
                _log.warning("%s", msg)
                return msg
            cname, mname = _names_from_hierarchy_item(item)
            query_meta = {
                "mode": "keyword",
                "keyword": symbol_query.strip(),
                "className": cname,
                "methodName": mname,
            }
            if kw_extra:
                query_meta.update(kw_extra)

        chains: list[dict[str, Any]] = []
        assert client is not None
        _trace_up_all(
            client=client,
            root=root,
            item=item,
            current_chain=[],
            out_chains=chains,
            seen=set(),
            depth=0,
            max_depth=max(1, int(max_depth)),
        )
        out = _finalize_callchains(root, chains, query_meta, output_format)
        if out == "无结果: callchain":
            _log.info("%s", out)
            return out
        _log.info("trace_call_chain_sync chainCount=%s result_chars=%s", len(chains), len(out))
        _log.debug("trace_call_chain_sync result=%s", format_payload(out))
        return out
    finally:
        if client is not None:
            client.shutdown()


__all__ = ["trace_call_chain_sync", "format_callchain_markdown", "extract_top_entry_info"]
