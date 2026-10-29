from pathlib import Path
from datetime import datetime,timezone
import json,sys,threading
from urllib.request import Request,urlopen
ROOT=Path('/home2/sjyang114/Counsel_Research_dynamic_client_new/gemma_test/counseling_test')
sys.path.insert(0,str(ROOT));import app_server,experiment_versions
out=ROOT/'experiments'/('paired_basic_counselor_smoke_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));out.mkdir()
(out/'OBJECTIVE.md').write_text('basic 내담자와 단일 프롬프트 기본 상담사의 실제 vLLM 연결을 새 세션 2개 발화로 확인한다. 품질 비교 실험이 아니며 재시도 없이 실제 입력·출력을 저장한다.\n')
server=app_server.make_server(port=0,storage_dir=out/'sessions');threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_port)
def get(route):return json.load(urlopen(base+route,timeout=30))
def post(route,data):return urlopen(Request(base+route,data=json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','X-Gemma-Request':'1'}),timeout=240)
try:
 cfg=get('/api/config');cat=get('/api/histories/group1');entry=cat['profiles'][0];scenario=entry['histories'][0]['scenario'];scenario['profile']=entry['source_profile']
 body={'scenario':scenario,'experiment_version':'basic','counselor_version':'basic','generation':cfg['generation'],'system_prompt':cfg['system_prompt'],'profile_context_mode':'selective','enabled_tools':[],'messages':[{'role':'user','content':scenario['selected_counselor_utterance']}]}
 (out/'input.json').write_text(json.dumps(body,ensure_ascii=False,indent=2));(out/'config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2));(out/'c4_sources.json').write_text(json.dumps(get('/api/paired/counselor'),ensure_ascii=False,indent=2))
 with post('/api/paired/create',body) as response: session=json.load(response)
 print('ARTIFACT',out,flush=True);print('SESSION',session['session_id'],flush=True)
 for index in range(2):
  print('START',index+1,session['next_actor'],flush=True)
  events=[]
  with post('/api/paired/step',{'session_id':session['session_id'],'expected_step':len(session['steps'])}) as response:
   for line in response:
    event=json.loads(line);events.append(event)
    inner=event['event']
    if inner['type']=='request':print('CALL',inner.get('phase','client'),inner.get('iteration'),flush=True)
  (out/f'events_{index+1}.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
  last=events[-1]['event'];print('RESULT',last['type'],last.get('error',last.get('response',''))[:300],flush=True)
  if last['type']!='done':break
  session=last['session']
 (out/'result.json').write_text(json.dumps(session,ensure_ascii=False,indent=2));print('COMPLETED_SPEECHES',len(session['steps']),flush=True)
finally:server.shutdown();server.server_close()
