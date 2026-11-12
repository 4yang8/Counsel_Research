#!/usr/bin/env python3
"""Freeze latest batches, run blinded Codex judges, summarize and plot."""
import argparse
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

from data import discover, extract, packet, read, write, digest, file_hash
from schema import SCHEMAS, validate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def implementation():
    files = [HERE/n for n in ('run.py','data.py','schema.py','report.py')]
    files += sorted((HERE/'prompts').glob('*.md'))
    return {str(p.relative_to(HERE)):file_hash(p) for p in files}


def cli_command(config, sandbox, schema_file, output):
    return ['codex','exec','--ignore-user-config','--ephemeral','--skip-git-repo-check',
            '--sandbox','read-only','--cd',str(sandbox),'--model',config['model'],
            '-c',f'model_reasoning_effort={json.dumps(config["effort"])}',
            '-c','approval_policy="never"','-c','project_doc_max_bytes=0',
            '-c','web_search="disabled"','-c','features.shell_tool=false',
            '-c','features.multi_agent=false','-c','features.apps=false',
            '-c','features.skill_search=false','-c','features.skip_host_skill_discovery=true',
            '--output-schema',str(schema_file),'--output-last-message',str(output),
            '--json','--color','never','-']


def preflight(config):
    if not shutil.which('codex'):
        raise RuntimeError('codex CLI not found in PATH. Install/login before evaluation.')
    version=subprocess.run(['codex','--version'],capture_output=True,text=True,check=True).stdout.strip()
    help_text=subprocess.run(['codex','exec','--help'],capture_output=True,text=True,check=True).stdout
    for flag in ('--ignore-user-config','--ephemeral','--output-schema'):
        if flag not in help_text:
            raise RuntimeError(f'Installed CLI lacks required {flag}; update CLI (no fallback used).')
    cache=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'models_cache.json'
    matched=None
    if cache.exists():
        matched=next((m for m in read(cache).get('models',[]) if m.get('slug')==config['model']),None)
        if matched:
            supported=[x['effort'] for x in matched.get('supported_reasoning_levels',[])]
            if supported and config['effort'] not in supported:
                raise RuntimeError(f'{config["model"]} does not list effort {config["effort"]}: {supported}')
    return {'cli':version,'model':config['model'],'effort':config['effort'],
            'model_in_local_catalog':matched is not None,
            'supported_efforts':None if matched is None else [x['effort'] for x in matched.get('supported_reasoning_levels',[])]}


def read_events(path):
    usage={};tools=[]
    for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
        try:e=json.loads(line)
        except ValueError:continue
        if e.get('type')=='turn.completed':
            for k,v in (e.get('usage') or {}).items():
                if isinstance(v,(int,float)):usage[k]=usage.get(k,0)+v
        item=e.get('item') or {}
        it=item.get('type','')
        if it in ('command_execution','mcp_tool_call','web_search','file_change','collab_tool_call') or 'tool_call' in it:
            tools.append(it)
    if tools:
        raise ValueError('Judge used tools; blinded evaluation rejected: '+str(tools))
    return usage


def judge(run,task,config):
    base=run/'judgments'/task['id'];base.mkdir(parents=True,exist_ok=True)
    p=read(run/'packets'/f'{task["id"]}.json')
    prompt=(run/'snapshot/prompts/common.md').read_text(encoding='utf-8')+'\n\n'+(run/f'snapshot/prompts/{task["kind"]}.md').read_text(encoding='utf-8')
    prompt+='\n\n평가 입력 JSON:\n'+json.dumps(p,ensure_ascii=False)
    required=[t['turn'] for t in p['turns'] if task['kind']=='private' or t['turn']>=p['scope']['counselor_start']]
    field='hidden_reactions' if task['kind']=='private' else 'counselor_actions'
    prompt+=f'\n\n누락 점검: {field} 배열에 반드시 포함할 turn 목록은 {required}이다. 각 번호를 정확히 한 번씩 포함하라.'
    fingerprint=digest({'prompt':prompt,'model':config['model'],'effort':config['effort'],
                        'schema':SCHEMAS[task['kind']],'implementation':read(run/'manifest.json')['implementation']})
    prior=read(base/'status.json') if (base/'status.json').exists() else {}
    if prior.get('status')=='completed' and prior.get('fingerprint')==fingerprint:
        try:
            validate(read(base/'result.json'),p,task['kind'])
            return 'cached'
        except Exception:pass
    start=time.monotonic();error='';total_usage={}
    # Every attempt is retained. A retry never overwrites a failed raw response.
    first=len(list(base.glob('attempt_*')))+1
    for attempt in range(first,first+config['retries']+1):
        directory=base/f'attempt_{attempt:03d}';directory.mkdir()
        full_prompt=prompt+(f'\n\n직전 응답 검증 실패: {error}\n스키마와 인용/턴 규칙을 지켜 새로 평가하라.' if error else '')
        (directory/'prompt.txt').write_text(full_prompt,encoding='utf-8')
        write(base/'status.json',{'status':'running','fingerprint':fingerprint,'attempt':attempt})
        try:
            if len(full_prompt)>config['max_input_chars']:
                raise ValueError('Input too large; not silently truncating. Increase max_input_chars or add a documented window adapter.')
            # Outside repository: no batch IDs, keys, source logs or project instructions.
            with tempfile.TemporaryDirectory(prefix='counsel-judge-') as td:
                td=Path(td)
                schema_file=td/'schema.json';answer_file=td/'answer.json'
                write(schema_file,SCHEMAS[task['kind']])
                command=cli_command(config,td,schema_file,answer_file)
                write(directory/'command.json',command)
                with (directory/'events.jsonl').open('w',encoding='utf-8') as out, (directory/'stderr.txt').open('w',encoding='utf-8') as err:
                    proc=subprocess.run(command,input=full_prompt,text=True,stdout=out,stderr=err,cwd=td,
                                        timeout=config['timeout_seconds'])
                if answer_file.exists():
                    shutil.copyfile(answer_file,directory/'raw_answer.json')
                usage=read_events(directory/'events.jsonl')
                for k,v in usage.items():total_usage[k]=total_usage.get(k,0)+v
                if proc.returncode:
                    tail=(directory/'stderr.txt').read_text(encoding='utf-8',errors='replace')[-1600:]
                    raise RuntimeError(f'codex exit {proc.returncode}: {tail}')
                result=validate(read(directory/'raw_answer.json'),p,task['kind'])
                write(base/'result.json',result)
                write(base/'status.json',{'status':'completed','fingerprint':fingerprint,'attempt':attempt,
                                         'elapsed_seconds':round(time.monotonic()-start,3),'usage':total_usage})
                return 'completed'
        except Exception as exc:
            error=str(exc)
            write(directory/'error.json',{'error':error})
            # Unsupported model/config/auth should not be silently replaced or endlessly retried.
            fatal_runtime=isinstance(exc,RuntimeError) and any(x in error.lower() for x in ('unsupported','not supported','unauthorized','model not found','authentication'))
            if fatal_runtime or error.startswith(('Input too large','Judge used tools')):
                break
    write(base/'status.json',{'status':'failed','fingerprint':fingerprint,'error':error,
                             'elapsed_seconds':round(time.monotonic()-start,3),'usage':total_usage})
    return 'failed'


def prepare(config,args):
    selected,excluded=discover(ROOT,config['counselors'],config['clients'],args.batch,getattr(args,'storage_root',None))
    docs,batches=extract(ROOT,selected,args.max_profiles)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    run=getattr(args,'output_dir',None) or HERE/'results'/f'{stamp}_{uuid.uuid4().hex[:6]}'
    run=run.resolve()
    if (HERE/'results').resolve() not in run.parents:
        raise ValueError('Output directory must be inside eval/results/')
    run.mkdir(parents=True)
    tasks=[]
    for doc in docs:
        doc['id']=uuid.uuid4().hex[:16]
        for repeat in range(config['repeats']):
            for kind in ('public','private'):
                tid=uuid.uuid4().hex[:20]
                tasks.append({'id':tid,'document':doc['id'],'kind':kind,'repeat':repeat})
                write(run/'packets'/f'{tid}.json',packet(doc,kind,tid))
    random.Random(config['seed']).shuffle(tasks)
    impl=implementation()
    for relative in impl:
        destination=run/'snapshot'/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(HERE/relative,destination)
    for kind,schema in SCHEMAS.items():write(run/'snapshot/schemas'/f'{kind}.json',schema)
    manifest={'format_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'config':config,
              'selection':'explicit completed batch IDs' if args.batch else 'latest completed compare batch per counselor/client, ordered by created_at',
              'storage_roots':[str(p) for p in (getattr(args,'storage_root',None) or [ROOT/'runs'])],
              'batches':batches,'excluded':excluded,'tasks':tasks,'implementation':impl,
              'sample_limit':args.max_profiles,'complete_at_preparation':True}
    for source,job in selected:
        write(run/'source_jobs'/f'{job["id"]}.json',job)
    write(run/'manifest.json',manifest);write(run/'normalized.json',docs)
    write(run/'code_metrics.json',[{'document':d['id'],'batch':d['batch'],'profile':d['profile'],'branch':d['branch'],**d['code']} for d in docs])
    print(f'결과 폴더: {run}',flush=True)
    for b in batches:
        print(f'  {b["counselor"]} × {b["client"]}: {b["id"]}, {len(b["profiles"])} profiles, {b["turns"]} turns / fork {b["fork_turn"]}',flush=True)
    print(f'대화 {len(docs)}개, 평가 {len(tasks)}회 (+실패 시 재시도), model={config["model"]}, effort={config["effort"]}',flush=True)
    return run,manifest


def smoke(config):
    """Synthetic requests for both rubrics, never mixed into research results."""
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    run=HERE/'results'/f'smoke_{stamp}_{uuid.uuid4().hex[:6]}';run.mkdir(parents=True)
    p={'scope':{'client_start':2,'counselor_start':3},'turns':[
        {'turn':1,'counselor':'오늘 어떤 이야기를 하고 싶으세요?','client':'업무가 너무 많아서 힘들어요.',
         'inner':{'appraisal':{'immediate_feeling':'힘든 일을 이야기하고 싶다.'}}},
        {'turn':2,'counselor':'상사에게 업무를 줄여 달라고 직접 말해 보면 어떨까요?','client':'네, 알겠습니다.',
         'inner':{'appraisal':{'immediate_feeling':'직접 말하라는 제안이 부담스럽다.'},'private_position':{'withheld_content':'상사에게 말하면 불이익을 받을까 두렵다. 상담사에게 반대하기 어려워 동의한 척한다.'}}},
        {'turn':3,'counselor':'아까 직접 말해 보자는 제안은 어떻게 들리셨어요?','client':'사실 부담스러웠어요. 상사가 불이익을 줄까 봐요.',
         'inner':{'appraisal':{'immediate_feeling':'직접 말하는 것은 여전히 두렵지만 상담사가 물어봐 주니 그 부담을 이야기할 수 있겠다.'}}}]}
    write(run/'manifest.json',{'implementation':implementation(),'config':config,'synthetic_smoke_only':True,'preflight':preflight(config)})
    for name in ('common','public','private'):
        destination=run/f'snapshot/prompts/{name}.md';destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(HERE/f'prompts/{name}.md',destination)
    failed=0
    for kind in ('public','private'):
        tid='smoke_'+kind
        task={'id':tid,'document':'synthetic','kind':kind,'repeat':0}
        payload={**p,'packet_id':tid,'turns':[{k:v for k,v in t.items() if k!='inner' or kind=='private'} for t in p['turns']]}
        write(run/'packets'/f'{tid}.json',payload)
        status=judge(run,task,config)
        print(f'Synthetic smoke {kind}: {status} — {run}',flush=True)
        if status=='failed':
            failed+=1
            print(read(run/f'judgments/{tid}/status.json')['error'],file=sys.stderr)
    return int(bool(failed))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,default=HERE/'config.json')
    ap.add_argument('--counselor',action='append',help='repeat to select multiple counselors')
    ap.add_argument('--client',action='append',help='repeat to select multiple clients')
    ap.add_argument('--batch',action='append',default=[],help='explicit completed compare batch ID (bypasses latest selection)')
    ap.add_argument('--storage-root',action='append',type=Path,help='data storage directory containing profile_batches and paired')
    ap.add_argument('--output-dir',type=Path,help='new result directory inside eval/results/')
    ap.add_argument('--max-profiles',type=int,help='smaller balanced-pair preparation; lexicographic profile IDs, logged')
    for flag in ('workers','repeats','retries','timeout-seconds'):
        ap.add_argument('--'+flag,type=int)
    ap.add_argument('--model');ap.add_argument('--effort')
    mode=ap.add_mutually_exclusive_group()
    mode.add_argument('--prepare-only','--dry-run',action='store_true',help='parse + snapshot + code summaries + NA plots; no LLM calls')
    mode.add_argument('--resume',type=Path,help='retry missing/failed judgments from a frozen run')
    mode.add_argument('--report-only',type=Path,help='regenerate summaries/plots from saved validated judgments')
    mode.add_argument('--smoke-test',action='store_true',help='two synthetic CLI requests (public/private); not a batch evaluation')
    args=ap.parse_args()
    config=read(args.config)
    for key in ('model','effort','workers','repeats','retries','timeout_seconds'):
        if getattr(args,key) is not None:config[key]=getattr(args,key)
    for key in ('counselors','clients'):
        if getattr(args,key[:-1]) is not None:config[key]=getattr(args,key[:-1])
    for k in ('workers','repeats','timeout_seconds','max_input_chars'):
        if not isinstance(config[k],int) or config[k]<1:raise ValueError(k+' must be positive')
    if config['retries']<0 or args.max_profiles is not None and args.max_profiles<1:
        raise ValueError('retries must be >=0; max_profiles must be >=1')
    if args.smoke_test:return smoke(config)
    from report import generate
    if args.resume or args.report_only:
        run=(args.resume or args.report_only).resolve()
        if HERE/'results' not in run.parents:
            raise ValueError('Resume/report path must be inside eval/results/')
        manifest=read(run/'manifest.json')
        if manifest['implementation']!=implementation():
            raise ValueError('Evaluation implementation/prompts changed since snapshot. Start a new run to avoid mixing rubrics.')
        config=manifest['config']
        for key in ('workers','retries','timeout_seconds'):
            if getattr(args,key) is not None:config={**config,key:getattr(args,key)}
        if any(getattr(args,k) is not None for k in ('model','effort','repeats','max_profiles','counselor','client','output_dir','storage_root')) or args.batch:
            raise ValueError('Resume uses frozen input/model/rubric. Start a new run to change these.')
    else:
        run,manifest=prepare(config,args)
    # Prevent two local invocations from writing the same resumed result folder.
    with (run/'.run.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('This evaluation run is already running.')
        if args.prepare_only or args.report_only:
            missing=generate(run)
            print(f'집계/그래프 저장 완료. 미완료 평가 {missing}개. LLM 호출 없음.',flush=True)
            return 0
        write(run/'preflight.json',preflight(config))
        tasks=manifest['tasks'];states=Counter()
        def progress():
            write(run/'progress.json',{'total':len(tasks),'completed':states['completed']+states['cached'],
                                      'failed':states['failed'],'finished':sum(states.values())})
        progress()
        # One real task first: catch model/auth/config errors before starting the queue.
        if tasks:
            status=judge(run,tasks[0],config);states[status]+=1
            progress()
            print(f'[1/{len(tasks)}] {tasks[0]["kind"]}: {status}',flush=True)
            if status=='failed':
                generate(run)
                print('첫 평가 실패. 상세: '+str(run/'judgments'/tasks[0]['id']/'status.json'),file=sys.stderr)
                return 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=config['workers']) as pool:
            future_map={pool.submit(judge,run,t,config):t for t in tasks[1:]}
            for i,future in enumerate(concurrent.futures.as_completed(future_map),start=2):
                status=future.result();states[status]+=1
                progress()
                print(f'[{i}/{len(tasks)}] {future_map[future]["kind"]}: {status}',flush=True)
        missing=generate(run)
        print(f'완료: {dict(states)}\n보고서: {run/"report.md"}\n그림: {run/"figures"}',flush=True)
        return 1 if missing else 0


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,RuntimeError,KeyError,FileNotFoundError) as exc:
        print('평가 중단: '+str(exc),file=sys.stderr);sys.exit(1)
