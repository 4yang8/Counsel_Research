"""Integration tests for private/public boundaries and state persistence."""
import copy
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from test_lab import app, fixture, close, post, Model
import client.state_summary.runtime as state_summary


class StateSummaryTests(unittest.TestCase):
    def test_fenced_json_keeps_schema_and_finish_validation(self):
        scenario = app.runtime.load_scenario({})
        value = {'inner_state': {'states': {key: {'level': row['level'], 'reason': '현재 상태 유지'} for key, row in scenario['states'].items()},
                 'interpretation': '상담사의 말에 안도함', 'response_basis': '앞선 맥락을 참고'}, 'feelings': [], 'utterance': '조금 안심이 되네요.'}
        import json
        raw = json.dumps(value, ensure_ascii=False)
        for wrapper in ('{}', '```json\n{}\n```', '```\n{}\n```', '```JSON\r\n{}\r\n```'):
            with self.subTest(wrapper=wrapper):
                parsed, after = state_summary.parse_response(wrapper.format(raw), scenario, scenario['states'], 'stop')
                self.assertEqual(parsed, value)
                self.assertEqual(after, scenario['states'])
        for invalid in ('설명입니다.\n```json\n'+raw+'\n```', '```json\n'+raw+'\n```\n추가 설명', '```json\n{broken}\n```',
                        '```json\n{"utterance":"필드 누락"}\n```'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                state_summary.parse_response(invalid, scenario, scenario['states'], 'stop')
        with self.assertRaises(ValueError):
            state_summary.parse_response('```json\n'+raw+'\n```', scenario, scenario['states'], 'length')

    def test_shipped_config_defaults_to_latest_version(self):
        import json
        config = json.loads((app.HERE / 'config/app.json').read_text())
        self.assertEqual(app.experiment_versions.catalog(config)['default'], 'state_summary')

    def setUp(self):
        self.server, self.model, self.temp = fixture()
        Model.requests.clear()

    def tearDown(self):
        close(self.server, self.model, self.temp)

    def request(self, **overrides):
        body = {'experiment_version': 'state_summary', 'messages': [{'role': 'user', 'content': '편한 만큼 이야기해 주세요.'}],
                'enabled_tools': ['retrieve_inner_state']}
        body.update(overrides)
        return body

    def chat(self, body):
        return [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]

    def first(self):
        events = self.chat(self.request())
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        return events

    def test_registry_and_basic_preserved(self):
        versions = json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}/api/versions'))
        self.assertEqual(versions['default'], 'basic')
        self.assertEqual([v['id'] for v in versions['versions']], list(app.experiment_versions.NAMES))
        for version, expected in [('basic', False), ('state_summary', True)]:
            tools = json.loads(post(self.server, '/api/tools', {'experiment_version': version}))['tools']
            self.assertEqual(any(t['function']['name'] == 'retrieve_inner_state' for t in tools), expected)
            if expected:
                definition = next(t['function'] for t in tools if t['function']['name'] == 'retrieve_inner_state')
                self.assertEqual(set(definition['parameters']['required']), {'query', 'state', 'reason'})
                self.assertNotIn('turn', definition['parameters']['properties'])
        baseline = self.chat({'messages': [{'role': 'user', 'content': '기본 생성'}]})
        self.assertEqual(baseline[-1]['type'], 'done')
        self.assertIn('/basic/', baseline[0]['saved_path'])
        self.assertNotIn('inner_state', baseline[-1])
        self.assertEqual(Model.requests[-1]['messages'][0]['content'], '주어진 툴을 적절히 활용하세요.')

    def test_continuation_rejects_changed_input_scope(self):
        from urllib.error import HTTPError
        events = self.first()
        session = next(event['session_id'] for event in events if event['type'] == 'experiment_setup')
        count = len(Model.requests)
        for change in ({'profile_context_mode': 'full'}, {'input_blocks': {'profile': False}}):
            with self.assertRaises(HTTPError):
                post(self.server, '/api/preview', self.request(session_id=session, **change))
        self.assertEqual(len(Model.requests), count)

    def test_first_turn_saves_only_valid_record_and_public_history(self):
        preview = json.loads(post(self.server, '/api/preview', self.request()))['payload']
        self.assertIn('생성된 이전 내면 기록이 없습니다', preview['messages'][1]['content'])
        self.assertEqual(Model.requests, [])
        events = self.first()
        done = events[-1]
        self.assertEqual(len(Model.requests), 1)
        self.assertEqual(done['history'][-1]['content'], done['response'])
        self.assertNotIn('PRIVATE_DETAIL', json.dumps(done['history']))
        self.assertIn('PRIVATE_DETAIL', done['model_response'])
        record_event = next(e for e in events if e['type'] == 'state_record')
        saved = json.loads(Path(record_event['saved_path']).read_text())
        self.assertEqual(saved['turn'], 1)
        self.assertEqual(saved['state_after']['trust']['level'], '높음')
        log = json.loads(Path(events[0]['saved_path']).read_text())
        self.assertEqual(log['experiment_version'], 'state_summary')
        self.assertEqual(len(log['model_calls']), 1)
        self.assertEqual(log['model_response'], done['model_response'])

    def test_next_turn_summary_private_boundary_and_state_carry(self):
        first = self.first()
        Model.requests.clear()
        second = self.chat(self.request(session_id=first[0]['session_id'],
            messages=first[-1]['history'] + [{'role': 'user', 'content': '서두르지 않아도 괜찮아요.'}]))
        self.assertEqual(second[-1]['type'], 'done', second[-1])
        self.assertEqual(len(Model.requests), 2)
        summary, main = Model.requests
        self.assertEqual(summary['model'], main['model'])
        self.assertNotIn('tools', summary)
        self.assertIn('PRIVATE_DETAIL', summary['messages'][1]['content'])
        self.assertNotIn('## 공개 대화 히스토리', summary['messages'][1]['content'])
        self.assertNotIn(first[-1]['response'], summary['messages'][1]['content'])
        self.assertIn('편한 만큼 이야기해 주세요.', summary['messages'][1]['content'])
        self.assertNotIn('서두르지 않아도', summary['messages'][1]['content'])
        self.assertNotIn('PRIVATE_DETAIL', main['messages'][1]['content'])
        self.assertIn('속도 존중에 조금 안도', main['messages'][1]['content'])
        self.assertEqual(len(main['messages']), 2)
        self.assertNotIn('tool_calls', json.dumps(main['messages']))
        event = next(e for e in second if e['type'] == 'state_summary')
        self.assertEqual(event['state_before']['trust']['level'], '높음')
        self.assertEqual(event['source_turns'], [1])
        self.assertEqual(event['summary_mode'], 'integrated_trajectory')
        self.assertEqual(second[-1]['usage']['total_tokens'], 260)
        log = json.loads(Path(second[0]['saved_path']).read_text())
        self.assertEqual(len(log['model_calls']), 1)
        self.assertEqual(len(log['subagent_events']), 3)

    def test_selective_retrieval_and_no_history_leak_to_next_turn(self):
        first = self.first()
        second = self.chat(self.request(session_id=first[0]['session_id'],
            messages=first[-1]['history'] + [{'role': 'user', 'content': '요약 조회 테스트'}]))
        self.assertEqual(second[-1]['type'], 'done', second[-1])
        result = next(e for e in second if e['type'] == 'tool_result')
        self.assertEqual(result['name'], 'retrieve_inner_state')
        self.assertFalse(result['is_error'])
        record = result['result']['record']
        self.assertEqual(set(record['inner_state']['states']), {'trust'})
        self.assertEqual(record['turn'], 1)
        self.assertNotIn('tool_calls', json.dumps(second[-1]['history']))
        Model.requests.clear()
        third = self.chat(self.request(session_id=first[0]['session_id'],
            messages=second[-1]['history'] + [{'role': 'user', 'content': '다음 이야기'}]))
        self.assertEqual(third[-1]['type'], 'done', third[-1])
        summary_input = Model.requests[0]['messages'][1]['content']
        self.assertIn('편한 만큼 이야기해 주세요.', summary_input)
        self.assertIn('요약 조회 테스트', summary_input)
        self.assertNotIn('다음 이야기', summary_input)
        self.assertNotIn(first[-1]['response'], summary_input)
        summary_event = next(e for e in third if e['type'] == 'state_summary')
        self.assertEqual(summary_event['source_turns'], [1, 2])
        self.assertEqual(summary_event['source_record_count'], 2)
        self.assertNotIn('PRIVATE_DETAIL', Model.requests[-1]['messages'][1]['content'])
        self.assertTrue(all(m['role'] != 'tool' for m in Model.requests[-1]['messages']))
        self.assertEqual(next(e for e in third if e['type'] == 'state_record')['record']['turn'], 3)

    def test_invalid_and_truncated_output_not_committed(self):
        for text in ('잘못된 상태 출력 테스트', '토큰 한도 테스트'):
            before = len(Model.requests)
            events = self.chat(self.request(messages=[{'role': 'user', 'content': text}]))
            self.assertEqual(events[-1]['type'], 'error')
            self.assertFalse(any(e['type'] == 'state_record' for e in events))
            self.assertEqual(len(Model.requests), before + 1)
            log = json.loads(Path(events[0]['saved_path']).read_text())
            self.assertEqual(log['status'], 'failed')
            self.assertTrue(log['response'])
            self.assertFalse(list(Path(events[0]['saved_path']).parent.glob('inner_states/*.json')))

    def test_session_and_profile_boundaries(self):
        first = self.first()
        with self.assertRaises(HTTPError):
            post(self.server, '/api/preview', self.request(session_id='../other'))
        with self.assertRaises(HTTPError):
            post(self.server, '/api/preview', self.request(session_id=first[0]['session_id'], messages=[
                {'role': 'user', 'content': '편한 만큼 이야기해 주세요.'},
                {'role': 'assistant', 'content': '저장되지 않은 다른 발화'},
                {'role': 'user', 'content': '이어가기'}]))
        scenario = copy.deepcopy(app.runtime.load_scenario({}))
        scenario['profile']['background'] = '다른 사람'
        with self.assertRaises(HTTPError):
            post(self.server, '/api/preview', self.request(session_id=first[0]['session_id'], scenario=scenario,
                 messages=first[-1]['history'] + [{'role': 'user', 'content': '다른 인물로 이어가기'}]))
        fresh = self.first()
        self.assertNotEqual(first[0]['session_id'], fresh[0]['session_id'])
        self.assertEqual(next(e for e in fresh if e['type'] == 'state_record')['record']['turn'], 1)

    def test_current_utterance_only_rebuilds_history_and_separates_instructions(self):
        first = self.first()
        system = state_summary.experiment_versions.catalog({})['versions'][1]['system_prompt'] + '\n실험 지시: 필요한 내면 원문은 도구로 확인하세요.'
        current = '아직 조심스러운 부분이 있으신가요?'
        second = self.chat(self.request(session_id=first[0]['session_id'], system_prompt=system,
                            messages=[{'role': 'user', 'content': current}]))
        self.assertEqual(second[-1]['type'], 'done', second[-1])
        main = Model.requests[-1]['messages']
        self.assertEqual([m['role'] for m in main], ['system', 'user'])
        self.assertEqual(main[0]['content'], system)
        self.assertIn('상담사: 편한 만큼 이야기해 주세요.', main[1]['content'])
        self.assertIn('내담자: ' + first[-1]['response'], main[1]['content'])
        self.assertTrue(main[1]['content'].endswith('[이번 상담자 발화]\n' + current))
        self.assertNotIn('실험 지시:', main[1]['content'])
        self.assertNotIn('PRIVATE_DETAIL', main[1]['content'])
        self.assertEqual(second[-1]['history'][:-2], first[-1]['history'])
        self.assertEqual(next(e for e in second if e['type'] == 'state_record')['record']['counselor_utterance'], current)
        summary = Model.requests[-2]['messages'][1]['content']
        for label in ('상담사 발화', '받아들인 의미', '내면상태와 설정 근거', '반응을 구성한 이유'):
            self.assertIn(label, summary)
        self.assertNotIn('turn_id', summary)

    def test_semantic_lookup_selects_original_record_and_rejects_unknown_id(self):
        first = self.first()
        second = self.chat(self.request(session_id=first[0]['session_id'],
                           messages=[{'role': 'user', 'content': '서두르지 않아도 괜찮아요.'}]))
        saved = state_summary.records({'_storage_dir': str(Path(self.temp.name)/'sessions')}, first[0]['session_id'])
        ctx = {'records': saved}
        query = '처음 상담사가 편한 만큼 말해도 된다고 했을 때 안도하면서 망설인 이유'
        database = state_summary.prepare_retrieval(ctx, {'query': query, 'state': 'trust', 'reason': '이전 반응 근거 확인'}, app.runtime.markdown_data)
        self.assertIn(saved[0]['counselor_utterance'], database['user'])
        self.assertIn(saved[1]['counselor_utterance'], database['user'])
        result = state_summary.retrieval_result(database, json.dumps({'record_id': saved[0]['turn_id']}), 'lookup_mock')
        self.assertEqual(result['record']['turn'], 1)
        self.assertEqual(result['record']['inner_state']['states']['trust'], saved[0]['inner_state']['states']['trust'])
        self.assertEqual(set(result['record']['inner_state']['states']), {'trust'})
        self.assertEqual(ctx['records'], saved)
        miss = state_summary.retrieval_result(database, '{"record_id":null}', 'lookup_mock')
        self.assertEqual(miss['status'], 'no_match')
        self.assertIsNone(miss['record'])
        with self.assertRaises(ValueError):
            state_summary.retrieval_result(database, '{"record_id":"not_saved"}', 'lookup_mock')
        with self.assertRaises(ValueError):
            state_summary.prepare_retrieval(ctx, {'query': '', 'state': 'trust', 'reason': '확인'}, app.runtime.markdown_data)

    def test_semantic_lookup_runs_agent_and_tool_result_is_in_same_turn_only(self):
        first = self.first()
        second = self.chat(self.request(session_id=first[0]['session_id'],
                          messages=[{'role': 'user', 'content': '요약 조회 테스트'}]))
        self.assertEqual(second[-1]['type'], 'done', second[-1])
        lookup = next(e for e in second if e['type'] == 'agent_event'
                      and e['agent_name'] == '내면 반응 조회 담당' and e['event']['type'] == 'request')
        self.assertEqual(lookup['event']['payload']['model'], Model.requests[-1]['model'])
        self.assertNotIn('tools', lookup['event']['payload'])
        tool = next(e for e in second if e['type'] == 'tool_result')
        parent_tool = next(m for m in Model.requests[-1]['messages'] if m['role'] == 'tool')
        self.assertEqual(json.loads(parent_tool['content']), tool['result'])
        self.assertNotIn('PRIVATE_DETAIL', json.dumps(second[-1]['history']))
        third = self.chat(self.request(session_id=first[0]['session_id'],
                         messages=[{'role': 'user', 'content': '지금은 어떤 마음이세요?'}]))
        self.assertEqual(third[-1]['type'], 'done', third[-1])
        self.assertEqual([m['role'] for m in Model.requests[-1]['messages']], ['system', 'user'])
        self.assertNotIn('PRIVATE_DETAIL', Model.requests[-1]['messages'][1]['content'])


if __name__ == '__main__':
    unittest.main()
