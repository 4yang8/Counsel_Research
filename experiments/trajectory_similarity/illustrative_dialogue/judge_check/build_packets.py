"""Build blind judging packets from the hand-written paired dialogues.

Each pair is split across two packets (A, B) so a judge run never sees both versions of a pair.
Each packet holds 10 dialogues (5 provided, 5 not provided), shuffled, with anonymous ids and a
profile card. The key (id -> profile, condition) is written to --key-dir, outside the packets.
"""
import argparse
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]


def card(pid):
    n = int(pid.split('_')[1])
    p = json.loads((PROJECT / f'resources/001-010/gold_ccd_profile_{n:03d}.json').read_text(encoding='utf-8'))
    d = p['demographic_context']
    return '\n'.join([f"- 나이·직업: {d['age']}, {d['occupation']}",
                      f"- 주 호소: {p['presenting_problem']}",
                      f"- 핵심 신념: {p['core_belief']['core_belief_statement']}",
                      '- 대처 방식:'] + [f'  - {c}' for c in p['coping_strategies']])


def numbered(path):
    ls = path.read_text(encoding='utf-8').strip().splitlines()
    assert len(ls) == 20
    return '\n'.join(f'[{i // 2 + 1}턴] {s}' for i, s in enumerate(ls))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pairs', default=str(HERE.parent / 'hidden_reaction_pairs'))
    ap.add_argument('--out', default=str(HERE / 'packets'))
    ap.add_argument('--key-dir', required=True)
    ap.add_argument('--seed', type=int, default=1111)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    pids = [f'gold_{i:03d}' for i in range(1, 11)]
    in_a = set(rng.sample(pids, 5))  # profiles whose provided version goes to packet A
    packets = {'A': [], 'B': []}
    for i, pid in enumerate(pids, 1):
        for cond in ('provided', 'not_provided'):
            pk = 'A' if (pid in in_a) == (cond == 'provided') else 'B'
            packets[pk].append((pid, cond, Path(a.pairs) / f'{i:02d}_{pid}_{cond}.txt'))
    key = {}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    for pk, items in packets.items():
        rng.shuffle(items)
        parts = []
        for j, (pid, cond, path) in enumerate(items, 1):
            did = f'{pk}{j:02d}'
            key[did] = {'profile': pid, 'condition': cond, 'file': path.name}
            parts.append(f'## 대화 {did}\n\n### 내담자 프로필\n{card(pid)}\n\n### 대화\n{numbered(path)}\n')
        (Path(a.out) / f'packet_{pk}.md').write_text('\n'.join(parts), encoding='utf-8')
        print(pk, [c for _, c, _ in items].count('provided'), 'provided /', len(items))
    Path(a.key_dir).mkdir(parents=True, exist_ok=True)
    (Path(a.key_dir) / 'key.json').write_text(json.dumps({'seed': a.seed, 'key': key}, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
