#!/usr/bin/env bash
set -euo pipefail

# Run on a GPU node. Select a GPU separate from the generation server, e.g.:
# CUDA_VISIBLE_DEVICES=1 PORT=18002 ./vllm/serve_qwen3_embedding_4b.sh
# Overrides: VLLM_ENV_NAME, MODEL_PATH (HF cache root or model directory),
# SERVED_MODEL_NAME, HOST, PORT, TENSOR_PARALLEL_SIZE, DTYPE,
# GPU_MEMORY_UTILIZATION, MAX_MODEL_LEN, MAX_NUM_SEQS, LOG_DIR.
# Additional arguments are forwarded to vllm serve.
# API: POST /v1/embeddings, model: Qwen/Qwen3-Embedding-4B.
# https://docs.vllm.ai/en/latest/models/pooling_models/embed/

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/.." && pwd)"
log_dir="${LOG_DIR:-${project_dir}/logs/qwen3_embedding_4b}"
mkdir -p -- "$log_dir"
log_file="${log_dir}/$(date +%Y%m%d_%H%M%S_%N)_$$.txt"
touch -- "$log_file"
ln -sfn -- "$(basename -- "$log_file")" "${log_dir}/latest.txt"
exec > >(tee -a -- "$log_file") 2>&1
export PYTHONUNBUFFERED=1
printf 'Started: %s\nLog file: %s\n' "$(date --iso-8601=seconds)" "$log_file"

# Hugging Face's models--... directory is a cache, not a model checkpoint.
# Resolve its main revision to the actual snapshot without downloading weights.
model_path="${MODEL_PATH:-/home2/sjyang114/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-4B}"
if [[ -f "$model_path/refs/main" ]]; then
  revision="$(cat -- "$model_path/refs/main")"
  model_path="$model_path/snapshots/$revision"
fi
if [[ ! -f "$model_path/config.json" ]]; then
  printf 'Error: model config not found: %s/config.json\nSet MODEL_PATH to a complete local model directory or HF cache root.\n' "$model_path" >&2
  exit 1
fi

# Default to one GPU; preserve a scheduler/user-provided CUDA_VISIBLE_DEVICES.
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES-0}"
if [[ -z "$CUDA_VISIBLE_DEVICES" || "$CUDA_VISIBLE_DEVICES" == '-1' ]]; then
  printf 'Error: no GPUs selected. Set CUDA_VISIBLE_DEVICES on a GPU node.\n' >&2
  exit 1
fi

source "${script_dir}/_activate_env.sh"
activate_vllm_environment "${VLLM_ENV_NAME:-next}"

printf 'Model: %s\nGPUs: %s\nTensor parallel size: %s\nEmbedding endpoint: http://%s:%s/v1/embeddings\n' \
  "$model_path" "$CUDA_VISIBLE_DEVICES" "${TENSOR_PARALLEL_SIZE:-1}" \
  "${HOST:-0.0.0.0}" "${PORT:-18002}"

# Pooling produces embedding vectors; generation/tool-call parsers are not used.
exec vllm serve "$model_path" \
  --served-model-name "${SERVED_MODEL_NAME:-Qwen/Qwen3-Embedding-4B}" \
  --runner pooling \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8002}" \
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE:-1}" \
  --dtype "${DTYPE:-auto}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.80}" \
  --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --max-num-seqs "${MAX_NUM_SEQS:-32}" \
  "$@"
