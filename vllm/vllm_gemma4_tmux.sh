#!/usr/bin/env bash
set -uo pipefail

# vllm_gemma4.sh와 분리된 tmux/Slurm 자동 실행 전용 entrypoint.
# Usage: vllm_gemma4_tmux.sh MODE PORT STATUS_FILE SLOT GPU_ALIAS PARTITION

MODE="${1:-2}"
PORT="${2:-8001}"
STATUS_FILE="${3:-}"
SLOT="${4:-1}"
GPU_ALIAS="${5:-unknown}"
PARTITION="${6:-${SLURM_JOB_PARTITION:-unknown}}"

MODEL=""
case "$MODE" in
  1) MODEL="google/gemma-4-31B-it" ;;
  2) MODEL="google/gemma-4-12B-it" ;;
  3) MODEL="google/gemma-4-31B-it" ;;
  *)
    echo "Usage: $0 {1|2|3} PORT STATUS_FILE SLOT GPU_ALIAS PARTITION" >&2
    exit 2
    ;;
esac

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "Invalid port: $PORT" >&2
  exit 2
fi

source /home2/sjyang114/miniconda3/etc/profile.d/conda.sh
conda activate gemma4
export HF_HOME=/home2/sjyang114/.cache/huggingface

NODE="$(hostname -s)"
URL="http://${NODE}:${PORT}/v1"
SERVER_PID=""
FINAL_STATE="EXITED"

write_status() {
  local state="$1"
  local detail="${2:-}"
  [[ -n "$STATUS_FILE" ]] || return 0
  local tmp_file="${STATUS_FILE}.tmp.$$"
  {
    printf 'STATE=%s\n' "$state"
    printf 'SLOT=%s\n' "$SLOT"
    printf 'GPU_ALIAS=%s\n' "$GPU_ALIAS"
    printf 'PARTITION=%s\n' "$PARTITION"
    printf 'NODE=%s\n' "$NODE"
    printf 'PORT=%s\n' "$PORT"
    printf 'URL=%s\n' "$URL"
    printf 'MODEL=%s\n' "$MODEL"
    printf 'JOB_ID=%s\n' "${SLURM_JOB_ID:-unknown}"
    printf 'DETAIL=%s\n' "$detail"
    printf 'UPDATED_AT=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  } > "$tmp_file"
  mv -f "$tmp_file" "$STATUS_FILE"
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if (( exit_code == 130 || exit_code == 143 )); then
    FINAL_STATE="STOPPED"
  elif (( exit_code != 0 )); then
    FINAL_STATE="FAILED"
  fi
  write_status "$FINAL_STATE" "exit_code=$exit_code"
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

COMMON_ARGS=(
  --host 0.0.0.0
  --port "$PORT"
  --max-model-len 12288
  --tensor-parallel-size 1
  --limit-mm-per-prompt '{"image": 0, "audio": 0}'
)

write_status "LOADING" "vLLM process starting"
echo "[slot $SLOT] allocated: job=${SLURM_JOB_ID:-unknown} gpu=$GPU_ALIAS partition=$PARTITION node=$NODE port=$PORT"
echo "[slot $SLOT] endpoint: $URL"

if [[ "$MODE" == "3" ]]; then
  LORA_PATH="${LORA_PATH:-/path/to/your/lora_adapter}"
  vllm serve "$MODEL" \
    "${COMMON_ARGS[@]}" \
    --enable-lora \
    --lora-modules "sft-lora=$LORA_PATH" &
else
  vllm serve "$MODEL" "${COMMON_ARGS[@]}" &
fi
SERVER_PID=$!

while kill -0 "$SERVER_PID" 2>/dev/null; do
  if curl --silent --fail --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    write_status "READY" "health check passed"
    echo "[slot $SLOT] READY: $URL"
    break
  fi
  sleep 2
done

wait "$SERVER_PID"
server_exit=$?
SERVER_PID=""
if (( server_exit != 0 )); then
  FINAL_STATE="FAILED"
fi
exit "$server_exit"
