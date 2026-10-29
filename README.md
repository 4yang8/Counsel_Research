# 상담 연구 프로젝트

현재 진행 중인 상담사·내담자 에이전트 연구와 실행 UI입니다.
이전 `gemma_test/counseling_test`가 이 프로젝트의 본체입니다.

## 실행

프로젝트 루트에서 실행합니다. Python 3.10 이상이 필요합니다.

```bash
bash scripts/run_counseling.sh
# 다른 포트 또는 설정
bash scripts/run_counseling.sh --port 8892 --config /절대/경로/config.json
```

서버를 종료하려면 다음을 실행합니다.

```bash
bash scripts/stop_counseling.sh
# 다른 포트에서 실행한 경우
bash scripts/stop_counseling.sh --port 8892
```

브라우저에서 `http://127.0.0.1:8891`을 엽니다. 원격 서버는 SSH 포트 전달을 사용합니다.
실행 위치가 달라도 같은 기본 설정·화면·프로필·저장 위치를 사용합니다.
`COUNSELING_PYTHON`으로 Python 실행 파일을 지정할 수 있습니다.
기존 `GEMMA_TEST_PYTHON`도 지원합니다.

모델 서버 주소·모델명·생성 설정은 `config/app.json`에 있습니다.
이전 앱의 기본 모델과 생성 옵션을 유지했습니다. 앱은 모델 서버를 자동 실행하지 않습니다.
모델 서빙 스크립트와 GPU 의존성은 `vllm/`에 별도로 있습니다.

## 구성

| 위치 | 내용 |
|---|---|
| `src/client/` | 내담자 구현, 버전별 프롬프트·도구·기억 처리 |
| `src/counselor/` | 상담사 구현, 버전별 프롬프트·가이드 |
| `src/app/` | 웹 API와 프롬프트·노트 화면 연결 |
| `src/runtime/` | 대화 진행, 도구 호출, 모델 요청·스트리밍 |
| `src/storage/` | 이벤트 기록, 세션 조회, 저장 파일 관리 |
| `src/resources/` | 프로필·초기 대화 선택과 조회 코드 |
| `frontend/` | 브라우저 HTML·CSS·JavaScript와 글꼴 |
| `resources/` | 프로필, 초기 사례, 공통 프롬프트와 실행 가이드 |
| `config/` | 앱 설정 |
| `vllm/` | 모델 서버 실행 스크립트와 GPU 의존성 |
| `experiments/` | 실험 코드·조건·기록·분석 |
| `runs/` | 원본 대화·비공개 노트·호출·이벤트·실패 기록 |
| `tests/` | 최신 앱의 자동 검사와 브라우저 검사 |
| `docs/`, `references/` | 설계 근거와 참고 자료 |
| `archive/` | 중단한 C/V 연구와 이전 도구 실습 |
| `trashcan/` | 정리 후보와 변경 전 사본. 영구 삭제 없이 원래 위치 기록 |

`runs/`는 Git에 추가하지 않습니다. 연구 자료이므로 별도 백업이 필요합니다.
실패·중단 기록과 비공개 노트도 보존합니다.

## 테스트

실제 모델을 호출하지 않는 모의 HTTP 모델 테스트:

```bash
bash scripts/test.sh
# pytest를 사용하는 경우
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
```

브라우저 검사는 Puppeteer와 Chromium이 있는 환경에서 실행합니다.

```bash
PYTHONPATH=src:tests:experiments PUPPETEER_MODULE=/설치/경로/puppeteer node tests/browser_q3b.cjs
```

선택 사항인 패키지 설치는 원본 저장소를 사용하는 editable 설치를 기준으로 합니다.

```bash
python3 -m pip install -e '.[test]'
# 과거 C4 비교 조건도 실행할 경우
python3 -m pip install -e '.[test,legacy-c4]'
```

## 연구 기록과 이전 실행

상담사에는 내담자의 공개 발화만 전달합니다. 내면 노트·도구 조회·추론은 비공개입니다.
실행에 실패한 턴의 변경안을 완료된 상태로 저장하지 않습니다.
버전별 프롬프트 적용 시점과 계획·기억 규칙을 이번 이동에서 바꾸지 않았습니다.

이전 세션의 원본 파일은 그대로 유지합니다. 예전 폴더를 가리키는 저장 경로는
조회할 때 새 위치로 해석하며, 당시 모델 입력이나 프롬프트를 소급 수정하지 않습니다.
C4는 중단한 연구의 비교 조건으로만 남아 있으며 `src/counselor/legacy_c4.py`를 통해
`archive/cv`에 보관한 구현을 읽습니다. Q/P 계열의 실행을 이 코드에 합치지 않습니다.

이동 상세와 검증 결과는 [리팩토링 기록](docs/REFACTOR_20261029.md)을 참고하세요.
