#!/usr/bin/env python3
"""Move memory-access sessions to client_state and losslessly archive old attempts.

Dry-run by default. Stop the application before --apply. Public dialogue, model
requests, reasoning, failed attempts and historical snapshots are preserved.
Other client runtimes (especially Q3p6 checkpoints) are left untouched.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))
from storage import paired_state
from client.state_summary.runtime import write_new


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def check_offline():
    """Do not migrate underneath an app process, including one using old code."""
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / 'cmdline').read_bytes().split(b'\0')
            app = any(a in (b'app.app_server', b'app.server') or a.endswith(b'/app_server.py') for a in args)
            if app and (proc / 'cwd').resolve() == PROJECT:
                raise RuntimeError('앱 서버를 종료한 후 정리하세요: PID ' + proc.name)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue


def inspect(directory):
    session = json.loads((directory / 'session.json').read_text(encoding='utf-8'))
    if paired_state.immutable(session):
        return session, None, 'other_runtime'
    attempts = directory / 'client_attempts'
    if not attempts.is_dir():
        return session, None, 'already_compact'
    if not session.get('client_storage_dir'):
        return session, None, 'no_committed_state'
    source = Path(session['client_storage_dir'])
    if not source.resolve().is_relative_to(directory.resolve()):
        raise ValueError('세션 외부의 상태 경로: ' + str(source))
    state = source / session['client_version'] / session['session_id'] / 'memory_state.json'
    if not state.is_file():
        raise ValueError('현재 상태 없음: ' + str(state))
    return session, state, 'candidate'


def compact(directory, apply=False):
    session, original, status = inspect(directory)
    if status != 'candidate':
        return {'session': directory.name, 'status': status}
    attempts = directory / 'client_attempts'
    files = [p for p in attempts.rglob('*') if p.is_file()]
    result = {'session': directory.name, 'status': 'planned', 'files': len(files),
              'attempt_bytes': sum(p.stat().st_size for p in files), 'state_sha256': sha(original)}
    if not apply:
        return result
    check_offline()
    archive_dir = directory / 'state_archive'
    archive_dir.mkdir(exist_ok=True)
    before = archive_dir / 'session.before.json'
    if not before.exists():
        write_new(before, session)
    snapshot = json.loads(json.dumps(session))
    current = directory / 'client_state'
    if Path(session['client_storage_dir']) != current:
        if current.exists():
            raise ValueError('사용되지 않는 client_state가 이미 있습니다: ' + str(current))
        work = paired_state.prepare(directory, session, 'migration_' + uuid.uuid4().hex)
        session['client_storage_dir'] = str(work)
        paired_state.save(directory, session)
    saved = json.loads((directory / 'session.json').read_text(encoding='utf-8'))
    for key in set(snapshot) | set(saved):
        if key not in ('client_storage_dir', 'client_state_commit') and snapshot.get(key) != saved.get(key):
            raise ValueError('세션 내용이 변경되었습니다: ' + key)
    target = current / session['client_version'] / session['session_id'] / 'memory_state.json'
    if sha(target) != result['state_sha256']:
        raise ValueError('최신 상태 원본 불일치: ' + str(target))
    archive = archive_dir / ('client_attempts_' + uuid.uuid4().hex[:8] + '.tar.gz')
    # archive_tree checks every archived byte against the original before removal.
    paired_state.archive_tree(attempts, archive)
    result.update(status='compacted', archive=str(archive.relative_to(PROJECT)),
                  archive_bytes=archive.stat().st_size, current_bytes=target.stat().st_size,
                  archive_sha256=sha(archive))
    write_new(archive_dir / 'manifest.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--session', help='Only this paired session ID')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    check_offline()
    paired = PROJECT / 'runs/paired'
    paths = sorted(paired.glob('*/session.json'))
    if args.session:
        paths = [p for p in paths if p.parent.name == args.session]
        if not paths:
            parser.error('세션을 찾을 수 없습니다.')
    results = []
    for index, path in enumerate(paths, 1):
        try:
            results.append(compact(path.parent, apply=args.apply))
        except Exception as error:
            results.append({'session': path.parent.name, 'status': 'error', 'error': str(error)})
        if args.report:
            write_new(args.report, {'apply': args.apply, 'results': results})
        if index % 25 == 0:
            print('progress', index, '/', len(paths), flush=True)
    print(json.dumps({'statuses': dict(Counter(r['status'] for r in results)),
                      'attempt_bytes': sum(r.get('attempt_bytes', 0) for r in results),
                      'archive_bytes': sum(r.get('archive_bytes', 0) for r in results),
                      'current_bytes': sum(r.get('current_bytes', 0) for r in results),
                      'errors': [r for r in results if r['status'] == 'error']}, ensure_ascii=False, indent=2))
    return int(any(r['status'] == 'error' for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
