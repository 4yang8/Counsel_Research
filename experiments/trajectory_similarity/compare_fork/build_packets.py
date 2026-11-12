"""Blind judging packets for revisit units (branch hidden, units shuffled).

dialogue packet: the earlier exchange at the target turn, the exchange before the revisit, the
  revisit question and the answer. No inner records.
record packet:   the same plus the client's inner record stored at the target turn.
The key (unit id -> profile, branch, turns) is written outside the packets.
"""
import argparse
import json
import random
from pathlib import Path


def turn_lines(public, k, label=True):
    c, r = public[2 * k - 2]['content'], public[2 * k - 1]['content']
    return [f'[{k}턴] 상담사: {c}', f'[{k}턴] 내담자: {r}']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('run')
    ap.add_argument('--key-dir', required=True)
    ap.add_argument('--seed', type=int, default=1111)
    a = ap.parse_args()
    run = Path(a.run)
    units = [u for u in json.loads((run / 'units.json').read_text(encoding='utf-8'))['units'] if u['kind'] == 'revisit']
    sessions = json.loads((run / 'sessions.json').read_text(encoding='utf-8'))
    rng = random.Random(a.seed)
    rng.shuffle(units)
    key, dia, rec = {}, [], []
    for i, u in enumerate(units, 1):
        uid = f'R{i:02d}'
        key[uid] = {k: u[k] for k in ('profile', 'branch', 'counselor_turn', 'target', 'target_common', 'target_injected')}
        public = sessions[f"{u['profile']}|{u['branch']}"]['public']
        c = u['counselor_turn']
        lines = turn_lines(public, u['target'])
        if c - 1 > u['target']:
            lines += ['…'] + turn_lines(public, c - 1)
        lines += [f'[{c}턴] 상담사: {public[2 * c - 2]["content"]}', f'[{c}턴] 내담자: {public[2 * c - 1]["content"]}']
        body = f"## 단위 {uid}\n\n- 되짚은 턴: {u['target']}턴 내담자 발화\n- 되짚는 질문과 답: {c}턴\n\n### 대화 발췌\n" + '\n'.join(lines) + '\n'
        dia.append(body)
        pp = u['target_private']
        rec.append(body + '\n### ' + str(u['target']) + '턴 당시 내담자의 비공개 기록 (상담사는 보지 못함)\n'
                   + '\n'.join(f'- {name}: {pp[k]}' for k, name in (('actual_wish', '실제 바람'), ('intended_message', '전달하려던 의미'),
                                                                  ('withheld_content', '말하지 않은 내용'), ('withholding_reason', '말하지 않은 이유'))) + '\n')
    (run / 'packets').mkdir(exist_ok=True)
    (run / 'packets' / 'dialogue.md').write_text('\n'.join(dia), encoding='utf-8')
    (run / 'packets' / 'record.md').write_text('\n'.join(rec), encoding='utf-8')
    Path(a.key_dir).mkdir(parents=True, exist_ok=True)
    (Path(a.key_dir) / 'key.json').write_text(json.dumps({'seed': a.seed, 'key': key}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(len(units), 'units')


if __name__ == '__main__':
    main()
