"""Mandatory per-turn trait routing; one private note committed after speech."""
import copy
import difflib
import json
from pathlib import Path
import re
import threading
import uuid

import experiment_versions
import reasoning_budget
from mcp_client import Cancelled
from state_summary import fingerprint, write_new
from tool_loop import validate_messages
from .tool_loop import run_turn

HERE = Path(__file__).resolve().parent
LOCK = threading.Lock()
BASIC_FIELDS = ('profile_id', 'name', 'age', 'gender', 'occupation', 'background',
                'demographic_context', 'presenting_problem', 'recent_context', 'desired_help')
EXCLUDED_FIELDS = ('childhood_data', 'situations', 'created_at', 'meta', 'start_rapport')
APPRAISAL_FIELDS = ('interpretation', 'immediate_feeling', 'relevant_evidence', 'uncertainty')
APPRAISAL_SCHEMA = {'type': 'object', 'properties': {
    key: {'type': 'string'} for key in APPRAISAL_FIELDS},
    'required': list(APPRAISAL_FIELDS), 'additionalProperties': False}
STAGE_TOOL = {'type': 'function', 'function': {
    'name': 'stage_client_update',
    'description': '이번 상담사 발화에 대한 appraisal, 다음 턴에 이어갈 내면 노트, 공개 발화 초안을 하나의 후보로 임시 저장합니다. '
        '검토가 필요한 경우 이 도구가 반환한 draft_id로 review_client_draft를 호출하세요. 충분하면 finalize_client_draft로 확정하세요. '
        '수정하거나 다른 후보를 만들 때 다시 호출할 수 있습니다. 기존 후보는 덮어쓰지 않습니다. '
        '이 호출만으로 공개 발화나 현재 내면 파일이 확정되지는 않습니다.',
    'parameters': {'type': 'object', 'properties': {
        'utterance': {'type': 'string', 'description': '이번 후보의 실제 내담자 발화. 상담사에게 하는 말만 작성합니다.'},
        'appraisal': {**APPRAISAL_SCHEMA, 'description': '이번 후보가 표현하는 주관적 해석·감정·근거·불확실성.'},
        'inner_notes': {'type': ['string', 'null'], 'description': '이 후보를 확정할 때 저장할 전체 내면 노트. null이면 이번 턴 시작 시 원본 파일을 유지합니다.'},
        'revises': {'type': ['string', 'null'], 'description': '기존 후보를 수정했다면 그 draft_id. 최초 후보 또는 독립적인 다른 후보이면 null.'}},
        'required': ['utterance', 'appraisal', 'inner_notes', 'revises'], 'additionalProperties': False}}}
REVIEW_TOOL = {'type': 'function', 'function': {
    'name': 'review_client_draft',
    'description': '저장된 초안을 검토 담당에게 전달하여 대화 맥락·내면 연속성·발화와 기록의 일치 여부를 확인합니다. '
        '현재 정보 사이에 충돌이 남거나, 초안이 이번 반응을 적절하게 표현하는지 구체적인 의문이 있을 때 사용하세요. '
        '같은 걱정의 반복·주저함·양가감정 자체는 오류가 아닙니다. 이미 충분하면 생략할 수 있습니다. '
        '검토 의견은 권고이며 메인이 수용·기각·수정·추가 검토 여부를 결정합니다. 후보 비교가 필요하면 question에 비교할 draft_id를 명시하세요.',
    'parameters': {'type': 'object', 'properties': {
        'draft_id': {'type': 'string', 'description': '이번 턴에 stage_client_update가 반환한 검토 대상 ID.'},
        'question': {'type': 'string', 'description': '검토할 구체적인 의문. 후보 비교 시 이번 턴의 다른 후보 ID도 포함할 수 있습니다.'}},
        'required': ['draft_id', 'question'], 'additionalProperties': False}}}
FINAL_TOOL = {'type': 'function', 'function': {
    'name': 'finalize_client_draft',
    'description': '이번 턴에 저장된 후보 하나를 최종 내담자 반응으로 선택하고 생성을 종료합니다. '
        '해당 후보의 발화·appraisal·내면 노트를 함께 확정합니다. 검토 호출은 필수가 아닙니다. '
        '검토했다면 남은 문제와 수정 필요성을 판단하고 선택하세요. 후보를 수정하려면 먼저 stage_client_update로 새 후보를 저장하세요. '
        '이 도구는 단독으로 호출하며, 확정 후 발화를 다시 작성하지 않습니다.',
    'parameters': {'type': 'object', 'properties': {
        'draft_id': {'type': 'string', 'description': '이번 턴에 저장한 최종 후보 ID.'},
        'reason': {'type': 'string', 'description': '이 후보를 확정해도 되는 이유. 검토를 받았다면 의견의 반영 또는 기각 근거를 간결하게 포함합니다.'}},
        'required': ['draft_id', 'reason'], 'additionalProperties': False}}}
TOOL = STAGE_TOOL
TOOLS = [STAGE_TOOL, REVIEW_TOOL, FINAL_TOOL]
REQUIRED_TOOLS = ('stage_client_update', 'finalize_client_draft')
REVIEW_SCHEMA = {'type': 'object', 'properties': {
    'assessment': {'type': 'string', 'enum': ['no_issue_found', 'revision_suggested', 'uncertain']},
    'findings': {'type': 'array', 'maxItems': 8, 'items': {'type': 'object', 'properties': {
        key: {'type': 'string'} for key in ('aspect', 'issue', 'evidence', 'suggestion')},
        'required': ['aspect', 'issue', 'evidence', 'suggestion'], 'additionalProperties': False}},
    'uncertainty': {'type': 'string'}},
    'required': ['assessment', 'findings', 'uncertainty'], 'additionalProperties': False}


def specification():
    return {'id': 'Q2', 'label': 'Q2 · 초안 / 선택적 검토 / 확정', 'view': 'client_drafts',
            'description': '원본 Q1의 특성 라우팅을 유지하고, 메인이 초안·선택적 검토·수정·확정을 결정합니다.',
            'system_prompt': (HERE / 'system.md').read_text(encoding='utf-8'), 'summary_system_prompt': '',
            'required_tools': list(REQUIRED_TOOLS),
            'default_reasoning_budgets': {'main': None, 'subagent': None},
            'subagent_prompts': [{'kind': 'trait_router', 'label': '특성 라우터'}, {'kind': 'draft_reviewer', 'label': '발화 초안 검토 담당'}],
            'tools': copy.deepcopy(TOOLS)}


def available_tools(body):
    names = body.get('enabled_tools')
    if names is None:
        return copy.deepcopy(TOOLS)
    known = {tool['function']['name'] for tool in TOOLS}
    if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or len(names) != len(set(names)) or set(names) - known or not set(REQUIRED_TOOLS) <= set(names)):
        raise ValueError('Q2 도구 이름을 확인하세요. 초안 저장과 확정 도구는 필수입니다.')
    return [copy.deepcopy(tool) for tool in TOOLS if tool['function']['name'] in names]


def split_profile(profile):
    basic = {key: copy.deepcopy(profile[key]) for key in BASIC_FIELDS if key in profile}
    traits = []
    def visit(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, path + '/' + str(key).replace('~', '~0').replace('/', '~1'))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + '/' + str(index))
        elif value is not None and value != '':
            traits.append({'trait_id': path, 'content': copy.deepcopy(value)})
    for key, value in profile.items():
        if key not in (*BASIC_FIELDS, *EXCLUDED_FIELDS):
            visit(value, '/' + str(key).replace('~', '~0').replace('/', '~1'))
    return basic, traits


def directory(config, session):
    if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', session):
        raise ValueError('올바르지 않은 Q2 session_id입니다.')
    return Path(config['_storage_dir']) / 'Q2' / session


def load(config, session):
    if not session:
        return {'content': (HERE / 'inner_notes_template.md').read_text(encoding='utf-8'), 'records': [], 'path': None}
    folder = directory(config, session)
    records = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((folder / 'appraisals').glob('*.json'))]
    path = folder / 'inner_notes.md'
    return {'content': path.read_text(encoding='utf-8') if path.exists() else (HERE / 'inner_notes_template.md').read_text(encoding='utf-8'),
            'records': records, 'path': str(path)}


def context(config, body, runtime, preview=False):
    scenario = runtime.validate_scenario(body['scenario']) if 'scenario' in body else runtime.load_scenario(config)
    messages = validate_messages(body.get('messages'), allow_empty=preview)
    if any(row.get('tool_calls') or row['role'] not in ('user', 'assistant') for row in messages):
        raise ValueError('Q2 이어가기에는 공개 발화만 사용하세요.')
    note = load(config, body.get('session_id'))
    if note['records']:
        if note['records'][-1]['scenario_hash'] != fingerprint(scenario):
            raise ValueError('Q2 초기 자료가 달라졌습니다. 새 대화를 시작하세요.')
        expected = [row for record in note['records'] for row in (
            {'role': 'user', 'content': record['counselor_utterance']}, {'role': 'assistant', 'content': record['utterance']})]
        if not config.get('_paired_public_dialogue'):
            if len(messages) == 1:
                messages = expected + messages
            elif messages[:-1] != expected:
                raise ValueError('Q2 공개 대화가 저장된 기록과 다릅니다.')
    basic, traits = split_profile(scenario['profile'])
    blocks = experiment_versions.input_blocks(config, body)
    if not blocks['profile']:
        basic, traits = {}, []
    if config.get('_paired_public_dialogue') is not None:
        public = config['_paired_public_dialogue'][:-1]
        if not blocks['dialogue']:
            public = public[config.get('_paired_initial_count', 0):]
        dialogue = '\n\n'.join(('상담사' if row['role'] == 'counselor' else '내담자') + ': ' + row['content'] for row in public)
    else:
        dialogue = (runtime.dialogue_script(scenario) + '\n\n' if blocks['dialogue'] else '')
        dialogue += '\n\n'.join(('상담사' if row['role'] == 'user' else '내담자') + ': ' + row['content'] for row in messages[:-1])
    return {'scenario': scenario, 'blocks': blocks, 'basic': basic, 'traits': traits, 'note': note,
            'dialogue': dialogue, 'current': messages[-1]['content'], 'messages': messages}


def payload_for(config, body, ctx, selected, runtime, base_build, preview=False):
    text = (runtime.markdown_data({'기본 정보': ctx['basic']})
            + '\n\n## 공개 대화\n\n' + ctx['dialogue']
            + '\n\n## 이번 상담사 발화\n\n' + ctx['current']
            + '\n\n' + runtime.markdown_data({'이번 상황의 관련 특성': selected})
            + '\n\n## 내면 노트 · 현재 파일 원문\n\n' + ctx['note']['content'])
    request = {**body, 'messages': [{'role': 'user', 'content': text}]}
    request.setdefault('system_prompt', specification()['system_prompt'])
    if not ctx['blocks']['system']:
        request['system_prompt'] = ''
    # The old task/states/taxonomy belong to other versions, not this experiment.
    return base_build(config, request, available_tools(body), preview)


def build(config, body, runtime, base_build, preview=False):
    ctx = context(config, body, runtime, preview)
    build = reasoning_budget.wrap_builder(base_build, body.get('client_reasoning_budgets'))
    return payload_for(config, body, ctx, [], runtime, build, preview)


def parse_json(text):
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(text)


def selection(response, traits):
    result = parse_json(response)
    if not isinstance(result, dict) or set(result) != {'selections'} or not isinstance(result['selections'], list):
        raise ValueError('특성 라우터는 selections 배열을 반환해야 합니다.')
    known = {row['trait_id']: row for row in traits}
    selected, seen = [], set()
    for row in result['selections']:
        if (not isinstance(row, dict) or set(row) != {'trait_id', 'reason'}
                or not isinstance(row['trait_id'], str) or row['trait_id'] not in known
                or row['trait_id'] in seen or not isinstance(row['reason'], str) or not row['reason'].strip()):
            raise ValueError('특성 라우터가 알 수 없는 ID·중복 ID 또는 비어 있는 근거를 반환했습니다.')
        seen.add(row['trait_id'])
        selected.append({**copy.deepcopy(known[row['trait_id']]), 'reason': row['reason']})
    return selected


def validate_update(args):
    if not isinstance(args, dict) or set(args) != {'appraisal', 'inner_notes'}:
        raise ValueError('appraisal과 inner_notes를 입력하세요.')
    appraisal = args['appraisal']
    if (not isinstance(appraisal, dict) or set(appraisal) != set(APPRAISAL_FIELDS)
            or any(not isinstance(appraisal[key], str) for key in APPRAISAL_FIELDS)
            or any(not appraisal[key].strip() for key in APPRAISAL_FIELDS if key != 'uncertainty')):
        raise ValueError('appraisal의 의미·즉각 감정·근거는 비어 있지 않은 문자열이어야 합니다. uncertainty는 문자열입니다.')
    note = args['inner_notes']
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise ValueError('inner_notes는 비어 있지 않은 원문 또는 null이어야 합니다.')
    return copy.deepcopy(args)


def execute(config, payload, client, upstream, frames, inspect, emit, cancelled, original, base_build, mode, runtime):
    body = config['_request_body']
    base_build = reasoning_budget.wrap_builder(base_build, body.get('client_reasoning_budgets'))
    ctx = context(config, body, runtime)
    info = config['_turn_info']
    folder = directory(config, info['session_id'])
    before = ctx['note']['content']
    note_path = folder / 'inner_notes.md'
    emit('version_event', version='Q2', section='inner_notes', action='snapshot',
         data={'content': before, 'path': str(note_path), 'label': '비공개 내면 노트'})
    router_role = (HERE / 'trait_router.md').read_text(encoding='utf-8')
    router_user = (runtime.markdown_data({'기본 정보': ctx['basic'], '전체 특성 목록': ctx['traits']})
                   + '\n\n## 공개 대화\n\n' + ctx['dialogue'] + '\n\n## 이번 상담사 발화\n\n' + ctx['current']
                   + '\n\n## 현재 내면 노트\n\n' + before)
    router = base_build(config, {'system_prompt': router_role, 'messages': [{'role': 'user', 'content': router_user}],
                                 '_reasoning_budget_role': 'subagent'}, [], False)
    router['structured_outputs'] = {'json': {'type': 'object', 'properties': {'selections': {'type': 'array', 'items': {
        'type': 'object', 'properties': {'trait_id': {'type': 'string'}, 'reason': {'type': 'string'}},
        'required': ['trait_id', 'reason'], 'additionalProperties': False}}}, 'required': ['selections'], 'additionalProperties': False}}
    agent_id = 'traits_' + uuid.uuid4().hex[:12]
    meta = {'agent_id': agent_id, 'agent_name': '특성 라우터', 'agent_kind': 'trait_router',
            'parent_agent_id': 'main', 'parent_call_id': None, 'depth': 1,
            'role_instruction': router_role, 'role_source': 'developer'}
    routed = {}
    def router_event(kind, **data):
        if kind == 'done':
            routed.update(data)
        emit('agent_event', **meta, event={'type': kind, **data})
    router_event('request', phase='매 턴 자동 특성 라우팅', iteration=1, payload=copy.deepcopy(router))
    try:
        run_turn(config, router, None, upstream, frames, inspect, router_event, cancelled, [{'role': 'user', 'content': router_user}])
        if routed.get('finish_reason') != 'stop':
            raise ValueError('특성 라우터 응답이 정상 완료되지 않았습니다: ' + str(routed.get('finish_reason')))
        selected = selection(routed['response'], ctx['traits'])
    except Exception as error:
        router_event('cancelled' if isinstance(error, Cancelled) else 'error', error=str(error))
        raise
    emit('version_event', version='Q2', section='traits', action='routed', data={'selected': selected, 'automatic': True})
    actual = payload_for(config, body, ctx, selected, runtime, base_build)
    emit('request', payload=copy.deepcopy(actual), iteration=1, phase='내담자 해석·내면 기록·발화 생성')
    final, drafts, reviews, actions = {}, {}, [], []
    agent_usage = {agent_id: routed.get('usage', {})}

    def answer(data):
        return {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'structuredContent': data}

    class InnerTools:
        requires_finalize = True
        opts = {'max_calls': config.get('delegation', {}).get('max_calls', 8), 'max_result_chars': 16000}
        finalized = None

        def call(self, name, arguments):
            if name == 'stage_client_update':
                if not isinstance(arguments, dict) or set(arguments) != {'utterance', 'appraisal', 'inner_notes', 'revises'}:
                    raise ValueError('utterance, appraisal, inner_notes, revises를 입력하세요.')
                change = validate_update({key: arguments[key] for key in ('appraisal', 'inner_notes')})
                utterance = arguments['utterance']
                if not isinstance(utterance, str) or not utterance.strip():
                    raise ValueError('utterance는 비어 있지 않은 내담자 발화여야 합니다.')
                parent = arguments['revises']
                if parent is not None and (not isinstance(parent, str) or parent not in drafts):
                    raise ValueError('revises는 이번 턴의 기존 draft_id 또는 null이어야 합니다.')
                draft_id = 'draft_' + str(len(drafts) + 1)
                after = before if change['inner_notes'] is None else change['inner_notes']
                candidate = {**change, 'draft_id': draft_id, 'revises': parent,
                             'utterance': utterance.strip(), 'notes_after': after}
                path = folder / 'drafts' / info['turn_id'] / (draft_id + '.json')
                write_new(path, candidate)
                drafts[draft_id] = candidate
                actions.append({'action': 'stage', 'draft_id': draft_id, 'revises': parent})
                emit('version_event', version='Q2', section='drafts', action='staged', data={**candidate, 'path': str(path)})
                diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile='기존 내면', tofile='초안 내면', lineterm=''))
                emit('version_event', version='Q2', section='appraisal', action='staged', data=change['appraisal'])
                emit('version_event', version='Q2', section='inner_notes', action='staged',
                     data={'content': after, 'before': before, 'diff': diff, 'path': str(note_path), 'draft_id': draft_id, 'pending': True})
                return answer({'draft_id': draft_id, 'staged': True, 'saved': False,
                               'note_changed': after != before, 'message': '초안을 임시 저장했습니다. 검토가 필요하면 review_client_draft, 충분하면 finalize_client_draft를 호출하세요.'})
            if name not in ('review_client_draft', 'finalize_client_draft'):
                raise ValueError('Q2에서 사용할 수 없는 도구입니다.')
            field = 'question' if name == 'review_client_draft' else 'reason'
            if not isinstance(arguments, dict) or set(arguments) != {'draft_id', field}:
                raise ValueError('draft_id와 ' + field + '을 입력하세요.')
            draft_id = arguments['draft_id']
            if not isinstance(draft_id, str) or draft_id not in drafts:
                raise ValueError('이번 턴에 저장된 draft_id를 사용하세요.')
            if not isinstance(arguments[field], str) or not arguments[field].strip():
                raise ValueError(field + '은 비어 있지 않은 문자열이어야 합니다.')
            if name == 'finalize_client_draft':
                self.finalized = {'draft_id': draft_id, 'reason': arguments['reason']}
                actions.append({'action': 'finalize', **self.finalized})
                emit('version_event', version='Q2', section='selection', action='selected', data={**self.finalized, 'pending': True})
                return answer({'selected_draft_id': draft_id, 'pending': True, 'saved': False,
                               'message': '선택한 초안으로 실행을 마치고 발화와 내면 기록을 함께 확정합니다.'})
            if 'review_client_draft' not in {tool['function']['name'] for tool in actual['tools']}:
                raise ValueError('초안 검토 도구가 비활성화되어 있습니다.')
            reviewer_role = (HERE / 'draft_reviewer.md').read_text(encoding='utf-8')
            review_user = (actual['messages'][1 if actual['messages'][0]['role'] == 'system' else 0]['content']
                           + '\n\n# 검토할 내담자 초안\n' + json.dumps(drafts[draft_id], ensure_ascii=False, indent=2)
                           + '\n\n# 메인의 검토 요청\n' + arguments['question'])
            # Explicitly referenced candidates can be compared without sharing
            # other turns, all private traits, or unrelated previous reviews.
            comparisons = [value for key, value in drafts.items() if key != draft_id and re.search(r'(?<![A-Za-z0-9_])' + re.escape(key) + r'(?![A-Za-z0-9_])', arguments['question'])]
            if comparisons:
                review_user += '\n\n# 요청에서 지정한 비교 후보\n' + json.dumps(comparisons, ensure_ascii=False, indent=2)
            review_payload = base_build(config, {'system_prompt': reviewer_role,
                'messages': [{'role': 'user', 'content': review_user}], '_reasoning_budget_role': 'subagent'}, [], False)
            review_payload['structured_outputs'] = {'json': copy.deepcopy(REVIEW_SCHEMA)}
            reviewer_id = 'review_' + uuid.uuid4().hex[:12]
            parent_call_id = getattr(self, 'active_call_id', None)
            review_meta = {'agent_id': reviewer_id, 'agent_name': '발화 초안 검토 담당', 'agent_kind': 'draft_reviewer',
                           'parent_agent_id': 'main', 'parent_call_id': parent_call_id, 'depth': 1,
                           'role_instruction': reviewer_role, 'role_source': 'developer'}
            result = {}
            def review_event(kind, **data):
                if kind == 'done':
                    result.update(data)
                emit('agent_event', **review_meta, event={'type': kind, **data})
            review_event('request', phase='발화 초안 검토 · ' + draft_id, iteration=1, payload=copy.deepcopy(review_payload))
            try:
                run_turn(config, review_payload, None, upstream, frames, inspect, review_event, cancelled,
                         [{'role': 'user', 'content': review_user}])
                agent_usage[reviewer_id] = result.get('usage', {})
                if result.get('finish_reason') != 'stop':
                    raise ValueError('초안 검토 응답이 정상 완료되지 않았습니다: ' + str(result.get('finish_reason')))
                feedback = validate_review(parse_json(result['response']))
            except Exception as error:
                review_event('cancelled' if isinstance(error, Cancelled) else 'error', error=str(error))
                raise
            review = {'review_id': reviewer_id, 'draft_id': draft_id, 'question': arguments['question'], **feedback}
            path = folder / 'reviews' / info['turn_id'] / (reviewer_id + '.json')
            write_new(path, review)
            reviews.append(review)
            actions.append({'action': 'review', 'draft_id': draft_id, 'review_id': reviewer_id})
            emit('version_event', version='Q2', section='reviews', action='completed', data={**review, 'path': str(path)})
            return answer(review)

        def terminal_response(self):
            return drafts[self.finalized['draft_id']]['utterance'] if self.finalized else None

    tools = InnerTools()
    def main_event(kind, **data):
        if kind == 'done':
            final.update(data)
        else:
            emit(kind, **data)
    try:
        run_turn(config, actual, tools, upstream, frames, inspect, main_event, cancelled, ctx['messages'])
        if not tools.finalized or final.get('finish_reason') != 'stop':
            raise ValueError('Q2는 finalize_client_draft로 저장된 초안을 확정해야 합니다.')
        if cancelled():
            raise Cancelled('Q2 내면 기록 확정 전에 중단되었습니다.')
        chosen = drafts[tools.finalized['draft_id']]
        after = chosen['notes_after']
        record = {'turn': len(ctx['note']['records']) + 1, 'turn_id': info['turn_id'],
                  'scenario_hash': fingerprint(ctx['scenario']), 'selected_traits': selected,
                  'counselor_utterance': ctx['current'], 'appraisal': chosen['appraisal'],
                  'notes_before': before, 'notes_after': after, 'utterance': chosen['utterance'],
                  'drafts': list(drafts.values()), 'reviews': reviews, 'actions': actions,
                  'finalized_draft_id': chosen['draft_id'], 'finalization_reason': tools.finalized['reason']}
        with LOCK:
            current = load(config, info['session_id'])
            if (current['content'] != before or [r['turn_id'] for r in current['records']] != [r['turn_id'] for r in ctx['note']['records']]):
                raise ValueError('다른 실행 또는 파일 편집이 먼저 내면 기록을 변경했습니다. 덮어쓰기를 중단합니다.')
            if cancelled():
                raise Cancelled('Q2 내면 기록 확정 전에 중단되었습니다.')
            from counselors.P1.runtime import write
            record_path = folder / 'appraisals' / f"{record['turn']:06d}_{info['turn_id']}.json"
            write_new(record_path, record)
            try:
                write(note_path, after)
            except Exception:
                record_path.unlink(missing_ok=True)
                raise
        emit('version_event', version='Q2', section='inner_notes', action='updated',
             data={'content': after, 'path': str(note_path), 'draft_id': chosen['draft_id'], 'pending': False, 'label': '비공개 내면 노트'})
        emit('version_event', version='Q2', section='appraisal', action='committed', data=chosen['appraisal'])
        emit('version_event', version='Q2', section='selection', action='committed', data={**tools.finalized, 'pending': False})
    except Exception as error:
        emit('version_event', version='Q2', section='inner_notes', action='discarded',
             data={'content': before, 'path': str(note_path), 'pending': False, 'discarded': True})
        emit('version_event', version='Q2', section='selection', action='discarded', data={'pending': False, 'discarded': True})
        if isinstance(error, Cancelled):
            raise Cancelled('Q2 생성을 중단했습니다. 초안은 기록에 남고 이번 내면 변경안은 확정하지 않습니다.') from error
        raise
    main_usage = final.get('usage', {})
    subagent_usage = {}
    for usage in agent_usage.values():
        for key, value in usage.items():
            subagent_usage[key] = subagent_usage.get(key, 0) + value
    total = {key: main_usage.get(key, 0) + subagent_usage.get(key, 0) for key in set(main_usage) | set(subagent_usage)}
    emit('done', response=record['utterance'], history=ctx['messages'] + [{'role': 'assistant', 'content': record['utterance']}],
         finish_reason='stop', response_source='finalized_draft', usage=total, main_usage=main_usage, subagent_usage=subagent_usage,
         agent_usage={'main': main_usage, **agent_usage}, experiment_version='Q2')


def validate_review(value):
    if (not isinstance(value, dict) or set(value) != {'assessment', 'findings', 'uncertainty'}
            or value['assessment'] not in ('no_issue_found', 'revision_suggested', 'uncertain')
            or not isinstance(value['findings'], list) or len(value['findings']) > 8
            or not isinstance(value['uncertainty'], str)):
        raise ValueError('초안 검토의 assessment, findings, uncertainty 형식을 확인하세요.')
    for finding in value['findings']:
        if (not isinstance(finding, dict) or set(finding) != {'aspect', 'issue', 'evidence', 'suggestion'}
                or any(not isinstance(text, str) or not text.strip() for text in finding.values())):
            raise ValueError('검토 의견에는 aspect, issue, evidence, suggestion을 근거와 함께 작성하세요.')
    if value['assessment'] == 'revision_suggested' and not value['findings']:
        raise ValueError('수정 권고에는 구체적인 문제와 근거가 필요합니다.')
    return copy.deepcopy(value)
