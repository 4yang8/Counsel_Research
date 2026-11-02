# Q3p1 구조도 생성 프롬프트

이미지 생성 방식: 내장 이미지 생성 도구. 기본 `summary_only` 조건을 표현한다.

참고 구현: `src/client/Q3/runtime.py`, `src/client/claim_experiments/runtime.py`, `src/client/claim_experiments/memory.py`, `src/client/claim_experiments/README.md`.

```text
Use case: infographic-diagram
Asset type: research framework figure for a Korean counseling-simulator project.
Primary request: Create one polished, technically accurate Q3p1 architecture diagram, default condition summary_only. A clean academic systems figure, high resolution landscape approximately 16:10, crisp flat vector-like raster art, white background, readable Korean sans-serif typography, restrained navy, blue, teal and gray, generous whitespace. No decorative illustrations. Precise routed arrows; avoid crossings. Korean labels with exact English identifiers. Do not invent components.

Exact title: "Q3p1 구조도"
Subtitle: "C1 · 현재 요약의 충분성 검증 | 기본 조건: summary_only"

Three main areas, with a clear main processing path and a lower persistent-state feedback lane.

LEFT AREA "입력과 자료 선택":
A source box "프로필" with "기본 정보" and "전체 특성·배경".
A source box "공개 대화" with "이전 공개 발화 + 이번 상담사 발화".
A small box "특성·배경 라우터" taking ONLY full traits/background, basic information, public dialogue/current counselor utterance, and current notes. Its output is "선택된 특성·배경".
A "메인 입력" bundle explicitly lists "기본 정보", "선택된 특성·배경", "공개 대화 + 이번 발화", "현재 노트 전체", "사전 궤적 + 직전 진행 요약".
Basic information enters both router and main. Main receives selected traits/background, not all traits/background. Router does not select note IDs. Use grouped arrows or compact source labels to avoid a spaghetti diagram.

CENTER AREA a large navy outlined rounded container "Q3 기반 메인 생성 · 같은 턴의 추론 유지".
Four numbered cards stacked vertically, with arrows in EXACT order:
"① 내면 판단·표현 계획"
"stage_client_update"
small text "appraisal · turn_goal"
"response_plan · trajectory_plan"
then
"② 공개 발화 잠정 고정"
"commit_client_utterance"
small text "아직 외부 전달 전"
then
"③ 현재 노트 갱신"
"update_client_notes"
small text "추가 · 수정 · 삭제"
then
"④ 궤적 진행 요약 갱신"
"update_trajectory_progress"
small text "현재 경험 · 진행 · 근거".
The main input bundle connects to card 1. A tiny optional side box beside main "현재 노트 조회 · 선택" and "retrieve_client_memory" and "source = current" has a short bidirectional dotted arrow ONLY to the pre-commit region of cards 1 and 2, never after commit. This is optional and not a mandatory fifth sequential step.

Following card 4, a clear teal validation gate "전체 검증 성공".
Success sends (a) the public utterance to "상담사" with a prominent label "공개 발화만 전달", and (b) updated current notes, updated progress summary, and original turn records to storage. A small failure branch "실패·중단 → 이번 턴 미확정" must be clearly separate; failed attempts remain logged, no invented retry loop.

LOWER PERSISTENCE AREA "턴 간 보존":
Blue storage box "현재 노트" small text "현재 마음 · 목표 · 보류 이유".
Blue storage box "직전 진행 요약" small text "위치 · 경험 · 근거 · 다음 조건".
These two boxes each have blue feedback arrows labeled "다음 턴 입력" going back to the main input bundle. The current notes also feed the router. The supplied macro trajectory is a static input, not automatically rewritten; mark "사전 궤적 · 고정 입력" near its source.
Gray storage box "과거 원본 기록" small text "발화 · 당시 의도 · 요청 로그". It receives an archive write from successful final validation. No active retrieval or feedback arrow from this archive to generation. A conspicuous red crossed short arrow toward the main input labelled "summary_only: 원본 의도 재주입 차단". This archive is preserved, NOT deleted.
A smaller gray note "실패 시도 로그는 보존" may sit by the failure branch, without feedback to generation.

Footer callout, exact Korean:
"summary_only = 과거 원본 에피소드 추가 없음"
"현재 노트·진행 요약에는 과거 이유가 남을 수 있음"
"비공개 자료는 상담사에게 전달하지 않음"

Use blue for information reused next turn, navy for model processing, teal for successful commit/public delivery, gray for audit storage and red only for the blocked archive feedback. Make the most important distinctions immediately clear. Do not add embeddings, vector databases, a summarizer subagent, independent evaluators, rewards, clinical scores, or other Q3p variants. This diagram is an implementation description, not an experimental result. Spell tool identifiers exactly and ensure every rendered arrow has a plausible endpoint.
```

## 후속 보완 프롬프트

```text
Edit this existing Q3p1 architecture diagram. Preserve the entire layout, all Korean and English text, colors, boxes, existing arrows, dimensions and clean research-figure style. Make only these two small corrections: (1) add an unmistakable short navy downward arrow connecting the bottom center of card ① stage_client_update to the top center of card ② commit_client_utterance. The four main numbered cards must form one visibly continuous chain ① → ② → ③ → ④. Do not cover any text. (2) In the bottom gray archive box, replace the parenthetical '(보존, 검색용)' with '(보존, 연구 검토용)', because the original archive is not retrieved for generation in summary_only. Keep the red crossed archive feedback exactly as it is. Change nothing else.
```
