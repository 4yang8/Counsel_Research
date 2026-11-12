"""Between-profile similarity of private inner records vs public utterances.

Q1. Does re-feeding past inner records change how similar the *inner* records of
    different profiles are? (Q3inner_memory vs inner_memory_no, same fields)
Q2. Within a condition, is profile identity more visible in inner records than in
    public utterances? (between-profile similarity and identity gap per field)

Reads saved records only; no generation model is called. Each field is embedded
without instructions and L2-normalised, the same representation as plot_similarity.py.
"""
import argparse
import csv
from datetime import datetime
import glob
import json
import os
from pathlib import Path
import sys
import uuid

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from plot_similarity import embed  # noqa: E402

VERSIONS = ['Q3inner_memory', 'inner_memory_no']
FIELDS = {
    'utterance': lambda r: r['utterance'],
    'interpretation': lambda r: r['inner']['appraisal']['interpretation'],
    'immediate_feeling': lambda r: r['inner']['appraisal']['immediate_feeling'],
    'appraisal_uncertainty': lambda r: r['inner']['appraisal']['uncertainty'],
    'intention': lambda r: r['inner']['turn_goal']['intention'],
    'action': lambda r: r['inner']['response_plan']['action'],
    'inner_all': lambda r: '\n'.join(str(v) for part in ('appraisal', 'turn_goal', 'response_plan')
                                     for v in r['inner'][part].values()),
}
# private_position_v1 schema only (2026-11-08~). inner_all above keeps the original three parts.
PRIVATE_POSITION_FIELDS = {
    f: (lambda name: lambda r: r['inner']['private_position'][name])(f)
    for f in ('actual_wish', 'intended_message', 'withheld_content', 'withholding_reason')
}


def load(batch_dir, turns):
    job = json.loads((batch_dir / 'job.json').read_text(encoding='utf-8'))
    storage = batch_dir.parents[1]
    data = {}
    for row in job['rows']:
        if row['version'] not in VERSIONS:
            continue
        sid = row['session_id']
        session = json.loads((storage / 'paired' / sid / 'session.json').read_text(encoding='utf-8'))
        public = [s['utterance'] for s in session['steps'] if s['actor'] == 'client'][:turns]
        current = Path(session['client_storage_dir']) / row['version'] / sid / 'memory_state.json' if session.get('client_storage_dir') else None
        states = [str(current)] if current and current.is_file() else glob.glob(str(storage / 'paired' / sid / 'client_attempts' / '*' / row['version'] / '*' / 'memory_state.json'))
        if not states:
            raise ValueError(f'memory_state.json 없음: {sid}')
        state_path = max(states, key=os.path.getmtime)
        records = json.loads(Path(state_path).read_text(encoding='utf-8'))['records'][:turns]
        if len(records) < turns or [r['utterance'] for r in records] != public:
            raise ValueError(f'기록과 공개 발화 불일치 또는 턴 부족: {sid}')
        data[(row['version'], row['profile_id'])] = {'session_id': sid, 'state': state_path, 'records': records}
    profiles = sorted({p for _, p in data})
    for v in VERSIONS:
        if sorted(p for vv, p in data if vv == v) != profiles:
            raise ValueError(f'{v}: 프로필 집합이 조건 간 다릅니다.')
    return job, data, profiles


def between(X):
    """X: profiles x turns x d -> per-turn mean cosine over distinct profile pairs."""
    iu = np.triu_indices(X.shape[0], 1)
    return np.array([(X[:, t] @ X[:, t].T)[iu].mean() for t in range(X.shape[1])])


def identity_gap(X, start, min_lag=3):
    """Same profile at other turns minus different profiles at other turns (turns >= start)."""
    n, T = X.shape[:2]
    off = ~np.eye(n, dtype=bool)
    same, diff = [], []
    for t in range(start, T):
        for u in range(start, T):
            if abs(t - u) < min_lag:
                continue
            M = X[:, t] @ X[:, u].T
            same.append(np.diag(M).mean())
            diff.append(M[off].mean())
    return float(np.mean(same)), float(np.mean(diff))


def permutation(A, B, start, n_perm, rng):
    """Paired by profile: swap condition labels per profile."""
    obs = (between(A)[start:] - between(B)[start:]).mean()
    null = np.empty(n_perm)
    for i in range(n_perm):
        s = rng.random(A.shape[0]) < .5
        A2 = np.where(s[:, None, None], B, A)
        B2 = np.where(s[:, None, None], A, B)
        null[i] = (between(A2)[start:] - between(B2)[start:]).mean()
    return float(obs), float((np.abs(null) >= abs(obs) - 1e-12).mean()), float(np.quantile(np.abs(null), .95))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batches', nargs='+', required=True)
    ap.add_argument('--turns', type=int, default=20)
    ap.add_argument('--start-turn', type=int, default=6, help='요약 구간 시작 턴(1부터)')
    ap.add_argument('--embedding-url', required=True)
    ap.add_argument('--embedding-model', default='Qwen/Qwen3-Embedding-4B')
    ap.add_argument('--batch-size', type=int, default=16)
    ap.add_argument('--timeout', type=int, default=180)
    ap.add_argument('--permutations', type=int, default=5000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--private-position', action='store_true',
                    help='private_position 항목도 분석(해당 스키마로 생성한 배치만)')
    args = ap.parse_args()
    if args.private_position:
        FIELDS.update(PRIVATE_POSITION_FIELDS)
    start = args.start_turn - 1
    out = HERE / 'results' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    out.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(args.seed)
    summary, per_turn, meta = [], [], {'args': vars(args), 'batches': {}}
    for batch in args.batches:
        bdir = Path(batch).resolve()
        job, data, profiles = load(bdir, args.turns)
        bid = job['id']
        meta['batches'][bid] = {'profiles': profiles,
                                'dialogues': {f'{v}/{p}': {k: d[k] for k in ('session_id', 'state')} for (v, p), d in data.items()}}
        keys = [(v, p) for v in VERSIONS for p in profiles]
        texts, lengths, empties = [], {}, {}
        for f, get in FIELDS.items():
            for k in keys:
                vals = [get(r) for r in data[k]['records']]
                if any(not isinstance(x, str) for x in vals):
                    raise ValueError(f'문자열이 아닌 항목: {bid} {k} {f}')
                # An empty inner field is embedded as '없음' (the schema's own "none" value) and counted.
                blank = sum(not x.strip() for x in vals)
                empties.setdefault(f, {}).setdefault(k[0], 0)
                empties[f][k[0]] += blank
                vals = [x if x.strip() else '없음' for x in vals]
                texts += vals
                lengths.setdefault(f, {}).setdefault(k[0], []).extend(len(x) for x in vals)
        print(f'{bid}: {len(texts)}개 임베딩', flush=True)
        E = embed(texts, args.embedding_url, args.embedding_model, args.batch_size, args.timeout)
        E = E.reshape(len(FIELDS), len(VERSIONS), len(profiles), args.turns, -1)
        for fi, f in enumerate(FIELDS):
            X = E[fi]
            obs, p, null95 = permutation(X[0], X[1], start, args.permutations, rng)
            cross_same = float(np.mean([[X[0, i, t] @ X[1, i, t] for i in range(len(profiles))] for t in range(start, args.turns)]))
            for vi, v in enumerate(VERSIONS):
                b = between(X[vi])
                same, diff = identity_gap(X[vi], start)
                summary.append({'batch': bid, 'field': f, 'version': v,
                                'mean_chars': round(float(np.mean(lengths[f][v])), 1),
                                'empty_as_none': empties[f][v],
                                'between_t1_5': round(float(b[:5].mean()), 4),
                                f'between_t{args.start_turn}_{args.turns}': round(float(b[start:].mean()), 4),
                                'same_profile_other_turns': round(same, 4),
                                'other_profile_other_turns': round(diff, 4),
                                'identity_gap': round(same - diff, 4),
                                'diff_mem_minus_no': round(obs, 4), 'perm_p': round(p, 3), 'perm_null95': round(null95, 4),
                                'same_profile_cross_condition': round(cross_same, 4)})
                per_turn += [{'batch': bid, 'field': f, 'version': v, 'turn': t + 1, 'mean_cosine': float(b[t])}
                             for t in range(args.turns)]
    for name, rows in (('summary.csv', summary), ('per_turn.csv', per_turn)):
        with (out / name).open('w', encoding='utf-8-sig', newline='') as s:
            w = csv.DictWriter(s, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    meta.update({'embedding_dimensions': int(E.shape[-1]), 'fields': list(FIELDS),
                 'metric': 'per-turn mean cosine over distinct profile pairs; identity gap = same profile vs other profiles at turns with lag>=3',
                 'permutation': 'paired by profile, condition labels swapped, two-sided on mean difference over summary turns'})
    (out / 'analysis.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'완료: {out}')


if __name__ == '__main__':
    main()
