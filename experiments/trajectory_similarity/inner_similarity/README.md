# 내면 기록과 공개 발화의 프로필 간 유사도

## 목적

가설: 과거 내면을 다시 제공하지 않으면, 프로필 정보와 공개 대화만으로 생성한 내담자 궤적은 LLM 편향 때문에 상대적으로 수렴한다.

공개 발화 임베딩만으로는 조건 차이가 노이즈 범위와 비슷했다. 그래서 두 조건이 모두 매 턴 생성·저장하는 내면 기록을 함께 비교한다.

1. **조건 간 비교:** 과거 내면을 다시 제공하면 이후 내면 기록의 프로필 간 유사도가 낮아지는가, 높아지는가? 높아진다면 LLM이 쓴 내면의 재주입이 편향을 강화한다는 대안 설명을 지지한다.
2. **같은 조건 안의 비교:** 프로필 고유성이 공개 발화보다 내면 기록에 더 크게 남는가? 고유성 차이는 같은 프로필의 다른 턴(3턴 이상 간격)과 다른 프로필의 유사도 차이로 측정한다.

## 조건

- 입력: `batch_28cf48…`(과거 내면 전체 주입), `batch_0d9d6d…`(LLM 선별 최대 3턴 주입). 프로필 gold_001~010, 조건당 20턴, 상담사는 `basic`.
- 두 배치의 기록 스키마는 `private_position` 추가 이전이다.
- 항목: 공개 발화, `appraisal.interpretation`, `immediate_feeling`, `appraisal.uncertainty`, `turn_goal.intention`, `response_plan.action`, 전체 내면 값의 연결(`inner_all`).
- 임베딩은 `plot_similarity.py`와 같다. Qwen3-Embedding-4B를 쓰고, 지시문을 붙이지 않으며, L2 정규화한다.
- 요약 구간은 6~20턴이다. 순열검정은 프로필 단위로 조건을 바꾸는 쌍대 방식이며 양측 5,000회, seed는 0이다.
- 각 세션의 최신 `memory_state.json`을 사용한다. 기록의 발화와 `session.json` 공개 발화의 일치를 검사한다.
- 생성 모델은 호출하지 않는다.

## 실행

```bash
~/miniconda3/envs/NLP/bin/python experiments/trajectory_similarity/inner_similarity/analyze.py \
  --batches runs/profile_batches/batch_28cf4899dde548a59f3f runs/profile_batches/batch_0d9d6d8d8c9a465a814f \
  --embedding-url http://192.168.0.32:8002/v1
```

결과는 `results/<시각>_<id>/`에 저장한다. `summary.csv`, `per_turn.csv`, `analysis.json`(입력 세션과 기록 경로)이다.

## 해석 한계

- 내면 항목은 발화보다 길다. 기억 조건의 내면도 미제공보다 5~25% 길다. 길이가 다르면 유사도 수준 자체가 달라진다. 따라서 **항목 간 절대값 비교는 의미가 없고**, 같은 항목의 조건 간 비교만 한다. 그 비교에도 길이 차이의 영향이 남는다.
- 기억 조건의 내면은 주입된 자기 과거 기록을 인용할 수 있다. 그래서 고유성 차이가 커지는 현상에는 자기 이력의 표현 재사용이 섞여 있다. 이 효과는 가설의 작동 원리와 구분되지 않는다.
- 배치 2개, 프로필 10개, 조건당 1회 생성이다. 항목들은 서로 상관되어 있다. 개별 p값보다 방향의 일관성을 우선해서 보되, 확증 결과로 쓰지 않는다.

## 추가 옵션 (2026-11-08 오후)

- `--private-position`: `private_position_v1` 스키마 배치의 `actual_wish`, `intended_message`, `withheld_content`, `withholding_reason`도 분석한다. `inner_all`은 기존과 같이 appraisal·turn_goal·response_plan만 연결한다.
- 빈 내면 항목은 스키마의 '없음'으로 바꿔 임베딩하고 `summary.csv`의 `empty_as_none`에 개수를 남긴다. `c9c8c4`와 `08f865` 배치에서 `appraisal.uncertainty`가 빈 칸인 기록이 3~7개 있었다. 이전 결과(`20261108_104212_bde3d4`)의 입력에는 빈 항목이 없었으므로 그 결과는 변하지 않는다.
