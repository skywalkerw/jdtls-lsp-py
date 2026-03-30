"""step3：数据库表清单与轻量抽取（用户清单为准 + @Table / SQL 字面量 / MyBatis XML）。"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jdtls_lsp.java_grep import SKIP_DIR_PARTS, java_scan_roots, walk_files_matching
from jdtls_lsp.logutil import get_logger
from jdtls_lsp.reverse_design.java_enclosing_method import java_enclosing_method_at_line

_log = get_logger("reverse_design.table_manifest")

# @Table( … ) 而非 @TableField
_TABLE_ANN = re.compile(
    r"@Table\s*\(\s*(?:name\s*=\s*)?[\"']([^\"']+)[\"']",
    re.MULTILINE,
)


def extract_jpa_table_names_from_java(text: str) -> list[str]:
    """从源码中提取 ``@Table( … name = '…' … )`` 的物理表名（不含 ``@TableField``）。"""
    names: list[str] = []
    for m in _TABLE_ANN.finditer(text):
        raw = m.group(1).strip()
        if "." in raw:
            raw = raw.split(".")[-1]
        names.append(raw)
    return names


_STRING_DOUBLE = re.compile(r"\"(?:[^\"\\]|\\.)*\"")
_STRING_SINGLE = re.compile(r"'(?:[^'\\]|\\.)*'")

_SQL_HINT = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM)\b", re.I)
# FROM/JOIN/INTO/UPDATE 后接物理表（可带 schema：SCHEMA.TABLE_ABC）；排除子查询 FROM (...)
_SQL_TABLE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(?!\()\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)",
    re.I,
)
_WITH_HEAD = re.compile(r"\bWITH\s+(?:RECURSIVE\s+)?", re.I)
# 最外层 SELECT 的 FROM 子句结束（括号内 WHERE 不计入）
_END_OUTER_FROM = re.compile(
    r"\b(WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|UNION|OFFSET|FETCH|FOR\s+UPDATE)\b",
    re.I,
)

_MYBATIS_TABLE_ATTR = re.compile(r"\btable\s*=\s*[\"']([^\"']+)[\"']", re.I)

_PHYSICAL_TABLE_TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")
_PASCAL_JAVA_TYPE = re.compile(r"^[A-Z][a-zA-Z0-9]+$")

_SQL_RESERVED = frozenset(
    {
        "DUAL",
        "WHERE",
        "GROUP",
        "ORDER",
        "HAVING",
        "LIMIT",
        "OFFSET",
        "SET",
        "VALUES",
        "SELECT",
        "DISTINCT",
        "INNER",
        "LEFT",
        "RIGHT",
        "OUTER",
        "FULL",
        "CROSS",
        "ON",
        "AS",
        "AND",
        "OR",
        "NOT",
        "NULL",
        "TRUE",
        "FALSE",
        "CASE",
        "WHEN",
        "THEN",
        "ELSE",
        "END",
        "BY",
        "ASC",
        "DESC",
        "UNION",
        "ALL",
        "BETWEEN",
        "LIKE",
        "IN",
        "EXISTS",
        "INTO",
        "FROM",
        "JOIN",
        "UPDATE",
    }
)


def _skip_string_literal(s: str, i: int) -> int:
    """跳过 SQL 字符串字面量（``'`` / ``"``），``''`` 视为单引号转义。"""
    if i >= len(s) or s[i] not in "'\"":
        return i + 1
    quote = s[i]
    i += 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == quote:
            if quote == "'" and i + 1 < n and s[i + 1] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return n


def _next_paren_scan_end(s: str, open_idx: int) -> int:
    """从 ``open_idx`` 指向的 ``(`` 起，匹配到与之平衡的 ``)`` 之后的位置。"""
    depth = 0
    i = open_idx
    n = len(s)
    while i < n:
        c = s[i]
        if c in "'\"":
            i = _skip_string_literal(s, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _with_cte_names_casefold(s: str) -> set[str]:
    """解析 ``WITH a AS (...), b AS (...)`` 中的 CTE 名（小写集合），用于忽略 ``SELECT * FROM a`` 中的别名。"""
    out: set[str] = set()
    m = _WITH_HEAD.search(s)
    if not m:
        return out
    i = m.end()
    n = len(s)
    while True:
        while i < n and s[i] in " \t\n\r,":
            i += 1
        if i >= n:
            break
        rest = s[i:].lstrip()
        if rest.upper().startswith("SELECT"):
            break
        m2 = re.match(r"([A-Za-z_][\w]*)\s+AS\s*\(", s[i:], re.I)
        if not m2:
            break
        name = m2.group(1)
        if name.upper() in _SQL_RESERVED:
            break
        out.add(name.casefold())
        open_paren = i + m2.end() - 1
        i = _next_paren_scan_end(s, open_paren)
    return out


def _find_outer_from_clause_span(s: str) -> tuple[int, int] | None:
    """返回最外层 ``FROM`` 之后、子句结束（``WHERE``/``GROUP BY``/…）之前的区间 ``[start, end)``。"""
    n = len(s)
    depth = 0
    i = 0
    while i < n:
        c = s[i]
        if c in "'\"":
            i = _skip_string_literal(s, i)
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            m = re.match(r"\bFROM\b", s[i:], re.I)
            if m:
                j = i + m.end()
                while j < n and s[j] in " \t\n\r":
                    j += 1
                start = j
                depth2 = 0
                k = start
                while k < n:
                    if s[k] in "'\"":
                        k = _skip_string_literal(s, k)
                        continue
                    if s[k] == "(":
                        depth2 += 1
                        k += 1
                        continue
                    if s[k] == ")":
                        depth2 -= 1
                        k += 1
                        continue
                    if depth2 == 0 and _END_OUTER_FROM.match(s, k):
                        return (start, k)
                    k += 1
                return (start, n)
        i += 1
    return None


def _comma_tables_from_from_body(body: str) -> list[str]:
    """``FROM`` 子句片段内、括号深度为 0 处的 ``,`` 后表名（``FROM t1, t2`` / ``, sch.t2``）。"""
    out: list[str] = []
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c in "'\"":
            i = _skip_string_literal(body, i)
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0 and c == ",":
            i += 1
            while i < n and body[i] in " \t\n\r":
                i += 1
            if i >= n:
                break
            if body[i] == "(":
                i += 1
                depth += 1
                continue
            m = re.match(r"([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)", body[i:], re.I)
            if m:
                out.append(m.group(1).strip())
                i += m.end()
            continue
        i += 1
    return out


def _append_sql_table_token(found: list[str], raw: str, *, cte_cf: set[str]) -> None:
    if raw.upper() in _SQL_RESERVED:
        return
    if "." not in raw and raw.casefold() in cte_cf:
        return
    phys = raw.rsplit(".", 1)[-1].strip() if "." in raw else raw
    found.append(phys)


def normalize_table_token_to_physical(name: str) -> str:
    """将 manifest/代码中出现的表名字符串统一为**蛇形物理表键**（小写），便于 ``MonitorData`` 与 ``monitor_data`` 同组。

    - ``schema.table`` / ``db.schema.table`` → 取**最后一段**作为物理表名再归一化。
    - 已是蛇形 ``[a-z][a-z0-9_]*`` → 转小写。
    - **PascalCase**（实体/JPQL 类名）→ 驼峰转蛇形后再小写。
    - 其它情况 → ``str`` 小写（兜底归并）。
    """
    t = str(name).strip()
    if not t:
        return t
    if "." in t:
        segs = [x.strip() for x in t.split(".") if x.strip()]
        if segs:
            t = segs[-1]
    if _PHYSICAL_TABLE_TOKEN.match(t):
        return t.lower()
    if _PASCAL_JAVA_TYPE.match(t):
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", t)
        s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
        snake = s2.lower()
        if _PHYSICAL_TABLE_TOKEN.match(snake):
            return snake
    return t.lower()


def _skip_path(p: Path) -> bool:
    return any(x in p.parts for x in SKIP_DIR_PARTS)


def load_user_tables_from_file(path: Path) -> list[str]:
    """每行一表；``#`` 起为注释；行内 ``#`` 后为注释。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    out: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line[: line.index("#")].strip()
        if not line:
            continue
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            out.append(line)
    return out


def parse_user_tables_inline(s: str) -> list[str]:
    parts = [x.strip() for x in s.replace("，", ",").split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        k = p.casefold()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _merge_user_lists(file_tables: list[str], inline: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in file_tables + inline:
        k = t.casefold()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def _tables_in_sql_fragment(s: str) -> list[str]:
    """从 SQL 片段中抽取物理表名：``SCHEMA.TABLE``、``FROM t1, t2``、``WITH … AS`` 中 CTE 别名不计入。"""
    if not _SQL_HINT.search(s):
        return []
    cte_cf = _with_cte_names_casefold(s)
    found: list[str] = []
    for m in _SQL_TABLE.finditer(s):
        _append_sql_table_token(found, m.group(1).strip(), cte_cf=cte_cf)
    span = _find_outer_from_clause_span(s)
    if span is not None:
        start, end = span
        for raw in _comma_tables_from_from_body(s[start:end]):
            _append_sql_table_token(found, raw, cte_cf=cte_cf)
    return found


def _collect_java_paths(root: Path, max_files: int) -> list[Path]:
    out: list[Path] = []
    for base in java_scan_roots(root):
        for p in sorted(walk_files_matching(base, "*.java")):
            if _skip_path(p):
                continue
            out.append(p)
            if len(out) >= max_files:
                return sorted(out[:max_files], key=lambda x: str(x))
    return sorted(out, key=lambda x: str(x))


def _collect_xml_paths(root: Path, max_files: int) -> list[Path]:
    out: list[Path] = []
    for base in java_scan_roots(root):
        for p in sorted(walk_files_matching(base, "*.xml")):
            if _skip_path(p):
                continue
            if "/test/" in str(p).replace("\\", "/") or p.name.startswith("."):
                continue
            out.append(p)
            if len(out) >= max_files:
                return sorted(out[:max_files], key=lambda x: str(x))
    return sorted(out, key=lambda x: str(x))


def _scan_java_file(fp: Path, rel: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        if "@TableField" in line:
            pass
        if "@Table(" in line and "@TableField" not in line:
            for m in _TABLE_ANN.finditer(line):
                raw = m.group(1).strip()
                if "." in raw:
                    raw = raw.split(".")[-1]
                hits.append(
                    {
                        "table": raw,
                        "source": "entity_annotation",
                        "confidence": "high",
                        "file": rel,
                        "line": line_no,
                        "snippet": line.strip()[:240],
                    }
                )
        for sm in list(_STRING_DOUBLE.finditer(line)) + list(_STRING_SINGLE.finditer(line)):
            lit = sm.group(0)[1:-1]
            lit_unesc = bytes(lit, "utf-8").decode("unicode_escape", errors="replace")
            for tbl in _tables_in_sql_fragment(lit_unesc):
                m_line, m_name = java_enclosing_method_at_line(lines, line_no)
                row: dict[str, Any] = {
                    "table": tbl,
                    "source": "jdbc_sql_literal",
                    "confidence": "medium",
                    "file": rel,
                    "line": line_no,
                    "snippet": line.strip()[:240],
                    "javaMethod": m_name,
                    "javaMethodLine": m_line,
                }
                hits.append(row)
    return hits


def _scan_xml_file(fp: Path, rel: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    if text.startswith("\ufeff"):
        text = text[1:]
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in _MYBATIS_TABLE_ATTR.finditer(line):
            hits.append(
                {
                    "table": m.group(1).strip(),
                    "source": "mybatis_xml",
                    "confidence": "high",
                    "file": rel,
                    "line": line_no,
                    "snippet": line.strip()[:240],
                }
            )
        for sm in _STRING_DOUBLE.finditer(line):
            lit = sm.group(0)[1:-1]
            for tbl in _tables_in_sql_fragment(lit):
                hits.append(
                    {
                        "table": tbl,
                        "source": "mybatis_sql_literal",
                        "confidence": "medium",
                        "file": rel,
                        "line": line_no,
                        "snippet": line.strip()[:240],
                    }
                )
    return hits


def build_table_manifest(
    project_root: Path,
    *,
    user_tables: list[str] | None = None,
    tables_file: Path | None = None,
    tables_inline: str = "",
    strict_tables_only: bool = False,
    max_java_files: int = 8_000,
    max_xml_files: int = 2_000,
) -> dict[str, Any]:
    """
    合并 **用户表清单**（权威规范名）与 **代码轻量抽取**。

    - ``canonicalTables``：用户给定顺序（文件 + 行内 ``--tables``）；若均未提供则为抽取到的表名排序。
    - ``extractedHits``：全部命中（含 source/confidence）；``jdbc_sql_literal`` 另含 ``javaMethod`` / ``javaMethodLine``（包围方法简单名与签名行号）。
    - ``anchorsByTable``：按规范表名聚合命中（键与 ``canonicalTables`` 对齐，大小写不敏感归并）。
    - ``anchorsByPhysicalTable``：按**物理蛇形表键**聚合（``monitor_data`` 与 ``MonitorData`` 等同组）；同 ``file``+``line``+``source`` 只保留一条。
    - ``canonicalPhysicalTables``：由 ``canonicalTables`` 去重得到的物理表键列表（顺序保留）。
    - ``unresolvedTables``：用户清单中有、但 **无任何命中** 的表。
    - ``extractedOnly``：有命中但 **不在用户清单** 中的表（``strict_tables_only`` 时仍计算但可从展示侧隐藏）。
    """
    root = project_root.resolve()
    if not root.exists():
        return {"error": f"项目路径不存在: {project_root}"}

    ut: list[str] = []
    if tables_file is not None:
        p = Path(tables_file).expanduser()
        if not p.is_file():
            return {"error": f"表清单文件不存在: {p}"}
        ut.extend(load_user_tables_from_file(p))
    ut = _merge_user_lists(ut, parse_user_tables_inline(tables_inline))

    canonical_norm: dict[str, str] = {}
    for t in ut:
        canonical_norm[t.casefold()] = t

    extracted_hits: list[dict[str, Any]] = []
    java_files = _collect_java_paths(root, max_java_files)
    for fp in java_files:
        try:
            rel = str(fp.relative_to(root))
        except ValueError:
            rel = str(fp)
        extracted_hits.extend(_scan_java_file(fp, rel))

    xml_files = _collect_xml_paths(root, max_xml_files)
    for fp in xml_files:
        try:
            rel = str(fp.relative_to(root))
        except ValueError:
            rel = str(fp)
        extracted_hits.extend(_scan_xml_file(fp, rel))

    tables_from_extract: set[str] = set()
    for h in extracted_hits:
        tables_from_extract.add(h["table"])

    if not ut:
        canonical_list = sorted(tables_from_extract, key=lambda x: x.casefold())
        for t in canonical_list:
            canonical_norm.setdefault(t.casefold(), t)
    else:
        canonical_list = ut

    anchors_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for h in extracted_hits:
        raw_t = str(h["table"])
        key = canonical_norm.get(raw_t.casefold(), raw_t)
        row = {k: v for k, v in h.items() if k != "table"}
        row["tableAsFound"] = raw_t
        anchors_by_table[key].append(row)

    anchors_by_physical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_phys_key: set[tuple[str, str, int, str]] = set()
    for h in extracted_hits:
        raw_t = str(h["table"])
        phys = normalize_table_token_to_physical(raw_t)
        f = str(h.get("file", ""))
        line_no = int(h.get("line", 0) or 0)
        src = str(h.get("source", ""))
        dk = (phys, f.replace("\\", "/"), line_no, src)
        if dk in seen_phys_key:
            continue
        seen_phys_key.add(dk)
        prow = {k: v for k, v in h.items() if k != "table"}
        prow["tableAsFound"] = raw_t
        prow["physicalTable"] = phys
        anchors_by_physical[phys].append(prow)

    canonical_physical: list[str] = []
    seen_pc: set[str] = set()
    for t in canonical_list:
        p = normalize_table_token_to_physical(t)
        if p.casefold() not in seen_pc:
            seen_pc.add(p.casefold())
            canonical_physical.append(p)

    hit_norms = {str(h["table"]).casefold() for h in extracted_hits}
    if ut:
        unresolved = [t for t in ut if t.casefold() not in hit_norms]
    else:
        unresolved = []

    extracted_only_names: list[str] = []
    if ut:
        user_norms = {t.casefold() for t in ut}
        extracted_only_names = sorted(
            {str(h["table"]) for h in extracted_hits if str(h["table"]).casefold() not in user_norms},
            key=lambda x: x.casefold(),
        )

    out: dict[str, Any] = {
        "projectRoot": str(root),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "userTablesFile": str(Path(tables_file).resolve()) if tables_file else None,
        "tablesInline": tables_inline.strip() or None,
        "strictTablesOnly": strict_tables_only,
        "canonicalTables": canonical_list,
        "canonicalPhysicalTables": canonical_physical,
        "extractedHitCount": len(extracted_hits),
        "javaFilesScanned": len(java_files),
        "xmlFilesScanned": len(xml_files),
        "extractedHits": extracted_hits,
        "anchorsByTable": {k: v for k, v in sorted(anchors_by_table.items(), key=lambda x: x[0].casefold())},
        "anchorsByPhysicalTable": {
            k: v for k, v in sorted(anchors_by_physical.items(), key=lambda x: x[0].casefold())
        },
        "unresolvedTables": unresolved,
        "extractedOnly": [] if strict_tables_only else extracted_only_names,
    }

    _log.info(
        "table manifest: canonical=%s physicalGroups=%s hits=%s unresolved=%s extractedOnly=%s",
        len(canonical_list),
        len(anchors_by_physical),
        len(extracted_hits),
        len(unresolved),
        len(extracted_only_names),
    )
    return out
