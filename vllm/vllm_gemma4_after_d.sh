#!/usr/bin/env bash
set -euo pipefail

# Run inside the interactive shell opened by: d h200 2
# Usage: bash vllm_gemma4_after_d.sh {1|2|3} [LORA_PATH]

DEFAULT_LORA_PATH="/home2/sjyang114/Training/trained_models/lora_SFT_gemma4_E4B_260924_ddp_full"
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
WORKER_MODE=0
if [[ "${1:-}" == --worker ]]; then
  WORKER_MODE=1
  shift
fi

MODE="${1:-}"
LORA_PATH="${2:-${LORA_PATH:-$DEFAULT_LORA_PATH}}"
case "$MODE" in
  1|2) ;;
  3)
    [[ -f "$LORA_PATH/adapter_config.json" ]] || {
      echo "LoRA adapter_config.json not found: $LORA_PATH" >&2
      exit 1
    }
    ;;
  *)
    echo "Usage: bash $SCRIPT_PATH {1|2|3} [LORA_PATH]" >&2
    exit 1
    ;;
esac

[[ -n "${SLURM_JOB_ID:-}" ]] || {
  echo "First allocate two nodes with: d h200 2" >&2
  exit 1
}
mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
[[ ${#NODES[@]} -eq 2 ]] || {
  echo "This script needs an allocation of exactly two nodes." >&2
  exit 1
}
HEAD_NODE="${NODES[0]}"
WORKER_NODE="${NODES[1]}"
NODE="$(hostname -s)"
if (( WORKER_MODE )); then
  [[ "$NODE" == "$WORKER_NODE" ]] || { echo "Worker is on unexpected node: $NODE" >&2; exit 1; }
  RANK=1
  EXTRA_ARGS=(--headless)
else
  [[ "$NODE" == "$HEAD_NODE" ]] || { echo "Run this script on $HEAD_NODE, currently on $NODE" >&2; exit 1; }
  RANK=0
  EXTRA_ARGS=()
fi

source /home2/sjyang114/miniconda3/etc/profile.d/conda.sh
conda activate gemma4
export HF_HOME=/home2/sjyang114/.cache/huggingface
export HF_TOKEN_PATH="$HOME/.huggingface/token"

NODE_IP="$(getent ahostsv4 "$NODE" | awk 'NR == 1 {print $1}' || true)"
MASTER_IP="$(getent ahostsv4 "$HEAD_NODE" | awk 'NR == 1 {print $1}' || true)"
[[ -n "$NODE_IP" && -n "$MASTER_IP" ]] || {
  echo "Could not resolve compute node IP addresses" >&2
  exit 1
}
export VLLM_HOST_IP="$NODE_IP"
MASTER_PORT=$((20000 + SLURM_JOB_ID % 20000))

COMMON_ARGS=(
  --host 0.0.0.0
  --port 8001
  --max-model-len 12288
  --limit-mm-per-prompt '{"image": 0, "audio": 0}'
  --tensor-parallel-size 1
  --pipeline-parallel-size 2
  --distributed-executor-backend mp
  --nnodes 2
  --node-rank "$RANK"
  --master-addr "$MASTER_IP"
  --master-port "$MASTER_PORT"
)

case "$MODE" in
  1) MODEL=google/gemma-4-31B-it; MODEL_ARGS=() ;;
  2) MODEL=google/gemma-4-12B-it; MODEL_ARGS=() ;;
  3)
    LORA_DIR="$(realpath "$LORA_PATH")"
    MODEL="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["base_model_name_or_path"])' "$LORA_DIR/adapter_config.json")"
    LORA_NAME="$(basename "$LORA_DIR")"
    if [[ "$LORA_NAME" == checkpoint-* ]]; then
      LORA_NAME="$(basename "$(dirname "$LORA_DIR")")-$LORA_NAME"
    fi
    MODEL_ARGS=(--enable-lora --lora-modules "$LORA_NAME=$LORA_DIR")
    ;;
esac

if (( WORKER_MODE )); then
  echo "Worker: node=$NODE GPU=${CUDA_VISIBLE_DEVICES:-unset} model=$MODEL"
  exec vllm serve "$MODEL" "${COMMON_ARGS[@]}" "${MODEL_ARGS[@]}" "${EXTRA_ARGS[@]}"
fi

WORKER_PID=""
HEAD_PID=""
cleanup() {
  trap - EXIT INT TERM
  [[ -z "$HEAD_PID" ]] || kill "$HEAD_PID" 2>/dev/null || true
  [[ -z "$WORKER_PID" ]] || kill "$WORKER_PID" 2>/dev/null || true
  [[ -z "$HEAD_PID" ]] || wait "$HEAD_PID" 2>/dev/null || true
  [[ -z "$WORKER_PID" ]] || wait "$WORKER_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

CPUS_PER_NODE="${D_CPUS_PER_NODE:-${SLURM_CPUS_PER_TASK:-2}}"
srun --nodes=1 --ntasks=1 --nodelist="$WORKER_NODE" \
  --cpus-per-task="$CPUS_PER_NODE" --gres=gpu:1 \
  --exact --export=ALL --label \
  bash "$SCRIPT_PATH" --worker "$MODE" "$LORA_PATH" &
WORKER_PID=$!

echo "API server: node=$NODE GPU=${CUDA_VISIBLE_DEVICES:-unset} model=$MODEL"
vllm serve "$MODEL" "${COMMON_ARGS[@]}" "${MODEL_ARGS[@]}" "${EXTRA_ARGS[@]}" &
HEAD_PID=$!

set +e
wait -n "$HEAD_PID" "$WORKER_PID"
STATUS=$?
set -e
exit "$STATUS"
