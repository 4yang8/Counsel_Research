"""Post-hoc diagnostic: use every original question verbatim, without query rewriting."""
import json,sys
from pathlib import Path
from run_experiment import retrieve,DEFAULTS,evaluate,write
p=Path(sys.argv[1]);assert (p/'summary.json').exists(), 'Run primary comparison first'
plan='''# 사후 진단 계획

본 비교에서 두 시점을 비교하는 질문의 일부가 검색어 작성 중 빠진 사례가 관찰됐다. 검색어 생성 손실과 단일 벡터 Top-3 검색의 한계를 구별하기 위해, 정답이나 기록을 이용해 검색어를 고치지 않고 원래 질문 전체를 그대로 검색어로 사용한다. 전체 10개 질문을 1회씩 실행한다. 기존 본 실험을 대체하거나 사전 계획된 비교로 취급하지 않는다. 후보·청크·임베딩 모델·Top-3는 동일하다. 공개 대화를 검색어에 모두 붙이지 않는다. 따라서 질문 전체 입력 진단이며 원래 주어지는 공개 대화 전체를 직접 읽는 방법과 동일한 조건은 아니다.
'''
(p/'POSTHOC_DIRECT_QUERY.md').write_text(plan)
f=json.loads((p/'records.json').read_text());qs=json.loads((p/'questions.json').read_text());g={x['id']:x for x in json.loads((p/'gold.json').read_text())}
config=json.loads((p/'config.json').read_text());options=config['embedding'];rows=[]
for q in qs:
 found=retrieve(f['records'],q['question'],options)
 selected=[r['turn'] for r in found['results']]
 rows.append({'case':q['id'],'selected':selected,'retrieval':found,'evaluation':evaluate(selected,g[q['id']])})
write(p/'direct_query_results.json',rows)
for r in rows:print(r['case'],r['selected'],r['evaluation'])
