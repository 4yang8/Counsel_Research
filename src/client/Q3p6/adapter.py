"""Full committed memory, isolated forks, and shared pre-response routing."""
import copy
import json
from contextvars import ContextVar
from pathlib import Path

from client.claim_experiments.memory import CurrentOnlyMemory, encode
from client.claim_experiments import condition, CONDITIONS
from client.state_summary.runtime import fingerprint, write_new

FIELDS = ('turn', 'turn_id', 'counselor_utterance', 'utterance', 'appraisal',
          'turn_goal', 'response_plan', 'trajectory_plan')


def memory_view(records, mode):
    rows = [{key: copy.deepcopy(row.get(key)) for key in FIELDS} for row in records]
    return {'condition': mode, 'selected_turns': [r['turn'] for r in rows],
            'omitted_turns': [], 'window': 'all_completed',
            'visible': {'episodes': rows if mode == 'intent_linked' else []}}


def install(module, claim):
    base_context, base_payload = module.context, module.payload_for
    base_run, base_execute, base_write = module.run_turn, module.execute, module.write_new
    base_spec = module.specification
    current = ContextVar('q3p6_execution')
    module.memory = CurrentOnlyMemory()
    tool = next(t['function'] for t in module.TOOLS if t['function']['name'] == 'retrieve_client_memory')
    tool['parameters']['properties']['source']['enum'] = ['current']
    tool['parameters']['properties']['turns']['maxItems'] = 0
    tool['description'] = '현재 노트만 조회합니다. 과거 원본은 지정된 기억 입력으로만 제공됩니다.'

    def specification():
        result = base_spec()
        result.update(claim='C6', claim_memory_default='summary_only',
                      description='공통 경로 시점별 자동 분기. 전체 당시 기록의 추가 효과를 비교합니다.',
                      claim_memory_conditions=[{'id': key, 'label': CONDITIONS[key]}
                                               for key in ('summary_only', 'intent_linked')])
        return result

    def context(config, body, runtime, preview=False):
        ctx = base_context(config, body, runtime, preview)
        mode = condition('Q3p6', body)
        rows = ctx['note']['records']
        # Imported history belongs to the parent. A newly committed child turn
        # belongs to this session and locks its condition thereafter.
        if rows and rows[-1].get('q3p6_session_id') == body.get('session_id'):
            if rows[-1]['claim_experiment']['memory_condition'] != mode:
                raise ValueError('진행 중인 Q3p6 세션의 기억 조건은 변경할 수 없습니다.')
        ctx['claim_memory'] = memory_view(rows, mode)
        return ctx

    def payload_for(config, body, ctx, selected, runtime, builder, preview=False):
        result = base_payload(config, body, ctx, selected, runtime, builder, preview)
        next(m for m in result['messages'] if m['role'] == 'user')['content'] += (
            '\n\n# 발화 당시 확정 내면 기록\n\n' + encode(ctx['claim_memory']['visible']))
        limit = config.get('_p6_control', {}).get('input_char_limit', 240000)
        if len(encode(result)) > limit:
            raise ValueError('Q3p6 입력 한도 초과: 원본을 자르지 않았습니다. 범위나 입력 예산을 조정하세요.')
        return result

    def run_turn(config, payload, tools, upstream, frames, inspect, emit, cancelled, original):
        cache_path = config.get('_p6_control', {}).get('router_cache')
        if tools is not None or not cache_path:
            return base_run(config, payload, tools, upstream, frames, inspect, emit, cancelled, original)
        # Only the Q3 trait router is a tool-less call in this runtime. Its input
        # excludes private episodes, so the identical decision can be reused.
        key = fingerprint({'model': payload.get('model'), 'messages': payload['messages'],
                           'schema': payload.get('structured_outputs')})
        path = Path(cache_path)
        if path.exists():
            stored = json.loads(path.read_text())
            if stored['key'] != key:
                raise ValueError('A/B 공통 라우터 입력이 달라졌습니다.')
            emit('router_cache_hit', source=str(path), request=stored['request'])
            emit('done', response=stored['response'], finish_reason='stop', usage={})
            return
        result = {}
        def event(kind, **data):
            if kind == 'done':
                result.update(data)
            emit(kind, **data)
        answer = base_run(config, payload, tools, upstream, frames, inspect, event, cancelled, original)
        if result.get('finish_reason') == 'stop':
            write_new(path, {'key': key, 'request': payload, 'response': result['response'],
                             'usage': result.get('usage', {})})
        return answer

    def execute(config, payload, client, upstream, frames, inspect, emit, cancelled, original,
                builder, mode, runtime):
        ctx = context(config, config['_request_body'], runtime)
        state = {'view': ctx['claim_memory'], 'session_id': config['_turn_info']['session_id'],
                 'control': copy.deepcopy(config.get('_p6_control', {}))}
        token = current.set(state)
        try:
            emit('version_event', version='Q3p6', section='claim_memory', action='selected', data=state['view'])
            return base_execute(config, payload, client, upstream, frames, inspect, emit,
                                cancelled, original, builder, mode, runtime)
        finally:
            current.reset(token)

    def persist(path, record):
        state = current.get()
        record['q3p6_session_id'] = state['session_id']
        record['claim_experiment'] = {'claim': claim, 'memory_condition': state['view']['condition'],
                                     'memory_view': state['view'], 'auxiliary_calls': {},
                                     'note_policy': 'full_current_notes', 'archive_lookup': 'blocked',
                                     'branch': state['control']}
        return base_write(path, record)

    module.specification, module.context, module.payload_for = specification, context, payload_for
    module.run_turn, module.execute, module.write_new = run_turn, execute, persist
