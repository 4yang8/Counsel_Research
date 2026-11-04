"""Summarize the two completed length conditions and their matched input audit."""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--short',type=Path,required=True);ap.add_argument('--long',type=Path,required=True);a=ap.parse_args()
 paths={'short':a.short,'long':a.long};runs={}
 for c,p in paths.items():
  runs[c]={'summary':json.loads((p/'summary.json').read_text()),'results':json.loads((p/'results.json').read_text())}
  cfg=json.loads((p/'config.json').read_text())
  for name,expected in cfg['hashes'].items():assert hashlib.sha256((p/name).read_bytes()).hexdigest()==expected
 short=json.loads((a.short/'records.json').read_text());long=json.loads((a.long/'records.json').read_text())
 assert short['public_dialogue']==long['public_dialogue'] and short['profile']==long['profile']
 assert (a.short/'gold.json').read_bytes()==(a.long/'gold.json').read_bytes()
 gold={r['id']:r for r in json.loads((a.long/'gold.json').read_text())}
 qs=json.loads((a.long/'questions.json').read_text())
 lines=['# 30턴: 짧은 기록과 긴 기록의 RAG·LLM 선별 비교','','## 범위','','동일한 30개 과거 사건과 공개 대화를 사용하고 비공개 기록의 표현을 바꿨다. 질문10개 × 반복2 × 방법2 × 길이2 = 80개 선별 호출이다. 모든 조건에서 공개 대화와 현재 질문은 동일하며, 정답 기준은 실행 전에 고정하고 모델 입력에서 제외했다. 실제 대화나 비공개 기록을 모델로 생성하지 않았다.','','짧은 비공개 기록은 평균137자, 긴 기록은 평균1114자(JSON 키 포함)다. 긴 기록은 해석·감정·검토 과정·불확실성·의도·표현 계획을 포함하며 원래 핵심 이유와 보류 내용을 그대로 보존했다. 순수 길이만 바뀐 것이 아니라 부연 내용과 스키마도 달라졌다.','','## 결과','','| 기록 | 방법 | 정상 실행 | 모든 필수 원본 확보 | 핵심 재현율 | 관련 기록 비율 | 없는 사건 빈 선택 | 평균 시간 | 입력 토큰 | 출력 토큰 |','|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
 for c in ['short','long']:
  for method in ['rag','llm']:
   s=runs[c]['summary'][method]
   lines.append(f"| {c} | {method} | {s['completed']}/{s['attempts']} | {s['all_core_found']}/{s['answerable_trials']} | {s['mean_core_recall']:.1%} | {s['mean_relevant_precision']:.1%} | {s['correct_abstentions']}/{s['missing_event_trials']} | {s['mean_latency_ms']/1000:.2f}초 | {s['mean_input_tokens']:.1f} | {s['mean_output_tokens']:.1f} |")
 lines += ['','모든 필수 원본 확보·핵심 재현율·관련 기록 비율은 정답이 존재하는 8개 질문×2회에서 계산했다. 없는 사건2개×2회는 별도 집계한다. 관련성은 사전에 정한 core/supporting 기준이며 선택된 원문을 실제 발화에 사용하는 정확도와는 다르다.','','RAG 시간은 검색어 생성과 임베딩/검색을 포함한다. 입력·출력 토큰은 생성 모델 사용량이고 임베딩 GPU 비용과 후속 내담자 발화는 제외한다. short를 먼저 실행한 순차 실험이라 캐시·서버 부하 영향을 통제하지 못했다. 긴 문맥을 읽는 실제 시간이 항상 토큰 수에 비례한다고 가정하지 않는다.','','## 문항별 선택','','| 질문 | 필수 원본 | 짧은 RAG (1/2회) | 긴 RAG (1/2회) | 짧은 LLM (1/2회) | 긴 LLM (1/2회) |','|---|---|---|---|---|---|']
 for q in qs:
  def selections(c,m):return ' / '.join(str(r.get('selected','ERROR')) for r in runs[c]['results'] if r['case']==q['id'] and r['method']==m)
  lines.append(f"| {q['id']} | {gold[q['id']]['core']} | {selections('short','rag')} | {selections('long','rag')} | {selections('short','llm')} | {selections('long','llm')} |")
 lines += ['','## 검색어 일치 여부','']
 def index(c):return {(r['case'],r['repeat']):r for r in runs[c]['results'] if r['method']=='rag' and r['status']=='ok'}
 si=index('short');li=index('long');shared=set(si)&set(li);same=sum(si[k]['query']==li[k]['query'] for k in shared)
 lines += [f'같은 공개 입력과 seed로 검색어를 각각 생성했다. 문자열이 완전히 같은 검색어는 {same}/{len(shared)}건이다. 서로 다른 검색어가 나온 경우 두 길이 조건의 차이를 기록 길이만의 영향으로 단정할 수 없다.','']
 for key in sorted(shared):
  if si[key]['query']!=li[key]['query']:
   lines += [f'- {key[0]} 반복{key[1]}: short `{si[key]["query"]}` / long `{li[key]["query"]}`']
 lines += ['','## 필수 원본 누락의 원문 점검','']
 records={r['turn']:r for r in long['records']}
 for c in ['short','long']:
  for r in runs[c]['results']:
   if r['status']!='ok':lines += [f'- {c}/{r["method"]}/{r["case"]}/반복{r["repeat"]}: 오류 {r["error"]}'];continue
   missing=set(gold[r['case']]['core'])-set(r['selected'])
   if missing:
    lines += [f"- {c}/{r['method']}/{r['case']}/반복{r['repeat']}: 선택{r['selected']}, 누락{sorted(missing)}."]
    if r['method']=='rag':lines += [f"  - 검색어: {r['query']}"]
    for n in sorted(missing):lines += [f"  - 누락 턴{n}: {records[n]['inner']['scene']} / {records[n]['utterance']}"]
 lines += ['','## 범위와 한계','','- 30턴의 통제 비교이므로 이전 60턴 실험과 점수를 바로 이어붙이지 않는다. 후보 감소와 공개 대화 축약이 함께 일어났다.','- long은 짧은 기록에 반복 문장만 붙인 조건이 아니다. 핵심 의미는 보존했지만 상세한 해석과 표현 계획이 추가되고 스키마도 변했다.','- 정답은 같은 분석자가 작성한 인공 사례 기준으로 독립 검증되지 않았다. 두 반복은 두 독립 내담자 사례가 아니다.','- 전체 기록이 32768 문맥 한도에 들어가는 상황만 평가했다. 입력은 자동 절삭하지 않았다.','- RAG는 유사도 하한 없이3개를 채우고 LLM은0~3개를 선택할 수 있다. 빈 결과와 관련성 차이는 반환 정책도 반영한다.','- 현재 질문의 비교 대상이 검색어 작성 중 누락될 수 있다. 검색어 생성 단계와 임베딩 검색 단계를 구별해야 한다.','- 선택 성능은 최종 상담 발화의 정확성·정보 보호·개입 반응 타당성의 증거가 아니다.','','## 산출물','',f'- [짧은 조건 원본 결과]({a.short.resolve().relative_to(ROOT)}/results.json)',f'- [긴 조건 원본 결과]({a.long.resolve().relative_to(ROOT)}/results.json)','- [긴 기록 Markdown](RECORDS_LONG.md)','- [짧은 기록 Markdown](RECORDS_SHORT.md)','- [사전 계획](PROTOCOL.md)','- [문맥 한도 사전 확인](token_preflight.json)','']
 (ROOT/'REPORT.md').write_text('\n'.join(lines));print(ROOT/'REPORT.md')
if __name__=='__main__':main()
