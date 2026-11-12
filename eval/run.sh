#!/usr/bin/env bash
set -euo pipefail
EVAL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${EVAL_PYTHON:-}" ]]; then
  PYTHON_BIN="$EVAL_PYTHON"
elif [[ -x /home2/sjyang114/miniconda3/envs/NLP/bin/python ]]; then
  PYTHON_BIN=/home2/sjyang114/miniconda3/envs/NLP/bin/python
else
  PYTHON_BIN=python3
fi
exec "$PYTHON_BIN" "$EVAL_DIR/run.py" "$@"
