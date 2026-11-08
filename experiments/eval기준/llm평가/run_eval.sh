#!/usr/bin/env bash
set -euo pipefail
EVAL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# 아래 설정만 수정한 뒤 ./run_eval.sh 실행 (명령행 옵션 불필요)
EVAL_MODE="vllm"                     # codex | vllm
INPUT_FILE="$EVAL_DIR/입력예시_03_response_1.json"
CRITERIA="all"                       # all 또는 01,02,03,04 (쉼표 구분)
OUTPUT_DIR="$EVAL_DIR/results"
DRY_RUN="false"                      # true: 입력·프롬프트 구성만 확인, 모델 호출 없음
TIMEOUT_SECONDS="600"                # 평가 항목 한 개의 호출 제한
PYTHON_BIN="python3"

CODEX_BIN="codex"
CODEX_MODEL="gpt-6-luna"
CODEX_EFFORT="max"

VLLM_URL="http://127.0.0.1:18001/v1"
VLLM_MODEL="Qwen/Qwen3.8-Flash-Next-FP8" # 서버의 /v1/models에 등록된 이름
VLLM_TEMPERATURE="0"
VLLM_MAX_TOKENS="8192"
VLLM_API_KEY_ENV=""                  # 인증 서버라면 API 키가 있는 환경변수 이름

if (( $# )); then
  echo "옵션은 sh 상단에서 설정하세요. 실행: ./run_eval.sh" >&2
  exit 2
fi
export EVAL_MODE INPUT_FILE CRITERIA OUTPUT_DIR DRY_RUN TIMEOUT_SECONDS
export CODEX_BIN CODEX_MODEL CODEX_EFFORT
export VLLM_URL VLLM_MODEL VLLM_TEMPERATURE VLLM_MAX_TOKENS VLLM_API_KEY_ENV
exec "$PYTHON_BIN" "$EVAL_DIR/run_eval.py"
