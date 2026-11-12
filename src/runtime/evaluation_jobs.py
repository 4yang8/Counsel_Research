"""HTTP control plane for the existing blinded eval CLI. No shell command input."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit, urlencode

from project_paths import PROJECT_ROOT
from runtime import job_ids, profile_batch
from counselor.P1.runtime import write

EVAL = PROJECT_ROOT / 'eval'
LOCK = threading.RLock()
ARTIFACTS = {'report.md','summary.json','metrics.csv','dialogue_metrics.csv','ratings.csv','events.csv','tasks.csv','paired_differences.csv'}


def json_read(path): return json.loads(path.read_text(encoding='utf-8'))
def json_write(path, value): write(path, json.dumps(value,ensure_ascii=False,indent=2))


def path(jid):
    if not job_ids.valid(jid): raise ValueError('평가 작업 ID가 올바르지 않습니다.')
    return EVAL/'web_jobs'/jid


def process_token(pid):
    try:
        text=Path(f'/proc/{pid}/stat').read_text()
        fields=text[text.rfind(')')+2:].split()
        return fields[19] if fields[0]!='Z' else None
    except (OSError,IndexError): return None


def catalog(server):
    rows=[]
    with server.batch_lock:
        for root in sorted(server.storage_roots):
            for p in (Path(root)/'profile_batches').glob('*/job.json'):
                job=profile_batch.read(server,p.parent.name)
                eligible=job.get('mode')=='compare' and job['status']=='completed' and bool(job['rows']) and all(r['status']=='completed' for r in job['rows'])
                rows.append({'id':job['id'],'created_at':job['created_at'],'status':job['status'],
                             'client':job['versions'][0] if len(job['versions'])==1 else ', '.join(job['versions']),
                             'counselor':job['request']['counselor_version'],'profiles':len(job['profiles']),
                             'turns':job['options']['turns'],'fork_turn':job['options'].get('fork_turn'),
                             'eligible':eligible,'reason':'' if eligible else '완료된 공통 생성·분기 배치만 평가할 수 있습니다.'})
    return sorted(rows,key=lambda r:(r['created_at'],r['id']),reverse=True)


def choose(rows, body):
    mode=body.get('selection','latest')
    if mode not in ('latest','explicit'): raise ValueError('평가 대상 선택 방식을 확인하세요.')
    if mode=='explicit':
        ids=body.get('batches',[])
        if not isinstance(ids,list) or not ids or any(not isinstance(v,str) for v in ids) or len(set(ids))!=len(ids):
            raise ValueError('평가할 배치를 하나 이상 선택하세요.')
        chosen=[r for r in rows if r['id'] in ids and r['eligible']]
        if len(chosen)!=len(ids): raise ValueError('미완료·누락 배치는 평가할 수 없습니다.')
        return chosen
    clients=body.get('clients',[]); counselors=body.get('counselors',[])
    if not isinstance(clients,list) or not clients or not isinstance(counselors,list) or not counselors:
        raise ValueError('평가할 내담자와 상담사 버전을 각각 선택하세요.')
    if any(not isinstance(v,str) for v in clients+counselors): raise ValueError('버전 이름을 확인하세요.')
    chosen=[]; seen=set()
    for r in rows:
        key=(r['client'],r['counselor'])
        if r['eligible'] and r['client'] in clients and r['counselor'] in counselors and key not in seen:
            seen.add(key);chosen.append(r)
    missing=[f'{c} × {s}' for c in clients for s in counselors if (c,s) not in seen]
    if missing: raise ValueError('완료 배치가 없는 조합: '+', '.join(missing)+'. 버전을 조정하거나 배치를 직접 선택하세요.')
    return chosen


def python():
    override=os.environ.get('EVAL_PYTHON')
    candidate=Path('/home2/sjyang114/miniconda3/envs/NLP/bin/python')
    return override or (str(candidate) if candidate.is_file() else sys.executable)


def read(jid):
    folder=path(jid); job=json_read(folder/'job.json'); result=copy.deepcopy(job)
    execution=json_read(folder/'execution.json') if (folder/'execution.json').exists() else {'status':'starting'}
    if execution['status'] in ('starting','running'):
        pid=job.get('pid'); token=job.get('process_token')
        if pid and token and process_token(pid)!=token: execution={'status':'interrupted','error':'평가 프로세스가 종료됐습니다. 로그를 확인하고 재개하세요.'}
        if (folder/'stop').exists() and execution['status']=='running': execution['status']='stopping'
    result.update(execution)
    run=EVAL/'results'/job['result_id']
    progress=json_read(run/'progress.json') if (run/'progress.json').exists() else {}
    if not progress and (run/'manifest.json').exists(): progress={'total':len(json_read(run/'manifest.json').get('tasks',[]))}
    result['progress']={**{'total':job.get('estimated_tasks',0),'completed':0,'failed':0,'finished':0},**progress}
    result['artifacts']=[]
    if run.is_dir():
        for f in sorted([*(run/name for name in ARTIFACTS), *(run/'figures').glob('*.png')]):
            relative=f.relative_to(run).as_posix()
            if f.is_file() and (relative in ARTIFACTS or re.fullmatch(r'figures/[a-z_]+\.png',relative)):
                result['artifacts'].append({'name':relative,'url':'/api/evaluation/artifact?'+urlencode({'id':jid,'file':relative})})
    log=folder/'console.log'
    if log.exists():
        with log.open('rb') as stream:
            stream.seek(max(0,log.stat().st_size-16000));result['log']=stream.read().decode('utf-8',errors='replace')
    else: result['log']=''
    result.pop('command',None); result.pop('pid',None); result.pop('process_token',None)
    result['output_path']=str(run)
    return result


def launch(job):
    folder=path(job['id'])
    json_write(folder/'job.json',job)
    try:
        with (folder/'worker.log').open('ab') as out:
            proc=subprocess.Popen([python(),str(EVAL/'web_worker.py'),str(folder)],cwd=EVAL,stdout=out,stderr=out,start_new_session=True)
    except OSError as exc:
        json_write(folder/'execution.json',{'status':'failed','error':str(exc)})
        raise
    job.update(pid=proc.pid,process_token=process_token(proc.pid))
    json_write(folder/'job.json',job)
    # Reap without tying work to the HTTP request or browser lifetime.
    threading.Thread(target=proc.wait,daemon=True).start()
    return read(job['id'])


def create(server, body):
    rows=choose(catalog(server),body)
    config=json_read(EVAL/'config.json')
    for key,default,lo,hi in [('workers',config['workers'],1,128),('repeats',config['repeats'],1,10),('retries',config['retries'],0,3),('timeout_seconds',config['timeout_seconds'],30,7200)]:
        config[key]=profile_batch.integer(body,key,default,lo,hi)
    prepare=body.get('prepare_only',False)
    if type(prepare) is not bool: raise ValueError('준비 실행 여부를 확인하세요.')
    limit=body.get('max_profiles')
    if limit is not None: limit=profile_batch.integer(body,'max_profiles',1,1)
    jid=job_ids.reserve(EVAL/'web_jobs')
    # Reserve the result name too; run.py creates it once preparation begins.
    result_id='web_'+jid
    config.update(clients=[],counselors=[])
    json_write(path(jid)/'config.json',config)
    command=[python(),str(EVAL/'run.py'),'--config',str(path(jid)/'config.json'),'--output-dir',str(EVAL/'results'/result_id)]
    for root in sorted(server.storage_roots): command+=['--storage-root',str(root)]
    for row in rows: command+=['--batch',row['id']]
    if limit is not None: command+=['--max-profiles',str(limit)]
    if prepare: command+=['--prepare-only']
    job={'id':jid,'created_at':datetime.now(timezone.utc).isoformat(),'result_id':result_id,'selection':body.get('selection','latest'),
         'batches':rows,'config':config,'prepare_only':prepare,'max_profiles':limit,'cwd':str(EVAL),
         'estimated_tasks':sum(min(r['profiles'],limit or r['profiles']) for r in rows)*4*config['repeats'],'command':command}
    return launch(job)


def resume(jid,body):
    current=read(jid)
    if current['status'] in ('starting','running','stopping'): raise ValueError('이미 실행 중입니다.')
    job=json_read(path(jid)/'job.json');run=EVAL/'results'/job['result_id']
    if not (run/'manifest.json').exists(): raise ValueError('입력 준비가 완료되지 않았습니다. 새 평가 작업을 생성하세요.')
    workers=profile_batch.integer(body,'workers',job['config']['workers'],1,128)
    job['config']['workers']=workers
    job['prepare_only']=False
    job['command']=[python(),str(EVAL/'run.py'),'--resume',str(run),'--workers',str(workers)]
    # Keep run inputs/judgments/logs; only process lifecycle is renewed.
    if (path(jid)/'execution.json').exists():
        history=path(jid)/'execution_history.jsonl'
        with history.open('a') as f: f.write(json.dumps(json_read(path(jid)/'execution.json'))+'\n')
    for name in ('stop','execution.json'):
        (path(jid)/name).unlink(missing_ok=True)
    job.pop('pid',None);job.pop('process_token',None)
    return launch(job)


def handle_get(handler):
    parts=urlsplit(handler.path)
    if not parts.path.startswith('/api/evaluation/'): return False
    try:
        args=parse_qs(parts.query)
        with LOCK:
            if parts.path.endswith('/options'): result={'batches':catalog(handler.server),'defaults':json_read(EVAL/'config.json')}
            elif parts.path.endswith('/list'):
                result={'jobs':sorted([{'id':p.parent.name,'created_at':json_read(p)['created_at']} for p in (EVAL/'web_jobs').glob('*/job.json')],key=lambda r:r['created_at'],reverse=True)}
            elif parts.path.endswith('/job'): result=read(args.get('id',[''])[0])
            elif parts.path.endswith('/artifact'):
                jid=args.get('id',[''])[0];name=args.get('file',[''])[0]
                if name not in ARTIFACTS and not re.fullmatch(r'figures/[a-z_]+\.png',name): raise ValueError('허용되지 않은 결과 파일입니다.')
                job=json_read(path(jid)/'job.json');base=(EVAL/'results'/job['result_id']).resolve();target=(base/name).resolve()
                if not target.is_relative_to(base): raise ValueError('결과 파일 경로를 확인하세요.')
                raw=target.read_bytes();mime='image/png' if name.endswith('.png') else 'text/plain; charset=utf-8'
                handler.send_response(200);handler.send_header('Content-Type',mime);handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff');handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw);return True
            else: raise ValueError('평가 경로를 확인하세요.')
        handler.json_response(result)
    except (ValueError,OSError,KeyError,TypeError) as exc: handler.json_response({'error':str(exc)},400)
    return True


def handle_post(handler):
    if not handler.path.startswith('/api/evaluation/'): return False
    try:
        if handler.headers.get('X-Gemma-Request')!='1': raise ValueError('요청 헤더가 없습니다.')
        size=int(handler.headers.get('Content-Length',0))
        if not 0<size<=1024*1024: raise ValueError('요청 크기를 확인하세요.')
        body=json.loads(handler.rfile.read(size))
        if not isinstance(body,dict): raise ValueError('JSON 객체가 필요합니다.')
        with LOCK:
            if handler.path.endswith('/create'): result=create(handler.server,body)
            elif handler.path.endswith('/resume'): result=resume(body.get('id'),body)
            elif handler.path.endswith('/stop'):
                jid=body.get('id');result=read(jid)
                if result['status'] in ('starting','running','stopping'):
                    (path(jid)/'stop').touch();result=read(jid)
            else: raise ValueError('평가 경로를 확인하세요.')
        handler.json_response(result)
    except (ValueError,OSError,KeyError,TypeError) as exc: handler.json_response({'error':str(exc)},400)
    return True
