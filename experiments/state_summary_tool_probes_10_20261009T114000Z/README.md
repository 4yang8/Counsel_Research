# 한 세션 10회 · 도구 호출 유도 관찰

조건: [OBJECTIVE.md](OBJECTIVE.md).
결과: [report.md](report.md), [전체 대화 및 요약](dialogue.md), [원문 관찰 인덱스](analysis_index.json), [사용량·분포](metrics.json), [경계 검증](handoff_checks.json).

10회 시도 중 7회 정상 반영, 3회 JSON 코드블록 형식 실패. 도구 호출은 2·9·10번째의 총 3회. 실패 원문과 모든 이벤트를 보존했다. 기존 지침·도구 설명·연구 코드 및 UI 서버는 바꾸지 않았다.

prompts/와 config.json은 실제 실행 지침·설정, source_snapshot/은 코드·도구 정의·DB 참조 스냅샷이다. manifest.json의 코드 해시와 실제 호출 payload를 검증했다. input_N.json은 실제 전송 body이고 probe_N.json의 목적·분류는 모델 입력에 별도로 주지 않았다. sessions/에는 단일 세션의 원문과 정상 내면 기록·요약이 저장돼 있다.

도구 이름이 포함된 후반 요청은 실행 기능을 보는 진단 입력이며 일반 상담 질문과 구분해 해석한다.
