"""Bounded private state for paired sessions, with recoverable public/private commit.

The model writes into a temporary working copy. Publishing session.json replaces
client_state as well; a journal rolls back interrupted commits. Q3p6 keeps its
immutable checkpoints because its experiment revisits historical private states.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import tarfile

from client.state_summary.runtime import write_new
from client.memory_access import VERSIONS


@contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.client_state.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def immutable(session):
    # Other runtimes may keep mutable notes or historical checkpoint references.
    # Only the append-only memory_state runtimes opt into this layout.
    return session.get('client_version') not in VERSIONS or bool(session.get('q3p6_managed'))


def archive_tree(source, destination):
    """Preserve and byte-verify every file before removing an old working copy."""
    if not source.exists():
        return
    if source.is_symlink() or any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('상태 보관 대상에 심볼릭 링크가 있습니다.')
    files = sorted(p for p in source.rglob('*') if p.is_file())
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        # A process may exit after publishing the archive but during source
        # cleanup. Verify all remaining files before completing that cleanup.
        with tarfile.open(destination, 'r:gz') as archive:
            for path in files:
                with archive.extractfile(str(Path(source.name) / path.relative_to(source))) as stored:
                    with path.open('rb') as original:
                        while True:
                            chunk = original.read(1024 * 1024)
                            if stored.read(len(chunk) or 1) != chunk:
                                raise ValueError('기존 보관본과 원본이 다릅니다: ' + str(path))
                            if not chunk:
                                break
        shutil.rmtree(source)
        return
    temporary = destination.with_name(destination.name + '.tmp')
    try:
        with tarfile.open(temporary, 'w:gz', compresslevel=6) as archive:
            archive.add(source, arcname=source.name)
        with tarfile.open(temporary, 'r:gz') as archive:
            members = [m for m in archive.getmembers() if m.isfile()]
            if len(members) != len(files):
                raise ValueError('상태 보관 파일 수가 일치하지 않습니다.')
            for path in files:
                with archive.extractfile(str(Path(source.name) / path.relative_to(source))) as stored:
                    with path.open('rb') as original:
                        while True:
                            chunk = original.read(1024 * 1024)
                            if stored.read(len(chunk) or 1) != chunk:
                                raise ValueError('상태 보관 검증 실패: ' + str(path))
                            if not chunk:
                                break
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        shutil.rmtree(source)
    finally:
        temporary.unlink(missing_ok=True)


def discard(directory, working):
    """Failed attempts remain recoverable without leaving cumulative directories."""
    working = Path(working)
    if working.parent != directory / '.client_pending' or not working.exists():
        return
    archive_tree(working, directory / 'client_failures' / (working.name + '.tar.gz'))
    if working.parent.exists() and not any(working.parent.iterdir()):
        working.parent.rmdir()


def _recover(directory):
    journal = directory / '.client_transaction.json'
    if not journal.exists():
        return
    transaction = json.loads(journal.read_text(encoding='utf-8'))
    current, previous = directory / 'client_state', directory / '.client_previous'
    working = directory / '.client_pending' / transaction['attempt']
    public = directory / 'session.json'
    session = json.loads(public.read_text(encoding='utf-8')) if public.exists() else {}
    committed = session.get('client_state_commit') == transaction['attempt']
    if committed:
        if not current.is_dir():
            raise RuntimeError('확정된 내담자 상태 폴더가 없습니다.')
        if previous.exists():
            shutil.rmtree(previous)
    else:
        if previous.exists():
            if current.exists():
                working.parent.mkdir(parents=True, exist_ok=True)
                os.replace(current, working)
            os.replace(previous, current)
        elif not transaction['had_current'] and not working.exists() and current.exists():
            working.parent.mkdir(parents=True, exist_ok=True)
            os.replace(current, working)
        discard(directory, working)
    journal.unlink()


def recover(directory):
    with locked(directory):
        _recover(directory)


def prepare(directory, session, attempt):
    if immutable(session):
        working = directory / 'client_attempts' / attempt
        source = session.get('client_storage_dir')
        if source:
            shutil.copytree(source, working)
        working.mkdir(parents=True, exist_ok=True)
        return working
    with locked(directory):
        _recover(directory)
        pending = directory / '.client_pending'
        if pending.exists():
            # The application serializes generation per session. Anything left
            # here before a new attempt is an interrupted, uncommitted attempt.
            for abandoned in sorted(pending.iterdir()):
                if abandoned.is_dir():
                    discard(directory, abandoned)
        working = directory / '.client_pending' / attempt
        source = session.get('client_storage_dir')
        if source:
            shutil.copytree(source, working)
        else:
            working.mkdir(parents=True)
        return working


def save(directory, session):
    """Commit private state and public session; recover either side after a crash."""
    source = session.get('client_storage_dir')
    working = Path(source) if source else None
    with locked(directory):
        _recover(directory)
        if working is None or working.parent != directory / '.client_pending':
            write_new(directory / 'session.json', session)
            return
        current, previous = directory / 'client_state', directory / '.client_previous'
        journal = directory / '.client_transaction.json'
        public_path = directory / 'session.json'
        prior = json.loads(public_path.read_text(encoding='utf-8')) if public_path.exists() else {}
        write_new(journal, {'attempt': working.name, 'had_current': current.exists()})
        old_commit = session.get('client_state_commit')
        try:
            if current.exists():
                os.replace(current, previous)
            os.replace(working, current)
            session['client_storage_dir'] = str(current)
            session['client_state_commit'] = working.name
            write_new(directory / 'session.json', session)
        except BaseException:
            _recover(directory)
            if prior.get('client_storage_dir'):
                session['client_storage_dir'] = prior['client_storage_dir']
            else:
                session.pop('client_storage_dir', None)
            if old_commit is None:
                session.pop('client_state_commit', None)
            else:
                session['client_state_commit'] = old_commit
            raise
        _recover(directory)
        if working.parent.exists() and not any(working.parent.iterdir()):
            working.parent.rmdir()
