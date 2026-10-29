"""Mandatory per-turn trait routing; one private note committed after speech."""
import runtime.user_templates as user_templates
import copy
import difflib
import json
from pathlib import Path
import re
import threading
import uuid

import client.registry as experiment_versions
import runtime.reasoning_budget as reasoning_budget
from runtime.cancellation import Cancelled
from client.state_summary.runtime import fingerprint, write_new
from runtime.recovery_tool_loop import run_turn, validate_messages

HERE = Path(__file__).resolve().parent
LOCK = threading.Lock()
BASIC_FIELDS = ('profile_id', 'name', 'age', 'gender', 'occupation', 'background',
                'demographic_context', 'presenting_problem', 'recent_context', 'desired_help')
EXCLUDED_FIELDS = ('childhood_data', 'situations', 'created_at', 'meta', 'start_rapport')
APPRAISAL_FIELDS = ('interpretation', 'immediate_feeling', 'relevant_evidence', 'uncertainty')
APPRAISAL_SCHEMA = {'type': 'object', 'properties': {
    key: {'type': 'string'} for key in APPRAISAL_FIELDS},
    'required': list(APPRAISAL_FIELDS), 'additionalProperties': False}
TOOL = {'type': 'function', 'function': {
    'name': 'stage_client_update',
    'description': '이번 상담사 말을 받아들인 의미·즉각 감정·근거를 비공개 appraisal로 기록하고, '
        '다음 턴에 이어갈 내면 노트 변경안을 제출합니다. 공개 발화를 마치기 전에 이번 해석을 기록하세요. '
        '현재 노트 원문은 기본 입력에 있습니다. 현재 인식·걱정·말할 준비가 바뀌거나 정리가 필요하면 '
        'inner_notes에 갱신할 전체 원문을, 변경이 없으면 null을 입력하세요. '
        '호출은 임시 저장이며 공개 발화 정상 완료 후 확정됩니다. 오류·중단 시 폐기됩니다. '
        '같은 턴에서 다시 제출하면 최신 변경안으로 교체합니다. 상담사에게는 전달되지 않습니다.',
    'parameters': {'type': 'object', 'properties': {
        'appraisal': {**APPRAISAL_SCHEMA, 'description': '이번 발화의 주관적 해석. 근거에는 사용한 공개 발화·기본 정보·선정 특성·기존 노트를 구분하세요. 불명확한 점이 없으면 uncertainty는 빈 문자열입니다.'},
        'inner_notes': {'type': ['string', 'null'], 'description': '다음 턴의 현재 내면으로 유지할 노트 전체 원문. 기존 파일을 유지하면 null.'}},
        'required': ['appraisal', 'inner_notes'], 'additionalProperties': False}}}


def specification():
    return {'id': 'Q1_s1', 'label': 'Q1_s1 · 특성 라우팅 / 내면 노트', 'view': 'client_inner',
            'description': '매 턴 관련 특성을 자동 선정하고, 이번 해석과 비공개 내면 노트를 기록합니다. 노트는 공개 발화 성공 후 확정합니다.',
            'system_prompt': (HERE / 'system.md').read_text(encoding='utf-8'), 'summary_system_prompt': '',
            'subagent_prompts': [{'kind': 'trait_router', 'label': '특성 라우터'}],
            'tools': [copy.deepcopy(TOOL)]}


def split_profile(profile, semantic=False):
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
            path = '/' + str(key).replace('~', '~0').replace('/', '~1')
            if semantic:
                # Keep a belief's statement, meaning and conditions together;
                # a domain/category label alone is not a personal disposition.
                units = list(enumerate(value)) if isinstance(value, list) else [(None, value)]
                for index, content in units:
                    if content is not None and content != '':
                        traits.append({'trait_id': path + ('/' + str(index) if index is not None else ''),
                                       'content': copy.deepcopy(content)})
            else:
                visit(value, path)
    return basic, traits


def directory(config, session):
    if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', session):
        raise ValueError('올바르지 않은 Q1_s1 session_id입니다.')
    return Path(config['_storage_dir']) / 'Q1_s1' / session


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
        raise ValueError('Q1_s1 이어가기에는 공개 발화만 사용하세요.')
    note = load(config, body.get('session_id'))
    if note['records']:
        if note['records'][-1]['scenario_hash'] != fingerprint(scenario):
            raise ValueError('Q1_s1 초기 자료가 달라졌습니다. 새 대화를 시작하세요.')
        expected = [row for record in note['records'] for row in (
            {'role': 'user', 'content': record['counselor_utterance']}, {'role': 'assistant', 'content': record['utterance']})]
        if not config.get('_paired_public_dialogue'):
            if len(messages) == 1:
                messages = expected + messages
            elif messages[:-1] != expected:
                raise ValueError('Q1_s1 공개 대화가 저장된 기록과 다릅니다.')
    basic, traits = split_profile(scenario['profile'], body.get('side_trait_units') == 'semantic')
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
    main_traits = selected
    if body.get('side_router_reason') == 'log_only':
        main_traits = [{key: copy.deepcopy(value) for key, value in row.items() if key != 'reason'}
                       for row in selected]
    text = user_templates.render('client', 'Q1_s1', basic_info=user_templates.md(runtime.markdown_data, ctx['basic']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'],
        selected_traits=user_templates.md(runtime.markdown_data, main_traits), state_notes=ctx['note']['content'])
    request = {**body, 'messages': [{'role': 'user', 'content': text}]}
    request.setdefault('system_prompt', specification()['system_prompt'])
    if not ctx['blocks']['system']:
        request['system_prompt'] = ''
    # The old task/states/taxonomy belong to other versions, not this experiment.
    return base_build(config, request, [copy.deepcopy(TOOL)], preview)


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
    emit('version_event', version='Q1_s1', section='inner_notes', action='snapshot',
         data={'content': before, 'path': str(note_path), 'label': '비공개 내면 노트'})
    router_role = (HERE / 'trait_router.md').read_text(encoding='utf-8')
    router_user = user_templates.render('client', 'Q1_s1', 'trait_router_user',
        basic_info=user_templates.md(runtime.markdown_data, ctx['basic']), traits=user_templates.md(runtime.markdown_data, ctx['traits']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'], state_notes=before)
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
    emit('version_event', version='Q1_s1', section='traits', action='routed', data={'selected': selected, 'automatic': True})
    actual = payload_for(config, body, ctx, selected, runtime, base_build)
    emit('request', payload=copy.deepcopy(actual), iteration=1, phase='내담자 해석·내면 기록·발화 생성')
    staged, final = {}, {}
    class InnerTools:
        opts = {'max_calls': config.get('delegation', {}).get('max_calls', 8), 'max_result_chars': 16000}
        def call(self, name, arguments):
            if name != TOOL['function']['name']:
                raise ValueError('Q1_s1에서는 stage_client_update만 사용할 수 있습니다.')
            change = validate_update(arguments)
            staged.clear()
            staged.update(change)
            after = before if change['inner_notes'] is None else change['inner_notes']
            diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile='기존 내면', tofile='변경안', lineterm=''))
            emit('version_event', version='Q1_s1', section='appraisal', action='staged', data=change['appraisal'])
            emit('version_event', version='Q1_s1', section='inner_notes', action='staged',
                 data={'content': after, 'before': before, 'diff': diff, 'path': str(note_path), 'pending': True})
            return {'content': [{'type': 'text', 'text': '변경안을 임시 접수했습니다. 공개 발화가 정상 완료되면 확정합니다.'}],
                    'structuredContent': {'staged': True, 'saved': False, 'note_changed': after != before}}
    def main_event(kind, **data):
        if kind == 'done':
            final.update(data)
            emit('model_result', response=data['response'], reasoning=data.get('reasoning', ''), finish_reason=data['finish_reason'])
        else:
            emit(kind, **data)
    try:
        run_turn(config, actual, InnerTools(), upstream, frames, inspect, main_event, cancelled, ctx['messages'])
        if final.get('finish_reason') != 'stop' or not final.get('response', '').strip():
            raise ValueError('Q1_s1 내담자 발화가 정상 완료되지 않았습니다.')
        if not staged:
            raise ValueError('이번 appraisal가 기록되지 않았습니다. stage_client_update 호출 후 발화를 생성해야 합니다.')
        if cancelled():
            raise Cancelled('Q1_s1 내면 기록 확정 전에 중단되었습니다.')
        after = before if staged['inner_notes'] is None else staged['inner_notes']
        record = {'turn': len(ctx['note']['records']) + 1, 'turn_id': info['turn_id'],
                  'scenario_hash': fingerprint(ctx['scenario']), 'selected_traits': selected,
                  'counselor_utterance': ctx['current'], 'appraisal': staged['appraisal'],
                  'notes_before': before, 'notes_after': after, 'utterance': final['response'].strip()}
        with LOCK:
            current = load(config, info['session_id'])
            if (current['content'] != before or [r['turn_id'] for r in current['records']] != [r['turn_id'] for r in ctx['note']['records']]):
                raise ValueError('다른 실행 또는 파일 편집이 먼저 내면 기록을 변경했습니다. 덮어쓰기를 중단합니다.')
            if cancelled():
                raise Cancelled('Q1_s1 내면 기록 확정 전에 중단되었습니다.')
            from counselor.P1.runtime import write
            record_path = folder / 'appraisals' / f"{record['turn']:06d}_{info['turn_id']}.json"
            write_new(record_path, record)
            try:
                write(note_path, after)
            except Exception:
                record_path.unlink(missing_ok=True)
                raise
        emit('version_event', version='Q1_s1', section='inner_notes', action='updated',
             data={'content': after, 'path': str(note_path), 'pending': False, 'label': '비공개 내면 노트'})
        emit('version_event', version='Q1_s1', section='appraisal', action='committed', data=staged['appraisal'])
    except Exception as error:
        emit('version_event', version='Q1_s1', section='inner_notes', action='discarded',
             data={'content': before, 'path': str(note_path), 'pending': False, 'discarded': True})
        if isinstance(error, Cancelled):
            raise Cancelled('Q1_s1 생성을 중단했습니다. 이번 내면 변경안은 폐기했습니다.') from error
        raise
    router_usage, main_usage = routed.get('usage', {}), final.get('usage', {})
    total = {key: main_usage.get(key, 0) + router_usage.get(key, 0) for key in set(main_usage) | set(router_usage)}
    emit('done', response=record['utterance'], history=ctx['messages'] + [{'role': 'assistant', 'content': record['utterance']}],
         finish_reason='stop', usage=total, main_usage=main_usage, subagent_usage=router_usage,
         agent_usage={'main': main_usage, agent_id: router_usage}, experiment_version='Q1_s1')
