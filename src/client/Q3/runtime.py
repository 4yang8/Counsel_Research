"""Q3: supplied macro trajectory, optional evidence lookup and grounded progress updates."""
import runtime.user_templates as user_templates
import copy
import difflib
import json
from pathlib import Path
import re
import threading
import uuid

import client.registry as experiment_versions
import runtime.reasoning_budget as reasoning_budget
from runtime.cancellation import Cancelled
from client.state_summary.runtime import fingerprint, write_new
from .tool_loop import run_turn, validate_messages
from client.Q1.runtime import split_profile, parse_json, APPRAISAL_SCHEMA as BASE_APPRAISAL_SCHEMA, APPRAISAL_FIELDS
from .profile_context import background_candidates, routing_schema, resolve_selection
from .states import STATES, INNER, parse_note
from . import note_items
from . import memory
from . import trajectory
from .context_view import manifest

from .settings import OPTIONS
VERSION='Q3'
HERE=Path(__file__).resolve().parent
LOCK = threading.Lock()
APPRAISAL_SCHEMA = copy.deepcopy(BASE_APPRAISAL_SCHEMA)
for field, description in {
    'interpretation': '개인적 배경과 현재 내면 상태, 대화 맥락을 바탕으로 이번 상담사의 말을 자신에게 어떤 의미로 받아들였는지 서술합니다.',
    'immediate_feeling': '이번 상담사의 말을 받아들이면서 현재 느끼는 감정을 서술합니다.',
    'relevant_evidence': '이번 해석과 감정의 판단 근거로 사용한 제공 정보의 구체적인 내용과, 그 내용이 판단에 어떻게 연결되는지 서술합니다.',
    'uncertainty': '이번 상담사의 말을 받아들이는 과정에서 내담자 자신이 아직 확신하지 못하거나 모호하게 느끼는 부분을 서술합니다. 없다면 빈 문자열을 작성합니다.',
}.items():
    APPRAISAL_SCHEMA['properties'][field]['description'] = description
TOOL = {'type':'function','function':{
    'name':'stage_client_update',
    'description':'상담사 말을 받아들인 의미·감정·근거와 무엇을 표현하거나 보류할지 기록합니다. 다음으로 commit_client_utterance에서 공개할 발화를 확정합니다.',
    'parameters':{'type':'object','properties':{
        'appraisal':copy.deepcopy(APPRAISAL_SCHEMA),
        'response_plan':{'type':'object','properties':{'action':{'type':'string','description':'이번에 무엇을 말하거나 보류하고 어떻게 반응할지'},'reason':{'type':'string','description':'현재 상태·해석·대화 맥락에 근거한 선택 이유'}},'required':['action','reason'],'additionalProperties':False}},
        'required':['appraisal','response_plan'],'additionalProperties':False}}}


def specification():
    return {'id':VERSION,'label':OPTIONS['label']+' · '+VERSION,'condition':OPTIONS['condition'],'view':'client_states',
            'description':'사전 신뢰 형성 궤적을 참고해 이번 반응을 계획하고, 필요 시 기록을 조회합니다. 발화 후 내면 노트와 실제 궤적 진행을 갱신합니다.',
            'system_prompt':(HERE/'system.md').read_text(encoding='utf-8'),'summary_system_prompt':'',
            'required_tools':[tool['function']['name'] for tool in TOOLS],'default_reasoning_budgets':{'main':None,'subagent':None},
            'subagent_prompts':[{'kind':'trait_router','label':'특성·배경·노트 라우터' if OPTIONS['select_notes'] else '특성·배경 라우터'}],
            'tools':copy.deepcopy(TOOLS)}


def directory(config,session):
    if not isinstance(session,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',session):
        raise ValueError('올바르지 않은 내담자 실험 session_id입니다.')
    return Path(config['_storage_dir'])/VERSION/session


def load(config,session):
    folder=directory(config,session) if session else None
    path=folder/'state_notes.md' if folder else None
    records=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((folder/'appraisals').glob('*.json'))] if folder else []
    content=(path if path and path.exists() else HERE/'state_notes_template.md').read_text(encoding='utf-8')
    items=note_items.read_items(content)
    return {'content':content,**parse_note(content),'items':items,'prompt_content':note_items.render_items(items),
            'records':records,'path':str(path) if path else None,
            'trajectory':(HERE/'trajectory.md').read_text(encoding='utf-8'),
            'trajectory_progress':copy.deepcopy(records[-1].get('trajectory_progress',{})) if records else {},
            'trajectory_progress_text':trajectory.render(records[-1].get('trajectory_progress',{})) if records else '(내용 없음)'}


def context(config, body, runtime, preview=False):
    scenario = runtime.validate_scenario(body['scenario']) if 'scenario' in body else runtime.load_scenario(config)
    messages = validate_messages(body.get('messages'), allow_empty=preview)
    if any(row.get('tool_calls') or row['role'] not in ('user', 'assistant') for row in messages):
        raise ValueError('내담자 실험 이어가기에는 공개 발화만 사용하세요.')
    note = load(config, body.get('session_id'))
    if note['records']:
        if note['records'][-1]['scenario_hash'] != fingerprint(scenario):
            raise ValueError('내담자 실험 초기 자료가 달라졌습니다. 새 대화를 시작하세요.')
        expected = [row for record in note['records'] for row in (
            {'role': 'user', 'content': record['counselor_utterance']}, {'role': 'assistant', 'content': record['utterance']})]
        if not config.get('_paired_public_dialogue'):
            if len(messages) == 1:
                messages = expected + messages
            elif messages[:-1] != expected:
                raise ValueError('내담자 실험 공개 대화가 저장된 기록과 다릅니다.')
    basic, traits = split_profile(scenario['profile'])
    backgrounds = background_candidates(scenario['profile'])
    blocks = experiment_versions.input_blocks(config, body)
    if not blocks['profile']:
        basic, traits, backgrounds = {}, [], []
    if config.get('_paired_public_dialogue') is not None:
        public = config['_paired_public_dialogue'][:-1]
        if not blocks['dialogue']:
            public = public[config.get('_paired_initial_count', 0):]
        dialogue = '\n\n'.join(('상담사' if row['role'] == 'counselor' else '내담자') + ': ' + row['content'] for row in public)
    else:
        dialogue = (runtime.dialogue_script(scenario) + '\n\n' if blocks['dialogue'] else '')
        dialogue += '\n\n'.join(('상담사' if row['role'] == 'user' else '내담자') + ': ' + row['content'] for row in messages[:-1])
    return {'scenario': scenario, 'blocks': blocks, 'basic': basic, 'traits': traits, 'backgrounds': backgrounds, 'note': note,
            'dialogue': dialogue, 'current': messages[-1]['content'], 'messages': messages}



def payload_for(config,body,ctx,selected,runtime,base_build,preview=False):
    text = user_templates.render('client', VERSION, basic_info=user_templates.md(runtime.markdown_data, ctx['basic']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'],
        selected_traits=user_templates.md(runtime.markdown_data, selected), state_notes=ctx.get('visible_note',ctx['note']['prompt_content']),
        trajectory=ctx['note']['trajectory'], trajectory_progress=ctx['note']['trajectory_progress_text'])
    request={**body,'messages':[{'role':'user','content':text}]}
    request.setdefault('system_prompt',specification()['system_prompt'])
    if not ctx['blocks']['system']:request['system_prompt']=''
    payload=base_build(config,request,copy.deepcopy(TOOLS),preview)
    payload['tool_choice']='required' if OPTIONS['retrieve'] else {'type':'function','function':{'name':'stage_client_update'}}
    return payload


def build(config,body,runtime,base_build,preview=False):
    ctx=context(config,body,runtime,preview)
    if OPTIONS['select_notes']:ctx['visible_note']='# 내담자 노트 · 선택 대기\n\n라우터 실행 후 선택된 항목 원문이 입력됩니다.'
    return payload_for(config,body,ctx,[],runtime,reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets')),preview)


def validate_update(args):
    if not isinstance(args,dict) or set(args)!={'appraisal','response_plan','turn_goal','trajectory_plan'}:
        raise ValueError('appraisal와 response_plan을 기록하세요. 상태 노트는 발화 후 갱신합니다.')
    for key,fields in [('appraisal',APPRAISAL_FIELDS),('response_plan',('action','reason'))]:
        part=args[key]
        if (not isinstance(part,dict) or set(part)!=set(fields)
            or any(not isinstance(part[k],str) or (k!='uncertainty' and not part[k].strip()) for k in fields)):
            raise ValueError(key+'의 항목·본문을 확인하세요.')
    if OPTIONS['turn_goal']:
        goal=args['turn_goal']
        if not isinstance(goal,dict) or set(goal)!={'intention','grounds','uncertainty'} or any(not isinstance(goal[k],str) or (k!='uncertainty' and not goal[k].strip()) for k in goal):
            raise ValueError('turn_goal에 intention, grounds, uncertainty를 기록하세요.')
    trajectory.validate(args['trajectory_plan'],trajectory.PLAN)
    return copy.deepcopy(args)


def function_tool(name, description, schema):
    return {'type':'function','function':{'name':name,'description':description,'parameters':schema}}


def note_update_schema():
    return note_items.schema()


TOOLS = [TOOL,
    function_tool('commit_client_utterance',
        '앞서 기록한 내면 판단과 반응 방향에 맞는 공개 발화를 확정합니다. 이후 노트 갱신 중에는 발화를 바꾸지 않습니다. 이 도구만 성공한 경우 전체 턴 저장은 아직 완료되지 않습니다.',
        {'type':'object','properties':{'utterance':{'type':'string','description':'상담사에게 말할 내담자 발화 원문'}},'required':['utterance'],'additionalProperties':False}),
    function_tool('update_client_notes',
        '앞서 생성된 appraisal·turn_goal·response_plan·확정 발화에서 다음 반응에 필요한 마음과 조건을 선별하여 이전 전체 노트에 반영합니다. 주관적 내면, 의도·표현 선택, 실제 공개한 내용을 구분하고 대상·의미·시점·확실성을 보존합니다. 기록 과정에서 새로운 해석·인과관계·상대의 의도·상호작용 결과를 추가하지 않습니다. 필요한 add/update/delete만 operations에 전달하고 유지할 항목은 생략합니다. content에는 현재 마음을, reason에는 이전 항목과 이번 자료의 어떤 차이가 변경을 뒷받침하는지 적습니다. 다음으로 update_trajectory_progress에서 진행을 정리하고 모든 기록 검증 후 함께 저장합니다.',
        note_update_schema())]


if OPTIONS['turn_goal']:
    TOOLS[0]['function']['description']='이번 말을 받아들인 해석·감정(appraisal), 표현하거나 지키려는 바람·경계(turn_goal), 이를 말하는 방식(response_plan)을 구분해 기록합니다. 다음으로 공개 발화를 확정합니다.'
    params=TOOLS[0]['function']['parameters']
    params['properties']['turn_goal']={'type':'object','properties':{
        'intention':{'type':'string','description':'이번 대화에서 내담자가 표현하거나 지키려는 바람·경계. 아직 결정하기 어려운 마음도 그대로 표현합니다.'},
        'grounds':{'type':'string','description':'제공된 개인 정보와 현재 appraisal의 어떤 점에서 이 바람·경계가 형성되었는지'},
        'uncertainty':{'type':'string','description':'내담자가 자신의 의도에서 아직 정하지 못한 부분. 없으면 빈 문자열'}},
        'required':['intention','grounds','uncertainty'],'additionalProperties':False}
    params['required'].append('turn_goal')
    params['properties']['response_plan']['properties']['action']['description']='대화 의도를 표현하기 위해 답할 내용, 보류할 내용, 말하는 방식을 구체적으로 정합니다.'
    params['properties']['response_plan']['properties']['reason']['description']='선택한 표현 방식이 현재 마음과 대화 의도에 맞는 이유'
if OPTIONS['retrieve']:
    TOOLS[0]['function']['description']=('이번 해석·감정·근거와 '+('대화 의도·표현 계획' if OPTIONS['turn_goal'] else '표현 계획')+'을 잠정 기록합니다. 조회로 근거가 바뀌면 발화 확정 전에 다시 호출하여 판단을 수정할 수 있습니다.')
    params=TOOLS[1]['function']['parameters']
    params['properties']['evidence_decision']={'type':'object','properties':{
        'status':{'type':'string','enum':['sufficient','unavailable','budget_exhausted'],'description':'판단 근거 충분 / 필요한 자료 없음·새 근거 없음 / 실행 한도 도달 중 실제 종료 이유'},
        'reason':{'type':'string','description':'조회한 경우 무엇을 확인했으며 판단에 어떤 영향을 주었는지, 조회하지 않았다면 현재 정보로 반응을 정할 수 있는 이유'},
        'remaining_gap':{'type':'string','description':'남은 자료상의 모호함. 없으면 빈 문자열. 내담자가 느끼는 불확실성과 구분합니다.'}},
        'required':['status','reason','remaining_gap'],'additionalProperties':False}
    params['required'].append('evidence_decision')
    TOOLS.append(copy.deepcopy(memory.TOOL))


TOOLS[0]['function']['parameters']['properties']['trajectory_plan']=copy.deepcopy(trajectory.PLAN)
TOOLS[0]['function']['parameters']['required'].append('trajectory_plan')
TOOLS[0]['function']['description']='이번 상담사 발화의 주관적 해석·감정(appraisal), 사전 궤적과 특성·현재 마음에 근거한 가능한 변화(trajectory_plan), 대화 의도(turn_goal), 표현 계획(response_plan)을 잠정 기록합니다. 필요한 과거 근거는 retrieve_client_memory로 확인하고, 판단이 바뀌면 다시 기록한 뒤 발화를 확정하세요.'
TOOLS.append(function_tool('update_trajectory_progress',
    '앞서 생성된 내면 판단·표현 선택과 확정 발화를 이전 기록과 비교하여 궤적 진행을 요약합니다. 내면의 변화와 실제 공개 범위를 구분하고 각각의 근거·시점·확실성을 보존합니다. 계획을 결과로 바꾸거나 새로운 심리 해석·상호작용 결과를 덧붙이지 않습니다. 남은 불확실성과 보류 조건을 함께 기록합니다. 사전 궤적은 유지하며 기록 검증 후 발화·노트와 함께 저장합니다.',copy.deepcopy(trajectory.PROGRESS)))


def selected_note(items,ids):
    selected=set(ids)
    return note_items.render_items({key:[r for r in rows if r['id'] in selected] for key,rows in items.items()}).replace('(내용 없음)','(선택된 항목 없음)')


def note_selection(parsed,items):
    ids=parsed.get('note_ids')
    available={r['id'] for rows in items.values() for r in rows}
    if not isinstance(ids,list) or any(not isinstance(i,str) or i not in available for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('note_ids에는 제공된 노트 항목 ID를 중복 없이 선택하세요.')
    return ids


def execute(config,payload,client,upstream,frames,inspect,emit,cancelled,original,base_build,mode,runtime):
    body=config['_request_body'];build_request=reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets'))
    ctx=context(config,body,runtime);info=config['_turn_info'];folder=directory(config,info['session_id'])
    path=folder/'state_notes.md';before=ctx['note']['content'];usages={}
    def version(section,action,data):emit('version_event',version=VERSION,section=section,action=action,data=data)
    router_request={}
    def subagent(kind,label,user,schema):
        if cancelled():raise Cancelled('내담자 실험 상태 처리 전에 중단되었습니다.')
        role=(HERE/(kind+'.md')).read_text(encoding='utf-8')
        req=build_request(config,{'system_prompt':role,'messages':[{'role':'user','content':user}],
                                 '_reasoning_budget_role':'subagent'},[],False)
        req['structured_outputs']={'json':schema}
        router_request.update(copy.deepcopy(req))
        aid=kind+'_'+uuid.uuid4().hex[:12];result={}
        meta={'agent_id':aid,'agent_name':label,'agent_kind':kind,'parent_agent_id':'main',
              'parent_call_id':None,'depth':1,'role_instruction':role,'role_source':'developer'}
        def event(event_type,**data):
            if event_type=='done':result.update(data)
            emit('agent_event',**meta,event={'type':event_type,**data})
        event('request',payload=copy.deepcopy(req),iteration=1,phase=label,context_manifest=manifest(req,ctx['note'],'router'))
        try:
            run_turn(config,req,None,upstream,frames,inspect,event,cancelled,[{'role':'user','content':user}])
            if result.get('finish_reason')!='stop':raise ValueError(label+' 응답이 정상 완료되지 않았습니다: '+str(result.get('finish_reason')))
            parsed=parse_json(result['response'])
        except Exception as error:
            event('cancelled' if isinstance(error,Cancelled) else 'error',error=str(error));raise
        usages[aid]=result.get('usage',{})
        return parsed
    version('trajectory_progress','snapshot',{'content':ctx['note']['trajectory_progress_text'],'progress':ctx['note']['trajectory_progress'],'trajectory':ctx['note']['trajectory']})
    version('state_notes','snapshot',{'content':before,'path':str(path),'label':'내담자 노트 · 자유 글머리','note_format':'free_items_v1','states':ctx['note']['states'],'inner':ctx['note']['inner']})
    router_user = user_templates.render('client', VERSION, 'trait_router_user',
        basic_info=user_templates.md(runtime.markdown_data, ctx['basic']), traits=user_templates.md(runtime.markdown_data, ctx['traits']),
        backgrounds=user_templates.md(runtime.markdown_data, ctx['backgrounds']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'], state_notes=ctx['note']['prompt_content'])
    trait_schema=routing_schema(ctx['traits'],ctx['backgrounds'])
    if OPTIONS['select_notes']:
        candidates=[r['id'] for rows in ctx['note']['items'].values() for r in rows]
        trait_schema['properties']['note_ids']={'type':'array','items':{'type':'string','enum':candidates} if candidates else {'type':'string'},'maxItems':len(candidates)}
        trait_schema['required'].append('note_ids')
    routed=subagent('trait_router','특성·배경·노트 라우터' if OPTIONS['select_notes'] else '특성·배경 라우터',router_user,trait_schema)
    selected_ids=note_selection(routed,ctx['note']['items']) if OPTIONS['select_notes'] else [r['id'] for rows in ctx['note']['items'].values() for r in rows]
    if OPTIONS['select_notes']:
        ctx['visible_note']=selected_note(ctx['note']['items'],selected_ids)
        version('note_selection','selected',{'selected_ids':selected_ids,'items':ctx['note']['items'],'mode':'selected','scope':'발화 생성까지'})
    selected=resolve_selection({k:v for k,v in routed.items() if k!='note_ids'},ctx['traits'],ctx['backgrounds'])
    version('traits','routed',{'selected':selected,'automatic':True,'background_routing':True,
                             'background_candidate_count':len(ctx['backgrounds'])})
    actual=payload_for(config,body,ctx,selected,runtime,build_request)
    staged={};final={};judgments=[];speech={};routes=[];updates=[];inner_updates=[];operations=[];patched_items=None;phase=0
    sequence=['stage_client_update','commit_client_utterance','update_client_notes','update_trajectory_progress']
    retrievals=[];queries=set();decision={};precommit_calls=0;progress={} 
    max_precommit=8
    max_calls=20 if OPTIONS['retrieve'] else config.get('delegation',{}).get('max_calls',8)
    request_log=[]
    class Tools:
        opts={'max_calls':max_calls,'max_result_chars':24000}
        requires_finalize=True
        def call(self,name,arguments):
            nonlocal phase,routes,updates,inner_updates,operations,patched_items,precommit_calls
            allowed=allowed_names()
            if name not in allowed:
                raise ValueError('현재 단계에서는 '+(sequence[phase] if phase<len(sequence) else '종료')+'만 사용할 수 있습니다.')
            if name=='retrieve_client_memory':
                memory.validate(arguments)
                signature=json.dumps({k:v for k,v in arguments.items() if k!='reason'},sort_keys=True,ensure_ascii=False)
                if signature in queries:
                    result={'status':'no_new_evidence','results':[],'reason':'같은 조회 조건을 이미 확인했습니다. 이전 결과를 검토하거나 다른 조건을 지정하세요.'}
                else:
                    result=memory.lookup(ctx['note'],arguments);queries.add(signature)
                precommit_calls+=1
                result['remaining_lookups']=memory.MAX_LOOKUPS-len(retrievals)-1
                result['remaining_precommit_decisions']=max(0,max_precommit-precommit_calls)
                entry={'index':len(retrievals)+1,'arguments':copy.deepcopy(arguments),'result':copy.deepcopy(result),'judgment_revision':len(judgments)}
                retrievals.append(entry);version('memory_retrieval','returned',entry)
                return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}],'structuredContent':result}
            if name=='stage_client_update':
                change=validate_update(arguments);staged.update(change);judgments.append(copy.deepcopy(change))
                version('appraisal','staged',change['appraisal']);version('response_plan','staged',change['response_plan'])
                if OPTIONS['turn_goal']:version('turn_goal','staged',change['turn_goal'])
                version('trajectory_plan','staged',change['trajectory_plan'])
                precommit_calls+=1
                result={'recorded':True,'revision':len(judgments)}
                if OPTIONS['retrieve']:result['remaining_precommit_decisions']=max(0,max_precommit-precommit_calls)
                result['next']='필요하면 조회·판단 수정, 근거 확인 후 commit_client_utterance' if OPTIONS['retrieve'] else 'commit_client_utterance'
            elif name=='commit_client_utterance':
                if not isinstance(arguments,dict) or set(arguments)!=({'utterance','evidence_decision'} if OPTIONS['retrieve'] else {'utterance'}) or not isinstance(arguments['utterance'],str) or not arguments['utterance'].strip():
                    raise ValueError('utterance에 공개 발화를 작성하세요.')
                if OPTIONS['retrieve']:
                    value=arguments['evidence_decision']
                    if not isinstance(value,dict) or set(value)!={'status','reason','remaining_gap'} or value['status'] not in ('sufficient','unavailable','budget_exhausted') or any(not isinstance(value[k],str) for k in value) or not value['reason'].strip():
                        raise ValueError('evidence_decision의 종료 종류·이유·남은 자료상의 모호함을 확인하세요.')
                    if value['status']=='budget_exhausted' and len(retrievals)<memory.MAX_LOOKUPS and precommit_calls<max_precommit:
                        raise ValueError('아직 실행 한도에 도달하지 않았습니다. 실제 종료 이유를 기록하세요.')
                    decision.update(copy.deepcopy(value));decision.update(lookup_limit_reached=len(retrievals)>=memory.MAX_LOOKUPS,decision_limit_reached=precommit_calls>=max_precommit)
                    version('evidence_decision','finished',decision)
                speech['utterance']=arguments['utterance'].strip()
                version('utterance','fixed',{'content':speech['utterance'],'pending':True})
                result={'utterance':speech['utterance'],'fixed':True,'saved':False,'next':'update_client_notes','instruction':'전체 노트와 이번 내면 경험을 비교해 필요한 add/update/delete만 전달하세요. 유지할 항목은 생략하고 변경이 없으면 operations를 비우세요.'}
            elif name=='update_client_notes':
                patched_items,operations=note_items.apply(ctx['note']['items'],arguments)
                result={'accepted':True,'operations':operations,'finished':False,'saved':False,'next':'update_trajectory_progress'}
            else:
                progress.update(trajectory.validate(arguments,trajectory.PROGRESS))
                version('trajectory_progress','staged',{'progress':copy.deepcopy(progress),'pending':True})
                result={'accepted':True,'progress':copy.deepcopy(progress),'finished':True,'saved':False}
            if name=='stage_client_update':phase=1
            else:phase+=1
            return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}],'structuredContent':result}
        def terminal_response(self):
            return speech.get('utterance') if phase==len(sequence) else None
    def allowed_names():
        if phase>=2:return sequence[phase:phase+1]
        if not OPTIONS['retrieve']:return [sequence[phase]]
        names=[]
        if precommit_calls<max_precommit or not staged:names.append('stage_client_update')
        if len(retrievals)<memory.MAX_LOOKUPS and precommit_calls<max_precommit:names.append('retrieve_client_memory')
        if staged:names.append('commit_client_utterance')
        return names
    def main_event(kind,**data):
        if kind=='done':final.update(data)
        else:
            if kind=='request':
                data['context_manifest']=manifest(data['payload'],ctx['note'],'full_note_update' if phase>=2 else 'selected_speech' if OPTIONS['select_notes'] else 'full_speech')
                request_log.append(copy.deepcopy(data['payload']))
            emit(kind,**data)
    try:
        def prepare(payload,iteration):
            if phase>=len(sequence):raise ValueError('이미 완료된 턴입니다.')
            if payload.get('tool_choice')=='none':raise ValueError('필수 기록을 마치기 전에 도구 호출 한도에 도달했습니다.')
            if phase>=2 and OPTIONS['select_notes']:
                ctx.pop('visible_note',None)
                full=payload_for(config,body,ctx,selected,runtime,build_request)
                # Replace only the initial user source. Same-turn reasoning and tool messages remain.
                target=next(m for m in payload['messages'] if m['role']=='user')
                target['content']=next(m['content'] for m in full['messages'] if m['role']=='user')
            allowed=allowed_names()
            payload['tools']=[copy.deepcopy(t) for t in TOOLS if not OPTIONS['retrieve'] or t['function']['name'] in allowed]
            payload['tool_choice']={'type':'function','function':{'name':allowed[0]}} if len(allowed)==1 else 'required'
            payload['parallel_tool_calls']=False
            if iteration==1:main_event('request',payload=copy.deepcopy(payload),iteration=1,phase='내담자 해석·반응 방향·발화 생성')
        main_config={**config,'_prepare_payload':prepare}
        run_turn(main_config,actual,Tools(),upstream,frames,inspect,main_event,cancelled,ctx['messages'])
        if phase!=len(sequence) or final.get('response_source')!='committed_utterance':
            raise ValueError('발화·상태 기록이 모두 완료되지 않았습니다.')
        utterance=speech['utterance'];usages['main']=final.get('usage',{})
        after=note_items.render_items(patched_items)
        parsed=parse_note(after);values=parsed['states'];inner=parsed['inner']
        diff=list(difflib.unified_diff(before.splitlines(),after.splitlines(),fromfile='이전 상태',tofile='다음 턴 상태',lineterm=''))
        record={'turn':len(ctx['note']['records'])+1,'turn_id':info['turn_id'],'scenario_hash':fingerprint(ctx['scenario']),
                'selected_traits':selected,'counselor_utterance':ctx['current'],'appraisal':staged['appraisal'],
                'response_plan':staged['response_plan'],'judgment_history':judgments,'utterance':utterance,
                'trajectory_source':ctx['note']['trajectory'],'trajectory_plan':staged['trajectory_plan'],
                'trajectory_before':ctx['note']['trajectory_progress'],'trajectory_progress':copy.deepcopy(progress),
                'inner_updates':inner_updates,'inner_before':ctx['note']['inner'],'inner_after':inner,
                'state_routing':routes,'state_updates':updates,'states_before':ctx['note']['states'],'states_after':values,
                'notes_before':before,'notes_after':after,'diff':diff,'note_operations':operations,
                'note_format':'free_items_v1','legacy_note_converted':before!=ctx['note']['prompt_content'],
                'experiment_condition':OPTIONS['condition'],'note_selection':{'selected_ids':selected_ids,'selective':OPTIONS['select_notes']},
                'memory_retrievals':retrievals,'evidence_decision':decision,'turn_goal':staged.get('turn_goal'),
                'limits':{'lookups':memory.MAX_LOOKUPS if OPTIONS['retrieve'] else 0,'precommit_decisions':max_precommit,'total_calls':max_calls},
                'model_requests':request_log,'router_request':router_request,'router_output':routed,'agent_usage':usages}
        with LOCK:
            current=load(config,info['session_id'])
            if current['content']!=before or [r['turn_id'] for r in current['records']]!=[r['turn_id'] for r in ctx['note']['records']]:
                raise ValueError('다른 실행 또는 편집이 심리상태를 변경했습니다. 덮어쓰기를 중단합니다.')
            if cancelled():raise Cancelled('내담자 실험 상태 확정 전에 중단되었습니다.')
            from counselor.P1.runtime import write
            record_path=folder/'appraisals'/f"{record['turn']:06d}_{info['turn_id']}.json"
            write_new(record_path,record)
            try:write(path,after)
            except Exception:
                record_path.unlink(missing_ok=True);raise
        version('state_notes','updated',{'content':after,'before':before,'path':str(path),'note_format':'free_items_v1','diff':diff,'updates':updates,
                                        'states':values,'inner':inner,'inner_updates':inner_updates,'operations':operations,'pending':False,'turn':record['turn'],'turn_id':info['turn_id']})
        version('trajectory_progress','updated',{'progress':copy.deepcopy(progress),'content':trajectory.render(progress),'before':ctx['note']['trajectory_progress_text'],'pending':False,'turn':record['turn'],'turn_id':info['turn_id']})
        version('appraisal','committed',staged['appraisal'])
        version('utterance','committed',{'content':utterance,'pending':False})
    except Exception:
        version('trajectory_progress','discarded',{'progress':ctx['note']['trajectory_progress'],'pending':False,'discarded':True})
        version('utterance','discarded',{'content':speech.get('utterance',''),'pending':False,'discarded':True})
        version('state_notes','discarded',{'content':before,'path':str(path),'note_format':'free_items_v1','pending':False,'discarded':True})
        raise
    total={};sub={}
    for agent,usage in usages.items():
        for key,value in usage.items():
            total[key]=total.get(key,0)+value
            if agent!='main':sub[key]=sub.get(key,0)+value
    emit('done',response=utterance,history=ctx['messages']+[{'role':'assistant','content':utterance}],finish_reason='stop',
         response_source='committed_utterance',usage=total,main_usage=usages['main'],subagent_usage=sub,agent_usage=usages,experiment_version=VERSION)
