# Q3p2 세 모드 통합 구조도 생성 프롬프트

방식: 내장 이미지 생성 모델. `Q3p1_framework_all_modes.png`를 스타일·공통 흐름 참고로 사용한다.

구현 확인: `src/client/claim_experiments/__init__.py`, `memory.py`, `runtime.py`.

표현 기준: `intent_linked`, `intent_unlinked`, `linked_prose`는 동일 대상 턴의 의도 본문을 사용한다. `intent_unlinked`는 추가 기억의 명시적 턴·발화 연결과 원래 순서를 제거하며, 공개 대화와 의도 본문은 유지한다. 본문으로 연결을 추론할 가능성과 토큰 길이 차이는 남는다.

```text
Use case: infographic-diagram.
Create a Q3p2 integrated architecture figure by redesigning the supplied Q3p1 all-modes diagram. Keep its clean white/navy academic style, landscape proportions, shared-core structure, blue persistent-state feedback, and clearly separated upper comparison band. High-resolution raster with crisp readable Korean text and exact English identifiers.

Title: "Q3p2 통합 구조도"
Subtitle: "C2 · 동일한 의도 내용에서 발화–의도 연결의 효과"
Selection label: "세션 시작 시 1개 모드 선택 · 세션 내 고정"

Critical facts:
Q3p2 has ONLY intent_linked, intent_unlinked, linked_prose. Default is intent_linked. There is NO summary_only mode in this figure.
All three receive intent content originating from the SAME selected completed episodes. Do not show missing intent content in intent_unlinked. What is removed is explicit binding metadata, not the intent objects.
The shared generation pipeline is drawn ONCE, not once per mode. The three modes are alternatives selected by OR, not parallel models or combined simultaneous inputs.

UPPER COMPARISON BAND:
Header: "달라지는 부분: 의도와 발화의 연결·표현"
A single gray archive source "완료된 과거 원본 기록" feeds one selector "최근 최대 12턴 · 원본 합계 18,000자". Split from that selector into ALL THREE cards with the label "동일 대상 턴·동일 의도 본문".
Three equal colored mode cards, aligned side by side, with enough space for readable labels:
1. VIOLET card:
"intent_linked"
small badge "기본값"
"발화·시점 ↔ 당시 의도"
"연결된 JSON 레코드"
small line "턴 ID + 상담사·내담자 발화 + 당시 판단"
2. AMBER/ORANGE card:
"intent_unlinked"
"의도 내용 묶음은 유지"
"명시적 발화 연결 제거"
small lines:
"추가 기억의 턴 ID·발화 필드 제거"
"원래 순서 제거 · 내용 해시 정렬"
3. TEAL card:
"linked_prose"
"동일 정보·동일 연결"
"서술형 문장으로 직렬화"
small line "LLM 재요약 없음"
Each card has a same-colored outgoing line merging at one "택1" junction and flowing into the single main-input node. Do not merge the contents of all three.

Place a short comparison callout beneath the mode cards, exact meaning:
"의도 본문은 동일 · 명시적 연결과 표현 형식만 다름"
And an uncertainty note:
"연결을 지워도 본문·공개 대화로 추론 가능"
Do not state equal token length or perfect information equivalence.

COMMON LOWER PROCESSING AREA:
Label: "세 모드에서 동일한 처리"
LEFT "공통 입력":
"기본 정보"
"공개 대화 + 이번 상담사 발화"
"현재 노트 전체"
"사전 궤적 + 직전 진행 요약"
A small routing diagram:
"전체 특성·배경 → 특성·배경 라우터 → 선택된 특성·배경"
Note "라우터는 기본 정보·대화·현재 노트도 참고".
The router does not select notes and does not get macro trajectory. Common inputs plus selected traits/background feed ONE "메인 입력" node. The chosen memory mode also feeds that same node.

CENTER common navy container:
"Q3 기반 메인 생성 · 같은 턴의 추론 유지"
One continuous four-stage vertical stack, with a clearly visible navy downward arrow at EVERY transition:
"① 내면 판단·표현 계획"
"stage_client_update"
"appraisal · turn_goal · response_plan · trajectory_plan"
arrow to
"② 공개 발화 잠정 고정"
"commit_client_utterance"
"아직 외부 전달 전"
arrow to
"③ 현재 노트 갱신"
"update_client_notes"
"추가 · 수정 · 삭제"
arrow to
"④ 궤적 진행 요약 갱신"
"update_trajectory_progress"
"현재 경험 · 진행 · 근거".
The main input feeds stage 1.
A small dotted optional side loop only before speech commit:
"현재 노트 조회 · 선택"
"retrieve_client_memory"
"source = current".
This lookup is common to all modes and cannot fetch raw archive episodes. Mode-specific archive injection is automatic, not an extra model or this tool.

RIGHT shared gate and output/storage:
After stage 4 ONLY, "전체 검증 성공 후 함께 확정".
Success splits to "상담사" through an arrow "공개 발화만 전달", and to the storage boxes "현재 노트", "직전 진행 요약", "과거 원본 기록".
Blue next-turn feedback arrows from current notes and latest progress go back to common input.
Gray outer feedback from original archive goes to the upper common episode selector, labeled "다음 턴". This route serves all three modes.
Small failure branch "실패·중단: 이번 턴 미확정, 로그 보존".

Footer:
"공통: 현재 노트 전체 + 직전 진행 요약"
"차이: 연결된 JSON / 명시적 연결 제거 / 연결 유지 서술형"
"공개 대화는 모든 모드에 유지"
"비공개 자료는 상담사에게 전달하지 않음"

Preserve exact tool names. Use color only to distinguish the mode representations; all common operations remain navy/blue. No summary_only, no retrospective inference, no evaluator/observer, no summarizer agent, no vector DB, no rewards, no clinical score. Do not imply that intent_unlinked deletes public dialogue, deletes private intention text, swaps intentions, or changes private-content wording. Render as one finished research figure, no surrounding mockup.
```

## 연결선 보완 프롬프트

```text
Make ONE minimal correction to this Q3p2 diagram. Preserve all text, layout, colors, existing arrows and contents exactly. In the top comparison band, the episode selector labeled '최근 최대 12턴 · 원본 합계 18,000자 선택' must feed ALL THREE mode cards, but the purple intent_linked card currently has no incoming arrow. Add one clearly visible PURPLE branch arrow from the existing selector/source distribution line to the TOP EDGE of the LEFTMOST purple card '① intent_linked'. Route it in the gap below the gray archive box and above the mode cards, pointing down into intent_linked; do not cover any text. Keep the existing incoming arrows to the orange intent_unlinked and teal linked_prose cards. Do not change any other part of the image. The visual result must clearly show the same selected episode set flowing to each of the three alternatives.
```

## 공통 선택 단계 명확화 프롬프트

```text
Apply exactly one local diagram correction to the provided image; preserve all other layout, text, colors, processing stages and arrows. At the very top of the mode-comparison band there are two gray boxes: the archive box around x727–1013 and the selector around x1073–1374, joined by a gray arrow. Replace these TWO boxes and their connecting arrow with ONE continuous wide gray rounded rectangle occupying approximately x727–1374, y122–181 in this 1586x992 image. Its two centered lines must read: '완료된 원본 기록 → 공통 턴 선택' and '최근 최대 12턴 · 원본 합계 18,000자'. This is a shared selection operation, not another storage unit. The purple arrow into intent_linked, orange/purple branch into intent_unlinked, and blue/teal arrow into linked_prose must ALL clearly start at the border of this SAME common-selection rectangle. Keep the purple left-side arrow into intent_linked as an output from the new rectangle. Preserve the gray next-turn feedback entering from the right into the new rectangle. Remove any obsolete line fragments inside the new rectangle. The meaning must be unambiguous: select the identical prior episodes once, THEN render them as one of the three modes. Do not change any other component.
```
