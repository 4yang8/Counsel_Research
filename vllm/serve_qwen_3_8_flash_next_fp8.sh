#!/usr/bin/env bash

set -euo pipefail

# Save stdout and stderr while keeping terminal output and vLLM's exit status.
# Use the script directory so logs have a stable location from any working dir.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "${script_dir}/.." && pwd)"
log_dir="${LOG_DIR:-${project_dir}/logs/qwen3_8_flash_next_fp8}"
mkdir -p -- "$log_dir"
log_file="${log_dir}/$(date +%Y%m%d_%H%M%S_%N)_$$.txt"
touch -- "$log_file"
ln -sfn -- "$(basename -- "$log_file")" "${log_dir}/latest.txt"
exec > >(tee -a -- "$log_file") 2>&1
export PYTHONUNBUFFERED=1
printf 'Started: %s\nLog file: %s\n' "$(date --iso-8601=seconds)" "$log_file"

# Activate the qwen38-flash-next conda environment before running this script.
# Official recipe: https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next
# Requires vLLM >=0.30.0; the official recipe recommends nightly.
# FP8 weights are detected from the checkpoint's quantization_config.
# Default: all detected GPUs. Set CUDA_VISIBLE_DEVICES to select specific GPUs.
# Expert parallelism is required for this FP8 checkpoint when using eight GPUs.
# N-gram embedding CPU offload needs at least 51 GB of host RAM plus headroom.
# Default to Marlin W8A16 for Ampere (SM 8.6), which lacks native FP8 compute.
# Keep KV caches in the model dtype and the QSA indexer in BF16 by default.
# The QSA indexer's FP8 path uses FP8 dot products, unsuitable for Ampere.
# These cache settings do not change the checkpoint's FP8 weight format.
# Override MOE_BACKEND, KV_CACHE_DTYPE, and INDEXER_KV_DTYPE for other GPUs.
# --language-model-only disables image/video inputs and skips the vision encoder.
# --reasoning-parser qwen3 separates thinking content from the final answer;
# it does not disable thinking mode.
if [[ ! ${CUDA_VISIBLE_DEVICES+x} ]]; then
  if ! detected_gpus="$(nvidia-smi --query-gpu=uuid --format=csv,noheader)"; then
    printf 'Error: failed to detect GPUs with nvidia-smi.\n' >&2
    exit 1
  fi
  # UUIDs avoid dependence on the CUDA device ordering of numeric GPU indices.
  export CUDA_VISIBLE_DEVICES="$(printf '%s\n' "$detected_gpus" | paste -sd, -)"
fi
if [[ -z "$CUDA_VISIBLE_DEVICES" || "$CUDA_VISIBLE_DEVICES" == '-1' ]]; then
  printf 'Error: no GPUs selected or detected.\n' >&2
  exit 1
fi
export CUDA_VISIBLE_DEVICES
IFS=',' read -r -a gpu_ids <<< "$CUDA_VISIBLE_DEVICES"
printf 'GPUs: %s\nCUDA_HOME: %s\nFlashInfer nvcc: %s\nAllocator: %s\n' \
  "$CUDA_VISIBLE_DEVICES" "${CUDA_HOME:-auto}" \
  "${FLASHINFER_NVCC:-auto}" "${PYTORCH_ALLOC_CONF:-${PYTORCH_CUDA_ALLOC_CONF:-default}}"
printf 'Tensor parallel size: %s\nGPU memory utilization: %s\nMax sequences: %s\nMax model length: %s\n' \
  "${TENSOR_PARALLEL_SIZE:-${#gpu_ids[@]}}" "${GPU_MEMORY_UTILIZATION:-0.90}" \
  "${MAX_NUM_SEQS:-64}" "${MAX_MODEL_LEN:-32768}"

# Leave more of the memory budget for KV cache and reduce per-request buffers.
exec vllm serve Qwen/Qwen3.8-Flash-Next-FP8 \
  --served-model-name "${SERVED_MODEL_NAME:-Qwen/Qwen3.8-Flash-Next-FP8}" \
  --host 0.0.0.0 \
  --port "${PORT:-8002}" \
  --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE:-${#gpu_ids[@]}}" \
  --enable-expert-parallel \
  --moe-backend "${MOE_BACKEND:-marlin}" \
  --dtype bfloat16 \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.90}" \
  --max-num-seqs "${MAX_NUM_SEQS:-64}" \
  --enable-prefix-caching \
  --kv-cache-dtype "${KV_CACHE_DTYPE:-auto}" \
  --attention-config.indexer_kv_dtype "${INDEXER_KV_DTYPE:-bf16}" \
  --engram-config '{"cpu_offload":true}' \
  --language-model-only \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --default-chat-template-kwargs '{"reasoning_effort":"low"}' \
  "$@"
