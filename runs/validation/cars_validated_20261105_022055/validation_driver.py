import copy,json,threading,time,sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request,urlopen
from app.app_server import make_server
from client.paper_baselines import common,cars
root=Path('/home2/sjyang114/Counsel_Research_dynamic_client_new')
old=json.loads((root/'runs/paired/261105_012403/session.json').read_text())
oldstate=json.loads((Path(old['client_storage_dir'])/'Q3cars'/old['session_id']/'paper_state.json').read_text())
out=root/'runs/validation'/('cars_validated_'+datetime.now().strftime('%Y%m%d_%H%M%S'));out.mkdir(parents=True)
Path('/tmp/cars_validated_live_output').write_text(str(out));print('OUTPUT',out,flush=True)
cfg=json.loads((root/'config/app.json').read_text());cfg.update(inspect_prompt=False,vllm_url='http://127.0.0.1:18001/v1',timeout_seconds=180)
cfg['generation']['max_tokens']=8192
config=out/'config.json';config.write_text(json.dumps(cfg));prompts=common.bundle('Q3cars');results={'review_probes':[],'fresh_turns':[]}
def save(): (out/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
def probe(name,prompt,sources):
 payload={'model':cfg['model'],'messages':[{'role':'system','content':common.render(prompts,prompt,data=common.encoded(sources))},{'role':'user','content':'검토 결과 JSON을 반환하세요.'}],'temperature':0,'max_tokens':4096,'chat_template_kwargs':{'reasoning_effort':'low'},'structured_outputs':{'json':cars.REVIEW_SCHEMA}}
 (out/(name+'_request.json')).write_text(json.dumps(payload,ensure_ascii=False,indent=2))
 with urlopen(Request(cfg['vllm_url']+'/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'}),timeout=180) as response:r=json.load(response)
 (out/(name+'_response.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2))
 assert r['choices'][0]['finish_reason']=='stop',r['choices'][0]['finish_reason']
 return json.loads(r['choices'][0]['message']['content'])
try:
 p=probe('old_profile','cars_profile_review',{'source_persona':oldstate['state']['native_profile']['persona'],'profile':oldstate['state']['native_profile']})
 results['review_probes'].append({'name':'old_profile','issues':p['issues']});save()
 assert any(x['kind']=='emotion_scale' for x in p['issues'])
 print('PASS known inverted-emotion profile rejected',flush=True)
 rec=oldstate['records'][2]
 p=probe('old_turn3','cars_response_review',{'profile':oldstate['state']['native_profile'],'public_dialogue':old['public_dialogue'][:5],'prior_emotion':oldstate['records'][1]['state']['emotion'],'candidate':{'Thinking':rec['state']['thinking'],'Response':rec['utterance'],'Emotion':rec['state']['emotion_delta'],'Intention':rec['state']['intention'],'Grounding':[]},'sources':cars.evidence_sources(oldstate['state']['native_profile'],old['public_dialogue'][:5])})
 results['review_probes'].append({'name':'old_turn3','issues':p['issues']});save()
 assert any(x['kind'] in ('unsupported_fact','grounding') for x in p['issues'])
 print('PASS known ungrounded response rejected',flush=True)
except Exception as e:
 results['probe_error']=repr(e);save();print('PROBE ERROR',repr(e),flush=True)
server=make_server(config,host='127.0.0.1',port=0,storage_dir=out/'sessions');threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_port)
def api(route,data,stream=False):
 req=Request(base+route,data=json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','X-Gemma-Request':'1'})
 with urlopen(req,timeout=300) as r:return [json.loads(line)['event'] for line in r if line.strip()] if stream else json.load(r)
try:
 questions=[m['content'] for m in old['public_dialogue'] if m['role']=='counselor']
 session=api('/api/paired/create',{'experiment_version':'Q3cars','counselor_version':'basic','speaker_modes':{'client':'model','counselor':'manual'},'scenario':copy.deepcopy(old['settings']['scenario']),'messages':[{'role':'user','content':questions[0]}],'generation':{**cfg['generation'],'seed':1105},'model':cfg['model'],'vllm_url':cfg['vllm_url']})
 for i,q in enumerate(questions):
  start=time.monotonic()
  if i:session=api('/api/paired/manual',{'session_id':session['session_id'],'expected_step':len(session['steps']),'actor':'counselor','content':q})['session']
  events=api('/api/paired/step',{'session_id':session['session_id'],'expected_step':len(session['steps'])},True)
  (out/f'turn{i+1}_events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
  assert events[-1]['type']=='done',events[-1]
  session=events[-1]['session'];state=common.load({'_storage_dir':session['client_storage_dir']},'Q3cars',session['session_id']);r=state['records'][-1]
  assert r['state']['validation'][-1]['issues']==[] and r['state']['pipeline_version']==cars.PIPELINE_VERSION
  results['fresh_turns'].append({'turn':r['turn'],'utterance':r['utterance'],'emotion':r['state']['emotion'],'reasoning':r['state']['reasoning'],'grounding':r['state']['grounding'],'validation':r['state']['validation'],'stages':[s['stage'] for s in r['stages']],'state_path':str(Path(session['client_storage_dir'])/'Q3cars'/session['session_id']/'paper_state.json')})
  save();print('PASS fresh turn',i+1,'seconds',round(time.monotonic()-start,1),'stages',results['fresh_turns'][-1]['stages'],flush=True)
 results['fresh_status']='passed';save()
except Exception as e:
 results['fresh_status']='failed';results['fresh_error']=repr(e);save();print('FRESH ERROR',repr(e),flush=True)
finally:server.shutdown();server.server_close()
print('DONE',out/'summary.json',flush=True)
