# 누적 내면 흐름 · 단일 세션 5턴

조건과 판정 기준: [OBJECTIVE.md](OBJECTIVE.md).
실행 후 report.md, dialogue.md, findings.json에서 결과를 확인한다. sessions/에는 실제 모델 요청·응답·이벤트·내면 DB·요약 기록을 저장한다.
config.json 및 prompts/는 실행에 사용한 설정·지침 스냅샷이다. 이 실험은 임시 로컬 서버를 사용하며 기존 UI 프로세스를 교체하지 않는다.

실행 완료: 메인 생성 5회, 정상 반영 4회, JSON 코드블록 형식 실패 1회, 통합 요약 4회. 실패 원문도 보존했으며 자동 재시도하지 않았다.

결과: [report.md](report.md), [대화 원문](dialogue.md), [구조화된 관찰](findings.json).
