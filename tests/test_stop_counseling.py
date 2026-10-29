"""The stop launcher recognizes this app and leaves unrelated listeners alive."""
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import unittest

from project_paths import CONFIG_FILE, PROJECT_ROOT


@unittest.skipUnless(shutil.which('ss'), 'The stop launcher requires ss')
class StopCounselingTests(unittest.TestCase):
    def launch(self, arguments, cwd):
        env = os.environ.copy()
        env['COUNSELING_PYTHON'] = sys.executable
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        process = subprocess.Popen(arguments, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.cleanup_process, process)
        self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'Server did not start')
        line = process.stdout.readline().strip()
        self.assertTrue(line, 'Server exited before reporting its port')
        return process, int(line.rsplit(':', 1)[-1])

    @staticmethod
    def cleanup_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()

    def stop(self, port, cwd):
        env = os.environ.copy()
        env['COUNSELING_PYTHON'] = sys.executable
        return subprocess.run(['bash', str(PROJECT_ROOT / 'scripts/stop_counseling.sh'),
                               '--port', str(port)], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=20)

    def test_module_launcher_stops_from_another_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            config = json.loads(CONFIG_FILE.read_text())
            config['storage_dir'] = str(Path(folder) / 'runs')
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps(config))
            process, port = self.launch(
                ['bash', str(PROJECT_ROOT / 'scripts/run_counseling.sh'),
                 '--config', str(path), '--port', '0'], folder)
            stopped = self.stop(port, folder)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertIn('종료 완료', stopped.stdout)
            process.wait(timeout=3)
            again = self.stop(port, folder)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn('실행 중인 서버가 없습니다', again.stdout)

    def test_unrelated_server_is_not_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            code = ("from http.server import HTTPServer, SimpleHTTPRequestHandler; "
                    "s=HTTPServer(('127.0.0.1',0),SimpleHTTPRequestHandler); "
                    "print(s.server_port,flush=True); s.serve_forever()")
            process, port = self.launch([sys.executable, '-c', code], folder)
            stopped = self.stop(port, folder)
            self.assertNotEqual(stopped.returncode, 0)
            self.assertIn('이 프로젝트의 상담 UI 서버가 아니므로', stopped.stderr)
            self.assertIsNone(process.poll())
