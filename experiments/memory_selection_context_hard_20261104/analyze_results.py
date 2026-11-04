"""Summarize known-N source retrieval, including incomplete generations."""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);a=ap.parse_args();p=a.run.resolve()
rows=json.loads((p/'results.json').read_text());summ=json.loads((p/'summary.json').read_text());gold={g['id']:g for g in json.loads((p/'gold.json').read_text())};qs=json.loads((p/'questions.json').read_text());config=json.loads((p/'config.json').read_text())
for n,h in config['hashes'].items():assert hashlib.sha256((p/n).read_bytes()).hexdigest()==h
lines=['# 장문·문맥 의존·동일 N 선별 결과','','## 비교 범위','','후보30턴, 비공개 기록 평균1078자, 공개 문맥이 필요한 질문12개, 두 반복이다. N=1/2/3을 양쪽에 똑같이 알려주고 정확히 N개만 선택한다. 정답이 없는 질문은 없다. 첫 반복은 후보 시간순, 두 번째는 후보만 고정 seed로 섞었으며 공개 대화의 시간 순서는 유지했다. 모든 실제 요청·응답은 이 실행 폴더에 저장했다.','','정답은 대상 발화를 만들 당시 원본 ID다. 나중의 회고나 유사한 다른 발화는 답을 설명할 수 있어도 해당 원본 대신 선택하면 오답이다. 이 제한은 양쪽 지침에 명시했다. 작성자 지정 정답이고 독립 검증은 없으며, 임상적 타당성이나 실제 후속 응답 품질을 측정한 실험은 아니다.','','## 전체 결과','','| 항목 | RAG | 전체 기록 LLM |','|---|---:|---:|']
for label,fn in [('정상 완료',lambda s:f"{s['completed']}/{s['attempts']}"),('정답 ID 집합 완전 일치 (오류 포함 전체 시도 기준)',lambda s:f"{s['exact_match']}/{s['attempts']}"),('정상 완료에서 평균 precision=recall',lambda s:f"{s['mean_precision_recall']:.1%}"),('정상 완료의 평균 선별 시간',lambda s:f"{s['mean_latency_ms']/1000:.2f}초"),('정상 완료의 평균 입력 토큰',lambda s:f"{s['mean_prompt_tokens']:.1f}"),('정상 완료의 평균 출력 토큰',lambda s:f"{s['mean_completion_tokens']:.1f}")]:lines.append(f"| {label} | {fn(summ['rag'])} | {fn(summ['llm'])} |")
lines+=['','실패가 있으면 정상 완료 평균과 전체 성공률을 분리해서 읽어야 한다. RAG 시간은 검색어 생성+임베딩/검색, LLM은 원본 선별 시간이다. 생성 모델 토큰만 집계하며 임베딩 GPU 및 후속 발화 비용은 포함하지 않는다. 캐시·서버 부하를 통제한 속도 벤치마크는 아니다.','','## N별 완전 일치','','| N | RAG | LLM |','|---:|---:|---:|']
for n in [1,2,3]:
 values=[]
 for m in ['rag','llm']:
  rr=[r for r in rows if r['method']==m and r['target_count']==n]
  values.append(f"{sum(r.get('evaluation',{}).get('exact_match',False) for r in rr)}/{len(rr)}")
 lines.append(f'| {n} | {values[0]} | {values[1]} |')
lines+=['','## 질문별 원본 ID','','| 질문 | N | 정답 | RAG 1회 / 2회 | LLM 1회 / 2회 |','|---|---:|---|---|---|']
for q in qs:
 def sels(m):return ' / '.join(str(r.get('selected','ERROR')) if r['status']=='ok' else 'ERROR' for r in rows if r['case']==q['id'] and r['method']==m)
 lines.append(f"| {q['id']} | {q['target_count']} | {gold[q['id']]['core']} | {sels('rag')} | {sels('llm')} |")
lines+=['','## 실패·누락 원문 점검','']
for r in rows:
 if r['status']!='ok':
  lines+=[f"- {r['case']} 반복{r['repeat']} {r['method']}: {r['error']}"]
  response=p/f"{r['case']}_r{r['repeat']}_{r['method']}.response.json"
  if response.exists():
   raw=json.loads(response.read_text());lines += [f"  - 종료: {raw['choices'][0]['finish_reason']}, usage: {raw.get('usage')}"]
 elif not r['evaluation']['exact_match']:
  lines += [f"- {r['case']} 반복{r['repeat']} {r['method']}: 선택{r['selected']}, 누락{r['evaluation']['missing']}, 다른 기록{r['evaluation']['wrong']}"]
  if r['method']=='rag':lines += [f"  - 검색어: {r['query']}"]
lines+=['','## 해석 제한','','- 두 방법의 반환 수는 동일하다. 따라서 이번 결과를 이전의 RAG 고정3개/LLM 최대3개 결과와 그대로 비교해서는 안 된다.','- 기록 제목 제거, 같은 인물로 사건 변형, 직전 문맥 추가, 원본 선별 지침 및 N 통제가 함께 바뀌었다. 단일 난도 요인의 인과 실험은 아니다.','- 난도를 높이도록 설계했지만 모델이 계속 모두 맞히면 해당 조건에서 원본 선별이 안정적이라는 관측까지만 말한다. 오류가 날 때까지 문항을 바꾸지 않았다.','- 두 번째 반복은 seed와 후보 순서가 함께 바뀐다. 순서 효과를 별도로 추정할 수 없다.','- 검색어 생성 실패/비교 대상 누락과 임베딩 순위 실패를 구분해야 한다. RAG 자체 전체를 단일 오류 원인으로 취급하지 않는다.','- 한 인공 내담자와 두 반복은 독립된 다수 상담 사례가 아니다. 신뢰구간·통계적 유의성·일반적 우열을 주장하지 않는다.','','## 자료','','- [후보 원문](RECORDS.md)','- [질문과 직전 문맥](QUESTIONS.md)','- [정답 기준](GOLD.md)','- [사전 계획](PROTOCOL.md)','- [원본 결과 JSON](results.json)','']
(p/'REPORT.md').write_text('\n'.join(lines));print(p/'REPORT.md')
