"""Print stored reasoning for chosen steps of a fork batch (representative-case inspection)."""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]


def step_reasoning(path):
    out = {}
    for line in open(path, encoding='utf-8'):
        e = json.loads(line)
        if e['type'] == 'agent_event' and e['event']['type'] == 'done':
            out[e['agent_kind']] = e['event'].get('reasoning') or ''
        elif e['type'] == 'chunk':
            for c in e.get('data', {}).get('choices', []):
                d = c.get('delta', {})
                out['main'] = out.get('main', '') + (d.get('reasoning_content') or d.get('reasoning') or '')
        elif e['type'] == 'model_result':
            out['main'] = e.get('reasoning') or out.get('main', '')
    return out


def session(batch, profile, branch):
    job = json.loads((PROJECT / batch / 'job.json').read_text(encoding='utf-8'))
    row = next(r for r in job['rows'] if r['profile_id'] == profile and r['branch'] == branch)
    return json.loads((PROJECT / 'runs/paired' / row['session_id'] / 'session.json').read_text(encoding='utf-8'))


def get(batch, profile, branch, actor, turn):
    """turn: Nth counselor utterance (opening = 1) or Nth client reply."""
    s = session(batch, profile, branch)
    n = 0 if actor == 'client' else 1
    for st in s['steps']:
        if st['actor'] == actor:
            n += 1
            if n == turn:
                return st, step_reasoning(st['saved_path'].replace('.json', '.events.jsonl'))
    raise KeyError((profile, branch, actor, turn))


if __name__ == '__main__':
    batch, limit = sys.argv[1], int(sys.argv[2])
    for spec in sys.argv[3:]:
        profile, branch, actor, turn, kind = spec.split(':')
        st, r = get(batch, profile, branch, actor, int(turn))
        text = r.get(kind, '')
        print(f'\n===== {spec} | 발화: {st["utterance"]}')
        if st.get('revisit'):
            print('  revisit:', {k: st['revisit'][k] for k in ('is_revisit', 'client_turn', 'quote')})
        print(f'  [{kind} reasoning {len(text)}자]')
        print(text if len(text) <= limit else text[:limit * 2 // 3] + '\n  …(중략)…\n' + text[-limit // 3:])
