"""Q3 cognition with a single, auditable historical-memory input boundary."""
import copy
import json
from pathlib import Path
import re
import threading
import time

from client.Q3.runtime import TOOLS as Q3_TOOLS
from client.Q3.tool_loop import run_turn, validate_messages
from client.Q1.runtime import parse_json
from .validation import validate
from counselor.P1.runtime import write
from runtime import reasoning_budget
from runtime.cancellation import Cancelled
from . import VERSIONS, INNER_VERSIONS, COMPARE_VERSION, COMPARE_STANCE_VERSION, COMPARE_VERSIONS, COMPARE_MODES, ALL_CM_VERSIONS
from .memory import MODEL_PATH, chunk, digest, encode, retrieve

HERE = Path(__file__).resolve().parent
LOCK = threading.RLock()
LABELS = {'Q3public': '과거 비공개 정보 미제공', 'Q3oracle': '사람이 지정한 과거 턴',
          'Q3full': '전체 과거 턴 직접 제공', 'Q3select': 'LLM이 전체 기록에서 선별',
          'Q3ragtool': '필요 시 RAG 도구 1회 · Top-3',
          'Q3inner_memory': '특성 라우터 + 관련 내면 최대 3턴 선별',
          'inner_memory_no': '특성 라우터 + 과거 내면 미제공',
          COMPARE_VERSION: '공통 구간 뒤 분기 · 과거 내면 제공/미제공 비교',
          COMPARE_STANCE_VERSION: '공통 구간 뒤 분기 · 상담사 말에 대한 입장 기록 추가'}
LABELS.update(dict(cm='compare memory · 입장 기록 기본형', cm_split='실제 반응 / 표현 결정 분리',
                   cm_temporal='과거 경험 / 현재 판단 분리', cm_state='사건별 내면 상태 갱신',
                   cm_tools='필요 시 과거 내면 조회 툴', cm_review='발화 초안 검토', cm_full='시간·반응·표현·검토·상태 통합'))
LABELS.update({'Q3'+v:LABELS[v] for v in ALL_CM_VERSIONS if v.startswith('cm')})
DEFAULTS = dict(top_k=3, oracle_turns=[], oracle_plan={}, seed=None, input_char_limit=240000,
                embedding_backend='local', embedding_path=MODEL_PATH, embedding_device='auto',
                embedding_url='', embedding_model='Qwen/Qwen3-Embedding-4B', embedding_timeout=120)


def selector_path(version):
    return HERE / ('inner_selector.md' if version in INNER_VERSIONS else 'selector.md')


def tool(name, description, schema):
    return {'type':'function', 'function':{'name':name, 'description':description, 'parameters':schema}}


def obj(fields):
    return {'type':'object', 'properties':fields, 'required':list(fields), 'additionalProperties':False}


STAGE = copy.deepcopy(Q3_TOOLS[0])
# Retain Q3's appraisal / turn goal / response plan; remove macro-trajectory requirements.
STAGE['function']['parameters']['properties'].pop('trajectory_plan', None)
STAGE['function']['parameters']['required'] = [k for k in STAGE['function']['parameters']['required'] if k != 'trajectory_plan']
STAGE['function']['description'] = '이번 발화의 해석·감정·대화 의도·표현 계획을 비공개 기록합니다. 다음에 commit_client_utterance로 발화하세요.'
INNER_STAGE = copy.deepcopy(STAGE)
INNER_STAGE['function']['parameters']['properties']['private_position'] = obj({
    'actual_wish': {'type':'string','description':'이번 상황에서 자신이 실제로 원하는 결과·선택·관계상의 바람. 상담사에게 할 말의 초안과 구분합니다. 아직 모르거나 상반된 바람이 있으면 그대로 기록합니다.'},
    'intended_message': {'type':'string','description':'이번 발화로 상담사에게 전달하려는 의미·입장. 실제 바람과 같을 수도 있으며, 다르다고 강제하지 않습니다.'},
    'withheld_content': {'type':'string','description':'현재 자신은 알고 있지만 이번 발화에서는 말하지 않기로 한 구체적인 생각·감정·이유. 없으면 없음, 아직 자신도 모르면 불확실이라고 적고 모르는 내용을 확정하지 않습니다.'},
    'withholding_reason': {'type':'string','description':'그 내용을 지금 말하지 않기로 한 이유. 공개하면 어떤 일이 생길 것으로 예상하는지 등 현재 근거가 있는 이유만 씁니다. 보류 내용이 없으면 해당 없음, 이유를 모르겠으면 불확실이라고 적습니다.'},
})
INNER_STAGE['function']['parameters']['required'].append('private_position')
INNER_STAGE['function']['description'] = '이번 해석·감정, 실제 바람과 전달하려는 의미, 보류 내용·이유, 대화 의도와 표현 계획을 구분해 비공개 기록합니다. 숨은 이유나 불일치를 강제하지 않습니다. 다음에 공개 발화를 확정하세요.'
STANCE_STAGE = copy.deepcopy(INNER_STAGE)
STANCE_STAGE['function']['parameters']['properties']['private_position'] = obj({
    **INNER_STAGE['function']['parameters']['properties']['private_position']['properties'],
    'counselor_stance': {'type':'string','enum':['agree','partly_agree','disagree','uncomfortable','unsure','none'],
        'description':'이번 상담사의 말(제안·해석·질문)에 대한 자신의 실제 입장. 발화에서 어떻게 말할지와 상관없이 적습니다. agree=맞다고 느낌, partly_agree=일부만 맞음, disagree=맞지 않음, uncomfortable=맞고 틀림과 별개로 불편하거나 부담스러움, unsure=아직 모르겠음, none=입장을 가질 만한 말이 아님.'},
    'stance_expression': {'type':'string','enum':['voiced','softened','not_voiced','none'],
        'description':'그 입장을 이번 공개 발화에서 어떻게 하는지. voiced=그대로 말함, softened=약하게 줄여 말함, not_voiced=말하지 않음, none=counselor_stance가 none이어서 해당 없음.'},
})
STANCE_STAGE['function']['description'] = '이번 해석·감정, 실제 바람과 전달하려는 의미, 보류 내용·이유, 상담사의 말에 대한 실제 입장과 그 표현 여부, 대화 의도와 표현 계획을 구분해 비공개 기록합니다. 숨은 이유나 불일치를 강제하지 않습니다. 다음에 공개 발화를 확정하세요.'
COMMIT = tool('commit_client_utterance', '앞서 기록한 내면에 따라 실제로 공개할 발화만 확정합니다.', obj({'utterance':{'type':'string','minLength':1}}))
SEARCH = tool('search_private_turns', '필요할 때 이전 내담자 턴의 비공개 내면+실제 발화를 검색합니다. 발화 생성당 최대 1회, 상위 3개. 현재 판단 기록 전에 호출하세요.',
              obj({'query':{'type':'string','minLength':1, 'maxLength':2000}}))


def path(config, version, sid):
    if version not in VERSIONS or not isinstance(sid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',sid):
        raise ValueError('기억 실험 버전 또는 session_id가 올바르지 않습니다.')
    return Path(config['_storage_dir']) / version / sid / 'memory_state.json'


def load(config, version, sid):
    p = path(config, version, sid) if sid else None
    return json.loads(p.read_text()) if p and p.exists() else {'revision':0, 'records':[], 'public_dialogue':[]}


def ids(value, label):
    if not isinstance(value,list) or any(type(n) is not int or n < 1 for n in value) or len(value)!=len(set(value)):
        raise ValueError(label+'은 중복 없는 양의 정수 턴 번호 목록이어야 합니다.')
    return value


def validate_seed(seed):
    if seed is None:
        return
    if not isinstance(seed,dict) or set(seed)!={'public_dialogue','records'} or not isinstance(seed['records'],list):
        raise ValueError('공통 기록은 public_dialogue와 records를 가진 JSON이어야 합니다.')
    public = seed['public_dialogue']
    if not isinstance(public,list) or len(public)%2:
        raise ValueError('공통 공개 대화는 상담사·내담자의 완료된 쌍이어야 합니다.')
    for i,m in enumerate(public):
        if not isinstance(m,dict) or set(m)!={'role','content'} or m['role']!=('counselor' if i%2==0 else 'client') or not isinstance(m['content'],str) or not m['content'].strip():
            raise ValueError('공통 대화의 역할·발화가 올바르지 않습니다.')
    ids([r.get('turn') for r in seed['records'] if isinstance(r,dict)], '공통 기록')
    previous = 0
    for row in seed['records']:
        if not isinstance(row,dict) or set(row)!={'turn','inner','utterance','counselor_utterance'}:
            raise ValueError('공통 records에는 turn, inner, utterance, counselor_utterance만 넣으세요.')
        n = row['turn']
        if type(n) is not int or n<=previous or n>len(public)//2:
            raise ValueError('공통 기록 턴은 공개 대화의 완료된 내담자 턴 번호 순서여야 합니다.')
        previous = n
        if row['utterance']!=public[2*n-1]['content'] or row['counselor_utterance']!=public[2*n-2]['content']:
            raise ValueError('공통 기록의 발화가 해당 공개 대화 턴과 다릅니다.')
        if not isinstance(row['inner'], (dict,str)) or not row['inner']:
            raise ValueError('공통 기록의 inner는 비어 있지 않은 객체 또는 문자열이어야 합니다.')


def configure(config, body):
    version = body.get('experiment_version',config.get('_experiment_version'))
    saved = load(config,version,body.get('session_id'))
    supplied = body.get('memory_options',saved.get('options',{}))
    if not isinstance(supplied,dict) or set(supplied)-set(DEFAULTS):
        raise ValueError('memory_options 설정 키를 확인하세요.')
    options = {**copy.deepcopy(DEFAULTS), **copy.deepcopy(supplied)}
    if type(options['top_k']) is not int or options['top_k']!=3:
        raise ValueError('이번 실험의 Top-k는 3으로 고정되어 있습니다.')
    ids(options['oracle_turns'],'Oracle 턴')
    if not isinstance(options['oracle_plan'],dict):
        raise ValueError('oracle_plan은 생성 대상 턴 번호를 키로 하는 객체여야 합니다.')
    for key,value in options['oracle_plan'].items():
        if not isinstance(key,str) or not re.fullmatch(r'[1-9][0-9]*',key):
            raise ValueError('oracle_plan 키는 양의 정수 턴 번호 문자열이어야 합니다.')
        ids(value,'Oracle 계획')
        if any(n>=int(key) for n in value):
            raise ValueError('Oracle 계획에서 현재·미래 턴을 참조할 수 없습니다.')
    validate_seed(options['seed'])
    if options['embedding_backend'] not in ('local','openai'):
        raise ValueError('임베딩 방식은 local 또는 openai입니다.')
    if not isinstance(options['embedding_device'],str) or not re.fullmatch(r'(auto|cpu|cuda(?::[0-9]+)?)',options['embedding_device']):
        raise ValueError('임베딩 장치는 auto/cpu/cuda/cuda:N 중 하나입니다.')
    for key in ('embedding_path','embedding_model'):
        if not isinstance(options[key],str) or not options[key].strip():
            raise ValueError(key+'를 입력하세요.')
    if options['embedding_backend']=='openai':
        from runtime.connection_settings import normalize_url
        options['embedding_url']=normalize_url(options['embedding_url'])
    for key,lo,hi in [('input_char_limit',1000,2000000),('embedding_timeout',1,600)]:
        if type(options[key]) is not int or not lo<=options[key]<=hi:
            raise ValueError(key+'의 범위를 확인하세요.')
    body['memory_options']=options
    if version in COMPARE_VERSIONS:
        # Session-level switch; stand-alone use behaves like the provided branch.
        if body.setdefault('compare_memory_mode','provided') not in COMPARE_MODES:
            raise ValueError('compare_memory_mode는 provided 또는 withheld입니다.')
    if version in ALL_CM_VERSIONS and version not in ('cm','Q3cm'):
        from .cm_runtime import bundle, STAGES
        if 'cm_prompts' not in body:
            body['cm_prompts'] = saved.get('cm_prompts') or bundle(version)
        if not isinstance(body['cm_prompts'], dict) or set(body['cm_prompts']) != set(STAGES[version]):
            raise ValueError('cm 단계 프롬프트 목록을 확인하세요.')
        if any(not isinstance(v,str) or not v.strip() for v in body['cm_prompts'].values()):
            raise ValueError('cm 단계 프롬프트가 비어 있습니다.')
    if version in INNER_VERSIONS:
        from .inner_router import PROMPT
        body.setdefault('memory_router_prompt', saved.get('router_prompt') or PROMPT.read_text())
        if not isinstance(body['memory_router_prompt'], str) or not body['memory_router_prompt'].strip():
            raise ValueError('특성 라우터 프롬프트가 비어 있습니다.')
    body.setdefault('memory_selector_prompt',saved.get('selector_prompt') or selector_path(version).read_text())
    if not isinstance(body['memory_selector_prompt'],str) or not body['memory_selector_prompt'].strip():
        raise ValueError('기억 선별 프롬프트가 비어 있습니다.')
    body.setdefault('system_prompt',RUNTIMES[version].specification()['system_prompt'])
    if not isinstance(body['system_prompt'],str) or not body['system_prompt'].strip():
        raise ValueError('내담자 지침이 비어 있습니다.')
    blocks=body.get('input_blocks',{})
    if any(blocks.get(k,True) is not True for k in ('system','profile','dialogue')):
        raise ValueError('기억 접근 실험에서는 공통 지침·프로필·공개 대화를 활성화하세요.')
    enabled=body.get('enabled_tools')
    if enabled is not None and set(enabled)!={t['function']['name'] for t in RUNTIMES[version].TOOLS}:
        raise ValueError('기억 접근 실험의 정의된 도구는 모두 활성화하세요. 검색 실행 여부는 모델이 결정합니다.')


def public_source(scenario, options):
    if options['seed'] is not None:
        return copy.deepcopy(options['seed']['public_dialogue'])
    public=copy.deepcopy(scenario['dialogue'])
    if scenario.get('dialogue_format','pairs')=='pairs':
        public=[{'role':role,'content':row[role]} for row in public for role in ('counselor','client')]
    return public


def context(config, body, runtime, preview=False):
    version=body['experiment_version']; options=body['memory_options']
    scenario=runtime.validate_scenario(body['scenario']) if 'scenario' in body else runtime.load_scenario(config)
    messages=validate_messages(body.get('messages'),allow_empty=preview)
    if any(m['role'] not in ('user','assistant') or m.get('tool_calls') for m in messages):
        raise ValueError('과거 모델 도구 기록은 재입력하지 마세요. 공개 대화만 받습니다.')
    saved=load(config,version,body.get('session_id'))
    signature=digest({'profile':scenario['profile'],'options':options,'system':body['system_prompt'],
                      'selector':body['memory_selector_prompt'],
                      **({'memory_policy':'llm_select_top3_v1'} if version=='Q3inner_memory' else {}),
                      # Identical for both branches so a fork can continue the shared records.
                      **({'memory_policy':'compare_fork_v1'} if version in COMPARE_VERSIONS else {}),
                      **({'inner_record_schema':'private_position_stance_v1' if version==COMPARE_STANCE_VERSION or version in ALL_CM_VERSIONS else 'private_position_v1'} if version in INNER_VERSIONS else {}),
                      **({'cm_pipeline':version,'cm_prompts':body.get('cm_prompts',{})} if version in ALL_CM_VERSIONS else {}),
                      **({'router':body['memory_router_prompt']} if version in INNER_VERSIONS else {})})
    if saved['revision'] and signature!=saved['signature']:
        raise ValueError('기억 실험 설정·프로필·지침이 달라졌습니다. 새 대화로 시작하세요.')
    initial=public_source(scenario,options)
    expected=saved['public_dialogue'] if saved['revision'] else initial
    if '_paired_public_dialogue' in config:
        public=copy.deepcopy(config['_paired_public_dialogue'])
        if public[:len(expected)]!=expected or not public or public[-1]['role']!='counselor':
            raise ValueError('저장된 공통 공개 대화와 진행 중 대화가 다릅니다.')
    else:
        mapped=[{'role':'counselor' if m['role']=='user' else 'client','content':m['content']} for m in messages]
        if len(mapped)==1:
            public=expected+mapped
        elif mapped[:-1]==expected:
            public=mapped
        else:
            raise ValueError('공개 대화가 저장된 실험 기록과 다릅니다.')
    records=copy.deepcopy(saved['records'] if saved['revision'] else (options['seed'] or {}).get('records',[]))
    turn=sum(m['role']=='client' for m in public)+1
    if any(r['turn']>=turn for r in records):
        raise ValueError('현재 또는 미래 턴 기록은 검색할 수 없습니다.')
    return dict(saved=saved,signature=signature,profile=scenario['profile'],public=public,records=records,turn=turn)


def oracle_records(ctx,options):
    selected=options['oracle_plan'].get(str(ctx['turn']),options['oracle_turns'])
    available={r['turn']:r for r in ctx['records']}
    if set(selected)-set(available):
        raise ValueError('Oracle이 지정한 턴 기록이 아직 없습니다: '+str(sorted(set(selected)-set(available))))
    return [chunk(available[n]) for n in sorted(selected)]


def source(ctx,visible):
    # Explicit allowlist: no state notes, trajectory progress, prior reasoning or full request body.
    return {'profile':ctx['profile'],'public_dialogue':ctx['public'],'private_turns':visible}


class MemoryRuntime:
    LOCK=LOCK
    TOOL=STAGE
    def __init__(self,version):
        self.version=version;self.HERE=HERE.parent/version
        self.TOOL=copy.deepcopy(STANCE_STAGE if version==COMPARE_STANCE_VERSION or version in ALL_CM_VERSIONS else INNER_STAGE if version in INNER_VERSIONS else STAGE)
        self.TOOLS=copy.deepcopy([self.TOOL,COMMIT]+([SEARCH] if version=='Q3ragtool' else []))

    def specification(self):
        return {'id':self.version,'label':self.version+' · '+LABELS[self.version],
                'description':LABELS[self.version]+' · 공통 Q3 내면 판단→공개 발화. 현재 노트·궤적 요약 자동 주입 없음.',
                'view':'none','memory_access':True,'system_prompt':(self.HERE/'system.md').read_text(),
                'summary_system_prompt':'','tools':copy.deepcopy(self.TOOLS),
                'required_tools':[t['function']['name'] for t in self.TOOLS],
                'default_reasoning_budgets':{'main':None,'subagent':None},
                'memory_defaults':copy.deepcopy(DEFAULTS),
                'subagent_prompts':(([{'kind':'trait_router','label':'특성·배경 라우터 (공개 정보만)'}] if self.version in INNER_VERSIONS else []) +
                    ([{'kind':'memory_selector','label':'기억 선별 담당'}] if self.version in ('Q3select','Q3inner_memory',*COMPARE_VERSIONS) else []))}

    def load(self,config,sid):
        state=load(config,self.version,sid)
        return {'content':json.dumps(state,ensure_ascii=False,indent=2),'records':state['records'],
                'path':str(path(config,self.version,sid)) if sid else None}

    def payload(self,config,body,ctx,visible,builder,preview=False):
        if self.version in INNER_VERSIONS:
            from .inner_router import main_source
            data = main_source(ctx, visible)
        else:
            data = source(ctx, visible)
        request={'system_prompt':body['system_prompt'],'messages':[{'role':'user','content':encode(data)}],
                 'generation':copy.deepcopy(body.get('generation',config.get('generation',{}))),'_reasoning_budget_role':'main'}
        payload=builder(config,request,copy.deepcopy(self.TOOLS),preview)
        payload['parallel_tool_calls']=False
        return payload

    def build(self,config,body,runtime,base_build,preview=False):
        ctx=context(config,body,runtime,preview)
        visible=oracle_records(ctx,body['memory_options']) if self.version=='Q3oracle' else [chunk(r) for r in ctx['records']] if self.version=='Q3full' else []
        result=self.payload(config,body,ctx,visible,base_build,preview)
        if len(encode(result))>body['memory_options']['input_char_limit']:
            raise ValueError('입력 길이 한도 초과: 과거 기록을 자동 절삭하지 않았습니다.')
        return result

    def execute(self,config,payload,client,upstream,frames,inspect,emit,cancelled,original,base_build,mode,runtime):
        started=time.monotonic();body=config['_request_body'];options=body['memory_options']
        ctx=context(config,body,runtime);build=reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets'))
        visible=[];retrievals=[];requests=[];usages={};staged={};speech={};final={};selection={}
        def event(section,action,data):
            emit('version_event',version=self.version,section=section,action=action,data=data)
        def check_size(req):
            if len(encode(req))>options['input_char_limit']:
                raise RuntimeError('입력 길이 한도 초과: 원문을 자동 절삭하지 않았습니다.')
        event('memory_access','started',{'turn':ctx['turn'],'condition':self.version,'available_turns':[r['turn'] for r in ctx['records']]})
        if self.version in INNER_VERSIONS:
            from .inner_router import route
            selected, request, usage = route(config, body, ctx, build, upstream, frames, inspect, emit, cancelled, check_size)
            requests.append({'stage':'trait_router','payload':copy.deepcopy(request)})
            usages['trait_router'] = usage
            event('traits', 'routed', {'selected':selected,'public_only':True})
        if self.version=='Q3full':
            visible=[chunk(r) for r in ctx['records']]
        elif self.version=='Q3oracle':
            visible=oracle_records(ctx,options)
        elif (self.version in ('Q3select','Q3inner_memory') or
              (self.version in COMPARE_VERSIONS and body['compare_memory_mode']=='provided')) and ctx['records']:
            began=time.monotonic()
            schema=obj({'turns':{'type':'array','items':{'type':'integer','enum':[r['turn'] for r in ctx['records']]},'maxItems':3,'uniqueItems':True}})
            req=build(config,{'system_prompt':body['memory_selector_prompt'],
                'messages':[{'role':'user','content':encode({**source(ctx,[chunk(r) for r in ctx['records']]),'max_turns':3})}],
                'generation':copy.deepcopy(body.get('generation',config.get('generation',{}))), '_reasoning_budget_role':'subagent'},[],False)
            # vLLM grammar backends may reject uniqueItems. Enforce uniqueness
            # locally below while keeping the wire schema in their supported subset.
            wire_schema=copy.deepcopy(schema)
            wire_schema['properties']['turns'].pop('uniqueItems',None)
            req['structured_outputs']={'json':wire_schema};check_size(req);requests.append({'stage':'selection','payload':copy.deepcopy(req)})
            result={}
            def subevent(kind,**data):
                if kind=='done':result.update(data)
                emit('agent_event',agent_id='memory_selector',agent_name='기억 선별',agent_kind='memory_selector',
                     parent_agent_id='main',depth=1,event={'type':kind,**data})
            subevent('request',payload=copy.deepcopy(req),iteration=1,phase='전체 과거 기록에서 선별')
            run_turn(config,req,None,upstream,frames,inspect,subevent,cancelled,[])
            if result.get('finish_reason')!='stop':raise ValueError('기억 선별 응답이 완료되지 않았습니다.')
            chosen=validate(parse_json(result['response']),schema)
            usages['memory_selector']=result.get('usage',{})
            visible=[chunk(r) for r in ctx['records'] if r['turn'] in chosen['turns']]
            selection={'selected_turns':chosen['turns'],'latency_ms':round((time.monotonic()-began)*1000,2),'source_count':len(ctx['records'])}
            event('memory_access','selected',selection)
        event('memory_access','injected',{'turn':ctx['turn'],'selected_turns':[r['turn'] for r in visible], 'records':visible,'condition':self.version})
        actual=self.payload(config,body,ctx,visible,build)
        owner=self
        class Tools:
            opts={'max_calls':8,'max_result_chars':4000000}
            requires_finalize=True
            search_used=False
            def call(self,name,args):
                if name not in allowed():raise ValueError('현재 단계에서 허용되지 않는 도구입니다.')
                schema=next(t['function']['parameters'] for t in owner.TOOLS if t['function']['name']==name)
                validate(args,schema)
                if name=='search_private_turns':
                    if len(args['query'])>2000:raise ValueError('검색어는 2000자 이하여야 합니다.')
                    self.search_used=True
                    event('memory_access','searching',{'query':args['query'],'turn':ctx['turn']})
                    try:
                        found=retrieve(ctx['records'],args['query'],options,cancelled)
                    except Cancelled:raise
                    except Exception as error:
                        # Never complete a broken retrieval as if it were the no-memory condition.
                        raise RuntimeError('비공개 기억 검색 실패: '+str(error)) from error
                    retrievals.append(found);visible[:]=found['results']
                    event('memory_access','retrieved',found)
                    result={'records':found['results'],'scores':found['scores'],'remaining_searches':0}
                    if len(encode(result))>options['input_char_limit']:
                        raise RuntimeError('검색 결과가 입력 길이 한도를 넘었습니다. 청크를 절삭하지 않았습니다.')
                elif name=='stage_client_update':
                    staged.update(copy.deepcopy(args));event('appraisal','staged',staged)
                    result={'recorded':True,'next':'commit_client_utterance'}
                else:
                    speech['utterance']=args['utterance'].strip()
                    result={'accepted':True,'pending':True}
                return {'structuredContent':result}
            def terminal_response(self):return speech.get('utterance')
        tools=Tools()
        def allowed():
            if staged:return ['commit_client_utterance']
            return ['stage_client_update']+(['search_private_turns'] if self.version=='Q3ragtool' and not tools.search_used else [])
        def prepare(req,iteration):
            if cancelled():raise Cancelled('기억 실험 중단')
            if iteration>8:raise ValueError('내면·발화 생성 도구 재시도 한도를 초과했습니다.')
            names=allowed();req['tools']=[copy.deepcopy(t) for t in self.TOOLS if t['function']['name'] in names]
            req['tool_choice']={'type':'function','function':{'name':names[0]}} if len(names)==1 else 'required'
            req['parallel_tool_calls']=False;check_size(req)
            if iteration==1:main_event('request',payload=copy.deepcopy(req),iteration=1,phase='기억 확인 · 내면 판단 · 발화')
        def main_event(kind,**data):
            if kind=='done':final.update(data)
            else:
                if kind=='request':requests.append({'stage':'main','payload':copy.deepcopy(data['payload'])})
                emit(kind,**data)
        try:
            run_turn({**config,'_prepare_payload':prepare},actual,tools,upstream,frames,inspect,main_event,cancelled,[])
            if not staged or not speech or final.get('response_source')!='committed_utterance':
                raise ValueError('이번 내면과 공개 발화가 모두 완료되지 않았습니다.')
            usages['main']=final.get('usage',{})
            total={}
            for usage in usages.values():
                for key,value in usage.items():
                    if type(value) in (int,float):total[key]=total.get(key,0)+value
            metrics={'latency_ms':round((time.monotonic()-started)*1000,2),'llm_usage':total,
                     'search_calls':len(retrievals),'injected_turns':[r['turn'] for r in visible],
                     'injected_chars':len(encode(visible)),'candidate_turns':len(ctx['records'])}
            record={'turn':ctx['turn'],'turn_id':config['_turn_info']['turn_id'], 'inner':copy.deepcopy(staged),
                    'utterance':speech['utterance'],'counselor_utterance':ctx['public'][-1]['content'],
                    'condition':self.version,'memory_input':copy.deepcopy(visible),'retrievals':retrievals,
                    'selection':selection,'metrics':metrics,'model_requests':requests,'agent_usage':usages}
            if self.version in INNER_VERSIONS:
                record['selected_traits'] = copy.deepcopy(ctx['selected_traits'])
            if self.version in COMPARE_VERSIONS:
                record['memory_mode'] = body['compare_memory_mode']
            saved={'revision':ctx['saved']['revision']+1,'signature':ctx['signature'],
                   'records':ctx['records']+[record], 'public_dialogue':ctx['public']+[{'role':'client','content':speech['utterance']}],
                   'options':options,'selector_prompt':body['memory_selector_prompt']}
            if self.version in INNER_VERSIONS:
                saved['router_prompt'] = body['memory_router_prompt']
            with LOCK:
                sid=config['_turn_info']['session_id']
                if load(config,self.version,sid)!=ctx['saved']:raise ValueError('다른 실행이 기록을 변경했습니다. 덮어쓰기를 중단합니다.')
                if cancelled():raise Cancelled('기록 저장 전에 중단되었습니다.')
                target=path(config,self.version,sid)
                target.parent.mkdir(parents=True,exist_ok=True)
                write(target,json.dumps(saved,ensure_ascii=False,indent=2))
            event('memory_access','completed',metrics)
            event('utterance','committed',{'content':speech['utterance'],'pending':False})
            subagent_usage={}
            for agent, usage in usages.items():
                if agent!='main':
                    for key,value in usage.items():
                        if type(value) in (int,float):subagent_usage[key]=subagent_usage.get(key,0)+value
            emit('done',response=speech['utterance'],finish_reason='stop',response_source='committed_utterance',
                 history=[{'role':'user' if m['role']=='counselor' else 'assistant','content':m['content']} for m in saved['public_dialogue']],
                 usage=total,main_usage=usages['main'],subagent_usage=subagent_usage,
                 agent_usage=usages,experiment_version=self.version)
        except Exception:
            event('memory_access','discarded',{'turn':ctx['turn'],'message':'이번 턴은 저장하지 않았습니다.'})
            raise


RUNTIMES={version:MemoryRuntime(version) for version in VERSIONS}
from .cm_runtime import CompareRuntime
RUNTIMES.update({version:CompareRuntime(version) for version in ALL_CM_VERSIONS if version not in ('cm','Q3cm')})


def snapshot(config,version,sid):
    state=load(config,version,sid)
    return {'public_dialogue':state['public_dialogue'],
            'records':[{**chunk(r),'counselor_utterance':r['counselor_utterance']} for r in state['records']]}
