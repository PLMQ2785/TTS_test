#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

usage() {
    cat <<'EOF'
Usage:
  ./build_flash_attn_wheel.sh sm86
  ./build_flash_attn_wheel.sh sm89
  ./build_flash_attn_wheel.sh sm120
  ./build_flash_attn_wheel.sh all

Options:
  --max-jobs N  Set MAX_JOBS for wheel build
  --force       Remove existing wheels before building
  -h, --help    Show this help message

GPU mapping:
  sm86   RTX 3060 / 3090
  sm89   RTX 4090
  sm120  RTX 5090
EOF
}

if ! command -v uv >/dev/null 2>&1; then
    echo "uv command not found."
    exit 1
fi

MAX_JOBS_VALUE="${MAX_JOBS:-2}"
FORCE_BUILD=0
FLASH_ATTN_SPEC="${FLASH_ATTN_SPEC:-flash-attn>=2.8.3}"
TARGET=""

while (($# > 0)); do
    case "$1" in
        --max-jobs)
            shift
            if (($# == 0)); then
                echo "--max-jobs requires a value."
                exit 1
            fi
            MAX_JOBS_VALUE="$1"
            ;;
        --force)
            FORCE_BUILD=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        sm86|sm89|sm120|all)
            if [[ -n "${TARGET}" ]]; then
                echo "Target can only be specified once."
                exit 1
            fi
            TARGET="$1"
            ;;
        *)
            echo "Unknown option: $1"
            echo
            usage
            exit 1
            ;;
    esac
    shift
done

if ! [[ "${MAX_JOBS_VALUE}" =~ ^[0-9]+$ ]] || [ "${MAX_JOBS_VALUE}" -lt 1 ]; then
    echo "유효하지 않은 MAX_JOBS 값: ${MAX_JOBS_VALUE}"
    exit 1
fi

TARGET="${TARGET:-sm86}"

uv sync

if [ ! -x ".venv/bin/python" ]; then
    echo "프로젝트 가상환경을 찾을 수 없습니다. uv sync가 정상적으로 완료되었는지 확인해 주세요."
    exit 1
fi

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "가상환경에 pip가 없어 ensurepip로 설치합니다..."
    .venv/bin/python -m ensurepip --upgrade
fi

build_one() {
    local arch="$1"
    local env_file=".env.docker.${arch}"
    local wheel_dir="wheelhouse/${arch}"

    if [[ ! -f "${env_file}" ]]; then
        echo "Missing env file: ${env_file}"
        exit 1
    fi

    # shellcheck disable=SC1090
    source "${env_file}"

    mkdir -p "${wheel_dir}"

    if [ "${FORCE_BUILD}" = "1" ]; then
        rm -f "${wheel_dir}"/flash_attn-*.whl
    fi

    echo "Building flash-attn wheel for ${arch} (TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}) ..."
    MAX_JOBS="${MAX_JOBS_VALUE}" \
    TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST}" \
    .venv/bin/python -m pip wheel --no-build-isolation --no-deps "${FLASH_ATTN_SPEC}" -w "${wheel_dir}"
}

case "${TARGET}" in
    sm86|sm89|sm120)
        build_one "${TARGET}"
        ;;
    all)
        build_one sm86
        build_one sm89
        build_one sm120
        ;;
esac
