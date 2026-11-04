"""Summarize frozen selection results; no model calls or changes to annotations."""
import argparse,json
from pathlib import Path

def main():
 ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);a=ap.parse_args();p=a.run
 results=json.loads((p/'results.json').read_text());summary=json.loads((p/'summary.json').read_text())
 gold={r['id']:r for r in json.loads((p/'gold.json').read_text())}
 questions=json.loads((p/'questions.json').read_text())
 fmt=lambda x:'—' if x is None else f'{x:.1%}'
 lines=['# 인공 기록 60개: RAG와 전체 기록 LLM 선별 비교','','## 실험 범위','','직접 작성한 한 내담자의 60개 후보 기록, 10개 질문, 질문별 2회 반복이다. 기존 Q3select 선별 프롬프트와 Q3ragtool의 임베딩·검색 함수를 사용했다. RAG는 검색 여부 선택을 제외하고 검색어를 항상 한 번 생성했다. 실제 후속 발화나 상담 결과는 평가하지 않았다. 자세한 설정은 [사전 계획](PROTOCOL.md), [원본 기록](RECORDS.md), [질문](QUESTIONS.md), [사전 정답](GOLD.md)을 참고한다.','','## 전체 결과','','| 항목 | RAG | 전체 기록 LLM 선별 |','|---|---:|---:|']
 fields=[('정상 실행',lambda s:f"{s['completed']}/{s['attempts']}"),('정답 있는 질문: 모든 핵심 원본 확보',lambda s:f"{s['all_core_found']}/{s['answerable_trials']}"),('정답 있는 질문: 핵심 원본 하나 이상 확보',lambda s:f"{s['any_core_found']}/{s['answerable_trials']}"),('핵심 원본 평균 재현율',lambda s:fmt(s['mean_core_recall'])),('선택 기록 중 관련 기록 비율 평균',lambda s:fmt(s['mean_relevant_precision'])),('기록에 없는 사건: 빈 선택',lambda s:f"{s['correct_abstentions']}/{s['missing_event_trials']}"),('평균 선별 지연시간',lambda s:f"{s['mean_latency_ms']/1000:.2f}초"),('평균 생성 모델 입력 토큰',lambda s:f"{s['mean_input_tokens']:.0f}"),('평균 생성 모델 출력 토큰',lambda s:f"{s['mean_output_tokens']:.0f}")]
 for name,fn in fields:lines.append(f"| {name} | {fn(summary['rag'])} | {fn(summary['llm'])} |")
 lines+=['','재현율·관련성은 정답 있는 8개 질문 × 2회에서 산출했다. 빈 선택 4회는 별도 지표다. 평균 관련성은 각 호출의 선택 중 core 또는 supporting의 비율을 평균한 값이며, 후속 응답의 정확도가 아니다. RAG는 Top-3를 항상 채우고 LLM은 0~3개를 선택할 수 있어 선택 개수 정책도 정밀도 차이에 영향을 준다.','','RAG 지연시간에는 검색어 LLM 호출과 실제 임베딩 검색이 모두 포함된다. 토큰은 생성 모델만 집계하며 임베딩 및 후속 발화 비용은 제외한다. RAG는 첫 문서 임베딩 이후 캐시를 사용한다. 단일 서버·소수 반복의 관측값이며 동시 부하·프롬프트 캐시를 통제한 성능 벤치마크가 아니다.','','## 질문별 결과','','ID 순서는 선택 목록이다. LLM 출력 순서를 관련성 순위로 해석하지 않는다. RAG는 점수 내림차순이다.','','| 질문 ID | 필수 원본 | RAG 1회 / 2회 | LLM 1회 / 2회 |','|---|---|---|---|']
 for q in questions:
  def selected(method):
   rs=[r for r in results if r['case']==q['id'] and r['method']==method]
   return ' / '.join(str(r.get('selected','ERROR')) for r in rs)
  lines.append(f"| {q['id']} | {gold[q['id']]['core']} | {selected('rag')} | {selected('llm')} |")
 lines+=['','## 개별 선택 점검','']
 records={r['turn']:r for r in json.loads((p/'records.json').read_text())['records']}
 for q in questions:
  g=gold[q['id']];lines += [f"### {q['id']}",'',q['question'],'',f"사전 기준: {g['rationale']}",'']
  for r in [r for r in results if r['case']==q['id']]:
   if r['status']!='ok':lines += [f"- {r['method']} 반복 {r['repeat']}: ERROR {r['error']}"];continue
   selected=set(r['selected']);missing=set(g['core'])-selected;extra=selected-set(g['core'])-set(g['supporting'])
   lines += [f"- {r['method']} 반복 {r['repeat']}: 선택 {r['selected']}; 필수 누락 {sorted(missing)}; 사전 기준 밖 선택 {sorted(extra)}."]
   if r['method']=='rag':lines += [f"  - 검색어: {r['query']}"]
   for n in sorted(extra):lines += [f"  - 턴 {n}: {records[n]['inner']['scene']} / {records[n]['utterance']}"]
  lines.append('')
 lines+=['## 해석 한계','','- 필수 원본과 보조 회고를 구별한 연구 목적별 기준이다. 회고만으로도 답할 수 있는 질문에서는 일반 검색 품질과 원본 확보 성능이 다를 수 있다.','- 사전 정답은 독립 평가자가 검증하지 않았다. 질문과 기록을 같은 분석자가 만들었다.','- 두 번 호출은 두 개의 독립 사례가 아니다. 이 결과로 유의성·보편적 우열을 주장할 수 없다.','- 사건·시점을 명시한 단서가 많고 60개 기록은 모델 전체 문맥에 들어간다. 더 긴 기록·모호한 질문에서 결과가 달라질 수 있다.','- 검색된 기록을 잘 활용하는지, 응답이 왜곡되는지, 상담 개입 효과가 개선되는지는 검증하지 않았다.','']
 (p/'REPORT.md').write_text('\n'.join(lines))
 print(p/'REPORT.md')
if __name__=='__main__':main()
