"""step1：**scan_modules** — Maven / Gradle 工程概要（模块与构建线索，无 JDTLS）。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from jdtls_lsp.logutil import get_logger

_log = get_logger("reverse_design.scan_modules")


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _parse_pom_modules(pom_path: Path) -> tuple[dict[str, Any], list[str]]:
    """返回 (pom 元数据, 子 module 相对路径列表)。"""
    meta: dict[str, Any] = {"pomFile": str(pom_path)}
    modules: list[str] = []
    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
    except ET.ParseError:
        return meta, modules

    for el in root.iter():
        t = _local_tag(el.tag)
        if t == "artifactId" and el.text and "artifactId" not in meta:
            meta["artifactId"] = el.text.strip()
        if t == "packaging" and el.text:
            meta["packaging"] = el.text.strip()
        if t == "name" and el.text and "name" not in meta:
            meta["name"] = el.text.strip()
        if t == "modules":
            for ch in el:
                if _local_tag(ch.tag) == "module" and ch.text:
                    m = ch.text.strip()
                    if m:
                        modules.append(m)
    return meta, modules


def _find_gradle_includes(settings_text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"include\s*\(\s*([^)]+)\)", settings_text):
        inner = m.group(1)
        for q in re.findall(r"['\"]([^'\"]+)['\"]", inner):
            qn = q.strip().lstrip(":").replace(":", "/")
            if qn and qn not in seen:
                seen.add(qn)
                out.append(qn)
    for m in re.finditer(r"include\s+['\"]([^'\"]+)['\"]", settings_text):
        qn = m.group(1).strip().lstrip(":").replace(":", "/")
        if qn and qn not in seen:
            seen.add(qn)
            out.append(qn)
    return out


def scan_modules(project_root: Path) -> dict[str, Any]:
    """
    扫描 ``projectRoot`` 下的 Maven ``pom.xml`` / Gradle ``settings.gradle*``。

    产出机器可读 JSON 结构（由 CLI 序列化）：``buildSystem``、``modules``、``pomFiles`` 等。
    """
    root = project_root.resolve()
    out: dict[str, Any] = {
        "projectRoot": str(root),
        "buildSystem": "unknown",
        "modules": [],
        "pomFiles": [],
        "gradleSettingsFiles": [],
    }

    pom_root = root / "pom.xml"
    if pom_root.is_file():
        out["buildSystem"] = "maven"
        meta, mods = _parse_pom_modules(pom_root)
        out["rootPom"] = meta
        out["pomFiles"].append(str(pom_root.relative_to(root)) if pom_root.is_relative_to(root) else str(pom_root))
        seen_paths: set[str] = set()

        def add_module(rel: str, source: str) -> None:
            rel = rel.strip().rstrip("/")
            if not rel or rel in seen_paths:
                return
            seen_paths.add(rel)
            mp = root / rel / "pom.xml"
            entry: dict[str, Any] = {
                "name": rel.split("/")[-1] if rel != "." else (meta.get("artifactId") or root.name),
                "path": rel,
                "source": source,
                "hasPom": mp.is_file(),
            }
            if mp.is_file():
                sub_meta, _ = _parse_pom_modules(mp)
                entry["artifactId"] = sub_meta.get("artifactId")
                entry["packaging"] = sub_meta.get("packaging")
                rp = str(mp.relative_to(root))
                if rp not in out["pomFiles"]:
                    out["pomFiles"].append(rp)
            out["modules"].append(entry)

        if mods:
            for m in mods:
                add_module(m, "root-pom-modules")
        else:
            out["modules"].append(
                {
                    "name": meta.get("artifactId") or root.name,
                    "path": ".",
                    "source": "single-maven-project",
                    "hasPom": True,
                    "artifactId": meta.get("artifactId"),
                    "packaging": meta.get("packaging"),
                }
            )

    # Gradle（可与 Maven 同仓较少见；若已有 maven 仍记录 gradle 文件）
    for name in ("settings.gradle", "settings.gradle.kts"):
        sp = root / name
        if sp.is_file():
            out["gradleSettingsFiles"].append(name)
            if out["buildSystem"] == "unknown":
                out["buildSystem"] = "gradle"
            try:
                text = sp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            includes = _find_gradle_includes(text)
            if includes and not out["modules"]:
                out["gradleIncludesRaw"] = includes
                for inc in includes:
                    out["modules"].append(
                        {
                            "name": inc.split("/")[-1],
                            "path": inc,
                            "source": "settings-include",
                            "hasPom": (root / inc / "pom.xml").is_file(),
                        }
                    )
            elif includes:
                out["gradleIncludesRaw"] = includes

    if out["buildSystem"] == "unknown" and (root / "build.gradle").is_file():
        out["buildSystem"] = "gradle"
        out["singleProjectGradle"] = True

    n_mod = len(out.get("modules") or [])
    _log.info(
        "reverse-design scan done root=%s buildSystem=%s modules=%s pomFiles=%s gradleSettings=%s",
        root,
        out.get("buildSystem"),
        n_mod,
        len(out.get("pomFiles") or []),
        len(out.get("gradleSettingsFiles") or []),
    )

    return out
