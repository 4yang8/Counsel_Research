# 진행 메모

사용자: 인터럽트 전까지 생성→분석→수정 반복. 핵심 기록은 JOURNAL.md에만 간결하게.
원본 Q1/P3a/tool_loop는 original_hashes.json으로 보존 확인. 실험 버전 Q1_s1/P3a_s1 등록, UI/프롬프트 편집 지원.

- 실행: NLP python, `side_experiments/run.py serve|batch`; 실험 저장소는 이 폴더 storage, 요약 runs/*/compact.json 및 dialogue.md.
- 서버: 8892 원래 매 이벤트 전체 스냅샷; 8893 빠른 스냅샷(0.5초), 8894 빠른 스냅샷+전체 노트 제출 후보 지원. 원본 UI 8891 유지.
- 실행 중: s1_r1_lab(6쌍, 버젯 없음, 8892), s1_budget512_005(5쌍, 내담자 메인/라우터 512, 8892), s1_r2_replace512_lab(5쌍, Q1 512 + side_note_mode=replace, 8894).
- baseline_r1_lab: API 4발화 완료 뒤 도구 XML 공개 오염으로 실행기 중단. manifest invalid_public_from_step=4. 그 이후를 유효한 대화로 평가하지 않기.
- 동일 입력 probe: 메인 원본 reasoning 1223/전체2081, 수정974/1695. 라우터 원본1615/전체1770, 수정1428/1588. 양쪽 라우터 모두 traits2,3만 선택하여 반응 유도 수정의 효과는 아직 미확정.
- s1 변경: Q1 추론 내 완성 초안 반복 억제/공개 말투 분리; 라우터 근거는 확인된 관련성만; P3a 가설·사실 구분/짧은 한 대응.
- r2 변경: 노트 target+content 전체 원문 제출(선택적 실험 설정), 잘린 JSON은 기록에 추가하거나 실행하기 전에 검증·최대2회 재생성, raw <tool_call> 최종 발화 거부. 현재 진행 중 r1 서버에는 신규 실행 코드가 소급 적용되지 않음.
- 검사: 기존 Q1 9개, P3a 8개, side 6개 테스트 통과. browser_q1 통과. 사이드 UI 선택/프롬프트 조회 정상(초기 로딩 완료 후 테스트).

다음: 3개 실행 완료까지 주기적 진행/직접 발화·appraisal·노트·가이드 검토. 반복 탐색, 계획/실행 차이, 추론 단축 부작용 점검. 유의미한 개선이 재현될 때만 s2 승격. 필요하면 동일 체크포인트 재생/추가 프로필 반복. 새 파일로 추가된 버전은 실행 서버를 새로 시작해야 등록됨. 기존 실험 프롬프트는 진행 중 수정하지 않음(매 턴 라우터/가이드가 파일을 다시 읽음).

## 진행 갱신
원본 대조 중단. s1_r1_lab 6쌍, s1_budget512_005 5쌍, s1_r2_replace512_lab 5쌍 완료. s1_r3_semantic_005(8895, 실행기 exec56861), s1_r4_source_only_lab(8892, 실행기 exec90791) 진행 중. 최신 UI8892(server exec6112), 8895(server exec19964) 유지. 이전8892/8893/8894 서버 종료.

r2 의미 오류: 내담자 '준비 부족만으로 해결 안 될 듯'→메인 '해결 불가능/고정'으로 과장→서브/노트/Q1 통찰로 전파. P3a_s1 옵션 side_guide_context=public(요청+실제 공개대화), source_only(guide만 인자, 해석 없이 공개대화 자동 전달). public+대조 지침v1/v2 512 실패, v2 1024 일부 유보 후 같은 가설. source_only512는 과도한 단정 사라졌지만 자문 일반화 경향. probe_guide.py/probes에 기록. r4는 source_only 폐루프검증. system_source_only.md가 별도 메인 지침, guide_grounded.md 별도 서브 지침. UI에서 둘 다 조회 가능.

Q1_s1 옵션 side_trait_units=semantic(신념 dict 의미 묶음), side_router_reason=log_only(이유 로그 전용). r3 적용. 두005조건 첫 main payload 해시 동일하지만 반응 달라짐: seed만으로 재현성 보장 안 됨. r2 step7 라우터 traits/4 이유는 반추를 서술했지만 실제traits/4는 경험 불일치 시 이의 제기: ID-근거 불일치도 관찰.

검증: 기존Q1 9개/P3a8개, side8개, browser_q1 통과. source_only 도구 실제인자는 guide만, 실제 대화 전달·비공개 프로필 차단 HTTP 테스트 통과. 원본 해시 unchanged.
