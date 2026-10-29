"""Counseling scenario input and isolated, optional model-defined delegation."""
import copy
import uuid
from tool_loop import run_turn
from mcp_client import Cancelled

from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
COMBINED = 'counseling'
MINIMAL_SYSTEM = '주어진 툴을 적절히 활용하세요.'


def load_scenario(config):
    path = Path(config.get('scenario_file', 'cases/lab_meeting.json'))
    if not path.is_absolute():
        path = HERE / path
    return validate_scenario(json.loads(path.read_text(encoding='utf-8')))


def validate_scenario(value):
    if not isinstance(value, dict):
        raise ValueError('scenario는 JSON 객체여야 합니다.')
    if not isinstance(value.get('task'), str) or not value['task'].strip():
        raise ValueError('scenario.task가 필요합니다.')
    dialogue = value.get('dialogue')
    if not isinstance(dialogue, list):
        raise ValueError('초기 대화가 필요합니다.')
    if value.get('dialogue_format', 'pairs') == 'messages':
        for row in dialogue:
            if not isinstance(row, dict) or row.get('role') not in ('counselor', 'client') or not isinstance(row.get('content'), str) or not row['content'].strip():
                raise ValueError('각 메시지에 counselor/client 역할과 content가 필요합니다.')
    elif value.get('dialogue_format', 'pairs') == 'pairs':
        if len(dialogue) != 5:
            raise ValueError('기존 쌍 형식의 초기 대화는 5쌍이어야 합니다.')
        for row in dialogue:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('counselor', 'client')):
                raise ValueError('각 턴에 counselor와 client 발화가 필요합니다.')
    else:
        raise ValueError('지원하지 않는 dialogue_format입니다.')
    states = value.get('states')
    if not isinstance(states, dict) or len(states) != 3:
        raise ValueError('초기 상태는 3개여야 합니다.')
    for state in states.values():
        if not isinstance(state, dict) or state.get('level') not in ('낮음', '중간', '높음'):
            raise ValueError('상태 수준은 낮음/중간/높음이어야 합니다.')
        if any(not isinstance(state.get(k), str) or not state[k].strip() for k in ('name', 'meaning')):
            raise ValueError('상태 이름과 의미가 필요합니다.')
    if not isinstance(value.get('profile'), dict) or not isinstance(value.get('taxonomy'), list) or not value['taxonomy']:
        raise ValueError('프로필과 taxonomy가 필요합니다.')
    for row in value['taxonomy']:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('name', 'meaning')):
            raise ValueError('taxonomy 각 항목에는 name과 meaning 문자열이 필요합니다.')
    if 'source_notes' in value and not isinstance(value['source_notes'], dict):
        raise ValueError('source_notes는 객체여야 합니다.')
    return copy.deepcopy(value)


def initial_context(scenario):
    data = {k: scenario[k] for k in ('dialogue', 'profile', 'states', 'taxonomy')}
    if 'source_notes' in scenario:
        data['source_notes'] = scenario['source_notes']
    return scenario['task'] + '\n\n[초기 자료: 이번 상담자 발화 직전]\n' + json.dumps(data, ensure_ascii=False, indent=2)


def retrieve_experience(config, args):
    if not isinstance(args, dict) or set(args) != {'reason', 'query'}:
        raise ValueError('retrieve_experience에는 reason, query만 필요합니다.')
    for key, limit in [('reason', 500), ('query', 300)]:
        if not isinstance(args[key], str) or not args[key].strip() or len(args[key]) > limit:
            raise ValueError(key + '는 비어 있지 않은 길이 제한 내 문자열이어야 합니다.')
    path = Path(config.get('experience_file', 'cases/lab_meeting_experiences.json'))
    if not path.is_absolute():
        path = HERE / path
    data = json.loads(path.read_text(encoding='utf-8'))
    case_id = config.get('_experience_case_id', config.get('scenario', {}).get('id'))
    if case_id is None:
        case_id = load_scenario(config).get('id')
    if data['scenario_id'] != case_id:
        raise ValueError('현재 사례에 연결된 과거 경험 자료가 없습니다.')
    query = args['query'].casefold()
    ranked = []
    for row in data['experiences']:
        matched = [word for word in row['keywords'] if word.casefold() in query]
        if matched:
            ranked.append((len(matched), row))
    ranked.sort(key=lambda item: (-item[0], item[1]['id']))
    matches = [{k: v for k, v in row.items() if k != 'keywords'} for _, row in ranked[:3]]
    return {'query': args['query'], 'status': 'found' if matches else 'no_match',
            'source': str(path.relative_to(HERE)) if path.is_relative_to(HERE) else str(path),
            'provenance': data['provenance'], 'matches': matches,
            'note': '키워드가 일치한 기록입니다. 관련성은 질문과 대조하세요. 빈 결과는 경험의 부재를 뜻하지 않습니다.'}


def inspect_record(config, name, args):
    if not isinstance(args, dict) or set(args) != {'reason'} or not isinstance(args['reason'], str) or not args['reason'].strip() or len(args['reason']) > 500:
        raise ValueError('reason은 1~500자 문자열이어야 합니다.')
    key, default = {'inspect_current_event': ('current_event_file', 'cases/lab_meeting_current_event.json'),
                    'get_current_constraints': ('constraints_file', 'cases/lab_meeting_constraints.json')}[name]
    path = Path(config.get(key, default))
    if not path.is_absolute(): path = HERE / path
    data = json.loads(path.read_text(encoding='utf-8'))
    case_id = config.get('_experience_case_id') or config.get('scenario', {}).get('id') or load_scenario(config).get('id')
    if data['scenario_id'] != case_id: raise ValueError('현재 사례에 연결된 자료가 없습니다.')
    return {'source': str(path), 'provenance': data['provenance'], 'record': data['record']}


def settings(config):
    source = config.get('nested', {})
    result = {}
    for key, default, maximum in [('max_agents', 6, 20), ('max_total_tool_calls', 32, 100)]:
        value = source.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'nested.{key}: 1~{maximum} 정수여야 합니다.')
        result[key] = value
    return result


def definitions(mode=None, kind=None, depth=0, filesystem=None, experiment=None):
    return json.loads((HERE / 'tools.json').read_text(encoding='utf-8'))


def build(config, body, filesystem, base_build, mode='dynamic', kind='main', depth=0, role=None, preview=False):
    settings(config)
    cfg = copy.deepcopy(config)
    cfg['filesystem'] = {'enabled': False}
    cfg['system_role_mode'] = 'system'
    request = copy.deepcopy(body)
    request['system_prompt'] = body.get('system_prompt', config.get('system_prompt', MINIMAL_SYSTEM)) if depth == 0 else role
    if depth == 0:
        scenario = validate_scenario(body['scenario']) if 'scenario' in body else load_scenario(config)
        messages = request.get('messages')
        if not isinstance(messages, list) or not messages or messages[0].get('role') != 'user':
            raise ValueError('첫 메시지는 user여야 합니다.')
        if not isinstance(messages[0].get('content'), str):
            raise ValueError('상담자 발화는 문자열이어야 합니다.')
        if not preview and not messages[-1].get('content', '').strip():
            raise ValueError('상담자 발화를 입력하세요.')
        messages[0]['content'] = initial_context(scenario) + '\n\n[이번 상담자 발화]\n' + messages[0]['content']
    return base_build(cfg, request, definitions(), preview)


def execute(config, payload, client, upstream, frames, inspect, emit, cancelled, original, base_build, mode):
    opts = settings(config)
    filesystem = client.definitions() if client else []
    created = 0
    executed = 0
    usage_by_agent = {}

    def total(ids):
        result = {}
        for agent in ids:
            for key,value in usage_by_agent.get(agent,{}).items():result[key]=result.get(key,0)+value
        return result

    def run(agent_id, kind, depth, agent_name, request, history, parent_id=None, parent_call_id=None, role=None):
        usage_by_agent[agent_id] = {}
        descendants = [agent_id]
        current_call = None
        final = {}
        meta = {'agent_id':agent_id,'agent_name':agent_name,'parent_agent_id':parent_id,
                'parent_call_id':parent_call_id,'depth':depth,'mode':mode,'experiment':config.get('_delegation_experiment'),
                'role_instruction':role if depth else next((m['content'] for m in request['messages'] if m['role']=='system'), ''),
                'role_source':'model' if mode=='dynamic' and depth else 'developer'}

        def send(event_type, **data):
            if depth==0:emit(event_type, **meta, **data)
            else:emit('agent_event', **meta, event={'type':event_type, **data})

        def event(event_type, **data):
            nonlocal current_call
            if event_type=='tool_start':current_call=data['call_id']
            if event_type=='chunk' and data['data'].get('usage'):
                for key in ('prompt_tokens','completion_tokens','total_tokens'):
                    usage_by_agent[agent_id][key]=usage_by_agent[agent_id].get(key,0)+(data['data']['usage'].get(key) or 0)
            if event_type=='done':
                final.update(data)
                data['own_usage']=dict(data.get('usage',{}))
                data['subtree_usage']=total(descendants)
                if depth==0:
                    data['main_usage']=dict(data['usage'])
                    data['subagent_usage']=total(i for i in usage_by_agent if i!='main')
                    data['usage']=total(usage_by_agent)
                    data['agent_usage']=copy.deepcopy(usage_by_agent)
            send(event_type, **data)

        allowed = {t['function']['name'] for t in request.get('tools',[])}
        class AgentTools:
            opts = {'max_calls':config.get('delegation', {}).get('max_calls', 8),
                    'max_result_chars':config.get('delegation', {}).get('max_result_chars', 16000)}
            def call(self,name,args):
                nonlocal created, executed
                if cancelled():raise Cancelled('중첩 에이전트 실행을 중단했습니다.')
                if executed >= opts['max_total_tool_calls']:
                    raise RuntimeError('전체 실행의 도구 호출 한도에 도달했습니다.')
                executed += 1
                if name not in allowed:raise ValueError('이 에이전트에 허용되지 않은 도구입니다: '+name)
                if name in ('inspect_current_event', 'get_current_constraints'):
                    return inspect_record(config, name, args)
                if name == 'retrieve_experience':
                    return retrieve_experience(config, args)
                expected = {'reason', 'role', 'task'}
                if not isinstance(args,dict) or set(args)!=expected:
                    raise ValueError('도구 인자는 '+', '.join(sorted(expected))+'만 포함해야 합니다.')
                for key in expected:
                    if not isinstance(args[key],str) or not args[key].strip() or len(args[key]) > (500 if key=='reason' else 2000 if key=='role' else 12000):
                        raise ValueError(key+'는 비어 있지 않은 길이 제한 내 문자열이어야 합니다.')
                if created>=opts['max_agents']:raise ValueError('전체 서브에이전트 생성 한도에 도달했습니다.')
                created += 1
                child_kind = 'dynamic'
                child_role = args.get('role')
                child_name = child_role+f' #{created}'
                child_id = 'agent_'+uuid.uuid4().hex[:12]
                child_history = [{'role':'user','content':args['task']}]
                child_config = copy.deepcopy(config)
                child_config['generation']={k:payload[k] for k in ('temperature','top_p','max_tokens','seed') if k in payload}
                child_request = build(child_config,{'messages':child_history},filesystem,base_build,mode,
                                      child_kind,depth+1,child_role)
                result, subtree = run(child_id,child_kind,depth+1,child_name,child_request,
                                      child_history,agent_id,current_call,child_role)
                descendants.extend(subtree)
                return {'agent_id':child_id,'agent_name':child_name,
                        'content':[{'type':'text','text':result['response']}]}

        if depth:send('request',payload=copy.deepcopy(request),iteration=1)
        try:
            run_turn(config,request,AgentTools(),upstream,frames,inspect,event,cancelled,history)
        except Exception as error:
            if depth:send('cancelled' if isinstance(error,Cancelled) else 'error',error=str(error))
            raise
        return final, descendants

    run('main','main',0,'내담자 메인',payload,original)
