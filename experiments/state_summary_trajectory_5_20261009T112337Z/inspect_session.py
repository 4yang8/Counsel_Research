"""Check real handoffs and export exact speech/state/summary evidence."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
session_dirs = list((ROOT / 'sessions/state_summary').iterdir())
assert len(session_dirs) == 1, session_dirs
session = session_dirs[0]
records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((session / 'inner_states').glob('*.json'))]
logs = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(session.glob('*.json'))]
scenario = json.loads((ROOT / 'scenario.json').read_text(encoding='utf-8'))
results = []
assert len(logs) == 5, len(logs)
for index, log in enumerate(logs, 1):
    committed = log['status'] == 'completed'
    if committed:
        record = records[index-1]
        assert record['turn'] == index
        assert log['history'][-1]['content'] == record['utterance']
        assert all(row['role'] in ('user', 'assistant') for row in log['history'])
    else:
        # Analysis only. Do not normalize the runtime result or commit a failed record.
        assert index == 5 and log['status'] == 'failed'
        raw = log['response'].strip()
        assert raw.startswith('```json\n') and raw.endswith('\n```'), raw
        parsed_for_analysis = json.loads(raw[len('```json\n'):-len('\n```')])
        before = records[-1]['state_after']
        after = {key: {**row, 'level': parsed_for_analysis['inner_state']['states'][key]['level']}
                 for key, row in before.items()}
        record = {'turn': index, 'turn_id': log['turn_id'], 'state_before': before,
                  'state_after': after, 'counselor_utterance': log['input']['messages'][-1]['content'],
                  **parsed_for_analysis}
    expected_before = records[index-2]['state_after'] if index > 1 else scenario['states']
    assert record['state_before'] == expected_before
    main = log['model_calls'][0]['request']['messages'][1]['content']
    assert all(r['utterance'] in main and r['counselor_utterance'] in main for r in records[:index-1])
    assert all(scenario['states'][key]['name'] in main for key in record['state_before'])
    events = log.get('subagent_events', [])
    requests = [e for e in events if e['event']['type'] == 'request' and e.get('agent_kind') == 'summary']
    summary_event = next(e for e in log['state_events'] if e['type'] == 'state_summary')
    summary_input_chars = 0
    if index > 1:
        assert len(requests) == 1
        payload = requests[0]['event']['payload']
        summary_input = payload['messages'][1]['content']
        summary_input_chars = len(summary_input)
        assert payload['model'] == log['model_calls'][0]['request']['model']
        assert 'tools' not in payload
        assert summary_event['source_turns'] == list(range(1, index))
        assert summary_event['source_record_count'] == index-1
        assert summary_event['summary_mode'] == 'integrated_trajectory'
        assert summary_event['summary'] in main
        assert '## 공개 대화 히스토리' not in summary_input
        assert scenario['dialogue'][0]['content'] not in summary_input
        assert record['counselor_utterance'] not in summary_input
        for previous in records[:index-1]:
            assert previous['counselor_utterance'] in summary_input
            assert previous['inner_state']['interpretation'] in summary_input
            assert previous['inner_state']['response_basis'] in summary_input
            for state in previous['inner_state']['states'].values():
                assert state['reason'] in summary_input
            assert previous['utterance'] not in summary_input
    else:
        assert not requests and summary_event['summary'] is None
    result = {'turn': index, 'turn_id': record['turn_id'], 'counselor': record['counselor_utterance'],
              'status': log['status'], 'committed': committed,
              'analysis_only_removed_code_fence': not committed,
              'client': record['utterance'], 'feelings': record['feelings'],
              'state_before': {k: v['level'] for k, v in record['state_before'].items()},
              'state_after': {k: v['level'] for k, v in record['state_after'].items()},
              'inner_state': record['inner_state'], 'summary_before_generation': summary_event['summary'],
              'summary_source_turns': summary_event.get('source_turns', []),
              'summary_input_chars': summary_input_chars, 'summary_usage': summary_event.get('usage', {}),
              'main_usage': log.get('main_usage', log['usage']),
              'total_usage': log['usage'] if committed else {
                  key: value + summary_event['usage'].get(key, 0) for key, value in log['usage'].items()},
              'tool_calls_used': log.get('tool_calls_used', 0), 'log_path': str(session / (log['turn_id']+'.json'))}
    results.append(result)
assert len(records) == 4, len(records)
findings = {'session_id': session.name, 'generation_attempts': 5, 'committed_turns': 4, 'failed_attempts': 1, 'summary_calls': 4,
            'handoff_checks_passed': True, 'turns': results}
(ROOT / 'findings.json').write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding='utf-8')
lines = ['# 실제 생성 대화', '',
         '프로필 001 / 히스토리 1. 초기 4쌍 뒤에 이어진 생성 시도 5회. 1~4회는 정상 반영됐고, 5회는 JSON 코드블록 때문에 검증 실패했다. 5회 발화는 실패 원문에서 분석용으로만 추출했다.', '']
for result in results:
    lines.extend([f"## 생성 {result['turn']}턴", '', '상담사: '+result['counselor'], '',
                  '내담자: '+result['client'], '',
                  ('반영된 상태' if result['committed'] else '모델이 출력했으나 DB에 반영되지 않은 상태')
                  +' (신뢰 / 자기개방 / 변화 자신감): '+' / '.join(result['state_after'].values()), '',
                  '이 발화 전에 메인에게 전달한 통합 요약: '+(result['summary_before_generation'] or '없음 — 첫 생성'), ''])
(ROOT / 'dialogue.md').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps(findings, ensure_ascii=False, indent=2))
