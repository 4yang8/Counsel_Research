"""P3a transport and context boundaries; no real inference or clinical grading."""
import copy
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import test_p1
import test_p3
from test_lab import Model, close, fixture, post
from counselor.P1.runtime import read
from counselor.P3 import runtime as p3
from counselor.P3a import runtime as p3a


class P3aTests(unittest.TestCase):
    get = test_p1.P1Tests.get
    manual_client = test_p1.P1Tests.manual_client
    step = test_p1.P1Tests.step

    def setUp(self):
        self.server, self.model, self.temp = fixture()
        Model.requests.clear()
        Model.p2_actions = Model.p3_actions = Model.p3a_actions = None
        Model.p3a_child_response = '## CBT-07 · 생각의 근거와 대안\n전달된 상황에서 사실과 해석을 함께 구분하세요.'
        Model.p3a_child_finish = Model.dual_notes_finish = 'stop'
        Model.p1_delay = 0

    def tearDown(self):
        Model.p2_actions = Model.p3_actions = Model.p3a_actions = None
        Model.p3a_child_finish = Model.dual_notes_finish = 'stop'
        Model.p1_delay = 0
        close(self.server, self.model, self.temp)

    def create(self, **settings):
        return test_p3.P3Tests.create(self, version='P3a', **settings)

    def directory(self, session):
        return self.server.storage_dir / 'paired' / session['session_id']

    def action(self, guide='cbt', request='공개 사실과 평가에 대한 예상을 어떻게 함께 검토할까요?'):
        return {'name': 'consult_counseling_guide', 'arguments': {'guide': guide, 'request': request}}

    def test_catalog_and_note_templates_preserve_p3(self):
        specs = {row['id']: row for row in self.get('/api/paired/counselor')['versions']}
        self.assertEqual(specs['P3'], p3.specification())
        self.assertEqual(specs['P3a']['view'], 'dual_notes')
        self.assertEqual(specs['P3a']['tools'][0], p3.TOOL)
        self.assertEqual([t['function']['name'] for t in specs['P3a']['tools']],
                         ['update_notes', 'consult_counseling_guide'])
        session = self.create()
        for target, path in p3.NOTE_TEMPLATES.items():
            actual = read(self.directory(session) / p3.NOTES[target][0])
            # Empty sections are rendered with a placeholder; preserve their headings.
            self.assertEqual(actual.replace('(내용 없음)\n', ''), path.read_text())
        changed = p3a.specification()
        changed['tools'][1]['function']['parameters']['properties']['guide']['enum'].append('other')
        self.assertEqual(p3a.GUIDE_TOOL['function']['parameters']['properties']['guide']['enum'], list(p3a.GUIDE_NAMES))

    def test_consultation_context_model_settings_parent_link_and_saved_stream(self):
        session = self.manual_client(self.create(), 'PUBLIC_NOT_IN_REQUEST: 複雑な公開発話')
        request = '정정 메일을 보내기 망설이는 상황입니다.\n현재의 양가감정을 어떻게 다룰까요?'
        Model.p3a_actions = [self.action('mi', request)]
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        self.assertEqual(len(Model.requests), 3)
        main, child, resumed = Model.requests
        self.assertEqual([m['role'] for m in child['messages']], ['system', 'user'])
        self.assertNotIn('tools', child)
        self.assertNotIn('PUBLIC_NOT_IN_REQUEST', json.dumps(child, ensure_ascii=False))
        self.assertIn(request, child['messages'][1]['content'])
        self.assertNotIn('## 내담자 노트 · 현재 파일 원문', child['messages'][1]['content'])
        for path in p3a.GUIDE_FILES.values():
            self.assertIn(path.read_text(encoding='utf-8'), child['messages'][1]['content'])
            self.assertNotIn(path.read_text(encoding='utf-8'), main['messages'][1]['content'])
        for key in ('model', 'temperature', 'top_p', 'max_tokens', 'chat_template_kwargs'):
            self.assertEqual(child[key], main[key])
        tool = next(e for e in events if e['type'] == 'tool_start')
        children = [e for e in events if e['type'] == 'agent_event']
        self.assertTrue(children)
        self.assertTrue(all(e['parent_agent_id'] == 'main' and e['parent_call_id'] == tool['call_id'] for e in children))
        self.assertTrue(any(e['event']['type'] == 'chunk' and 'P3A_CHILD_REASON' in json.dumps(e) for e in children))
        child_request = next(e['event'] for e in children if e['event']['type'] == 'request')
        self.assertEqual(len(child_request['guide_sources']), 4)
        result = next(e for e in events if e['type'] == 'tool_result')
        self.assertEqual(resumed['messages'][-1]['content'], result['model_content'])
        self.assertEqual(json.loads(result['model_content'])['content'][0]['text'], Model.p3a_child_response)
        self.assertEqual(resumed['messages'][-2]['reasoning'], 'P3a_REASON_0')
        self.assertNotIn('P3A_CHILD_REASON', json.dumps(resumed, ensure_ascii=False))
        self.assertEqual(events[-1]['usage'], {'prompt_tokens': 300, 'completion_tokens': 90, 'total_tokens': 390})
        saved = json.loads(Path(events[-1]['step']['saved_path']).read_text())
        self.assertTrue(saved['subagent_events'])
        self.assertEqual(len(saved['model_calls']), 2)
        replay = self.get('/api/paired/events?session_id=' + session['session_id'] + '&attempt=' + saved['turn_id'])
        self.assertTrue(any(e['type'] == 'agent_event' for e in replay['events']))
        self.assertNotIn('CBT-07', json.dumps(events[-1]['session']['public_dialogue'], ensure_ascii=False))

    def test_multiple_consultations_and_note_write_keep_main_reasoning(self):
        session = self.manual_client(self.create(), '必要な作業を確認します。')
        note = read(self.directory(session) / 'counselor_notes.md')
        Model.p3a_actions = [self.action('routing'), self.action('common'),
                            {'name': 'update_notes', 'arguments': {'target': 'counselor', 'edits': [
                                {'old_text': note, 'new_text': note + '\n현재는 요청한 도움을 함께 확인한다.'}]}}]
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        calls = [e['payload'] for e in events if e['type'] == 'request']
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(p['messages'][1] == calls[0]['messages'][1] for p in calls))
        self.assertEqual([m['reasoning'] for m in calls[-1]['messages'] if m.get('tool_calls')],
                         ['P3a_REASON_0', 'P3a_REASON_1', 'P3a_REASON_2'])
        children = [e for e in events if e['type'] == 'agent_event' and e['event']['type'] == 'request']
        self.assertEqual(len({e['agent_id'] for e in children}), 2)
        self.assertEqual(len({e['parent_call_id'] for e in children}), 2)
        self.assertIn('요청한 도움', read(self.directory(session) / 'counselor_notes.md'))
        self.assertEqual(events[-1]['usage']['total_tokens'], 780)

    def test_no_input_or_result_truncation_and_files_reload(self):
        path = self.server.storage_dir / 'detail.md'
        original = '# 긴 상세 가이드\n' + 'LONG_CURRENT_SOURCE\n' * 2000
        path.write_text(original)
        Model.p3a_child_response = '선별 결과\n' * 4000
        Model.p3a_actions = [self.action()]
        with patch.dict(p3a.GUIDE_FILES, {'common': path}):
            session = self.manual_client(self.create(), '긴 자료와 결과를 확인해요.')
            events = self.step(session)
            child = next(e['event']['payload'] for e in events if e['type'] == 'agent_event' and e['event']['type'] == 'request')
            self.assertIn(original, child['messages'][1]['content'])
            result = next(e for e in events if e['type'] == 'tool_result')
            self.assertFalse(result['truncated'])
            self.assertGreater(len(result['model_content']), 16000)
            self.assertEqual(json.loads(result['model_content'])['content'][0]['text'], Model.p3a_child_response)
            path.write_text('# 변경한 가이드\nRELOADED_SOURCE')
            session = self.manual_client(events[-1]['session'], '다음 턴도 확인해요.')
            following = self.step(session)
            child = next(e['event']['payload'] for e in following if e['type'] == 'agent_event' and e['event']['type'] == 'request')
            self.assertIn('RELOADED_SOURCE', child['messages'][1]['content'])
            self.assertNotIn('LONG_CURRENT_SOURCE', child['messages'][1]['content'])
            main = next(e['payload'] for e in following if e['type'] == 'request')
            self.assertEqual([m['role'] for m in main['messages']], ['system', 'user'])
            self.assertNotIn('P3a_REASON', json.dumps(main))
            self.assertNotIn(Model.p3a_child_response, json.dumps(main, ensure_ascii=False))

    def test_invalid_arguments_are_tool_errors_without_child_inference(self):
        for arguments in ({}, {'guide': [] , 'request': '질문'}, {'guide': '../secret', 'request': '질문'},
                          {'guide': 'common', 'request': ''}, {'guide': 'common', 'request': '질문', 'path': '/tmp/x'}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                p3a.guidance_input(arguments)
        session = self.manual_client(self.create(), '잘못된 인자를 복구해요.')
        Model.p3a_actions = [{'name': 'consult_counseling_guide', 'arguments': {'guide': 'unknown', 'request': '질문'}}]
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertTrue(next(e for e in events if e['type'] == 'tool_result')['is_error'])
        self.assertFalse(any(e['type'] == 'agent_event' for e in events))
        self.assertEqual(len(Model.requests), 2)

    def test_partial_child_output_is_not_accepted_as_guidance(self):
        Model.p3a_child_finish = 'length'
        Model.p3a_child_response = 'PARTIAL_GUIDANCE'
        Model.p3a_actions = [self.action()]
        session = self.manual_client(self.create(), '응답이 잘리는 상황을 확인해요.')
        events = self.step(session)
        result = next(e for e in events if e['type'] == 'tool_result')
        self.assertTrue(result['is_error'])
        self.assertNotIn('PARTIAL_GUIDANCE', result['model_content'])
        child_events = [e['event'] for e in events if e['type'] == 'agent_event']
        self.assertEqual(child_events[-1]['type'], 'error')
        self.assertFalse(any(e['type'] == 'done' for e in child_events))
        self.assertEqual(events[-1]['usage']['total_tokens'], 390)

    def test_missing_guide_fails_explicitly_without_overwriting_notes(self):
        with patch.dict(p3a.GUIDE_FILES, {'mi': self.server.storage_dir / 'missing.md'}):
            Model.p3a_actions = [self.action('common')]
            session = self.manual_client(self.create(), '자료 누락을 확인해요.')
            before = read(self.directory(session) / 'notes.md')
            events = self.step(session)
            result = next(e for e in events if e['type'] == 'tool_result')
            self.assertTrue(result['is_error'])
            self.assertIn('상세 가이드 파일을 읽지 못했습니다: mi', result['model_content'])
            self.assertFalse(any(e['type'] == 'agent_event' for e in events))
            self.assertEqual(read(self.directory(session) / 'notes.md'), before)

    def test_cancel_during_child_preserves_public_dialogue(self):
        Model.p1_delay = .25
        Model.p3a_actions = [self.action()]
        session = self.manual_client(self.create(), '실행 중 중단해요.')
        received = []
        request_id = 'cancel_p3a_child'

        def generate():
            raw = post(self.server, '/api/paired/step', {'session_id': session['session_id'],
                       'expected_step': len(session['steps']), 'request_id': request_id})
            received.extend(json.loads(line)['event'] for line in raw.splitlines())

        worker = threading.Thread(target=generate)
        worker.start()
        deadline = time.monotonic() + 5
        while len(Model.requests) < 2 and time.monotonic() < deadline:
            threading.Event().wait(.01)
        self.assertGreaterEqual(len(Model.requests), 2)
        self.assertTrue(json.loads(post(self.server, '/api/cancel', {'request_id': request_id}))['cancelled'])
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(received[-1]['type'], 'cancelled')
        self.assertTrue(any(e['type'] == 'agent_event' and e['event']['type'] == 'cancelled' for e in received))
        restored = self.get('/api/paired/session?session_id=' + session['session_id'])
        self.assertEqual(restored['public_dialogue'], session['public_dialogue'])
        self.assertEqual(restored['next_actor'], 'counselor')


if __name__ == '__main__':
    unittest.main()
