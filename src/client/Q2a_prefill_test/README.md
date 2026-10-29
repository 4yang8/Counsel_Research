# Q2a_prefill_test

- 기준: Q2a_trait_background. 메인/라우터 지침, 입력 자료, JSON 스키마, 메인 4단계는 동일하다.
- 변경: 매 내담자 턴 특성 라우터의 reasoning 시작에 `trait_router_prefill.txt`의 영어 문장만 넣는다. 메인 호출에는 넣지 않는다.
- 서버 변경 없음. 실행 중 서버의 `/tokenize`·`/detokenize`로 기존 chat template을 적용하고, 열린 `<think>` 뒤에 문장을 붙여 `/v1/completions`로 전송한다.
- JSON 구조 제약, 생성 옵션, UI의 서브에이전트 reasoning 예산을 전달한다. 스트리밍 출력의 `</think>` 전후를 Python이 reasoning/결과로 분리한다.
- 현재 Qwen/Qwen3.8-27B-FP8에서 확인했다. 입력 템플릿이 열린 `<think>`로 끝나지 않으면 오류로 알리고 실행하지 않는다.
- prefill은 입력 토큰이다. 모델 출력 reasoning에는 합산하지 않는다. 채팅에는 별도 입력 표시, raw 화면에는 입력 블록, 실제 요청 화면에는 `wire_request`, 템플릿 화면에는 실제 원문을 남긴다.
- 라우터 출력이 잘리거나 JSON/ID 검증에 실패하면 메인 실행·노트 저장으로 진행하지 않는다.
- 문구 파일은 매 라우터 실행 시 다시 읽는다. UI의 system/user 편집기는 이 버전의 독립 파일을 수정한다.

## 확인

`tests/test_prefill.py`: 라우터에만 적용, 실제 전송과 inspection 일치, 같은 턴 reasoning 유지, 저장 경로 분리, 잘린 출력 중단, 구분자 스트리밍 분할 처리.

`tests/browser_prefill.cjs`: 버전/프로필 선택, prefill 입력 표시, 생성 추론, 실제 요청·템플릿, 노트·diff·workflow·새로고침.
