#!/usr/bin/env bash

# Prefer a verified node-local cache, falling back to the original Conda env.
source "$(dirname -- "${BASH_SOURCE[0]}")/_activate_env.sh" || exit 1
activate_vllm_environment gemma4 || exit 1

vllm serve google/gemma-4-31B-it \
  --host 0.0.0.0 \
  --port 8001 \
  --max-model-len 12288 \
  --tensor-parallel-size 1 \
  --limit-mm-per-prompt '{"image": 0, "audio": 0}'
