# 논문 비교 시뮬레이터

2026-11-03 구현. 기존 Q3의 특성 라우터, 노트 갱신, 궤적 프롬프트를 사용하지 않는 독립 실행 경로다.
공유하는 것은 모델 연결, 로그, 화면, 세션 저장뿐이다.

프롬프트는 사용자의 요청에 따라 **한국어 번역본**으로 전환했다. 논문별 템플릿 29개,
역할 지침 4개, 실행 시 추가하는 출력 지침, PatientAct의 분류 설명과 예시 대화를 번역했다.
JSON 키, 분류 라벨, 템플릿 변수, 종료 마커는 실행 호환성을 위해 유지한다.
발화와 설명은 한국어로 요청하지만, 공식 프로필 40개의 원본 데이터와 검색 태그는 번역하지 않았다.
새 대화부터 번역된 템플릿을 고정해 사용하며, 이미 저장된 대화의 프롬프트 snapshot과 로그는 덮어쓰지 않는다.

## 실행

1. 프로젝트 환경에 `pip install -e .`로 의존성(Jinja2 포함)을 설치하고 기존 서버를 재시작한다.
2. 내담자 버전에서 `Q3cars`, `Q3patientact`, `Q3calmit`를 선택한다.
3. 내담자 설정의 **논문 비교 실행**에서 프로필과 JSON 설정을 확인한다.
4. CALM-IT 전체 구조는 상담사도 `Q3calmit_counsel`로 선택한다. 다른 상담사와의 조합은 별도 교차 실험이다.
5. 시작 상담사 발화를 편집하고 기존 한 발화/자동 생성 버튼으로 실행한다.
6. 생성 과정에는 각 단계의 실제 입력·출력과 사용량이, **노트**에는 마지막으로 확정된 비공개 상태가 표시된다.

새 버전 선택 시 일반적인 첫 인사를 설정한다. 논문 버전의 새 세션에는 기존 예제 히스토리를 자동으로 넣지 않는다.
내담자의 profile/state/thinking/후보는 상담사에게 전달하지 않는다.
기존 초기 task/states/taxonomy/profile exposure 설정은 이 세 방법의 계산에 사용하지 않는다.

## 재현 범위

| 버전 | 구현한 로직 | 원논문과 다른 조건 |
|---|---|---|
| Q3cars | main/session CCD → 주제·촉발·인지 일치 판단 → 감정 변화·의도·비언어 행동·발화. 감정과 이전 의도 지속 | 프로젝트 프로필 변환, 원본 감정 예문 미포함, 초기 감정 기본 50, JSON 출력. 사후 대화 절단과 STREAMS 학습은 미구현 |
| Q3patientact | 공식 프롬프트/분류체계/40개 프로필. 태그 검색 → trust gate → reaction → behavior → 조건부 resistance → response → trust update | 모델·샘플링 설정은 이 프로젝트 선택값. 공식 인물 생성 데이터는 재사용하며 새 인물 생성 파이프라인과 원논문 평가 점수는 미재현 |
| Q3calmit | A1의 요약·확률적 System-1·준비도·감정·라포·목표·행동·3후보, A2의 독립 추정·정체·MI/전환 전략·3후보 | 원본 Gemma-2-9B 역할별 likelihood ranker 없음. 기본 LLM judge 대체. 미공개 프롬프트·초기값·임계값·후보와 전략 대응은 명시적 구현 선택 |

**PatientAct 기본 프로필은 공식 40명이고, CARS/CALM-IT 기본은 화면의 프로젝트 프로필이다.**
동일 인물 비교가 자동으로 성립하지 않는다. 동일 인물 실험에는 각 방법의 native 구조를 구성해
`paper_options.profile`로 공급하고 변환 방법을 별도로 기록해야 한다.
Q3 접두사는 이 프로젝트의 버전 이름이며 Q3 인지/기억 로직을 상속한다는 뜻이 아니다.

## 설정/API

기존 `/api/preview`, `/api/chat`, `/api/paired/create`에 아래 필드를 추가한다.

```json
{
  "experiment_version": "Q3calmit",
  "counselor_version": "Q3calmit_counsel",
  "paper_options": {
    "initial_rapport": 0,
    "selector": "llm_judge",
    "random_seed": 0,
    "recent_messages": 6,
    "min_plan_turn": 10,
    "min_end_turn": 30,
    "max_speaker_turns": 60
  }
}
```

위 필드는 기존 scenario/messages/generation 등의 요청 필드에 더한다.
`selector`는 `llm_judge` 또는 `first_candidate`다. 후자는 후보 3개를 생성한 뒤 첫 후보를
그대로 사용하는 무순위 대조 조건이다. 둘 다 원본 likelihood 선택이 아니다.
모든 생성 모델 호출은 현재 선택한 모델·endpoint를 이용한다.
원논문의 모델을 자동으로 다운로드하거나 실제 LLM 실험을 자동 실행하지 않는다.

- PatientAct: `{"profile_index": 0}`부터 39까지. `profile`이 있으면 index보다 우선한다.
- CARS: `{"initial_emotion": 50}`, 범위 0–100.
- CALM-IT: `initial_rapport` 범위 -1–1. 매 갱신 delta는 [-0.10, +0.05] 검증 후 누적값을 [-1,1]로 제한한다.
- `max_speaker_turns`는 **두 화자의 개별 발화 총수**다. client-turn 횟수와 다르다.
  후보에 종료 마커가 있거나 client action이 Terminate이거나 이 한도에 도달하면 paired 실행을 종료한다.
  원논문의 30/50/100+closure 데이터 생성 프로토콜 전체를 구현한 것은 아니다.
- System-1 표본은 seed와 공개 메시지 수로 결정해 실패 후 재시도 시 같은 분기를 따른다.
  모델 seed가 설정되면 단계별로 증가시켜 후보 3개가 동일한 난수 seed로 생성되지 않게 한다.
- 후보 생성에는 main 추론 한도를, 분석·선택에는 subagent 추론 한도를 적용한다.
- 원문 출력 형식 대신 검증 가능한 JSON을 사용한다. 잘못된 label/범위/형식/미완료 스트림은 실패로 남기며 자동 보정하지 않는다.

### Native profile

CARS:

```json
{
  "profile": {
    "persona": {},
    "background": "상담 배경",
    "main_ccd": {},
    "session_ccd": [
      {
        "theme": "therapeutic alliance",
        "automatic_thought": {"situation":"촉발 상황","cognition":"자동 사고","reaction":"반응"},
        "intermediate_belief": {"attitude":"태도","assumption":"가정","rule":"규칙"}
      }
    ],
    "preferences": {},
    "emotion_examples": []
  }
}
```

`persona/main_ccd/preferences`는 실제 내용으로 채운다. 문자열 또는 객체로 전달할 수 있다.
native가 없으면 프로젝트 프로필을 persona/main CCD로 매핑하고 원문 cognitive-pattern
생성 프롬프트로 배경/session CCD/preferences를 한 번 생성해 성공한 첫 턴에 함께 저장한다.
이것은 PatientPsi/MESC 원본 인물 생성 절차와 동일하지 않다.
감정 10 미만 여부는 별도 플래그로 기록하지만 대화를 소급 삭제하거나 자동 절단하지 않는다.

PatientAct: `data/patientact.json`의 개별 항목처럼 `profile`, `memory.items`, `seed`를 가진
완전한 인물 객체를 위 `paper_options.profile`에 넣는다. 원시 전체 프로필을 응답 프롬프트에
통째로 넣지 않으며, 공식 static system 부분과 trust를 통과한 활성 기억만 사용한다.
막힌 기억은 원문 대신 민감한 영역 여부만 후속 프롬프트에 제공한다.
공식 코드처럼 trust는 2.5에서 시작하고 [1,4]로 제한하며 방향별 delta는 ±0.25/±0.5/0이다.
trust update는 response 생성 뒤 호출하지만 **새 response 이전의 대화 snapshot**을 판단한다.
이는 고정한 공식 커밋의 동작을 유지한 것이다.

CALM-IT:

```json
{
  "profile": {
    "background": "생활 배경 또는 구조화된 객체",
    "cognitive_model": "신념·대처 방식 또는 구조화된 객체",
    "session_goal": "이번 상담에서 원하는 것"
  }
}
```

초기 change stage는 Precontemplation, emotion은 "알 수 없음", rapport는 0, counselor MI stage는 Engaging이다.
turn_length는 "짧게"(기존 short)로 고정한다. 상담사 background는 관찰된 추가 내용을 누적하고,
정체가 없으면 상위 전략 3개에 후보를 하나씩 배정한다. 정체 시 단일 pivot으로 후보 3개를 생성한다.
Figure A14의 최종 allowed list를 그대로 사용한다(설명에만 나오는 CHANGE PLANNING은 추가하지 않음).

## 저장과 실패 처리

- `paper_prompts`는 시작 시 원문 snapshot으로 고정된다. 프롬프트 파일 편집은 새 논문 세션부터 적용된다.
- `paper_provenance`에는 출처·변형·모델·설정·프롬프트 hash를 기록한다. 개별 실제 요청/출력도 기존 이벤트 로그에 남는다.
- `<storage>/<version>/<session>/paper_state.json` 한 파일에 상태·완료 턴·공개 대화를 atomic replace한다.
- paired 실행은 기존의 attempt별 분리 저장을 사용한다. 마지막 단계까지 성공하기 전에는 세션이
  새 client state 경로를 가리키지 않는다. 상담사 상태는 paired session checkpoint로 함께 확정한다.
- 중단·오류·경쟁 갱신은 상태를 덮어쓰지 않는다. 실패한 요청/출력은 로그에 보존한다.
- 기존 세션은 프로필·옵션·프롬프트를 바꾸어 이어갈 수 없다. 새 세션을 사용한다.

## 검증

```bash
PYTHONPATH=src:tests python -m unittest test_paper_baselines
COUNSELING_PYTHON=python node tests/browser_paper_baselines.cjs
bash scripts/test.sh
```

모의 모델 HTTP/SSE 테스트이며 실제 모델에서의 출력 안정성이나 원논문 성능을 검증한 것은 아니다.
출처·프롬프트 추출·라이선스는 [THIRD_PARTY.md](THIRD_PARTY.md)를 참고한다.
