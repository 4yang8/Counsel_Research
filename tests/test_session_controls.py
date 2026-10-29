"""Verify persisted sessions, speaker ownership and the actual upstream model."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_paired as paired_fixture
from test_lab import post, Model


class SessionControlsTests(unittest.TestCase):
    setUp = paired_fixture.PairedTests.setUp
    tearDown = paired_fixture.PairedTests.tearDown
    create = paired_fixture.PairedTests.create
    step = paired_fixture.PairedTests.step
    stored = paired_fixture.PairedTests.stored
    def get(self, path):
        return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}' + path))

    def controls(self, session, client, counselor):
        return json.loads(post(self.server, '/api/paired/controls', {
            'session_id': session['session_id'], 'expected_step': len(session['steps']),
            'speaker_modes': {'client': client, 'counselor': counselor}}))

    def test_session_folders_use_korean_time_and_do_not_overwrite_collisions(self):
        instant = datetime(2026, 10, 16, 3, 49, tzinfo=timezone.utc)
        with patch('runtime.paired_lab.datetime') as clock:
            clock.now.side_effect = lambda tz: instant.astimezone(tz)
            first = self.create(counselor='P3', content='첫 번째 세션')
            second = self.create(counselor='P3', content='두 번째 세션')
        self.assertEqual(first['session_id'], '261016_124900')
        self.assertEqual(second['session_id'], '261016_124900_2')
        for session in (first, second):
            directory = self.server.storage_dir / 'paired' / session['session_id']
            self.assertTrue((directory / 'session.json').is_file())
            self.assertTrue((directory / 'notes.md').is_file())
            self.assertTrue((directory / 'counselor_notes.md').is_file())
            self.assertEqual(self.stored(session)['public_dialogue'], session['public_dialogue'])
        rows = self.get('/api/sessions')['sessions']
        self.assertEqual({row['session_id'] for row in rows}, {first['session_id'], second['session_id']})

    def test_existing_uuid_session_can_still_be_listed_and_loaded(self):
        session = self.create(counselor='P3')
        previous = self.server.storage_dir / 'paired' / session['session_id']
        legacy_id = '86907114f91246aab3de0cfdd6c86676'
        directory = previous.with_name(legacy_id)
        previous.rename(directory)
        session['session_id'] = legacy_id
        (directory / 'session.json').write_text(json.dumps(session, ensure_ascii=False), encoding='utf-8')
        rows = self.get('/api/sessions')['sessions']
        self.assertEqual([row['session_id'] for row in rows], [legacy_id])
        self.assertEqual(self.stored(session)['public_dialogue'], session['public_dialogue'])
        state = self.get('/api/paired/version_state?session_id=' + legacy_id)['data']
        self.assertEqual(Path(state['client_notes']['current']['path']), directory / 'notes.md')
        self.assertEqual(Path(state['counselor_notes']['current']['path']), directory / 'counselor_notes.md')

    def test_list_restores_public_dialogue_without_inference(self):
        first = self.step(self.create(counselor='basic'))[-1]['session']
        second = self.create(counselor='basic', content='두 번째 세션의 질문')
        before = len(Model.requests)
        rows = self.get('/api/sessions')['sessions']
        self.assertEqual({row['session_id'] for row in rows}, {first['session_id'], second['session_id']})
        for original in (first, second):
            row = next(row for row in rows if row['session_id'] == original['session_id'])
            saved = self.get('/api/paired/session?session_id=' + row['session_id'] + '&key=' + row['key'])
            self.assertEqual(saved['public_dialogue'], original['public_dialogue'])
        self.assertEqual(len(Model.requests), before)
        # A broken file must not remove other sessions from the list.
        corrupt = self.server.storage_dir / 'paired' / 'corrupt' / 'session.json'
        corrupt.parent.mkdir(parents=True);corrupt.write_text('{broken')
        self.assertEqual(len(self.get('/api/sessions')['sessions']), 2)

    def test_manual_modes_wait_preserve_turn_order_and_resume(self):
        session = self.controls(self.create(counselor='basic'), 'manual', 'model')
        before = len(Model.requests)
        with self.assertRaises(HTTPError):
            self.step(session)
        self.assertEqual(len(Model.requests), before)
        # Client input is public speech; there is no fabricated private record.
        session = json.loads(post(self.server, '/api/paired/manual', {
            'session_id': session['session_id'], 'expected_step': 0,
            'actor': 'client', 'content': '직접 쓴 내담자 응답'}))['session']
        self.assertNotIn('client_storage_dir', session)
        self.assertEqual(session['next_actor'], 'counselor')
        session = self.step(session)[-1]['session']
        self.assertIn('직접 쓴 내담자 응답', Model.requests[-1]['messages'][1]['content'])
        with self.assertRaises(HTTPError):
            post(self.server, '/api/paired/manual', {'session_id': session['session_id'],
                 'expected_step': len(session['steps']), 'actor': 'counselor', 'content': '잘못된 차례'})
        session = self.controls(session, 'model', 'manual')
        session = self.step(session)[-1]['session']
        self.assertEqual(session['next_actor'], 'counselor')
        self.assertEqual(self.stored(session)['speaker_modes'], {'client': 'model', 'counselor': 'manual'})

    def test_model_selection_is_frozen_for_both_speakers(self):
        config = self.get('/api/config')
        system = self.get('/api/versions')['versions'][1]['system_prompt']
        for model in ('Qwen/Qwen3.8-27B', 'Qwen/Qwen3.8-27B-FP8', 'Qwen/Qwen3.8-Flash-Next-FP8'):
            request = {'scenario': config['scenario'], 'generation': config['generation'],
                       'system_prompt': system, 'experiment_version': 'state_summary',
                       'enabled_tools': [], 'counselor_version': 'basic', 'model': model,
                       'messages': [{'role': 'user', 'content': '편한 만큼 이야기해 주세요.'}]}
            preview = json.loads(post(self.server, '/api/preview', request))
            self.assertEqual(preview['payload']['model'], model)
            session = json.loads(post(self.server, '/api/paired/create', request))
            begin = len(Model.requests)
            for _ in range(3):
                session = self.step(session)[-1]['session']
            self.assertTrue(all(row['model'] == model for row in Model.requests[begin:]))
            self.assertEqual(self.stored(session)['connection']['model'], model)
        self.assertEqual(self.get('/api/config')['model'], config['model'])

    def test_failed_reply_allows_replacing_last_manual_speech_without_duplicate_turns(self):
        session = self.controls(self.create(counselor='basic'), 'manual', 'model')
        session = json.loads(post(self.server, '/api/paired/manual', {
            'session_id': session['session_id'], 'expected_step': 0,
            'actor': 'client', 'content': 'ORIGINAL_MANUAL_SPEECH'}))['session']
        turn_id = session['steps'][-1]['turn_id']
        replacement = {'session_id': session['session_id'], 'expected_step': 1,
                       'actor': 'client', 'content': 'REPLACED_MANUAL_SPEECH', 'replace_turn_id': turn_id,
                       'replace_content': 'ORIGINAL_MANUAL_SPEECH'}
        with self.assertRaises(HTTPError):
            post(self.server, '/api/paired/manual', replacement)
        Model.basic_counselor_finish = 'length'
        failed = self.step(session)
        self.assertEqual(failed[-1]['type'], 'error')
        saved = self.stored(session)
        self.assertEqual(saved['retry_manual']['content'], 'ORIGINAL_MANUAL_SPEECH')
        self.assertEqual(saved['next_actor'], 'counselor')
        with self.assertRaises(HTTPError):
            post(self.server, '/api/paired/manual', {**replacement, 'expected_step': 0})
        changed = json.loads(post(self.server, '/api/paired/manual', replacement))['session']
        self.assertEqual(len(changed['steps']), 1)
        self.assertEqual(changed['public_dialogue'][:-1], session['public_dialogue'][:-1])
        self.assertEqual(changed['public_dialogue'][-1]['content'], 'REPLACED_MANUAL_SPEECH')
        self.assertEqual(changed['steps'][-1]['turn_id'], turn_id)
        self.assertEqual(changed['next_actor'], 'counselor')
        self.assertEqual(changed['manual_revisions'][-1]['before'], 'ORIGINAL_MANUAL_SPEECH')
        with self.assertRaises(HTTPError):
            post(self.server, '/api/paired/manual', replacement)
        Model.basic_counselor_finish = 'stop'
        completed = self.step(changed)
        self.assertEqual(completed[-1]['type'], 'done')
        request = next(e['payload'] for e in completed if e['type'] == 'request')
        self.assertIn('REPLACED_MANUAL_SPEECH', request['messages'][1]['content'])
        self.assertNotIn('ORIGINAL_MANUAL_SPEECH', request['messages'][1]['content'])
        current = self.stored(session)
        self.assertIsNone(current['retry_manual'])
        self.assertEqual(len(current['steps']), 2)
        self.assertEqual(len(current['attempts']), 2)
        history = self.get('/api/paired/events?session_id='+session['session_id']+'&attempt='+current['attempts'][0]['turn_id'])['events']
        self.assertEqual(history[-1]['type'], 'error')
        self.assertEqual(history[0]['public_dialogue'][-1]['content'], 'ORIGINAL_MANUAL_SPEECH')
        with self.assertRaises(HTTPError):
            post(self.server, '/api/paired/manual', {**replacement, 'expected_step': 2})

    def test_legacy_session_list_and_journal_are_read_only(self):
        config = self.get('/api/config')
        request = {'scenario': config['scenario'], 'generation': config['generation'],
                   'system_prompt': self.get('/api/versions')['versions'][1]['system_prompt'],
                   'experiment_version': 'state_summary', 'enabled_tools': [],
                   'messages': [{'role': 'user', 'content': config['scenario']['example_counselor_utterance']}]}
        # The API validates the final enabled counselor target.
        request['messages'][0]['content'] = config['scenario'].get('selected_counselor_utterance',
                                                        config['scenario']['example_counselor_utterance'])
        events = [json.loads(line) for line in post(self.server, '/api/chat', request).splitlines()]
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        rows = self.get('/api/sessions')['sessions']
        row = next(row for row in rows if row['kind'] == 'state_summary')
        before = len(Model.requests)
        loaded = self.get('/api/sessions/standalone?key=' + row['key'])
        self.assertEqual(loaded['session_id'], row['session_id'])
        self.assertTrue(any(e['type'] == 'request' for e in loaded['turns'][0]['events']))
        self.assertEqual(len(Model.requests), before)
        with self.assertRaises(HTTPError):
            self.get('/api/sessions/standalone?key=../../config.json')


if __name__ == '__main__':
    import unittest
    unittest.main()
