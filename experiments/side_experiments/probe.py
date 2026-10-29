"""Replay one recorded request; no public dialogue or notes are changed."""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from project_paths import PROJECT_ROOT
LAB = PROJECT_ROOT
sys.path.insert(0, str(LAB / 'src'))
import app.server as server

OUT = LAB / 'experiments/side_auto_20261018'
SOURCE = LAB / 'runs/paired/logs/261018_204046/20261018T123608_796043Z_3c210382.json'


def run(args):
    source = json.loads(SOURCE.read_text())
    if args.stage == 'main':
        payload = copy.deepcopy(source['model_calls'][0]['request'])
    else:
        payload = next(copy.deepcopy(w['event']['payload']) for w in source['subagent_events']
                       if w['event']['type'] == 'request')
    if args.condition == 's1':
        prompt = LAB / 'src/client/Q1_s1' / ('system.md' if args.stage == 'main' else 'trait_router.md')
        payload['messages'][0]['content'] = prompt.read_text()
    payload.update(max_tokens=4096, seed=20261018)
    if args.budget:
        payload['thinking_token_budget'] = args.budget
    config = json.loads((OUT / 'config.json').read_text())
    record = {'source': str(SOURCE), 'stage': args.stage, 'condition': args.condition,
              'created_at': datetime.now(timezone.utc).isoformat(), 'request': payload,
              'reasoning': '', 'response': '', 'tool_calls': {}, 'usage': None, 'finish_reason': None}
    folder = OUT / 'probes'
    folder.mkdir(exist_ok=True)
    name = args.stage + '_' + args.condition + ('_budget' + str(args.budget) if args.budget else '')
    path = folder / (name + '.json')
    if path.exists():
        raise ValueError('Probe already exists: ' + str(path))
    with server.upstream(config, '/v1/chat/completions', payload) as response:
        for frame in server.stream_events(response):
            if frame == '[DONE]':
                break
            chunk = json.loads(frame)
            if chunk.get('error'):
                record['error'] = chunk['error']
                break
            if chunk.get('usage'):
                record['usage'] = chunk['usage']
            for choice in chunk.get('choices', []):
                delta = choice.get('delta', {})
                record['reasoning'] += delta.get('reasoning_content') or delta.get('reasoning') or ''
                record['response'] += delta.get('content') or ''
                record['finish_reason'] = choice.get('finish_reason') or record['finish_reason']
                for fragment in delta.get('tool_calls', []):
                    call = record['tool_calls'].setdefault(str(fragment.get('index', 0)), {'name': '', 'arguments': ''})
                    for key in ('name', 'arguments'):
                        call[key] += fragment.get('function', {}).get(key) or ''
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in record.items() if k not in ['request', 'reasoning']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--stage', choices=['main', 'router'], required=True)
    p.add_argument('--condition', choices=['baseline', 's1'], required=True)
    p.add_argument('--budget', type=int)
    run(p.parse_args())
