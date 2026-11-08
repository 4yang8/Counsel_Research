#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

# ===== 여기만 수정 =====
# 대량 작업 폴더(job.json이 있는 곳). 여러 경로를 한 줄씩 넣을 수 있습니다.
# 일반 대화의 session.json 파일 또는 session.json들을 포함한 폴더도 가능합니다.
# 같은 버전·프로필의 대화를 중복 지정하면 오류로 안내합니다.
INPUT_PATHS=(
  "$PROJECT_ROOT/runs/profile_batches/batch_0d9d6d8d8c9a465a814f"
)
MAX_TURNS=20                  # 생성된 내담자 발화의 1턴~N턴 분석. 초기 예시 대화 제외.
VERSIONS=("Q3inner_memory" "inner_memory_no")
EMBEDDING_URL="http://192.168.0.32:8002/v1" # 발화 생성 서버와 별개
EMBEDDING_MODEL="Qwen/Qwen3-Embedding-4B"
BATCH_SIZE=8                  # 임베딩 요청당 발화 개수
TIMEOUT_SECONDS=180
OUTPUT_DIR="$SCRIPT_DIR/results"
PYTHON_BIN="python3"
# ======================

exec "$PYTHON_BIN" "$SCRIPT_DIR/plot_similarity.py" \
  --inputs "${INPUT_PATHS[@]}" --versions "${VERSIONS[@]}" \
  --max-turns "$MAX_TURNS" --embedding-url "$EMBEDDING_URL" \
  --embedding-model "$EMBEDDING_MODEL" --batch-size "$BATCH_SIZE" \
  --timeout "$TIMEOUT_SECONDS" --output-dir "$OUTPUT_DIR"
