"""Score blind judgments of the hand-written paired dialogues against the key.

Per dialogue and judge:
  cause_hit      judge's `cause` equals the intervention turn (4)
  withdraw_run   consecutive '물러섬' labels on client turns starting at turn 5
  uptake         `uptake` == '수용'
  linked_hit     reversal present and `linked` equals the intervention turn
  mismatch       profile_mismatch present
  interv_ok      first_intervention_turn == 4 (sanity: judges find the intervention unaided)
Code-only: len_ratio = mean client length at turns 5-6 / mean client length at turns 1-3.
Judges are combined by majority (binary) or mean (counts). Conditions are compared per profile
with an exact paired sign-flip test (2^10 sign patterns). Agreement = share of dialogues on which
all judges give the same value, plus Fleiss' kappa for binary items.
"""
import argparse
import itertools
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TRIGGER = 4
BINARY = ['cause_hit', 'uptake', 'linked_hit', 'reversal', 'mismatch', 'interv_ok']


def per_judgment(j):
    eng = j['engagement']
    run = 0
    for lab in eng[TRIGGER:]:
        if lab != '물러섬':
            break
        run += 1
    rev = j['reversal']
    return {'cause_hit': int(j['cause'] == TRIGGER), 'cause_raw': j['cause'],
            'withdraw_run': run, 'withdraw_after': sum(l == '물러섬' for l in eng[TRIGGER:]),
            'uptake': int(j['uptake'] == '수용'), 'uptake_raw': j['uptake'],
            'reversal': int(bool(rev['present'])), 'linked_hit': int(bool(rev['present']) and rev['linked'] == TRIGGER),
            'linked_raw': rev['linked'], 'mismatch': int(j['profile_mismatch']['present'] == '있음'),
            'interv_ok': int(j['first_intervention_turn'] == TRIGGER), 'interv_raw': j['first_intervention_turn']}


def exact_perm(d):
    d = np.asarray(d, float)
    obs = abs(d.mean())
    null = [abs((np.array(s) * d).mean()) for s in itertools.product([-1, 1], repeat=len(d))]
    return float(np.mean([n >= obs - 1e-12 for n in null]))


def fleiss(rows):
    """rows: list of per-item label lists (binary), equal number of raters."""
    n = len(rows[0])
    cats = [0, 1]
    P, pj = [], np.zeros(2)
    for r in rows:
        c = np.array([r.count(k) for k in cats])
        pj += c
        P.append((np.sum(c * c) - n) / (n * (n - 1)))
    pj /= (len(rows) * n)
    pe = float(np.sum(pj * pj))
    return float((np.mean(P) - pe) / (1 - pe)) if pe < 1 else float('nan')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--key', required=True)
    ap.add_argument('--answers', default=str(HERE / 'answers'))
    ap.add_argument('--pairs', default=str(HERE.parent / 'hidden_reaction_pairs'))
    ap.add_argument('--out', default=str(HERE / 'score.json'))
    a = ap.parse_args()
    key = json.loads(Path(a.key).read_text(encoding='utf-8'))['key']
    judges, models = {}, {}
    for f in sorted(Path(a.answers).glob('judge*_*.json')):
        jn = f.stem.split('_')[0]
        data = json.loads(f.read_text(encoding='utf-8'))
        models.setdefault(jn, set()).add(data.get('model'))
        for j in data['judgments']:
            judges.setdefault(jn, {})[j['id']] = per_judgment(j)
    names = sorted(judges)
    ids = sorted(key)
    for jn in names:
        assert sorted(judges[jn]) == ids, (jn, len(judges[jn]))

    def length_ratio(fname):
        ls = (Path(a.pairs) / fname).read_text(encoding='utf-8').strip().splitlines()
        cl = [len(l.split(': ', 1)[1]) for l in ls[1::2]]
        return float(np.mean(cl[4:6]) / np.mean(cl[0:3]))

    items = BINARY + ['withdraw_run', 'withdraw_after']
    combined = {}
    for did in ids:
        row = {}
        for it in items:
            vals = [judges[jn][did][it] for jn in names]
            row[it] = float(np.mean(vals)) if it.startswith('withdraw') else int(sum(vals) * 2 > len(vals))
            row[it + '_all'] = vals
        row['len_ratio'] = length_ratio(key[did]['file'])
        combined[did] = row

    by = {}
    for did in ids:
        by.setdefault(key[did]['profile'], {})[key[did]['condition']] = combined[did]
    profiles = sorted(by)
    report = {'judges': {jn: sorted(str(m) for m in models[jn]) for jn in names}, 'n_profiles': len(profiles),
              'metrics': {}, 'agreement': {}}
    print('판정자 모델:', report['judges'])
    print(f"\n{'지표':16s} {'제공':>7s} {'미제공':>7s} {'차이':>7s}  p(정확 순열)  제공>미제공/같음/제공<미제공")
    for it in items + ['len_ratio']:
        P = np.array([by[p]['provided'][it] for p in profiles], float)
        N = np.array([by[p]['not_provided'][it] for p in profiles], float)
        d = P - N
        pv = exact_perm(d)
        report['metrics'][it] = {'provided': float(P.mean()), 'not_provided': float(N.mean()), 'diff': float(d.mean()), 'p': pv,
                                 'gt': int((d > 0).sum()), 'eq': int((d == 0).sum()), 'lt': int((d < 0).sum())}
        print(f"{it:16s} {P.mean():7.2f} {N.mean():7.2f} {d.mean():+7.2f}  {pv:.4f}       {(d > 0).sum()}/{(d == 0).sum()}/{(d < 0).sum()}")
    print('\n판정자 간 일치')
    for it in items:
        rows = [combined[did][it + '_all'] for did in ids]
        full = float(np.mean([len(set(r)) == 1 for r in rows]))
        kap = fleiss(rows) if it in BINARY else None
        report['agreement'][it] = {'all_agree': full, 'fleiss_kappa': kap}
        print(f"{it:16s} 전원 일치 {full:.2f}" + (f"  Fleiss κ {kap:.2f}" if kap is not None else ''))
    report['dialogues'] = {did: {**key[did], **{k: v for k, v in combined[did].items()},
                                 'raw': {jn: {k: judges[jn][did][k] for k in ('cause_raw', 'uptake_raw', 'linked_raw', 'interv_raw')} for jn in names}}
                           for did in ids}
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n저장', a.out)


if __name__ == '__main__':
    main()
