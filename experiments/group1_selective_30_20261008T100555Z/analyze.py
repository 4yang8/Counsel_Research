"""Deterministic artifact and handoff audit; no model judge or extra generation."""
import copy, hashlib, json, statistics, re, collections
from pathlib import Path
from urllib.request import Request,urlopen
OUT=Path(__file__).resolve().parent
BASE=OUT.parent/'group1_full_context_15_20261008T063458Z'
def dump(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def main():
    rs=json.loads((OUT/'results.json').read_text());baseline=json.loads((BASE/'results.json').read_text())
    assert len(rs)==30 and len({(r['condition'],r['case_id']) for r in rs})==30
    pairs={r['case_id']:{} for r in rs};handoffs=[]
    for r in rs:
        folder=OUT/r['condition']/('case_'+r['case_id'])
        payload=json.loads((folder/'payload.json').read_text());body=json.loads((folder/'input.json').read_text())
        original=json.loads((OUT/'baseline_inputs'/(r['case_id']+'.json')).read_text())
        assert body['scenario']['states']==original['scenario']['states']
        assert body['scenario']['dialogue_turns']==original['scenario']['dialogue_turns']
        for key in ['task','taxonomy','dialogue','selected_counselor_utterance']:
            assert body['scenario'][key]==original['scenario'][key]
        assert 'childhood_data' not in body['scenario']['profile'] and 'situations' not in body['scenario']['profile']
        assert body['generation']==original['generation']
        paired=copy.deepcopy(payload);paired['messages'][0]['content']='SYSTEM_CONDITION'
        pairs[r['case_id']][r['condition']]=paired
        log=json.loads(Path(r['log']).read_text());starts=[e for e in log.get('tool_events',[]) if e['type']=='tool_start']
        for tool in r['tools']:
            if tool['is_error']:raise AssertionError('Tool error: '+r['case_id'])
            result=tool['result'];childid=result['agent_id']
            events=[e['event'] for e in log['subagent_events'] if e['agent_id']==childid]
            child=next(e['payload'] for e in events if e['type']=='request')
            assert child['model']==payload['model'] and 'tools' not in child and len(child['messages'])==2
            source=json.loads(child['messages'][1]['content'])
            assert source['profile_id']==body['scenario']['profile']['profile_id']
            assert 'records' not in result
            record=result['record']
            assert record is None or record in source['records']
            # Result is inserted exactly as emitted; no hidden full-database merge.
            calls=log['model_calls'];resumed=calls[-1]['request']['messages']
            assert any(m['role']=='tool' and m['content']==tool['model_content'] for m in resumed)
            handoffs.append({'condition':r['condition'],'case_id':r['case_id'],'tool':tool['name'],
                'arguments':next((e['arguments'] for e in starts if e['name']==tool['name']),None),
                'selected_id':record['id'] if record else None,'status':result['status'],
                'candidate_count':len(source['records']),'returned_records':int(record is not None),'exact_original':True})
    for case,pair in pairs.items():assert pair['optional']==pair['required'],case
    stats={}
    for name,items in [('full_baseline',baseline)]+[(name,[r for r in rs if r['condition']==name]) for name in ('optional','required')]:
        stats[name]={'n':len(items),'completed':sum(r['status']=='completed' for r in items),
            'cases_with_calls':sum(bool(r['tools']) for r in items),'tool_calls':sum(len(r['tools']) for r in items),
            'returned_records':sum(t.get('result',{}).get('status')=='found' for r in items for t in r['tools']),
            'tool_errors':sum(bool(t['is_error']) for r in items for t in r['tools']),
            'main_model_calls':sum(r['model_calls'] for r in items),
            'subagent_calls':sum(len(r.get('children',[])) for r in items),
            'latency_mean_seconds':round(statistics.mean(r['latency_seconds'] for r in items),2),
            'prompt_tokens':sum(r['usage'].get('prompt_tokens',0) for r in items),
            'completion_tokens':sum(r['usage'].get('completion_tokens',0) for r in items),
            'total_tokens':sum(r['usage'].get('total_tokens',0) for r in items)}
    analysis={'stats':stats,'matched_initial_payloads':15,'source_handoffs':handoffs,
        'scope':'Only these 30 and the explicitly named previous 15; semantic reviews in report.md are qualitative artifact audits.'}
    names={'1':'이해받음','2':'선택권을 존중받음','3':'부담·압박을 느낌','4':'오해받음'}
    analysis['appraisal_counts']={c:dict(collections.Counter(names[n] for r in rs if r['condition']==c for n in set(re.findall(r'[1-4]',r['response'].split('판단')[0])))) for c in ('optional','required')}
    analysis['stage_direction_counts']={c:sum('(' in re.split('실제 발화|내담자 발화',r['response'])[-1] for r in rs if r['condition']==c) for c in ('optional','required')}
    if (OUT/'reviews.json').exists():analysis.update(json.loads((OUT/'reviews.json').read_text()))
    dump('analysis.json',analysis)
    text=['# 30개 생성 원문','', '단일 응답 생성이며 각 히스토리 마지막 내담자 참고 답변은 모델에 전달하지 않았습니다.','']
    for case in pairs:
        text+=['## '+case,'']
        for r in [r for r in rs if r['case_id']==case]:
            body=json.loads((OUT/r['condition']/('case_'+case)/'input.json').read_text())
            text+=['### '+r['condition'],'', '상태: '+str(r['states']),'', '상담사: '+body['messages'][0]['content'],'']
            for h in [h for h in handoffs if h['case_id']==case and h['condition']==r['condition']]:
                text+=['조회: '+h['tool']+' / '+str(h['selected_id']),'','```json',str(h['arguments']),'```','']
            for tool in r['tools']:
                text+=['반환 자료:','', '```json',json.dumps(tool['result'],ensure_ascii=False,indent=2),'```','']
            text+=[r['response'],'']
    (OUT/'outputs.md').write_text('\n'.join(text))
    print(json.dumps(stats,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
