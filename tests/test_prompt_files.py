import json
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from test_lab import Model, close, fixture, post


class PromptFileTests(unittest.TestCase):
    def setUp(self):
        self.server, self.model, self.temp = fixture(prompt_editing=True)
        Model.requests.clear()

    def tearDown(self):
        close(self.server, self.model, self.temp)

    def get(self, path):
        return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}' + path))

    def source(self, role='counselor', version='P3', kind='system'):
        return self.get('/api/prompts/source?' + urlencode(dict(role=role, version=version, kind=kind)))

    def save(self, source, content):
        return json.loads(post(self.server, '/api/prompts/save', {
            **{key: source[key] for key in ('role', 'version', 'kind', 'revision')}, 'content': content}))

    def test_all_editors_save_the_actual_source_and_catalogs_read_it(self):
        targets = [('counselor', version, 'system') for version in ('basic', 'P1', 'P2', 'P3', 'P3a')]
        targets += [('client', 'basic', 'system'), ('client', 'state_summary', 'system'),
                    ('client', 'state_summary', 'summary'), ('counselor', 'P3a', 'guide_agent')]
        for role, version, kind in targets:
            with self.subTest(role=role, version=version, kind=kind):
                source = self.source(role, version, kind)
                self.assertTrue(Path(source['path']).is_relative_to(self.temp.name))
                before_config = json.loads(self.server.config_path.read_text())
                content = source['content'] + '\nUI_SAVE: 줄바꿈과 한글을 그대로 저장\n'
                saved = self.save(source, content)
                self.assertTrue(saved['saved'])
                self.assertEqual(self.source(role, version, kind)['content'], content)
                raw = Path(saved['path']).read_text()
                self.assertEqual(json.loads(raw)[source['field']] if source['field'] else raw, content)
                if source['field']:
                    expected = {**before_config, 'system_prompt': content}
                    self.assertEqual(json.loads(raw), expected)
                catalog = self.get('/api/paired/counselor' if role == 'counselor' else '/api/versions')
                entry = next(row for row in catalog['versions'] if row['id'] == version)
                if kind == 'guide_agent':
                    self.assertTrue(any(row['kind'] == kind for row in entry['subagent_prompts']))
                    self.assertNotEqual(entry['system_prompt'], content)
                else:
                    self.assertEqual(entry['summary_system_prompt' if kind == 'summary' else 'system_prompt'], content)
        self.assertEqual(Model.requests, [])

    def test_stale_revision_and_generation_lock_preserve_the_file(self):
        for version, kind in (('P3', 'system'), ('P3a', 'guide_agent')):
            source = self.source(version=version, kind=kind)
            saved = self.save(source, source['content'] + '\nFIRST_SAVE')
            with self.assertRaises(HTTPError) as error:
                self.save(source, 'stale overwrite')
            self.assertEqual(error.exception.code, 409)
            with self.server.storage_lock:
                self.server.storage_jobs += 1
            try:
                with self.assertRaises(HTTPError) as error:
                    self.save(saved, 'changed during generation')
                self.assertEqual(error.exception.code, 409)
            finally:
                with self.server.storage_lock:
                    self.server.storage_jobs -= 1
            self.assertEqual(Path(saved['path']).read_text(), saved['content'])

    def test_only_registered_sources_and_authorized_posts_are_accepted(self):
        for role, version, kind in [('client', '../P3', 'system'), ('counselor', 'C4', 'system'),
                                    ('client', 'basic', 'summary'), ('other', 'P3', 'system'),
                                    ('counselor', 'P3', 'guide_agent'), ('counselor', 'P3a', '../guide_agent')]:
            with self.subTest(role=role, version=version, kind=kind), self.assertRaises(HTTPError) as error:
                self.source(role, version, kind)
            self.assertEqual(error.exception.code, 400)
        source = self.source()
        request = Request(f'http://127.0.0.1:{self.server.server_port}/api/prompts/save',
                          data=json.dumps({**source, 'content': 'missing header'}).encode())
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(Path(source['path']).read_text(), source['content'])

    def test_saved_defaults_reach_new_inputs_without_rewriting_old_sessions(self):
        config = self.get('/api/config')
        body = {'scenario': config['scenario'], 'generation': config['generation'],
                'experiment_version': 'state_summary', 'counselor_version': 'P3', 'enabled_tools': [],
                'speaker_modes': {'client': 'manual', 'counselor': 'manual'},
                'messages': [{'role': 'user', 'content': '오늘은 어떤 이야기를 할까요?'}]}
        old = json.loads(post(self.server, '/api/paired/create', body))
        original = self.source()
        updated = self.save(original, original['content'] + '\nNEW_FILE_DEFAULT')
        new = json.loads(post(self.server, '/api/paired/create', body))
        self.assertEqual(new['settings']['counselor_system_prompt'], updated['content'])
        stored = self.get('/api/paired/session?session_id=' + old['session_id'])
        self.assertEqual(stored['settings']['counselor_system_prompt'], original['content'])
        client = self.source('client', 'state_summary')
        client = self.save(client, client['content'] + '\nNEW_CLIENT_DEFAULT')
        preview = json.loads(post(self.server, '/api/preview', body))
        self.assertEqual(preview['payload']['messages'][0]['content'], client['content'])

    def test_subagent_saves_reach_next_call_in_existing_session(self):
        config = self.get('/api/config')
        body = {'scenario': config['scenario'], 'generation': config['generation'],
                'experiment_version': 'state_summary', 'counselor_version': 'P3a', 'enabled_tools': [],
                'speaker_modes': {'client': 'manual', 'counselor': 'model'},
                'messages': [{'role': 'user', 'content': '오늘은 어떤 이야기를 할까요?'}]}
        session = json.loads(post(self.server, '/api/paired/create', body))
        main_prompt = session['settings']['counselor_system_prompt']
        source = self.source(version='P3a', kind='guide_agent')
        Model.p3a_actions = [{'name': 'consult_counseling_guide', 'arguments': {
            'guide': 'common', 'request': '내담자의 현재 걱정을 어떻게 다루면 좋을까요?'}}]
        try:
            for marker in ('FIRST_CALL_PROMPT', 'NEXT_CALL_PROMPT'):
                source = self.save(source, source['content'] + '\n' + marker)
                session = json.loads(post(self.server, '/api/paired/manual', {
                    'session_id': session['session_id'], 'expected_step': len(session['steps']),
                    'actor': 'client', 'content': '평가받는 것이 걱정돼요.'}))['session']
                events = [json.loads(line)['event'] for line in post(self.server, '/api/paired/step', {
                    'session_id': session['session_id'], 'expected_step': len(session['steps'])}).splitlines()]
                self.assertEqual(events[-1]['type'], 'done', events[-1])
                child = next(e['event']['payload'] for e in events
                             if e['type'] == 'agent_event' and e['event']['type'] == 'request')
                self.assertEqual(child['messages'][0]['content'], source['content'])
                main = next(e['payload'] for e in events if e['type'] == 'request')
                self.assertEqual(main['messages'][0]['content'], main_prompt)
                session = events[-1]['session']
                self.assertEqual(session['settings']['counselor_system_prompt'], main_prompt)
        finally:
            Model.p3a_actions = None
