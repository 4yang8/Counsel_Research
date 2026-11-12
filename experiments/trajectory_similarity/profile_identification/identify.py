"""Embedding-based profile identification from 5-turn windows of client utterances.

For each dialogue window (turns 1-5, 6-10, 11-15, 16-20) rank the 10 profile cards by cosine
similarity and record top-1 hit, reciprocal rank and margin (own-card sim minus mean of other
cards). Two query methods:
  centroid  mean of per-utterance embeddings (no instruction), card embedded without instruction
  instruct  the 5 utterances joined into one text, embedded with a retrieval instruction
            (Qwen3-Embedding query format); cards are documents without instruction
Each is also scored 'centered': card similarity minus that card's mean similarity over all
queries of the same window (both conditions, all batches) to remove card hubness.
Condition comparison: paired permutation over (batch, profile) pairs, swapping condition labels.
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
sys.path.insert(0, str(HERE))
from plot_similarity import embed  # noqa: E402
from data import CONDITIONS, DEFAULT_BATCHES, card_text, load  # noqa: E402

W = 5
INSTRUCT = ('Instruct: Given several utterances spoken by a counseling client, retrieve the client profile '
            '(background, core belief, coping strategies, typical situations) of the person who said them\nQuery:')


def cached_embed(name, texts, args):
    f = HERE / f'cache_{name}.npz'
    if f.exists():
        z = np.load(f, allow_pickle=True)
        if list(z['texts']) == texts:
            return z['X']
    X = embed(texts, args.embedding_url, args.embedding_model, args.batch_size, args.timeout)
    np.savez(f, X=X, texts=np.array(texts, dtype=object))
    return X


def scores(S, truth):
    """S: queries x cards similarity; truth: card index per query."""
    own = S[np.arange(len(S)), truth]
    rank = 1 + (S > own[:, None]).sum(1)
    others = (S.sum(1) - own) / (S.shape[1] - 1)
    return {'hit': (rank == 1).astype(float), 'rr': 1 / rank, 'margin': own - others}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch', nargs='*', default=DEFAULT_BATCHES)
    ap.add_argument('--turns', type=int, default=20)
    ap.add_argument('--permutations', type=int, default=5000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--embedding-url', required=True)
    ap.add_argument('--embedding-model', default='Qwen/Qwen3-Embedding-4B')
    ap.add_argument('--batch-size', type=int, default=32)
    ap.add_argument('--timeout', type=int, default=180)
    args = ap.parse_args()
    data, cards = load(args.batch, args.turns)
    pids = sorted(cards)
    jobs = sorted({j for j, _ in data}, key=lambda j: [Path(b).name for b in args.batch].index(j))
    keys = [(j, c, p) for j in jobs for c in CONDITIONS for p in pids]   # query order
    truth = np.array([pids.index(p) for _, _, p in keys])
    windows = [(a, a + W) for a in range(0, args.turns, W)]

    C = cached_embed('cards', [card_text(cards[p]) for p in pids], args)
    utt = [t for j, c, p in keys for t in data[(j, c)][p]]
    U = cached_embed('utterances', utt, args).reshape(len(keys), args.turns, -1)
    qtexts = [INSTRUCT + '\n'.join(data[(j, c)][p][a:b]) for j, c, p in keys for a, b in windows]
    Q = cached_embed('instruct_windows', qtexts, args).reshape(len(keys), len(windows), -1)

    rng = np.random.default_rng(args.seed)
    rows, per_query = [], []
    for wi, (a, b) in enumerate(windows):
        cen = U[:, a:b].mean(1)
        cen /= np.linalg.norm(cen, axis=1, keepdims=True)
        sims = {'centroid': cen @ C.T, 'instruct': Q[:, wi] @ C.T}
        for m in list(sims):
            sims[m + '_centered'] = sims[m] - sims[m].mean(0, keepdims=True)
        for m, S in sims.items():
            sc = scores(S, truth)
            pred = S.argmax(1)
            for i, (j, c, p) in enumerate(keys):
                per_query.append({'window': f'{a + 1}-{b}', 'method': m, 'job': j, 'condition': c, 'profile': p,
                                  'pred': pids[pred[i]], **{k: float(v[i]) for k, v in sc.items()}})
            for scope in ['pooled'] + jobs:
                sel = np.array([scope == 'pooled' or j == scope for j, _, _ in keys])
                idx = {c: np.array([i for i, (j, cc, _) in enumerate(keys) if sel[i] and cc == c]) for c in CONDITIONS}
                row = {'window': f'{a + 1}-{b}', 'method': m, 'scope': scope, 'n_per_condition': len(idx[CONDITIONS[0]])}
                for k in ('hit', 'rr', 'margin'):
                    A, B = sc[k][idx[CONDITIONS[0]]], sc[k][idx[CONDITIONS[1]]]
                    d = A.mean() - B.mean()
                    flips = rng.random((args.permutations, len(A))) < .5
                    null = np.where(flips, B - A, A - B).mean(1)
                    row[f'{k}_memory'], row[f'{k}_no'] = float(A.mean()), float(B.mean())
                    row[f'{k}_diff'] = float(d)
                    row[f'{k}_p'] = float((np.abs(null) >= abs(d) - 1e-12).mean())
                rows.append(row)

    out = HERE / 'results' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    out.mkdir(parents=True, exist_ok=False)
    cols = list(rows[0])
    (out / 'summary.csv').write_text('\n'.join([','.join(cols)] + [','.join(
        f'{r[k]:.4f}' if isinstance(r[k], float) else str(r[k]) for k in cols) for r in rows]) + '\n', encoding='utf-8-sig')
    (out / 'per_query.json').write_text(json.dumps(per_query, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'args.json').write_text(json.dumps({**vars(args), 'jobs': jobs, 'profiles': pids, 'instruction': INSTRUCT},
                                              ensure_ascii=False, indent=2), encoding='utf-8')
    for m in sims:
        print(f'\n[{m}] pooled (n=40/조건): 창 | top1 기억/미제공 (p) | MRR 기억/미제공 | margin 기억/미제공 (p)')
        for r in rows:
            if r['method'] == m and r['scope'] == 'pooled':
                print(f"  {r['window']:>5} | {r['hit_memory']:.3f}/{r['hit_no']:.3f} ({r['hit_p']:.2f}) | "
                      f"{r['rr_memory']:.3f}/{r['rr_no']:.3f} | {r['margin_memory']:+.4f}/{r['margin_no']:+.4f} ({r['margin_p']:.2f})")
    print(f'\n완료: {out}')


if __name__ == '__main__':
    main()
