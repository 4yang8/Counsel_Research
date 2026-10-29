#!/usr/bin/env bash

set -euo pipefail

# Official recipe: https://recipes.vllm.ai/Qwen/Qwen3.8-27B
# FP8 weights are detected from the checkpoint's quantization_config.
# --language-model-only disables image/video inputs and skips the vision encoder.
# --reasoning-parser qwen3 separates thinking content from the final answer;
# it does not disable thinking mode.
# Use every GPU visible to CUDA, honoring the caller's GPU selection.
# conda activate를 shell script에서 사용할 수 있도록 설정 
source /home2/sjyang114/miniconda3/etc/profile.d/conda.sh || exit 1

# 기존 `gemma4` alias와 동일 
conda activate gemma4 || exit 1

# 기존 `HF` alias와 동일 
export HF_HOME=/home2/sjyang114/.cache/huggingface

NUM_GPUS="$(python -c 'import torch; print(torch.cuda.device_count())')"
if (( NUM_GPUS < 1 )); then
  echo "No CUDA GPUs detected." >&2
  exit 1
fi

exec vllm serve Qwen/Qwen3.8-27B-FP8 \
  --host 0.0.0.0 \
  --port 8001 \
  --max-model-len 32768 \
  --tensor-parallel-size "${NUM_GPUS}" \
  --language-model-only \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --default-chat-template-kwargs '{"reasoning_effort":"low"}'
