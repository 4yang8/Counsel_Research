"""Matched selective retrieval experiment: optional versus prompt-required, 15 each."""
import copy, hashlib, json, shutil, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
BASE=ROOT/'experiments/group1_full_context_15_20261008T063458Z'
sys.path.insert(0,str(ROOT))
import app_server
from profile_retrieval import initial_profile, TOOL_NAMES

def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def post(server,route,body):
    return urlopen(Request('http://127.0.0.1:'+str(server.server_port)+route,
        data=json.dumps(body,ensure_ascii=False).encode(),headers={'X-Gemma-Request':'1','Content-Type':'application/json'}),timeout=1200)

def main():
    if (OUT/'manifest.json').exists():
        raise RuntimeError('Existing batch: do not overwrite or silently repeat.')
    snapshot=OUT/'snapshot'
    hashes={}
    sources=['app_server.py','history_cases.py','profile_retrieval.py','runtime.py','server.py','tool_loop.py',
        'mcp_client.py','config.json','tools.json','cases/lab_meeting.json','cases/group1/histories.json',
        'cases/lab_meeting_experiences.json','cases/lab_meeting_current_event.json',
        'cases/lab_meeting_constraints.json','cases/lab_meeting_state_basis.json']
    for rel in sources:
        dst=snapshot/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,dst)
        hashes[rel]=hashlib.sha256(dst.read_bytes()).hexdigest()
    for number in ('001','002','005','007','008'):
        name='gold_ccd_profile_'+number+'.json';dst=snapshot/'profiles'/name;dst.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT.parent/'profile/group1'/name,dst)
        hashes['profiles/'+name]=hashlib.sha256(dst.read_bytes()).hexdigest()
    cfg=json.loads((BASE/'config.json').read_text())
    cfg['profile_context_mode']='selective'
    cfg['profile_database_dir']=str(snapshot/'profiles')
    cfg['scenario_file']=str(snapshot/'cases/lab_meeting.json')
    for key,filename in [('experience_file','lab_meeting_experiences.json'),('current_event_file','lab_meeting_current_event.json'),
        ('constraints_file','lab_meeting_constraints.json'),('state_basis_file','lab_meeting_state_basis.json')]:
        cfg[key]=str(snapshot/'cases'/filename)
    dump(OUT/'config.json',cfg)
    systems={'optional':cfg['system_prompt'],
        'required':cfg['system_prompt']+'\n최종 내담자 발화를 생성하기 전에 제공된 도구를 적어도 한 번 호출하세요.'}
    manifest={'run_id':OUT.name,'baseline':str(BASE),'started_at':datetime.now(timezone.utc).isoformat(),
        'expected_generations':30,'sets':{s:{'expected':15,'system_prompt':p} for s,p in systems.items()},
        'model':cfg['model'],'vllm_url':cfg['vllm_url'],'generation':cfg['generation'],'enabled_tools':list(TOOL_NAMES),
        'source_sha256':hashes,'matched_inputs':'Exact baseline histories, counselor target, states, state meanings, task, taxonomy; initial profile reduced identically in both sets.',
        'initial_fields':['profile_id','demographic_context','presenting_problem','core_belief','intermediate_beliefs','coping_strategies'],
        'external_fields':['childhood_data','situations'],'retrieval':'Same Gemma model selects one record ID in independent context, no tools, max_tokens=128; Python returns exact single source unit, no summary generation.',
        'units':{'childhood_data':'One original sentence','situations':'One original event with its five fields'},
        'prompt_only_requirement':True,'repeats_per_input':1,'execution_order':'Matched pairs interleaved optional then required',
        'limitations':['One stochastic run per input; no statistical causal claim.','Basic background and histories already overlap with some external facts.',
            'Full-context baseline used a different tool catalog; total-token differences include removed information and tool schema changes.'],
        'analysis_method':'Artifact audit by Codex, no extra model judge.'}
    dump(OUT/'manifest.json',manifest)
    with urlopen(cfg['vllm_url'].rstrip('/')+'/models',timeout=15) as r:
        if cfg['model'] not in [m['id'] for m in json.load(r)['data']]:raise RuntimeError('Model not served')
    server=app_server.make_server(OUT/'config.json',port=0,storage_dir=OUT/'sessions')
    server.RequestHandlerClass.log_message=lambda *args:None
    threading.Thread(target=server.serve_forever,daemon=True).start()
    results=[]
    print(json.dumps({'ready':True,'batch':str(OUT),'cases':30},ensure_ascii=False),flush=True)
    try:
        for previous in json.loads((BASE/'results.json').read_text()):
            case_id=previous['case_id']
            original=json.loads((BASE/('case_'+case_id)/'input.json').read_text())
            dump(OUT/'baseline_inputs'/ (case_id+'.json'),original)
            for condition,system in systems.items():
                body=copy.deepcopy(original)
                body['scenario']['profile']=initial_profile(body['scenario']['profile'])
                body.update(system_prompt=system,enabled_tools=list(TOOL_NAMES))
                folder=OUT/condition/('case_'+case_id)
                dump(folder/'input.json',body);dump(folder/'scenario.json',body['scenario'])
                with post(server,'/api/preview',body) as r:payload=json.load(r)['payload']
                dump(folder/'payload.json',payload)
                assert 'childhood_data' not in payload['messages'][1]['content'] and 'situations' not in payload['messages'][1]['content']
                assert previous['reference_client_not_sent'] not in payload['messages'][1]['content']
                start=time.monotonic();events=[];error=None
                try:
                    with post(server,'/api/chat',body) as r:
                        for line in r:
                            if line.strip():events.append(json.loads(line))
                except Exception as exc:error=str(exc)
                dump(folder/'events.json',events)
                saved=next((e.get('saved_path') for e in events if e['type']=='request'),None)
                log=json.loads(Path(saved).read_text()) if saved else {}
                tool_events=[e for e in events if e['type']=='tool_result']
                children=[]
                for e in events:
                    if e['type']=='agent_event' and e['event']['type']=='done':
                        children.append({'agent_id':e['agent_id'],'name':e['agent_name'],'response':e['event']['response'],
                            'usage':e['event'].get('usage'),'finish_reason':e['event'].get('finish_reason')})
                result={'condition':condition,'case_id':case_id,'profile':previous['profile'],'history_title':previous['history_title'],
                    'states':previous['states'],'status':log.get('status','transport_error'),'finish_reason':log.get('finish_reason'),
                    'latency_seconds':round(time.monotonic()-start,2),'response':log.get('response',''),'reasoning':log.get('reasoning',''),
                    'usage':log.get('usage'),'main_usage':log.get('main_usage'),'subagent_usage':log.get('subagent_usage'),
                    'model_calls':len(log.get('model_calls',[])),'tools':[{'name':e['name'],'is_error':e.get('is_error'),
                        'result':e.get('result'),'model_content':e.get('model_content')} for e in tool_events],
                    'children':children,'log':saved,'error':error or log.get('error')}
                dump(folder/'result.json',result);results.append(result);dump(OUT/'results.json',results)
                print(json.dumps({k:result[k] for k in ('condition','case_id','status','latency_seconds','model_calls')}
                    |{'tool_calls':len(tool_events),'subagents':len(children)},ensure_ascii=False),flush=True)
        manifest.update(finished_at=datetime.now(timezone.utc).isoformat(),completed=sum(r['status']=='completed' for r in results))
        dump(OUT/'manifest.json',manifest)
    finally:server.shutdown();server.server_close()
if __name__=='__main__':main()
