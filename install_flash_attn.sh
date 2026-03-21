#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

FLASH_ATTN_SPEC="${FLASH_ATTN_SPEC:-flash-attn>=2.8.3}"
MAX_JOBS_VALUE="${MAX_JOBS:-2}"
FORCE_REINSTALL="${FORCE_REINSTALL_FLASH_ATTN:-0}"

if ! [[ "${MAX_JOBS_VALUE}" =~ ^[0-9]+$ ]] || [ "${MAX_JOBS_VALUE}" -lt 1 ]; then
    echo "유효하지 않은 MAX_JOBS 값: ${MAX_JOBS_VALUE}"
    exit 1
fi

if [ "${FORCE_REINSTALL}" != "1" ]; then
    if uv run python -c "import flash_attn" >/dev/null 2>&1; then
        echo "flash-attn이 이미 설치되어 있습니다. 건너뜁니다."
        exit 0
    fi
fi

echo "flash-attn 설치를 시도합니다... (spec=${FLASH_ATTN_SPEC}, MAX_JOBS=${MAX_JOBS_VALUE})"
MAX_JOBS="${MAX_JOBS_VALUE}" uv pip install -v --no-build-isolation "${FLASH_ATTN_SPEC}"
echo "flash-attn 설치 완료."
