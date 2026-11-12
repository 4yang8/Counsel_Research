"""Score blinded LLM profile-identification answers against the keys.

Per condition: top-1 and top-2 accuracy, confidence distribution; paired permutation over
(batch, profile) pairs (swap condition labels), pooled over evaluators by averaging their hits.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np

from data import CONDITIONS, HERE


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seeds', type=int, nargs='+', default=[1108, 2026])
    ap.add_argument('--blind-dir', default=str(HERE / 'blind'))
    ap.add_argument('--permutations', type=int, default=10000)
    args = ap.parse_args()
    bd = Path(args.blind_dir)
    rec = {}          # (job, cond, profile) -> list of per-evaluator dicts
    report = {'evaluators': {}}
    for s in args.seeds:
        key = json.loads((bd / f'key_{s}.json').read_text(encoding='utf-8'))
        ans = json.loads((bd / f'answers_{s}.json').read_text(encoding='utf-8'))
        missing = set(key['excerpts']) - set(ans)
        if missing:
            raise ValueError(f'seed {s}: 답 없음 {sorted(missing)}')
        for eid, k in key['excerpts'].items():
            a = ans[eid]
            rec.setdefault((k['job'], k['condition'], k['profile']), []).append({
                'seed': s, 'eid': eid, 'hit1': float(a['top1'] == k['label']),
                'hit2': float(k['label'] in (a['top1'], a.get('top2'))), 'conf': int(a['confidence'])})
        ev = {}
        for c in CONDITIONS:
            r = [x for (j, cc, p), xs in rec.items() if cc == c for x in xs if x['seed'] == s]
            ev[c] = {'top1': np.mean([x['hit1'] for x in r]), 'top2': np.mean([x['hit2'] for x in r]),
                     'conf': dict(sorted(Counter(x['conf'] for x in r).items())),
                     'top1_by_conf': {k: float(np.mean([x['hit1'] for x in r if x['conf'] == k]))
                                      for k in sorted({x['conf'] for x in r})}}
        report['evaluators'][s] = ev
    pairs = sorted({(j, p) for j, _, p in rec})
    jobs = sorted({j for j, _ in pairs})
    rng = np.random.default_rng(0)
    report['pooled'] = {}
    for metric in ('hit1', 'hit2'):
        for scope in ['all'] + jobs:
            ps = [x for x in pairs if scope == 'all' or x[0] == scope]
            A = np.array([np.mean([x[metric] for x in rec[(j, CONDITIONS[0], p)]]) for j, p in ps])
            B = np.array([np.mean([x[metric] for x in rec[(j, CONDITIONS[1], p)]]) for j, p in ps])
            d = A.mean() - B.mean()
            flips = rng.random((args.permutations, len(A))) < .5
            null = np.where(flips, B - A, A - B).mean(1)
            report['pooled'][f'{metric}_{scope}'] = {'memory': float(A.mean()), 'no': float(B.mean()),
                                                     'diff': float(d), 'n_pairs': len(ps),
                                                     'p': float((np.abs(null) >= abs(d) - 1e-12).mean())}
    # agreement between evaluators on correctness
    if len(args.seeds) == 2:
        h = np.array([[x['hit1'] for x in xs] for xs in rec.values()])
        report['evaluator_agreement_hit1'] = float((h[:, 0] == h[:, 1]).mean())
    per_profile = {}
    for (j, c, p), xs in rec.items():
        per_profile.setdefault(p, {}).setdefault(c, []).append(np.mean([x['hit1'] for x in xs]))
    report['per_profile_top1'] = {p: {c: float(np.mean(v)) for c, v in d.items()} for p, d in sorted(per_profile.items())}
    report['per_excerpt'] = {f'{j}|{c}|{p}': xs for (j, c, p), xs in sorted(rec.items())}
    (bd / 'score.json').write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'per_excerpt'}, ensure_ascii=False, indent=1, default=float))


if __name__ == '__main__':
    main()
