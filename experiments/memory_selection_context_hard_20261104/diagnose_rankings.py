"""Post-hoc replay of failed RAG rankings with original queries; no query edits."""
import json,sys
from pathlib import Path
from run_experiment import retrieve,DEFAULTS,write
p=Path(sys.argv[1]);f=json.loads((p/'records.json').read_text());gold={x['id']:x['core'] for x in json.loads((p/'gold.json').read_text())};rows=json.loads((p/'results.json').read_text());cfg=json.loads((p/'config.json').read_text())
options={**DEFAULTS,'embedding_backend':'openai','embedding_url':cfg['embedding_url'],'top_k':30};out=[]
for r in rows:
 if r['method']!='rag' or r['status']!='ok' or r['evaluation']['exact_match']:continue
 result=retrieve(f['records'],r['query'],options)
 ranked={s['turn']:{'rank':i+1,'similarity':s['similarity']} for i,s in enumerate(result['scores'])}
 top=[x['turn'] for x in result['results'][:r['target_count']]]
 row={'case':r['case'],'repeat':r['repeat'],'query':r['query'],'original_selected':r['selected'],'replayed_top_n':top,'same_top_n':top==r['selected'],'gold_ranks':{str(n):ranked[n] for n in gold[r['case']]},'all_scores':result['scores']}
 out.append(row);print(row['case'],row['repeat'],row['gold_ranks'],'same_top_n',row['same_top_n'])
write(p/'ranking_diagnosis.json',out)
