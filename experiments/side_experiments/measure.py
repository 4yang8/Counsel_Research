"""Retokenize saved reasoning; cache counts without confusing output with thinking."""
from collections import defaultdict
import hashlib
import http.client
import json
from pathlib import Path
from urllib.parse import urlsplit

OUT = Path(__file__).resolve().parents[1] / 'experiments/side_auto_20261018'


if __name__ == '__main__':
    config = json.loads((OUT / 'config.json').read_text())
    url = urlsplit(config['vllm_url'])
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
    cache_path = OUT / 'token_cache.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    summaries = []

    def count(model, text):
        key = hashlib.sha256((model + '\0' + text).encode()).hexdigest()
        if key not in cache:
            connection.request('POST', '/tokenize', json.dumps({'model': model, 'prompt': text,
                'add_special_tokens': False}), headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            value = json.loads(response.read())
            if response.status != 200:
                raise ValueError(str(value))
            cache[key] = value['count']
        return cache[key]

    for folder in sorted((OUT / 'runs').iterdir()):
        manifest = json.loads((folder / 'manifest.json').read_text())
        if not manifest.get('finished_at') and not manifest.get('stopped_reason'):
            continue
        compact = json.loads((folder / 'compact.json').read_text())
        rows, grouped = [], defaultdict(list)
        for attempt in compact['attempts']:
            log = json.loads(Path(attempt['saved_path']).read_text())
            for call in log.get('model_calls', []):
                role = attempt['actor'] + ('_main_final' if call.get('response', '').strip() else '_main_tool')
                if call.get('usage') is None:
                    continue
                tokens = count(call['request']['model'], call.get('reasoning', ''))
                grouped[role].append(tokens)
                rows.append({'step': attempt['step'], 'role': role, 'iteration': call['iteration'],
                    'reasoning_tokens': tokens, 'completion_tokens': call['usage']['completion_tokens']})
            for wrapper in log.get('subagent_events', []):
                event = wrapper['event']
                if event['type'] == 'done':
                    role = wrapper['agent_kind']
                    tokens = count(manifest['settings']['model'], event.get('reasoning', ''))
                    grouped[role].append(tokens)
                    rows.append({'step': attempt['step'], 'role': role, 'reasoning_tokens': tokens,
                                 'completion_tokens': event['usage']['completion_tokens']})
        summary = {'run': folder.name, 'roles': {role: {'calls': len(values), 'total': sum(values),
                    'mean': round(sum(values) / len(values), 1), 'max': max(values)} for role, values in grouped.items()},
                   'calls': rows}
        summaries.append(summary)
        print(json.dumps({k: v for k, v in summary.items() if k != 'calls'}, ensure_ascii=False), flush=True)
    cache_path.write_text(json.dumps(cache))
    (OUT / 'reasoning_metrics.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
    connection.close()
