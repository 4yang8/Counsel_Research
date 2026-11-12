#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# 기존 분석의 입력 스냅샷. 이 파일럿은 10개 프로필 × 두 조건 × 20턴 기준.
ANALYSIS="$SCRIPT_DIR/../results/20261108_155115_e93a66/analysis.json"
OUTPUT="$SCRIPT_DIR/results/batch_08f865c61ebd4284a5ae"
export OPENBLAS_NUM_THREADS=1
exec python3 "$SCRIPT_DIR/analyze.py" --analysis "$ANALYSIS" --output "$OUTPUT"
