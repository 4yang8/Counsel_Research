"""Ordered event batches, independent of the streaming response thread."""
from collections import deque
import os
import threading
import time
import json


class EventJournal:
    def __init__(self, path, interval=0.5, max_pending_bytes=4 * 1024 * 1024):
        self.path = path
        self.interval = interval
        self.max_pending_bytes = max_pending_bytes
        self._condition = threading.Condition()
        self._pending = deque()
        self._pending_bytes = 0
        self._since = None
        self._sequence = self._durable = self._requested = 0
        self._thread = None
        self._error = None
        self._closed = False

    def _check(self):
        if self._error is not None:
            raise OSError('이벤트 로그 저장 실패: ' + str(self._error)) from self._error

    def append(self, event):
        line = (json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8')
        with self._condition:
            self._check()
            if self._closed:
                raise OSError('종료된 이벤트 로그에는 기록할 수 없습니다.')
            # Bound memory without dropping events. One oversized event is allowed.
            while self._pending and self._pending_bytes + len(line) > self.max_pending_bytes:
                self._requested = self._sequence
                self._condition.notify_all()
                self._condition.wait()
                self._check()
            self._sequence += 1
            self._pending.append((self._sequence, line))
            self._pending_bytes += len(line)
            if self._since is None:
                self._since = time.monotonic()
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='event-journal', daemon=True)
                self._thread.start()
            self._condition.notify_all()

    def flush(self):
        """Wait until all events accepted so far have reached fsync."""
        with self._condition:
            self._check()
            target = self._sequence
            self._requested = target
            self._condition.notify_all()
            while self._durable < target:
                self._condition.wait()
                self._check()

    def close(self):
        try:
            self.flush()
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()
            if self._thread is not None:
                self._thread.join()

    def _write(self, batch):
        with self.path.open('ab') as stream:
            stream.write(b''.join(line for _, line in batch))
            stream.flush()
            os.fsync(stream.fileno())

    def _run(self):
        try:
            while True:
                with self._condition:
                    while True:
                        if self._closed and not self._pending:
                            return
                        if self._pending:
                            delay = self.interval - (time.monotonic() - self._since)
                            if delay <= 0 or self._requested > self._durable or self._pending_bytes >= self.max_pending_bytes:
                                break
                            self._condition.wait(delay)
                        else:
                            self._condition.wait()
                    batch = list(self._pending)
                    self._pending.clear()
                    self._pending_bytes = 0
                    self._since = None
                    self._condition.notify_all()
                self._write(batch)
                with self._condition:
                    self._durable = batch[-1][0]
                    self._condition.notify_all()
        except Exception as error:
            with self._condition:
                self._error = error
                self._condition.notify_all()


def read_events(path):
    """A reader may catch the writer mid-batch; ignore only an unfinished tail."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    lines = raw.split(b'\n')[:-1]
    return [json.loads(line) for line in lines if line.strip()]
