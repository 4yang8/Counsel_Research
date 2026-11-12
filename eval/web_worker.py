"""Durable web evaluation worker; stop-file cancels the entire CLI process group."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def run(directory):
    job = json.loads((directory/'job.json').read_text())
    status = directory/'execution.json'
    stop = directory/'stop'
    write(status, {'status':'running','started_at':time.time()})
    with (directory/'console.log').open('a',encoding='utf-8') as log:
        try:
            if stop.exists():
                write(status,{'status':'cancelled'}); return
            proc = subprocess.Popen(job['command'], stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, cwd=job['cwd'], env={**os.environ,'PYTHONUNBUFFERED':'1'})
            cancelled = False
            while proc.poll() is None:
                if stop.exists():
                    cancelled = True
                    os.killpg(proc.pid, signal.SIGTERM)
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired: pass
                    # A descendant can outlive the CLI parent. Always clear the group.
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    proc.wait(); break
                time.sleep(.25)
            write(status,{'status':'cancelled' if cancelled else 'completed' if proc.returncode==0 else 'failed',
                          'exit_code':proc.returncode,'finished_at':time.time()})
        except Exception as exc:
            traceback.print_exc(file=log)
            write(status,{'status':'failed','error':str(exc),'finished_at':time.time()})


if __name__=='__main__':
    run(Path(sys.argv[1]))
