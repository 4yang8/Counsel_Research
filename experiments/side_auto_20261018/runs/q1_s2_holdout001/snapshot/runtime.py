"""Counseling scenario input and isolated, optional model-defined delegation."""
import copy
import uuid
from tool_loop import run_turn
from mcp_client import Cancelled

from pathlib import Path
import json
import profile_retrieval
import experiment_versions
import state_summary
from versions.Q1 import runtime as q1

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
    value = copy.deepcopy(value)
    if 'dialogue_turns' in value:
        value['dialogue'], value['selected_counselor_utterance'] = select_dialogue_turns(value['dialogue_turns'])
        value['dialogue_format'] = 'messages'
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


def select_dialogue_turns(turns):
    """Keep enabled pairs in order; withhold the final pair's reference answer."""
    if not isinstance(turns, list) or not turns:
        raise ValueError('상담사·내담자 턴이 필요합니다.')
    enabled = []
    for turn in turns:
        if not isinstance(turn, dict) or type(turn.get('enabled')) is not bool:
            raise ValueError('각 턴에는 enabled 체크 상태가 필요합니다.')
        if any(not isinstance(turn.get(k), str) for k in ('counselor', 'client')):
            raise ValueError('각 턴에는 상담사·내담자 발화 문자열이 필요합니다.')
        if turn['enabled']:
            enabled.append(turn)
    if not enabled:
        raise ValueError('생성 대상으로 사용할 턴을 하나 이상 활성화하세요.')
    if any(not turn['counselor'].strip() for turn in enabled):
        raise ValueError('활성화된 턴의 상담사 발화를 입력하세요.')
    if any(not turn['client'].strip() for turn in enabled[:-1]):
        raise ValueError('마지막 턴 이전의 활성화된 내담자 발화를 입력하세요.')
    dialogue = [{'role': role, 'content': turn[role]}
                for turn in enabled[:-1] for role in ('counselor', 'client')]
    return dialogue, enabled[-1]['counselor']


def markdown_data(data):
    """Render original fields as headings and nested lists without rewriting values."""
    lines = []

    def render(value, depth):
        items = value.items() if isinstance(value, dict) else enumerate(value, 1)
        for key, child in items:
            prefix = '  ' * depth + '- ' + str(key) + ':'
            if isinstance(child, (dict, list)) and child:
                lines.append(prefix)
                render(child, depth + 1)
            else:
                text = child if isinstance(child, str) else json.dumps(child, ensure_ascii=False)
                parts = text.split('\n')
                lines.append(prefix + ' ' + parts[0])
                lines.extend('  ' * (depth + 1) + part for part in parts[1:])

    for key, value in data.items():
        lines.extend(['## ' + key, ''])
        render(value, 0)
        lines.append('')
    return '\n'.join(lines).rstrip()


def dialogue_script(scenario):
    """Preserve utterances and order in both supported source formats."""
    dialogue = scenario['dialogue']
    if scenario.get('dialogue_format', 'pairs') == 'pairs':
        dialogue = [
            {'role': role, 'content': row[role]}
            for row in dialogue for role in ('counselor', 'client')
        ]
    speakers = {'counselor': '상담사', 'client': '내담자'}
    return '\n\n'.join(speakers[row['role']] + ': ' + row['content'] for row in dialogue)


def initial_context(scenario, blocks=None):
    blocks = blocks or experiment_versions.input_blocks({})
    data = {k: scenario[k] for k in ('profile', 'states', 'taxonomy') if k != 'profile' or blocks['profile']}
    return ((scenario['task'] + '\n\n' if blocks['task'] else '') + '[초기 자료: 이번 상담자 발화 직전]\n'
            + ('## dialogue\n\n' + dialogue_script(scenario) if blocks['dialogue'] else '')
            + '\n\n' + markdown_data(data))


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
        if str(case_id).startswith('group1_'):
            return {'query': args['query'], 'status': 'not_recorded', 'matches': [],
                    'note': '선택한 프로필·히스토리에 별도로 연결된 과거 경험 자료가 없습니다.'}
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



def inspect_state_basis(config, args):
    if not isinstance(args, dict) or set(args) != {'state', 'reason'}:
        raise ValueError('state와 reason만 필요합니다.')
    if args['state'] not in ('trust', 'disclosure', 'change_confidence'):
        raise ValueError('지원하지 않는 상태입니다.')
    if not isinstance(args['reason'], str) or not 1 <= len(args['reason'].strip()) <= 500:
        raise ValueError('reason은 1~500자 문자열이어야 합니다.')
    path = Path(config.get('state_basis_file', 'cases/lab_meeting_state_basis.json'))
    if not path.is_absolute(): path = HERE / path
    data = json.loads(path.read_text(encoding='utf-8'))
    case_id = config.get('_experience_case_id') or load_scenario(config)['id']
    if data['scenario_id'] != case_id:
        if str(case_id).startswith('group1_'):
            return {'state': args['state'], 'current_state': config['_request_states'][args['state']],
                    'status': 'not_recorded', 'record': None,
                    'note': '선택한 프로필·히스토리에는 상태 변화의 시점·근거 기록이 없습니다.'}
        raise ValueError('현재 사례에 연결된 상태 근거 자료가 없습니다.')
    states = config.get('_request_states', load_scenario(config)['states'])
    record = data.get('states', {}).get(args['state'])
    return {'state': args['state'], 'current_state': states[args['state']],
            'status': 'found' if record else 'not_recorded',
            'source': str(path), 'record': record,
            'note': '기록된 시점과 근거를 반환합니다. 현재 상태를 자동 갱신하거나 감정을 판정하지 않습니다. 기록 부재는 숨겨진 감정이나 변화 원인을 뜻하지 않습니다.'}


def inspect_record(config, name, args):
    if not isinstance(args, dict) or set(args) != {'reason'} or not isinstance(args['reason'], str) or not args['reason'].strip() or len(args['reason']) > 500:
        raise ValueError('reason은 1~500자 문자열이어야 합니다.')
    key, default = {'inspect_current_event': ('current_event_file', 'cases/lab_meeting_current_event.json'),
                    'get_current_constraints': ('constraints_file', 'cases/lab_meeting_constraints.json')}[name]
    path = Path(config.get(key, default))
    if not path.is_absolute(): path = HERE / path
    data = json.loads(path.read_text(encoding='utf-8'))
    case_id = config.get('_experience_case_id') or config.get('scenario', {}).get('id') or load_scenario(config).get('id')
    if data['scenario_id'] != case_id:
        if str(case_id).startswith('group1_'):
            return {'status': 'not_recorded', 'record': None,
                    'note': '선택한 프로필·히스토리에 별도로 연결된 사건 상세·실행 여건 자료가 없습니다.'}
        raise ValueError('현재 사례에 연결된 자료가 없습니다.')
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


def definitions(mode=None, kind=None, depth=0, filesystem=None, experiment=None, version='basic'):
    client_version = experiment_versions.client_runtime(version)
    if client_version is not None:
        return [copy.deepcopy(client_version.TOOL)]
    tools = json.loads((HERE / 'tools.json').read_text(encoding='utf-8'))
    return tools + ([copy.deepcopy(state_summary.TOOL)] if version == 'state_summary' else [])


def selected_definitions(config, body):
    version = config.get('_experiment_version', experiment_versions.name(config, body))
    catalog = definitions(version=version)
    names = body.get('enabled_tools', config.get('_enabled_tools'))
    if names is None and 'enabled_tools' not in body:
        if version == 'state_summary':
            return [tool for tool in catalog if tool['function']['name'] in
                    ('retrieve_inner_state', *profile_retrieval.TOOL_NAMES)]
        is_profile = str(body.get('scenario', {}).get('id', '')).startswith('group1_')
        return [tool for tool in catalog if
                (tool['function']['name'] in profile_retrieval.TOOL_NAMES) == is_profile]
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError('enabled_tools는 도구 이름의 배열이어야 합니다.')
    if len(names) != len(set(names)):
        raise ValueError('도구 이름은 중복될 수 없습니다.')
    known = {tool['function']['name'] for tool in catalog}
    if set(names) - known:
        raise ValueError('지원하지 않는 도구 이름입니다: ' + ', '.join(sorted(set(names) - known)))
    return [tool for tool in catalog if tool['function']['name'] in names]


def build(config, body, filesystem, base_build, mode='dynamic', kind='main', depth=0, role=None, preview=False):
    client_version = experiment_versions.client_runtime(experiment_versions.name(config, body))
    if depth == 0 and client_version is not None:
        return client_version.build(config, body, __import__(__name__), base_build, preview)
    if depth == 0 and experiment_versions.name(config, body) == 'state_summary':
        return state_summary.build(config, body, __import__(__name__), base_build, preview)
    settings(config)
    cfg = copy.deepcopy(config)
    cfg['filesystem'] = {'enabled': False}
    cfg['system_role_mode'] = 'system'
    request = copy.deepcopy(body)
    request['system_prompt'] = body.get('system_prompt', config.get('system_prompt', MINIMAL_SYSTEM)) if depth == 0 else role
    if depth == 0:
        blocks = experiment_versions.input_blocks(config, body)
        if not blocks['system']:
            request['system_prompt'] = ''
        scenario = validate_scenario(body['scenario']) if 'scenario' in body else load_scenario(config)
        if str(scenario.get('id', '')).startswith('group1_'):
            scenario['profile'] = profile_retrieval.initial_profile(
                scenario['profile'], config.get('profile_context_mode', 'selective'))
        messages = request.get('messages')
        if not isinstance(messages, list) or not messages or messages[0].get('role') != 'user':
            raise ValueError('첫 메시지는 user여야 합니다.')
        if not isinstance(messages[0].get('content'), str):
            raise ValueError('상담자 발화는 문자열이어야 합니다.')
        if 'dialogue_turns' in scenario and messages[0]['content'] != scenario['selected_counselor_utterance']:
            raise ValueError('첫 상담사 발화가 마지막 활성 턴과 일치하지 않습니다. 초기 대화를 확인하세요.')
        if not preview and not messages[-1].get('content', '').strip():
            raise ValueError('상담자 발화를 입력하세요.')
        messages[0]['content'] = initial_context(scenario, blocks) + '\n\n[이번 상담자 발화]\n' + messages[0]['content']
    return base_build(cfg, request, selected_definitions(config, body), preview)


def execute(config, payload, client, upstream, frames, inspect, emit, cancelled, original, base_build, mode, version_dispatch=True):
    client_version = experiment_versions.client_runtime(config.get('_experiment_version'))
    if version_dispatch and client_version is not None:
        return client_version.execute(config, payload, client, upstream, frames, inspect, emit,
                          cancelled, original, base_build, mode, __import__(__name__))
    if version_dispatch and config.get('_experiment_version') == 'state_summary':
        return state_summary.execute(config, payload, client, upstream, frames, inspect, emit,
                                     cancelled, original, base_build, mode, __import__(__name__))
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
                'role_source':'model' if mode=='dynamic' and depth and kind!='retrieval' else 'developer'}

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
                if name == 'retrieve_inner_state' and (not isinstance(args, dict) or 'query' not in args):
                    return config['_inner_state_reader'](args)
                if name in ('inspect_current_event', 'get_current_constraints'):
                    return inspect_record(config, name, args)
                if name == 'inspect_state_basis':
                    return inspect_state_basis(config, args)
                if name == 'retrieve_experience':
                    return retrieve_experience(config, args)
                if name in (*profile_retrieval.TOOL_NAMES, 'retrieve_inner_state'):
                    if name == 'retrieve_inner_state':
                        database = state_summary.prepare_retrieval(config['_inner_state_context'], args, markdown_data)
                        if not database['records']:
                            return {'status': 'not_recorded', 'source': 'current_session.inner_states',
                                    'query': args['query'], 'record': None,
                                    'note': '현재 세션에 생성·저장된 내면 반응이 없습니다. 초기 대화의 속마음은 기록되어 있지 않습니다.'}
                    else:
                        database = profile_retrieval.prepare(config, name, args)
                    if created >= opts['max_agents']:
                        raise ValueError('전체 서브에이전트 생성 한도에 도달했습니다.')
                    created += 1
                    child_id = 'agent_' + uuid.uuid4().hex[:12]
                    child_history = [{'role': 'user', 'content': database['user']}]
                    child_config = copy.deepcopy(config)
                    child_config['generation'] = {key: payload[key] for key in
                        ('temperature', 'top_p', 'max_tokens', 'seed') if key in payload}
                    child_config['generation']['max_tokens'] = 128
                    child_request = base_build(child_config, {'system_prompt': database['system'],
                        'messages': child_history}, [], False)
                    result, subtree = run(child_id, 'retrieval', depth+1, database['role'],
                        child_request, child_history, agent_id, current_call, database['system'])
                    descendants.extend(subtree)
                    if name == 'retrieve_inner_state':
                        if result.get('finish_reason') != 'stop':
                            raise ValueError('내면 조회 담당이 정상 완료되지 않았습니다.')
                        return state_summary.retrieval_result(database, result['response'], child_id)
                    return profile_retrieval.selected_result(database, result['response'], child_id)
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
                child_config['_enabled_tools'] = [tool['function']['name'] for tool in payload.get('tools', [])]
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
