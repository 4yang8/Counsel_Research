# Q2a_variant_context

Q2를 보존한 비교 버전. 특성 라우터는 유지하며, 별도 상태 라우터·갱신 에이전트는 사용하지 않는다.

메인은 같은 messages와 같은 턴 reasoning을 유지하면서 다음 도구를 차례로 호출한다.

1. stage_client_update: 내면 판단·반응 계획
2. commit_client_utterance: 공개 발화 내용 고정
3. select_client_states: 검토할 항목과 이유 (빈 목록 가능)
4. update_client_states: 실제로 변경할 전체 설명과 근거 (빈 목록 가능)

순서는 named tool choice와 인자 검증으로 보장한다. 검토 대상·내용은 모델이 결정한다. 한 번의 LLM 호출이 아니라 결과를 누적한 메인 호출 4회이며, 마지막 도구 후 별도의 종료용 모델 호출은 없다. 특성 라우터까지 정상 경로 총 5회다. 오류 정정 시 메인 호출은 늘어날 수 있다.

발화 고정은 턴의 영구 저장과 다르다. 전체 단계 성공 후에만 공개 대화와 Q2a_variant_context 전용 상태 노트를 확정한다. 실패·취소 시 기존 노트를 유지한다. 다음 턴에는 공개 대화와 최신 노트만 이어가며 이전 턴 reasoning은 넣지 않는다.

UI에서 Q2a_variant_context 선택, 메인/특성 라우터 프롬프트 편집, 메인 연속 reasoning·도구 기록, raw/workflow, 심리상태 노트 원문·변경 근거를 확인할 수 있다. Q2와 파일·세션 저장 영역은 분리된다.
