"""Focused source attribution probe; indices identify actual immutable public utterances."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from project_paths import PROJECT_ROOT
LAB = PROJECT_ROOT
sys.path.insert(0, str(LAB / 'src'))
import app.server as server

OUT = LAB / 'experiments/side_auto_20261018'
data = json.loads((OUT / 'runs/s1_r3_semantic_005/compact.json').read_text())
public = data['dialogue'][:10]
script = '\n\n'.join(f'[{i}] ' + ('상담사' if r['role'] == 'counselor' else '내담자') + ': ' + r['content']
                       for i, r in enumerate(public))
claim = 'HR 억울함은 내담자가 다음에 다루기로 스스로 보류했다.'
system = ('공개 대화와 메모의 출처를 대조하세요. 누가 먼저 제안했고 상대가 요청한 것인지 제안을 따른 것인지 구분하세요. '
          '주어진 문장의 타당성과 근거 발화 번호를 반환하세요. 대화에 없는 근거는 null로 표시하세요. '
          'proposal_index에는 보류를 처음 제안한 발화, response_index에는 그 제안에 대한 상대 반응을 지정하세요.')
cfg = json.loads((OUT / 'config.json').read_text())
payload = {'model': cfg['model'], 'messages': [{'role': 'system', 'content': system},
    {'role': 'user', 'content': '# 검토할 메모\n' + claim + '\n\n# 공개 대화\n' + script}],
    'temperature': .7, 'top_p': .95, 'seed': 20261018, 'max_tokens': 2048,
    'thinking_token_budget': 512, 'chat_template_kwargs': {'enable_thinking': True},
    'stream': True, 'stream_options': {'include_usage': True},
    'structured_outputs': {'json': {'type': 'object', 'properties': {
        'supported': {'type': 'boolean'}, 'proposal_index': {'type': ['integer', 'null']},
        'response_index': {'type': ['integer', 'null']}, 'correction': {'type': 'string'}},
        'required': ['supported', 'proposal_index', 'response_index', 'correction'], 'additionalProperties': False}}}
record = {'request': payload, 'response': '', 'reasoning': '', 'usage': None, 'finish_reason': None}
path = OUT / 'probes/claim_ownership_512.json'
if path.exists():
    raise ValueError('Already exists')
with server.upstream(cfg, '/v1/chat/completions', payload) as response:
    for frame in server.stream_events(response):
        if frame == '[DONE]':
            break
        chunk = json.loads(frame)
        if chunk.get('usage'):
            record['usage'] = chunk['usage']
        for c in chunk.get('choices', []):
            d = c.get('delta', {})
            record['response'] += d.get('content') or ''
            record['reasoning'] += d.get('reasoning_content') or d.get('reasoning') or ''
            record['finish_reason'] = c.get('finish_reason') or record['finish_reason']
path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
result = json.loads(record['response'])
for key in ('proposal_index', 'response_index'):
    i = result.get(key)
    if type(i) is int and 0 <= i < len(public):
        result[key + '_source'] = public[i]
print(json.dumps({'result': result, 'usage': record['usage']}, ensure_ascii=False), flush=True)
