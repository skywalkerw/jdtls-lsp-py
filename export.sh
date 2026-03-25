#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[export] 项目目录: ${PROJECT_DIR}"
echo "[export] 使用 Python 生成 zip（与 export.bat / scripts/export_portable.py 一致）..."

if command -v python3 >/dev/null 2>&1; then
  python3 "${PROJECT_DIR}/scripts/export_portable.py"
elif command -v python >/dev/null 2>&1; then
  python "${PROJECT_DIR}/scripts/export_portable.py"
else
  echo "[export][error] 未找到 python3/python" >&2
  exit 1
fi
