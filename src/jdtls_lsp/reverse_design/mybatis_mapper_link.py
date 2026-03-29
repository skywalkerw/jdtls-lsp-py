"""MyBatis XML 行号 → Mapper 接口 Java 方法位置（用于 callchain-up ``file_path``/``line``/``character``）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAPPER_NAMESPACE = re.compile(
    r"<mapper\b[^>]*\bnamespace\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE | re.DOTALL,
)
STMT_WITH_ID = re.compile(
    r"<(select|insert|update|delete)\b[^>]*\bid\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)


def _find_java_file_for_fqcn(project_root: Path, fqcn: str) -> Path | None:
    rel = fqcn.strip().replace(".", "/") + ".java"
    root = project_root.resolve()
    for trial in (root / "src/main/java" / rel, root / rel):
        if trial.is_file():
            return trial
    stem = rel.split("/")[-1]
    for p in sorted(root.rglob(stem)):
        if not p.is_file() or p.suffix.lower() != ".java":
            continue
        try:
            if p.resolve() == (root / "src/main/java" / rel).resolve():
                return p
        except ValueError:
            pass
        try:
            if str(p.relative_to(root)).replace("\\", "/").endswith(rel):
                return p
        except ValueError:
            continue
    return None


def _find_statement_id_before_line(lines: list[str], line_idx: int, *, lookback: int = 48) -> str | None:
    """``line_idx`` 为 0-based；在向上窗口内取**最后一个** ``<select|insert|… id=``（即包含当前行所在语句块）。"""
    start = max(0, line_idx - lookback)
    block = "\n".join(lines[start : line_idx + 1])
    last_id: str | None = None
    for m in STMT_WITH_ID.finditer(block):
        last_id = m.group(2).strip()
    return last_id


def _java_mapper_method_line_char(java_path: Path, method_id: str) -> tuple[int, int] | None:
    """Mapper 接口中 ``method_id`` 与 XML statement id 同名。"""
    try:
        text = java_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    mid = re.escape(method_id)
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        m = re.search(rf"\b{mid}\s*\(", line)
        if m:
            return (i, m.start() + 1)
    return None


def resolve_mapper_java_method_from_xml_line(
    project_root: Path,
    xml_rel: str,
    line_1based: int,
) -> dict[str, Any]:
    """
    由 MyBatis Mapper XML 中任一行定位 **namespace** 对应接口中的 **statement id** 方法。

    成功时 ``ok`` True，含 ``javaFile``（相对项目根）、``line``、``character``、``mapperStatementId``、``mapperNamespace``。
    """
    root = project_root.resolve()
    rel = xml_rel.replace("\\", "/").lstrip("/")
    path = root / rel
    if not path.is_file():
        return {"ok": False, "reason": f"XML 不存在: {rel}"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "reason": str(e)}
    if text.startswith("\ufeff"):
        text = text[1:]
    ns_m = MAPPER_NAMESPACE.search(text)
    if not ns_m:
        return {"ok": False, "reason": "无 <mapper namespace=...>"}
    namespace = ns_m.group(1).strip()
    if not namespace:
        return {"ok": False, "reason": "namespace 为空"}

    lines = text.splitlines()
    idx = line_1based - 1
    if idx < 0 or idx >= len(lines):
        return {"ok": False, "reason": f"行号越界: {line_1based}"}

    stmt_id = _find_statement_id_before_line(lines, idx)
    if not stmt_id:
        return {"ok": False, "reason": "未找到包含该行的 statement id（select/insert/update/delete）"}

    java_path = _find_java_file_for_fqcn(root, namespace)
    if java_path is None:
        return {
            "ok": False,
            "reason": f"未找到接口文件: {namespace.replace('.', '/')}.java",
            "mapperNamespace": namespace,
            "mapperStatementId": stmt_id,
        }

    pos = _java_mapper_method_line_char(java_path, stmt_id)
    if not pos:
        return {
            "ok": False,
            "reason": f"接口中无方法 {stmt_id}",
            "mapperNamespace": namespace,
            "mapperStatementId": stmt_id,
        }

    line1, char1 = pos
    try:
        jrel = str(java_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        jrel = str(java_path)

    return {
        "ok": True,
        "javaFile": jrel,
        "line": line1,
        "character": char1,
        "mapperStatementId": stmt_id,
        "mapperNamespace": namespace,
        "xmlFile": rel,
        "xmlLine": line_1based,
    }


__all__ = ["resolve_mapper_java_method_from_xml_line"]
