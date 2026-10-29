"""Two real component calls, preserving raw output; no public conversation mutation."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
LAB = ROOT.parents[1]
sys.path.insert(0, str(LAB))
import app_server as app
import server
import state_summary
from tool_loop import run_turn

config = json.loads((LAB/'config.json').read_text(encoding='utf-8'))
config['inspect_prompt'] = False
source = LAB/'experiments/state_summary_trajectory_5_20261009T112337Z/sessions/state_summary/bd8275d501114ba3b652a0cb68b70f10/inner_states'
records = [json.loads(path.read_text(encoding='utf-8')) for path in sorted(source.glob('*.json'))]
assert len(records) == 4
query = '동료에게 직접 평가를 물어보자는 행동 제안을 처음 들었을 때, 내담자가 위험하다고 느끼며 거절했던 이유'
database = state_summary.prepare_retrieval({'records': records},
    {'query': query, 'state': 'all', 'reason': '해당 제안을 처음 들었던 당시의 원래 해석과 거절 이유 확인'}, app.runtime.markdown_data)
summary_system = (LAB/'versions/state_summary/summary.md').read_text(encoding='utf-8')
summary_user = app.runtime.markdown_data({'내면상태 항목': {key: row['name'] for key, row in records[-1]['state_after'].items()},
                                        '누적 내면 반응 기록': [state_summary.reaction_event(record) for record in records]})
saved = {'scope': 'component_replay', 'model': config['model'], 'source': str(source), 'records': 4, 'calls': []}


def persist():
    (ROOT/'replay.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')


for name, system, user, maximum in [('semantic_lookup', database['system'], database['user'], 128),
                                     ('integrated_trajectory', summary_system, summary_user, 512)]:
    cfg = {**config, 'generation': {**config['generation'], 'max_tokens': maximum}}
    payload = server.build_payload(cfg, {'system_prompt': system, 'messages': [{'role': 'user', 'content': user}]}, [])
    item = {'component': name, 'request': payload, 'status': 'running', 'response': ''}
    saved['calls'].append(item)
    persist()
    def event(kind, **data):
        if kind == 'chunk':
            for choice in data['data'].get('choices', []):
                item['response'] += choice.get('delta', {}).get('content', '')
            persist()
        elif kind == 'done':
            item.update(response=data['response'], finish_reason=data['finish_reason'], usage=data['usage'])
            persist()
    try:
        run_turn(cfg, payload, None, server.upstream, server.stream_events, server.inspect_prompt,
                 event, lambda: False, [{'role': 'user', 'content': user}])
        if item.get('finish_reason') != 'stop':
            raise ValueError('Non-stop completion; raw output preserved.')
        if name == 'semantic_lookup':
            result = state_summary.retrieval_result(database, item['response'], 'component_replay')
            item['result'] = result
            item['expected_source_turn'] = 3
            item['matched_expected'] = result.get('record', {}).get('turn') == 3
        item['status'] = 'completed'
    except Exception as error:
        item.update(status='failed', error=str(error))
    persist()
    print(json.dumps({key: value for key, value in item.items() if key != 'request'}, ensure_ascii=False, indent=2), flush=True)
