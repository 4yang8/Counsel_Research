"""Replay a failed semantic handoff with actual public evidence separately supplied."""
import argparse
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from project_paths import PROJECT_ROOT
LAB = PROJECT_ROOT
sys.path.insert(0, str(LAB / 'src'))
import app.server as server
from counselor.P3a_s1 import runtime

OUT = LAB / 'experiments/side_auto_20261018'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--budget', type=int, default=512)
    parser.add_argument('--tag', default='guide_grounded')
    parser.add_argument('--source-only', action='store_true')
    parser.add_argument('--run', default='s1_r2_replace512_lab')
    parser.add_argument('--step', type=int, default=8)
    args = parser.parse_args()
    data = json.loads((OUT / 'runs' / args.run / 'compact.json').read_text())
    attempt = next(a for a in data['attempts'] if a['step'] == args.step)
    source = json.loads(Path(attempt['saved_path']).read_text())
    payload = next(copy.deepcopy(e['event']['payload']) for e in source['subagent_events'] if e['event']['type'] == 'request')
    arguments = next(t['arguments'] for t in attempt['tools'] if t['name'] == 'consult_counseling_guide')
    if args.source_only:
        arguments = {**arguments, 'request': '실제 공개 대화를 바탕으로 현재 상황에서 필요한 다음 상담 대응과 이유를 조언하세요.'}
    public = '\n\n'.join(('상담사' if row['role'] == 'counselor' else '내담자') + ': ' + row['content']
                         for row in data['dialogue'][:args.step])
    user, sources = runtime.guidance_input(arguments, public)
    payload['messages'] = [{'role': 'system', 'content': (runtime.HERE / 'guide_grounded.md').read_text()},
                           {'role': 'user', 'content': user}]
    payload['thinking_token_budget'] = args.budget
    config = json.loads((OUT / 'config.json').read_text())
    record = {'source': attempt['saved_path'], 'request': payload, 'sources': sources,
              'response': '', 'reasoning': '', 'usage': None, 'finish_reason': None}
    path = OUT / 'probes' / (args.tag + '_' + str(args.budget) + '.json')
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
    print(json.dumps({k: v for k, v in record.items() if k not in ['request', 'reasoning', 'sources']}, ensure_ascii=False), flush=True)
