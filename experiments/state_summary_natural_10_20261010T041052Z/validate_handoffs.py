"""Check boundaries against actual payloads, including failed attempts."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
LAB = ROOT.parents[1]
sys.path.insert(0, str(LAB))
import app_server as app

index = json.loads((ROOT/'analysis_index.json').read_text(encoding='utf-8'))
assert len(index) == 10, len(index)
assert len({row['session_id'] for row in index}) == 1
scenario = json.loads((ROOT/'scenario.json').read_text(encoding='utf-8'))
manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
for name, digest in manifest['source_sha256'].items():
    assert hashlib.sha256((LAB/name).read_bytes()).hexdigest() == digest, name
records, history, result = [], [], []
tool_result_messages = 0
for row in index:
    attempt = row['attempt']
    body = json.loads((ROOT/f'input_{attempt}.json').read_text(encoding='utf-8'))
    log = json.loads(Path(row['log_path']).read_text(encoding='utf-8'))
    events = [json.loads(line) for line in (ROOT/f'events_{attempt}.jsonl').read_text(encoding='utf-8').splitlines()]
    assert body['scenario'] == scenario
    assert len(body['messages']) == 1 and body['messages'][0]['role'] == 'user'
    default_system = (ROOT/'prompts/system.md').read_text(encoding='utf-8')
    directive = row['probe'].get('system_directive', '')
    assert body['system_prompt'] == default_system + ('\n\n## 이번 생성의 실험 지시\n' + directive if directive else '')
    assert ('system_directive' in row['probe']) == (attempt >= 9)
    assert body['summary_system_prompt'] == (ROOT/'prompts/summary.md').read_text(encoding='utf-8')
    expected_states = records[-1]['state_after'] if records else scenario['states']
    assert row['state_before'] == expected_states
    expected_turns = [record['turn'] for record in records]
    assert row['summary_source_turns'] == expected_turns
    summary_requests = [event['event']['payload'] for event in events if event['type']=='agent_event'
                        and event.get('agent_kind')=='summary' and event['event']['type']=='request']
    main_payload = log['model_calls'][0]['request']
    assert main_payload['messages'][0]['content'] == body['system_prompt']
    counselor = body['messages'][0]['content']
    assert all(name not in counselor for name in body['enabled_tools'])
    assert all(word not in counselor for word in ('실습', '시스템', 'DB', '조회', '생성 턴'))
    assert {tool['function']['name'] for tool in main_payload['tools']} == set(body['enabled_tools'])
    assert main_payload['temperature'] == 0.7 and main_payload['max_tokens'] == 1024
    main_input = main_payload['messages'][1]['content']
    assert all(record['utterance'] in main_input for record in records)
    assert all(message['content'] in main_input for message in scenario['dialogue'])
    assert 'childhood_data' not in main_input and 'physiological_response' not in main_input
    if records:
        assert len(summary_requests) == 1
        projection = [app.runtime.state_summary.reaction_event(record) for record in records]
        expected_input = app.runtime.markdown_data({'내면상태 항목': {key: row['name'] for key, row in expected_states.items()}, '누적 내면 반응 기록': projection})
        assert summary_requests[0]['messages'][1]['content'] == expected_input
        assert 'tools' not in summary_requests[0]
        assert row['summary'] in main_input
        assert body['messages'][-1]['content'] not in expected_input
    else:
        assert not summary_requests
    # Confirm returned objects actually become role=tool messages in the following model call.
    for tool in row['tools']:
        seen = [message for call in log['model_calls'] for message in call['request']['messages']
                if message['role']=='tool' and message['tool_call_id']==tool['call_id']]
        assert seen, tool['name']
        assert all(json.loads(message['content']) == tool['result'] for message in seen)
        assert not tool['is_error']
        if tool['name']=='retrieve_inner_state':
            args = json.loads(tool['arguments'])
            assert 'query' in args and 'turn' not in args
            assert tool['result']['record']['turn_id'] in {record['turn_id'] for record in records}
            assert set(tool['result']['record']['inner_state']['states']) == (set(expected_states) if args['state']=='all' else {args['state']})
        tool_result_messages += 1
    record = next((event['record'] for event in events if event['type']=='state_record'), None)
    if record:
        assert record['turn'] == len(records)+1
        assert record['state_before'] == expected_states
        records.append(record)
        history += [{'role':'user','content':record['counselor_utterance']},
                    {'role':'assistant','content':record['utterance']}]
        assert history == log['history']
    else:
        assert log['status']=='failed' and row['error']
    result.append({'attempt':attempt, 'summary_source_turns':expected_turns,
                   'committed':bool(record), 'tool_calls':len(row['tools']), 'checks_passed':True})
output = {'session_id':index[0]['session_id'], 'attempts':10, 'committed_turns':len(records),
          'all_checks_passed':True, 'verified_tool_result_messages':tool_result_messages,
          'attempts_checked':result}
(ROOT/'handoff_checks.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(output,ensure_ascii=False,indent=2))
