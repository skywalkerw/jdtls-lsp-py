#!/usr/bin/env python3
"""生成 portable full/mini tar.gz（与 export.sh 规则一致，跨平台）。"""
from __future__ import annotations

import os
import tarfile
from pathlib import Path


def should_exclude(rel: str, project: str, mini: bool) -> bool:
    r = rel.replace("\\", "/")
    if r.endswith("/") or r == project + "/":
        return False
    parts = r.split("/")
    if len(parts) < 2:
        return False
    rest = "/".join(parts[1:])
    if rest.startswith(".git/"):
        return True
    if rest.startswith(".venv/"):
        return True
    if "/__pycache__/" in "/" + rest + "/":
        return True
    if ".egg-info/" in rest or rest.endswith(".egg-info"):
        return True
    if rest.endswith(".pyc"):
        return True
    if rest.startswith("openjdk/"):
        return True
    if rest.startswith("jdtls/"):
        return True
    if rest.endswith(".DS_Store") or parts[-1] == ".DS_Store":
        return True
    if mini and rest.startswith("offline-packages/"):
        return True
    return False


def add_tree(tar: tarfile.TarFile, base: Path, project: str, mini: bool) -> int:
    count = 0
    root = base / project
    for p in root.rglob("*"):
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        rel_s = rel.as_posix()
        if p.is_dir():
            continue
        if should_exclude(rel_s, project, mini):
            continue
        tar.add(p, arcname=rel_s, recursive=False)
        count += 1
    return count


def _verify_flags(names: list[str], project: str) -> dict[str, bool]:
    return {
        "offline-packages": any(n.startswith(f"{project}/offline-packages/") for n in names),
        "openjdk": any(n.startswith(f"{project}/openjdk/") for n in names),
        "jdtls": any(n.startswith(f"{project}/jdtls/") for n in names),
        "DS_Store": any(n.endswith(".DS_Store") for n in names),
    }


def main() -> None:
    project_dir = Path(__file__).resolve().parent.parent
    workspace = project_dir.parent
    project_name = project_dir.name
    os.chdir(str(workspace))

    full_path = workspace / f"{project_name}-portable-full.tar.gz"
    mini_path = workspace / f"{project_name}-portable-mini.tar.gz"

    print(f"[export] 项目目录: {project_dir}")
    print(f"[export] 输出目录: {workspace}")

    print("[export] 生成 full 包（包含 offline-packages）...")
    if full_path.exists():
        full_path.unlink()
    with tarfile.open(full_path, "w:gz") as tar:
        n = add_tree(tar, workspace, project_name, mini=False)
    print(f"[export] full entries={n} size_mb={full_path.stat().st_size / (1024*1024):.2f}")

    print("[export] 生成 mini 包（不包含 offline-packages）...")
    if mini_path.exists():
        mini_path.unlink()
    with tarfile.open(mini_path, "w:gz") as tar:
        n = add_tree(tar, workspace, project_name, mini=True)
    print(f"[export] mini entries={n} size_mb={mini_path.stat().st_size / (1024*1024):.2f}")

    for p in (full_path, mini_path):
        with tarfile.open(p, "r:gz") as tf:
            names = tf.getnames()
        flags = _verify_flags(names, project_name)
        print(f"[export] {p.name}\t{p.stat().st_size / (1024*1024):.2f} MB\tentries={len(names)}\t{flags}")

    print("[export] 完成。")


if __name__ == "__main__":
    main()
