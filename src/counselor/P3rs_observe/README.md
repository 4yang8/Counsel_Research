# P3rs_observe

공개 정보만 사용하는 상담사 실험 후보. 실행은 `counselor/rs_variants/runtime.py`의 해당 분기입니다.

단계: observe → 메인 발화. 구현/호출 예산/기록 정책은 [실험군 문서](../rs_variants/README.md)를 참고하세요.

보조 프롬프트와 user 템플릿은 배치/세션 생성 시 고정됩니다. 모델의 실제 유효성은 새 생성 결과로 검증해야 합니다.
