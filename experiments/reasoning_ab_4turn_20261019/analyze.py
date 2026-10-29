"""Summarize saved calls without a second model judging its own output."""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent
LAB = ROOT.parents[1]


def load(path):
    return json.loads(path.read_text())


def main():
    manifest = load(ROOT / 'manifest.json')
    result, all_rows = {}, {}
    for condition in ('on', 'off'):
        rows = [load(p) for p in sorted((ROOT / condition).glob('turn_*.json'))]
        all_rows[condition] = rows
        calls = [c for r in rows for c in r['calls']]
        summary = {'turns': len(rows), 'completed': sum(r['status'] == 'completed' for r in rows),
                   'elapsed_seconds': round(sum(r['elapsed_seconds'] for r in rows), 2),
                   'mean_turn_seconds': round(statistics.mean(r['elapsed_seconds'] for r in rows), 2) if rows else None,
                   'tools': dict(Counter(t['name'] for r in rows for t in r['tools'])),
                   'tool_errors': sum(bool(t.get('error')) for r in rows for t in r['tools']),
                   'guides': dict(Counter(t['arguments'].get('guide') for r in rows for t in r['tools']
                                         if t['name'] == 'consult_counseling_guide' and isinstance(t['arguments'], dict))),
                   'roles': {}}
        for role in ('main', 'subagent'):
            subset = [c for c in calls if c['role'] == role]
            summary['roles'][role] = {'calls': len(subset),
                **{key: sum(c.get('usage', {}).get(key, 0) for c in subset)
                   for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')},
                'reasoning_tokens_retokenized': sum(c['reasoning_tokens_retokenized'] for c in subset),
                'nonempty_reasoning_calls': sum(bool(c.get('reasoning')) for c in subset),
                'thinking_settings': list({c['thinking_enabled'] for c in subset})}
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens_retokenized'):
            summary[key] = sum(x[key] for x in summary['roles'].values())
        summary['per_turn'] = [{'turn': r['turn'], 'elapsed_seconds': round(r['elapsed_seconds'], 2),
                               'calls': len(r['calls']), 'response': r['response'],
                               'completion_tokens': sum(c.get('usage', {}).get('completion_tokens', 0) for c in r['calls']),
                               'reasoning_tokens_retokenized': sum(c['reasoning_tokens_retokenized'] for c in r['calls'])}
                              for r in rows]
        result[condition] = summary
    if all_rows['on'] and all_rows['off']:
        normalized = []
        for condition in ('on', 'off'):
            p = copy.deepcopy(all_rows[condition][0]['calls'][0]['request'])
            p['chat_template_kwargs'].pop('enable_thinking', None)
            normalized.append(p)
        result['verification'] = {'first_main_request_identical_except_enable_thinking': normalized[0] == normalized[1]}
    changed = []
    for path, sha in manifest['hashes'].items():
        if hashlib.sha256((LAB / path).read_bytes()).hexdigest() != sha:
            changed.append(path)
    result.setdefault('verification', {})['source_changes_since_snapshot'] = changed
    (ROOT / 'SUMMARY.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    lines = ['# Reasoning 켜기/끄기 · 실제 생성 결과', '',
             '내담자 발화는 고정 입력. 상담사 응답과 노트는 조건별로 누적된다.', '']
    for i, utterance in enumerate(manifest['inputs'], 1):
        lines += [f'## {i}턴 · 내담자', '', utterance, '']
        for condition in ('on', 'off'):
            row = next((r for r in all_rows[condition] if r['turn'] == i), None)
            lines += ['### Reasoning ' + condition, '', row['response'] if row else '(미완료)', '']
    (ROOT / 'DIALOGUE.md').write_text('\n'.join(lines))
    print(json.dumps({k: {x: y for x, y in v.items() if x != 'per_turn'} for k, v in result.items()},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
