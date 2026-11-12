"""Package explicitly authored synthetic positive controls; no model calls.

These are measurement fixtures, never Q3 experimental runs. The source marker
remains synthetic_authored in the analyzer-compatible session wrapper.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONDITIONS = ('인공_다양성유지', '인공_반응수렴')
QUESTIONS = [
    '최근 회의에서 어떤 일이 있었고, 지금 어떤 부분이 마음에 남아 있나요?',
    '그 자리에서 원래 마음을 말하기 어려웠던 구체적인 배경이 있을까요?',
    '그 순간 가장 신경 쓰였던 것은 무엇이었나요?',
    '당시 괜찮다고 한 말에는 어떤 뜻이 담겨 있었나요?',
    '어떤 조건이 있었다면 그 자리에서 다르게 답할 수 있었을까요?',
    '업무량만 줄어든다면 지금 말씀하신 부담도 해결될까요?',
    '이 이야기를 다룰 때 어떤 반응이나 도움이 먼저 필요할까요?',
    '바로 방법을 제안받는다면 어떤 점이 걸릴 것 같나요?',
    '어떤 경험을 하면 지금의 부담이 조금 덜해질 것 같나요?',
    '다음 회의에서는 어떻게 답해보고 싶으신가요?',
    '그렇게 말하거나 행동하는 데 여전히 어려운 부분은 무엇인가요?',
    '지금 가능한 작은 시도 하나를 정한다면 무엇이 좋을까요?',
    '그 시도를 한 뒤에는 어떤 걱정이 생길 수 있을까요?',
    '어떤 일이 실제로 일어나면 이전과 다른 경험이라고 느낄 수 있을까요?',
    '그런 경험이 생기더라도 남아 있을 마음이 있을까요?',
    '처음 회의에서 괜찮다고 했던 이유를 다시 말한다면 어떻게 설명하시겠어요?',
    '그때와 비교해 지금 생각에서 달라진 부분은 무엇인가요?',
    '주변에서 어떤 조언을 하면 오히려 도움이 되지 않을 것 같나요?',
    '앞으로 무엇부터 해보고 싶으신가요?',
    '이 문제에서 본인이 바라는 변화는 어떤 모습인가요?',
]


def assemble():
    cases = json.loads((ROOT / 'authored_cases.json').read_text())
    phrases = json.loads((ROOT / 'convergent_phrases.json').read_text())
    assert len(cases) == 20 and len(QUESTIONS) == 20
    assert len({c['id'] for c in cases}) == 20
    manifest = {'kind': 'synthetic_measurement_positive_control',
                'authorship': 'assistant-authored text with deterministic packaging; no Q3 or generation-server calls',
                'conditions': CONDITIONS, 'profiles': 20, 'client_turns': 20,
                'common_prefix_turns': 5, 'partly_specific_turns': [6, 7, 8],
                'generic_turns': list(range(9, 21)), 'sessions': []}
    all_texts = {condition: [] for condition in CONDITIONS}
    for i, case in enumerate(cases):
        assert len(case['responses']) == 20, case['id']
        profile = {'profile_id': case['id'], 'synthetic': True,
                   'setting': '직장 회의에서 부담이나 보류가 있는데도 괜찮다고 말한 직원',
                   'distinctive_context': case['label'], 'past_private_reason': case['private_reason']}
        for condition in CONDITIONS:
            responses = []
            for t in range(1, 21):
                if condition == CONDITIONS[0] or t <= 5:
                    response = case['responses'][t - 1]
                else:
                    pool = phrases[str(t)]
                    # Bijection over 5 × 4 clauses: twenty unique utterances at
                    # each turn, with balanced clause reuse and varying pairing.
                    slot = (i * 7 + t * 3) % 20
                    response = pool['starts'][slot // 4] + ' ' + pool['ends'][slot % 4]
                    if t <= 8:
                        response = case['transition_anchor'] + ' 때문에 아직 마음이 쓰여요. ' + response
                assert response.strip() and not any(ch in response for ch in '()（）*<>')
                responses.append(response)
            all_texts[condition].append(responses)
            sid = ('maintained_' if condition == CONDITIONS[0] else 'convergent_') + case['id']
            folder = ROOT / 'sessions' / sid
            folder.mkdir(parents=True, exist_ok=True)
            steps = []
            md = [f'# {case["id"]} · {condition}', '',
                  '> 인공적으로 작성한 측정용 대화. 실제 Q3 생성 결과가 아님.', '',
                  f'- 프로필 특징: {case["label"]}', f'- 최초 비공개 이유: {case["private_reason"]}',
                  '- 각 턴은 상담사 질문과 내담자 답변 한 쌍이다. 아래 대화 밖 프로필은 임베딩에 포함되지 않는다.', '']
            for t, (question, response) in enumerate(zip(QUESTIONS, responses), 1):
                for actor, text in [('counselor', question), ('client', response)]:
                    steps.append({'number': len(steps) + 1, 'turn': t, 'actor': actor,
                                  'source': 'synthetic_authored', 'utterance': text})
                md += [f'## {t}턴', '', f'**상담사:** {question}', '', f'**내담자:** {response}', '']
            session = {'session_id': sid, 'client_version': condition,
                       'synthetic': True, 'provenance': manifest['authorship'],
                       'condition_purpose': '개인별 이유를 계속 반영' if condition == CONDITIONS[0] else '개인별 이유를 잃고 일반적인 눈치·자기표현 반응으로 수렴',
                       'settings': {'scenario': {'profile': profile}}, 'steps': steps,
                       'public_dialogue': [{'role': s['actor'], 'content': s['utterance']} for s in steps]}
            (folder / 'session.json').write_text(json.dumps(session, ensure_ascii=False, indent=2) + '\n')
            (folder / 'dialogue.md').write_text('\n'.join(md))
            manifest['sessions'].append({'profile_id': case['id'], 'condition': condition,
                                         'json': str((folder / 'session.json').relative_to(ROOT)),
                                         'markdown': str((folder / 'dialogue.md').relative_to(ROOT))})
    for i in range(20):
        assert all_texts[CONDITIONS[0]][i][:5] == all_texts[CONDITIONS[1]][i][:5]
    for condition, rows in all_texts.items():
        for t in range(20):
            assert len({r[t] for r in rows}) == 20, (condition, t + 1, 'duplicate utterance')
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print('Created 40 synthetic dialogues × 20 client turns, JSON + readable Markdown.')


if __name__ == '__main__':
    assemble()
