import json
import unittest

import test_p1
from test_lab import fixture, close, Model
from counselor.P1.runtime import read


class P2Tests(unittest.TestCase):
    get = test_p1.P1Tests.get
    manual_client = test_p1.P1Tests.manual_client
    step = test_p1.P1Tests.step

    def setUp(self):
        self.server, self.model, self.temp = fixture()
        Model.requests.clear()
        Model.p2_actions = None

    def tearDown(self):
        Model.p2_actions = None
        close(self.server, self.model, self.temp)

    def create(self):
        from test_lab import post
        config = self.get('/api/config')
        return json.loads(post(self.server, '/api/paired/create', {
            'scenario':config['scenario'], 'generation':config['generation'],
            'experiment_version':'state_summary', 'counselor_version':'P2',
            'enabled_tools':[], 'thinking_mode':'on',
            'messages':[{'role':'user','content':config['scenario']['example_counselor_utterance']}]}))

    def directory(self, session):
        return self.server.storage_dir / 'paired' / session['session_id']

    def test_both_notes_frozen_within_turn_and_fresh_next_turn(self):
        session = self.manual_client(self.create(), 'P2_EDIT: 발표가 두려워요.')
        directory = self.directory(session)
        (directory / 'notes.md').write_text('OLD_CLIENT', encoding='utf-8')
        (directory / 'counselor_notes.md').write_text('OLD_PLAN: 불안한 상황을 확인한다.', encoding='utf-8')
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        calls = [e['payload'] for e in events if e['type']=='request']
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c['messages'][1] == calls[0]['messages'][1] for c in calls))
        self.assertIn('OLD_CLIENT', calls[-1]['messages'][1]['content'])
        self.assertIn('OLD_PLAN', calls[-1]['messages'][1]['content'])
        self.assertEqual([m['reasoning'] for m in calls[-1]['messages'] if m['role']=='assistant'], ['P2_REASON_0','P2_REASON_1'])
        replies = [m['content'] for m in calls[-1]['messages'] if m['role']=='tool']
        self.assertIn('client', replies[0]);self.assertIn('counselor', replies[1])
        updates = [e for e in events if e['type']=='version_event' and e['action']=='updated']
        self.assertEqual([e['section'] for e in updates], ['client_notes','counselor_notes'])
        self.assertIn('OLD_PLAN', '\n'.join(updates[1]['data']['deleted']))
        state = self.get('/api/paired/version_state?session_id='+session['session_id'])['data']
        for key, filename in [('client_notes','notes.md'),('counselor_notes','counselor_notes.md')]:
            self.assertEqual(state[key]['current']['content'], read(directory / filename))
        self.assertNotIn('P2_REASON', json.dumps(events[-1]['session']['public_dialogue']))
        # Both files are reloaded, including external edits, without old raw reasoning.
        (directory / 'counselor_notes.md').write_text('EXTERNAL_PLAN', encoding='utf-8')
        session = self.manual_client(events[-1]['session'], '이어서 이야기할게요.')
        following = self.step(session)
        first = next(e['payload'] for e in following if e['type']=='request')
        self.assertIn(read(directory / 'notes.md'), first['messages'][1]['content'])
        self.assertTrue(first['messages'][1]['content'].endswith('EXTERNAL_PLAN'))
        self.assertNotIn('P2_REASON', json.dumps(first['messages']))
        self.assertFalse(any(e['type']=='tool_start' for e in following))
        other = self.directory(self.create())
        self.assertEqual(read(other / 'notes.md'), '')
        self.assertEqual(read(other / 'counselor_notes.md'), '')

    def test_optional_calls_and_repeated_edits_to_same_target(self):
        Model.p2_actions = [
            {'target':'counselor','edits':[{'old_text':'','new_text':'계획 A'}]},
            {'target':'counselor','edits':[{'old_text':'계획 A','new_text':'계획 B'}]},
        ]
        session = self.manual_client(self.create(), '생각을 수정해볼게요.')
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(read(self.directory(session) / 'notes.md'), '')
        self.assertEqual(read(self.directory(session) / 'counselor_notes.md'), '계획 B')

    def test_invalid_target_cannot_write_other_files(self):
        Model.p2_actions = [{'target':'../notes.md','edits':[{'old_text':'','new_text':'잘못된 기록'}]}]
        session = self.manual_client(self.create(), '잘못된 도구 인자 테스트')
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertFalse(any(e['type']=='version_event' and e['action']=='updated' for e in events))
        calls = [e['payload'] for e in events if e['type']=='request']
        self.assertIn('target은 client 또는 counselor', calls[-1]['messages'][-1]['content'])
        self.assertEqual(read(self.directory(session) / 'notes.md'), '')
        self.assertEqual(read(self.directory(session) / 'counselor_notes.md'), '')
