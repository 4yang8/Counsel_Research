import copy,json,pathlib,sys,threading,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=pathlib.Path(__file__).resolve().parent
SOURCE=ROOT/'experiments/state_inner_low_5_20261008'
sys.path.insert(0,str(ROOT));import app_server

def dump(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')

def main():
 if (OUT/'manifest.json').exists():raise RuntimeError('Experiment exists')
 combos=[('낮음','낮음','낮음'),('높음','높음','높음'),('낮음','높음','중간'),('높음','낮음','중간'),('중간','중간','높음')]
 basis={
 'trust':{'낮음':'직전의 이해받았다는 표현은 상담자가 이번 마음을 짚어준 순간에 대한 긍정이다. 믿고 이야기해보고 싶다는 바람은 있지만, 상담자가 앞으로도 자신의 경험을 이해할 사람인지에 대한 확신은 아직 낮다.', '중간':'상담자가 서두른 점을 인정한 뒤 이전보다 기대가 생겼지만, 더 민감한 이야기도 이해받을지는 아직 지켜보고 싶다.', '높음':'상담자가 자신의 성급함을 인정하고 준비 부족과 걱정을 함께 받아준 경험을 통해, 현재 상담자가 자신의 경험을 이해하려는 사람이라고 믿는다.'},
 'disclosure':{'낮음':'대화를 이어가고 싶은 마음과 별개로, 그때의 자기비난을 자세히 말하는 것은 수치스럽고 아직 내키지 않는다.', '중간':'당황하고 부끄러웠다는 점은 이야기하고 싶지만, 가장 아픈 자기비난까지 말할지는 반응을 살피고 싶다.', '높음':'당시의 부끄러움과 자기비난을 혼자 반복하는 것에서 벗어나고 싶어, 지금은 불편한 생각도 구체적으로 이야기할 의향이 있다.'},
 'change_confidence':{'낮음':'다시 준비해야 한다고 생각하지만, 다음에도 질문 앞에서 막히지 않고 설명할 수 있을지는 자신이 부족하다.', '중간':'준비를 보완해볼 수 있다고 생각하지만, 예상하지 못한 질문에 대응할 수 있을지는 아직 확신하지 못한다.', '높음':'미팅 당시의 당황과 별개로, 현재는 부족한 비교 기준을 확인하고 설명을 다시 준비하는 행동을 해낼 수 있다고 느낀다.'}}
 system='주어진 툴을 적절히 활용하세요. 이번 응답에서는 느낌·판단 이유·내담자 발화를 작성하기 전에 inspect_state_basis 도구로 trust, disclosure, change_confidence 세 항목을 각각 조회하세요. 조회 결과를 현재 내면 상태와 대화 히스토리의 관계를 판단하는 근거로 사용하세요.'
 dump(OUT/'manifest.json',{'scope':'5 independent continuations; forced by system instruction, not API tool_choice; no matched no-tool controls','combinations':combos,'system_prompt':system,'basis':basis,'source':str(SOURCE),'note':'Fresh synthetic current-state records replace stale records; cannot isolate tool effect from data and instruction changes.'})
 results=[]
 for i,levels in enumerate(combos,1):
  folder=OUT/f'case_{i:02d}';folder.mkdir();sc=json.loads((SOURCE/'scenario.json').read_text());records={}
  for key,level in zip(['trust','disclosure','change_confidence'],levels):
   sc['states'][key].update(level=level,meaning=sc['state_levels'][key][level]);records[key]={'source':'実習용 가상 사례의 내면 상태 설정','as_of':'직전 내담자 발화 이후, 이번 상담사 발화를 듣기 직전','level_at_recording':level,'basis':basis[key][level],'history':[],'note':'대화에서 자동 추정한 값이 아니라 실험용 현재 내면 설정이다. 이전 수준과 변화 시점의 추가 기록은 없다.'}
  dump(folder/'scenario.json',sc);dump(folder/'state_basis.json',{'scenario_id':sc['id'],'provenance':'실습용 가상 심리 상태 기록. 실제 사용자의 내면에 대한 진술이 아니다.','states':records})
  cfg=json.loads((SOURCE/'config.json').read_text());cfg.update(scenario_file=str(folder/'scenario.json'),state_basis_file=str(folder/'state_basis.json'));dump(folder/'config.json',cfg)
  body={'messages':[{'role':'user','content':sc['example_counselor_utterance']}],'system_prompt':system};dump(folder/'input.json',body)
  server=app_server.make_server(folder/'config.json',port=0,storage_dir=folder/'sessions');threading.Thread(target=server.serve_forever,daemon=True).start()
  try:
   req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/chat',data=json.dumps(body).encode(),headers={'X-Gemma-Request':'1','Content-Type':'application/json'})
   with urllib.request.urlopen(req,timeout=240) as response:events=[json.loads(line) for line in response]
   log=pathlib.Path(next(e['saved_path'] for e in events if e['type']=='request'));d=json.loads(log.read_text());row={'id':i,'levels':levels,'status':d['status'],'response':d.get('response'),'tools':d.get('tool_events',[]),'model_calls':len(d.get('model_calls',[])),'usage':d.get('usage'),'log':str(log)}
   dump(folder/'result.json',row);results.append(row);dump(OUT/'results.json',results)
   print(json.dumps({**row,'tools':[{'type':e['type'],'name':e['name'],'arguments':e.get('arguments'),'is_error':e.get('is_error')} for e in row['tools']]},ensure_ascii=False),flush=True)
  finally:server.shutdown();server.server_close()
if __name__=='__main__':main()
