"""step1 内部：**scan_java_top_level_types** — 轻量扫描 Java 源顶层 ``class`` / ``interface`` / ``enum`` / ``record``（无 JDTLS）。

由 ``batch_symbols_by_package`` 聚合为按包索引；不单独暴露 CLI。
"""

from __future__ import annotations

import re
from typing import Any

# LSP SymbolKind 对齐，便于沿用 symbols-by-package 消费方
_KIND_CLASS = 5
_KIND_INTERFACE = 11
_KIND_ENUM = 10

_TOP_DECL_RE = re.compile(r"\b(class|interface|enum|record)\s+([A-Za-z_]\w*)\b")


def _kind_for(word: str) -> tuple[int, str]:
    if word == "interface":
        return _KIND_INTERFACE, "interface"
    if word == "enum":
        return _KIND_ENUM, "enum"
    if word == "record":
        return _KIND_CLASS, "record"
    return _KIND_CLASS, "class"


def scan_java_top_level_types(source: str) -> list[dict[str, Any]]:
    """
    从源码中提取 **文件顶层** 的类型声明（``class`` / ``interface`` / ``enum`` / ``record``）。

    跳过注释与字符串内的大括号；在顶层忽略注解参数里的 ``{…}``（通过 ``depth==0`` 时的括号深度）。
    不解析方法体内部，故不列出成员。对非常规排版可能漏报或误报；适合设计导出 A3。
    """
    n = len(source)
    i = 0
    line = 1
    depth = 0
    paren0 = 0
    state = "code"
    buf: list[str] = []
    out: list[dict[str, Any]] = []

    def flush_decl(open_line: int) -> None:
        s = "".join(buf).strip()
        buf.clear()
        if not s:
            return
        last: re.Match[str] | None = None
        for m in _TOP_DECL_RE.finditer(s):
            kw = m.group(1)
            if kw == "interface" and m.start() > 0 and s[m.start() - 1] == "@":
                continue
            last = m
        if last is None:
            return
        kw = last.group(1)
        name = last.group(2)
        k, label = _kind_for(kw)
        out.append({"name": name, "kind": k, "kindLabel": label, "line": open_line})

    while i < n:
        ch = source[i]

        if state == "code":
            if ch == "\n":
                line += 1
                if depth == 0:
                    buf.append(" ")
                i += 1
                continue
            if ch == "/" and i + 1 < n:
                nxt = source[i + 1]
                if nxt == "/":
                    state = "line_com"
                    i += 2
                    continue
                if nxt == "*":
                    state = "block_com"
                    i += 2
                    continue
            if ch == '"':
                state = "str_d"
                i += 1
                continue
            if ch == "'":
                state = "char_lit"
                i += 1
                continue
            if ch == "(" and depth == 0:
                paren0 += 1
                i += 1
                continue
            if ch == ")" and depth == 0 and paren0 > 0:
                paren0 -= 1
                i += 1
                continue
            if ch == "{":
                if depth == 0 and paren0 == 0:
                    flush_decl(line)
                    depth = 1
                elif depth > 0:
                    depth += 1
                i += 1
                continue
            if ch == "}":
                if depth > 0:
                    depth -= 1
                i += 1
                continue
            if ch == ";" and depth == 0:
                buf.clear()
                i += 1
                continue
            if depth == 0 and paren0 == 0:
                buf.append(ch)
            i += 1
            continue

        if state == "line_com":
            if ch == "\n":
                line += 1
                state = "code"
            i += 1
            continue

        if state == "block_com":
            if ch == "\n":
                line += 1
            elif ch == "*" and i + 1 < n and source[i + 1] == "/":
                state = "code"
                i += 2
                continue
            i += 1
            continue

        if state == "str_d":
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                state = "code"
            elif ch == "\n":
                line += 1
            i += 1
            continue

        if state == "char_lit":
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                state = "code"
            elif ch == "\n":
                line += 1
            i += 1
            continue

    return out


__all__ = ["scan_java_top_level_types"]
