"""Build blinded profile-identification packets for LLM evaluators.

All excerpts (client utterances of turns 16-20, tags stripped; counselor turns omitted) from all
batches and both conditions are pooled and shuffled; ten profile cards are relabelled P1..P10 in
shuffled order. One packet per seed so independent evaluators see different orders.
The answer key is written to --key-dir (kept outside the evaluator's working folder during
evaluation, copied into this folder afterwards).
"""
import argparse
import json
from pathlib import Path
import random

from data import CONDITIONS, DEFAULT_BATCHES, HERE, card_text, load


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch', nargs='*', default=DEFAULT_BATCHES)
    ap.add_argument('--start', type=int, default=16, help='첫 턴(1부터)')
    ap.add_argument('--end', type=int, default=20)
    ap.add_argument('--seeds', type=int, nargs='+', default=[1108, 2026])
    ap.add_argument('--out-dir', default=str(HERE / 'blind'))
    ap.add_argument('--key-dir', required=True)
    args = ap.parse_args()
    data, cards = load(args.batch, args.end)
    out, key_dir = Path(args.out_dir), Path(args.key_dir)
    out.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)
    items = [(j, c, p) for (j, c), d in data.items() for p in d]
    for seed in args.seeds:
        rng = random.Random(seed)
        pids = sorted(cards)
        rng.shuffle(pids)
        label = {p: f'P{i + 1}' for i, p in enumerate(pids)}
        order = items[:]
        rng.shuffle(order)
        lines = ['# 프로필 맞추기 자료', '',
                 f'상담 대화의 내담자 발화 발췌 {len(order)}개와 내담자 프로필 10개(P1~P10)가 있다. '
                 f'각 발췌는 한 대화의 {args.start}~{args.end}턴 내담자 발화 5개다(상담사 발화는 뺐다). '
                 '각 발췌가 어느 프로필의 내담자인지 고른다.', '', '## 프로필', '']
        for p in pids:
            lines += [f'### {label[p]}', '', card_text(cards[p]), '']
        lines += ['## 발췌', '']
        key = {}
        for n, (j, c, p) in enumerate(order, 1):
            eid = f'E{n:02d}'
            key[eid] = {'job': j, 'condition': c, 'profile': p, 'label': label[p]}
            lines += [f'### {eid}', '']
            lines += [f'{args.start + k}. {u}' for k, u in enumerate(data[(j, c)][p][args.start - 1:args.end])]
            lines.append('')
        (out / f'packet_{seed}.md').write_text('\n'.join(lines), encoding='utf-8')
        (key_dir / f'key_{seed}.json').write_text(json.dumps({'labels': label, 'excerpts': key,
                                                              'turns': [args.start, args.end]},
                                                             ensure_ascii=False, indent=1), encoding='utf-8')
        print(out / f'packet_{seed}.md', len(order), sum(len(x) for x in lines))


if __name__ == '__main__':
    main()
