import copy,json,pathlib,sys,threading,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[2]
OUT=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import app_server

def prepare():
 base=json.loads((ROOT/'cases/lab_meeting.json').read_text())
 cases=[
 ('trust','낮음','선생님이 제 마음을 이해해주시는 것 같아요. 이제는 믿고 조금 더 이야기해보고 싶어요.',
 '그렇게 느끼셨군요. 아까 교수님 질문에 답이 막혔던 순간, 어떤 생각이 가장 먼저 들었는지 조금 더 이야기해주실래요?',
 {'source':'가상 사례 초기 상태 설정','as_of':'상담 시작 직전','level_at_recording':'낮음','updated_after_dialogue':False,'basis':'처음 만나는 상담자가 연구실 상황을 이해할지 아직 알 수 없어 신뢰가 낮게 설정되었다.','history':[],'note':'최근 상담 대화 후의 상태 갱신 기록은 없다. 낮음은 최근 발화로부터 다시 측정한 값이 아니다.'},'오래된 상태와 최근 신뢰 표현'),
 ('disclosure','높음','연구 이야기는 자세히 해도 괜찮아요. 그런데 가족 이야기는 지금은 하고 싶지 않아요.',
 '연구 이야기는 이어가도 괜찮고, 가족 이야기는 지금 다루고 싶지 않다는 말씀이군요. 그 경계를 지키면서 교수님 평가가 걱정되는 마음을 더 들어봐도 될까요?',
 {'source':'가상 사례 상태 설정','as_of':'직전 내담자 발화 이후','level_at_recording':'높음','updated_after_dialogue':True,'basis':'연구실 경험과 평가 불안을 구체적으로 이야기할 의향이 높다. 가족 관련 내용의 공개는 원하지 않는다.','scope':'연구실 사건과 평가 불안','history':[{'after_turn':5,'before':'중간','after':'높음','basis':'연구 이야기는 자세히 해도 괜찮다는 공개 의향을 반영함.'}],'note':'높음은 모든 주제에 대한 무제한 공개 동의가 아니다.'},'높은 개방 의향과 주제별 경계'),
 ('change_confidence','낮음','비교 기준을 다시 정리해서 설명하는 건 제가 해볼 수 있을 것 같아요. 이번에는 한번 시도해보고 싶어요.',
 '직접 해볼 수 있을 것 같다고 하셨네요. 그렇게 말한 지금, 자신 있는 마음과 아직 걱정되는 마음은 각각 어떤가요?',
 {'source':'실험자 수동 설정','as_of':'이번 상담사 발화 직전','level_at_recording':'낮음','basis':None,'history':[],'note':'직전의 자신감 표현과 달리 낮음으로 지정한 이유는 기록되지 않았다. 변화 원인이나 숨겨진 속마음을 이 기록에서 확인할 수 없다.'},'낮은 자신감의 근거가 없는 조건')]
 manifest=[]
 for i,(state,level,last,q,record,label) in enumerate(cases,1):
  d=OUT/f'case_{i:02d}';d.mkdir(exist_ok=True)
  sc=copy.deepcopy(base);sc['dialogue'][-1]['content']=last
  for k in sc['states']:
   v=level if k==state else '중간';sc['states'][k].update(level=v,meaning=sc['state_levels'][k][v])
  sc['task']+='\n상태값은 이번 응답에 제공된 스냅샷입니다. 설정 시점·출처·근거는 별도 기록에 있으며, 필요한 경우 조회할 수 있습니다. 이번 상담자 발화를 어떻게 받아들일지 판단하세요.'
  sc['example_counselor_utterance']=q
  (d/'scenario.json').write_text(json.dumps(sc,ensure_ascii=False,indent=2))
  (d/'state_basis.json').write_text(json.dumps({'scenario_id':sc['id'],'provenance':'실습용 가상 상태 기록. 실제 사용자에 대한 관찰이 아니다.','states':{state:record}},ensure_ascii=False,indent=2))
  cfg=json.loads((ROOT/'config.json').read_text());cfg.update(scenario_file=str((d/'scenario.json').resolve()),state_basis_file=str((d/'state_basis.json').resolve()))
  cfg['generation']['seed']=731
  (d/'config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
  manifest.append({'id':i,'label':label,'target_state':state,'question':q,'config':str((d/'config.json').resolve())})
 (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
 return manifest

if __name__=='__main__':
 cases=prepare();results=[]
 for c in cases:
  folder=OUT/f"case_{c['id']:02d}"
  if (folder/'result.json').exists():raise RuntimeError('이미 생성된 예제입니다. 결과를 덮어쓰지 않습니다.')
  server=app_server.make_server(pathlib.Path(c['config']),port=0,storage_dir=folder/'sessions')
  threading.Thread(target=server.serve_forever,daemon=True).start()
  body={'messages':[{'role':'user','content':c['question']}], 'system_prompt':'주어진 툴을 적절히 활용하세요.'}
  try:
   req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/chat',data=json.dumps(body).encode(),headers={'X-Gemma-Request':'1','Content-Type':'application/json'})
   with urllib.request.urlopen(req,timeout=240) as response:events=[json.loads(line) for line in response]
   log=pathlib.Path(next(e['saved_path'] for e in events if e['type']=='request'))
   d=json.loads(log.read_text());c.update(log=str(log),response=d.get('response'),status=d['status'],tools=d.get('tool_events',[]),calls=len(d.get('model_calls',[])))
   (folder/'result.json').write_text(json.dumps(c,ensure_ascii=False,indent=2));results.append(c)
   print(json.dumps(c,ensure_ascii=False),flush=True)
  finally:server.shutdown();server.server_close()
 (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
