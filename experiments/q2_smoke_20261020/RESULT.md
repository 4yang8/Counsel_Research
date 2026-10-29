# Q2 기능 확인

- 기반: 원본 Q1. 원본 파일 해시 변경 없음.
- 모델: Qwen/Qwen3.8-27B-FP8, max_tokens 4096, 메인·서브 추론 예산 미지정.
- 실모델 신규 1턴: done / 31.2초.
- 실제 선택: stage_client_update → finalize_client_draft (검토 생략).
- 최종 선택 후보의 발화·appraisal·내면 노트 확정 확인. 추가 발화 생성 호출 없음.
- 별도 HTTP fixture로 선택적 검토·수정·권고 기각·이전 후보 선택·상한·중단·정보 격리 검증.
- 이는 동작 확인이며 Q1 대비 발화 품질 개선을 입증한 결과는 아님.
