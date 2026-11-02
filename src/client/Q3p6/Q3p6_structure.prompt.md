# Q3p6 구조 이미지 생성 프롬프트

생성 방식: built-in image_gen

Use case: infographic-diagram.
Create one high-resolution Korean educational infographic explaining the actual Q3p6 counseling client simulator experiment. Landscape canvas, approximately 3:2 ratio. Clear modern research presentation, white/off-white background, dark navy text, muted blue for condition A, teal for condition B, amber for counselor interventions. Large perfectly legible Korean sans-serif typography, generous whitespace, clean flat vector-like shapes, simple arrows, no decorative characters, no fake screenshots, no tiny paragraphs. Prioritize conceptual understanding and correct arrows.

Title verbatim: "Q3p6 실험 구조"
Subtitle verbatim: "같은 과거 · 같은 상담 개입에서, 당시 내면 기록의 추가 효과 비교"

Top band: two equally sized input cards.
Left blue card:
"A · summary_only"
"공개 대화 + 프로필 + 현재 노트"
"현재 상태·목표·이유를 포함할 수 있음"
Right teal card:
"B · intent_linked"
"A와 동일한 입력 + 과거 내면 기록"
"완료된 모든 턴의 발화 ↔ 당시 확정 내면"
Small note beneath both: "A의 노트에서 이유를 일부러 지우지 않음"

Middle large flow panel, carefully separated into left common path and right fork:
Left vertical navy path labeled "① 공통 경로 · A 조건"
node "내담자 n턴 완료"
arrow down to node "② 시점 고정"
small text "대화 · 노트 · 내면 기록"
From this frozen snapshot, one right-pointing arrow enters large right panel titled "③ 같은 시점에서 네 조건 분기".
Inside this right panel, at top a slim amber box "공개 대화만으로 I1 / I2를 한 번 생성"
and smaller line "상담사는 비공개 내면을 받지 않음".
Under it show a 2-by-2 matrix, two rows = intervention, two columns = A and B.
Column headers "A · 노트" and "B · 노트 + 당시 기록".
First row label "I1 · 실행 구체화", cells "A × I1" and "B × I1", horizontal double arrow between cells with label "반응 비교".
Second row label "I2 · 현재 의향 확인", cells "A × I2" and "B × I2", horizontal double arrow between cells with label "반응 비교".
Below matrix a compact shared-control note: "같은 개입 문장 · 같은 기본 입력 · 첫 응답 라우터 공유"
and "각 조건: 내담자 1턴 생성 × 3회 반복".
Do not draw diagonal A-I1 to B-I2 comparisons. Do not imply B is superior.
Below right panel draw an arrow to a storage card "④ 분기 결과 독립 저장" with smaller text "A/B 응답 · 실제 입력 · 개입 근거".
On left path below frozen snapshot, draw a downward continuation arrow to "⑤ 공통 경로 한 턴 추가" then a loop toward the n-turn node labelled "n ← n + 1".
Make a light dashed control arrow FROM independent-results storage toward left continuation node labelled "분기 완료 후 진행"; alongside it explicit note "분기 대화는 공통 경로에 합치지 않음". Distinguish control arrow from data arrows.
Small unobtrusive note near fork: "개입 부적합 시: 사유 기록 후 다음 공통 턴으로"

Bottom band, separated from flow:
"기본 설정: n = 2~20 · 개입 2종 × 기억 2조건 × 반복 3회"
"확인할 것: 같은 개입에서 A/B 반응이 달라지는가?"
"해석 범위: 전체 기록의 추가 효과 — 특정 한 턴이나 연결 방식 자체의 효과는 별도 검증"
All visible text Korean except technical labels above. No other text. Maintain correct hierarchy and enough space for all text.
