"""Known-N source selection: long records and contextual reference resolution."""
import argparse,hashlib,json,random,sys,time
from datetime import datetime
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent.parent
sys.path.insert(0,str(PROJECT/'src'))
from client.memory_access.memory import retrieve,chunk,encode
from client.memory_access.runtime import DEFAULTS
from client.memory_access.validation import validate
QUERY_PROMPT='''당신은 상담 내담자의 과거 비공개 기억을 찾기 위한 검색어 작성 담당입니다. 공통 프로필과 전체 공개 대화를 읽고 마지막 질문의 그때·그 말·그 두 번 같은 지시어가 어느 과거 발화들을 가리키는지 직전 문맥으로 해석하세요. 찾을 대상 발화 당시 원본의 수는 target_count로 제공됩니다. 후일 회고나 유사한 다른 사건 대신 그 당시 원본을 찾도록 공개된 사건·시점·비교 대상을 보존한 검색어 하나를 작성하세요. 공개되지 않은 심리 이유를 만들어 넣지 마세요. 비공개 후보 기록은 볼 수 없습니다. JSON {"query": "검색어"}만 반환하세요.'''
SELECT_PROMPT='''당신은 상담 내담자의 과거 기억 선별 담당입니다. 공통 프로필, 전체 공개 대화와 과거 턴별 비공개 기록을 읽고 마지막 질문이 가리키는 과거 발화 당시 원본 기록을 선택하세요. 마지막 질문의 그때·그 말·그 두 번 같은 지시어는 직전 공개 문맥을 이용해 해석하세요. 후일 회고만으로 답을 설명할 수 있어도 대상 발화 당시 원본을 대신 선택하지 마세요. 같은 말이나 인물이 나오는 다른 사건, 현재 마음을 과거의 이유와 혼동하지 마세요. target_count개를 정확히 선택하세요. 반환 순서는 중요하지 않습니다. 새로운 심리 해석이나 요약을 작성하지 마세요. JSON {"turns": [정수 턴 번호]}만 반환하세요.'''
QUERY_SCHEMA={'type':'object','properties':{'query':{'type':'string','minLength':1,'maxLength':2000}},'required':['query'],'additionalProperties':False}
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2))
def call(url,model,system,source,schema,seed,prefix):
 payload={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':encode(source)}],'temperature':0.7,'top_p':0.95,'max_tokens':4096,'seed':seed,'stream':False,'chat_template_kwargs':{'reasoning_effort':'low'},'structured_outputs':{'json':schema}}
 write(prefix.with_suffix('.request.json'),payload);started=time.monotonic()
 with urlopen(Request(url.rstrip('/')+'/chat/completions',data=encode(payload).encode(),headers={'Content-Type':'application/json'}),timeout=180) as response:raw=json.load(response)
 ms=round((time.monotonic()-started)*1000,2);write(prefix.with_suffix('.response.json'),raw)
 choice=raw['choices'][0]
 if choice['finish_reason']!='stop':raise ValueError('Incomplete generation: '+choice['finish_reason'])
 parsed=validate(json.loads(choice['message']['content']),schema)
 if 'turns' in parsed and len(set(parsed['turns']))!=len(parsed['turns']):raise ValueError('duplicate IDs')
 return parsed,{'latency_ms':ms,'usage':raw.get('usage',{})}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--generation-url',default='http://192.168.0.94:8002/v1');ap.add_argument('--embedding-url',default='http://192.168.0.84:18002/v1');a=ap.parse_args()
 f=json.loads((ROOT/'records.json').read_text());qs=json.loads((ROOT/'questions.json').read_text());model='Qwen/Qwen3.8-Flash-Next-FP8'
 # No gold IDs needed to run: N is declared as part of each public task.
 assert len(f['records'])==30 and len(qs)==12
 out=ROOT/'runs'/datetime.now().strftime('%Y%m%d_%H%M%S');out.mkdir(parents=True)
 for name in ['records.json','questions.json','gold.json','RECORDS.md','QUESTIONS.md','GOLD.md','PROTOCOL.md','token_preflight.json']:(out/name).write_bytes((ROOT/name).read_bytes())
 (out/'runner.py').write_bytes(Path(__file__).read_bytes());(out/'selector.md').write_text(SELECT_PROMPT);(out/'query_prompt.md').write_text(QUERY_PROMPT)
 config={'model':model,'generation_url':a.generation_url,'embedding_url':a.embedding_url,'repeats':2,'temperature':0.7,'top_p':0.95,'max_tokens':4096,'reasoning_effort':'low','seeds':[1104,1105],'orders':['chronological','shuffle seed 20261104'],'known_n':True,'hashes':{n:hashlib.sha256((out/n).read_bytes()).hexdigest() for n in ['records.json','questions.json','gold.json']}}
 write(out/'config.json',config);results=[];started=time.monotonic();done=0;total=48
 options={**DEFAULTS,'embedding_backend':'openai','embedding_url':a.embedding_url}
 print('OUTPUT '+str(out),flush=True)
 for repeat in range(2):
  records=list(f['records'])
  if repeat:random.Random(20261104).shuffle(records)
  write(out/f'candidate_order_r{repeat+1}.json',[r['turn'] for r in records])
  for qi,q in enumerate(qs):
   n=q['target_count'];common={'profile':f['profile'],'public_dialogue':f['public_dialogue']+q['recent_context']+[{'role':'counselor','content':q['question']}],'target_count':n}
   methods=['rag','llm'] if (qi+repeat)%2==0 else ['llm','rag']
   for method in methods:
    row={'case':q['id'],'repeat':repeat+1,'method':method,'target_count':n,'seed':1104+repeat};prefix=out/f"{q['id']}_r{repeat+1}_{method}"
    try:
     if method=='rag':
      parsed,met=call(a.generation_url,model,QUERY_PROMPT,common,QUERY_SCHEMA,row['seed'],prefix)
      found=retrieve(records,parsed['query'],{**options,'top_k':n})
      row.update(query=parsed['query'],selected=[r['turn'] for r in found['results']],retrieval=found,query_metrics=met,latency_ms=round(met['latency_ms']+found['latency_ms'],2),usage=met['usage'])
     else:
      schema={'type':'object','properties':{'turns':{'type':'array','items':{'type':'integer','enum':list(range(1,31))},'minItems':n,'maxItems':n}},'required':['turns'],'additionalProperties':False}
      parsed,met=call(a.generation_url,model,SELECT_PROMPT,{**common,'private_turns':[chunk(r) for r in records]},schema,row['seed'],prefix)
      row.update(selected=parsed['turns'],**met)
     assert len(row['selected'])==n and len(set(row['selected']))==n
     row['status']='ok'
    except Exception as err:row.update(status='error',error=repr(err))
    results.append(row);write(out/'results.json',results);done+=1;elapsed=time.monotonic()-started
    print(f"PROGRESS {done}/{total} ({100*done/total:.0f}%) {q['id']} r{repeat+1} {method} N={n}: {row.get('selected',row.get('error'))} elapsed={elapsed:.1f}s ETA={elapsed/done*(total-done):.0f}s",flush=True)
 # IDs used for scoring only after all calls.
 gold={r['id']:r for r in json.loads((out/'gold.json').read_text())}
 for r in results:
  if r['status']!='ok':continue
  true=set(gold[r['case']]['core']);chosen=set(r['selected']);assert len(true)==r['target_count']
  r['evaluation']={'exact_match':chosen==true,'precision':len(chosen&true)/len(chosen),'recall':len(chosen&true)/len(true),'missing':sorted(true-chosen),'wrong':sorted(chosen-true)}
 write(out/'results.json',results);summary={}
 for method in ['rag','llm']:
  rs=[r for r in results if r['method']==method];ok=[r for r in rs if r['status']=='ok'];mean=lambda xs:sum(xs)/len(xs) if xs else None
  summary[method]={'attempts':len(rs),'completed':len(ok),'exact_match':sum(r['evaluation']['exact_match'] for r in ok),'mean_precision_recall':mean([r['evaluation']['recall'] for r in ok]),'mean_latency_ms':mean([r['latency_ms'] for r in ok]),'mean_prompt_tokens':mean([r['usage'].get('prompt_tokens',0) for r in ok]),'mean_completion_tokens':mean([r['usage'].get('completion_tokens',0) for r in ok]),'by_n':{str(n):{'count':sum(r['target_count']==n for r in ok),'exact':sum(r['evaluation']['exact_match'] for r in ok if r['target_count']==n),'recall':mean([r['evaluation']['recall'] for r in ok if r['target_count']==n])} for n in [1,2,3]}}
 write(out/'summary.json',summary);print('SUMMARY '+json.dumps(summary),flush=True);print('DONE '+str(out),flush=True)
if __name__=='__main__':main()
