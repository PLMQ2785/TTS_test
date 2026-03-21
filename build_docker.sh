#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

usage() {
    cat <<'EOF'
Usage:
  ./build_docker.sh sm86
  ./build_docker.sh sm89
  ./build_docker.sh sm120
  ./build_docker.sh all

Options:
  --no-cache   Build without Docker layer cache
  --max-jobs N Set MAX_JOBS for flash-attn build
  -h, --help   Show this help message

GPU mapping:
  sm86   RTX 3060 / 3090
  sm89   RTX 4090
  sm120  RTX 5090
EOF
}

if ! command -v docker >/dev/null 2>&1; then
    echo "docker command not found."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "docker compose is not available."
    exit 1
fi

TARGET="${1:-sm86}"
shift || true

NO_CACHE_ARGS=()
MAX_JOBS_VALUE="${MAX_JOBS:-8}"

while (($# > 0)); do
    case "$1" in
        --no-cache)
            NO_CACHE_ARGS+=(--no-cache)
            ;;
        --max-jobs)
            shift
            if (($# == 0)); then
                echo "--max-jobs requires a value."
                exit 1
            fi
            MAX_JOBS_VALUE="$1"
            ;;
        -h|--help)
            usage
            exit 0
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

build_one() {
    local arch="$1"
    local env_file=".env.docker.${arch}"

    if [[ ! -f "${env_file}" ]]; then
        echo "Missing env file: ${env_file}"
        exit 1
    fi

    echo "Building image for ${arch} using ${env_file} ..."
    MAX_JOBS="${MAX_JOBS_VALUE}" docker compose --env-file "${env_file}" build "${NO_CACHE_ARGS[@]}" --build-arg MAX_JOBS="${MAX_JOBS_VALUE}" api
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
    -h|--help)
        usage
        ;;
    *)
        echo "Unknown target: ${TARGET}"
        echo
        usage
        exit 1
        ;;
esac
