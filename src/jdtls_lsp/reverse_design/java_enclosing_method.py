"""从 ``*.java`` 行号推断**包含该行**的方法简单名（Repository 注解在上、签名在下；或类内方法体向上查找）。"""

from __future__ import annotations

import re


def line_likely_java_method_declaration(line: str) -> str | None:
    """若本行像接口/类方法签名，返回方法简单名。"""
    s = line.strip()
    if not s or s.startswith("@") or s.startswith("//") or s.startswith("*") or s.startswith("/*"):
        return None
    if s.startswith(("if (", "if(", "for (", "for(", "while (", "while(", "switch (", "catch (", "try {")):
        return None
    m = re.match(r"^([\w.<>,?\[\]\s]+?)\s+(\w+)\s*\(", s)
    if not m:
        return None
    ret, name = m.group(1).strip(), m.group(2)
    if name in {"if", "for", "while", "switch", "catch", "new", "return", "super", "this", "synchronized"}:
        return None
    if re.search(r"[<>\[\]]|\b(void|int|long|boolean|byte|short|float|double|var)\b", ret):
        return name
    toks = ret.split()
    if toks and toks[-1][0].isupper():
        return name
    return None


def java_enclosing_method_at_line(lines: list[str], line_1based: int) -> tuple[int, str]:
    """
    返回 ``(method_signature_line_1based, method_simple_name)``。

    无法解析时返回 ``(line_1based, "_line{N}")`` 占位。
    """
    idx = line_1based - 1
    if idx < 0 or idx >= len(lines):
        return (line_1based, f"_line{line_1based}")
    for j in range(idx, min(len(lines), idx + 48)):
        name = line_likely_java_method_declaration(lines[j])
        if name:
            return (j + 1, name)
        m = re.search(
            r"^\s*(?:public|protected|private|static)\s+[\w.<>,?\[\]\s]+\s+(\w+)\s*\(",
            lines[j],
        )
        if m and m.group(1) not in {"if", "for", "while", "switch"}:
            return (j + 1, m.group(1))
    for j in range(idx, max(-1, idx - 160), -1):
        line = lines[j]
        if line.strip().startswith("//"):
            continue
        name = line_likely_java_method_declaration(line)
        if name:
            return (j + 1, name)
        m = re.search(
            r"^\s*(?:public|protected|private|static)\s+[\w.<>,?\[\]\s]+\s+(\w+)\s*\(",
            line,
        )
        if m and m.group(1) not in {"if", "for", "while", "switch"}:
            return (j + 1, m.group(1))
    return (line_1based, f"_line{line_1based}")


__all__ = ["java_enclosing_method_at_line", "line_likely_java_method_declaration"]
