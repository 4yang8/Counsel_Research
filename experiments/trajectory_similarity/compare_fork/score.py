"""Combine blind judgments of revisit units with the key and compare branches.

Majority vote over judges per unit (ties -> 'tie'). Branch comparison: share of units per label;
exact permutation test on the unit-level difference in share (labels shuffled across branches,
10,000 draws) because units are not paired (the counselor chose different targets per branch).
Also reported per profile-level average for profiles with revisits in both branches.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
import random

ITEMS = {'dialogue': ['addresses_then', 'new_content', 'fit_with_earlier', 'depth'],
         'record': ['relation', 'withheld_overlap', 'contradiction']}


def majority(vals):
    c = Counter(vals).most_common()
    return c[0][0] if len(c) == 1 or c[0][1] > c[1][1] else 'tie'


def perm(a, b, n=10000, seed=0):
    rng = random.Random(seed)
    obs = abs(sum(a) / len(a) - sum(b) / len(b))
    pool, hit = a + b, 0
    for _ in range(n):
        rng.shuffle(pool)
        x, y = pool[:len(a)], pool[len(a):]
        hit += abs(sum(x) / len(x) - sum(y) / len(y)) >= obs - 1e-12
    return hit / n


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('run')
    ap.add_argument('--key', required=True)
    a = ap.parse_args()
    run = Path(a.run)
    key = json.loads(Path(a.key).read_text(encoding='utf-8'))['key']
    report = {'n_units': len(key), 'models': {}, 'items': {}, 'agreement': {}}
    units = {uid: dict(v) for uid, v in key.items()}
    for kind, items in ITEMS.items():
        files = sorted(run.glob(f'answers/{kind}_judge*.json'))
        judged = []
        for f in files:
            d = json.loads(f.read_text(encoding='utf-8'))
            report['models'][f.stem] = d.get('model')
            judged.append({j['id']: j for j in d['judgments']})
        for it in items:
            agree = 0
            for uid in units:
                vals = [j[uid][it] for j in judged]
                units[uid][it] = majority(vals)
                units[uid][it + '_all'] = vals
                agree += len(set(vals)) == 1
            report['agreement'][it] = agree / len(units)
    print('판정자 모델', report['models'])
    for kind, items in ITEMS.items():
        for it in items:
            labels = sorted({u[it] for u in units.values()})
            row = {}
            print(f'\n## {it}  (전원 일치 {report["agreement"][it]:.2f})')
            for lab in labels:
                P = [int(u[it] == lab) for u in units.values() if u['branch'] == 'provided']
                W = [int(u[it] == lab) for u in units.values() if u['branch'] == 'withheld']
                p = perm(P, W)
                row[lab] = {'provided': sum(P), 'n_provided': len(P), 'withheld': sum(W), 'n_withheld': len(W), 'p': p}
                print(f'  {lab:20s} 제공 {sum(P):2d}/{len(P)}  미제공 {sum(W):2d}/{len(W)}  p={p:.3f}')
            report['items'][it] = row
    (run / 'score.json').write_text(json.dumps({**report, 'units': units}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n저장', run / 'score.json')


if __name__ == '__main__':
    main()
