"""HTTP 静态入口：Spring Web 映射（``rest-map.json`` 结构）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jdtls_lsp.java_grep import SKIP_DIR_PARTS, java_scan_roots, walk_files_matching
from jdtls_lsp.logutil import get_logger

_log = get_logger("entry_scan.rest_http")

_STRING_LITERAL = re.compile(r"[\"']([^\"']+)[\"']")
_REQUEST_METHOD = re.compile(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)")
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;")
_CLASS_LINE = re.compile(
    r"^\s*(?:public\s+|protected\s+|private\s+)?(?:abstract\s+)?(?:final\s+)?class\s+(\w+)\b"
)
_METHOD_LINE = re.compile(
    r"^\s*(?:@\w+\s+)*(?:public|protected|private)\s+[\w<>,?\s\[\]]+\s+(\w+)\s*\("
)
_MAPPING_IN_LINE = re.compile(
    r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*(\([^)]*\))?"
)
# Spring Web：``@RestController`` 与 ``@org.springframework.web.bind.annotation.RestController`` 等
_REST_CONTROLLER = re.compile(r"@(?:[\w.]+\.)*RestController\b")
# 仅 ``@Controller`` / ``@org.springframework.stereotype.Controller``（不含 RestController）
_STEREOTYPE_CONTROLLER = re.compile(r"@(?:[\w.]+\.)*Controller\b")


def _http_path_from_mapping(ann: str, paren: str | None) -> tuple[str | None, str]:
    paren = paren or ""
    if ann == "GetMapping":
        return "GET", _first_path(paren)
    if ann == "PostMapping":
        return "POST", _first_path(paren)
    if ann == "PutMapping":
        return "PUT", _first_path(paren)
    if ann == "DeleteMapping":
        return "DELETE", _first_path(paren)
    if ann == "PatchMapping":
        return "PATCH", _first_path(paren)
    if ann == "RequestMapping":
        rm = _REQUEST_METHOD.search(paren)
        http = rm.group(1) if rm else None
        path = _first_path(paren)
        if path is None:
            for key in ("value", "path"):
                m = re.search(rf"{key}\s*=\s*[\"']([^\"']+)[\"']", paren)
                if m:
                    path = m.group(1)
                    break
        if path is None:
            path = ""
        return http, path
    return None, ""


def _first_path(paren: str) -> str:
    m = _STRING_LITERAL.search(paren)
    if m:
        return m.group(1)
    if re.search(r"\(\s*\)\s*$", paren.strip()):
        return ""
    return ""


def _join_paths(base: str, sub: str) -> str:
    b = base.strip().rstrip("/")
    s = (sub or "").strip()
    if not s:
        return ("/" + b) if b and not b.startswith("/") else (b or "")
    if s.startswith("/"):
        s = s[1:]
    if not b:
        return "/" + s
    if not b.startswith("/"):
        b = "/" + b
    return b + "/" + s


def _class_base_from_annots(lines: list[str]) -> str:
    for ln in lines:
        if "@RequestMapping" not in ln:
            continue
        m = re.search(r"@RequestMapping\s*(\([^)]*\))?", ln)
        if not m:
            continue
        _http, path = _http_path_from_mapping("RequestMapping", m.group(1))
        if path is not None:
            return path
    return ""


def _is_controller_file(lines: list[str]) -> bool:
    """
    视作 Spring Web 映射源文件：``@RestController`` 或 stereotype ``@Controller``（含
    ``@Controller(value = "…")`` 等属性），或 **仅有** 类级/方法级 ``@RequestMapping``、``@GetMapping`` 等
    （兼容旧代码、非 ``*Controller`` 类名）。

    ``@Controller`` 与 ``@RestController`` 一样：只要出现即视为待扫（不要求同文件内另有映射注解；
    方法上可无映射或仅有子类/配置中的映射）。
    支持限定名写法，如 ``@org.springframework.stereotype.Controller``。
    """
    text = "\n".join(lines)
    if _REST_CONTROLLER.search(text):
        return True
    if _STEREOTYPE_CONTROLLER.search(text) and not _REST_CONTROLLER.search(text):
        return True
    # 无 @Controller/@RestController，但存在 Spring MVC 映射注解（行首风格，减少误匹配注释内片段）
    if any(
        re.search(rf"^\s*{re.escape(m)}\b", text, re.MULTILINE)
        for m in (
            "@RequestMapping",
            "@GetMapping",
            "@PostMapping",
            "@PutMapping",
            "@DeleteMapping",
            "@PatchMapping",
        )
    ):
        return True
    return False


def _emit_from_ann_block(
    ann_lines: list[str],
    *,
    pkg: str,
    simple_class: str | None,
    class_base: str,
    method_name: str,
    rel: str,
    line_no: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    fqcn = f"{pkg}.{simple_class}" if pkg and simple_class else simple_class or "?"
    blob = "\n".join(ann_lines)
    for m in _MAPPING_IN_LINE.finditer(blob):
        ann = m.group(1)
        par = m.group(2) or ""
        http, sub = _http_path_from_mapping(ann, par)
        if ann == "RequestMapping" and http is None:
            http = "GET"
        if http is None:
            continue
        full = _join_paths(class_base, sub)
        out.append(
            {
                "httpMethod": http,
                "path": full,
                "className": fqcn,
                "simpleClassName": simple_class,
                "methodName": method_name,
                "file": rel,
                "line": line_no,
                "annotation": f"@{ann}",
            }
        )
    return out


def scan_rest_map(project_root: Path, *, max_files: int = 8_000) -> dict[str, Any]:
    """Spring MVC 映射启发式扫描；返回 ``rest-map.json`` 兼容结构。"""
    root = project_root.resolve()
    endpoints: list[dict[str, Any]] = []
    scanned = 0
    controller_files = 0
    _log.info("entry_scan rest-map start root=%s max_files=%s", root, max_files)
    for base in java_scan_roots(root):
        for path in sorted(walk_files_matching(base, "*.java")):
            if any(x in path.parts for x in SKIP_DIR_PARTS):
                continue
            scanned += 1
            if scanned > max_files:
                break
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            if not _is_controller_file(lines):
                continue
            controller_files += 1
            pkg = ""
            for ln in lines[:120]:
                pm = _PACKAGE.match(ln)
                if pm:
                    pkg = pm.group(1)
                    break
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                rel = str(path)

            pending_ann: list[str] = []
            class_base = ""
            simple_class: str | None = None

            for i, line in enumerate(lines):
                s = line.strip()
                if s.startswith("@"):
                    pending_ann.append(line)
                    continue
                cm = _CLASS_LINE.match(line)
                if cm:
                    simple_class = cm.group(1)
                    class_base = _class_base_from_annots(pending_ann)
                    pending_ann = []
                    continue
                mm = _METHOD_LINE.match(line)
                if mm:
                    method_name = mm.group(1)
                    if method_name in ("if", "for", "while", "switch", "catch", "try", "new"):
                        pending_ann = []
                        continue
                    if pending_ann and _MAPPING_IN_LINE.search("\n".join(pending_ann)):
                        endpoints.extend(
                            _emit_from_ann_block(
                                pending_ann,
                                pkg=pkg,
                                simple_class=simple_class,
                                class_base=class_base,
                                method_name=method_name,
                                rel=rel,
                                line_no=i + 1,
                            )
                        )
                    pending_ann = []
                    continue
                if s and not s.startswith("//") and not s.startswith("*") and not s.startswith("import "):
                    pending_ann = []
        if scanned > max_files:
            break

    capped = scanned > max_files
    out = {
        "projectRoot": str(root),
        "endpointCount": len(endpoints),
        "endpoints": endpoints,
        "javaFilesScanned": scanned,
        "controllerFilesSeen": controller_files,
        "scanCapped": capped,
        "note": "heuristic-regex; not a substitute for JDTLS or runtime routing",
    }
    _log.info(
        "entry_scan rest-map done javaFilesScanned=%s controllerFiles=%s endpoints=%s capped=%s",
        scanned,
        controller_files,
        len(endpoints),
        capped,
    )
    return out
