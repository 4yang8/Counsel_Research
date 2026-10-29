#!/usr/bin/env bash

# ============================================================
# 사용자 설정: MODE 3에서 쓸 LoRA 어댑터 경로
# 실행할 때 경로를 따로 주지 않으면 이 값을 쓴다.
# 우선순위: 세 번째 인자 > LORA_PATH 환경변수 > 아래 값
# ============================================================
DEFAULT_LORA_PATH="/home2/sjyang114/Training/trained_models/lora_SFT_gemma4_E4B_260924_ddp_full"

# ============================================================
# Environment setup
# ============================================================ 

# conda activate를 shell script에서 사용할 수 있도록 설정 
source /home2/sjyang114/miniconda3/etc/profile.d/conda.sh 

# 기존 `gemma4` alias와 동일 
conda activate gemma4 

# 기존 `HF` alias와 동일 
export HF_HOME=/home2/sjyang114/.cache/huggingface

MODE=$1
TP_SIZE=${2:-${TP_SIZE:-1}}
LORA_PATH=${3:-${LORA_PATH:-$DEFAULT_LORA_PATH}}

if ! [[ "$TP_SIZE" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: tensor-parallel-size는 1 이상의 정수여야 합니다 (입력값: '$TP_SIZE')"
  echo "Usage: $0 {1|2|3} [TP_SIZE] [LORA_PATH]"
  exit 1
fi

COMMON_ARGS=(
  --host 0.0.0.0
  --port 8001
  --max-model-len 16384
  --tensor-parallel-size "$TP_SIZE"
  # --data-parallel-size 2
  --limit-mm-per-prompt '{"image": 0, "audio": 0}'
)

case "$MODE" in
  1)
    echo "[MODE 1] gemma-4-31B-it 실행 (TP=$TP_SIZE)"
    vllm serve google/gemma-4-31B-it \
      "${COMMON_ARGS[@]}"
    ;;

  2)
    echo "[MODE 2] gemma-4-E4B-it 실행 (TP=$TP_SIZE)"
    vllm serve google/gemma-4-E4B-it \
      "${COMMON_ARGS[@]}"
    ;;

  3)
    if [[ -z "$LORA_PATH" ]]; then
      echo "Error: MODE 3에서는 LoRA 어댑터 경로가 필요합니다."
      echo "Usage: $0 3 <TP_SIZE> /path/to/lora_adapter"
      echo "   or: LORA_PATH=/path/to/lora_adapter $0 3 <TP_SIZE>"
      exit 1
    fi

    LORA_DIR="$(realpath "$LORA_PATH")"
    if [[ ! -f "$LORA_DIR/adapter_config.json" ]]; then
      echo "Error: LoRA 어댑터 폴더가 아닙니다 (adapter_config.json 없음): $LORA_DIR"
      exit 1
    fi
    # 베이스 모델은 어댑터가 학습된 모델을 그대로 쓴다.
    BASE_MODEL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["base_model_name_or_path"])' "$LORA_DIR/adapter_config.json")"
    # 어댑터마다 이름을 다르게 둔다. 대화 파일에 이 이름이 남아서 어느 어댑터였는지 알 수 있다.
    # 규칙을 바꾸면 scripts/run_conversation_shards.py의 registered_lora_name()도 같이 바꾼다.
    LORA_NAME="$(basename "$LORA_DIR")"
    if [[ "$LORA_NAME" == checkpoint-* ]]; then
      LORA_NAME="$(basename "$(dirname "$LORA_DIR")")-$LORA_NAME"
    fi

    echo "[MODE 3] $BASE_MODEL 베이스 + LoRA 동시 서빙 (TP=$TP_SIZE)"
    echo "  Base model: $BASE_MODEL"
    echo "  LoRA model: $LORA_NAME ($LORA_DIR)"

    vllm serve "$BASE_MODEL" \
      "${COMMON_ARGS[@]}" \
      --enable-lora \
      --lora-modules "$LORA_NAME=$LORA_DIR"
    ;;

  *)
    echo "Usage: $0 {1|2|3} [TP_SIZE] [LORA_PATH]"
    echo ""
    echo "  1: google/gemma-4-31B-it"
    echo "  2: google/gemma-4-E4B-it"
    echo "  3: LoRA 어댑터 + 그 베이스 모델 (어댑터 경로 필요, 베이스는 adapter_config.json에서 읽음)"
    echo ""
    echo "  TP_SIZE: --tensor-parallel-size 값 (기본값 1)"
    echo ""
    echo "Examples:"
    echo "  $0 1        # 31B, TP=1"
    echo "  $0 1 4      # 31B, TP=4"
    echo "  $0 3 2 /path/to/lora_adapter"
    echo "  LORA_PATH=/path/to/lora_adapter $0 3 2"
    exit 1
    ;;
esac
