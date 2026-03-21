#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

FLASH_ATTN_WHEEL_DIR="${FLASH_ATTN_WHEEL_DIR:-${SCRIPT_DIR}/wheelhouse}"
FLASH_ATTN_WHEEL_ARCH="${FLASH_ATTN_WHEEL_ARCH:-}"
FORCE_REINSTALL="${FORCE_REINSTALL_FLASH_ATTN:-0}"

if [ "${FORCE_REINSTALL}" != "1" ]; then
    if uv run python -c "import flash_attn" >/dev/null 2>&1; then
        echo "flash-attn이 이미 설치되어 있습니다. 건너뜁니다."
        exit 0
    fi
fi

SEARCH_DIR="${FLASH_ATTN_WHEEL_DIR}"
if [ -n "${FLASH_ATTN_WHEEL_ARCH}" ]; then
    SEARCH_DIR="${FLASH_ATTN_WHEEL_DIR}/${FLASH_ATTN_WHEEL_ARCH}"
fi

if [ ! -d "${SEARCH_DIR}" ]; then
    echo "wheel 디렉터리를 찾을 수 없습니다: ${SEARCH_DIR}"
    echo "먼저 ./build_flash_attn_wheel.sh ${FLASH_ATTN_WHEEL_ARCH:-sm86} 를 실행해 주세요."
    exit 1
fi

WHEEL_PATH="$(find "${SEARCH_DIR}" -maxdepth 1 -type f -name 'flash_attn-*.whl' | sort | tail -n 1)"

if [ -z "${WHEEL_PATH}" ]; then
    echo "flash-attn wheel 파일을 찾을 수 없습니다: ${SEARCH_DIR}"
    echo "먼저 ./build_flash_attn_wheel.sh ${FLASH_ATTN_WHEEL_ARCH:-sm86} 를 실행해 주세요."
    exit 1
fi

echo "flash-attn wheel 설치를 시도합니다: ${WHEEL_PATH}"
if [ "${FORCE_REINSTALL}" = "1" ]; then
    uv pip install --reinstall --no-deps "${WHEEL_PATH}"
else
    uv pip install --no-deps "${WHEEL_PATH}"
fi
echo "flash-attn 설치 완료."
