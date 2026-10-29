import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lab import fixture, close, post, Model
from counselor.P1.runtime import apply_edits, initialize, revision, read


class NoteFileTests(unittest.TestCase):
    def test_atomic_edits_and_duplicate_source_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = initialize(directory)
            apply_edits(path, {'edits':[{'old_text':'','new_text':'기존 항목\n삭제 항목'}]}, revision(''))
            before = read(path)
            changed = apply_edits(path, {'edits':[{'old_text':'기존 항목','new_text':'수정 항목'}, {'old_text':'삭제 항목','new_text':''}]}, revision(before))
            self.assertIn('기존 항목', '\n'.join(changed['deleted']))
            self.assertIn('수정 항목', '\n'.join(changed['added']))
            with self.assertRaises(ValueError):
                apply_edits(path, {'edits':[{'old_text':'수정 항목','new_text':'다른 항목'},{'old_text':'없음','new_text':'추가'}]}, revision(read(path)))
            self.assertEqual(read(path), changed['content'])
            with self.assertRaises(ValueError):
                apply_edits(path, {'edits':[{'old_text':'','new_text':'추가'}]}, revision(before))


class P1Tests(unittest.TestCase):
    def setUp(self):
        self.server, self.model, self.temp = fixture()
        Model.requests.clear()
        Model.p1_repeat = False
    def tearDown(self):
        Model.p1_gate = None
        Model.p1_repeat = False
        close(self.server, self.model, self.temp)
    def get(self, path):
        return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}' + path))
    def create(self):
        config = self.get('/api/config')
        return json.loads(post(self.server, '/api/paired/create', {'scenario':config['scenario'], 'generation':config['generation'],
            'experiment_version':'state_summary', 'counselor_version':'P1', 'enabled_tools':[], 'thinking_mode':'on',
            'messages':[{'role':'user','content':config['scenario']['example_counselor_utterance']}]}))
    def manual_client(self, session, content):
        return json.loads(post(self.server, '/api/paired/manual', {'session_id':session['session_id'],'expected_step':len(session['steps']), 'actor':'client','content':content}))['session']
    def step(self, session):
        return [json.loads(line)['event'] for line in post(self.server, '/api/paired/step', {'session_id':session['session_id'],'expected_step':len(session['steps'])}).splitlines()]
    def test_file_injection_updates_and_reasoning_survive_every_handoff(self):
        session = self.manual_client(self.create(), 'P1_EDIT: 평가가 걱정돼요.')
        path = self.server.storage_dir / 'paired' / session['session_id'] / 'notes.md'
        path.write_text('OLD_NOTE: 더 이상 쓰지 않는 설명', encoding='utf-8')
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        updates = [event for event in events if event['type']=='version_event' and event['action']=='updated']
        self.assertEqual(len(updates), 1)
        self.assertIn('OLD_NOTE', '\n'.join(updates[0]['data']['deleted']))
        self.assertEqual(read(path), '현재 걱정: 평가에 대한 불안.\n')
        calls = [event['payload'] for event in events if event['type']=='request']
        self.assertEqual(len(calls), 2)
        self.assertIn('OLD_NOTE', calls[0]['messages'][1]['content'])
        self.assertEqual(calls[0]['messages'][1], calls[1]['messages'][1])
        self.assertIn('OLD_NOTE', calls[1]['messages'][1]['content'])
        assistant = next(message for message in calls[1]['messages'] if message['role']=='assistant')
        self.assertEqual(assistant['reasoning'], 'P1_REASON_BEFORE_TOOL')
        self.assertEqual(len([event for event in events if event['type']=='version_event' and event['action']=='snapshot']), 1)
        self.assertTrue(all(call['chat_template_kwargs']['enable_thinking'] for call in calls))
        self.assertTrue(any(message['role']=='tool' for message in calls[1]['messages']))
        self.assertNotIn('P1_REASON', json.dumps(events[-1]['session']['public_dialogue']))
        state = self.get('/api/paired/version_state?session_id='+session['session_id'])
        self.assertEqual(state['data']['notes']['current']['content'], read(path))
        # The next counselor turn, rather than a tool continuation, imports saved notes.
        session = self.manual_client(events[-1]['session'], '지금의 걱정을 계속 이야기해볼게요.')
        next_events = self.step(session)
        request = next(event['payload'] for event in next_events if event['type']=='request')
        self.assertTrue(request['messages'][1]['content'].endswith(read(path)))
        self.assertFalse(any(message.get('reasoning') or message.get('reasoning_content') for message in request['messages']))
        # External file edits are reloaded on the next counselor turn too.
        path.write_text('EXTERNAL_NOTE_CURRENT', encoding='utf-8')
        session = self.manual_client(next_events[-1]['session'], '이어서 이야기해볼게요.')
        next_events = self.step(session)
        self.assertIn('EXTERNAL_NOTE_CURRENT', next(event['payload'] for event in next_events if event['type']=='request')['messages'][1]['content'])
        self.assertFalse(any(event['type']=='tool_start' for event in next_events))
        other = self.create()
        self.assertEqual(read(self.server.storage_dir / 'paired' / other['session_id'] / 'notes.md'), '')

    def test_two_tool_calls_keep_both_reasoning_blocks_and_original_base_note(self):
        Model.p1_repeat = True
        session = self.manual_client(self.create(), 'P1_EDIT: 여러 번 기록해 봅니다.')
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        calls = [event['payload'] for event in events if event['type']=='request']
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call['messages'][1] == calls[0]['messages'][1] for call in calls))
        assistants = [message for message in calls[2]['messages'] if message['role']=='assistant']
        self.assertEqual([message['reasoning'] for message in assistants], ['P1_REASON_BEFORE_TOOL', 'P1_REASON_AFTER_TOOL'])
        self.assertEqual([message['role'] for message in calls[2]['messages']], ['system','user','assistant','tool','assistant','tool'])
        starts = [event for event in events if event['type']=='tool_start']
        self.assertEqual([event['assistant_message'] for event in starts], assistants)
        note = read(self.server.storage_dir / 'paired' / session['session_id'] / 'notes.md')
        self.assertIn('추가 기록', note)
        session = self.manual_client(events[-1]['session'], '다음 상담 턴입니다.')
        next_events = self.step(session)
        first = next(event['payload'] for event in next_events if event['type']=='request')
        self.assertTrue(first['messages'][1]['content'].endswith(note))
        self.assertNotIn('P1_REASON', json.dumps(first['messages'], ensure_ascii=False))
