"""Transport, validation and atomic state; no Q3 cognition or memory machinery."""
import copy
import hashlib
import json
import math
import re
import threading
from pathlib import Path

from jinja2 import Environment, StrictUndefined
from runtime.cancellation import Cancelled
from runtime.tool_loop import run_turn, validate_messages
from client.state_summary.runtime import write_new

HERE = Path(__file__).resolve().parent
LOCK = threading.RLock()
VERSIONS = ('Q3cars', 'Q3patientact', 'Q3calmit')
ENV = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
TEXT = {'type': 'string', 'minLength': 1}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def enum(values):
    return {'type': 'string', 'enum': list(values)}


def obj(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def validate(value, schema, path='result'):
    kind = schema['type']
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'boolean': type(value) is bool,
             'number': type(value) in (int, float), 'integer': type(value) is int}[kind]
    if not valid:
        raise ValueError(f'{path}: expected {kind}')
    if kind == 'object':
        if set(value) != set(schema['properties']):
            raise ValueError(f'{path}: incorrect fields')
        for key, sub in schema['properties'].items():
            validate(value[key], sub, path + '.' + key)
    elif kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100000):
            raise ValueError(f'{path}: incorrect array length')
        if schema.get('uniqueItems') and len({encoded(x) for x in value}) != len(value):
            raise ValueError(f'{path}: duplicate entries')
        for item in value:
            validate(item, schema['items'], path + '[]')
    elif kind in ('number', 'integer'):
        if not math.isfinite(value) or not schema.get('minimum', -math.inf) <= value <= schema.get('maximum', math.inf):
            raise ValueError(f'{path}: number outside allowed range')
    elif kind == 'string' and schema.get('minLength', 0) and not value.strip():
        raise ValueError(f'{path}: empty text')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: unknown label {value}')
    return value


def bundle(version):
    prefix = {'Q3cars': 'cars_', 'Q3patientact': 'patientact_', 'Q3calmit': 'calmit_'}[version]
    return {p.stem: p.read_text(encoding='utf-8') for p in sorted((HERE / 'prompts').glob(prefix + '*.md'))}


def render(prompts, name, **values):
    return ENV.from_string(prompts[name]).render(**values)


def directory(config, version, session):
    if version not in VERSIONS or not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', session):
        raise ValueError('올바르지 않은 논문 버전/session_id입니다.')
    return Path(config['_storage_dir']) / version / session / 'paper_state.json'


def load(config, version, session):
    path = directory(config, version, session) if session else None
    return json.loads(path.read_text()) if path and path.exists() else {'revision': 0, 'records': [], 'state': {}}


def configure(config, body):
    """Freeze source prompts and data as part of the session settings."""
    version = body['experiment_version']
    saved = load(config,version,body.get('session_id')) if body.get('session_id') else {}
    options = copy.deepcopy(body.get('paper_options', saved.get('frozen_options',{})))
    if not isinstance(options, dict):
        raise ValueError('paper_options는 JSON 객체여야 합니다.')
    allowed = {'profile_index', 'profile', 'initial_emotion', 'initial_rapport', 'selector',
               'random_seed', 'recent_messages', 'min_plan_turn', 'min_end_turn', 'max_speaker_turns'}
    if set(options) - allowed:
        raise ValueError('알 수 없는 paper_options: ' + ', '.join(sorted(set(options)-allowed)))
    defaults = dict(profile_index=0, initial_emotion=50, initial_rapport=0,
                    selector='llm_judge', random_seed=0, recent_messages=6,
                    min_plan_turn=10, min_end_turn=30, max_speaker_turns=60)
    options = {**defaults, **options}
    for key, lo, hi in [('profile_index',0,39), ('random_seed',0,2147483647),
                        ('recent_messages',2,200), ('min_plan_turn',0,1000),
                        ('min_end_turn',0,1000), ('max_speaker_turns',2,1000)]:
        validate(options[key], {'type':'integer','minimum':lo,'maximum':hi}, key)
    validate(options['initial_emotion'], {'type':'number','minimum':0,'maximum':100})
    validate(options['initial_rapport'], {'type':'number','minimum':-1,'maximum':1})
    if options['selector'] not in ('llm_judge', 'first_candidate'):
        raise ValueError('CALM-IT selector는 llm_judge 또는 first_candidate입니다.')
    if 'profile' in options and not isinstance(options['profile'], dict):
        raise ValueError('paper_options.profile은 논문별 native JSON 객체여야 합니다.')
    body['paper_options'] = options
    if version == 'Q3patientact':
        from .patientact import character
        if 'paper_native_profile' not in body:
            body['paper_native_profile'] = saved.get('frozen_native_profile') or character(options)
        character({'profile':body['paper_native_profile'],'profile_index':options['profile_index']})
    # Caller-supplied frozen bundles are validated; edits remain visible in provenance.
    prompts = body.setdefault('paper_prompts', saved.get('frozen_prompts') or bundle(version))
    expected = set(bundle(version))
    if not isinstance(prompts, dict) or set(prompts) != expected or any(not isinstance(v,str) or not v.strip() for v in prompts.values()):
        raise ValueError('논문 프롬프트 묶음이 올바르지 않습니다.')
    body['paper_provenance'] = provenance(version, options, config, prompts)
    body['paper_provenance']['system_prompt_sha256'] = digest(body.get('system_prompt',''))
    body['paper_provenance']['generation'] = copy.deepcopy(body.get('generation',config.get('generation',{})))
    if 'paper_native_profile' in body:
        body['paper_provenance']['native_profile_sha256'] = digest(body['paper_native_profile'])


def provenance(version, options, config, prompts):
    deviations = ['선택한 모델과 생성 설정을 사용하며, 원논문 모델과 점수의 재현을 뜻하지 않습니다.',
                  '필요한 경우 원문의 출력 형식을 구조화된 JSON으로 대체합니다.',
                  '현재 기본 프롬프트는 한국어 번역본이며, 코드가 읽는 키·라벨·태그·종료 마커는 유지합니다.']
    if version == 'Q3patientact':
        source = 'https://github.com/Sahandfer/PatientHub/tree/54d5c8761a9055a1e86d153715607ef9fa26358a'
        profile_source = '사용자가 제공한 논문 형식 프로필' if 'profile' in options else 'PatientHub 공식 프로필 #' + str(options['profile_index'])
    elif version == 'Q3cars':
        source = '로컬 CARS PDF, 3.1절 및 A.2절(12–16쪽)'
        profile_source = '사용자가 제공한 논문 형식 프로필' if 'profile' in options else '프로젝트 프로필 + 생성한 회기별 CCD(변환)'
        deviations += ['감정은 설정값(기본 50)에서 시작합니다. 원문의 초기값은 명시되지 않았습니다.',
                       '원본 감정별 예문 자료는 포함하지 않았습니다. 필요하면 emotion_examples로 제공하세요.',
                       '사후 대화 절단과 STREAMS 상담사 학습은 구현하지 않았으며 원문 발화를 보존합니다.']
    else:
        source = '로컬 CALM-IT PDF, 알고리즘 A1/A2 및 그림 A2–A16'
        profile_source = '사용자가 제공한 논문 형식 프로필' if 'profile' in options else '프로젝트 프로필 변환(원본 Reddit 데이터 아님)'
        deviations += ['원본의 미세조정된 Gemma-2-9B 가능도 순위 모델은 미확보입니다. 선택기=' + options['selector'],
                       '요약, 상담사의 감정 추정, 대화 정체 판단 프롬프트는 재구성했습니다.',
                       '초기값, 최근 대화 범위, 계획·종료 임계값, 발화 길이, 난수 시드는 명시적인 구현 선택입니다.',
                       '상담사 전략 순위별로 후보 하나를 생성하고, 전환 시에는 단일 전략으로 후보 세 개를 생성합니다.',
                       '데이터셋별 길이·마무리 표집 대신 설정한 양쪽 화자 합계 발화 수 한도를 사용합니다.']
    return dict(version=version, implementation='paper-baselines-v1', source=source,
                profile_source=profile_source, fidelity='ported_official_pipeline' if version=='Q3patientact' else 'paper_based_approximation',
                deviations=deviations, model=config.get('model'), generation=copy.deepcopy(config.get('generation',{})),
                options={k:v for k,v in options.items() if k!='profile'}, prompts_sha256=digest(prompts),
                prompt_language='ko' if all(re.search('[가-힣]',text) for text in prompts.values()) else 'mixed_or_original')


def context(config, body, runtime, preview=False):
    scenario = runtime.validate_scenario(body['scenario']) if 'scenario' in body else runtime.load_scenario(config)
    messages = validate_messages(body.get('messages'), allow_empty=preview)
    if any(m['role'] not in ('user','assistant') or m.get('tool_calls') for m in messages):
        raise ValueError('논문 버전에는 공개 대화만 입력하세요.')
    version = body['experiment_version']
    saved = load(config, version, body.get('session_id'))
    signature = digest({'profile':scenario['profile'], 'options':body['paper_options'], 'prompts':body['paper_prompts'],
                        'system_prompt':body.get('system_prompt',''), 'native_profile':body.get('paper_native_profile')})
    if saved['revision'] and saved['signature'] != signature:
        raise ValueError('논문 프로필·설정·프롬프트가 바뀌었습니다. 새 대화를 시작하세요.')
    if saved['state'].get('terminated'):
        raise ValueError('이 논문 세션은 종료되었습니다. 새 대화를 시작하세요.')
    if config.get('_paired_public_dialogue') is not None:
        messages = [{'role':'user' if m['role']=='counselor' else 'assistant','content':m['content']}
                    for m in config['_paired_public_dialogue']]
    elif saved['revision']:
        expected = saved['history']
        if len(messages)==1:
            messages = expected + messages
        elif messages[:-1] != expected:
            raise ValueError('저장된 논문 세션의 공개 대화와 입력이 다릅니다.')
    return dict(scenario=scenario, messages=messages, saved=saved, signature=signature)


class StageCalls:
    def __init__(self, config, body, build, upstream, frames, inspect, emit, cancelled):
        self.config, self.body, self.build = config, body, build
        self.upstream, self.frames, self.inspect = upstream, frames, inspect
        self.emit, self.cancelled = emit, cancelled
        self.trace, self.usage, self.agent_usage = [], {}, {}

    def __call__(self, stage, prompt, schema, messages=None):
        if self.cancelled():
            raise Cancelled('논문 단계 실행을 중단했습니다. 이번 상태는 저장하지 않습니다.')
        schema = {**schema,'title':stage}
        instruction = self.body.get('system_prompt','') + '\n\n' + prompt + '\n\n출력 규약: 아래 스키마에 맞는 JSON만 반환하세요. 이 규약은 위의 출력 형식 지침보다 우선하지만 행동 지침을 바꾸지는 않습니다. JSON 키, 지정된 분류 라벨, 종료 마커, 입력에서 선택해야 하는 태그는 그대로 유지하고, 나머지 설명과 발화는 한국어로 작성하세요.\n' + encoded(schema)
        request = {'system_prompt':instruction, 'messages':copy.deepcopy(messages or [{'role':'user','content':'주어진 과제를 수행하세요.'}]),
                   'generation':copy.deepcopy(self.body.get('generation', self.config.get('generation',{}))),
                   '_reasoning_budget_role':'main' if stage in ('response','cognitive_response') or
                       re.search(r'_candidate_[123]$',stage) else 'subagent'}
        # Repeated seeded candidate requests must not collapse to identical samples.
        if request['generation'].get('seed') is not None:
            request['generation']['seed'] = (request['generation']['seed'] + len(self.trace)) % 2147483647
        payload = self.build(self.config, request, [], False)
        payload['structured_outputs'] = {'json':schema}
        agent_id = 'paper_' + stage + '_' + str(len(self.trace)+1)
        meta = dict(agent_id=agent_id, agent_name=stage, agent_kind=stage,
                    parent_agent_id='main',parent_call_id=None,depth=1)
        result = {}
        def event(kind, **data):
            if kind == 'done':
                result.update(data)
            self.emit('agent_event', **meta, event={'type':kind, **data})
        event('request', phase=stage, iteration=1, payload=copy.deepcopy(payload))
        try:
            run_turn(self.config, payload, None, self.upstream, self.frames, self.inspect,
                     event, self.cancelled, request['messages'])
            if result.get('finish_reason') != 'stop':
                raise ValueError(stage + ': incomplete model response')
            raw = result['response'].strip()
            if raw.startswith('```'):
                raw = raw.split('\n',1)[1].rsplit('```',1)[0]
            value = validate(json.loads(raw), schema, stage)
        except Exception as error:
            event('cancelled' if isinstance(error,Cancelled) else 'error',error=str(error))
            raise
        usage = result.get('usage', {})
        self.agent_usage[agent_id] = usage
        for key, amount in usage.items():
            if type(amount) is int:
                self.usage[key] = self.usage.get(key,0) + amount
        self.trace.append({'stage':stage, 'output':copy.deepcopy(value), 'usage':usage})
        self.emit('version_event',version=self.body.get('experiment_version'),section='paper_pipeline',
                  action=stage,data=value)
        return value


def commit(config, body, ctx, state, utterance, calls):
    if calls.cancelled():
        raise Cancelled('상태 저장 전에 중단되었습니다.')
    sid = config['_turn_info']['session_id']
    path = directory(config, body['experiment_version'], sid)
    history = ctx['messages'] + [{'role':'assistant','content':utterance}]
    record = {'turn':ctx['saved']['revision']+1, 'turn_id':config['_turn_info']['turn_id'],
              'utterance':utterance, 'state':copy.deepcopy(state), 'stages':calls.trace,
              'provenance':body['paper_provenance']}
    after = dict(revision=record['turn'], signature=ctx['signature'], state=state, history=history,
                 frozen_prompts=body['paper_prompts'],
                 frozen_options=body['paper_options'],frozen_native_profile=body.get('paper_native_profile'),
                 records=ctx['saved']['records']+[record])
    with LOCK:
        if load(config, body['experiment_version'],sid) != ctx['saved']:
            raise ValueError('다른 실행이 먼저 상태를 변경했습니다.')
        if calls.cancelled():
            raise Cancelled('상태 저장 전에 중단되었습니다.')
        write_new(path,after)  # one atomic replace: state and transcript commit together
    calls.emit('version_event',version=body['experiment_version'],section='paper_state',action='committed',
               data={'state':state,'path':str(path),'provenance':body['paper_provenance']})
    calls.emit('done', response=utterance,history=history,finish_reason='stop',usage=calls.usage,
               main_usage={},subagent_usage=calls.usage,agent_usage=calls.agent_usage,
               experiment_version=body['experiment_version'],paper_state=state,
               paper_provenance=body['paper_provenance'],terminated=bool(state.get('terminated')))
