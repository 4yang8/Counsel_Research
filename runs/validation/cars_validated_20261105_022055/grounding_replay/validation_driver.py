import json,time
from pathlib import Path
from urllib.request import Request,urlopen
from client.paper_baselines import cars,common
base=Path(Path('/tmp/cars_validated_live_output').read_text().strip());summary=json.loads((base/'summary.json').read_text());before=json.loads(Path(summary['fresh_turns'][1]['state_path']).read_text());native=before['state']['native_profile']
original=json.loads(Path('runs/paired/261105_012403/session.json').read_text());questions=[x['content'] for x in original['public_dialogue'] if x['role']=='counselor'];messages=before['history']+[{'role':'user','content':questions[2]}]
out=base/'grounding_replay';out.mkdir(exist_ok=True);prompts=common.bundle('Q3cars');(out/'prompts.json').write_text(json.dumps(prompts,ensure_ascii=False,indent=2));trace=[]
def call(stage,prompt,schema):
 payload={'model':'Qwen/Qwen3.8-Flash-Next-FP8','messages':[{'role':'system','content':prompt+'\n출력 스키마에 맞는 JSON을 한국어로 반환하세요.\n'+common.encoded(schema)},{'role':'user','content':'주어진 과제를 수행하세요.'}],'temperature':0.7,'top_p':0.95,'seed':1105+len(trace),'max_tokens':8192,'chat_template_kwargs':{'reasoning_effort':'low'},'structured_outputs':{'json':schema}}
 path=out/(str(len(trace)+1)+'_'+stage);path.with_suffix('.request.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2));t=time.monotonic()
 with urlopen(Request('http://127.0.0.1:18001/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'}),timeout=180) as r:response=json.load(r)
 path.with_suffix('.response.json').write_text(json.dumps(response,ensure_ascii=False,indent=2));assert response['choices'][0]['finish_reason']=='stop';v=common.validate(json.loads(response['choices'][0]['message']['content']),schema,stage);trace.append({'stage':stage,'output':v,'usage':response.get('usage'),'seconds':round(time.monotonic()-t,1)});print(stage,trace[-1]['seconds'],flush=True);return v
result={'scope':'수정된 사실성 규약으로 동일 2턴 prefix 이후 3턴만 재생성. 이전 4턴 실행은 수정 전 snapshot이다.'}
try:
 bad=json.loads(Path(summary['fresh_turns'][2]['state_path']).read_text())['records'][-1]
 candidate=next(x['output'] for x in bad['stages'] if x['stage']=='cognitive_response')
 issues=cars.review(call,prompts,'cars_response_review','regression_review',{'profile':native,'public_dialogue':messages,'prior_emotion':before['state']['emotion'],'prior_intention':before['state']['intention'],'candidate':candidate,'sources':cars.evidence_sources(native,messages)})
 result['old_candidate_issues']=issues;print('OLD ISSUES',issues,flush=True)
 utterance,state=cars.generate(call,prompts,before['frozen_options'],native,messages,before['state'])
 result.update(status='passed',utterance=utterance,state=state)
 print('REPLAY',utterance,flush=True)
except Exception as e:result.update(status='failed',error=repr(e));print('ERROR',repr(e),flush=True)
finally:
 result['trace']=trace;(out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print('DONE',out,flush=True)
