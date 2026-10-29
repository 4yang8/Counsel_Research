"""Compact index of exact inputs, tool results, summaries and state records."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
results = []
for source in sorted(ROOT.glob('events_*.jsonl'), key=lambda p: int(p.stem.split('_')[-1])):
    number = int(source.stem.split('_')[-1])
    events = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()]
    if not events or events[-1]['type'] not in ('done', 'error'):
        continue
    body = json.loads((ROOT / f'input_{number}.json').read_text(encoding='utf-8'))
    probe = json.loads((ROOT / f'probe_{number}.json').read_text(encoding='utf-8'))
    path = Path(events[0]['saved_path'])
    log = json.loads(path.read_text(encoding='utf-8'))
    state = next((e['record'] for e in events if e['type'] == 'state_record'), None)
    analysis_only = False
    parsed = None
    if not state:
        raw = log.get('response', '').strip()
        if raw.startswith('```') and raw.endswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
            analysis_only = True
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    summary = next((e for e in events if e['type'] == 'state_summary'), {})
    starts = [e for e in events if e['type'] == 'tool_start']
    tools = []
    for result in (e for e in events if e['type'] == 'tool_result'):
        start = next((e for e in starts if e['call_id'] == result['call_id']), {})
        tools.append({'name': result['name'], 'call_id': result['call_id'],
                      'arguments': start.get('arguments'), 'is_error': result['is_error'],
                      'result': result.get('result')})
    record = state or parsed or {}
    result = {'attempt': number, 'generated_turn': state.get('turn') if state else None,
              'probe': probe, 'counselor': body['messages'][-1]['content'], 'status': log['status'],
              'committed': bool(state), 'analysis_only_fence_removal': analysis_only,
              'client': record.get('utterance'), 'inner_state': record.get('inner_state'),
              'feelings': record.get('feelings'), 'state_before': summary.get('state_before'),
              'state_after': state.get('state_after') if state else None,
              'summary': summary.get('summary'), 'summary_source_turns': summary.get('source_turns', []),
              'summary_usage': summary.get('usage', {}), 'tools': tools,
              'log_path': str(path), 'session_id': log['session_id'],
              'main_calls': len(log.get('model_calls', [])),
              'retrieval_agent_calls': len([e for e in events if e['type'] == 'agent_event'
                  and e.get('agent_kind') != 'summary' and e['event']['type'] == 'request']),
              'last_event': events[-1]['type']}
    results.append(result)
(ROOT / 'analysis_index.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(results[-1:] if '--last' in sys.argv else results, ensure_ascii=False, indent=2))
