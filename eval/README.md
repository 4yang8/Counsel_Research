# 상담 대화 평가 초안

`bash eval/run.sh` 한 번으로 버전별 최신 완료 분기 배치를 읽고, `codex exec`로 평가하여 CSV·JSON·Markdown 보고서와 PNG/SVG 그래프를 저장한다. 원본 대화/프롬프트/기록은 변경하지 않는다.

## 실행

```bash
# Codex에 로그인되어 있어야 한다. 기본: gpt-6-luna / max / 동시 평가 2개 / 평가자 반복 1회
bash eval/run.sh

# 권장 사전 확인: 데이터 선택·파싱·코드 집계·그래프만. LLM 호출 없음
bash eval/run.sh --prepare-only

# 현재 두 상담사와 같은 내담자 버전만 비교
bash eval/run.sh --counselor P3rs --counselor P3rs_sub \
  --client Q3compare_memory_private_position

# 각 배치에서 같은 순서의 프로필 한 쌍씩 먼저 실행 (제공/미제공 모두 포함)
bash eval/run.sh --counselor P3rs --client Q3compare_memory_private_position --max-profiles 1

# 고정된 입력/프롬프트로 실패·미평가 작업 재개. 완료된 검증 결과는 재사용
bash eval/run.sh --resume eval/results/<run-id>

# 저장된 판정으로 집계/그림만 다시 만들기
bash eval/run.sh --report-only eval/results/<run-id>

# 실제 CLI 연결·모델·JSON 출력 검증용 합성 대화, 공개/내면 각 1회; 연구 결과와 별도
bash eval/run.sh --smoke-test

# 평가 반복 수/동시성 (반복은 동일 모델의 반복 판정이며 독립 인간 평가자가 아님)
bash eval/run.sh --repeats 3 --workers 4
```

Python은 `EVAL_PYTHON` 환경변수, 프로젝트 NLP conda Python, `python3` 순서로 선택한다. 필요한 패키지는 `requirements.txt`에 있다. CLI는 `--ignore-user-config`, `--ephemeral`, `--output-schema`를 지원해야 한다. 요청한 모델/effort가 실패해도 다른 모델로 자동 대체하지 않는다. 모델 목록에 없을 때는 서버에 그대로 요청하고 실제 지원 여부를 확인한다.

`config.json`에서 기본값을 바꿀 수 있다. `counselors`, `clients` 빈 배열은 지원되는 모든 조합을 뜻한다. `--batch <ID>`를 반복하면 명시한 완료 배치를 사용한다. 기본 전체 평가는 배치당 프로필 수 × 2조건 × 2평가종류 × repeats만큼 요청한다. 실패 시 retries 횟수만큼 추가 요청할 수 있다. 실제 작업 수와 선택 배치를 실행 직후 출력한다.

## 최신 배치 선택과 파서 경계

- 기본 단위는 **상담사 버전 × 내담자 버전**. `created_at`이 가장 늦은 **완료 compare 배치** 하나를 선택한다. 파일 수정시간은 사용하지 않는다.
- 실패·중단·진행 중 배치는 제외하고 그 사유를 manifest에 남긴다. 현재 어댑터는 compare-fork만 지원한다. 과거 일반/paired 실험을 자동으로 합치지 않는다.
- common/provided/withheld가 완전한 프로필만 허용한다. 선택 배치에서 누락/오정렬이 발견되면 조용히 제외하지 않고 중단한다.
- 내담자 `n`턴부터 조건이 갈리고 상담사 `n`턴은 공통 생성이다. 따라서 내담자 평가는 n부터, 상담사 행동 평가는 n+1부터. 공통 대화와 내면은 맥락으로 제공하되 결과 집계에 공통 상담 행동을 중복 포함하지 않는다.
- 상태는 session의 `client_storage_dir` 포인터를 따른다. 이전 `client_attempts/...` 및 새 `client_state/...` 모두 지원한다. 실패 시도 중 가장 최신 폴더를 임의로 선택하지 않는다.
- 신규 저장 형식은 `data.py` 어댑터만 먼저 수정한다. 공통 평가 형식은 packet_id/scope/turns이며, 각 turn은 번호·상담사·내담자와 private 평가에서만 inner를 포함한다.
- 원본 경로·SHA256·프로필 ID/해시·프롬프트 해시·배치 시각·선택/제외 이유를 저장한다. 같은 버전명이라도 이전 프롬프트 개정과 같은 실험은 아니다. 다른 배치 간 차이를 곧 버전의 인과 효과로 해석하지 않는다.

## 평가 입력과 기준

프롬프트 초안은 `prompts/common.md`, `public.md`, `private.md`. 출력 구조/검증은 `schema.py`.

| 평가 | 제공 자료 | 판단/척도 |
|---|---|---|
| 공개 대화 | 번호가 붙은 전체 공개 대화와 평가 구간 | 모든 평가 구간 상담사 행동 분류, 실제 반응 확인 yes/no/undetermined와 대상 턴 |
| 공개 대화 | 동일 | 내담자 일관성 consistent/problem/undetermined; 구체적 모순·설명 없는 변화·유도·반복·단정·전환 사건과 심각도 1/2 |
| 공개 대화 | 동일 | 대화 자연스러움 1~5 또는 null |
| 내면 참조 | 전체 공개 대화 + 각 시점 inner | 모든 턴의 비공개 상담사 관련 반응 none/partial/unvoiced/undetermined |
| 내면 참조 | 동일 | 관련 후속 사건의 연속성 compatible/contradiction/undetermined |
| 내면 참조 | 동일 | 현재 변화 unchanged/supported/unsupported/undetermined; 공개 방식 withheld/partial/explicit/undetermined |

당시 경험을 바꾸어 설명하는 것과 현재 마음이 변화하는 것을 구분한다. 새 정보를 말함, 생략, 비공개 유지, 의견 변화 자체를 오류로 보지 않는다. 계속 숨기는 의도가 후속 내면에 있으면 공개 부인만으로 기억 오류라고 판정하지 않는다. 공개 증가나 물러남 표지 증가를 품질 향상으로 채점하지 않는다.

원문에서 그대로 찾을 수 있는 인용과 턴 번호를 요구하고 코드로 검증한다. 필수 턴 누락, 잘못된 대상, 중복 사건, 존재하지 않는 인용은 실패 처리한다. 이 검증은 의미적 정답성을 보장하지 않으며, 프롬프트는 작은 표본에서 추가 교정할 초안이다.

## 블라인드 실행

- 평가 종류마다 별도 CLI 호출. 버전명·배치·프로필 ID·조건·상담사 자기라벨·선택 기억·모델 reasoning은 전달하지 않는다. `_private_position`의 입장 라벨은 inner의 일부로 남되 정답으로 보지 않도록 명시한다.
- 임의 packet_id를 쓰고 호출 순서를 섞는다. 평가용 임시 디렉토리는 프로젝트 밖에 두며 작업은 stdin으로 전달한다.
- 사용자 config를 무시하고 shell, apps, multi_agent, skill search, web search를 비활성화한다. 읽기 전용 sandbox와 승인 불가 설정을 사용한다. 도구 실행이 JSONL에 보고되면 해당 판정은 거부한다. 이는 입력의 메타데이터를 가리는 절차이며 문체/스키마에서 조건이 추측될 가능성까지 제거하지는 못한다.
- evaluator에게 실제 파일 탐색을 맡기지 않는다. 부모 Python만 원본과 조건 매핑을 읽는다. 사용자의 Codex 로그인은 그대로 사용하며 인증 파일을 복사하지 않는다.

## 집계와 그림

| 핵심 지표 | 분자 / 분모 |
|---|---|
| 비공개 반응 대화 비율 | 전체 맥락에 partial/unvoiced가 있는 대화 / 유무 판단 가능한 대화 |
| 반응 확인 횟수 | 공개 평가자가 확인한 실제 확인 질문 수 / 평가 대화 |
| 비공개 반응 대상 확인율 | 비공개 반응 턴을 대상으로 한 확인 / 대상과 그 반응을 판정 가능한 확인 |
| 과거 경험 충돌률 | contradiction 후속 사건 / compatible 또는 contradiction 후속 사건 |
| 근거 불충분한 변화율 | unsupported / supported 또는 unsupported 변화 사건 |
| 공개 내담자 문제율 | problem 대화 / consistent 또는 problem 대화 |
| 명시적 공개율 | explicit / 공개 방식을 판단 가능한 관련 후속 사건 |

비율은 먼저 대화 안에서 계산하고, 반복 평가를 대화 안에서 평균한 뒤, 대화 간 평균한다. CSV에는 사건 합계 기준 pooled_value도 별도로 제공한다. 기회가 없는 대화의 충돌률/공개율은 N/A다. 미평가/실패/판단 불가는 0점으로 대체하지 않는다. `hidden_new`는 평가 구간의 비공개 반응 횟수이며 미판정 턴이 있으면 NA로 둔다.

`figures/opportunities.png`는 상황·확인 기회, `outcomes.png`는 연속성·공개 대화 문제·자연스러움이다. 같은 내용의 SVG도 저장한다. 막대는 평균, 점은 대화별 값, n은 유효/전체 대화 수. 그림 제목은 폰트 의존을 줄이기 위해 영어다. 종합점수/승자/유의성은 자동 생성하지 않는다.

`paired_differences.csv`는 같은 배치·프로필의 제공−미제공 차이이며 양쪽 모두 평가 가능한 쌍만 포함한다. 프로필/사건/반복 판정을 독립 표본처럼 늘려 검정하지 않는다.

## 결과 폴더

```
eval/results/<UTC시각_ID>/
  manifest.json              # 고정된 선택/설정/작업 목록/해시
  normalized.json            # 원본에서 추출한 공통 형식 + 조건 매핑
  code_metrics.json          # 생성자의 자기라벨/사용량 집계
  snapshot/                  # 실행 당시 코드/프롬프트/출력 스키마
  source_jobs/               # 선택한 원본 job.json 복사본(평가자에게 전달하지 않음)
  packets/                   # 메타데이터가 제거된 평가 입력
  judgments/<task>/
    status.json, result.json
    attempt_001/             # 정확한 프롬프트/CLI 명령/JSONL/오류/원문 응답
  report.md, summary.json
  metrics.csv                # 버전·조건별 지표와 분모
  dialogue_metrics.csv       # 대화별 반복 평균
  ratings.csv, events.csv     # 판정별 값과 근거
  tasks.csv                  # 실패/소요시간/평가 토큰 사용량
  paired_differences.csv
  figures/*.png, *.svg
```

`--prepare-only` 결과는 미완료라고 명시하며 LLM 그래프는 N/A로 만든다. 원본을 변경해도 resume은 저장된 packet만 평가한다. 코드/프롬프트가 변경됐으면 혼합을 막기 위해 resume/report를 중단하고 새 실행을 요구한다. 합성 smoke 결과는 `smoke_*`에 분리하며 실험 결과에 넣지 않는다.

## 검증

```bash
EVAL_PYTHON=/home2/sjyang114/miniconda3/envs/NLP/bin/python
"$EVAL_PYTHON" -m unittest discover -s eval/tests -v
bash eval/run.sh --prepare-only
bash eval/run.sh --smoke-test
```

코드 집계는 모델의 자기보고다. 독립 LLM의 내용 판정과 구분한다. 내면기록은 합성 경험의 참조값이지 임상적 진실이 아니다. 본 도구는 상담사 능력 평가/임상 벤치마크가 아니라 생성 실험의 진단용 초안이다.
# 웹 화면에서 실행

앱 상단 **평가** 탭에서도 동일한 평가 로직을 실행할 수 있습니다. 버전 조합별 최신 완료 배치 또는 명시적인 배치 목록, 병렬 수·반복 수·재시도·시간 제한을 선택하고 진행률·로그·보고서·그림을 확인합니다. **입력 준비·코드 집계만 실행**은 유료 LLM 호출을 하지 않습니다. 자세한 사용법은 [작업 화면 안내](../docs/workbench.md)를 참고하세요.

웹 실행은 `eval/web_jobs/`에 작업 상태를, `eval/results/web_<시각>/`에 기존 형식의 결과를 저장합니다. CLI에는 `--storage-root`(여러 번 지정 가능)와 `--output-dir`(새 eval/results 하위 폴더) 옵션을 추가했습니다. 기존 기본 경로와 sh 실행 방식은 유지합니다.

