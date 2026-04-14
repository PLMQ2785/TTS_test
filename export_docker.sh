#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

usage() {
    cat <<'EOF'
Usage:
  ./export_docker.sh sm86
  ./export_docker.sh sm89
  ./export_docker.sh sm120
  ./export_docker.sh --image qwen3-tts:cu130-sm86

Options:
  --image NAME   Export a specific image name instead of using arch presets
  --output PATH  Output file path
  --gzip         Export as .tar.gz
  -h, --help     Show this help message
EOF
}

if ! command -v docker >/dev/null 2>&1; then
    echo "docker command not found."
    exit 1
fi

TARGET=""
IMAGE_NAME=""
OUTPUT_PATH=""
USE_GZIP=0

while (($# > 0)); do
    case "$1" in
        --image)
            shift
            if (($# == 0)); then
                echo "--image requires a value."
                exit 1
            fi
            IMAGE_NAME="$1"
            ;;
        --output)
            shift
            if (($# == 0)); then
                echo "--output requires a value."
                exit 1
            fi
            OUTPUT_PATH="$1"
            ;;
        --gzip)
            USE_GZIP=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        sm86|sm89|sm120)
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

if [[ -z "${IMAGE_NAME}" ]]; then
    TARGET="${TARGET:-sm86}"
    case "${TARGET}" in
        sm86|sm89|sm120)
            IMAGE_NAME="qwen3-tts:cu130-${TARGET}"
            ;;
        *)
            echo "Unknown target: ${TARGET}"
            exit 1
            ;;
    esac
fi

if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
    echo "이미지를 찾을 수 없습니다: ${IMAGE_NAME}"
    exit 1
fi

if [[ -z "${OUTPUT_PATH}" ]]; then
    SAFE_NAME="${IMAGE_NAME//[:\/]/-}"
    if [[ "${USE_GZIP}" == "1" ]]; then
        OUTPUT_PATH="${SAFE_NAME}.tar.gz"
    else
        OUTPUT_PATH="${SAFE_NAME}.tar"
    fi
fi

echo "Exporting ${IMAGE_NAME} -> ${OUTPUT_PATH}"

if [[ "${USE_GZIP}" == "1" ]]; then
    docker save "${IMAGE_NAME}" | gzip > "${OUTPUT_PATH}"
else
    docker save -o "${OUTPUT_PATH}" "${IMAGE_NAME}"
fi

echo "Docker image export 완료."
