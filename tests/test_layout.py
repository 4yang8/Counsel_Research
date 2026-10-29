"""Relocation preserves original research inputs and adapts filesystem pointers only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import project_paths
from app import app_server


class LayoutTests(unittest.TestCase):
    def test_historical_session_pointers_relocate_without_rewriting_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            new = Path(folder)
            old = new / 'old/gemma_test/counseling_test/sessions'
            target = new / 'runs/paired/session'
            target.mkdir(parents=True)
            (target / 'call.json').write_text('{}')
            original = {'storage_dir': str(old), 'client_storage_dir': str(old / 'private'),
                        'steps': [{'saved_path': str(old / 'paired/session/call.json')}],
                        'attempts': [{'saved_path': str(old / 'paired/session/call.json')}],
                        'settings': {'system_prompt': str(old)},
                        'public_dialogue': [{'content': str(old)}]}
            before = copy.deepcopy(original)
            with patch.object(project_paths, 'PROJECT_ROOT', new):
                loaded = project_paths.relocate_session(copy.deepcopy(original))
            self.assertEqual(loaded['storage_dir'], str(new / 'runs'))
            self.assertEqual(loaded['client_storage_dir'], str(new / 'runs/private'))
            self.assertEqual(loaded['steps'][0]['saved_path'], str(target / 'call.json'))
            self.assertEqual(loaded['attempts'], loaded['steps'])
            self.assertEqual(loaded['settings'], before['settings'])
            self.assertEqual(loaded['public_dialogue'], before['public_dialogue'])
            self.assertEqual(original, before)

    def test_external_storage_is_not_relocated(self):
        external = '/tmp/independent-study/sessions/checkpoint.json'
        self.assertEqual(project_paths.relocate_path(external), external)

    def test_default_resources_exist_without_old_app_folder(self):
        self.assertTrue(project_paths.CONFIG_FILE.is_file())
        self.assertTrue((project_paths.PROJECT_ROOT / 'frontend/workspace.html').is_file())
        self.assertTrue(app_server.runtime.load_scenario({})['profile'])
        self.assertTrue(app_server.load_group1_profiles()['profiles'])

    def test_old_browser_session_key_resolves_to_moved_session(self):
        from types import SimpleNamespace
        import threading
        from storage import session_browser
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            storage = project / 'runs'
            directory = storage / 'paired/existing-session'
            directory.mkdir(parents=True)
            (directory / 'session.json').write_text(json.dumps({
                'session_id': 'existing-session', 'public_dialogue': [], 'steps': []}))
            server = SimpleNamespace(storage_dir=storage, storage_roots={str(storage)},
                                     storage_lock=threading.RLock())
            old = project / 'gemma_test/counseling_test/sessions'
            key = session_browser.key_for(old, 'paired', 'existing-session')
            with patch.object(project_paths, 'PROJECT_ROOT', project):
                self.assertEqual(session_browser.resolve(server, key),
                                 (storage, 'paired', 'existing-session'))

    def test_all_clients_import_with_only_the_main_source_path(self):
        import os
        import subprocess
        import sys
        env = os.environ.copy()
        env['PYTHONPATH'] = str(project_paths.PROJECT_ROOT / 'src')
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        code = ('from client.registry import NAMES, client_runtime; '
                '[client_runtime(v).specification() for v in NAMES '
                'if v not in ("basic", "state_summary")]')
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, '-c', code], cwd=folder,
                                    env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_help_from_another_working_directory(self):
        import os
        import subprocess
        import sys
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        env['COUNSELING_PYTHON'] = sys.executable
        script = project_paths.PROJECT_ROOT / 'scripts/run_counseling.sh'
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(['bash', str(script), '--help'], cwd=folder,
                                    env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--config', result.stdout)
