"""Cleanup keeps research tools and new experiment snapshots executable."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_paths import PROJECT_ROOT
from runtime import recovery_tool_loop
from runtime.cancellation import Cancelled
from side_experiments import run as experiment
from side_experiments import tool_loop as historical_loop


class CleanupTests(unittest.TestCase):
    def test_historical_retry_import_uses_the_production_implementation(self):
        self.assertIs(historical_loop.run_turn, recovery_tool_loop.run_turn)
        self.assertIs(historical_loop.validate_messages, recovery_tool_loop.validate_messages)

    def test_snapshot_copies_current_code_and_inputs_without_using_the_model(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(experiment, 'api', side_effect=AssertionError('No model/API calls')):
                snapshot = experiment.save_snapshot(directory)
            for relative in ('src/runtime/cancellation.py', 'src/runtime/paired_lab.py',
                             'src/runtime/recovery_tool_loop.py', 'src/app/app_server.py',
                             'src/client/Q1_s1/system.md', 'src/counselor/P3a_s1/system.md',
                             'resources/tools.json', 'config/app.json'):
                self.assertEqual((snapshot / relative).read_bytes(),
                                 (PROJECT_ROOT / relative).read_bytes())
            self.assertFalse(any(snapshot.rglob('*.pyc')))
            self.assertFalse((snapshot / 'trashcan').exists())
            self.assertFalse((snapshot / 'runs').exists())
            with self.assertRaises(FileExistsError):
                experiment.save_snapshot(directory)

    def test_missing_snapshot_source_fails_before_creating_a_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(experiment, 'LAB', folder / 'missing'):
                with self.assertRaises(FileNotFoundError):
                    experiment.save_snapshot(folder / 'run')
            self.assertFalse((folder / 'run/snapshot').exists())

    def test_tools_and_cancellation_no_longer_depend_on_filesystem_mcp(self):
        from app import server
        from client.Q1 import runtime as q1
        self.assertIs(q1.Cancelled, Cancelled)
        self.assertIs(server.Cancelled, Cancelled)
        self.assertFalse((PROJECT_ROOT / 'src/runtime/mcp_client.py').exists())
        self.assertEqual(server.PlaygroundServer.definitions(None, {}), [])
