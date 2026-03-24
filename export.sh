#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname "${PROJECT_DIR}")"
PROJECT_NAME="$(basename "${PROJECT_DIR}")"

FULL_ZIP="${WORKSPACE_DIR}/${PROJECT_NAME}-portable-full.zip"
MINI_ZIP="${WORKSPACE_DIR}/${PROJECT_NAME}-portable-mini.zip"

echo "[export] 项目目录: ${PROJECT_DIR}"
echo "[export] 输出目录: ${WORKSPACE_DIR}"

cd "${WORKSPACE_DIR}"

# 统一排除规则
EXCLUDES=(
  "${PROJECT_NAME}/.git/*"
  "${PROJECT_NAME}/.venv/*"
  "${PROJECT_NAME}/**/__pycache__/*"
  "${PROJECT_NAME}/**/*.egg-info/*"
  "${PROJECT_NAME}/**/*.pyc"
  "${PROJECT_NAME}/openjdk/*"
  "${PROJECT_NAME}/jdtls/*"
  "*/.DS_Store"
)

echo "[export] 生成 full 包（包含 offline-packages）..."
rm -f "${FULL_ZIP}"
zip -r "${FULL_ZIP}" "${PROJECT_NAME}" -x "${EXCLUDES[@]}"

echo "[export] 生成 mini 包（不包含 offline-packages）..."
rm -f "${MINI_ZIP}"
zip -r "${MINI_ZIP}" "${PROJECT_NAME}" -x "${EXCLUDES[@]}" "${PROJECT_NAME}/offline-packages/*"

python3 - <<PY
from pathlib import Path
import zipfile

files = [
    Path("${FULL_ZIP}"),
    Path("${MINI_ZIP}"),
]

for p in files:
    with zipfile.ZipFile(p, "r") as z:
        names = z.namelist()
    flags = {
        "offline-packages": any(n.startswith("${PROJECT_NAME}/offline-packages/") for n in names),
        "openjdk": any(n.startswith("${PROJECT_NAME}/openjdk/") for n in names),
        "jdtls": any(n.startswith("${PROJECT_NAME}/jdtls/") for n in names),
        "DS_Store": any(n.endswith(".DS_Store") for n in names),
    }
    print(f"[export] {p.name}\\t{p.stat().st_size / (1024*1024):.2f} MB\\tentries={len(names)}\\t{flags}")
PY

echo "[export] 完成。"
