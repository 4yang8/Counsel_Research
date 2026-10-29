"""Streaming durability, batching, and isolation (no live model calls)."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage.event_journal import EventJournal, read_events
from app.server import TurnLog


def chunk(**delta):
    return {'type': 'chunk', 'data': {'choices': [{'index': 0, 'delta': delta}]}}


class JournalTests(unittest.TestCase):
    def test_periodic_flush_without_another_token(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = EventJournal(Path(directory) / 'events.jsonl', interval=.03)
            written = threading.Event()
            original = journal._write
            def write(batch):
                original(batch)
                written.set()
            journal._write = write
            try:
                journal.append({'text': '추론'})
                self.assertTrue(written.wait(2), 'idle stream must still flush')
                self.assertEqual(read_events(journal.path), [{'text': '추론'}])
            finally:
                journal.close()
            self.assertFalse(journal._thread.is_alive())

    def test_slow_disk_does_not_block_stream_enqueue(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = EventJournal(Path(directory) / 'events.jsonl', interval=0)
            entered, release, queued = threading.Event(), threading.Event(), threading.Event()
            original = journal._write
            def write(batch):
                entered.set()
                release.wait(3)
                original(batch)
            journal._write = write
            journal.append({'n': 0})
            self.assertTrue(entered.wait(2))
            def enqueue():
                for n in range(1, 1001):
                    journal.append({'n': n})
                queued.set()
            producer = threading.Thread(target=enqueue)
            producer.start()
            try:
                self.assertTrue(queued.wait(2), 'UI producer must not wait for disk fsync')
            finally:
                release.set()
                producer.join(3)
                journal.close()
            self.assertEqual(read_events(journal.path), [{'n': n} for n in range(1001)])

    def test_write_failure_reaches_caller_and_thread_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = EventJournal(Path(directory) / 'events.jsonl')
            with patch.object(journal, '_write', side_effect=OSError('disk full')):
                journal.append({'n': 0})
                with self.assertRaisesRegex(OSError, 'disk full'):
                    journal.flush()
                with self.assertRaisesRegex(OSError, 'disk full'):
                    journal.append({'n': 1})
                with self.assertRaisesRegex(OSError, 'disk full'):
                    journal.close()
                self.assertFalse(journal._thread.is_alive())

    def test_bounded_batches_preserve_concurrent_session_order(self):
        with tempfile.TemporaryDirectory() as directory:
            def generate(session):
                journal = EventJournal(Path(directory) / (session + '.jsonl'), max_pending_bytes=256)
                for n in range(300):
                    journal.append({'session': session, 'n': n})
                journal.close()
            threads = [threading.Thread(target=generate, args=(s,)) for s in ('a', 'b', 'c')]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
                self.assertFalse(thread.is_alive())
            for s in ('a', 'b', 'c'):
                self.assertEqual(read_events(Path(directory) / (s + '.jsonl')),
                                 [{'session': s, 'n': n} for n in range(300)])

    def test_reader_ignores_only_incomplete_tail_including_partial_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            self.assertEqual(read_events(path), [])
            path.write_bytes(b'{"n": 1}\n{"text": "\xed\x95')
            self.assertEqual(read_events(path), [{'n': 1}])
            path.write_bytes(b'{"n": 1}\ninvalid\n')
            with self.assertRaises(json.JSONDecodeError):
                read_events(path)


class TurnLoggingTests(unittest.TestCase):
    def test_full_stream_order_without_per_chunk_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            log = TurnLog(directory, {'session_id': 'stream'})
            expected = []
            def send(event):
                expected.append(log.append(event))
            try:
                with patch.object(log, 'checkpoint', wraps=log.checkpoint) as snapshots:
                    send({'type': 'request', 'payload': {'messages': []}, 'context_manifest': {'blocks': []}})
                    for n in range(600):
                        send(chunk(reasoning_content='생각 '))
                        send(chunk(tool_calls=[{'index': 0, 'function': {'arguments': str(n)}}]))
                        send({'type': 'agent_event', 'agent_id': 'child', 'event': chunk(reasoning='하위 추론')})
                    self.assertEqual(snapshots.call_count, 1)
                    send({'type': 'agent_event', 'agent_id': 'child', 'event': {'type': 'done', 'response': '조언'}})
                    self.assertFalse(log.terminal)
                    send({'type': 'tool_result', 'name': 'test', 'result': '완료'})
                    send(chunk(content='최종 답변'))
                    send({'type': 'done'})
                    self.assertEqual(snapshots.call_count, 4)
                self.assertEqual(read_events(log.journal), expected)
                state = json.loads(log.path.read_text())
                self.assertEqual(state['response'], '최종 답변')
                self.assertEqual(state['reasoning'], '생각 ' * 600)
                self.assertEqual(state['model_calls'][0]['context_manifest'], {'blocks': []})
                self.assertEqual(state['status'], 'completed')
                self.assertFalse(log._journal_writer._thread.is_alive())
            finally:
                log.close()

    def test_all_terminal_outcomes_flush_pending_response_and_reasoning(self):
        for terminal, status in [('done', 'completed'), ('error', 'failed'),
                                 ('cancelled', 'stopped'), ('disconnected', 'disconnected')]:
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory:
                log = TurnLog(directory, {})
                log.append({'type': 'request', 'payload': {}})
                log.append(chunk(content='일부 답변', reasoning='일부 추론'))
                log.append({'type': terminal})
                state = json.loads(log.path.read_text())
                self.assertEqual(state['status'], status)
                self.assertEqual(state['response'], '일부 답변')
                self.assertEqual(state['reasoning'], '일부 추론')
                self.assertEqual(len(read_events(log.journal)), 3)
                self.assertFalse(log._journal_writer._thread.is_alive())


if __name__ == '__main__':
    unittest.main()
