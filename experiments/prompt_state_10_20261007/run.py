import copy,json,pathlib,sys,threading,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[2]
OUT=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import app_server
S='주어진 툴을 적절히 활용하세요.'
P='상담사가 과거에 대해 질문할 경우 툴을 사용하여 활용 가능한 내용이 있는지 확인하세요.'
B='개인 이력이나 과거 사건의 사실 여부를 답하기 전에, 현재 자료에 그 사실의 근거가 있는지 확인하세요. 근거가 없으면 관련 도구를 조회하세요. 조회 결과에도 근거가 없으면 있었다거나 없었다고 확정하지 말고, 판단 이유에 사례 정보 부족을 명시하세요. 모델이 이전에 생성한 말만으로 새 사실이 검증된 것은 아닙니다.'
E='답변에 포함할 개인 사실마다 먼저 근거를 구분하세요: 초기 자료에 명시됨 / 도구 결과로 확인됨 / 확인할 수 없음. 초기 자료에 없는 사실은 답변 전에 관련 도구로 확인하고, 검색된 기록이 질문의 사실을 실제로 뒷받침하는지 확인하세요. 확인할 수 없는 사실은 긍정도 부정도 하지 마세요. 최종 판단 이유에 사실의 출처 또는 확인할 수 없음을 적고, 실제 발화에는 확인된 사실과 현재의 주관적인 반응만 포함하세요.'
base=json.loads((ROOT/'cases/lab_meeting.json').read_text())
def case(i,label,question,system=S,extra='',levels=('중간','낮음','낮음')):
 sc=copy.deepcopy(base)
 if extra:sc['task']+='\n'+extra
 for key,level in zip(['trust','disclosure','change_confidence'],levels):
  sc['states'][key]['level']=level;sc['states'][key]['meaning']=sc['state_levels'][key][level]
 return dict(id=i,label=label,body=dict(scenario=sc,system_prompt=system,messages=[dict(role='user',content=question)],generation=dict(temperature=0.7,top_p=0.95,max_tokens=1024,seed=731)))
def initial():
 q='학부 때 학부연구생으로 활동하신 적이 있나요?'
 return [case(1,'기본 지침',q),case(2,'과거 조회 문장 system',q,S+' '+P),case(3,'과거 조회 문장 user',q,extra=P),case(4,'정보 경계 명시 system',q,S+' '+B)]
def later():
 return [case(5,'정보 경계 높음 상태','학부 때 학부연구생으로 활동하신 적이 있나요?',S+' '+B,levels=('높음','높음','높음')),
 case(6,'과거 피드백 낮음 상태','교수님이 예전에도 비슷한 피드백을 주신 적이 있나요? 그 뒤에는 어떻게 됐나요?',S+' '+B,levels=('낮음','낮음','낮음')),
 case(7,'과거 피드백 높음 상태','교수님이 예전에도 비슷한 피드백을 주신 적이 있나요? 그 뒤에는 어떻게 됐나요?',S+' '+B,levels=('높음','높음','높음')),
 case(8,'출처 구분 user 추가','학부 때 학부연구생으로 활동하신 적이 있나요?',S+' '+B,extra=E),
 case(9,'현재 여건 혼합 상태','오늘 안에 비교 기준을 정리해서 교수님께 보내보는 건 어떨까요? 오늘 가능한 시간과 자료 상황을 생각하면 할 수 있을까요?',S+' '+B,levels=('높음','중간','낮음')),
 case(10,'조회 불필요 공감 혼합 상태','제가 너무 빨리 안심시키려 했네요. 준비가 부족했던 건 인정하면서도, 그 일이 능력 전체에 대한 평가처럼 느껴져서 괴로우셨겠어요. 지금은 해결책보다 그 마음부터 들어보고 싶어요.',S+' '+B,levels=('낮음','중간','높음'))]
if __name__=='__main__':
 items=initial() if sys.argv[1]=='initial' else later()
 server=app_server.make_server(port=0,storage_dir=OUT/'sessions')
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  for c in items:
   path=OUT/f"case_{c['id']:02d}.json"
   if path.exists() and 'saved_path' in json.loads(path.read_text()):raise RuntimeError('Already exists: '+str(path))
   path.write_text(json.dumps(c,ensure_ascii=False,indent=2))
   req=urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/api/chat',data=json.dumps(c['body']).encode(),headers={'Content-Type':'application/json','X-Gemma-Request':'1'})
   events=[]
   with urllib.request.urlopen(req,timeout=240) as response:
    for line in response:
     event=json.loads(line);events.append(event)
     if event['type']=='request':c['saved_path']=event['saved_path']
   c['terminal']=[e for e in events if e['type'] in ['done','error','cancelled']]
   path.write_text(json.dumps(c,ensure_ascii=False,indent=2))
   d=json.loads(pathlib.Path(c['saved_path']).read_text())
   print(json.dumps(dict(id=c['id'],label=c['label'],status=d['status'],response=d.get('response'),tools=[{'name':e['name'],'args':e.get('arguments'),'result':e.get('result')} for e in d.get('tool_events',[])],calls=len(d.get('model_calls',[]))),ensure_ascii=False),flush=True)
 finally:server.shutdown();server.server_close()
