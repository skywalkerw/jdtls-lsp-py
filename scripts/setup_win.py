#!/usr/bin/env python3
"""
Windows 初始化脚本：与 setup.sh 行为对齐。
解压 offline-packages 中的 OpenJDK（文件名含 win）与 JDTLS，再尝试全局 pip install -e .，失败则创建 .venv。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
OFFLINE_DIR = PROJECT_DIR / "offline-packages"
JDTLS_TARGET = PROJECT_DIR / "jdtls"
OPENJDK_TARGET = PROJECT_DIR / "openjdk"
VENV_DIR = PROJECT_DIR / ".venv"


def log(msg: str) -> None:
    print(f"[setup] {msg}")


def warn(msg: str) -> None:
    print(f"[setup][warn] {msg}")


def err(msg: str) -> None:
    print(f"[setup][error] {msg}", file=sys.stderr)


def pick_python_cmd() -> list[str]:
    for args in (["py", "-3"], ["python"]):
        try:
            r = subprocess.run(
                [*args, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode == 0:
                return args
        except FileNotFoundError:
            continue
    err("未找到 Python。请安装 Python 3.10+，并确保 py 或 python 在 PATH 中。")
    raise SystemExit(1)


def check_python_version(py_cmd: list[str]) -> None:
    code = """
import sys
major, minor = sys.version_info[:2]
if (major, minor) < (3, 10):
    raise SystemExit("需要 Python 3.10+，当前版本: " + sys.version.split()[0])
print("Python 版本检查通过:", sys.version.split()[0])
"""
    r = subprocess.run([*py_cmd, "-c", code], capture_output=True, text=True)
    if r.returncode != 0:
        err((r.stderr or r.stdout or "Python 版本检查失败").strip())
        raise SystemExit(1)
    log(r.stdout.strip())


def _parse_java_major(text: str) -> int | None:
    m = re.search(r'version\s+"([^"]+)"', text, re.I)
    if not m:
        return None
    token = m.group(1)
    legacy = re.match(r"^1\.(\d+)", token)
    if legacy:
        return int(legacy.group(1))
    head = re.match(r"^(\d+)", token)
    return int(head.group(1)) if head else None


def check_java(java_exe: str) -> None:
    r = subprocess.run([java_exe, "-version"], capture_output=True, text=True)
    out = (r.stderr or "") + (r.stdout or "")
    ver = _parse_java_major(out)
    if ver is None or ver < 21:
        err(f"需要 Java 21+，当前解析版本: {ver or 'unknown'}")
        raise SystemExit(1)
    log(f"Java 版本检查通过: {ver}")


def _is_zip(p: Path) -> bool:
    return p.suffix.lower() == ".zip"


def _is_tarball(p: Path) -> bool:
    n = p.name.lower()
    return n.endswith(".tar.gz") or n.endswith(".tgz")


def pick_openjdk_archive() -> Path | None:
    if not OFFLINE_DIR.is_dir():
        return None
    for p in sorted(OFFLINE_DIR.iterdir()):
        if not p.is_file():
            continue
        n = p.name.lower()
        if not any(x in n for x in ("openjdk", "jdk")):
            continue
        if "win" not in n and "windows" not in n:
            continue
        if _is_zip(p) or _is_tarball(p):
            return p
    return None


def pick_jdtls_archive() -> Path | None:
    if not OFFLINE_DIR.is_dir():
        return None
    for p in sorted(OFFLINE_DIR.iterdir()):
        if not p.is_file():
            continue
        n = p.name.lower()
        if "jdtls" not in n:
            continue
        if _is_zip(p) or _is_tarball(p):
            return p
    return None


def _extract_zip(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


def _extract_tar(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(dest)


def find_java_bin_under(root: Path) -> Path | None:
    for name in ("java.exe", "java"):
        for p in root.rglob(name):
            try:
                if p.is_file() and p.parent.name.lower() == "bin":
                    return p
            except OSError:
                continue
    return None


def install_openjdk(archive: Path) -> str | None:
    tmp = Path(tempfile.mkdtemp(prefix="jdtls-lsp-openjdk-"))
    try:
        log(f"正在解压离线 OpenJDK 包: {archive}")
        if _is_zip(archive):
            _extract_zip(archive, tmp)
        else:
            _extract_tar(archive, tmp)
        java_p = find_java_bin_under(tmp)
        if not java_p:
            warn("OpenJDK 已解压，但未找到 bin/java.exe。")
            return None
        jdk_root = java_p.parent.parent
        if OPENJDK_TARGET.exists():
            shutil.rmtree(OPENJDK_TARGET, ignore_errors=True)
        shutil.move(str(jdk_root), str(OPENJDK_TARGET))
        java_exe = str(OPENJDK_TARGET / "bin" / "java.exe")
        os.environ["JAVA_HOME"] = str(OPENJDK_TARGET)
        bin_dir = str(OPENJDK_TARGET / "bin")
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        log(f"OpenJDK 已安装到: {OPENJDK_TARGET}")
        log(f"当前会话优先使用: {java_exe}")
        return java_exe
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def find_jdtls_root(extracted: Path) -> Path | None:
    plugins = extracted / "plugins"
    if plugins.is_dir():
        if list(plugins.glob("org.eclipse.equinox.launcher_*.jar")):
            return extracted
    for jar in extracted.rglob("org.eclipse.equinox.launcher_*.jar"):
        return jar.parent.parent
    return None


def install_jdtls(archive: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="jdtls-lsp-jdtls-"))
    try:
        log(f"正在解压离线 JDTLS 包: {archive}")
        if _is_zip(archive):
            _extract_zip(archive, tmp)
        else:
            _extract_tar(archive, tmp)
        inner = find_jdtls_root(tmp)
        if not inner:
            warn("压缩包已解压，但未识别到标准 JDTLS 目录结构。")
            return
        if JDTLS_TARGET.exists():
            shutil.rmtree(JDTLS_TARGET, ignore_errors=True)
        shutil.move(str(inner), str(JDTLS_TARGET))
        log(f"JDTLS 已安装到: {JDTLS_TARGET}")
        log("已完成 JDTLS 离线安装。")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def venv_python() -> Path:
    win_py = VENV_DIR / "Scripts" / "python.exe"
    if win_py.is_file():
        return win_py
    nix_py = VENV_DIR / "bin" / "python"
    if nix_py.is_file():
        return nix_py
    return win_py


def install_python_package(py_cmd: list[str]) -> None:
    log(f"优先尝试全局安装: {' '.join(py_cmd)} -m pip install -e .")
    r1 = subprocess.run([*py_cmd, "-m", "pip", "install", "--upgrade", "pip"], cwd=PROJECT_DIR)
    r2 = subprocess.run([*py_cmd, "-m", "pip", "install", "-e", str(PROJECT_DIR)], cwd=PROJECT_DIR)
    if r1.returncode == 0 and r2.returncode == 0:
        log("全局安装成功。可直接使用: jdtls-lsp")
        return

    warn("全局安装失败，回退到项目虚拟环境（常见原因：PEP 668 / externally-managed-environment）。")
    vp = venv_python()
    if not vp.is_file():
        log(f"创建项目虚拟环境: {VENV_DIR}")
        subprocess.run([*py_cmd, "-m", "venv", str(VENV_DIR)], cwd=PROJECT_DIR, check=True)
        vp = venv_python()
    log(f"虚拟环境安装: {vp} -m pip install -e .")
    subprocess.run([str(vp), "-m", "pip", "install", "--upgrade", "pip"], cwd=PROJECT_DIR, check=True)
    subprocess.run([str(vp), "-m", "pip", "install", "-e", str(PROJECT_DIR)], cwd=PROJECT_DIR, check=True)
    log("Python 包安装完成（venv）。")
    scripts = VENV_DIR / "Scripts"
    if (scripts / "jdtls-lsp.exe").is_file():
        log(f"可执行: {scripts / 'jdtls-lsp.exe'}")
    else:
        log(f"可执行: {VENV_DIR / 'bin' / 'jdtls-lsp'}")


def main() -> None:
    os.chdir(PROJECT_DIR)
    log(f"项目目录: {PROJECT_DIR}")

    py_cmd = pick_python_cmd()
    check_python_version(py_cmd)

    java_exe = "java.exe"
    jdk_arc = pick_openjdk_archive()
    if jdk_arc:
        j = install_openjdk(jdk_arc)
        if j:
            java_exe = j
    else:
        warn("未找到 Windows 离线 OpenJDK 压缩包（文件名需包含 windows 或 win）。")
        warn(f"请将包放到: {OFFLINE_DIR}")

    jdtls_arc = pick_jdtls_archive()
    if jdtls_arc:
        install_jdtls(jdtls_arc)
    else:
        warn(f"在 {OFFLINE_DIR} 下未找到离线 JDTLS 压缩包。")

    check_java(java_exe)
    install_python_package(py_cmd)

    log("初始化完成。")
    log(f"提示：可设置 JAVA_HOME={OPENJDK_TARGET} 并将 %JAVA_HOME%\\bin 加入 PATH。")


if __name__ == "__main__":
    main()
