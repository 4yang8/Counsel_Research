# cm / P3rs 실험군 구현 검증

일자: 2026-11-12. 목적: 새 7개 cm 내담자와 8개 상담사의 실행 경로, 기억 접근 경계, 저장, UI 및 기존 기능 호환성 확인. 실제 대화 품질 비교가 아닌 로컬 HTTP 모의 모델 검사다.

| 검사 | 결과 | 원본 출력 |
|---|---|---|
| `scripts/test.sh` | 362 tests, OK, 342.558초 | [full-tests.log](full-tests.log) |
| `test_cm_rs_variants test_compare_branch test_profile_batch` | 24 tests, OK | [targeted-tests.log](targeted-tests.log) |
| `unittest discover -s eval/tests` | 8 tests, OK | [eval-tests.log](eval-tests.log) |
| `browser_compare_branch.cjs` | PASS | [browser-compare.log](browser-compare.log) |
| `browser_profile_batch.cjs` | PASS | [browser-batch.log](browser-batch.log) |

Python: `/home2/sjyang114/miniconda3/envs/NLP/bin/python`。`scripts/test.sh`는 `COUNSELING_PYTHON`을 이 경로로 지정했고, 브라우저 검사는 해당 환경을 PATH 앞에 넣어 실행했다. 신규 검사 구현은 `tests/test_cm_rs_variants.py`다.

초기 전체 실행은 테스트 fixture의 `package.runtime.HERE` 가정 때문에 새 공유 런타임에서 실패했고, 정리되지 않은 fixture 경로 때문에 후속 검사도 연쇄 실패했다. fixture에서 공유 런타임 ROOT를 별도로 격리하도록 고친 뒤 전체 재실행이 통과했다. 보존된 full-tests.log는 수정 후 전체 실행의 원본 출력이다.

검증 범위: 실제 반응/표현 분리, 선택·시간 구분·검토·상태 갱신·툴 경로, 제공/미제공 분기, 파생 비공개 상태 차단, 상담사 비공개 입력 배제, 과거 원본 보존, 초안 실패 시 미확정, 프롬프트 고정, 버전 선택과 기존 배치 UI. 모델의 의미적 판단 정확도나 임상적 타당성은 검증하지 않았다. 실제 대량생성과 codex exec LLM 평가는 실행하지 않았다.
