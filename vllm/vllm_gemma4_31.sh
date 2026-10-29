#!/usr/bin/env bash

vllm serve google/gemma-4-31B-it \
  --host 0.0.0.0 \
  --port 8001 \
  --max-model-len 12288 \
  --tensor-parallel-size 1 \
  --limit-mm-per-prompt '{"image": 0, "audio": 0}'
