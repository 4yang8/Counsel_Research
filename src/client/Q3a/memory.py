"""Session-local exact-record lookup. No generated summaries or external paths."""
import copy
import json
import re

MAX_LOOKUPS=4
MAX_RESULTS=5
MAX_CHARS=14000
SCHEMA={'type':'object','properties':{
    'source':{'type':'string','enum':['current','changes','episodes','trajectory'],'description':'current: 현재 노트 원문, changes: 노트 항목 변경 전후·이유, episodes: 완료된 턴의 실제 발화와 당시 판단 원문, trajectory: 당시 사전 궤적·잠정 계획·진행 전후와 근거'},
    'query':{'type':'string','description':'원문에 포함될 단어를 공백으로 구분. 부분 문자열 OR 검색이며 의미 검색은 아닙니다. 빈 문자열이면 필터에 해당하는 최신 기록을 봅니다.'},
    'item_ids':{'type':'array','items':{'type':'string'},'description':'확인할 노트 항목 ID. 빈 배열이면 ID로 제한하지 않습니다.'},
    'turns':{'type':'array','items':{'type':'integer','minimum':1},'description':'변경 이력·원본 맥락의 완료된 턴 번호. 빈 배열이면 모든 완료 턴.'},
    'offset':{'type':'integer','minimum':0,'description':'같은 조건으로 다음 결과를 볼 때 next_offset을 지정합니다.'},
    'limit':{'type':'integer','minimum':1,'maximum':MAX_RESULTS},
    'reason':{'type':'string','description':'현재 어떤 판단에 어떤 근거가 부족하며, 이 조회로 무엇을 확인해 해석·표현 선택을 정하려는지 설명합니다.'}
},'required':['source','query','item_ids','turns','offset','limit','reason'],'additionalProperties':False}
TOOL={'type':'function','function':{'name':'retrieve_client_memory',
    'description':'이번 반응의 중요한 해석·공개 범위 또는 궤적을 얼마나 진행할지 결정하는 데 필요한 조건과 근거가 부족하고 같은 세션 기록에서 확인할 수 있을 때 사용합니다. 현재 노트의 빠진 항목(current), 변경 이유(changes), 당시 발화·판단(episodes), 이전 궤적 계획과 실제 진행 판단(trajectory)을 원문으로 조회합니다. 어떤 상황에서 신뢰하거나 망설였는지 확인한 결과를 이번 반응 계획에 연결하세요. 결과의 출처와 턴을 읽고 필요하면 다른 종류를 이어 조회하세요. 과거 판단은 당시 주관적 해석이며 현재 사실의 확정 근거와 구분합니다. 조회는 발화 확정 전 최대 4회, 한 번에 최대 5개입니다. 결과가 없으면 확인할 자료가 없다는 뜻입니다.',
    'parameters':SCHEMA}}


def validate(args):
    if not isinstance(args,dict) or set(args)!=set(SCHEMA['required']):raise ValueError('조회 인자 전체를 확인하세요.')
    if not isinstance(args['source'],str) or args['source'] not in ('current','changes','episodes','trajectory'):raise ValueError('알 수 없는 조회 대상입니다.')
    if not isinstance(args['query'],str) or not isinstance(args['reason'],str) or not args['reason'].strip():raise ValueError('검색어와 조회 이유를 확인하세요.')
    for key,typ in [('item_ids',str),('turns',int)]:
        if not isinstance(args[key],list) or any(type(v) is not typ for v in args[key]):raise ValueError(key+' 배열을 확인하세요.')
    if any(t<1 for t in args['turns']):raise ValueError('턴 번호는 1 이상입니다.')
    if type(args['offset']) is not int or args['offset']<0 or type(args['limit']) is not int or not 1<=args['limit']<=MAX_RESULTS:raise ValueError('조회 범위를 확인하세요.')
    if len(args['query'])>500 or len(args['item_ids'])>50 or len(args['turns'])>50:raise ValueError('검색 조건이 너무 큽니다.')
    if args['source']=='current' and args['turns']:raise ValueError('current는 현재 노트입니다. 과거 턴은 changes 또는 episodes로 조회하세요.')
    if args['source'] in ('episodes','trajectory') and args['item_ids']:raise ValueError('episodes와 trajectory는 turns로 조회하세요. 항목 변경의 턴 번호는 changes에서 확인할 수 있습니다.')


def lookup(note,args):
    validate(args)
    source=args['source'];rows=[]
    if source=='current':
        for section,items in note['items'].items():
            for item in items:
                rows.append({'source_id':'current:'+item['id'],'source':'current','item_id':item['id'],'content':item['content'],
                             'meaning':'이번 상담사 발화 전에 저장된 내담자 노트'})
    else:
        for record in note['records']:
            turn=record['turn']
            if source=='changes':
                for index,op in enumerate(record.get('note_operations',[])):
                    rows.append({'source_id':f'changes:{turn}:{index}','source':'changes','turn':turn,'turn_id':record['turn_id'],
                                 **copy.deepcopy(op),'after':op['content'] if op['action']!='delete' else '',
                                 'meaning':'당시 노트 변경과 모델이 기록한 변경 근거'})
            elif source=='trajectory':
                if 'trajectory_progress' not in record:continue
                rows.append({'source_id':f'trajectory:{turn}','source':'trajectory','turn':turn,'turn_id':record['turn_id'],
                    **{k:copy.deepcopy(record[k]) for k in ('trajectory_source','trajectory_plan','trajectory_before','trajectory_progress','counselor_utterance','utterance') if k in record},
                    'meaning':'해당 턴의 사전 궤적과 잠정 계획, 실제 내면 판단·표현을 비교해 기록한 진행 전후의 근거'})
            else:
                rows.append({'source_id':f'episode:{turn}','source':'episodes','turn':turn,'turn_id':record['turn_id'],
                    **{k:copy.deepcopy(record[k]) for k in ('counselor_utterance','utterance','appraisal','response_plan','turn_goal','trajectory_plan') if k in record},
                    'meaning':'완료된 과거 턴의 공개 발화와 당시 주관적 판단 원문'})
    tokens=re.findall(r'\S+',args['query'].casefold())
    def score(row):
        body=json.dumps(row,ensure_ascii=False).casefold()
        return sum(token in body for token in tokens)
    rows=[r for r in rows if (not args['item_ids'] or r.get('item_id') in args['item_ids'])
          and (not args['turns'] or r.get('turn') in args['turns']) and (not tokens or score(r))]
    rows.sort(key=lambda r:(score(r),r.get('turn',0)),reverse=True)
    result=[];size=0
    for row in rows[args['offset']:args['offset']+args['limit']]:
        entry=copy.deepcopy(row);encoded=json.dumps(entry,ensure_ascii=False)
        if len(encoded)>MAX_CHARS:
            # A large episode still identifies exact source; mark excerpt explicitly.
            entry={'source_id':row['source_id'],'source':source,'turn':row.get('turn'),
                   'excerpt':encoded[:MAX_CHARS-700],'truncated':True,'meaning':'출처 원문 JSON 앞부분 발췌; 전체 기록은 저장 로그에서 확인 가능'}
        length=len(json.dumps(entry,ensure_ascii=False))
        if size+length>MAX_CHARS and result:break
        result.append(entry);size+=length
    next_offset=args['offset']+len(result)
    return {'source':source,'scope':'동일 세션의 현재 노트 및 완료된 과거 턴',
            'status':'found' if result else 'not_found','results':result,'matched_count':len(rows),
            'has_more':next_offset<len(rows),'next_offset':next_offset if next_offset<len(rows) else None,
            'available_completed_turns':[r['turn'] for r in note['records']],
            'search_method':'원문 단어 부분일치 OR + 지정 필터; 동점은 최신 턴 우선'}
