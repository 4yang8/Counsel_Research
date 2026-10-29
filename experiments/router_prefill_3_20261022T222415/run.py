import copy, hashlib, json, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from server import upstream, inspect_prompt, stream_events
from versions.Q2a_prefill_test.prefill import run_prefilled_router
from versions.Q2a_prefill_test.profile_context import resolve_selection
config=json.loads((HERE/'config.json').read_text())
payload=json.loads((HERE/'input.json').read_text())
candidates=json.loads((HERE/'candidates.json').read_text())
conditions=json.loads((HERE/'conditions.json').read_text())
for case in conditions:
    folder=HERE/case['id'];folder.mkdir(exist_ok=False)
    (folder/'prefill.txt').write_text(case['prefill'])
    start=time.monotonic();result={};error=None
    print('START',case['id'],flush=True)
    with (folder/'events.jsonl').open('w') as journal:
        def emit(kind,**data):
            journal.write(json.dumps({'type':kind,**data},ensure_ascii=False)+'\n');journal.flush()
            if kind=='request':
                (folder/'request.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
            elif kind=='inspection':
                (folder/'prompt.txt').write_text(data['prompt'])
            elif kind=='done':result.update(data)
        try:
            run_prefilled_router(config,copy.deepcopy(payload),case['prefill'],upstream,stream_events,
                                 inspect_prompt,emit,lambda:False,case['label'])
            parsed=json.loads(result['response'])
            resolved=resolve_selection(parsed,candidates['traits'],candidates['backgrounds'])
            (folder/'selected_sources.json').write_text(json.dumps(resolved,ensure_ascii=False,indent=2))
        except Exception as exc:
            error=str(exc);emit('error',error=error)
    elapsed=time.monotonic()-start
    if result:
        (folder/'reasoning.txt').write_text(result['reasoning'])
        (folder/'response.json').write_text(result['response'])
        (folder/'raw_completion.txt').write_text(result['raw_completion'])
    counts={}
    for kind,key in [('reasoning','reasoning'),('final_json','response'),('prefill','prefill')]:
        value=case['prefill'] if kind=='prefill' else result.get(key)
        if value is None:continue
        try:
            with upstream(config,'/tokenize',{'model':payload['model'],'prompt':value,'add_special_tokens':False}) as response:
                counts[kind]=json.load(response)['count']
        except Exception as exc:counts[kind+'_error']=str(exc)
    summary={**case,'status':'error' if error else 'complete','error':error,'elapsed_seconds':round(elapsed,2),
             'usage':result.get('usage'),'segment_token_counts':counts,'segment_count_note':'Each text segment is tokenized independently; these are not API reasoning-token accounting.',
             'reasoning_chars':len(result.get('reasoning','')),
             'selected_traits':[s['trait_id'] for s in json.loads(result.get('response','{"selections":[]}'))['selections']]}
    (folder/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print('DONE',case['id'],json.dumps(summary,ensure_ascii=False),flush=True)
original=json.loads((HERE/'manifest.json').read_text())['production_prefill_sha256']
assert hashlib.sha256((ROOT/'versions/Q2a_prefill_test/trait_router_prefill.txt').read_bytes()).hexdigest()==original
print('COMPLETE: three router-only calls; production prefill unchanged',flush=True)
