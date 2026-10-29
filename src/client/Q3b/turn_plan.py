"""Turn-local work state. Atomic patches; no inference about semantic completion."""
import copy
import re

MAX_UPDATES = 24
TOOL = {'type':'function','function':{
    'name':'update_turn_plan',
    'description': '앞선 판단·조회 결과에 따라 다음 작업이 달라지는 미해결 문제가 있을 때 이번 턴의 작업 계획을 기록합니다. 현재 정보로 반응을 정할 수 있으면 stage_client_update로 바로 진행하고, 단일 자료 확인은 retrieve_client_memory로 직접 할 수 있습니다. 계획에는 아직 해결하지 못한 질문만 넣으세요. 최초 add로 goal과 작업·완료 기준을 등록하고 start로 시작한 뒤 complete로 결정·남은 불확실성과 핵심 근거를 기록합니다. 새 근거로 작업이 달라지면 revise/add/skip을 사용합니다. 다음 단계가 정해졌다면 complete와 다음 start를 함께 요청하세요. 같은 요청에서 각 ID는 한 번만 수정합니다. 성공 결과는 accepted·revision·steps(단계별 상태)·ready_for_judgment·remaining_plan_updates를 반환하며, 내용과 근거는 성공한 호출 인자에서 참고합니다. 실패한 요청은 적용되지 않습니다. 시작한 계획은 필요한 단계를 완료·생략한 후 stage_client_update로 종합합니다. 이 도구는 기록을 저장하며 판단의 타당성을 대신 평가하지 않습니다.',
    'parameters': {'type':'object','properties':{
        'goal':{'type':'string','description':'이번 발화가 충족할 구체적인 생성 목표. 현재 상황·마음과 표현 범위를 고려한 목표이며 전체 상담의 신뢰 상승 목표나 내담자 본인의 바람(turn_goal)과 구분합니다. 최초에는 필수로 작성하고 이후 빈 문자열이면 기존 목표를 유지합니다.'},
        'reason':{'type':'string','description':'처음에는 이 계획이 필요한 이유, 이후에는 결과 기록·순서 선택·계획 변경의 이유를 짧게 적습니다.'},
        'operations':{'type':'array','minItems':1,'maxItems':8,'items':{'type':'object','properties':{
            'action':{'type':'string','enum':['add','revise','start','complete','skip'],'description':'add 새 대기 단계 / revise 단계 내용 변경 및 대기로 재개 / start 대기 단계 시작 / complete 진행 단계 결과 기록 / skip 불필요해진 대기·진행 단계 생략'},
            'step_id':{'type':'string','description':'계획 안에서 유지할 단계 ID. 영문자로 시작하는 영문·숫자·밑줄, 예: s1. add는 새 ID, 나머지는 존재하는 ID.'},
            'task':{'type':'string','description':'add/revise에 필요한 구체적 작업. 예: 직전 내담자 발화의 개방성과 현재 경계가 함께 가능한 이유를 확인한다.'},
            'completion_criterion':{'type':'string','description':'add/revise에 필요한 완료 기준. 예: 현재 경계의 대상과 이미 공개한 범위를 근거와 함께 설명할 수 있다. 자료 부족이 확인된 경우에는 무엇을 유보해야 하는지 정할 수 있다.'},
            'result':{'type':'string','description':'complete에서는 이 단계에서 결정한 내용과 다음 판단에 필요한 미해결 사항을 간결하게 기록합니다. skip에서는 더 필요하지 않은 이유를 기록합니다. start/add/revise에는 넣지 않습니다.'},
            'sources':{'type':'array','minItems':1,'items':{'type':'string'},'description':'complete에서 판단에 실제 사용한 핵심 근거만 적습니다. 각 항목은 입력 영역·노트 ID·조회 source_id 등 출처와 판단을 뒷받침하는 내용을 짧게 연결합니다.'}},
            'required':['action','step_id'],'additionalProperties':False}}},
        'required':['goal','reason','operations'],'additionalProperties':False}}}


# One source of truth for the published per-action schema and runtime field rules.
OP_FIELDS = {
    'add': ('action','step_id','task','completion_criterion'),
    'revise': ('action','step_id','task','completion_criterion'),
    'start': ('action','step_id'),
    'complete': ('action','step_id','result','sources'),
    'skip': ('action','step_id','result'),
}
_operation_properties = TOOL['function']['parameters']['properties']['operations']['items']['properties']
_operation_properties['step_id']['pattern'] = r'^[a-zA-Z][a-zA-Z0-9_]{0,31}$'
_variants = []
for _action, _fields in OP_FIELDS.items():
    _properties = {key:copy.deepcopy(_operation_properties[key]) for key in _fields}
    _properties['action']['enum'] = [_action]
    _variants.append({'type':'object','properties':_properties,'required':list(_fields),'additionalProperties':False})
TOOL['function']['parameters']['properties']['operations']['items'] = {'oneOf':_variants}
TOOL['function']['description'] += ' 각 operation에는 해당 action의 인자만 넣습니다: add/revise는 action·step_id·task·completion_criterion, start는 action·step_id, complete는 action·step_id·result·sources, skip은 action·step_id·result입니다.'


def nonempty(value):
    return isinstance(value,str) and bool(value.strip())


def ready(plan):
    steps=plan.get('steps',[])
    return bool(steps) and any(s['status']=='completed' for s in steps) and all(s['status'] in ('completed','skipped') for s in steps)


def receipt(plan):
    """Model-facing acknowledgement; full snapshots stay in events and records."""
    return {'accepted':True, 'revision':plan['revision'],
            'steps':{s['id']:s['status'] for s in plan['steps']},
            'ready_for_judgment':ready(plan),
            'remaining_plan_updates':MAX_UPDATES-plan['revision']}


def apply(previous, args):
    if not isinstance(args,dict) or set(args)!={'goal','reason','operations'} or not isinstance(args['goal'],str) or not nonempty(args['reason']):
        raise ValueError('goal(유지는 빈 문자열), reason, operations를 확인하세요.')
    if not previous and not nonempty(args['goal']):raise ValueError('처음에는 이번 턴의 생성 목표 goal을 작성하세요.')
    ops=args['operations']
    if not isinstance(ops,list) or not 1<=len(ops)<=8:raise ValueError('operations는 1~8개 변경으로 작성하세요.')
    plan=copy.deepcopy(previous) if previous else {'goal':args['goal'].strip(),'steps':[],'revision':0}
    if plan['revision']>=MAX_UPDATES:raise ValueError('이번 턴의 계획 갱신 한도에 도달했습니다.')
    if args['goal'].strip() and args['goal'].strip()!=plan['goal'] and not any(o.get('action') in ('add','revise') for o in ops if isinstance(o,dict)):
        raise ValueError('목표 변경에는 add 또는 revise로 변경된 목표를 검토할 단계가 필요합니다.')
    if args['goal'].strip():plan['goal']=args['goal'].strip()
    seen=set()
    for op in ops:
        if not isinstance(op,dict):raise ValueError('단계 변경은 객체로 작성하세요.')
        action=op.get('action');sid=op.get('step_id')
        if not isinstance(sid,str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,31}',sid) or sid in seen:raise ValueError('step_id는 유효한 ID이며 요청 안에서 중복되지 않아야 합니다.')
        seen.add(sid)
        if not isinstance(action,str) or action not in OP_FIELDS:raise ValueError('지원하지 않는 계획 변경 action입니다.')
        allowed=set(OP_FIELDS[action])
        if set(op)!=allowed:raise ValueError('단계 action에 필요한 인자만 작성하세요: '+str(sorted(allowed)))
        step=next((s for s in plan['steps'] if s['id']==sid),None)
        if not previous and action!='add':raise ValueError('최초 호출은 대기 단계 add로 계획을 세우세요. 수행 결과는 이후 기록합니다.')
        if action=='add':
            if step is not None or len(plan['steps'])>=8:raise ValueError('새 ID로 추가하세요. 계획은 최대 8단계입니다.')
            step={'id':sid};plan['steps'].append(step)
        elif step is None:raise ValueError('존재하는 단계 ID를 지정하세요.')
        if action in ('add','revise'):
            if not all(nonempty(op[k]) for k in ('task','completion_criterion')):raise ValueError('작업 내용과 완료 기준을 구체적으로 작성하세요.')
            step.update(task=op['task'].strip(),completion_criterion=op['completion_criterion'].strip(),status='pending',result='',sources=[])
        elif action=='start':
            if step['status']!='pending':raise ValueError('대기 단계만 start로 시작할 수 있습니다.')
            step['status']='in_progress'
        elif action=='complete':
            if step['status']!='in_progress':raise ValueError('이전 호출에서 시작한 단계를 수행한 후 complete하세요.')
            if not nonempty(op['result']) or not isinstance(op['sources'],list) or not op['sources'] or any(not nonempty(s) for s in op['sources']):raise ValueError('완료에는 실제 결과와 확인한 근거 출처가 필요합니다.')
            step.update(status='completed',result=op['result'].strip(),sources=copy.deepcopy(op['sources']))
        elif action=='skip':
            if step['status'] not in ('pending','in_progress') or not nonempty(op['result']):raise ValueError('미완료 단계를 생략하는 이유를 result에 적으세요.')
            step.update(status='skipped',result=op['result'].strip(),sources=[])
        else:raise ValueError('지원하지 않는 계획 변경 action입니다.')
    if sum(s['status']=='in_progress' for s in plan['steps'])>1:raise ValueError('한 번에 한 단계만 진행하세요. 현재 단계 완료·생략 후 다음 단계를 시작하세요.')
    if not any(s['status']!='skipped' for s in plan['steps']):raise ValueError('모든 단계를 생략할 수 없습니다. 필요한 판단 단계를 남기세요.')
    plan['revision']+=1
    plan['last_change_reason']=args['reason'].strip()
    return plan


def render(plan):
    if not plan:return '(내용 없음)'
    lines=['## 이번 턴 작업 목표',plan['goal'],'','## 수행 단계']
    for s in plan['steps']:
        status={'pending':'대기','in_progress':'진행 중','completed':'완료','skipped':'생략'}[s['status']]
        lines += [f"- [{'x' if s['status']=='completed' else ' '}] {s['id']} · {status} · {s['task']}", '  완료 기준: '+s['completion_criterion']]
        if s['result']:lines.append('  결과: '+s['result'])
        if s['sources']:lines.append('  근거: '+' / '.join(s['sources']))
    return '\n'.join(lines)
