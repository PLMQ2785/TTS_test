#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
#  Qwen3-TTS Voice Clone API — Server Launcher
# ─────────────────────────────────────────────

# ── 1. uv 설치 확인 ──
if ! command -v uv &>/dev/null; then
    echo "uv가 설치되어 있지 않습니다. 설치를 시작합니다..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo "uv 설치에 실패했습니다. 수동 설치 후 다시 실행해 주세요."
        exit 1
    fi
    echo "uv 설치 완료."
else
    echo "uv 확인됨: $(uv --version)"
fi

# ── 2. 가상환경 & 의존성 동기화 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "가상환경이 없습니다. uv sync 실행..."
    uv sync
    echo "가상환경 생성 및 의존성 설치 완료."
else
    echo "기존 가상환경 감지. uv sync로 의존성 확인..."
    uv sync
    echo "의존성 동기화 완료."
fi

# ── 3. 서버 포트 입력 ──
DEFAULT_PORT=8000
read -rp "🔌 서버 포트를 입력하세요 [기본: ${DEFAULT_PORT}]: " USER_PORT
PORT="${USER_PORT:-$DEFAULT_PORT}"

# 포트 번호 유효성 검사
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    echo "유효하지 않은 포트 번호: $PORT (1-65535 사이의 숫자를 입력하세요)"
    exit 1
fi

echo ""
echo "Qwen3-TTS Voice Clone API 서버를 시작합니다..."
echo "   URL: http://0.0.0.0:${PORT}"
echo "   Docs: http://0.0.0.0:${PORT}/docs"
echo ""

# ── 4. 서버 실행 ──
uv run uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
