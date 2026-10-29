"""Use the actual C4 implementation with an HTTP mock, including its handoffs."""
import copy
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_lab import fixture, close, post, Model
from runtime.paired_lab import public_client_response


class PublicSpeechTests(unittest.TestCase):
    def test_explicit_speech_sections_and_markdown(self):
        speech = '네, 맞아요.\n그래도 아직 조금 망설여져요.'
        for header in ('내담자 발화:', '- 실제 발화:', '**실제 발화:**', '- **내담자 발화**:',
                       '### 실제 내담자 발화', '3. __실제 발화__：'):
            with self.subTest(header=header):
                response = '- 느낌: 이해받음\n- 판단 이유: PRIVATE_BEFORE\n' + header + '\n' + speech + '\n\n### 판단 이유\nPRIVATE_AFTER'
                self.assertEqual(public_client_response(response), speech)
        self.assertEqual(public_client_response('- 실제 발화: 네, 맞아요.'), '네, 맞아요.')
        self.assertEqual(public_client_response('```text\n- 실제 발화: 네, 맞아요.\n```'), '네, 맞아요.')
        self.assertEqual(public_client_response(json.dumps({'utterance': speech, 'reason': 'PRIVATE'})), speech)

    def test_missing_empty_and_ambiguous_speech_rejected(self):
        for response in ('판단 이유: PRIVATE', '안녕하세요.', '- 실제 발화:',
                         '실제 발화: 첫 답변\n내담자 발화: 두 번째 답변', '{"reason":"PRIVATE"}'):
            with self.subTest(response=response), self.assertRaises(ValueError):
                public_client_response(response)


class PairedTests(unittest.TestCase):
    def setUp(self):
        self.server, self.model, self.temp = fixture()
        Model.requests.clear()
        Model.c4_fail = False
        Model.c4_gate = None
        Model.basic_label = '내담자 발화'
        Model.basic_counselor_finish = 'stop'
        Model.state_json_fence = False

    def tearDown(self):
        Model.c4_fail = False
        if Model.c4_gate:
            Model.c4_gate.set()
        Model.c4_gate = None
        Model.basic_label = '내담자 발화'
        Model.basic_counselor_finish = 'stop'
        Model.state_json_fence = False
        close(self.server, self.model, self.temp)

    def create(self, version='state_summary', content='편한 만큼 이야기해 주세요.', counselor='C4', counselor_prompt=None):
        config = json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}/api/config'))
        return json.loads(post(self.server, '/api/paired/create', {'scenario': config['scenario'],
                         'generation': config['generation'], 'system_prompt': '주어진 툴을 적절히 활용하세요.' if version == 'basic' else
                         json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}/api/versions'))['versions'][1]['system_prompt'],
                         'experiment_version': version, 'enabled_tools': [], 'profile_context_mode': 'selective',
                         'counselor_version': counselor,
                         **({'counselor_system_prompt': counselor_prompt} if counselor_prompt is not None else {}),
                         'messages': [{'role': 'user', 'content': content}]}))

    def step(self, session):
        events = [json.loads(line)['event'] for line in post(self.server, '/api/paired/step',
                  {'session_id': session['session_id'], 'expected_step': len(session['steps'])}).splitlines()]
        return events

    def stored(self, session):
        return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}/api/paired/session?session_id=' + session['session_id']))

    def test_manual_speech_remains_in_authoritative_history_and_does_not_invent_state(self):
        session = self.create(counselor='basic')
        initial = len(session['public_dialogue'])
        first = self.step(session)[-1]['session']
        before = len(Model.requests)
        manual = json.loads(post(self.server, '/api/paired/manual', {'session_id': first['session_id'],
                           'expected_step': len(first['steps']), 'actor': 'counselor', 'content': 'MANUAL_COUNSELOR_UNIQUE'}))
        self.assertEqual(len(Model.requests), before)
        current = manual['session']
        self.assertEqual(current['public_dialogue'][:-1], first['public_dialogue'])
        self.assertEqual(current['steps'][-1]['source'], 'manual')
        self.assertEqual(current['next_actor'], 'client')
        second = self.step(current)
        self.assertEqual(second[-1]['type'], 'done', second[-1])
        payload = next(e['payload'] for e in second if e['type'] == 'request')
        self.assertIn('MANUAL_COUNSELOR_UNIQUE', payload['messages'][1]['content'])
        self.assertIn(first['public_dialogue'][-1]['content'], payload['messages'][1]['content'])
        current = second[-1]['session']
        with self.assertRaises(HTTPError):
            post(self.server, '/api/paired/manual', {'session_id': current['session_id'],
                 'expected_step': len(current['steps']), 'actor': 'client', 'content': '잘못된 차례'})
        current = self.step(current)[-1]['session']
        human = json.loads(post(self.server, '/api/paired/manual', {'session_id': current['session_id'],
                           'expected_step': len(current['steps']), 'actor': 'client', 'content': 'MANUAL_CLIENT_UNIQUE'}))['session']
        counselor = self.step(human)
        self.assertEqual(counselor[-1]['type'], 'done', counselor[-1])
        self.assertIn('MANUAL_CLIENT_UNIQUE', next(e['payload'] for e in counselor if e['type'] == 'request')['messages'][1]['content'])
        self.assertEqual(len(counselor[-1]['session']['public_dialogue']), initial + 6)
        self.assertEqual(len(counselor[-1]['session']['attempts']), 4)

    def test_manual_first_counselor_can_override_seed_without_losing_initial_history(self):
        session = self.create(counselor='basic')
        session = json.loads(post(self.server, '/api/paired/manual', {'session_id': session['session_id'],
                'expected_step': 0, 'actor': 'counselor', 'content': '새로운 상담사 질문'}))['session']
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        self.assertEqual(events[-1]['session']['public_dialogue'][-2]['content'], '새로운 상담사 질문')

    def test_fenced_state_json_commits_public_speech_and_preserves_raw(self):
        Model.state_json_fence = True
        session = self.create(counselor='basic')
        before = len(session['public_dialogue'])
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        saved = self.stored(session)
        self.assertEqual(len(saved['public_dialogue']), before + 1)
        self.assertEqual(saved['public_dialogue'][-1]['content'], '조금 편해졌어요. 아직 자세히 말하기는 망설여져요.')
        self.assertNotIn('PRIVATE_DETAIL', saved['public_dialogue'][-1]['content'])
        record = next(e for e in events if e['type'] == 'client_record')
        self.assertTrue(record['model_response'].startswith('```json'))
        self.assertTrue(any(e['type'] == 'state_record' for e in events))

    def test_alternation_real_c4_private_boundary_and_restored_ledger(self):
        session = self.create()
        self.assertEqual(Model.requests, [])
        initial = len(session['public_dialogue'])
        for index in range(6):
            actor = 'client' if index % 2 == 0 else 'counselor'
            start = len(Model.requests)
            events = self.step(session)
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            session = events[-1]['session']
            self.assertEqual(session['steps'][-1]['actor'], actor)
            self.assertEqual(len(session['public_dialogue']), initial + index + 1)
            if actor == 'counselor':
                requests = Model.requests[start:]
                self.assertEqual(len(requests), 3)
                self.assertEqual([e['phase'] for e in events if e['type'] == 'request'],
                                 ['관찰·정보 갱신', '상담 행동 선택', '상담사 발화 생성'])
                for request in requests:
                    raw = json.dumps(request, ensure_ascii=False)
                    self.assertNotIn('PRIVATE_DETAIL', raw)
                    payload = json.loads(request['messages'][1]['content'])
                    for key in ('profile', 'states', 'taxonomy', 'inner_state', 'summary'):
                        self.assertNotIn(key, payload)
                observation = json.loads(requests[0]['messages'][1]['content'])
                selection = json.loads(requests[1]['messages'][1]['content'])
                execution = json.loads(requests[2]['messages'][1]['content'])
                self.assertEqual(selection['counseling_work_history'][-1]['actual_client_response'], observation['latest_client_utterance'])
                record = next(e['record'] for e in events if e['type'] == 'counselor_record')['e2e_counselor']
                self.assertEqual(execution['current_action'], record['selection'])
                self.assertEqual(execution['required_public_work'], record['selection']['instruction'])
                self.assertNotIn('action_catalog', selection)
                self.assertEqual(session['counselor_checkpoint']['turn_index'], (index+1)//2)
            else:
                request = next(e['payload'] for e in events if e['type'] == 'request')
                self.assertEqual([m['role'] for m in request['messages']], ['system', 'user'])
                self.assertNotIn('PRIVATE_DETAIL', request['messages'][1]['content'])
                if index:
                    self.assertTrue(any(e['type'] == 'state_summary' and e['summary'] for e in events))
            self.assertEqual(self.stored(session), session)
        # Execution journal can be reloaded without another model call.
        before = len(Model.requests)
        attempt = session['attempts'][-1]['turn_id']
        journal = json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}/api/paired/events?session_id={session["session_id"]}&attempt={attempt}'))
        self.assertTrue(any(e['type'] == 'counselor_record' for e in journal['events']))
        self.assertEqual(len(Model.requests), before)
        self.assertNotIn('PRIVATE_DETAIL', json.dumps(session['public_dialogue']))

    def test_basic_counselor_single_call_public_input_and_prompt_persistence(self):
        from runtime.paired_lab import BASIC_COUNSELOR_PROMPT
        prompt = BASIC_COUNSELOR_PROMPT.read_text() + '\n수동 편집한 상담사 지침'
        session = self.create(counselor='basic', counselor_prompt=prompt)
        for index in range(6):
            count = len(Model.requests)
            events = self.step(session)
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            session = events[-1]['session']
            self.assertEqual(session['counselor_version'], 'basic')
            if index % 2:
                self.assertEqual(len(Model.requests) - count, 1)
                request = Model.requests[-1]
                self.assertEqual(request['messages'][0]['content'], prompt)
                self.assertEqual([m['role'] for m in request['messages']], ['system', 'user'])
                public = request['messages'][1]['content']
                self.assertIn(session['public_dialogue'][-2]['content'], public)
                for marker in ('PRIVATE_DETAIL', 'inner_state', '## profile', '## states', 'task_board'):
                    self.assertNotIn(marker, public)
                self.assertNotIn('tools', request)
                self.assertEqual(len([e for e in events if e['type'] == 'request']), 1)
                self.assertFalse(any(e['type'] == 'counselor_record' for e in events))
                self.assertIsNone(session['counselor_checkpoint'])
                self.assertEqual(session['public_dialogue'][-1]['content'], '그 마음을 조금 더 들려주셔도 괜찮아요.')
                self.assertEqual(session['settings']['counselor_system_prompt'], prompt)

    def test_basic_counselor_truncation_and_invalid_configuration(self):
        session = self.step(self.create(counselor='basic'))[-1]['session']
        Model.basic_counselor_finish = 'length'
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'error')
        stored = self.stored(session)
        self.assertEqual(stored['steps'], session['steps'])
        self.assertEqual(stored['next_actor'], 'counselor')
        for choice, prompt in (('unknown', None), ('basic', ''), ('basic', 123)):
            with self.assertRaises(HTTPError):
                self.create(counselor=choice, counselor_prompt=prompt)

    def test_failed_c4_and_stale_click_do_not_advance(self):
        session = self.step(self.create())[-1]['session']
        before = copy.deepcopy(session)
        Model.c4_fail = True
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'error')
        saved = self.stored(session)
        for key in ('steps', 'public_dialogue', 'next_actor', 'counselor_checkpoint', 'client_storage_dir'):
            self.assertEqual(saved.get(key), before.get(key))
        with self.assertRaises(HTTPError):
            post(self.server, '/api/paired/step', {'session_id': session['session_id'], 'expected_step': 0})
        Model.c4_fail = False
        recovered = self.step(saved)
        self.assertEqual(recovered[-1]['type'], 'done', recovered[-1])
        self.assertEqual(recovered[-1]['session']['steps'][-1]['number'], 2)

    def test_basic_public_speech_and_fresh_prompt(self):
        Model.basic_label = '- 실제 발화'
        session = self.create('basic')
        for index in range(3):
            events = self.step(session)
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            session = events[-1]['session']
        public = session['public_dialogue'][-1]['content']
        self.assertNotIn('판단 이유:', public)
        self.assertNotIn('내담자 발화:', public)
        self.assertIn('감사해요', public)
        request = next(e['payload'] for e in events if e['type'] == 'request')
        self.assertEqual(len(request['messages']), 2)
        self.assertIn(session['public_dialogue'][-2]['content'], request['messages'][1]['content'])
        self.assertNotIn('판단 이유: 걱정을 알아주지만', request['messages'][1]['content'])

    def test_invalid_client_output_does_not_commit_private_state(self):
        session = self.create(content='잘못된 상태 출력 테스트')
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'error')
        saved = self.stored(session)
        self.assertEqual(saved['steps'], [])
        self.assertNotIn('client_storage_dir', saved)
        self.assertEqual(saved['next_actor'], 'client')

    def test_unknown_record_and_path_are_rejected(self):
        session = self.create()
        for route in ('/api/paired/session?session_id=../bad',
                      '/api/paired/events?session_id='+session['session_id']+'&attempt=../../bad'):
            with self.assertRaises(HTTPError):
                urlopen(f'http://127.0.0.1:{self.server.server_port}' + route)

    def test_cancel_and_concurrent_click_keep_public_checkpoint(self):
        session = self.step(self.create())[-1]['session']
        before = copy.deepcopy(session)
        Model.c4_gate = threading.Event()
        result = []
        request_id = 'cancel_c4_test'
        count = len(Model.requests)
        def worker():
            result.extend(json.loads(line)['event'] for line in post(self.server, '/api/paired/step',
                          {'session_id': session['session_id'], 'expected_step': 1, 'request_id': request_id}).splitlines())
        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.monotonic() + 5
        while len(Model.requests) == count and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertGreater(len(Model.requests), count)
        with self.assertRaises(HTTPError):
            post(self.server, '/api/paired/step', {'session_id': session['session_id'], 'expected_step': 1})
        reply = json.loads(post(self.server, '/api/cancel', {'request_id': request_id}))
        self.assertTrue(reply['cancelled'])
        Model.c4_gate.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[-1]['type'], 'cancelled', result[-1])
        saved = self.stored(session)
        for key in ('steps', 'public_dialogue', 'next_actor', 'counselor_checkpoint'):
            self.assertEqual(saved.get(key), before.get(key))


if __name__ == '__main__':
    unittest.main()
