"""Deterministic router statistics. Completed client turns only; no model calls."""
import collections
import copy
import hashlib
import json
import re
from pathlib import Path

from client.Q1.runtime import split_profile
from client.Q3.profile_context import background_candidates

VERSIONS = ('Q3', 'Q3a', 'Q3b', 'Q3sk', 'Q3n', 'Q3sb', 'Q3x')
LABELS = {'core_belief':'핵심 신념','intermediate_beliefs':'중간 신념','coping_strategies':'대처 전략',
          'assumption_positive':'긍정적 가정','assumption_negative':'부정적 가정',
          'attitude':'태도','assumption':'가정','rule':'규칙','core_belief_statement':'핵심 신념',
          'appraisal':'내면 판단','turn_goal':'대화 의도','response_plan':'표현 계획'}


def ratio(a, b):
    return a / b if b else None


def text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def trait_label(identity):
    parts=identity.strip('/').split('/')
    return ' / '.join(str(int(p)+1) if p.isdigit() else LABELS.get(p,p) for p in parts)


def note_kind(content):
    first=content.split('\n',1)[0]
    if first=='요약':return '요약'
    if first=='실제 공개 발화':return first
    for field in ('appraisal','turn_goal','response_plan'):
        if first.startswith(field+'.'):return LABELS[field]
    match=re.match(r'\[(\w+) · 턴 \d+ · ([^\]]+)\]',first)
    if match:
        kind,source=match.groups()
        if kind=='exploratory_reasoning':return '탐색 reasoning 원문'
        if kind=='actual_utterance':return '실제 발화 원문'
        return next((LABELS[k]+' 원문' for k in ('appraisal','turn_goal','response_plan') if source.startswith('judgment:'+k+'.')),'기타 원문')
    return '자유 노트'


def schema_ids(record, field):
    props=record.get('router_request',{}).get('structured_outputs',{}).get('json',{}).get('properties',{})
    if field=='notes':
        spec=props.get('note_ids',{})
        if spec.get('maxItems')==0:return []
        return spec.get('items',{}).get('enum')
    item=props.get('selections',{}).get('items',{}).get('properties',{})
    spec=item.get('trait_id',{}) if field=='traits' else item.get('background_ids',{}).get('items',{})
    return spec.get('enum')


def flatten(items):
    return [copy.deepcopy(row) for rows in items.values() for row in rows]


def before_items(record, previous, parser):
    if previous and previous.get('notes_after')==record.get('notes_before') and 'note_items_after' in previous:
        return flatten(previous['note_items_after'])
    return flatten(parser(record.get('notes_before','')))


def build(session, records, parser, start=1, end=None, key='', sources=None):
    """records are explicit (record, path) pairs; reject copied or uncommitted duplicates."""
    profile=session.get('settings',{}).get('scenario',{}).get('profile',{})
    enabled=session.get('settings',{}).get('input_blocks',{}).get('profile',True)
    traits=split_profile(profile)[1] if enabled else []
    backgrounds=background_candidates(profile) if enabled else []
    trait_map={r['trait_id']:{'id':r['trait_id'],'label':trait_label(r['trait_id']),'content':r['content']} for r in traits}
    bg_map={r['background_id']:{'id':r['background_id'],'label':r['kind']+' · '+r['background_id'],'content':r['content']} for r in backgrounds}
    steps=[s for s in session.get('steps',[]) if s.get('actor')=='client' and not s.get('manual')]
    committed={s['turn_id']:s for s in steps};order={s['turn_id']:i+1 for i,s in enumerate(steps)}
    valid={};warnings=[]
    for record,path in records:
        tid=record.get('turn_id')
        if tid not in committed:continue
        if tid in valid:
            if valid[tid][0]!=record:warnings.append('같은 완료 턴의 서로 다른 기록: '+tid)
            continue
        valid[tid]=(record,path)
    missing=[s['turn_id'] for s in steps if s['turn_id'] not in valid]
    if missing:warnings.append('완료된 내담자 턴 중 기록을 찾지 못한 턴 '+str(len(missing))+'개. 누락을 0회 선택으로 집계하지 않습니다.')
    turns=[];previous=None;prev_selected=set();prev_traits=set();seen_selected=set();born={};last_number=None;prev_note_known=False;prev_trait_known=False
    for tid in sorted(valid,key=lambda t:order[t]):
        r,path=valid[tid];number=order[tid];step=committed[tid]
        contiguous=last_number is not None and number==last_number+1
        if not contiguous:prev_selected=set();prev_traits=set()
        selected_traits=r.get('selected_traits');trait_recorded=isinstance(selected_traits,list)
        selected_traits=selected_traits if trait_recorded else []
        for row in selected_traits:
            identity=row['trait_id']
            trait_map.setdefault(identity,{'id':identity,'label':trait_label(identity),'content':row.get('content','')})
            for bg in row.get('backgrounds',[]):bg_map.setdefault(bg['background_id'],{'id':bg['background_id'],'label':bg.get('kind','배경')+' · '+bg['background_id'],'content':bg.get('content','')})
        ts=set(row['trait_id'] for row in selected_traits)
        bs=sorted(set(bg for row in selected_traits for bg in row.get('background_ids',[])))
        selection=r.get('note_selection');note_recorded=isinstance(selection,dict) and isinstance(selection.get('selected_ids'),list)
        ids=list(dict.fromkeys(selection['selected_ids'])) if note_recorded else None
        available=before_items(r,previous,parser)
        actual_ids=schema_ids(r,'notes')
        if actual_ids is not None and set(actual_ids)!={n['id'] for n in available}:
            warnings.append(f'내담자 {number}턴: 노트 본문과 실제 라우터 후보 ID가 다릅니다. 실제 후보 목록을 기준으로 표시합니다.')
            known={n['id']:n for n in available}
            available=[known.get(identity,{'id':identity,'content':'(원문 미확인)','content_missing':True}) for identity in actual_ids]
        aset={n['id'] for n in available};chosen=set(ids or [])
        invalid=chosen-aset
        if invalid:warnings.append(f'내담자 {number}턴: 후보에 없는 선택 ID '+', '.join(sorted(invalid)))
        for n in available:
            n['selected']=n['id'] in chosen if note_recorded else None
            n['created_turn']=born.get(n['id'])
            n['age']=number-born[n['id']] if n['id'] in born else None
            n['kind']=note_kind(n['content'])
        selected=[n for n in available if n['selected']]
        comparable=contiguous and note_recorded and prev_note_known
        eligible=prev_selected & aset if comparable else set()
        after=flatten(r.get('note_items_after',{})) if 'note_items_after' in r else flatten(parser(r.get('notes_after','')))
        for n in after:
            if n['id'] not in born and n['id'] not in aset:born[n['id']]=number
        chars_known=not any(n.get('content_missing') for n in available)
        counts={'available':len(available),'selected':len(ids) if ids is not None else None,
                'available_chars':sum(len(n['content']) for n in available) if chars_known else None,
                'selected_chars':sum(len(n['content']) for n in selected) if chars_known and note_recorded and not invalid else None,
                'selection_rate':ratio(len(ids),len(available)) if ids is not None else None,
                'eligible_again':len(eligible) if comparable else None,
                'selected_again':len(chosen & eligible) if comparable else None,
                'no_longer_available':len(prev_selected-aset) if comparable else None,
                'first_selected':len(chosen-seen_selected) if note_recorded else None,
                'after_gap_selected':len((chosen & seen_selected)-prev_selected) if note_recorded else None,
                'after_count':len(after)}
        turns.append({'turn':number,'public_step':step.get('number'),'turn_id':tid,'record_source':str(path),
            'counselor':r.get('counselor_utterance',''),'utterance':r.get('utterance',''),
            'traits_recorded':trait_recorded,'selected_traits':selected_traits,'selected_background_ids':bs,
            'trait_available_ids':schema_ids(r,'traits') if schema_ids(r,'traits') is not None else list(trait_map),
            'background_available_ids':schema_ids(r,'backgrounds') if schema_ids(r,'backgrounds') is not None else list(bg_map),
            'trait_change':{'new':sorted(ts-prev_traits),'kept':sorted(ts&prev_traits),'dropped':sorted(prev_traits-ts)} if contiguous and trait_recorded and prev_trait_known else None,
            'notes_recorded':note_recorded,'selected_note_ids':ids,'notes':available,'stats':counts,
            'retrievals':len(r.get('memory_retrievals',[]))})
        previous=r;prev_selected=chosen;prev_traits=ts;seen_selected|=chosen;last_number=number;prev_note_known=note_recorded;prev_trait_known=trait_recorded
    limit=max((t['turn'] for t in turns),default=0) if end is None else end
    displayed=[t for t in turns if start<=t['turn']<=limit]
    index={}
    for t in displayed:
        for n in t['notes']:
            item=index.setdefault(n['id'],{'id':n['id'],'content':n['content'],'kind':n['kind'],'created_turn':n['created_turn'],'available_turns':[],'routing_available_turns':[],'selected_turns':[],'variants':[]})
            item['available_turns'].append(t['turn'])
            if t['notes_recorded']:item['routing_available_turns'].append(t['turn'])
            if n['selected']:item['selected_turns'].append(t['turn'])
            if not item['variants'] or item['variants'][-1]['content']!=n['content']:item['variants'].append({'turn':t['turn'],'content':n['content']})
            item['content']=n['content']
    for item in index.values():item['rate']=ratio(len(item['selected_turns']),len(item['routing_available_turns']))
    note_turns=[t for t in displayed if t['notes_recorded']]
    available_total=sum(t['stats']['available'] for t in note_turns)
    selected_total=sum(t['stats']['selected'] for t in note_turns)
    eligible_total=sum(t['stats']['eligible_again'] or 0 for t in note_turns)
    repeated_total=sum(t['stats']['selected_again'] or 0 for t in note_turns)
    chars=[t for t in note_turns if t['stats']['available_chars'] is not None and t['stats']['selected_chars'] is not None]
    distributions=collections.Counter();ages=collections.Counter();candidate_kinds=collections.Counter();candidate_ages=collections.Counter()
    for t in note_turns:
        for n in t['notes']:
            a=n['age']
            age='시점 미상' if a is None else '1턴 전' if a==1 else '2~3턴 전' if a<=3 else '4턴 이상 전'
            candidate_kinds[n['kind']]+=1;candidate_ages[age]+=1
            if n['selected']:distributions[n['kind']]+=1;ages[age]+=1
    for identity,item in trait_map.items():
        eligible=[t for t in displayed if t['traits_recorded'] and identity in t['trait_available_ids']]
        item['selected_turns']=[t['turn'] for t in eligible if any(s['trait_id']==identity for s in t['selected_traits'])]
        item['available_turns']=len(eligible);item['rate']=ratio(len(item['selected_turns']),len(eligible))
    for identity,item in bg_map.items():
        eligible=[t for t in displayed if t['traits_recorded'] and identity in t['background_available_ids']]
        item['selected_turns']=[t['turn'] for t in eligible if identity in t['selected_background_ids']]
        item['available_turns']=len(eligible);item['rate']=ratio(len(item['selected_turns']),len(eligible))
    settings=session.get('settings',{});scenario=settings.get('scenario',{})
    digest=lambda value:hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {'session':{'id':session['session_id'],'key':key,'version':session['client_version'],
        'profile_id':profile.get('profile_id',''),'model':session.get('connection',{}).get('model',''),
        'batch_id':settings.get('comparison',{}).get('batch_id'),'generation':settings.get('generation',{}),
        'counselor_version':session.get('counselor_version'),'scenario_hash':digest(scenario),
        'expected_client_turns':len(steps),'recorded_client_turns':len(turns),'public_steps':len(session.get('steps',[])),
        'range':[start,limit],'sources':sources or []},'turns':displayed,'traits':list(trait_map.values()),
        'backgrounds':list(bg_map.values()),'notes':list(index.values()),'warnings':warnings,
        'totals':{'turns':len(displayed),'traits_recorded':sum(t['traits_recorded'] for t in displayed),
            'notes_recorded':len(note_turns),'available_notes':available_total,'selected_notes':selected_total,
            'note_selection_rate':ratio(selected_total,available_total),'eligible_again':eligible_total,
            'selected_again':repeated_total,'reselection_rate':ratio(repeated_total,eligible_total),
            'available_chars':sum(t['stats']['available_chars'] for t in chars),
            'selected_chars':sum(t['stats']['selected_chars'] for t in chars),
            'kind_distribution':dict(distributions),'age_distribution':dict(ages),
            'kind_candidates':dict(candidate_kinds),'age_candidates':dict(candidate_ages)}}
