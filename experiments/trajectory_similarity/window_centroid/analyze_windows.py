"""Window-level convergence: window-centroid similarity, DTW over sliding centroids,
and late-window content checks. Reads saved dialogues only; no generation model calls.

Inputs
  --batch <job dir> [...]          real batches (Q3inner_memory vs inner_memory_no)
  --synthetic <sessions dir>       synthetic positive control (maintained vs convergent),
                                   plus a derived 'convergent_shifted' sequence set in which
                                   profile k's generic onset is delayed by k % 5 turns using
                                   only existing manuscript utterances (not saved as sessions).
"""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import uuid

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from plot_similarity import embed  # noqa: E402

REAL = ['Q3inner_memory', 'inner_memory_no']
SYN = {'maintained': '인공_다양성유지', 'convergent': '인공_반응수렴'}
W = 5          # window size (turns)
BAND = 5       # DTW Sakoe-Chiba band (window positions)


def load_batch(job_dir, turns):
    job = json.loads((job_dir / 'job.json').read_text(encoding='utf-8'))
    storage = job_dir.parents[1]
    D = {}
    for r in job['rows']:
        s = json.loads((storage / 'paired' / r['session_id'] / 'session.json').read_text(encoding='utf-8'))
        u = [x['utterance'] for x in s['steps'] if x['actor'] == 'client'][:turns]
        if len(u) < turns:
            raise ValueError(f"턴 부족: {r['session_id']}")
        D[(r['version'], r['profile_id'])] = u
    profiles = sorted({p for _, p in D})
    return job['id'], {v: [D[(v, p)] for p in profiles] for v in REAL}, profiles


def load_synthetic(sessions_dir, turns):
    D = {}
    for f in sorted(sessions_dir.glob('*/session.json')):
        s = json.loads(f.read_text(encoding='utf-8'))
        D[(s['client_version'], s['settings']['scenario']['profile']['profile_id'])] = \
            [x['utterance'] for x in s['steps'] if x['actor'] == 'client'][:turns]
    profiles = sorted({p for _, p in D})
    seqs = {k: [D[(name, p)] for p in profiles] for k, name in SYN.items()}
    shifted = []
    for i, p in enumerate(profiles):
        d = i % 5
        conv, maint = D[(SYN['convergent'], p)], D[(SYN['maintained'], p)]
        shifted.append(conv[:8] + maint[8:8 + d] + conv[8:turns - d])
    seqs['convergent_shifted'] = shifted
    return 'synthetic_control', seqs, profiles


def get_vectors(tag, seqs, args):
    cache = HERE / f'cache_full_{tag}.npz'
    texts = [t for k in seqs for prof in seqs[k] for t in prof]
    key = str(abs(hash(tuple(texts))))  # session-local guard only; checked with texts below
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        if list(z['texts']) == texts:
            X = z['X']
            return {k: X[i] for i, k in enumerate(seqs)}
    X = embed(texts, args.embedding_url, args.embedding_model, args.batch_size, args.timeout)
    X = X.reshape(len(seqs), len(next(iter(seqs.values()))), args.turns, -1)
    np.savez(cache, X=X, texts=np.array(texts, dtype=object))
    del key
    return {k: X[i] for i, k in enumerate(seqs)}


def unit(C):
    return C / np.linalg.norm(C, axis=-1, keepdims=True)


def pair_mean(M):
    n = M.shape[0]
    return float(M[~np.eye(n, dtype=bool)].mean())


def same_turn(X, a, b):
    return float(np.mean([pair_mean(X[:, t] @ X[:, t].T) for t in range(a, b)]))


def window_sim(X, a):
    C = unit(X[:, a:a + W].mean(1))
    return pair_mean(C @ C.T)


def sliding(X):
    return unit(np.stack([X[:, a:a + W].mean(1) for a in range(X.shape[1] - W + 1)], 1))


def dtw(a, b, band):
    """Cosine-distance DTW between two centroid sequences, normalised by path length."""
    n, m = len(a), len(b)
    cost = 1 - a @ b.T
    D = np.full((n + 1, m + 1), np.inf)
    L = np.zeros((n + 1, m + 1))
    D[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(max(1, i - band), min(m, i + band) + 1):
            prev = min((D[i - 1, j - 1], L[i - 1, j - 1]), (D[i - 1, j], L[i - 1, j]), (D[i, j - 1], L[i, j - 1]))
            D[i, j] = prev[0] + cost[i - 1, j - 1]
            L[i, j] = prev[1] + 1
    return D[n, m] / L[n, m]


def dtw_pairs(S, start):
    """Mean over profile pairs of (aligned distance, DTW distance) for window positions >= start."""
    S = S[:, start:]
    al, dw = [], []
    for i in range(len(S)):
        for j in range(i + 1, len(S)):
            al.append(float(np.mean(1 - (S[i] * S[j]).sum(-1))))
            dw.append(dtw(S[i], S[j], BAND))
    return float(np.mean(al)), float(np.mean(dw))


def metrics(X, dtw_start):
    T = X.shape[1]
    S = sliding(X)
    al, dw = dtw_pairs(S, dtw_start)
    out = {f'same_turn_{a + 1}_{a + W}': same_turn(X, a, a + W) for a in range(0, T, W)}
    out.update({f'window_{a + 1}_{a + W}': window_sim(X, a) for a in range(0, T, W)})
    out['window_rise'] = out[f'window_{T - W + 1}_{T}'] - out[f'window_1_{W}']
    out['aligned_dist'] = al
    out['dtw_dist'] = dw
    out['dtw_gain'] = al - dw
    return out


def permute(XA, XB, keys, n_perm, rng, dtw_start):
    obs_a, obs_b = metrics(XA, dtw_start), metrics(XB, dtw_start)
    null = {k: [] for k in keys}
    for _ in range(n_perm):
        f = rng.random(len(XA)) < .5
        A = np.where(f[:, None, None], XB, XA)
        B = np.where(f[:, None, None], XA, XB)
        ma, mb = metrics(A, dtw_start), metrics(B, dtw_start)
        for k in keys:
            null[k].append(ma[k] - mb[k])
    res = {}
    for k in keys:
        d = obs_a[k] - obs_b[k]
        nl = np.abs(np.array(null[k]))
        res[k] = {'a': obs_a[k], 'b': obs_b[k], 'diff': d, 'p': float((nl >= abs(d) - 1e-12).mean()),
                  'null95': float(np.quantile(nl, .95))}
    return obs_a, obs_b, res


TOK = re.compile(r'[가-힣]{2,}')


def content(seqs_cond, a, b):
    """Length and cross-profile shared vocabulary inside a turn window."""
    lens = [len(t) for prof in seqs_cond for t in prof[a:b]]
    vocab = [set(w for t in prof[a:b] for w in TOK.findall(t)) for prof in seqs_cond]
    c = Counter(w for v in vocab for w in v)
    n = len(vocab)
    shared = sorted([w for w, k in c.items() if k >= max(2, int(0.6 * n))], key=lambda w: -c[w])
    jacc = [len(vocab[i] & vocab[j]) / max(1, len(vocab[i] | vocab[j]))
            for i in range(n) for j in range(i + 1, n)]
    return {'mean_chars': float(np.mean(lens)), 'vocab_jaccard': float(np.mean(jacc)),
            'shared_words_60pct': shared[:15]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch', nargs='*', default=[])
    ap.add_argument('--synthetic')
    ap.add_argument('--turns', type=int, default=20)
    ap.add_argument('--dtw-start-window', type=int, default=5, help='DTW에 쓰는 첫 슬라이딩 구간 위치(0부터). 기본 5 = 6~10턴 구간부터')
    ap.add_argument('--permutations', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--embedding-url', required=True)
    ap.add_argument('--embedding-model', default='Qwen/Qwen3-Embedding-4B')
    ap.add_argument('--batch-size', type=int, default=32)
    ap.add_argument('--timeout', type=int, default=180)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    keys = ['window_16_20', 'window_rise', 'same_turn_16_20', 'aligned_dist', 'dtw_dist', 'dtw_gain']
    report = {'args': vars(args), 'window': W, 'dtw_band': BAND, 'inputs': {}}
    sources = [load_batch(Path(b).resolve(), args.turns) for b in args.batch]
    if args.synthetic:
        sources.append(load_synthetic(Path(args.synthetic).resolve(), args.turns))
    for tag, seqs, profiles in sources:
        print(f'{tag}: {len(profiles)}개 프로필, 조건 {list(seqs)}', flush=True)
        V = get_vectors(tag, seqs, args)
        entry = {'profiles': profiles, 'conditions': {}, 'comparisons': {}, 'content': {}}
        for k in seqs:
            entry['conditions'][k] = metrics(V[k], args.dtw_start_window)
            entry['content'][k] = {f'{a + 1}_{a + W}': content(seqs[k], a, a + W) for a in (0, args.turns - W)}
        pairs = [('Q3inner_memory', 'inner_memory_no')] if tag != 'synthetic_control' else \
                [('convergent', 'maintained'), ('convergent_shifted', 'maintained')]
        for a, b in pairs:
            _, _, res = permute(V[a], V[b], keys, args.permutations, rng, args.dtw_start_window)
            entry['comparisons'][f'{a}-{b}'] = res
        report['inputs'][tag] = entry
    out = HERE / 'results' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    out.mkdir(parents=True, exist_ok=False)
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'완료: {out}')


if __name__ == '__main__':
    main()
