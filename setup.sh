#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OFFLINE_DIR="${PROJECT_DIR}/offline-packages"
JDTLS_TARGET="${PROJECT_DIR}/jdtls"
OPENJDK_TARGET="${PROJECT_DIR}/openjdk"
JAVA_BIN="${JAVA_BIN:-java}"
VENV_DIR="${PROJECT_DIR}/.venv"

log() {
  printf '[setup] %s\n' "$1"
}

warn() {
  printf '[setup][warn] %s\n' "$1"
}

err() {
  printf '[setup][error] %s\n' "$1" >&2
}

require_cmd() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    err "缺少命令: ${cmd}。${hint}"
    exit 1
  fi
}

pick_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  err "未找到 Python，请先安装 Python 3.10+。"
  exit 1
}

check_python_version() {
  local py="$1"
  "${py}" - <<'PY'
import sys
major, minor = sys.version_info[:2]
if (major, minor) < (3, 10):
    raise SystemExit("需要 Python 3.10+，当前版本: %s" % sys.version.split()[0])
print("Python 版本检查通过:", sys.version.split()[0])
PY
}

check_java_version() {
  if ! command -v "${JAVA_BIN}" >/dev/null 2>&1; then
    err "未找到 Java。请安装 Java 21+，并确保 '${JAVA_BIN}' 可执行。"
    exit 1
  fi

  local out
  out="$("${JAVA_BIN}" -version 2>&1 || true)"
  if [[ -z "${out}" ]]; then
    err "执行 '${JAVA_BIN} -version' 失败。"
    exit 1
  fi

  local ver raw major
  raw="$(printf '%s\n' "${out}" | awk -F\" '/version/ {print $2; exit}')"
  if [[ -z "${raw}" ]]; then
    err "无法解析 Java 版本信息: ${out}"
    exit 1
  fi

  if [[ "${raw}" =~ ^1\.([0-9]+) ]]; then
    major="${BASH_REMATCH[1]}"
  elif [[ "${raw}" =~ ^([0-9]+) ]]; then
    major="${BASH_REMATCH[1]}"
  else
    err "无法解析 Java 主版本号: '${raw}'"
    exit 1
  fi

  if (( major < 21 )); then
    err "需要 Java 21+，当前版本: ${raw}"
    exit 1
  fi

  log "Java 版本检查通过: ${raw}"
}

install_python_package() {
  local py="$1"
  log "优先尝试全局安装: ${py} -m pip install -e ."
  if "${py}" -m pip install --upgrade pip && "${py}" -m pip install -e "${PROJECT_DIR}"; then
    log "全局安装成功。可直接使用: jdtls-lsp"
    return
  fi

  warn "全局安装失败，回退到项目虚拟环境（常见原因：PEP 668 / externally-managed-environment）。"
  local use_py="${py}"
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    log "创建项目虚拟环境: ${VENV_DIR}"
    "${py}" -m venv "${VENV_DIR}"
  fi
  use_py="${VENV_DIR}/bin/python"
  log "虚拟环境安装: ${use_py} -m pip install -e ."
  "${use_py}" -m pip install --upgrade pip
  "${use_py}" -m pip install -e "${PROJECT_DIR}"
  log "Python 包安装完成（venv）。"
  log "可执行: ${VENV_DIR}/bin/jdtls-lsp"
}

pick_jdtls_archive() {
  if [[ ! -d "${OFFLINE_DIR}" ]]; then
    return
  fi

  local f
  for f in "${OFFLINE_DIR}"/*; do
    [[ -e "${f}" ]] || continue
    case "${f}" in
      *jdtls*.tar.gz|*jdtls*.tgz|*jdtls*.zip|*JDTLS*.tar.gz|*JDTLS*.tgz|*JDTLS*.zip)
        echo "${f}"
        return
        ;;
    esac
  done
}

detect_os_tag() {
  local u
  u="$(uname -s 2>/dev/null || true)"
  case "${u}" in
    Linux*) echo "linux" ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT*) echo "windows" ;;
    Darwin*) echo "mac" ;;
    *) echo "unknown" ;;
  esac
}

pick_openjdk_archive() {
  if [[ ! -d "${OFFLINE_DIR}" ]]; then
    return
  fi

  local os_tag
  os_tag="$(detect_os_tag)"
  local f base
  for f in "${OFFLINE_DIR}"/*; do
    [[ -e "${f}" ]] || continue
    base="$(basename "${f}")"
    case "${base}" in
      *openjdk*|*OpenJDK*|*jdk*)
        case "${os_tag}" in
          linux)
            [[ "${base}" == *linux* ]] || continue
            ;;
          windows)
            [[ "${base}" == *windows* || "${base}" == *win* ]] || continue
            ;;
          mac)
            [[ "${base}" == *mac* || "${base}" == *osx* ]] || continue
            ;;
          *)
            continue
            ;;
        esac
        case "${base}" in
          *.tar.gz|*.tgz|*.zip)
            echo "${f}"
            return
            ;;
        esac
        ;;
    esac
  done
}

install_openjdk_from_archive() {
  local archive="$1"
  local temp
  temp="$(mktemp -d "${TMPDIR:-/tmp}/offline-openjdk-XXXXXX")"

  log "正在解压离线 OpenJDK 包: ${archive}"
  case "${archive}" in
    *.tar.gz|*.tgz)
      tar -xzf "${archive}" -C "${temp}"
      ;;
    *.zip)
      require_cmd unzip "请先安装 unzip 后重试。"
      unzip -q "${archive}" -d "${temp}"
      ;;
    *)
      err "不支持的 OpenJDK 压缩包格式: ${archive}"
      rm -rf "${temp}"
      exit 1
      ;;
  esac

  mkdir -p "$(dirname "${OPENJDK_TARGET}")"
  rm -rf "${OPENJDK_TARGET}"

  local java_path=""
  java_path="$(find "${temp}" -type f -name java -path '*/bin/java' -print -quit || true)"
  if [[ -z "${java_path}" ]]; then
    java_path="$(find "${temp}" -type f -name java.exe -path '*/bin/java.exe' -print -quit || true)"
  fi
  if [[ -z "${java_path}" ]]; then
    warn "OpenJDK 已解压，但未找到 bin/java。请手动检查 ${temp}。"
    return
  fi

  local jdk_root
  jdk_root="$(dirname "$(dirname "${java_path}")")"
  mv "${jdk_root}" "${OPENJDK_TARGET}"
  rm -rf "${temp}"

  if [[ -x "${OPENJDK_TARGET}/bin/java" ]]; then
    JAVA_BIN="${OPENJDK_TARGET}/bin/java"
  elif [[ -x "${OPENJDK_TARGET}/bin/java.exe" ]]; then
    JAVA_BIN="${OPENJDK_TARGET}/bin/java.exe"
  fi
  export JAVA_HOME="${OPENJDK_TARGET}"
  export PATH="${OPENJDK_TARGET}/bin:${PATH}"

  log "OpenJDK 已安装到: ${OPENJDK_TARGET}"
  log "当前会话优先使用: ${JAVA_BIN}"
}

extract_archive_to_dir() {
  local archive="$1"
  local target="$2"
  local temp
  temp="$(mktemp -d "${TMPDIR:-/tmp}/liteclaw-jdtls-XXXXXX")"

  log "正在解压离线 JDTLS 包: ${archive}"
  case "${archive}" in
    *.tar.gz|*.tgz)
      tar -xzf "${archive}" -C "${temp}"
      ;;
    *.zip)
      require_cmd unzip "请先安装 unzip 后重试。"
      unzip -q "${archive}" -d "${temp}"
      ;;
    *)
      err "不支持的压缩包格式: ${archive}"
      rm -rf "${temp}"
      exit 1
      ;;
  esac

  mkdir -p "$(dirname "${target}")"
  rm -rf "${target}"

  # Prefer extracted root if it already looks like jdtls layout.
  if [[ -d "${temp}/plugins" ]] || compgen -G "${temp}/plugins/org.eclipse.equinox.launcher_*.jar" >/dev/null; then
    mv "${temp}" "${target}"
    log "JDTLS 已安装到: ${target}"
    return
  fi

  # Otherwise locate first directory containing plugins + launcher.
  local candidate
  candidate="$(find "${temp}" -type f -name 'org.eclipse.equinox.launcher_*.jar' -print -quit || true)"
  if [[ -n "${candidate}" ]]; then
    candidate="$(dirname "$(dirname "${candidate}")")"
    mv "${candidate}" "${target}"
    rm -rf "${temp}"
    log "JDTLS 已安装到: ${target}"
    return
  fi

  warn "压缩包已解压，但未识别到标准 JDTLS 目录结构。"
  warn "请检查 ${temp} 下内容，并把正确目录移动到 ${target}"
}

main() {
  log "项目目录: ${PROJECT_DIR}"

  local py
  py="$(pick_python)"
  check_python_version "${py}"

  local jdk_archive
  jdk_archive="$(pick_openjdk_archive || true)"
  if [[ -n "${jdk_archive}" ]]; then
    install_openjdk_from_archive "${jdk_archive}"
  else
    warn "未找到当前系统可用的离线 OpenJDK 压缩包（支持 Linux/Windows/macOS）。"
    warn "如需离线安装，请放到 ${OFFLINE_DIR}，文件名需包含系统标识（linux/windows/mac 或 osx）。"
  fi

  local archive
  archive="$(pick_jdtls_archive || true)"
  if [[ -n "${archive}" ]]; then
    extract_archive_to_dir "${archive}" "${JDTLS_TARGET}"
    log "已完成 JDTLS 离线安装。"
  else
    warn "在 ${OFFLINE_DIR} 下未找到离线 JDTLS 压缩包。"
    warn "安装方式："
    warn "1) 先把 JDTLS 压缩包放到 offline-packages/，再重新执行本脚本"
    warn "2) 手动下载 JDTLS 并解压到 ${JDTLS_TARGET}（推荐）"
    warn "   或设置 LITECLAW_JDTLS_PATH 指向你的 JDTLS 目录"
    warn "   （目录必须包含 config_mac|config_linux|config_win 和 plugins/org.eclipse.equinox.launcher_*.jar）"
  fi

  check_java_version
  install_python_package "${py}"

  log "初始化完成。"
  log "提示：如需长期使用离线 OpenJDK，可将 JAVA_HOME 设为 ${OPENJDK_TARGET} 并把 \$JAVA_HOME/bin 加入 PATH。"
}

main "$@"
