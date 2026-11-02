"""Adapters around Q3, with explicit memory views and post-response observers."""
import copy
import uuid
from runtime import user_templates

from runtime.cancellation import Cancelled
from runtime import reasoning_budget
from . import CONDITIONS, SPECS, condition
from .memory import CurrentOnlyMemory, encode, make_view, selected_episodes


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


TEXT = {'type': 'string'}
RECONSTRUCTION = object_schema({
    'turn': {'type': 'integer', 'minimum': 1},
    'status': {'type': 'string', 'enum': ['hypothesis', 'unknown']},
    'inferred_intent': TEXT, 'public_evidence': TEXT, 'uncertainty': TEXT})
SCHEMAS = {
    'intent_reconstructor': object_schema({'claim_result': {'type': 'array', 'maxItems': 12, 'items': RECONSTRUCTION}}),
    'intervention_observer': object_schema({'claim_result': object_schema({
        'intervention_description': TEXT, 'response_description': TEXT,
        'history_alignment': TEXT, 'current_change': TEXT, 'uncertainty': TEXT})}),
    'progress_observer': object_schema({'claim_result': object_schema({
        'apparent_agreement': TEXT, 'change_evidence': TEXT,
        'progress_assessment': {'type': 'string', 'enum': ['advance', 'maintain', 'regress', 'uncertain']},
        'grounds': TEXT, 'uncertainty': TEXT})}),
}


def validate(value, schema):
    """Validate auxiliary output locally even when the provider ignores its schema."""
    kind = schema['type']
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int}[kind]
    if not valid:
        raise ValueError('실험 관찰자 응답의 자료형이 잘못되었습니다.')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('실험 관찰자의 분류 값을 확인하세요.')
    if kind == 'object':
        if set(value) != set(schema['required']):
            raise ValueError('실험 관찰자 응답 필드를 확인하세요.')
        for key, sub in schema['properties'].items():
            validate(value[key], sub)
    elif kind == 'array':
        if len(value) > schema['maxItems']:
            raise ValueError('실험 관찰자 응답이 너무 큽니다.')
        for row in value:
            validate(row, schema['items'])
    elif kind == 'integer' and value < schema.get('minimum', value):
        raise ValueError('실험 관찰자의 턴 번호를 확인하세요.')
    return value


def install(module, claim):
    base_context, base_payload = module.context, module.payload_for
    base_execute, base_spec = module.execute, module.specification
    base_run, base_write = module.run_turn, module.write_new
    version = module.VERSION
    module.memory = CurrentOnlyMemory()
    lookup = next(t['function'] for t in module.TOOLS if t['function']['name'] == 'retrieve_client_memory')
    lookup['description'] = '현재 노트에서 빠진 항목만 조회합니다. 과거 원본 이력은 실험 조건에 따라 별도로 전달되며 이 도구로 우회할 수 없습니다.'
    lookup['parameters']['properties']['source']['enum'] = ['current']
    lookup['parameters']['properties']['source']['description'] = 'current: 현재 노트 원문만 조회 가능'
    lookup['parameters']['properties']['turns']['maxItems'] = 0
    lookup['parameters']['properties']['turns']['description'] = '현재 노트만 조회하므로 항상 빈 배열'
    module.TOOLS[0]['function']['description'] = '전체 현재 노트와 조건별 과거 의도 자료를 근거로 appraisal·trajectory_plan·turn_goal·response_plan을 기록합니다. 과거 기록과 현재 마음을 구분하세요. 필요한 현재 항목만 current로 확인한 뒤 발화를 확정합니다.'

    def specification():
        result = base_spec()
        result['description'] = SPECS[version][1] + ' 검증용 Q3 기반 버전. 전체 현재 노트를 공통 제공하고, 기억 대조 조건은 세션 시작 시 고정합니다.'
        result['claim'] = claim
        result['claim_memory_default'] = SPECS[version][2]
        result['claim_memory_conditions'] = [{'id': key, 'label': CONDITIONS[key]} for key in SPECS[version][3]]
        agents = list(result['subagent_prompts'])
        if version == 'Q3p3':
            agents.append({'kind': 'intent_reconstructor', 'label': '공개 대화 기반 의도 재구성'})
        if version == 'Q3p4':
            agents.append({'kind': 'intervention_observer', 'label': '개입 반응 관찰자 · 발화 후'})
        if version == 'Q3p5':
            agents.append({'kind': 'progress_observer', 'label': '진전 평가 관찰자 · 공개/비공개 조건'})
        result['subagent_prompts'] = agents
        return result

    def context(config, body, runtime, preview=False):
        ctx = base_context(config, body, runtime, preview)
        mode = condition(version, body)
        if ctx['note']['records']:
            previous = ctx['note']['records'][-1].get('claim_experiment', {}).get('memory_condition')
            if previous is not None and previous != mode:
                raise ValueError('진행 중인 세션의 기억 조건은 변경할 수 없습니다. 새 세션을 시작하세요.')
        state = config.get('_claim_execution', {})
        ctx['claim_memory'] = state.get('memory_view') or make_view(ctx['note']['records'], mode)
        return ctx

    def payload_for(config, body, ctx, selected, runtime, builder, preview=False):
        result = base_payload(config, body, ctx, selected, runtime, builder, preview)
        text = '\n\n# 실험 조건에 따라 제공된 과거 의도 자료\n\n' + encode(ctx['claim_memory']['visible'])
        next(m for m in result['messages'] if m['role'] == 'user')['content'] += text
        return result

    def auxiliary(config, kind, data, state, upstream, frames, inspect, emit, cancelled, tag=None):
        if cancelled():
            raise Cancelled('연구 보조 호출 전에 중단되었습니다.')
        prompt = (module.HERE / (kind + '.md')).read_text(encoding='utf-8')
        config = {key: value for key, value in config.items() if key != '_prepare_payload'}
        user = user_templates.render('client', version, kind+'_user', claim_input=encode(data))
        request = reasoning_budget.wrap_builder(state['builder'], state['body'].get('client_reasoning_budgets'))(
            config, {'system_prompt': prompt, 'messages': [{'role': 'user', 'content': user}],
                     '_reasoning_budget_role': 'subagent'}, [], False)
        request['structured_outputs'] = {'json': copy.deepcopy(SCHEMAS[kind])}
        aid = (tag or kind) + '_' + uuid.uuid4().hex[:12]
        meta = dict(agent_id=aid, agent_name=tag or kind, agent_kind=kind,
                    parent_agent_id='main', parent_call_id=None, depth=1,
                    role_instruction=prompt, role_source='developer')
        result = {}
        def event(event_type, **values):
            if event_type == 'done':
                result.update(values)
            emit('agent_event', **meta, event={'type': event_type, **values})
        event('request', payload=copy.deepcopy(request), iteration=1, phase=tag or kind,
              context_manifest=module.manifest(request, None, 'claim_observer'))
        try:
            base_run(config, request, None, upstream, frames, inspect, event, cancelled,
                     [{'role': 'user', 'content': user}])
            if result.get('finish_reason') != 'stop':
                raise ValueError('연구 보조 응답이 정상 완료되지 않았습니다.')
            parsed = validate(module.parse_json(result.get('response', '')), SCHEMAS[kind])['claim_result']
        except Exception as error:
            event('cancelled' if isinstance(error, Cancelled) else 'error', error=str(error))
            raise
        state['auxiliary'][aid] = dict(kind=kind, condition=tag, request=copy.deepcopy(request),
                                       output=copy.deepcopy(parsed), usage=result.get('usage', {}),
                                       system_source=prompt)
        return parsed

    def run_turn(config, payload, tools, upstream, frames, inspect, emit, cancelled, original):
        result = base_run(config, payload, tools, upstream, frames, inspect, emit, cancelled, original)
        state = config.get('_claim_execution')
        if tools is None or state is None or not tools.terminal_response():
            return result
        # Observe only after generation is complete, before the transactional save.
        # Observer outputs never enter current or subsequent client/counselor input.
        public = {'dialogue_before': state['context']['dialogue'],
                  'counselor_utterance': state['context']['current'],
                  'client_utterance': tools.terminal_response()}
        informed = {**public, 'historical_committed_episodes': state['episodes'],
                    'current_committed_judgment': state['judgment']}
        if version == 'Q3p4':
            auxiliary(config, 'intervention_observer', informed, state, upstream, frames, inspect,
                      state['emit'], cancelled)
        if version == 'Q3p5':
            for label, data in [('public_only', public), ('intent_informed', informed)]:
                auxiliary(config, 'progress_observer', data, state, upstream, frames, inspect,
                          state['emit'], cancelled, label)
        return result

    def execute(config, payload, client, upstream, frames, inspect, emit, cancelled,
                original, builder, mode, runtime):
        body = config['_request_body']
        ctx = context(config, body, runtime)
        selected, _ = selected_episodes(ctx['note']['records'])
        state = dict(body=body, builder=builder, context=ctx, episodes=selected,
                     auxiliary={}, judgment={}, emit=emit,
                     memory_view=make_view(ctx['note']['records'], condition(version, body)))
        # Q3 already provides per-turn storage/concurrency validation. Keep state
        # request-local; never change module globals for an executing session.
        config = {**config, '_claim_execution': state}
        if condition(version, body) == 'retrospective' and selected:
            data = {'dialogue': ctx['dialogue'], 'basic_profile': ctx['basic'],
                    'target_episodes': [{key: row[key] for key in (
                        'turn', 'counselor_utterance', 'utterance')} for row in selected]}
            inferred = auxiliary(config, 'intent_reconstructor', data, state, upstream, frames, inspect, emit, cancelled)
            targets = [r['turn'] for r in selected]
            if sorted(r['turn'] for r in inferred) != sorted(targets):
                raise ValueError('사후 해석은 제공된 각 대상 턴을 한 번씩 반환해야 합니다.')
            state['memory_view'] = make_view(ctx['note']['records'], 'retrospective', inferred)
        elif condition(version, body) == 'retrospective':
            state['memory_view'] = make_view([], 'retrospective', [])

        def event(kind, **data):
            if kind == 'version_event' and data.get('action') == 'staged':
                section = data.get('section')
                if section in ('appraisal', 'turn_goal', 'response_plan', 'trajectory_plan'):
                    state['judgment'][section] = copy.deepcopy(data['data'])
            if kind == 'done':
                data = copy.deepcopy(data)
                for aid, row in state['auxiliary'].items():
                    data['agent_usage'][aid] = row['usage']
                    for key, value in row['usage'].items():
                        data['usage'][key] = data['usage'].get(key, 0) + value
                        data['subagent_usage'][key] = data['subagent_usage'].get(key, 0) + value
            emit(kind, **data)

        # The base pipeline calls its writer through a global. Rather than swap
        # writers per request, its installed writer obtains state from this
        # execute-local ContextVar, preserving concurrency between sessions.
        token = writer_state.set(state)
        try:
            emit('version_event', version=version, section='claim_memory', action='selected',
                 data=copy.deepcopy(state['memory_view']))
            base_execute(config, payload, client, upstream, frames, inspect, event, cancelled,
                         original, builder, mode, runtime)
        finally:
            writer_state.reset(token)

    from contextvars import ContextVar
    writer_state = ContextVar('claim_writer_' + version)

    def persist(path, record):
        state = writer_state.get()
        record['claim_experiment'] = {
            'claim': claim, 'memory_condition': condition(version, state['body']),
            'memory_view': copy.deepcopy(state['memory_view']),
            'auxiliary_calls': copy.deepcopy(state['auxiliary']),
            'note_policy': 'full_current_notes_for_all_conditions',
            'archive_lookup': 'blocked; controlled automatic memory view only',
            'observer_policy': 'post_response_only; never fed back',
        }
        record['agent_usage'] = copy.deepcopy(record['agent_usage'])
        for aid, row in state['auxiliary'].items():
            record['agent_usage'][aid] = row['usage']
        return base_write(path, record)

    module.specification, module.context, module.payload_for = specification, context, payload_for
    module.execute, module.run_turn, module.write_new = execute, run_turn, persist
