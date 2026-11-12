"""Score recall-probe samples against the covert state originally recorded at turn k.

Unit = (dialogue, k). For each setting:
  match     cosine(original field at k, sample field), mean over samples
  spread    mean pairwise cosine among samples of the setting (consistency)
  none_rate share of samples whose withheld_content is '없음'-type (original is never none here)
  top_is_k  share of samples whose most similar original record (over all turns 1..T of the same
            dialogue) is turn k itself; and mean (argmax turn - k) -> drift toward later states
Comparisons: paired sign-flip permutation over units between settings.
Embedding: Qwen3-Embedding-4B without instruction (document-document), L2-normalised.
"""
import argparse
import glob
import json
import os
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from plot_similarity import embed  # noqa: E402
from generate import is_none  # noqa: E402

FIELDS = ['withheld_content', 'actual_wish', 'withholding_reason']
SETTINGS = ['regen', 'probe_k', 'probe_T_none', 'probe_T_sel', 'probe_T_withk']


def cached(run, name, texts, args):
    f = run / f'cache_{name}.npz'
    if f.exists():
        z = np.load(f, allow_pickle=True)
        if list(z['texts']) == texts:
            return z['X']
    X = embed(texts, args.embedding_url, args.embedding_model, 32, 180)
    np.savez(f, X=X, texts=np.array(texts, dtype=object))
    return X


def perm(A, B, n=10000, seed=0):
    ok = ~(np.isnan(A) | np.isnan(B))
    A, B = A[ok], B[ok]
    d = A - B
    rng = np.random.default_rng(seed)
    null = (rng.choice([-1, 1], (n, len(d))) * d).mean(1)
    return float(d.mean()), float((np.abs(null) >= abs(d.mean()) - 1e-12).mean()), int(ok.sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('run')
    ap.add_argument('--embedding-url', default='http://192.168.0.33:8002/v1')
    ap.add_argument('--embedding-model', default='Qwen/Qwen3-Embedding-4B')
    args = ap.parse_args()
    run = Path(args.run)
    rows = [json.loads(l) for l in (run / 'raw.jsonl').read_text(encoding='utf-8').splitlines()]
    errors = [r for r in rows if 'error' in r['result']]
    rows = [r for r in rows if 'error' not in r['result']]
    meta = json.loads((run / 'args.json').read_text(encoding='utf-8'))
    states = {d['session_id']: d for d in meta['dialogues']}
    recs = {}
    for sid, d in states.items():
        recs[sid] = json.loads((PROJECT / d['state']).read_text(encoding='utf-8'))['records'][:meta['turns']]

    report = {'n_rows': len(rows), 'n_errors': len(errors), 'fields': {}}
    units = sorted({(r['session_id'], r['k']) for r in rows})
    for field in FIELDS:
        orig_texts = [rec['inner']['private_position'][field] for sid in states for rec in recs[sid]]
        O = cached(run, f'orig_{field}', orig_texts, args)
        idx, i = {}, 0
        for sid in states:
            for t in range(len(recs[sid])):
                idx[(sid, t + 1)] = i
                i += 1
        S = cached(run, f'sample_{field}', [r['result']['private_position'][field] for r in rows], args)
        out = {s: {'match': [], 'spread': [], 'none_rate': [], 'top_is_k': [], 'shift': []} for s in SETTINGS}
        unit_vals = {s: {} for s in SETTINGS}
        for sid, k in units:
            T = len(recs[sid])
            ok_vec = O[idx[(sid, k)]]
            all_orig = np.stack([O[idx[(sid, t)]] for t in range(1, T + 1)])
            for s in SETTINGS:
                ix = [j for j, r in enumerate(rows) if r['session_id'] == sid and r['k'] == k and r['setting'] == s]
                if not ix:
                    continue
                V = S[ix]
                m = float((V @ ok_vec).mean())
                sims = V @ all_orig.T
                top = sims.argmax(1) + 1
                pair = [float(V[a] @ V[b]) for a in range(len(V)) for b in range(a + 1, len(V))]
                later = [t for t in range(k + 3, T + 1)]
                unit_vals[s][(sid, k)] = {
                    'match': m, 'spread': float(np.mean(pair)) if pair else np.nan,
                    'none_rate': float(np.mean([is_none(rows[j]['result']['private_position']['withheld_content']) for j in ix])),
                    'top_is_k': float((top == k).mean()), 'shift': float((top - k).mean()),
                    'sim_later': float(sims[:, [t - 1 for t in later]].mean()) if later else np.nan,
                    'k_given': bool(k in rows[ix[0]]['private_turns_given'])}
            # profile-level floor: same profile, other dialogues in this run, same turn k
            others = [s2 for s2 in states if s2 != sid and states[s2]['profile'] == states[sid]['profile']]
            unit_vals.setdefault('floor_same_profile', {})[(sid, k)] = {
                'match': float(np.mean([O[idx[(s2, k)]] @ ok_vec for s2 in others])) if others else np.nan}
        summary = {'floor_same_profile': {'match': float(np.nanmean([v['match'] for v in unit_vals['floor_same_profile'].values()])),
                                          'n_units': len(unit_vals['floor_same_profile'])}}
        sel = unit_vals['probe_T_sel']
        for flag in (True, False):
            us = [u for u, v in sel.items() if v['k_given'] == flag]
            summary[f'probe_T_sel_k_given={flag}'] = {
                'n_units': len(us), 'match': float(np.mean([sel[u]['match'] for u in us])) if us else np.nan,
                'match_none_same_units': float(np.mean([unit_vals['probe_T_none'][u]['match'] for u in us])) if us else np.nan}
        for s in SETTINGS:
            vals = unit_vals[s]
            if not vals:
                continue
            summary[s] = {m: float(np.nanmean([v[m] for v in vals.values()])) for m in ('match', 'spread', 'none_rate', 'top_is_k', 'shift', 'sim_later')}
            summary[s]['n_units'] = len(vals)
            for ver in ('Q3inner_memory', 'inner_memory_no'):
                vv = [v['match'] for (sid, k), v in vals.items() if states[sid]['version'] == ver]
                if vv:
                    summary[s][f'match_{ver}'] = float(np.mean(vv))
        comps = {}
        for a, b in [('probe_T_none', 'regen'), ('probe_T_none', 'probe_k'), ('probe_k', 'regen'),
                     ('probe_T_sel', 'probe_T_none'), ('probe_T_withk', 'probe_T_none')]:
            common = sorted(set(unit_vals[a]) & set(unit_vals[b]))
            for m in ('match', 'top_is_k'):
                A = np.array([unit_vals[a][u][m] for u in common])
                B = np.array([unit_vals[b][u][m] for u in common])
                d, p, n = perm(A, B)
                comps[f'{a}-{b}:{m}'] = {'diff': d, 'p': p, 'n_units': n}
        report['fields'][field] = {'summary': summary, 'comparisons': comps,
                                   'units': {s: {f'{sid}|{k}': v for (sid, k), v in unit_vals[s].items()} for s in unit_vals}}
        print(f'\n## {field}')
        print(f"프로필 수준 바닥(같은 프로필 다른 대화, 같은 턴): {summary['floor_same_profile']['match']:.3f}")
        for flag in (True, False):
            v = summary[f'probe_T_sel_k_given={flag}']
            print(f"probe_T_sel k턴 포함={flag}: n={v['n_units']} sel {v['match']:.3f} / none {v['match_none_same_units']:.3f}")
        print('setting         n   match  spread  none%  top=k  shift  later   (match 기억대화/미제공대화)')
        for s in SETTINGS:
            v = summary.get(s)
            if not v:
                continue
            print(f"{s:14s} {v['n_units']:3d}  {v['match']:.3f}  {v['spread']:.3f}  {v['none_rate']:.2f}  {v['top_is_k']:.2f}  {v['shift']:+.1f}  {v['sim_later']:.3f}"
                  f"   ({v.get('match_Q3inner_memory', float('nan')):.3f} / {v.get('match_inner_memory_no', float('nan')):.3f})")
        for k, v in comps.items():
            print(f"  {k:38s} diff {v['diff']:+.3f}  p={v['p']:.4f}  n={v['n_units']}")
    (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('\n오류', len(errors), '| 저장', run / 'report.json')


if __name__ == '__main__':
    main()
