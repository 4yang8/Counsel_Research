"""Test a focused note curator on known source-attribution failures before integration."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from project_paths import PROJECT_ROOT
LAB = PROJECT_ROOT
sys.path.insert(0, str(LAB / 'src'))
import app.server as server

OUT = LAB / 'experiments/side_auto_20261018'


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run', default='s1_r3_semantic_005')
    p.add_argument('--step', type=int, default=10)
    p.add_argument('--budget', type=int, default=512)
    p.add_argument('--no-old-notes', action='store_true')
    args = p.parse_args()
    data = json.loads((OUT / 'runs' / args.run / 'compact.json').read_text())
    row = next(a for a in data['attempts'] if a['step'] == args.step)
    snapshots = {}
    for line in Path(row['saved_path']).with_suffix('.events.jsonl').read_text().splitlines():
        e = json.loads(line)
        if e['type'] == 'version_event' and e.get('action') == 'snapshot':
            snapshots.setdefault(e['section'], e['data']['content'])
    # Use an actually generated plan, clearly marked as a hypothesis/next action.
    parent = json.loads(Path(row['saved_path']).read_text())
    plan = ''
    for tool in row['tools']:
        a = tool['arguments']
        if tool['name'] == 'update_notes' and a.get('target') == 'counselor' and 'content' in a:
            plan = a['content'].split('## 다음 대응', 1)[-1]
    public = '\n\n'.join(('상담사' if r['role'] == 'counselor' else '내담자') + ': ' + r['content']
                         for r in data['dialogue'][:args.step])
    user = ('# 실제 공개 대화\n\n' + public + '\n\n# 기존 내담자 노트\n\n' + snapshots['client_notes']
            + '\n\n# 기존 상담사 생각 노트\n\n' + snapshots['counselor_notes']
            + '\n\n# 메인의 이번 계획 · 작업 가설/예정\n\n' + plan)
    if args.no_old_notes:
        user = '# 실제 공개 대화\n\n' + public + '\n\n# 메인의 이번 계획 · 작업 가설/예정\n\n' + plan
    payload = dict(parent['model_calls'][0]['request'])
    payload.pop('tools', None)
    payload.pop('tool_choice', None)
    payload.pop('parallel_tool_calls', None)
    payload['messages'] = [{'role': 'system', 'content': (LAB / 'src/counselor/P3a_s1/note_agent.md').read_text()},
                           {'role': 'user', 'content': user}]
    payload.update(thinking_token_budget=args.budget, max_tokens=4096,
                   structured_outputs={'json': {'type': 'object', 'properties': {
                       'client_notes': {'type': 'string'}, 'counselor_notes': {'type': 'string'},
                       'corrections': {'type': 'array', 'items': {'type': 'string'}}},
                       'required': ['client_notes', 'counselor_notes', 'corrections'], 'additionalProperties': False}})
    config = json.loads((OUT / 'config.json').read_text())
    record = {'source': row['saved_path'], 'request': payload, 'response': '', 'reasoning': '',
              'usage': None, 'finish_reason': None}
    path = OUT / 'probes' / ('notes_' + args.run + '_' + str(args.step) + '_' + str(args.budget)
                           + ('_fresh' if args.no_old_notes else '') + '.json')
    if path.exists():
        raise ValueError('Already exists: ' + str(path))
    with server.upstream(config, '/v1/chat/completions', payload) as response:
        for frame in server.stream_events(response):
            if frame == '[DONE]':
                break
            chunk = json.loads(frame)
            if chunk.get('usage'):
                record['usage'] = chunk['usage']
            for choice in chunk.get('choices', []):
                delta = choice.get('delta', {})
                record['response'] += delta.get('content') or ''
                record['reasoning'] += delta.get('reasoning') or delta.get('reasoning_content') or ''
                record['finish_reason'] = choice.get('finish_reason') or record['finish_reason']
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    parsed = json.loads(record['response'])
    print(json.dumps({'path': str(path), 'usage': record['usage'], 'finish': record['finish_reason'],
                      'notes': parsed}, ensure_ascii=False), flush=True)
