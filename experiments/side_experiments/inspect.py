"""Compact deterministic indices; semantic judgments remain in the short journal."""
from collections import Counter
import hashlib
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from project_paths import PROJECT_ROOT
LAB = PROJECT_ROOT
OUT = LAB / 'experiments/side_auto_20261018'


def inspect_run(folder):
    manifest = json.loads((folder / 'manifest.json').read_text())
    if not (folder / 'compact.json').exists():
        return {'run': folder.name, 'status': 'starting'}
    data = json.loads((folder / 'compact.json').read_text())
    count, token_total, reasoning_chars, errors, retries = Counter(), Counter(), Counter(), [], []
    success = Counter()
    for attempt in data['attempts']:
        actor = attempt['actor']
        count['attempts_' + actor] += 1
        if attempt['status'] == 'completed':
            success[actor] += 1
        else:
            errors.append({'step': attempt['step'], 'actor': actor, 'error': attempt['error']})
        for call in attempt['calls']:
            role = actor + '_main'
            count[role] += 1
            reasoning_chars[role] += call['reasoning_chars']
            if call['usage']:
                token_total[role] += call['usage'].get('completion_tokens', 0)
        for child in attempt['children']:
            role = child['kind']
            count[role] += 1
            reasoning_chars[role] += child['reasoning_chars']
            if child['usage']:
                token_total[role] += child['usage'].get('completion_tokens', 0)
        for tool in attempt['tools']:
            count['tool_' + tool['name']] += 1
            if tool.get('error'):
                count['tool_errors'] += 1
        events = Path(attempt['saved_path']).with_suffix('.events.jsonl')
        for line in events.read_text().splitlines():
            event = json.loads(line)
            if event['type'] == 'generation_retry':
                retries.append({k: event.get(k) for k in ('iteration', 'reason', 'max_tokens', 'executed')})
    usable = Counter(a['actor'] for a in data['attempts'] if a['status'] == 'completed'
                     and a['step'] < manifest.get('invalid_public_from_step', 10**9))
    return {'run': folder.name, 'session_id': data['session_id'], 'versions': [manifest['client'], manifest['counselor']],
            'planned_pairs': manifest['planned_pairs'], 'completed_speeches': dict(success),
            'usable_speeches': dict(usable), 'invalid_public_from_step': manifest.get('invalid_public_from_step'),
            'counts': dict(count), 'output_tokens_including_reasoning': dict(token_total),
            'reasoning_characters': dict(reasoning_chars), 'errors': errors, 'generation_retries': retries}


if __name__ == '__main__':
    hashes = json.loads((OUT / 'original_hashes.json').read_text())
    changed = [p for p, expected in hashes.items() if hashlib.sha256((LAB / p).read_bytes()).hexdigest() != expected]
    rows = [inspect_run(p) for p in sorted((OUT / 'runs').iterdir()) if (p / 'manifest.json').exists()]
    report = {'original_files_changed': changed, 'runs': rows}
    (OUT / 'metrics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
