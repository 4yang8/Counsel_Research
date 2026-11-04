"""Supplement: hold every short-condition generated query fixed for both corpora."""
import argparse,json
from pathlib import Path
from run_experiment import retrieve,evaluate,write
ROOT=Path(__file__).resolve().parent
ap=argparse.ArgumentParser();ap.add_argument('--short',type=Path,required=True);ap.add_argument('--long',type=Path,required=True);a=ap.parse_args()
assert (a.short/'summary.json').exists() and (a.long/'summary.json').exists()
fixtures={c:json.loads((p/'records.json').read_text()) for c,p in [('short',a.short),('long',a.long)]}
gold={r['id']:r for r in json.loads((a.short/'gold.json').read_text())}
source=[r for r in json.loads((a.short/'results.json').read_text()) if r['method']=='rag' and r['status']=='ok']
options=json.loads((a.long/'config.json').read_text())['embedding']
plan='''# 보조 진단: 같은 검색어를 두 기록 길이에 적용

본 비교는 동일한 공개 입력과 seed를 주지만 검색어를 별도 모델 호출로 생성하므로 두 길이 조건에서 검색어가 달라질 수 있다. 이 영향을 제거하기 위해 짧은 조건에서 실제 생성된 20개 검색어 전체를 그대로 짧은/긴 후보군에 각각 검색한다. 정답으로 검색어를 고치거나 유리한 질문만 고르지 않는다. 생성 LLM을 다시 호출하지 않는다. RAG 검색 단계만의 보조 진단이며 기존 본 실험 결과와 합산하지 않는다. 검색어의 품질을 개선하는 진단도 아니다.
'''
(ROOT/'MATCHED_QUERY_PROTOCOL.md').write_text(plan)
rows=[]
for r in source:
 for c in ['short','long']:
  found=retrieve(fixtures[c]['records'],r['query'],options)
  selected=[x['turn'] for x in found['results']]
  rows.append({'condition':c,'case':r['case'],'repeat':r['repeat'],'query':r['query'],'selected':selected,'retrieval':found,'evaluation':evaluate(selected,gold[r['case']])})
write(ROOT/'matched_query_results.json',rows)
summary={}
for c in ['short','long']:
 answerable=[r for r in rows if r['condition']==c and gold[r['case']]['core']]
 summary[c]={'answerable_trials':len(answerable),'all_core_found':sum(r['evaluation']['all_core_found'] for r in answerable),'mean_core_recall':sum(r['evaluation']['core_recall'] for r in answerable)/len(answerable),'mean_relevant_precision':sum(r['evaluation']['relevant_precision'] for r in answerable)/len(answerable)}
write(ROOT/'matched_query_summary.json',summary);print(json.dumps(summary))
