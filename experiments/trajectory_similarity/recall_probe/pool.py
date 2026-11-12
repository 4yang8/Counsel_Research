"""Pool unit-level scores from several analyze.py reports and test key comparisons.

Each report must already exist (python analyze.py <run>). Units from different runs are distinct
(different sessions). Paired sign-flip permutation over pooled units.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from analyze import perm

PAIRS = [('probe_T_none', 'regen'), ('probe_T_none', 'probe_k'), ('probe_k', 'regen'),
         ('probe_T_none', 'floor_same_profile'), ('probe_k', 'floor_same_profile')]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('runs', nargs='+')
    ap.add_argument('--out')
    args = ap.parse_args()
    reports = [(r, json.loads((Path(r) / 'report.json').read_text(encoding='utf-8'))) for r in args.runs]
    out = {'runs': args.runs, 'fields': {}}
    for field in ('withheld_content', 'withholding_reason', 'actual_wish'):
        units = {}
        for run, rep in reports:
            for s, vals in rep['fields'][field]['units'].items():
                for u, v in vals.items():
                    units.setdefault(s, {})[f'{run}|{u}'] = v
        res = {'mean': {s: float(np.nanmean([v['match'] for v in vals.values()])) for s, vals in units.items()},
               'n': {s: len(vals) for s, vals in units.items()}, 'comparisons': {}}
        sel = units.get('probe_T_sel', {})
        nok = [u for u, v in sel.items() if not v['k_given']]
        if nok:
            A = np.array([sel[u]['match'] for u in nok])
            B = np.array([units['probe_T_none'][u]['match'] for u in nok])
            d, p, n = perm(A, B)
            res['comparisons']['probe_T_sel(k 미포함)-probe_T_none'] = {'diff': d, 'p': p, 'n_units': n,
                                                                       'sel': float(A.mean()), 'none': float(B.mean())}
        for a, b in PAIRS:
            common = sorted(set(units[a]) & set(units[b]))
            A = np.array([units[a][u]['match'] for u in common])
            B = np.array([units[b][u]['match'] for u in common])
            d, p, n = perm(A, B)
            res['comparisons'][f'{a}-{b}'] = {'diff': d, 'p': p, 'n_units': n, 'a_gt_b': int((A > B).sum())}
        out['fields'][field] = res
        print(f'\n## {field}  (단위 수 {res["n"].get("probe_T_none")})')
        for s in ('floor_same_profile', 'regen', 'probe_k', 'probe_T_none', 'probe_T_sel', 'probe_T_withk'):
            if s in res['mean']:
                print(f'  {s:20s} {res["mean"][s]:.3f}  (n={res["n"][s]})')
        for k, v in res['comparisons'].items():
            extra = f"  a>b {v['a_gt_b']}/{v['n_units']}" if 'a_gt_b' in v else ''
            print(f"  {k:45s} diff {v['diff']:+.3f}  p={v['p']:.4f}{extra}")
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
