"""从 Java 源码行列表解析方法 **紧上方** 的 ``/** … */``（不依赖 JDTLS）。"""

from __future__ import annotations

import re

_METHOD_LINE_HINT = re.compile(r"^\s*(?:public|protected|private)\b")


def _looks_like_java_method_line(s: str) -> bool:
    t = s.strip()
    if not t or t.startswith("//") or t.startswith("/*"):
        return False
    if not _METHOD_LINE_HINT.match(t):
        return False
    if re.search(r"\b(?:class|interface|enum)\s+\w+", t):
        return False
    return "(" in t


def _best_method_line_index(lines: list[str], line1: int) -> int:
    """
    将 ``line1``（1-based，来自 LSP/追踪）对齐到方法声明行：可能落在注解行或方法体内。
    """
    i0 = line1 - 1
    if i0 < 0:
        return 0
    if i0 >= len(lines):
        return max(0, len(lines) - 1)
    for k in range(i0, min(i0 + 16, len(lines))):
        if _looks_like_java_method_line(lines[k]):
            return k
    for k in range(i0, max(i0 - 48, -1), -1):
        if _looks_like_java_method_line(lines[k]):
            return k
    return i0


def _line_is_bare_close_brace(s: str) -> bool:
    """上一方法或块的单独收尾 ``}`` / ``};``（与本方法之间无 Javadoc 时常见）。"""
    t = s.strip()
    if not t:
        return False
    return bool(re.match(r"^}\s*;?\s*$", t))


def _collect_javadoc_block_from_opening(lines: list[str], k: int) -> list[str] | None:
    """从含 ``/**`` 的第 ``k`` 行起收集到含 ``*/`` 的行为止（单行或多行 Javadoc）。"""
    if k < 0 or k >= len(lines) or "/**" not in lines[k]:
        return None
    out: list[str] = []
    for m in range(k, min(k + 200, len(lines))):
        out.append(lines[m])
        if "*/" in lines[m]:
            return out
    return None


def _strip_javadoc_raw(block_lines: list[str]) -> str:
    """将 ``/** ... */`` 行列表压成一段可读纯文本（保留换行）。"""
    text = "\n".join(block_lines)
    text = re.sub(r"^\s*/\*\*?\s*", "", text, count=1)
    text = re.sub(r"\s*\*/\s*$", "", text.strip())
    out_lines: list[str] = []
    for ln in text.splitlines():
        ln = re.sub(r"^\s*\*\s?", "", ln)
        out_lines.append(ln.rstrip())
    return "\n".join(out_lines).strip()


def extract_javadoc_above_method(lines: list[str], line1: int) -> str | None:
    """
    取方法声明行 **正上方** 的 Javadoc（``/** ... */``），不含行尾 ``//`` 注释。

    ``line1`` 为 1-based；若指向注解或方法体内，会先尝试对齐到方法签名行。
    若紧贴本方法的是上一方法的收尾 ``}``（中间无 Javadoc），返回 ``None``，避免误把更上方的
    Javadoc + 整段方法体收进来。
    """
    if not lines:
        return None
    sig_idx = _best_method_line_index(lines, line1)
    j = sig_idx - 1
    while j >= 0:
        s = lines[j].strip()
        if s == "":
            j -= 1
            continue
        if s.startswith("@"):
            j -= 1
            continue
        break
    if j < 0:
        return None
    if _line_is_bare_close_brace(lines[j]):
        return None
    k = j
    while k >= 0:
        if "/**" in lines[k]:
            block = _collect_javadoc_block_from_opening(lines, k)
            if block:
                return _strip_javadoc_raw(block) or None
            return None
        k -= 1
    return None


__all__ = ["extract_javadoc_above_method"]
