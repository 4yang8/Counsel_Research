"""Storage-root explorer. Mutations are explicit and deletion is reversible."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import threading
import uuid
from urllib.parse import urlsplit, parse_qs

from project_paths import PROJECT_ROOT
HERE = PROJECT_ROOT
PROJECT = PROJECT_ROOT
LIMIT = 2 * 1024 * 1024


def storage_path(value, config_path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('저장 경로를 입력하세요.')
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(config_path).resolve().parent / path
    path = path.resolve()
    if not any(path.is_relative_to(base) and path != base for base in (PROJECT, Path('/tmp'))):
        raise ValueError('프로젝트 폴더 또는 /tmp의 하위 폴더를 저장 경로로 지정하세요.')
    if path.is_relative_to(PROJECT) and any(part in ('.git', '.codex', '.agents', '.aws') for part in path.relative_to(PROJECT).parts):
        raise ValueError('이 폴더는 저장 경로로 사용할 수 없습니다.')
    return path


def initialize(server):
    server.storage_lock = threading.RLock()
    server.storage_jobs = 0
    server.storage_roots = {str(Path(server.storage_dir).resolve())}
    config = json.loads(server.config_path.read_text())
    server.storage_roots.update(str(storage_path(path, server.config_path)) for path in config.get('storage_history', []))
    server.paired_locations = {}


@contextmanager
def generation(server):
    with server.storage_lock:
        server.storage_jobs += 1
    try:
        yield
    finally:
        with server.storage_lock:
            server.storage_jobs -= 1


def path_in(root, value, allow_root=False):
    if not isinstance(value, str) or Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('저장 폴더 안의 상대 경로를 사용하세요.')
    if '.trash' in Path(value).parts:
        raise ValueError('휴지통은 복원 기능으로 접근하세요.')
    path = root / value
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink():
            raise ValueError('심볼릭 링크를 통한 접근은 지원하지 않습니다.')
    path = path.resolve()
    if not path.is_relative_to(root) or (path == root and not allow_root):
        raise ValueError('저장 경로 자체를 수정하거나 삭제할 수 없습니다.')
    return path


def info(server):
    return {'storage_dir': str(server.storage_dir), 'busy': server.storage_jobs > 0,
            'note': '경로 변경은 새 세션부터 적용되며 기존 세션은 원래 위치를 유지합니다.'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def handle_get(handler):
    parts = urlsplit(handler.path)
    if not parts.path.startswith('/api/files/'):
        return False
    params = parse_qs(parts.query)
    server = handler.server
    try:
        with server.storage_lock:
            root = Path(server.storage_dir).resolve()
            if parts.path == '/api/files/storage':
                result = info(server)
            elif parts.path == '/api/files/list':
                path = path_in(root, params.get('path', [''])[0], True)
                if not path.is_dir():
                    raise ValueError('폴더가 존재하지 않습니다.')
                rows = []
                for entry in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    if entry.name == '.trash':
                        continue
                    link = entry.is_symlink()
                    stat = entry.lstat()
                    rows.append({'name': entry.name, 'path': str(entry.relative_to(root)),
                                 'type': 'symlink' if link else 'directory' if entry.is_dir() else 'file',
                                 'size': stat.st_size, 'modified': stat.st_mtime})
                result = {**info(server), 'path': str(path.relative_to(root)) if path != root else '', 'entries': rows}
            elif parts.path == '/api/files/read':
                path = path_in(root, params.get('path', [''])[0])
                if not path.is_file() or path.stat().st_size > LIMIT:
                    raise ValueError('편집할 파일은 2 MB 이하의 텍스트 파일이어야 합니다.')
                raw = path.read_bytes()
                if b'\0' in raw:
                    raise ValueError('바이너리 파일은 텍스트 편집을 지원하지 않습니다.')
                result = {'path': str(path.relative_to(root)), 'content': raw.decode('utf-8'), 'revision': digest(raw)}
            else:
                raise ValueError('지원하지 않는 파일 조회입니다.')
        handler.json_response(result)
    except (ValueError, OSError, UnicodeError) as error:
        handler.json_response({'error': str(error)}, 400)
    return True


def handle_post(handler):
    if not handler.path.startswith('/api/files/'):
        return False
    server = handler.server
    try:
        if handler.headers.get('X-Gemma-Request') != '1':
            raise ValueError('요청 헤더가 없습니다.')
        size = int(handler.headers.get('Content-Length', 0))
        if not 0 < size <= LIMIT + 65536:
            raise ValueError('파일 요청 크기를 확인하세요.')
        body = json.loads(handler.rfile.read(size))
        if not isinstance(body, dict):
            raise ValueError('파일 요청은 JSON 객체여야 합니다.')
        with server.storage_lock:
            if server.storage_jobs:
                handler.json_response({'error': '생성 중에는 저장 경로와 파일을 변경할 수 없습니다.'}, 409)
                return True
            root = Path(server.storage_dir).resolve()
            if handler.path == '/api/files/storage':
                target = storage_path(body.get('storage_dir'), server.config_path)
                target.mkdir(parents=True, exist_ok=True)
                config = json.loads(server.config_path.read_text())
                config['storage_dir'] = str(target)
                config['storage_history'] = sorted(server.storage_roots | {str(target)})
                from client.state_summary.runtime import write_new
                write_new(server.config_path, config)
                server.storage_dir = target
                server.storage_roots.add(str(target))
                result = info(server)
            elif handler.path in ('/api/files/create', '/api/files/write', '/api/files/delete'):
                path = path_in(root, body.get('path'))
                if handler.path == '/api/files/create':
                    if body.get('type') == 'directory':
                        path.mkdir(exist_ok=False)
                    elif body.get('type') == 'file':
                        content = body.get('content', '')
                        if not isinstance(content, str) or len(content.encode()) > LIMIT:
                            raise ValueError('텍스트 파일 크기는 2 MB 이하여야 합니다.')
                        with path.open('x', encoding='utf-8') as stream:
                            stream.write(content)
                    else:
                        raise ValueError('파일 또는 폴더 종류를 선택하세요.')
                    result = {'path': str(path.relative_to(root)), 'created': True}
                elif handler.path == '/api/files/write':
                    if not path.is_file() or path.stat().st_size > LIMIT:
                        raise ValueError('편집할 파일을 확인하세요.')
                    raw = path.read_bytes()
                    if b'\0' in raw:
                        raise ValueError('바이너리 파일은 편집할 수 없습니다.')
                    if body.get('revision') != digest(raw):
                        handler.json_response({'error': '파일이 변경되었습니다. 다시 읽은 뒤 편집하세요.'}, 409)
                        return True
                    content = body.get('content')
                    if not isinstance(content, str) or len(content.encode()) > LIMIT:
                        raise ValueError('텍스트 파일 크기는 2 MB 이하여야 합니다.')
                    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
                    try:
                        temporary.write_text(content, encoding='utf-8')
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
                    result = {'path': str(path.relative_to(root)), 'revision': digest(content.encode()), 'saved': True}
                else:
                    if not path.exists():
                        raise ValueError('삭제할 항목이 존재하지 않습니다.')
                    token = uuid.uuid4().hex
                    trash = root / '.trash' / token
                    if (root / '.trash').is_symlink():
                        raise ValueError('휴지통 경로를 확인하세요.')
                    trash.mkdir(parents=True)
                    (trash / 'metadata.json').write_text(json.dumps({'path': str(path.relative_to(root))}))
                    path.rename(trash / 'item')
                    result = {'deleted': True, 'trash_id': token, 'path': str(path.relative_to(root))}
            elif handler.path == '/api/files/restore':
                token = body.get('trash_id', '')
                if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
                    raise ValueError('복원할 삭제 기록을 확인하세요.')
                trash = root / '.trash' / token
                if (root / '.trash').is_symlink() or trash.is_symlink():
                    raise ValueError('휴지통 경로를 확인하세요.')
                metadata = json.loads((trash / 'metadata.json').read_text())
                path = path_in(root, metadata['path'])
                if path.exists():
                    raise ValueError('같은 이름의 항목이 있어 복원할 수 없습니다.')
                path.parent.mkdir(parents=True, exist_ok=True)
                (trash / 'item').rename(path)
                shutil.rmtree(trash)
                result = {'restored': True, 'path': metadata['path']}
            else:
                raise ValueError('지원하지 않는 파일 작업입니다.')
        handler.json_response(result)
    except (ValueError, OSError, UnicodeError, TypeError) as error:
        handler.json_response({'error': str(error)}, 400)
    return True
