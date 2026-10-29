"""Discover saved sessions without changing their transcripts or issuing inference."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from storage.event_journal import read_events
from project_paths import historical_storage_root

ID = re.compile(r'[a-zA-Z0-9_-]{1,80}')


def key_for(root, kind, sid):
    return hashlib.sha256(f'{root}\0{kind}\0{sid}'.encode()).hexdigest()


def read(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('일반 세션 파일만 읽을 수 있습니다.')
    return json.loads(path.read_text(encoding='utf-8'))


def discover(server):
    rows, locations = [], {}
    with server.storage_lock:
        roots = [Path(server.storage_dir), *(Path(p) for p in sorted(server.storage_roots)
                                            if Path(p) != Path(server.storage_dir))]
    for root in roots:
        for kind in ('paired', 'basic', 'state_summary', 'Q1', 'Q1_s1', 'Q1_s2', 'Q1a', 'Q2', 'Q2a', 'Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'):
            base = root / kind
            if base.is_symlink() or not base.is_dir():
                continue
            for directory in base.iterdir():
                if directory.is_symlink() or not directory.is_dir() or not ID.fullmatch(directory.name):
                    continue
                try:
                    if kind == 'paired':
                        path = directory / 'session.json'
                        data = read(path)
                        if data.get('session_id') != directory.name:
                            continue
                        public = data.get('public_dialogue', [])
                        title = public[data.get('initial_public_count', len(public)) - 1]['content'] if public else directory.name
                        version = data.get('client_version', 'basic')
                        model = data.get('connection', {}).get('model', '')
                        modified = path.stat().st_mtime
                        count = len(data.get('steps', []))
                    else:
                        paths = []
                        for path in sorted(directory.glob('*.json')):
                            if path.is_symlink():
                                continue
                            try:
                                data = read(path)
                            except (OSError, ValueError):
                                continue
                            if data.get('session_id') == directory.name and data.get('turn_id') and data.get('input', {}).get('messages'):
                                paths.append(path)
                        if not paths:
                            continue
                        first, last = read(paths[0]), read(paths[-1])
                        title = first['input']['messages'][-1].get('content', directory.name)
                        model = last['input'].get('model', last.get('request', {}).get('model', ''))
                        version, count = kind, len(paths)
                        modified = paths[-1].stat().st_mtime
                    key = key_for(root, kind, directory.name)
                    locations[key] = (root, kind, directory.name)
                    historical = historical_storage_root(root)
                    if historical is not None:
                        locations[key_for(historical, kind, directory.name)] = (root, kind, directory.name)
                    rows.append({'key': key, 'session_id': directory.name, 'kind': kind,
                                 'version': version, 'model': model, 'title': str(title)[:90],
                                 'count': count, 'modified': modified, 'storage_dir': str(root)})
                except (OSError, ValueError, KeyError, IndexError, TypeError):
                    # A damaged session must not hide all other saved conversations.
                    continue
    return sorted(rows, key=lambda row: row['modified'], reverse=True), locations


def resolve(server, key):
    _, locations = discover(server)
    if key not in locations:
        raise ValueError('저장된 대화 세션을 찾을 수 없습니다.')
    return locations[key]


def configure(server, config, body):
    if not body.get('session_key'):
        return
    root, kind, sid = resolve(server, body['session_key'])
    if kind == 'paired' or body.get('session_id') != sid or body.get('experiment_version') != kind:
        raise ValueError('불러온 대화의 세션과 버전이 일치하지 않습니다.')
    config['_storage_dir'] = str(root)


def handle_get(handler):
    parts = urlsplit(handler.path)
    if parts.path not in ('/api/sessions', '/api/sessions/standalone'):
        return False
    try:
        if parts.path == '/api/sessions':
            rows, _ = discover(handler.server)
            handler.json_response({'sessions': rows})
        else:
            params = parse_qs(parts.query)
            root, kind, sid = resolve(handler.server, params.get('key', [''])[0])
            if kind == 'paired':
                raise ValueError('양방향 세션은 양방향 API로 읽으세요.')
            turns = []
            for path in sorted((root / kind / sid).glob('*.json')):
                if path.is_symlink():
                    continue
                try:
                    data = read(path)
                except (OSError, ValueError):
                    continue
                if data.get('session_id') != sid or not data.get('turn_id') or not data.get('input', {}).get('messages'):
                    continue
                journal = path.with_suffix('.events.jsonl')
                events = read_events(journal) if journal.is_file() and not journal.is_symlink() else []
                turns.append({'record': data, 'events': events})
            handler.json_response({'session_id': sid, 'version': kind, 'turns': turns})
    except (OSError, ValueError, KeyError) as error:
        handler.json_response({'error': str(error)}, 400)
    return True
