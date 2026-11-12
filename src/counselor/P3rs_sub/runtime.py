"""P3rs_sub: P3rs with a separate observer call before every counselor utterance.

Step 1, observer (subagent): reads the numbered public dialogue, the counselor's memo and the full
rupture guide, then returns the withdrawal marker in the latest client reply, a recommended action
and short advice. Step 2, main counselor: reads the public dialogue and that advice and writes the
utterance. The runtime always runs step 1; the main counselor is not obliged to follow the advice,
and the record keeps both so adherence can be counted. Everything stays on the counselor side.
"""
import copy
from pathlib import Path

import runtime.user_templates as user_templates
from counselor.P3q.runtime import numbered
from counselor.P3rs.runtime import KINDS, MOVES, asked, call, notes, record, state, valid_marker, valid_move

HERE = Path(__file__).resolve().parent
VERSION = 'P3rs_sub'
GUIDE = HERE / 'guides' / 'rupture.md'
SUBAGENT_PROMPTS = {'observer': {'label': '관찰 담당 서브에이전트', 'filename': 'observer.md'}}
ACTIONS = {'continue': '지금 상담을 이어 가기', 'check_in': '개입 확인', 'rupture_explore': '균열 탐색'}
OBSERVER_SCHEMA = {'type': 'object', 'properties': {
    'marker': {'type': 'object', 'properties': {
        'found': {'type': 'boolean'}, 'kind': {'type': 'string', 'enum': list(KINDS)}, 'quote': {'type': 'string'}},
        'required': ['found', 'kind', 'quote'], 'additionalProperties': False},
    'recommend': {'type': 'string', 'enum': list(ACTIONS)},
    'target_turn': {'type': 'integer', 'minimum': 0},
    'advice': {'type': 'string', 'minLength': 1}},
    'required': ['marker', 'recommend', 'target_turn', 'advice'], 'additionalProperties': False}
MAIN_SCHEMA = {'type': 'object', 'properties': {
    'move': {'type': 'string', 'enum': list(MOVES)},
    'rupture_turn': {'type': 'integer', 'minimum': 0},
    'check_turn': {'type': 'integer', 'minimum': 0},
    'utterance': {'type': 'string', 'minLength': 1}},
    'required': ['move', 'rupture_turn', 'check_turn', 'utterance'], 'additionalProperties': False}


def specification():
    return {'id': VERSION, 'label': 'P3rs_sub · 관찰 담당 서브에이전트 + 균열 탐색', 'view': 'none',
            'description': '매 턴 별도 관찰 담당이 공개 대화·상담사 메모·균열 가이드를 읽고 표지와 권하는 행동을 돌려주며, 메인 상담사가 그 조언을 참고해 발화합니다.',
            'system_prompt': (HERE / 'system.md').read_text(encoding='utf-8'),
            'subagent_prompts': [{'kind': kind, 'label': item['label']} for kind, item in SUBAGENT_PROMPTS.items()]}


def advice_text(observed):
    """What the main counselor reads: the observer's recommendation, target and advice."""
    action, target = observed['recommend'], observed['target_turn']
    line = '권하는 행동: ' + ACTIONS[action]
    if action == 'check_in':
        line += f' (내 {target}턴 발화가 어떻게 들렸는지)'
    elif action == 'rupture_explore':
        line += f' ({target}턴 내담자 답의 순간)'
    mark = observed['marker']
    seen = (f"최근 내담자 답의 물러남 표지: {KINDS[mark['kind']]} · \"{mark['quote'].strip()}\""
            if mark['found'] and mark['kind'] != 'none' else '최근 내담자 답의 물러남 표지: 없음')
    return '\n'.join([line, seen, '조언: ' + observed['advice'].strip()])


def followed(observed, parsed):
    """Whether the main counselor's move matches the observer's recommendation."""
    action = observed['recommend']
    if action == 'check_in':
        return parsed['move'] == 'check_in' and parsed['check_turn'] == observed['target_turn']
    if action == 'rupture_explore':
        return parsed['move'] == 'rupture_explore' and parsed['rupture_turn'] == observed['target_turn']
    return parsed['move'] not in ('check_in', 'rupture_explore')


def run(config, session, backend, base_build, emit, cancelled, directory):
    public = session['public_dialogue']
    records, pending, unchecked = state(session, VERSION)
    role = (HERE / 'observer.md').read_text(encoding='utf-8')
    guide = GUIDE.read_text(encoding='utf-8')
    if not guide.strip():
        raise ValueError('균열 가이드 파일이 비어 있습니다.')
    meta = {'agent_id': 'rupture_observer', 'agent_name': '관찰 담당', 'agent_kind': 'rupture_observer',
            'parent_agent_id': 'main', 'parent_call_id': None, 'depth': 1,
            'role_instruction': role, 'role_source': 'developer'}

    def child(kind, **data):
        # Stored like other subagents; the finished call is a `done` event with its reasoning.
        emit('agent_event', **meta, event={'type': 'done' if kind == 'model_result' else kind, **data})

    observer_user = user_templates.render('counselor', VERSION, 'observer_user', dialogue=numbered(public),
                                          notes=notes(pending, unchecked, asked(records)), guide=guide)
    observed, usage = call(config, backend, base_build, child, cancelled, role, observer_user, OBSERVER_SCHEMA,
                           VERSION + ' 관찰', label=VERSION + ' 관찰 담당', body={'_counselor_budget_role': 'subagent'})
    if (not valid_marker(observed.get('marker')) or observed.get('recommend') not in ACTIONS
            or type(observed.get('target_turn')) is not int
            or not isinstance(observed.get('advice'), str) or not observed['advice'].strip()):
        raise ValueError(VERSION + ' 관찰 담당 응답 형식이 올바르지 않습니다.')
    advice = advice_text(observed)
    user = user_templates.render('counselor', VERSION, dialogue=numbered(public), advice=advice)
    parsed, main_usage = call(config, backend, base_build, emit, cancelled, session['settings']['counselor_system_prompt'],
                              user, MAIN_SCHEMA, VERSION + ' 발화 생성', label=VERSION)
    if not valid_move(parsed):
        raise ValueError(VERSION + ' 응답 형식이 올바르지 않습니다.')
    row, pending, unchecked = record({**parsed, 'marker': observed['marker']}, public, pending, unchecked)
    row['observer'] = {'recommend': observed['recommend'], 'target_turn': observed['target_turn'],
                       'advice': observed['advice'].strip(), 'followed': followed(observed, parsed), 'shown': advice}
    checkpoint = {'version': VERSION, 'records': [*copy.deepcopy(records), row], 'pending': pending, 'unchecked': unchecked}
    emit('version_event', version=VERSION, section='rupture', action='recorded', data=row)
    for key, value in main_usage.items():
        usage[key] = usage.get(key, 0) + value
    return parsed['utterance'].strip(), checkpoint, usage
