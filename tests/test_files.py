import json
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from test_lab import fixture, close, post, Model, app
import storage.file_manager as file_manager


class FileManagerTests(unittest.TestCase):
    def setUp(self):
        self.server, self.model, self.temp = fixture()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.root = Path(self.server.storage_dir)
        Model.requests.clear()

    def tearDown(self):
        close(self.server, self.model, self.temp)

    def get(self, route):
        return json.load(urlopen(self.base + route))

    def action(self, action, body):
        return json.loads(post(self.server, '/api/files/' + action, body))

    def test_crud_revisions_and_reversible_nonempty_folder_delete(self):
        self.action('create', {'path': 'notes', 'type': 'directory'})
        self.action('create', {'path': 'notes/test.md', 'type': 'file', 'content': '초기 내용'})
        file = self.get('/api/files/read?path=notes/test.md')
        changed = self.action('write', {'path': file['path'], 'revision': file['revision'], 'content': '편집 내용'})
        with self.assertRaises(HTTPError) as raised:
            self.action('write', {'path': file['path'], 'revision': file['revision'], 'content': 'stale overwrite'})
        self.assertEqual(raised.exception.code, 409)
        self.assertNotEqual(file['revision'], changed['revision'])
        deleted = self.action('delete', {'path': 'notes'})
        self.assertFalse((self.root / 'notes').exists())
        self.assertFalse(any(row['name'] == '.trash' for row in self.get('/api/files/list')['entries']))
        self.action('restore', {'trash_id': deleted['trash_id']})
        self.assertEqual((self.root / 'notes/test.md').read_text(), '편집 내용')
        self.assertEqual(Model.requests, [])

    def test_root_traversal_symlinks_binary_and_busy_mutations_rejected(self):
        (self.root/'binary').write_bytes(b'\0binary')
        (self.root/'escape').symlink_to('/tmp', target_is_directory=True)
        for action, body in [('delete', {'path': ''}), ('create', {'path': '../outside', 'type': 'file'}),
                             ('create', {'path': 'escape/outside', 'type': 'file'}), ('storage', {'storage_dir': '/'})]:
            with self.subTest(action=action, body=body), self.assertRaises(HTTPError):
                self.action(action, body)
        with self.assertRaises(HTTPError):
            self.get('/api/files/read?path=binary')
        with file_manager.generation(self.server), self.assertRaises(HTTPError) as raised:
            self.action('create', {'path': 'busy.txt', 'type': 'file'})
        self.assertEqual(raised.exception.code, 409)
        self.assertFalse((self.root/'busy.txt').exists())

    def test_changed_storage_is_persisted_and_old_pairs_remain_in_their_root(self):
        config = self.get('/api/config')
        request = {'scenario': config['scenario'], 'generation': config['generation'], 'experiment_version': 'state_summary',
                   'system_prompt': app.experiment_versions.catalog(config)['versions'][1]['system_prompt'],
                   'enabled_tools': [], 'counselor_version': 'basic', 'messages': [{'role': 'user', 'content': '편한 만큼 이야기해 주세요.'}]}
        old = json.loads(post(self.server, '/api/paired/create', request))
        new_root = Path(self.temp.name)/'new-sessions'
        self.action('storage', {'storage_dir': str(new_root)})
        self.assertEqual(self.server.storage_dir, new_root)
        self.assertEqual(json.loads(self.server.config_path.read_text())['storage_dir'], str(new_root))
        events = [json.loads(line)['event'] for line in post(self.server, '/api/paired/step', {'session_id':old['session_id'], 'expected_step':0}).splitlines()]
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        self.assertTrue(events[-1]['step']['saved_path'].startswith(str(self.root)))
        fresh = json.loads(post(self.server, '/api/paired/create', request))
        self.assertTrue((new_root/'paired'/fresh['session_id']/'session.json').is_file())
        # Restart lookup preserves the old session mapping without migrating files.
        self.server.paired_locations.clear()
        self.assertEqual(self.get('/api/paired/session?session_id='+old['session_id'])['storage_dir'], str(self.root))
        cfg = app.experiment_versions.catalog(self.get('/api/config'))
        self.assertEqual(cfg['default'], 'basic')
