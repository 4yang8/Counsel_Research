"""Private/public atomicity and lossless cleanup, using temporary data only."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from storage import paired_state as state


class PairedStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = {'session_id': 'test', 'client_version': 'Q3compare_memory', 'steps': []}
        state.save(self.root, self.session)

    def prepare(self, attempt, text):
        working = state.prepare(self.root, self.session, attempt)
        (working / 'memory.txt').write_text(text)
        return working

    def commit(self, attempt, text):
        working = self.prepare(attempt, text)
        self.session['client_storage_dir'] = str(working)
        self.session['steps'].append(attempt)
        state.save(self.root, self.session)
        return self.root / 'client_state'

    def test_twenty_turns_keep_one_state_and_stable_path(self):
        for i in range(20):
            current = self.commit(str(i), '\n'.join(map(str, range(i + 1))))
            self.assertEqual(self.session['client_storage_dir'], str(current))
            self.assertEqual((current / 'memory.txt').read_text().splitlines()[-1], str(i))
        self.assertFalse((self.root / 'client_attempts').exists())
        self.assertFalse((self.root / '.client_pending').exists())
        self.assertFalse((self.root / '.client_previous').exists())
        self.assertEqual(json.loads((self.root / 'session.json').read_text())['steps'], self.session['steps'])

    def test_failed_generation_preserves_old_state_and_archives_attempt(self):
        current = self.commit('one', 'old')
        working = self.prepare('two', 'failed new')
        state.discard(self.root, working)
        self.assertEqual((current / 'memory.txt').read_text(), 'old')
        with tarfile.open(self.root / 'client_failures/two.tar.gz') as archive:
            self.assertEqual(archive.extractfile('two/memory.txt').read(), b'failed new')
        self.assertFalse(working.exists())

    def test_new_attempt_archives_work_left_by_process_exit(self):
        current = self.commit('one', 'old')
        abandoned = self.prepare('crashed', 'uncommitted')
        working = self.prepare('retry', 'retry')
        self.assertFalse(abandoned.exists())
        self.assertTrue((self.root / 'client_failures/crashed.tar.gz').is_file())
        self.assertEqual((current / 'memory.txt').read_text(), 'old')
        self.assertEqual((working / 'memory.txt').read_text(), 'retry')

    def test_failed_public_save_rolls_back_private_state(self):
        current = self.commit('one', 'old')
        before = (self.root / 'session.json').read_bytes()
        self.session['client_storage_dir'] = str(self.prepare('two', 'new'))
        writer = state.write_new
        def fail_public(path, data):
            if path.name == 'session.json':
                raise OSError('simulated disk failure')
            writer(path, data)
        with patch.object(state, 'write_new', side_effect=fail_public):
            with self.assertRaises(OSError):
                state.save(self.root, self.session)
        self.assertEqual((current / 'memory.txt').read_text(), 'old')
        self.assertEqual((self.root / 'session.json').read_bytes(), before)
        self.assertFalse((self.root / '.client_transaction.json').exists())

    def test_crash_before_public_commit_restores_previous_state(self):
        current = self.commit('one', 'old')
        working = self.prepare('two', 'new')
        state.write_new(self.root / '.client_transaction.json', {'attempt': 'two', 'had_current': True})
        current.rename(self.root / '.client_previous')
        working.rename(current)
        state.recover(self.root)
        self.assertEqual((current / 'memory.txt').read_text(), 'old')

    def test_crash_after_public_commit_keeps_new_state(self):
        current = self.commit('one', 'old')
        working = self.prepare('two', 'new')
        state.write_new(self.root / '.client_transaction.json', {'attempt': 'two', 'had_current': True})
        current.rename(self.root / '.client_previous')
        working.rename(current)
        self.session['client_state_commit'] = 'two'
        state.write_new(self.root / 'session.json', self.session)
        state.recover(self.root)
        self.assertEqual((current / 'memory.txt').read_text(), 'new')
        self.assertFalse((self.root / '.client_previous').exists())

    def test_first_turn_crash_does_not_publish_private_state(self):
        working = self.prepare('one', 'new')
        state.write_new(self.root / '.client_transaction.json', {'attempt': 'one', 'had_current': False})
        working.rename(self.root / 'client_state')
        state.recover(self.root)
        self.assertFalse((self.root / 'client_state').exists())
        self.assertNotIn('client_storage_dir', json.loads((self.root / 'session.json').read_text()))

    def test_historical_q3p6_snapshots_are_not_changed(self):
        self.session['client_version'] = 'Q3p6'
        first = self.prepare('one', 'old')
        self.session['client_storage_dir'] = str(first)
        state.save(self.root, self.session)
        second = self.prepare('two', 'new')
        self.session['client_storage_dir'] = str(second)
        state.save(self.root, self.session)
        self.assertEqual((first / 'memory.txt').read_text(), 'old')
        self.assertEqual((second / 'memory.txt').read_text(), 'new')

    def test_fork_copies_are_independent(self):
        current = self.commit('one', 'common')
        for name in ('provided', 'withheld'):
            child = self.root / name
            session = copy.deepcopy(self.session)
            working = state.prepare(child, session, 'branch')
            (working / 'memory.txt').write_text(name)
            session['client_storage_dir'] = str(working)
            state.save(child, session)
        self.assertEqual((current / 'memory.txt').read_text(), 'common')
        self.assertEqual((self.root / 'provided/client_state/memory.txt').read_text(), 'provided')
        self.assertEqual((self.root / 'withheld/client_state/memory.txt').read_text(), 'withheld')

    def test_archive_verification_failure_keeps_source(self):
        working = self.prepare('one', 'important')
        with patch.object(tarfile.TarFile, 'extractfile', side_effect=OSError('read error')):
            with self.assertRaises(OSError):
                state.archive_tree(working, self.root / 'failed.tar.gz')
        self.assertEqual((working / 'memory.txt').read_text(), 'important')
        self.assertFalse((self.root / 'failed.tar.gz').exists())

    def test_archive_cleanup_can_resume_without_overwriting_archive(self):
        working = self.prepare('one', 'important')
        archive = self.root / 'saved.tar.gz'
        with patch.object(state.shutil, 'rmtree', side_effect=OSError('interrupted cleanup')):
            with self.assertRaises(OSError):
                state.archive_tree(working, archive)
        before = archive.read_bytes()
        self.assertTrue(working.exists())
        state.archive_tree(working, archive)
        self.assertFalse(working.exists())
        self.assertEqual(archive.read_bytes(), before)

    def test_legacy_migration_preserves_latest_and_archives_every_attempt(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/compact_paired_state.py'
        spec = importlib.util.spec_from_file_location('compact_test', script)
        compact = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compact)
        for attempt, value in [('first', {'records': [1]}), ('last', {'records': [1, 2]}),
                               ('failed', {'records': [1, 2], 'partial': True})]:
            target = self.root / 'client_attempts' / attempt / 'Q3compare_memory/test/memory_state.json'
            state.write_new(target, value)
        old = self.root / 'client_attempts/last'
        self.session.update(client_storage_dir=str(old), public_dialogue=[{'role': 'client', 'content': 'unchanged'}])
        state.write_new(self.root / 'session.json', self.session)
        with patch.object(compact, 'PROJECT', self.root), patch.object(compact, 'check_offline'):
            plan = compact.compact(self.root)
            self.assertEqual(plan['status'], 'planned')
            self.assertTrue(old.exists())
            result = compact.compact(self.root, apply=True)
            again = compact.compact(self.root, apply=True)
        self.assertEqual(result['status'], 'compacted')
        self.assertEqual(again['status'], 'already_compact')
        current = self.root / 'client_state/Q3compare_memory/test/memory_state.json'
        self.assertEqual(json.loads(current.read_text()), {'records': [1, 2]})
        saved = json.loads((self.root / 'session.json').read_text())
        self.assertEqual(saved['public_dialogue'], self.session['public_dialogue'])
        self.assertFalse((self.root / 'client_attempts').exists())
        with tarfile.open(self.root / result['archive']) as archive:
            self.assertEqual(len([m for m in archive.getmembers() if m.isfile()]), 3)
            failed = json.load(archive.extractfile('client_attempts/failed/Q3compare_memory/test/memory_state.json'))
            self.assertTrue(failed['partial'])

    def test_legacy_first_turn_save_failure_restores_old_path(self):
        old = self.root / 'client_attempts/old'
        old.mkdir(parents=True)
        (old / 'memory.txt').write_text('legacy')
        self.session['client_storage_dir'] = str(old)
        state.write_new(self.root / 'session.json', self.session)
        self.session['client_storage_dir'] = str(self.prepare('new', 'new'))
        writer = state.write_new
        def fail_public(path, data):
            if path.name == 'session.json':
                raise OSError('simulated disk failure')
            writer(path, data)
        with patch.object(state, 'write_new', side_effect=fail_public):
            with self.assertRaises(OSError):
                state.save(self.root, self.session)
        self.assertEqual(self.session['client_storage_dir'], str(old))
        self.assertEqual((old / 'memory.txt').read_text(), 'legacy')
