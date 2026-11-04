"""Controlled selection probe. No dialogue generation or production configuration edits."""
import argparse, copy, hashlib, json, sys, time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent.parent
sys.path.insert(0,str(PROJECT/'src'))
from client.memory_access.memory import retrieve, chunk, encode, digest
from client.memory_access.runtime import DEFAULTS
from client.memory_access.validation import validate

QUERY_PROMPT='''당신은 상담 내담자의 과거 비공개 기억을 찾기 위한 검색어 작성 담당입니다. 공통 프로필, 공개 대화와 마지막 상담사의 질문만 보고, 필요한 발화 당시 기록을 찾는 검색어 하나를 작성하세요. 공개된 사건·인물·시점과 질문의 초점을 보존하고 공개되지 않은 이유를 만들어 넣지 마세요. 후보 비공개 기록은 볼 수 없습니다. JSON {"query": "검색어"}만 반환하세요.'''
SELECTION_PROMPT=(PROJECT/'src/client/memory_access/selector.md').read_text()
QUERY_SCHEMA={'type':'object','properties':{'query':{'type':'string','minLength':1,'maxLength':2000}},'required':['query'],'additionalProperties':False}
SELECT_SCHEMA={'type':'object','properties':{'turns':{'type':'array','items':{'type':'integer','enum':list(range(1,31))},'maxItems':3}},'required':['turns'],'additionalProperties':False}

def write(path,obj):
 path.write_text(json.dumps(obj,ensure_ascii=False,indent=2))

def call(base, model, system, user, schema, seed, prefix):
 payload={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':encode(user)}],
          'temperature':0.7,'top_p':0.95,'max_tokens':4096,'seed':seed,'stream':False,
          'chat_template_kwargs':{'reasoning_effort':'low'},'structured_outputs':{'json':schema}}
 write(prefix.with_suffix('.request.json'),payload)
 start=time.monotonic()
 request=Request(base.rstrip('/')+'/chat/completions',data=encode(payload).encode(),headers={'Content-Type':'application/json'})
 with urlopen(request,timeout=180) as response: raw=json.load(response)
 latency=round((time.monotonic()-start)*1000,2)
 write(prefix.with_suffix('.response.json'),raw)
 choice=raw['choices'][0]
 if choice['finish_reason']!='stop': raise ValueError('Incomplete generation: '+choice['finish_reason'])
 parsed=validate(json.loads(choice['message']['content']),schema)
 if 'turns' in parsed and len(parsed['turns'])!=len(set(parsed['turns'])):raise ValueError('Duplicate selection')
 return parsed, {'latency_ms':latency,'usage':raw.get('usage',{})}

def evaluate(selected,gold):
 core=set(gold['core']);support=set(gold['supporting']);chosen=set(selected)
 return {'core_recall':len(core&chosen)/len(core) if core else None,
         'all_core_found':core<=chosen if core else None,
         'any_core_found':bool(core&chosen) if core else None,
         'relevant_precision':len(chosen&(core|support))/len(chosen) if chosen else None,
         'irrelevant_count':len(chosen-(core|support)),
         'confusable_count':len(chosen&set(gold['confusable'])),
         'abstained':not chosen,
         'correct_abstention':not chosen if not core else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--generation-url',default='http://192.168.0.94:8002/v1');ap.add_argument('--embedding-url',default='http://192.168.0.84:18002/v1');ap.add_argument('--repeats',type=int,default=2);ap.add_argument('--condition',choices=['short','long'],required=True);args=ap.parse_args()
 fixture=json.loads((ROOT/f'records_{args.condition}.json').read_text());questions=json.loads((ROOT/'questions.json').read_text())
 assert len(fixture['records'])==30
 out=ROOT/'runs'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+args.condition);out.mkdir(parents=True)
 # Freeze every input and evaluation criterion before any model call; gold is never sent.
 for name in ['records.json','questions.json','gold.json','RECORDS.md','QUESTIONS.md','GOLD.md','PROTOCOL.md']:
  source_name=f'records_{args.condition}.json' if name=='records.json' else f'RECORDS_{args.condition.upper()}.md' if name=='RECORDS.md' else name
  (out/name).write_bytes((ROOT/source_name).read_bytes())
 (out/'runner.py').write_bytes(Path(__file__).read_bytes())
 (out/'selector.md').write_text(SELECTION_PROMPT);(out/'query_prompt.md').write_text(QUERY_PROMPT)
 hashes={name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ['records.json','questions.json','gold.json']}
 options={**DEFAULTS,'embedding_backend':'openai','embedding_url':args.embedding_url}
 config={'condition':args.condition,'candidate_count':30,'generation_url':args.generation_url,'model':'Qwen/Qwen3.8-Flash-Next-FP8','embedding':options,'repeats':args.repeats,'seed_base':1104,'hashes':hashes,'query_generation':'mandatory separate single query, no hidden input','candidate_order':'chronological','generation_temperature':0.7}
 write(out/'config.json',config)
 print('OUTPUT '+str(out),flush=True)
 start=time.monotonic();results=[];done=0;total=len(questions)*args.repeats*2
 for repeat in range(args.repeats):
  for question in questions:
   common={'profile':fixture['profile'],'public_dialogue':fixture['public_dialogue']+[{'role':'counselor','content':question['question']}]}
   # Alternate order to reduce systematic warmup/server-order effects.
   methods=['rag','llm'] if (questions.index(question)+repeat)%2==0 else ['llm','rag']
   for method in methods:
    row={'condition':args.condition,'case':question['id'],'repeat':repeat+1,'method':method,'seed':1104+repeat}
    prefix=out/f"{question['id']}_r{repeat+1}_{method}"
    try:
     if method=='rag':
      parsed,query_metrics=call(args.generation_url,config['model'],QUERY_PROMPT,common,QUERY_SCHEMA,row['seed'],prefix)
      found=retrieve(fixture['records'],parsed['query'],options)
      row.update(status='ok',query=parsed['query'],selected=[r['turn'] for r in found['results']],retrieval=found,query_metrics=query_metrics,
                 latency_ms=round(query_metrics['latency_ms']+found['latency_ms'],2),usage=query_metrics['usage'])
     else:
      parsed,metrics=call(args.generation_url,config['model'],SELECTION_PROMPT,{**common,'private_turns':[chunk(r) for r in fixture['records']],'max_turns':3},SELECT_SCHEMA,row['seed'],prefix)
      row.update(status='ok',selected=parsed['turns'],**metrics)
    except Exception as error:
     row.update(status='error',error=repr(error))
    results.append(row);write(out/'results.json',results)
    done+=1;elapsed=time.monotonic()-start
    print(f"PROGRESS {args.condition} {done}/{total} ({100*done/total:.0f}%) {question['id']} r{repeat+1} {method}: {row.get('selected',row.get('error'))} elapsed={elapsed:.1f}s ETA={elapsed/done*(total-done):.0f}s",flush=True)
 # Evaluate only after all model selections, never use gold to build a prompt/query.
 gold={g['id']:g for g in json.loads((out/'gold.json').read_text())}
 for row in results:
  if row['status']=='ok':row['evaluation']=evaluate(row['selected'],gold[row['case']])
 write(out/'results.json',results)
 summary={}
 for method in ['rag','llm']:
  all_rows=[r for r in results if r['method']==method];ok=[r for r in all_rows if r['status']=='ok'];answerable=[r for r in ok if gold[r['case']]['core']];absent=[r for r in ok if not gold[r['case']]['core']]
  mean=lambda xs: sum(xs)/len(xs) if xs else None
  summary[method]={'completed':len(ok),'attempts':len(all_rows),'answerable_trials':len(answerable),
   'mean_core_recall':mean([r['evaluation']['core_recall'] for r in answerable]),
   'all_core_found':sum(r['evaluation']['all_core_found'] for r in answerable),
   'any_core_found':sum(r['evaluation']['any_core_found'] for r in answerable),
   'mean_relevant_precision':mean([r['evaluation']['relevant_precision'] for r in answerable if r['selected']]),
   'missing_event_trials':len(absent),'correct_abstentions':sum(r['evaluation']['correct_abstention'] for r in absent),
   'mean_latency_ms':mean([r['latency_ms'] for r in ok]),
   'mean_input_tokens':mean([r['usage'].get('prompt_tokens',0) for r in ok]),
   'mean_output_tokens':mean([r['usage'].get('completion_tokens',0) for r in ok])}
 write(out/'summary.json',summary)
 print('SUMMARY '+json.dumps(summary,ensure_ascii=False),flush=True)
 print('DONE '+str(out),flush=True)
if __name__=='__main__':main()
