#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
#  Qwen3-TTS Voice Clone API — Server Launcher
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_DIM=$'\033[2m'
    C_CYAN=$'\033[36m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'
else
    C_RESET=""
    C_BOLD=""
    C_DIM=""
    C_CYAN=""
    C_GREEN=""
    C_YELLOW=""
    C_RED=""
fi

print_banner() {
    echo
    echo "${C_CYAN}${C_BOLD}============================================================${C_RESET}"
    echo "${C_CYAN}${C_BOLD}  Qwen3-TTS Voice Clone API${C_RESET}"
    echo "${C_DIM}  Production Runtime Launcher${C_RESET}"
    echo "${C_CYAN}${C_BOLD}============================================================${C_RESET}"
    echo
}

print_step() {
    echo "${C_CYAN}${C_BOLD}[$1]${C_RESET} $2"
}

print_info() {
    echo "${C_DIM}- $1${C_RESET}"
}

print_success() {
    echo "${C_GREEN}OK${C_RESET} $1"
}

print_error() {
    echo "${C_RED}ERROR${C_RESET} $1"
}

# ── 설정값 ──
DEFAULT_PORT=8000
FIXED_MAX_JOBS=2

print_banner

# ── 1. uv 설치 확인 ──
print_step "1/5" "Runtime bootstrap 확인"
if ! command -v uv &>/dev/null; then
    print_info "uv가 설치되어 있지 않아 설치를 진행합니다."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"

    if ! command -v uv &>/dev/null; then
        print_error "uv 설치에 실패했습니다. 수동 설치 후 다시 실행해 주세요."
        exit 1
    fi
    print_success "uv 설치 완료: $(uv --version)"
else
    print_success "uv 확인됨: $(uv --version)"
fi

# ── 2. 기본 의존성 동기화 ──
print_step "2/5" "Python environment 동기화"
if [ ! -d ".venv" ]; then
    print_info "가상환경이 없어 새로 동기화합니다."
else
    print_info "기존 가상환경을 감지했습니다. 의존성 상태를 점검합니다."
fi

uv sync --inexact
print_success "기본 의존성 동기화 완료."

# ── 3. flash-attn 설치 ──
print_step "3/5" "flash-attn 빌드 상태 확인"
print_info "MAX_JOBS=${FIXED_MAX_JOBS} 기준으로 설치를 점검합니다."
MAX_JOBS="${FIXED_MAX_JOBS}" ./install_flash_attn.sh

# ── 4. 서버 포트 입력 ──
print_step "4/5" "서비스 포트 설정"
read -rp "🔌 서버 포트를 입력하세요 [기본: ${DEFAULT_PORT}]: " USER_PORT
PORT="${USER_PORT:-$DEFAULT_PORT}"

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    print_error "유효하지 않은 포트 번호: $PORT (1-65535 사이의 숫자를 입력하세요)"
    exit 1
fi

echo ""
print_step "5/5" "API server launch"
echo "${C_BOLD}Service Ready${C_RESET}"
echo "  Bind Address : http://0.0.0.0:${PORT}"
echo "  API Docs     : http://0.0.0.0:${PORT}/docs"
echo "  Reload Mode  : enabled"
echo ""

# ── 5. 서버 실행 ──
uv run uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
