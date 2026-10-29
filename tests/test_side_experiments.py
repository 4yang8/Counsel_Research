"""Side-only recovery must never execute or replay an incomplete write."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from side_experiments.tool_loop import run_turn
from side_experiments.side_log import log_factory
from app.server import TurnLog


class RecoveryTests(unittest.TestCase):
    def execute(self, outputs):
        requests, calls, events = [], [], []

        @contextmanager
        def upstream(config, route, payload):
            requests.append(copy.deepcopy(payload))
            delta, finish = outputs[len(requests) - 1]
            yield [json.dumps({'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
                               'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}), '[DONE]']

        class Tools:
            opts = {'max_calls': 8, 'max_result_chars': 16000}

            def call(self, name, arguments):
                calls.append((name, arguments))
                return {'content': [{'type': 'text', 'text': 'saved'}]}

        payload = {'messages': [{'role': 'user', 'content': 'input'}], 'max_tokens': 1024, 'seed': 1}
        run_turn({'inspect_prompt': False}, payload, Tools(), upstream, iter, None,
                 lambda kind, **data: events.append({'type': kind, **data}), lambda: False, payload['messages'])
        return requests, calls, events

    def tool(self, arguments):
        return {'tool_calls': [{'index': 0, 'id': 'call1', 'type': 'function',
                                'function': {'name': 'write', 'arguments': arguments}}]}, 'tool_calls'

    def test_truncated_call_retries_clean_input_and_only_executes_complete_write(self):
        requests, calls, events = self.execute([
            self.tool('{"note":'), self.tool('{"note":"complete"}'), ({'content': 'final'}, 'stop')])
        self.assertEqual(calls, [('write', {'note': 'complete'})])
        self.assertEqual(requests[1]['messages'], requests[0]['messages'])
        self.assertEqual(requests[1]['max_tokens'], 2048)
        self.assertEqual(len([e for e in events if e['type'] == 'tool_start']), 1)
        self.assertEqual(len([e for e in events if e['type'] == 'generation_retry']), 1)

    def test_permanent_malformed_call_stops_without_executing(self):
        with self.assertRaisesRegex(ValueError, '파일 변경 없이 중단'):
            self.execute([self.tool('{')] * 3)

    def test_text_length_retries_before_finalizing(self):
        requests, calls, events = self.execute([({'content': 'partial'}, 'length'), ({'content': 'complete'}, 'stop')])
        self.assertEqual(calls, [])
        self.assertEqual([e['response'] for e in events if e['type'] == 'done'], ['complete'])

    def test_unparsed_tool_markup_is_not_public_speech(self):
        with self.assertRaisesRegex(ValueError, '공개 발화로 저장하지'):
            self.execute([({'content': '<tool_call>not a public reply</tool_call>'}, 'stop')])


class LoggingTests(unittest.TestCase):
    def test_all_chunks_persist_and_final_state_matches_original(self):
        with tempfile.TemporaryDirectory() as directory:
            ordinary = TurnLog(Path(directory) / 'ordinary', {'session_id': 'pair'})
            experimental = log_factory(TurnLog)(Path(directory) / 'experimental', {'session_id': 'pair'})
            events = [{'type': 'request', 'iteration': 1, 'payload': {'messages': [{'role': 'user', 'content': 'hello'}]}}]
            events += [{'type': 'chunk', 'data': {'choices': [{'index': 0, 'delta': {'reasoning_content': 'think'}}]}}]
            events += [{'type': 'chunk', 'data': {'choices': [{'index': 0, 'delta': {'content': str(i)}}]}} for i in range(20)]
            events += [{'type': 'done', 'response': 'answer', 'finish_reason': 'stop'}]
            for event in events:
                ordinary.append(copy.deepcopy(event))
                experimental.append(copy.deepcopy(event))
            def clean(value):
                if isinstance(value, dict):
                    return {k: clean(v) for k, v in value.items() if k not in ('saved_at', 'started_at', 'updated_at', 'finished_at', 'turn_id')}
                if isinstance(value, list):
                    return [clean(v) for v in value]
                return value
            self.assertEqual(clean(json.loads(ordinary.path.read_text())), clean(json.loads(experimental.path.read_text())))
            self.assertEqual(len(experimental.journal.read_text().splitlines()), len(events))
            self.assertTrue(experimental.terminal)


class WholeNoteTests(unittest.TestCase):
    def test_whole_note_submission_preserves_both_targets_and_private_client_boundary(self):
        from test_lab import fixture, close, post, Model
        from urllib.request import urlopen
        server, model, temp = fixture()
        try:
            root = 'http://127.0.0.1:' + str(server.server_port)
            with urlopen(root + '/api/config') as response:
                config = json.load(response)
            body = {'scenario': config['scenario'], 'generation': config['generation'],
                    'experiment_version': 'Q1_s1', 'counselor_version': 'P3a_s1',
                    'speaker_modes': {'client': 'manual', 'counselor': 'model'}, 'side_note_mode': 'replace',
                    'messages': [{'role': 'user', 'content': '오늘은 어떤 이야기를 할까요?'}]}
            body['scenario']['profile']['background'] = 'PRIVATE_BACKGROUND_NOT_PUBLIC'
            body['side_guide_context'] = 'source_only'
            session = json.loads(post(server, '/api/paired/create', body))
            session = json.loads(post(server, '/api/paired/manual', {'session_id': session['session_id'],
                'expected_step': 0, 'actor': 'client', 'content': 'PUBLIC_CLIENT_QUOTE: 준비 부족이 걱정돼요.'}))['session']
            Model.requests.clear()
            Model.p3a_actions = [
                {'name': 'update_notes', 'arguments': {'target': 'client', 'content': '## 기본 정보와 배경\n## 현재 고민과 관련 경험\n- 준비 부족이 걱정됨\n## 원하는 도움과 요청·경계'}},
                {'name': 'consult_counseling_guide', 'arguments': {'guide': 'common'}},
                {'name': 'update_notes', 'arguments': {'target': 'counselor', 'content': '## 현재 이해\n- 준비 부족 걱정\n## 현재 접근\n- Common\n## 진행 상황\n## 다음 대응\n- 원하는 도움 확인'}}]
            events = [json.loads(line)['event'] for line in post(server, '/api/paired/step',
                {'session_id': session['session_id'], 'expected_step': 1}).splitlines()]
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            self.assertFalse(any(e.get('is_error') for e in events if e['type'] == 'tool_result'))
            folder = server.storage_dir / 'paired' / session['session_id']
            self.assertIn('준비 부족이 걱정됨', (folder / 'notes.md').read_text())
            self.assertIn('원하는 도움 확인', (folder / 'counselor_notes.md').read_text())
            tool = next(t['function'] for t in Model.requests[0]['tools'] if t['function']['name'] == 'update_notes')
            self.assertEqual(tool['parameters']['required'], ['target', 'content'])
            guide = next(t['function'] for t in Model.requests[0]['tools'] if t['function']['name'] == 'consult_counseling_guide')
            self.assertEqual(guide['parameters']['required'], ['guide'])
            self.assertNotIn('request', guide['parameters']['properties'])
            self.assertNotIn('stage_client_update', json.dumps(Model.requests, ensure_ascii=False))
            child = next(r for r in Model.requests if not r.get('tools') and '# 실제 공개 대화' in r['messages'][-1]['content'])
            self.assertIn('PUBLIC_CLIENT_QUOTE', child['messages'][-1]['content'])
            self.assertNotIn('PRIVATE_BACKGROUND_NOT_PUBLIC', json.dumps(child))
            self.assertIn('# 메인의 질문과 작업 가설', child['messages'][-1]['content'])
        finally:
            Model.p3a_actions = None
            close(server, model, temp)


class TraitBoundaryTests(unittest.TestCase):
    def test_semantic_units_preserve_belief_context_and_original_values(self):
        from client.Q1_s1.runtime import split_profile
        profile = {'background': 'basic', 'core_belief': {'young_domain': 'domain',
                   'core_belief_statement': 'statement', 'schema_definition': 'meaning'},
                   'coping_strategies': ['first', 'second'], 'childhood_data': 'not provided'}
        basic, traits = split_profile(profile, semantic=True)
        self.assertEqual(basic, {'background': 'basic'})
        self.assertEqual([t['trait_id'] for t in traits], ['/core_belief', '/coping_strategies/0', '/coping_strategies/1'])
        self.assertEqual(traits[0]['content'], profile['core_belief'])
        self.assertNotIn('not provided', json.dumps(traits))

    def test_router_reason_can_remain_in_logs_without_entering_main_context(self):
        from client.Q1_s1.runtime import payload_for
        from types import SimpleNamespace
        selected = [{'trait_id': '/traits/0', 'content': 'ORIGINAL_TRAIT', 'reason': 'PRIVATE_ROUTER_HYPOTHESIS'}]
        ctx = {'basic': {}, 'dialogue': 'PUBLIC', 'current': 'CURRENT',
               'note': {'content': 'INNER'}, 'blocks': {'system': True}}
        output = payload_for({}, {'side_router_reason': 'log_only'}, ctx, selected,
            SimpleNamespace(markdown_data=lambda data: json.dumps(data)), lambda cfg, body, tools, preview: body)
        text = output['messages'][0]['content']
        self.assertIn('ORIGINAL_TRAIT', text)
        self.assertNotIn('PRIVATE_ROUTER_HYPOTHESIS', text)
        self.assertEqual(selected[0]['reason'], 'PRIVATE_ROUTER_HYPOTHESIS')

    def test_promoted_q1_s2_http_defaults_and_actual_main_boundary(self):
        from test_lab import fixture, close, post, Model
        from urllib.request import urlopen
        from client.Q1_s2.runtime import specification
        server, model, temp = fixture()
        try:
            with urlopen('http://127.0.0.1:' + str(server.server_port) + '/api/config') as response:
                config = json.load(response)
            scenario = config['scenario']
            scenario.pop('dialogue_turns', None)
            scenario['dialogue'] = []
            scenario['dialogue_format'] = 'messages'
            scenario['profile'] = {'background': 'PUBLIC_BASIC', 'core_belief': {
                'young_domain': 'DOMAIN', 'core_belief_statement': 'BELIEF_STATEMENT', 'schema_definition': 'BELIEF_MEANING'},
                'coping_strategies': ['UNSELECTED_SECRET']}
            Model.q1_router_response = json.dumps({'selections': [{'trait_id': '/core_belief', 'reason': 'DIAGNOSTIC_ONLY'}]})
            Model.requests.clear()
            session = json.loads(post(server, '/api/paired/create', {'scenario': scenario,
                'experiment_version': 'Q1_s2', 'counselor_version': 'basic',
                'system_prompt': specification()['system_prompt'], 'generation': config['generation'],
                'messages': [{'role': 'user', 'content': '편한 만큼 말씀해 주세요.'}]}))
            self.assertEqual(session['settings']['client_reasoning_budgets'], {'main': 512, 'subagent': 512})
            events = [json.loads(line)['event'] for line in post(server, '/api/paired/step', {
                'session_id': session['session_id'], 'expected_step': 0}).splitlines()]
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            router, main, resumed = Model.requests
            self.assertTrue(all(r['thinking_token_budget'] == 512 for r in Model.requests))
            self.assertIn('UNSELECTED_SECRET', router['messages'][1]['content'])
            text = main['messages'][1]['content']
            for value in ('BELIEF_STATEMENT', 'BELIEF_MEANING'):
                self.assertIn(value, text)
            for value in ('DIAGNOSTIC_ONLY', 'UNSELECTED_SECRET'):
                self.assertNotIn(value, text)
            self.assertEqual(resumed['messages'][-2]['reasoning'], 'Q1_MAIN_REASON')
            stored = Path(events[-1]['session']['client_storage_dir']) / 'Q1_s2' / session['session_id']
            self.assertTrue((stored / 'inner_notes.md').exists())
        finally:
            Model.q1_router_response = None
            close(server, model, temp)


if __name__ == '__main__':
    unittest.main()
