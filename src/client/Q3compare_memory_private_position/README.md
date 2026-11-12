# Q3compare_memory_private_position

`Q3compare_memory`와 같은 분기 구조에, 내면 기록의 `private_position`에 **상담사의 방금 말에 대한 실제 입장**과 **그 입장을 발화에서 어떻게 했는지**를 적는 칸 두 개를 더한 내담자 버전이다. 분기 구조, 저장 위치, 실행 방법은 `src/client/Q3compare_memory/README.md`를 따른다.

## 목적

상담사 `P3rs`로 생성한 `batch_130eab25…`에서 내담자가 상담사 개입에 대한 이견을 그대로 말해, 물러남 균열 상황이 만들어지지 않았다. 고치는 방법을 둘로 나눠 비교한다.

| 버전 | 지침 문단 추가 | 입장 기록 칸 추가 |
|---|---|---|
| `Q3compare_memory` (2026-11-11 수정) | 함 | 안 함 |
| `Q3compare_memory_private_position` | 함 | 함 |

두 버전의 차이는 입장 기록 칸과 그 칸을 안내하는 문장뿐이다. 칸을 두면 "숨은 부정 반응이 있던 턴"을 글자 검색 없이 셀 수 있다.

## 추가된 기록 칸

| 칸 | 값 | 뜻 |
|---|---|---|
| `counselor_stance` | `agree`, `partly_agree`, `disagree`, `uncomfortable`, `unsure`, `none` | 이번 상담사의 말에 대한 실제 입장. 발화에서 어떻게 말할지와 상관없이 적는다. `none`은 입장을 가질 만한 말이 아닌 경우 |
| `stance_expression` | `voiced`, `softened`, `not_voiced`, `none` | 그 입장을 이번 공개 발화에서 그대로 말했는지, 줄여 말했는지, 말하지 않았는지 |

- 두 칸은 `private_position` 안의 기존 네 칸(`actual_wish`, `intended_message`, `withheld_content`, `withholding_reason`) 뒤에 온다.
- 기록은 과거 내면 기록의 일부로 저장되므로, 제공 갈래에서는 기억 선별이 고른 과거 턴과 함께 다음 입력에 들어간다. 미제공 갈래에서는 들어가지 않는다.
- 설정 서명의 내면 기록 형식은 `private_position_stance_v1`이다.

## 분석에서 쓰는 방법

- **숨은 반응이 있던 턴:** `counselor_stance`가 `disagree`, `uncomfortable`, `partly_agree` 가운데 하나이고 `stance_expression`이 `not_voiced` 또는 `softened`인 턴.
- 상담사 `P3rs`의 균열 탐색(`steps[].rupture.explore.client_turn`)이 그런 턴을 가리켰는지를 본 단위로 센다.

## 결과 (2026-11-11, 두 버전 비교)

조건: 프로필 5개(gold_001~005), 10턴, 분기 턴 5, 상담사 `P3rs`. 집계 코드는 세션 저장 파일의 `steps[].rupture`와 `memory_state.json`의 `private_position`을 턴 번호로 맞춰 센 것이다.

| 항목 | `Q3compare_memory` (`batch_7ef89da1…`) | 이 버전 (`batch_b559fd27…`) |
|---|---|---|
| 상담사 발화 (공통 20 + 갈래 25×2) | 70 | 70 |
| 물러남 표지 기록 | 0 | 0 |
| 균열 탐색 | 0 | 0 |
| `repair` | 3 | 1 |
| `suggest` / `interpret` / `task` | 4 / 11 / 0 | 3 / 18 / 0 |
| 내담자 턴 (공통 20 + 갈래 30×2) | 80 | 80 |
| 보류 내용이 상담사 말에 대한 부정적 반응인 턴 (직접 읽고 셈) | 4 | 5 |

이 버전의 입장 기록 80턴: `agree/voiced` 25, `partly_agree/softened` 28, `partly_agree/voiced` 13, `partly_agree/not_voiced` 1, `uncomfortable/softened` 4, `unsure/softened` 1, `none/none` 8. `disagree`는 0이다.

- **판단:** 두 버전 모두 물러남 균열 상황을 만들지 못했다. 내담자는 이견을 "줄여서 말하기"로 처리했고(예: "높은 기준을 두고 있다는 표현은 좀 다른 것 같아요"), 말하지 않고 따른 턴은 1개였다. 줄여 말한 이견은 공개 발화에 드러나므로 상담사가 표지로 적지 않은 것은 정의상 맞다.
- "숨은 반응이 있던 턴" 규칙(위)에 걸린 33턴 가운데 대부분은 `withheld_content`가 프로필 내용(과거 사건, 핵심 신념)이었다. `partly_agree/softened`가 기본 표시처럼 쓰여 이 규칙만으로는 셀 수 없다.
- 상담사의 `suggest`가 70개 중 3~4개, `task`가 0개여서 내담자가 "따를" 개입 자체가 적었다.
- 확인 범위: 집계는 전체, 발화 원문은 숨은 반응이 있던 턴 약 10개만 읽었다.

## 한계

- 두 칸은 내담자 모델이 스스로 적은 것이다. 실제 발화가 `stance_expression`과 맞는지는 따로 확인해야 한다.
- 칸을 두는 것 자체가 모델이 이견을 더 자주 만들거나 더 자주 숨기게 할 수 있다. 지침은 어느 쪽도 기본값이 아니라고 적었지만, 효과는 생성 결과로 확인해야 한다.

## 검증

`PYTHONPATH=src:tests python3 -m unittest test_compare_branch -q` — 이 버전으로 분기 생성이 되는지, 두 칸이 요청과 기록에 들어가는지, `Q3compare_memory`의 기록 형식이 그대로인지를 확인한다.
