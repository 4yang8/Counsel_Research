# 작업 화면 구현 검증 — 2026-11-12

목적: 조합별 대량생성/평가 화면, 시간 기반 배치 이름, 공유 동시 실행 한도, 저장과 재개를 확인한다. 원본 연구 기록은 수정하지 않았다. 모의 HTTP 모델과 LLM 미호출 준비 모드로만 실행했다.

| 검사 | 결과 | 출력 |
|---|---|---|
| 전체 Python (`scripts/test.sh`) | 369 tests, OK (362.175초) | full-tests.log |
| 최종 새 기능 검사 (`test_workbench`) | 7 tests, OK | workbench-tests.log |
| 최종 평가 검사 (`eval/tests`) | 8 tests, OK | eval-tests.log |
| browser_workbench | PASS | browser-workbench.log |
| browser_workspace | PASS | browser-workspace.log |
| browser_master_controller | PASS | browser-master.log |
| browser_compare_branch | PASS | browser-compare.log |
| browser_profile_batch | PASS | browser-batch.log |

Python은 `/home2/sjyang114/miniconda3/envs/NLP/bin/python`을 사용했다. 브라우저 테스트는 `PYTHONPATH=src:tests`와 해당 Python 환경을 사용했다.

전체 검사 후 평가 manifest의 대상 선택 설명과 저장 루트 메타데이터, 결과 파일 캐시 헤더, 목록 상태 표시를 보완했다. 최종 새 기능/평가 검사와 workbench 브라우저 검사를 다시 통과했다.

확인한 동작: 2개 내담자×2개 상담사 전체 생성, 2개로 설정한 전역 프로필 동시 실행 상한, 분기 설정 보존, 조합 중단·재개, 생성 시각 ID 충돌 방지, 완료 배치 선택과 고정, 실제 평가 CLI의 준비 실행, PNG·보고서 생성, 평가 중단 시 하위 프로세스 종료, 화면 전환과 새로고침, 모바일 표시.

브라우저 검사 과정에서 새 상위 작업 탭까지 세던 기존 테스트 선택자를 실험 탭 영역으로 한정했다. 기존 컨트롤러 테스트가 아직 비활성인 '현재 탭 조건 불러오기' 버튼을 누르던 대기도 보완했다. 제품의 기존 컨트롤러 동작은 변경하지 않았다.

production.png, evaluation.png는 모의 실행 화면 캡처다. 실제 모델 생성 및 유료 codex exec 평가는 수행하지 않았다.
