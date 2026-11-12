# Paired 상태 저장 정리 (2026-11-12)

## 목적과 범위

발화마다 누적 내면 상태 폴더를 복사해 보존하던 방식을, 세션별 최신 누적 상태 하나를 갱신하는 방식으로 바꾼다. 연구용 공개 대화·내면 기록·요청·응답·reasoning의 의미와 제공/미제공 조건은 변경하지 않는다.

적용 대상은 `client.memory_access.VERSIONS`의 `memory_state.json` 런타임이다. `Q3p6` 등 과거 시점의 상태 경로를 참조하거나 다른 저장 형식을 쓰는 런타임은 기존 구조를 유지한다.

## 새 생성

- `session.json`: 공개 대화, 설정, 상담사 체크포인트, 최신 상태 경로.
- `client_state/<버전>/<세션>/memory_state.json`: 과거 모든 내면 기록을 포함하는 최신 상태.
- `.client_pending/<시도ID>`: 생성 중인 임시 작업본. 생성에 성공하면 최신 상태로 교체되며 누적 보존하지 않는다.
- `.client_transaction.json`, `.client_previous`: 공개 세션/비공개 상태 확정 사이의 중단 복구용. 확정 여부를 세션의 `client_state_commit`으로 판별한다.
- `client_failures/<시도ID>.tar.gz`: 실패한 작업본을 손실 없이 보존. 실패 기록을 성공 결과로 바꾸지 않는다.
- `logs/<세션>`: 기존 호출별 JSON/JSONL 원본을 유지한다. 이번 변경은 상세 로그 압축이나 스트리밍 로그 제거를 포함하지 않는다.

현재 구현은 생성 중 임시 사본을 한 번 만들기 때문에 복사 I/O 자체를 없애지는 않는다. 성공한 모든 턴의 누적 사본을 디스크에 남기지 않는 것이 차이이다. 분기 생성 시에는 독립적인 최신 상태를 한 번 복사한다.

## 기존 데이터 정리

앱 서버를 종료한 상태에서 실행한다. 실행 중인 앱 프로세스가 감지되면 중단한다.

```bash
python scripts/compact_paired_state.py --report /tmp/paired_state_plan.json
python scripts/compact_paired_state.py --apply --report runs/validation/paired_state_compaction/report.json
```

한 세션만 먼저 적용하려면 `--session <세션ID>`를 추가한다. 실행은 모델을 호출하지 않는다.

1. `session.json`이 가리키는 확정 상태를 기준으로 삼는다. 파일 수정시간이 가장 최근이라는 이유로 실패 시도를 채택하지 않는다.
2. 원래 `session.json`을 `state_archive/session.before.json`에 보관한다.
3. 최신 상태를 `client_state`에 복사하고 SHA-256이 동일한지 확인한다. 공개 대화와 설정 등 다른 세션 필드가 동일한지도 검사한다.
4. `client_attempts` 전체를 `state_archive/client_attempts_<ID>.tar.gz`에 압축한다. 실패 시도와 과거 스냅샷도 포함한다.
5. 압축본의 모든 파일을 원본과 바이트 단위로 대조한 뒤 기존 `client_attempts` 폴더를 정리한다. 압축본 SHA-256과 경로·용량은 `manifest.json`에 남긴다.

과거 스냅샷이 필요하면 압축본을 별도 디렉터리에 풀어 읽을 수 있다. 현재 상태를 덮어쓰지 않는다. 기존 로그에 기록된 과거 `client_attempts` 경로는 보관본 내부의 상대 경로에 대응한다. 새 분석에서는 반드시 `session.json`의 `client_storage_dir`를 사용한다.

## 검증

`tests/test_paired_state.py`는 연속 갱신, 생성 실패, 공개 세션 저장 실패, 확정 전/후 중단 복구, 분기 독립성, 보관본 검증 실패 시 원본 보존, 기존 자료 마이그레이션과 반복 실행을 검증한다. 기존 메모리·분기·대량생성 테스트와 전체 모의 모델 테스트로 통합 동작을 확인한다.
