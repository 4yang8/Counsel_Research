"""Extract revisit units from a Q3compare_memory fork batch.

A unit = one P3q counselor revisit generated in a branch (counselor turn > fork turn, valid mark).
For it we keep: the target client turn t and its stored inner record (same in both branches when
t < fork turn), the counselor revisit question, the client's answer (utterance + inner) and, for the
provided branch, whether the memory selector injected turn t at the answer turn.
"""
import argparse
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]


def is_none(w):
    w = (w or '').strip().strip('.').strip()
    return not w or w.startswith('없음') or w.startswith('해당 없음') or w in ('불확실',)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('batch')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    job = json.loads((PROJECT / a.batch / 'job.json').read_text(encoding='utf-8'))
    n = job['options']['fork_turn']
    groups = {}
    for r in job['rows']:
        groups.setdefault(r['profile_id'], {})[r['branch']] = r
    units, sessions = [], {}
    for pid, rows in sorted(groups.items()):
        if any(rows[b]['status'] != 'completed' for b in ('common', 'provided', 'withheld')):
            continue
        for branch in ('provided', 'withheld'):
            sid = rows[branch]['session_id']
            s = json.loads((PROJECT / 'runs/paired' / sid / 'session.json').read_text(encoding='utf-8'))
            state = json.loads((Path(s['client_storage_dir']) / 'Q3compare_memory' / sid / 'memory_state.json').read_text(encoding='utf-8'))
            recs = {r['turn']: r for r in state['records']}
            sessions[(pid, branch)] = {'session_id': sid, 'public': s['public_dialogue'], 'records': state['records']}
            for step in s['steps']:
                m = step.get('revisit')
                if step['actor'] != 'counselor' or not m or step.get('common'):
                    continue
                c = m['counselor_turn']
                if not m['is_revisit'] or not m['valid'] or c not in recs:
                    units.append({'profile': pid, 'branch': branch, 'counselor_turn': c, 'kind': 'invalid' if m['is_revisit'] else 'none'})
                    continue
                t, ans = m['client_turn'], recs[c]
                pp = recs[t]['inner']['private_position']
                units.append({'profile': pid, 'branch': branch, 'kind': 'revisit', 'counselor_turn': c, 'target': t,
                              'target_common': t < n, 'gap': not is_none(pp['withheld_content']),
                              'quote_found': m['quote_found'], 'quote': m['quote'],
                              'target_utterance': recs[t]['utterance'], 'target_counselor': recs[t]['counselor_utterance'],
                              'target_private': pp, 'target_appraisal': recs[t]['inner'].get('appraisal'),
                              'question': ans['counselor_utterance'], 'answer': ans['utterance'],
                              'answer_private': ans['inner']['private_position'],
                              'injected': [x['turn'] for x in ans['memory_input']], 'memory_mode': ans.get('memory_mode'),
                              'target_injected': t in [x['turn'] for x in ans['memory_input']]})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'units.json').write_text(json.dumps({'batch': a.batch, 'fork_turn': n, 'turns': job['options']['turns'], 'units': units}, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'sessions.json').write_text(json.dumps({f'{p}|{b}': v for (p, b), v in sessions.items()}, ensure_ascii=False), encoding='utf-8')
    rv = [u for u in units if u['kind'] == 'revisit']
    print('profiles', len({u['profile'] for u in units}), 'counselor turns', len(units), 'revisits', len(rv),
          'invalid', sum(u['kind'] == 'invalid' for u in units))
    for b in ('provided', 'withheld'):
        x = [u for u in rv if u['branch'] == b]
        print(b, 'revisits', len(x), 'target<fork', sum(u['target_common'] for u in x), 'gap', sum(u['gap'] for u in x),
              'gap&common', sum(u['gap'] and u['target_common'] for u in x), 'quote_found', sum(bool(u['quote_found']) for u in x),
              'target injected', sum(u['target_injected'] for u in x))


if __name__ == '__main__':
    main()
