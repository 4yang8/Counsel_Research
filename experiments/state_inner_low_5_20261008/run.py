import copy,hashlib,json,pathlib,sys,threading,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[2]
OUT=pathlib.Path(__file__).resolve().parent
SOURCE=ROOT/'experiments/state_basis_3_20261007/case_01'
sys.path.insert(0,str(ROOT))
import app_server

def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def main():
 if (OUT/'manifest.json').exists():raise RuntimeError('既存実験の上書きを防止: manifest exists')
 tools=json.loads((ROOT/'tools.json').read_text());original=next(t['function']['description'] for t in tools if t['function']['name']=='inspect_state_basis')
 variants=[('원래 설명',original),
 ('역할과 입출력 명시','당신은 내담자의 반응을 작성하고 있습니다. 입력에 있는 상태 수준과 대화만으로 그 상태의 배경을 알 수 없을 때, 이 내담자의 상태 설정 기록을 조회하는 도구입니다. state에 trust(상담자 신뢰), disclosure(자기개방 의향), change_confidence(변화 자신감) 중 하나를 지정하세요. 현재 입력된 수준과 기록된 설정 시점·출처·근거·변화 이력을 반환합니다. 확인한 기록을 상담자 발화를 어떻게 받아들일지 판단하는 데 참고하세요. 기록이 없을 수 있으며 상태를 변경하지 않습니다.'),
 ('판단에 필요한 정보 명시','상담자 발화에 대한 내담자의 반응을 정하기 전에, 상태값과 대화 히스토리의 관계를 확인하는 도구입니다. 현재 입력된 상태가 최근 대화를 반영한 것인지, 어떤 근거로 정해졌는지 불명확하면 해당 상태를 조회하세요. 반환되는 설정 시점과 근거를 이용해 과거의 말과 현재 상태를 구분하세요. 이미 근거가 주어졌다면 다시 조회하지 않아도 됩니다. 기록이 없으면 원인을 알 수 없다는 뜻이며, 이 도구는 상태나 감정을 대신 결정하지 않습니다.'),
 ('관찰 가능한 호출 조건','최근 내담자 발화가 제공된 상태 수준과 다른 방향의 신호를 보일 때 사용하는 상태 근거 조회 도구입니다. 낮은 신뢰와 신뢰 표현, 낮은 자기개방 의향과 상세히 말하려는 표현, 낮은 변화 자신감과 할 수 있다는 표현 등이 함께 주어지면, 반응을 작성하기 전에 해당 상태를 조회하세요. state로 상태 항목을 선택하고 reason에 두 정보의 어떤 관계를 확인하려는지 적으세요. 설정 시점·출처·근거·기록된 변화 이력을 반환하므로, 시점 차이인지 같은 시점에 양립하는 반응인지 판단하는 데 참고할 수 있습니다. 기록이 없으면 그 관계는 확인되지 않은 것입니다. 상태를 자동 변경하지 않습니다.'),
 ('해석 전 확인할 정보의 경계','이 도구는 입력된 상태 수준이 어떤 시점과 근거에서 나온 값인지 저장된 기록으로 확인합니다. 최근 내담자 발화를 보고 제공된 상태보다 신뢰·공개 의향·자신감이 높아졌거나 낮아졌다고 해석하려면, 먼저 해당 상태를 조회하세요. 발화와 상태가 다른 이유를 시간 변화나 숨은 마음으로 설명하려는 경우에도, 기록에 그 근거가 있는지 확인하세요. state에는 trust, disclosure, change_confidence 중 확인할 항목을, reason에는 확인하려는 관계를 적습니다. 반환된 시점·출처·근거·이력을 참고하여 상담자 발화에 대한 느낌과 내담자 발화를 작성하세요. 확인되지 않은 변화 이유를 도구가 밝혀준 것처럼 말하지 마세요. 이미 필요한 기록이 있으면 생략하며, 이 도구는 상태를 갱신하지 않습니다.')]
 cfg=json.loads((SOURCE/'config.json').read_text());sc=json.loads((SOURCE/'scenario.json').read_text());
 current=json.loads((ROOT/'cases/lab_meeting.json').read_text())
 sc['task']=current['task']+'\n'+sc['task'].splitlines()[-1]
 sc['state_levels']=current['state_levels']
 for key in sc['states']:
  sc['states'][key]['level']='낮음';sc['states'][key]['meaning']=sc['state_levels'][key]['낮음']
 record=json.loads((SOURCE/'state_basis.json').read_text())
 dump(OUT/'scenario.json',sc);dump(OUT/'state_basis.json',record)
 cfg.update(scenario_file=str(OUT/'scenario.json'),state_basis_file=str(OUT/'state_basis.json'));dump(OUT/'config.json',cfg)
 body={'messages':[{'role':'user','content':sc['example_counselor_utterance']}],'system_prompt':'주어진 툴을 적절히 활용하세요.'};dump(OUT/'input.json',body)
 dump(OUT/'manifest.json',{'hypothesis':'Only function.description changes; all other model input fields and state record content remain identical.','source_case':str(SOURCE),'variants':[{'id':i,'label':label,'description':desc} for i,(label,desc) in enumerate(variants,1)],'generation':cfg['generation'],'original_tools_sha256':hashlib.sha256((ROOT/'tools.json').read_bytes()).hexdigest()})
 snapshot=OUT/'code_snapshot';snapshot.mkdir()
 for name in ['runtime.py','tool_loop.py','server.py','app_server.py','tools.json']:(snapshot/name).write_bytes((ROOT/name).read_bytes())
 reference=None;results=[]
 for i,(label,desc) in enumerate(variants,1):
  defs=copy.deepcopy(tools)
  next(t['function'] for t in defs if t['function']['name']=='inspect_state_basis')['description']=desc
  # Isolated process override only; production tools.json remains untouched.
  app_server.runtime.definitions=lambda *args,**kwargs:copy.deepcopy(defs)
  folder=OUT/f'case_{i:02d}';folder.mkdir();dump(folder/'tools.json',defs)
  server=app_server.make_server(OUT/'config.json',port=0,storage_dir=folder/'sessions');threading.Thread(target=server.serve_forever,daemon=True).start()
  try:
   req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/chat',data=json.dumps(body).encode(),headers={'X-Gemma-Request':'1','Content-Type':'application/json'})
   with urllib.request.urlopen(req,timeout=240) as response:events=[json.loads(line) for line in response]
   log=pathlib.Path(next(e['saved_path'] for e in events if e['type']=='request'));d=json.loads(log.read_text());payload=copy.deepcopy(d['model_calls'][0]['request'])
   observed=next(t['function'] for t in payload['tools'] if t['function']['name']=='inspect_state_basis');assert observed['description']==desc;observed['description']='<VARIANT>'
   if reference is None:reference=payload
   assert payload==reference,'Unexpected model input difference'
   inspection=d['model_calls'][0].get('inspection',{});prompt=inspection.get('prompt','')
   row={'id':i,'label':label,'description':desc,'status':d['status'],'response':d.get('response'),'tools':d.get('tool_events',[]),'agent_events':d.get('subagent_events',[]),'model_calls':len(d['model_calls']),'usage':d.get('usage'),'log':str(log),'only_description_changed':True,'description_in_rendered_prompt':desc in prompt}
   dump(folder/'result.json',row);results.append(row);dump(OUT/'results.json',results);print(json.dumps(row,ensure_ascii=False),flush=True)
  finally:server.shutdown();server.server_close()
 assert len(results)==5
 assert (ROOT/'tools.json').read_bytes()==(snapshot/'tools.json').read_bytes()
if __name__=='__main__':main()
