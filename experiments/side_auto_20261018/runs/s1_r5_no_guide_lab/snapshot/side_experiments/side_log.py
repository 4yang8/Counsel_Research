"""Keep every streamed event; checkpoint the growing JSON at most twice/second."""
from datetime import datetime, timezone
import json
import os
import time


def log_factory(base):
    class StreamingLog(base):
        def append(self, event):
            now = datetime.now(timezone.utc).isoformat()
            event = {**event, 'saved_at': now}
            kind = event['type']
            chunk = kind == 'chunk' or kind == 'agent_event' and event.get('event', {}).get('type') == 'chunk'
            checkpoint = not chunk or time.monotonic() - getattr(self, '_checkpoint', 0) >= .5
            with self.journal.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
                stream.flush()
                if checkpoint:
                    os.fsync(stream.fileno())
            self.state.setdefault('started_at', now)
            self.state['updated_at'] = now
            if kind == 'request':
                self.state.update(request=event['payload'], response='', reasoning='')
                if 'url' in event:
                    self.state['url'] = event['url']
                self.state.setdefault('model_calls', []).append({'iteration': event.get('iteration', 1),
                    'request': event['payload'], 'response': '', 'reasoning': ''})
            elif kind == 'inspection':
                self.state['inspection'] = event
                if self.state.get('model_calls'):
                    self.state['model_calls'][-1]['inspection'] = event
            elif kind == 'chunk':
                data = event['data']
                for choice in data.get('choices', []):
                    if choice.get('index', 0) != 0:
                        continue
                    delta = choice.get('delta', {})
                    self.state['response'] += delta.get('content') or ''
                    self.state['reasoning'] += delta.get('reasoning_content') or delta.get('reasoning') or ''
                    if choice.get('finish_reason'):
                        self.state['finish_reason'] = choice['finish_reason']
                if data.get('usage'):
                    self.state['usage'] = data['usage']
                if self.state.get('model_calls'):
                    self.state['model_calls'][-1].update(response=self.state['response'], reasoning=self.state['reasoning'])
                    if data.get('usage'):
                        self.state['model_calls'][-1]['usage'] = data['usage']
            elif kind in ('tool_start', 'tool_result'):
                self.state.setdefault('tool_events', []).append(event)
            elif kind in ('done', 'error', 'disconnected', 'cancelled'):
                if kind == 'done':
                    for key in ('history', 'response', 'reasoning', 'usage', 'finish_reason', 'tool_calls_used'):
                        if key in event:
                            self.state[key] = event[key]
                self.state['status'] = {'done': 'completed' if self.state['response'] else 'empty',
                    'error': 'failed', 'disconnected': 'disconnected', 'cancelled': 'stopped'}[kind]
                self.state['finished_at'] = now
                if event.get('error'):
                    self.state['error'] = event['error']
            if checkpoint:
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, self.path)
                self._checkpoint = time.monotonic()
            self.terminal = kind in ('done', 'error', 'disconnected', 'cancelled')
            return event
    return StreamingLog
