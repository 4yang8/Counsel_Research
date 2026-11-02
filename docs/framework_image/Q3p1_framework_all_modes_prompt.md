# Q3p1 세 모드 통합 구조도 생성 프롬프트

방식: 내장 이미지 생성 모델. 기존 `Q3p1_framework.png`를 스타일 참고로 사용하여 통합 구조도로 재구성한다.

구현 확인: `src/client/claim_experiments/__init__.py`, `memory.py`, `runtime.py`.

기준: 세 모드의 공통 생성·노트 갱신 흐름은 한 번만 그리고, 원본 의도 자료의 추가 방식만 구분한다. `linked_prose`는 LLM 요약이 아닌 동일 자료의 서술형 직렬화다.

```text
Use case: infographic-diagram.
Redesign the supplied Q3p1 architecture image into ONE integrated research framework diagram showing ALL THREE selectable Q3p1 memory modes. Keep the clean white, navy academic aesthetic, but redesign the composition substantially for clarity. High-resolution wide landscape, approximately 16:10. Large readable Korean sans-serif text and exact English tool/mode names. Flat vector-like raster graphic, generous white space, clean orthogonal arrows, no decorative illustrations.

Most important visual principle: draw the common pipeline ONCE. Only the past-intent input representation differs across three colored alternative branches. The three modes are mutually exclusive, not parallel agents and not three inputs combined at once.

Title exactly: "Q3p1 통합 구조도"
Subtitle: "공통 처리 흐름 + 3가지 기억 입력 모드"
Prominent selection label: "세션 시작 시 1개 모드 선택 · 세션 내 고정"

UPPER THIRD: a visually prominent comparison band titled "달라지는 부분: 과거 의도 자료의 추가 방식". Three equal-width colored cards side by side:
1. gray/amber card:
"summary_only"
"과거 원본 자료 추가 없음"
"현재 노트·진행 요약은 유지"
a tiny code-like row "episodes: []"
2. violet card:
"intent_linked"
"발화·시점 ↔ 당시 의도"
"연결된 JSON 레코드"
small line "turn · turn_id · 발화 · 당시 판단"
3. teal card:
"linked_prose"
"동일 정보·동일 연결"
"서술형 문장으로 직렬화"
small line "LLM 재요약 없음"

Above or alongside cards 2 and 3, one shared gray source node "완료된 과거 원본 기록" passes through ONE small selector "최근 최대 12턴 · 원본 합계 18,000자" and forks ONLY toward cards 2 and 3. Label this fork "동일 대상 턴". No active archive input to summary_only. The gray source is an archive, not another model.
All three alternative cards connect downward via matching colored lines to a SINGLE small junction marked "택1" and then into ONE "메인 입력" node. Explicitly show OR selection, never concatenate all three cards.

COMMON MIDDLE / LOWER AREA titled "세 모드에서 동일한 처리".
LEFT common input panel titled "공통 입력":
- "기본 정보"
- "공개 대화 + 이번 상담사 발화"
- "현재 노트 전체"
- "사전 궤적 + 직전 진행 요약"
A small routing flow in the same panel:
"전체 특성·배경 → 특성·배경 라우터 → 선택된 특성·배경"
Use a small note "라우터는 기본 정보·대화·현재 노트도 참고".
The router selects traits/background only, never note IDs.
This common input panel and the chosen mode's past-intent input BOTH feed the single main input node.

CENTER shared main generation container labeled "Q3 기반 메인 생성 · 같은 턴의 추론 유지".
Within it, draw ONE visibly continuous sequence of four numbered cards. Use a vertical stack if needed for legibility. Every consecutive pair MUST have an unmistakable navy directional arrow:
"① 내면 판단·표현 계획"
"stage_client_update"
"appraisal · turn_goal · response_plan · trajectory_plan"
then arrow to
"② 공개 발화 잠정 고정"
"commit_client_utterance"
"아직 외부 전달 전"
then arrow to
"③ 현재 노트 갱신"
"update_client_notes"
"추가 · 수정 · 삭제"
then arrow to
"④ 궤적 진행 요약 갱신"
"update_trajectory_progress"
"현재 경험 · 진행 · 근거".
Main input feeds stage 1.
A small optional dotted side loop to the pre-commit part of the main sequence:
"현재 노트 조회 · 선택"
"retrieve_client_memory"
"source = current".
This optional lookup is COMMON to all three modes and never reads raw archive episodes.

RIGHT shared output/storage panel, fed ONLY after stage 4 through a clear gate:
"전체 검증 성공 후 함께 확정"
Then split to:
a) "상담사" with a prominent arrow label "공개 발화만 전달".
b) storage boxes "현재 노트", "직전 진행 요약", "과거 원본 기록".
Use blue feedback arrows labeled "다음 턴" from current notes and progress summary back to common input. Show raw archive records preserved for the next turn's mode-specific input selection, with a gray next-turn feedback connection to the top source/selector if it can be routed cleanly on the outer margin. Never show direct raw-archive input to main bypassing the selected mode.
A tiny failure branch can say "실패·중단: 이번 턴 미확정, 로그 보존".

Footer, exact concise callouts:
"공통: 현재 노트 전체 + 직전 진행 요약"
"차이: 원본 추가 없음 / 연결된 JSON / 연결 유지 서술형"
"노트·진행 요약에도 과거 이유가 남을 수 있음"
"비공개 자료는 상담사에게 전달하지 않음"

Color semantics: navy/blue for shared processing and state feedback; gray/amber for summary_only; violet for intent_linked; teal for linked_prose. Highlight only the comparison band and branch lines with mode colors. All shared core processing is visually unified. No evaluator, observer, summarizer agent, embeddings, vector database, reward, clinical score, or other Q3p versions. Do not depict linked_prose as inferred intentions or lossy model summarization. Do not depict the modes as simultaneous execution. Ensure text is large and complete; shorten nonessential labels rather than shrink everything.
```
