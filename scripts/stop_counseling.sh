#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${COUNSELING_PYTHON:-${GEMMA_TEST_PYTHON:-python3}}" - "$script_dir/../src/app/app_server.py" "$@" <<'PY'
import argparse
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

server_script = Path(sys.argv[1]).resolve()
parser = argparse.ArgumentParser(description='상담 실습 UI 서버를 종료합니다.')
parser.add_argument('--port', type=int, default=8891, help='UI 포트 (기본값: 8891)')
args = parser.parse_args(sys.argv[2:])
if not 1 <= args.port <= 65535:
    parser.error('포트는 1~65535 사이여야 합니다.')

try:
    result = subprocess.run(['ss', '-ltnp', 'sport = :{}'.format(args.port)],
                            check=True, capture_output=True, text=True)
except (OSError, subprocess.CalledProcessError) as error:
    raise SystemExit('포트 조회 실패: {}'.format(error))
lines = result.stdout.strip().splitlines()[1:]
if not lines:
    print('포트 {}에 실행 중인 서버가 없습니다.'.format(args.port))
    raise SystemExit(0)
pids = sorted({int(pid) for pid in re.findall(r'pid=(\d+)', '\n'.join(lines))})
if not pids:
    raise SystemExit('포트 {}의 프로세스를 확인할 수 없어 종료하지 않았습니다.'.format(args.port))

def matches(pid):
    process = Path('/proc') / str(pid)
    try:
        if process.stat().st_uid != os.getuid():
            return False
        cwd = (process / 'cwd').resolve(strict=True)
        command = (process / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
        direct = any((Path(arg) if Path(arg).is_absolute() else cwd / arg).resolve() == server_script
                     for arg in command[1:] if arg.endswith('app_server.py'))
        if direct:
            return True
        if not any(command[index:index + 2] == ['-m', 'app.app_server']
                   for index in range(1, len(command) - 1)):
            return False
        # Read only the module search path, never display the process environment.
        search = next((entry[len(b'PYTHONPATH='):].decode()
                       for entry in (process / 'environ').read_bytes().split(b'\0')
                       if entry.startswith(b'PYTHONPATH=')), '')
        for entry in [str(cwd), *search.split(os.pathsep)]:
            directory = Path(entry) if entry else cwd
            if not directory.is_absolute():
                directory = cwd / directory
            candidate = directory / 'app/app_server.py'
            if candidate.is_file():
                return candidate.resolve() == server_script
        return False
    except (OSError, UnicodeError):
        return False

def alive(pid):
    try:
        state = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
        return state != 'Z'
    except FileNotFoundError:
        return False

for pid in pids:
    if not matches(pid):
        raise SystemExit('PID {}는 이 프로젝트의 상담 UI 서버가 아니므로 종료하지 않았습니다.'.format(pid))
for pid in pids:
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        continue
    deadline = time.monotonic() + 8
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if alive(pid):
        if not matches(pid):
            raise SystemExit('PID {}의 프로세스가 변경되어 종료를 중단했습니다.'.format(pid))
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 3
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
    if alive(pid):
        raise SystemExit('PID {} 종료를 확인하지 못했습니다.'.format(pid))
    print('상담 UI 서버 종료 완료: PID {} / 포트 {}'.format(pid, args.port))
PY
