"""JDTLS spawn and project root detection (aligned with LiteClaw jdtls.ts)."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT_MARKERS = ("pom.xml", "build.gradle", "build.gradle.kts", ".project", ".classpath")


def find_project_root(file_or_dir: str) -> Path:
    """Walk upward from file or directory until a Maven/Gradle/Eclipse marker is found."""
    p = Path(file_or_dir).resolve()
    if not p.exists():
        p = p.parent
    elif p.is_file():
        p = p.parent
    current = p
    while True:
        if current.is_dir():
            for marker in ROOT_MARKERS:
                if (current / marker).exists():
                    return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path(file_or_dir).resolve().parent if Path(file_or_dir).exists() else Path(".").resolve()


def _parse_java_major(stderr: str) -> int | None:
    m = re.search(r'version\s+"([^"]+)"', stderr, re.I)
    if not m:
        return None
    token = m.group(1)
    legacy = re.match(r"^1\.(\d+)", token)
    if legacy:
        return int(legacy.group(1))
    head = re.match(r"^(\d+)", token)
    return int(head.group(1)) if head else None


def check_java_version() -> tuple[bool, str | None]:
    try:
        r = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        stderr = (r.stderr or "") + (r.stdout or "")
        ver = _parse_java_major(stderr)
        if ver is None or ver < 21:
            return False, f"JDTLS requires Java 21+, found {ver or 'unknown'}"
        return True, None
    except Exception as e:
        return False, str(e)


def _default_jdtls_path() -> Path:
    home = Path.home()
    return Path(os.environ.get("LITECLAW_JDTLS_PATH", str(home / ".liteclaw" / "jdtls")))


def _find_launcher_jar(jdtls_root: Path) -> Path | None:
    plugins = jdtls_root / "plugins"
    if not plugins.is_dir():
        return None
    for f in sorted(plugins.glob("org.eclipse.equinox.launcher_*.jar")):
        return f
    return None


def _config_dir_name() -> str:
    system = platform.system()
    if system == "Darwin":
        return "config_mac"
    if system == "Windows":
        return "config_win"
    return "config_linux"


def spawn_jdtls(project_root: str, jdtls_path: Path | None = None) -> tuple[subprocess.Popen[Any], Path, Path]:
    """
    Start JDTLS process. Returns (process, temp data dir, launcher jar path).
    Caller must delete data_dir after shutdown.
    """
    ok, err = check_java_version()
    if not ok:
        raise RuntimeError(err or "Java check failed")

    root = jdtls_path or _default_jdtls_path()
    launcher = _find_launcher_jar(root)
    if launcher is None:
        raise RuntimeError(
            f"JDTLS not found under {root}. Install with LiteClaw: liteclaw lsp install-jdtls",
        )

    config_name = _config_dir_name()
    config_file = root / config_name
    if not config_file.is_dir():
        raise RuntimeError(f"Missing JDTLS config directory: {config_file}")

    data_dir = Path(tempfile.mkdtemp(prefix="liteclaw-jdtls-"))

    java_exe = "java.exe" if platform.system() == "Windows" else "java"
    args = [
        java_exe,
        "-jar",
        str(launcher),
        "-configuration",
        str(config_file),
        "-data",
        str(data_dir),
        "-Declipse.application=org.eclipse.jdt.ls.core.id1",
        "-Dosgi.bundles.defaultStartLevel=4",
        "-Declipse.product=org.eclipse.jdt.ls.core.product",
        "-Dlog.level=ALL",
        "--add-modules=ALL-SYSTEM",
        "--add-opens",
        "java.base/java.util=ALL-UNNAMED",
        "--add-opens",
        "java.base/java.lang=ALL-UNNAMED",
    ]
    proc = subprocess.Popen(
        args,
        cwd=project_root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("Failed to open JDTLS stdio pipes")
    return proc, data_dir, launcher
