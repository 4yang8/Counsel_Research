"""Delayed disclosure: does content recorded as withheld at turn s surface in the public
utterance of the same dialogue at a later turn t >= s + gap, without having been said before?

Uses private_position.withheld_content (private_position_v1 schema batches only). Both conditions
store this record every turn; only Q3inner_memory re-reads past records. Reads saved data only.

Similarity: utterance embedded as a query with a retrieval instruction, withheld content as a
document (Qwen3-Embedding-4B, L2-normalised). Threshold tau is calibrated on unrelated pairs:
a percentile of sim(withheld of profile p, utterance of profile q != p) pooled over batches and
conditions, so the same tau applies to both conditions. A sweep over percentiles is reported.

Definitions for one dialogue (T turns, withheld items W_s with s = 1..T, '없음' items skipped):
  item s is *pending* at turn t if max_{k<=s} sim(W_s, U_k) < tau (not said by the time it was withheld)
  and max_{s<k<t} sim(W_s, U_k) < tau (not said since).
  U_t is a *delayed-disclosure event* if some pending item with s <= t - gap has sim(W_s, U_t) >= tau.
  delay of the event = t - (earliest such s).
Primary per-dialogue metric: events / eligible turns (t = gap+1..T). Also: share of non-empty
withheld items ever disclosed later, mean delay, events with delay >= 5.
Condition comparison: paired permutation over (batch, profile).
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import uuid

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / 'inner_similarity'))
from plot_similarity import embed  # noqa: E402
from analyze import load  # noqa: E402

VERSIONS = ['Q3inner_memory', 'inner_memory_no']
DEFAULT_BATCHES = ['runs/profile_batches/batch_c9c8c4f98fef4f7781c6', 'runs/profile_batches/batch_08f865c61ebd4284a5ae']
PROJECT = HERE.parents[2]
INSTRUCT = ("Instruct: Given a counseling client's spoken utterance, retrieve the private thought "
            "that this utterance reveals or states\nQuery:")


def is_none(w):
    w = w.strip().strip('.').strip()
    return not w or w.startswith('없음') or w in ('해당 없음', '불확실')


def cached(name, texts, args):
    f = HERE / f'cache_{name}.npz'
    if f.exists():
        z = np.load(f, allow_pickle=True)
        if list(z['texts']) == texts:
            return z['X']
    X = embed(texts, args.embedding_url, args.embedding_model, args.batch_size, args.timeout)
    np.savez(f, X=X, texts=np.array(texts, dtype=object))
    return X


def dialogue_events(S, valid, tau, gap):
    """S[s, t] = sim(W_s, U_t) (0-based). valid[s]: withheld item non-empty."""
    T = S.shape[0]
    events, disclosed_items = [], set()
    for t in range(gap, T):
        hits = []
        for s in range(0, t - gap + 1):
            if not valid[s]:
                continue
            if S[s, :s + 1].max() >= tau or (t > s + 1 and S[s, s + 1:t].max() >= tau):
                continue
            if S[s, t] >= tau:
                hits.append(s)
        if hits:
            events.append({'turn': t + 1, 'source_turns': [h + 1 for h in hits], 'delay': t - min(hits)})
            disclosed_items.update(hits)
    return events, disclosed_items


def summarize(S, valid, tau, gap):
    ev, items = dialogue_events(S, valid, tau, gap)
    T = S.shape[0]
    eligible_items = [s for s in range(T - gap) if valid[s] and S[s, :s + 1].max() < tau]
    return {'event_rate': len(ev) / (T - gap),
            'item_disclosed_share': (len(items) / len(eligible_items)) if eligible_items else np.nan,
            'n_eligible_items': len(eligible_items),
            'mean_delay': float(np.mean([e['delay'] for e in ev])) if ev else np.nan,
            'long_events': sum(e['delay'] >= 5 for e in ev), 'events': ev}


def paired_perm(A, B, n, rng):
    ok = ~(np.isnan(A) | np.isnan(B))
    A, B = A[ok], B[ok]
    d = A.mean() - B.mean()
    flips = rng.random((n, len(A))) < .5
    null = np.where(flips, B - A, A - B).mean(1)
    return float(A.mean()), float(B.mean()), float(d), float((np.abs(null) >= abs(d) - 1e-12).mean()), int(ok.sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batches', nargs='+', default=DEFAULT_BATCHES)
    ap.add_argument('--turns', type=int, default=20)
    ap.add_argument('--gap', type=int, default=2, help='withheld 이후 최소 몇 턴 뒤 공개를 지연 공개로 셀지')
    ap.add_argument('--percentiles', type=float, nargs='+', default=[95, 97.5, 99, 99.5])
    ap.add_argument('--primary', type=float, default=99)
    ap.add_argument('--permutations', type=int, default=10000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--embedding-url', required=True)
    ap.add_argument('--embedding-model', default='Qwen/Qwen3-Embedding-4B')
    ap.add_argument('--batch-size', type=int, default=32)
    ap.add_argument('--timeout', type=int, default=180)
    args = ap.parse_args()

    dialogues = []    # dicts: job, version, profile, W list, U list
    for b in args.batches:
        job, data, profiles = load((PROJECT / b).resolve(), args.turns)
        for (v, p), d in data.items():
            dialogues.append({'job': job['id'], 'version': v, 'profile': p, 'session_id': d['session_id'],
                              'W': [r['inner']['private_position']['withheld_content'] for r in d['records']],
                              'U': [r['utterance'] for r in d['records']]})
    dialogues.sort(key=lambda d: (d['job'], d['version'], d['profile']))
    T = args.turns
    Wv = cached('withheld', [w for d in dialogues for w in d['W']], args).reshape(len(dialogues), T, -1)
    Uv = cached('utterance_query', [INSTRUCT + u for d in dialogues for u in d['U']], args).reshape(len(dialogues), T, -1)
    valid = np.array([[not is_none(w) for w in d['W']] for d in dialogues])

    # threshold calibration on unrelated-profile pairs (all turns)
    null = []
    for i, a in enumerate(dialogues):
        for j, b in enumerate(dialogues):
            if a['profile'] != b['profile']:
                M = Wv[i][valid[i]] @ Uv[j].T
                null.append(M.ravel())
    null = np.concatenate(null)
    taus = {p: float(np.percentile(null, p)) for p in args.percentiles}
    own = np.concatenate([(Wv[i][valid[i]] @ Uv[i].T).ravel() for i in range(len(dialogues))])
    print('tau:', {k: round(v, 4) for k, v in taus.items()}, '| 같은 대화 쌍 중 tau 이상 비율:',
          {k: round(float((own >= v).mean()), 3) for k, v in taus.items()})

    rng = np.random.default_rng(args.seed)
    results, per_dialogue = {}, {}
    for pct, tau in taus.items():
        res = [summarize(Wv[i] @ Uv[i].T, valid[i], tau, args.gap) for i in range(len(dialogues))]
        per_dialogue[pct] = res
        idx = {v: {(d['job'], d['profile']): i for i, d in enumerate(dialogues) if d['version'] == v} for v in VERSIONS}
        keys = sorted(idx[VERSIONS[0]])
        out = {}
        for m in ('event_rate', 'item_disclosed_share', 'mean_delay', 'long_events'):
            A = np.array([res[idx[VERSIONS[0]][k]][m] for k in keys], dtype=float)
            B = np.array([res[idx[VERSIONS[1]][k]][m] for k in keys], dtype=float)
            a, b, d, p, n = paired_perm(A, B, args.permutations, rng)
            out[m] = {'memory': a, 'no': b, 'diff': d, 'p': p, 'n_pairs': n}
        for j in sorted({d['job'] for d in dialogues}):
            ks = [k for k in keys if k[0] == j]
            A = np.array([res[idx[VERSIONS[0]][k]]['event_rate'] for k in ks])
            B = np.array([res[idx[VERSIONS[1]][k]]['event_rate'] for k in ks])
            out[f'event_rate_{j}'] = {'memory': float(A.mean()), 'no': float(B.mean()),
                                      'memory_gt_no': int((A > B).sum()), 'no_gt_memory': int((B > A).sum())}
        out['total_events'] = {v: sum(len(res[i]['events']) for i, d in enumerate(dialogues) if d['version'] == v) for v in VERSIONS}
        out['eligible_items'] = {v: sum(res[i]['n_eligible_items'] for i, d in enumerate(dialogues) if d['version'] == v) for v in VERSIONS}
        out['valid_items'] = {v: int(sum(valid[i].sum() for i, d in enumerate(dialogues) if d['version'] == v)) for v in VERSIONS}
        results[pct] = {'tau': tau, **out}
        print(f"\n[{pct} 백분위, tau={tau:.4f}] 사건 수 기억/미제공 {out['total_events']} | 공개 전 보류 항목 {out['eligible_items']}")
        for m in ('event_rate', 'item_disclosed_share', 'mean_delay', 'long_events'):
            r = out[m]
            print(f"  {m:22s} {r['memory']:.3f} / {r['no']:.3f}  diff {r['diff']:+.3f}  p={r['p']:.3f} (n={r['n_pairs']})")
        for j in sorted({d['job'] for d in dialogues}):
            r = out[f'event_rate_{j}']
            print(f"  {j[6:12]} event_rate {r['memory']:.3f}/{r['no']:.3f}  기억>미제공 {r['memory_gt_no']} / 반대 {r['no_gt_memory']}")

    run = HERE / 'results' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    run.mkdir(parents=True, exist_ok=False)
    (run / 'summary.json').write_text(json.dumps({'args': vars(args), 'instruction': INSTRUCT, 'results': results},
                                                 ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    rows = []
    for pct, res in per_dialogue.items():
        for d, r in zip(dialogues, res):
            for e in r['events']:
                s = e['source_turns'][0]
                rows.append({'percentile': pct, 'job': d['job'], 'version': d['version'], 'profile': d['profile'],
                             'session_id': d['session_id'], 'turn': e['turn'], 'source_turns': e['source_turns'],
                             'delay': e['delay'], 'withheld': d['W'][s - 1], 'utterance': d['U'][e['turn'] - 1],
                             'sim': float(Wv[dialogues.index(d)][s - 1] @ Uv[dialogues.index(d)][e['turn'] - 1])})
    (run / 'events.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n완료: {run}')


if __name__ == '__main__':
    main()
