#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# 인공 대화만 분석. 실제 Q3 실험의 run_similarity.sh 설정은 변경하지 않습니다.
INPUT_PATHS=("$SCRIPT_DIR/sessions")
MAX_TURNS=20
VERSIONS=("인공_다양성유지" "인공_반응수렴")
EMBEDDING_URL="http://192.168.0.32:8002/v1"
EMBEDDING_MODEL="Qwen/Qwen3-Embedding-4B"
BATCH_SIZE=8
TIMEOUT_SECONDS=180
OUTPUT_DIR="$SCRIPT_DIR/results"
PYTHON_BIN="python3"

exec "$PYTHON_BIN" "$SCRIPT_DIR/../plot_similarity.py" \
  --inputs "${INPUT_PATHS[@]}" --versions "${VERSIONS[@]}" \
  --max-turns "$MAX_TURNS" --embedding-url "$EMBEDDING_URL" \
  --embedding-model "$EMBEDDING_MODEL" --batch-size "$BATCH_SIZE" \
  --timeout "$TIMEOUT_SECONDS" --output-dir "$OUTPUT_DIR"
