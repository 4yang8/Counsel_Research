"""Small synchronous MCP stdio client for the official Filesystem server."""
from collections import deque
import json
from pathlib import Path
import queue
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent
READ_TOOLS = {'list_directory', 'read_text_file', 'read_multiple_files', 'search_files',
              'get_file_info', 'list_allowed_directories', 'directory_tree'}
WRITE_TOOLS = {'write_file', 'edit_file', 'create_directory'}


class Cancelled(Exception):
    pass


def options(config):
    opts = dict(config.get('filesystem', {}))
    if not opts.get('enabled', False):
        return None
    folder = Path(opts.get('directory', 'playground'))
    opts['directory'] = str((ROOT / folder).resolve())
    if not Path(opts['directory']).is_dir():
        raise ValueError('Filesystem 실습 폴더가 없습니다: ' + opts['directory'])
    command = opts.get('command', ['node', 'node_modules/@modelcontextprotocol/server-filesystem/dist/index.js'])
    if not isinstance(command, list) or not command or not all(isinstance(s, str) and s for s in command):
        raise ValueError('filesystem.command는 실행 명령 배열이어야 합니다.')
    opts['command'] = command
    for key, default, maximum in [('timeout_seconds', 20, 300), ('max_calls', 8, 50), ('max_result_chars', 16000, 200000)]:
        value = opts.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'filesystem.{key}: 1 ~ {maximum} 정수여야 합니다.')
        opts[key] = value
    allowed = opts.get('allowed_tools', sorted(READ_TOOLS | WRITE_TOOLS))
    if not isinstance(allowed, list) or not allowed or any(not isinstance(t, str) or t not in READ_TOOLS | WRITE_TOOLS for t in allowed):
        raise ValueError('filesystem.allowed_tools에 지원하지 않는 도구가 있습니다.')
    opts['allowed_tools'] = allowed
    return opts


class FilesystemClient:
    def __init__(self, opts, cancelled=lambda: False):
        self.opts = opts
        self.cancelled = cancelled
        self.sequence = 0
        self.inbox = queue.Queue()
        self.errors = deque(maxlen=12)
        self.process = None
        self.tools = []

    def __enter__(self):
        try:
            self.process = subprocess.Popen([*self.opts['command'], self.opts['directory']],
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', bufsize=1)
            self.readers = [threading.Thread(target=self._read, daemon=True),
                            threading.Thread(target=self._stderr, daemon=True)]
            for thread in self.readers:
                thread.start()
            result = self.rpc('initialize', {'protocolVersion':'2025-11-25', 'capabilities':{},
                'clientInfo':{'name':'gemma-test','version':'1.0.0'}})
            if result.get('protocolVersion') not in ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'):
                raise RuntimeError('지원하지 않는 MCP 프로토콜: ' + str(result.get('protocolVersion')))
            self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
            cursor = None
            for _ in range(20):
                page = self.rpc('tools/list', {'cursor':cursor} if cursor else {})
                self.tools.extend(t for t in page['tools'] if t['name'] in self.opts['allowed_tools'])
                cursor = page.get('nextCursor')
                if not cursor:
                    break
            else:
                raise RuntimeError('MCP 도구 목록 페이지 제한 초과')
            found = {t['name'] for t in self.tools}
            missing = set(self.opts['allowed_tools']) - found
            if missing:
                raise RuntimeError('MCP에 없는 도구: ' + ', '.join(sorted(missing)))
            return self
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                self.inbox.put(json.loads(line))
        except Exception as error:
            self.inbox.put(error)
        finally:
            self.inbox.put(None)

    def _stderr(self):
        for line in self.process.stderr:
            self.errors.append(line.strip())

    def send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
        self.process.stdin.flush()

    def rpc(self, method, params):
        if self.cancelled():
            raise Cancelled('도구 실행 전에 중단했습니다.')
        self.sequence += 1
        request_id = self.sequence
        self.send({'jsonrpc':'2.0','id':request_id,'method':method,'params':params})
        deadline = time.monotonic() + self.opts['timeout_seconds']
        while True:
            if self.cancelled():
                raise Cancelled('도구 실행을 중단했습니다. 이미 실행된 파일 변경은 유지됩니다.')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'MCP {method} 시간 초과. 실행 여부는 파일과 기록에서 확인하세요.')
            try:
                message = self.inbox.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if message is None:
                raise RuntimeError('MCP 서버 종료. gemma_test에서 npm ci를 실행했는지 확인하세요. ' + '\n'.join(self.errors))
            if isinstance(message, Exception):
                raise RuntimeError('MCP 메시지 오류: ' + str(message))
            if 'method' in message:
                if 'id' in message:
                    self.send({'jsonrpc':'2.0', 'id':message['id'], **(
                        {'result':{}} if message['method']=='ping' else
                        {'error':{'code':-32601,'message':'Unsupported client method'}})})
                continue
            if message.get('id') == request_id:
                if 'error' in message:
                    raise ValueError('MCP: ' + json.dumps(message['error'], ensure_ascii=False))
                return message['result']

    def definitions(self):
        return [{'type':'function','function':{'name':t['name'], 'description':t.get('description',''),
                                             'parameters':t['inputSchema']}} for t in self.tools]

    def call(self, name, arguments):
        if name not in {t['name'] for t in self.tools}:
            raise ValueError('허용되지 않은 도구: ' + name)
        if not isinstance(arguments, dict):
            raise ValueError('도구 인자는 JSON 객체여야 합니다.')
        # The MCP server also enforces the configured root. Resolve symlinks here
        # so denied calls are rejected before execution, including write targets.
        root = Path(self.opts['directory'])
        values = [arguments[k] for k in ('path',) if k in arguments]
        if 'paths' in arguments:
            if not isinstance(arguments['paths'], list):
                raise ValueError('paths는 배열이어야 합니다.')
            values.extend(arguments['paths'])
        for value in values:
            if not isinstance(value, str):
                raise ValueError('파일 경로는 문자열이어야 합니다.')
            candidate = Path(value)
            if not candidate.is_absolute():
                raise ValueError('실습 폴더 안의 절대 경로를 사용하세요.')
            if not candidate.resolve().is_relative_to(root):
                raise ValueError('실습 폴더 밖의 경로에는 접근할 수 없습니다.')
        return self.rpc('tools/call', {'name':name, 'arguments':arguments})

    def close(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for thread in getattr(self, 'readers', []):
            thread.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def __exit__(self, *args):
        self.close()
