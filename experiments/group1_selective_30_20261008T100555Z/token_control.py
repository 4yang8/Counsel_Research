"""Tokenize matched prompts with identical tools; no model generation."""
import copy,json,sys
from pathlib import Path
from urllib.request import Request,urlopen
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
BASE=OUT.parent/'group1_full_context_15_20261008T063458Z'
sys.path.insert(0,str(ROOT))
import app_server
from profile_retrieval import initial_profile,TOOL_NAMES
cfg=json.loads((OUT/'config.json').read_text())
tools=[t for t in json.loads((OUT/'snapshot/tools.json').read_text()) if t['function']['name'] in TOOL_NAMES]
endpoint=cfg['vllm_url'].removesuffix('/v1').rstrip('/')+'/tokenize'
rows=[]
for r in json.loads((BASE/'results.json').read_text()):
    folder=BASE/('case_'+r['case_id'])
    full=json.loads((folder/'payload.json').read_text())
    body=json.loads((folder/'input.json').read_text())
    body['scenario']['profile']=initial_profile(body['scenario']['profile'])
    slim=copy.deepcopy(full)
    slim['messages'][1]['content']=app_server.runtime.initial_context(body['scenario'])+'\n\n[이번 상담자 발화]\n'+body['messages'][0]['content']
    counts={}
    for name,payload in [('full_same_two_tools',full),('selective_same_two_tools',slim)]:
        data={'model':payload['model'],'messages':payload['messages'],'tools':tools,
            'chat_template_kwargs':payload['chat_template_kwargs'],'add_generation_prompt':True}
        with urlopen(Request(endpoint,data=json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'}),timeout=20) as response:
            counts[name]=json.load(response)['count']
    rows.append({'case_id':r['case_id'],**counts,'saved_tokens':counts['full_same_two_tools']-counts['selective_same_two_tools']})
(OUT/'token_control.json').write_text(json.dumps({'note':'Same model template and same two retrieval tool schemas, only initial profile content differs. Tokenization only; not a third generation set.','cases':rows},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'cases':len(rows),'full_mean':sum(r['full_same_two_tools'] for r in rows)/15,'selective_mean':sum(r['selective_same_two_tools'] for r in rows)/15,'saved_mean':sum(r['saved_tokens'] for r in rows)/15}))
