"""Shared loaders: saved client utterances per (batch, condition, profile) and profile cards.

Reads saved sessions only. Leaked tool-call tags (</utterance>, </invoke> ...) are stripped
from public utterances because they are generation artefacts, not client content, and are
unevenly distributed across conditions (most in 0d9d6d memory condition).
"""
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
CONDITIONS = ['Q3inner_memory', 'inner_memory_no']
DEFAULT_BATCHES = [
    'runs/profile_batches/batch_28cf4899dde548a59f3f',
    'runs/profile_batches/batch_0d9d6d8d8c9a465a814f',
    'runs/profile_batches/batch_c9c8c4f98fef4f7781c6',
    'runs/profile_batches/batch_08f865c61ebd4284a5ae',
]
TAG = re.compile(r'</?(utterance|invoke|parameter|function_calls)[^>]*>')


def clean(text):
    return TAG.sub('', text).strip()


def load(batch_dirs, turns=20):
    """Returns (data, cards). data[(job_id, condition)] = {profile_id: [utterance x turns]};
    cards[profile_id] = profile dict (asserted identical across all sessions)."""
    data, cards = {}, {}
    for b in batch_dirs:
        job_dir = (PROJECT / b).resolve()
        job = json.loads((job_dir / 'job.json').read_text(encoding='utf-8'))
        for r in job['rows']:
            s = json.loads((PROJECT / 'runs/paired' / r['session_id'] / 'session.json').read_text(encoding='utf-8'))
            prof = s['settings']['scenario']['profile']
            pid = prof['profile_id']
            if pid != r['profile_id']:
                raise ValueError(f"프로필 불일치: {r['session_id']}")
            if pid in cards and cards[pid] != prof:
                raise ValueError(f'배치마다 프로필 내용이 다름: {pid}')
            cards[pid] = prof
            u = [clean(x['utterance']) for x in s['steps'] if x['actor'] == 'client'][:turns]
            if len(u) < turns:
                raise ValueError(f"턴 부족: {r['session_id']}")
            data.setdefault((job['id'], r['version']), {})[pid] = u
    return data, cards


def card_text(p):
    """Readable profile card without ids/meta (same text for embedding and the blind packet)."""
    d = p['demographic_context']
    cb = p['core_belief']
    ib = p['intermediate_beliefs']
    lines = [
        f"- 나이/직업: {d['age']}, {d['occupation']}",
        f"- 생애 단계: {d['life_stage']}",
        f"- 배경: {d['background']}",
        f"- 호소 문제: {p['presenting_problem']}",
        f"- 핵심 신념: {cb['core_belief_statement']} ({cb['young_schema']})",
        f"- 어린 시절: {p['childhood_data']}",
        f"- 태도: {ib['attitude']}",
        f"- 규칙: {ib['rule']}",
        f"- 긍정 가정: {ib['assumption_positive']}",
        f"- 부정 가정: {ib['assumption_negative']}",
        '- 대처 전략:',
        *[f'  - {c}' for c in p['coping_strategies']],
        '- 상황 예시:',
    ]
    for s in p['situations']:
        lines.append(f"  - 상황: {s['situation']} / 자동사고: {s['automatic_thought']} / 감정: {s['emotion']} "
                     f"/ 신체: {s['physiological_response']} / 행동: {s['behavior']}")
    return '\n'.join(lines)
