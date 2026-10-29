"""Client with one private note: current inner experience and ten qualitative states."""
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

HERE = Path(__file__).resolve().parent
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
    return {'id':'Q2a_trait_background','label':'Q2a_trait_background · 특성·배경 연결','view':'client_states',
            'description':'라우터가 관련 특성과 성장 경험·상황 사례를 함께 선택하고 원문을 전달합니다. 이후 내면 판단과 발화를 생성하고, 노트는 글머리 항목 단위로 추가·수정·삭제합니다.',
            'system_prompt':(HERE/'system.md').read_text(encoding='utf-8'),'summary_system_prompt':'',
            'required_tools':[tool['function']['name'] for tool in TOOLS],'default_reasoning_budgets':{'main':None,'subagent':None},
            'subagent_prompts':[{'kind':'trait_router','label':'특성·배경 라우터'}],
            'tools':copy.deepcopy(TOOLS)}


def directory(config,session):
    if not isinstance(session,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',session):
        raise ValueError('올바르지 않은 Q2a_trait_background session_id입니다.')
    return Path(config['_storage_dir'])/'Q2a_trait_background'/session


def load(config,session):
    folder=directory(config,session) if session else None
    path=folder/'state_notes.md' if folder else None
    records=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((folder/'appraisals').glob('*.json'))] if folder else []
    content=(path if path and path.exists() else HERE/'state_notes_template.md').read_text(encoding='utf-8')
    items=note_items.read_items(content)
    return {'content':content,**parse_note(content),'items':items,'prompt_content':note_items.render_items(items),
            'records':records,'path':str(path) if path else None}


def context(config, body, runtime, preview=False):
    scenario = runtime.validate_scenario(body['scenario']) if 'scenario' in body else runtime.load_scenario(config)
    messages = validate_messages(body.get('messages'), allow_empty=preview)
    if any(row.get('tool_calls') or row['role'] not in ('user', 'assistant') for row in messages):
        raise ValueError('Q2a_trait_background 이어가기에는 공개 발화만 사용하세요.')
    note = load(config, body.get('session_id'))
    if note['records']:
        if note['records'][-1]['scenario_hash'] != fingerprint(scenario):
            raise ValueError('Q2a_trait_background 초기 자료가 달라졌습니다. 새 대화를 시작하세요.')
        expected = [row for record in note['records'] for row in (
            {'role': 'user', 'content': record['counselor_utterance']}, {'role': 'assistant', 'content': record['utterance']})]
        if not config.get('_paired_public_dialogue'):
            if len(messages) == 1:
                messages = expected + messages
            elif messages[:-1] != expected:
                raise ValueError('Q2a_trait_background 공개 대화가 저장된 기록과 다릅니다.')
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
    text = user_templates.render('client', 'Q2a_trait_background', basic_info=user_templates.md(runtime.markdown_data, ctx['basic']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'],
        selected_traits=user_templates.md(runtime.markdown_data, selected), state_notes=ctx['note']['prompt_content'],
        state_definitions=user_templates.md(runtime.markdown_data, {k: {'name': v[0], 'meaning': v[1]} for k,v in STATES.items()}))
    request={**body,'messages':[{'role':'user','content':text}]}
    request.setdefault('system_prompt',specification()['system_prompt'])
    if not ctx['blocks']['system']:request['system_prompt']=''
    payload=base_build(config,request,copy.deepcopy(TOOLS),preview)
    payload['tool_choice']={'type':'function','function':{'name':'stage_client_update'}}
    return payload


def build(config,body,runtime,base_build,preview=False):
    ctx=context(config,body,runtime,preview)
    return payload_for(config,body,ctx,[],runtime,reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets')),preview)


def array_schema(key,fields):
    properties={name:({'type':'string','enum':list(STATES)} if name=='state' else {'type':'string'}) for name in fields}
    properties['state']['description']='입력의 상태 항목의 의미에 제시된 심리상태 ID.'
    if key=='selections':
        properties['reason']['description']='입력된 이전 노트와 이번 내면 경험의 어떤 내용 때문에 이 심리상태를 검토할 필요가 있는지 설명합니다.'
    return {'type':'object','properties':{key:{'type':'array','items':{
        'type':'object','properties':properties,
        'required':list(fields),'additionalProperties':False}}},'required':[key],'additionalProperties':False}


def checked_rows(value,key,fields,allowed,id_field='state'):
    if not isinstance(value,dict) or set(value)!={key} or not isinstance(value[key],list):
        raise ValueError(key+' 배열을 반환해야 합니다.')
    seen=set()
    for row in value[key]:
        if (not isinstance(row,dict) or set(row)!=set(fields)
            or any(not isinstance(row[k],str) or not row[k].strip() for k in fields)
            or row[id_field] not in allowed or row[id_field] in seen):
            raise ValueError('알 수 없거나 중복된 항목, 빈 본문·근거를 반환했습니다.')
        if 'content' in row and re.search(r'^#{1,6} ',row['content'],re.M):
            raise ValueError('항목 본문에는 제목을 넣지 마세요.')
        seen.add(row[id_field])
    return value[key]


def validate_update(args):
    if not isinstance(args,dict) or set(args)!={'appraisal','response_plan'}:
        raise ValueError('appraisal와 response_plan을 기록하세요. 상태 노트는 발화 후 갱신합니다.')
    for key,fields in [('appraisal',APPRAISAL_FIELDS),('response_plan',('action','reason'))]:
        part=args[key]
        if (not isinstance(part,dict) or set(part)!=set(fields)
            or any(not isinstance(part[k],str) or (k!='uncertainty' and not part[k].strip()) for k in fields)):
            raise ValueError(key+'의 항목·본문을 확인하세요.')
    return copy.deepcopy(args)


def function_tool(name, description, schema):
    return {'type':'function','function':{'name':name,'description':description,'parameters':schema}}


def note_update_schema():
    return note_items.schema()


TOOLS = [TOOL,
    function_tool('commit_client_utterance',
        '앞서 기록한 내면 판단과 반응 방향에 맞는 공개 발화를 확정합니다. 이후 상태 검토 중에는 발화를 바꾸지 않습니다. 이 도구만 성공한 경우 전체 턴 저장은 아직 완료되지 않습니다.',
        {'type':'object','properties':{'utterance':{'type':'string','description':'상담사에게 말할 내담자 발화 원문'}},'required':['utterance'],'additionalProperties':False}),
    function_tool('select_client_states',
        '입력된 내담자 노트의 심리상태 항목과 상태 항목의 의미를 읽고, 이번 appraisal·표현 선택·말하는 경험과 연결해 검토할 항목과 이유를 기록합니다. 이전 상태는 이번 상담사 발화 전에 저장된 기록입니다. 실제 변경 여부는 선택한 항목을 검토한 뒤 판단합니다. 검토할 항목이 없으면 selections를 빈 배열로 전달합니다.',
        array_schema('selections',('state','reason'))),
    function_tool('update_client_states',
        '입력 노트의 글머리 항목을 이번 appraisal·표현 선택·말하는 경험과 비교하고, 현재도 유효한 마음과 조건을 관리합니다. operations에 필요한 add/update/delete만 전달하세요. 같은 대상의 마음이 달라지면 기존 ID를 update하고, 별개의 새 내용은 add하며, 해소·정정·중복으로 유효하지 않은 내용은 delete합니다. 유지할 항목은 생략합니다. 심리상태는 선택한 영역만, 현재 내면의 세 영역은 별도로 수정할 수 있습니다. 변경 근거는 실행 이력에 남고 현재 노트에는 자동 추가되지 않습니다. 전체 검증 후 발화와 노트를 함께 저장합니다.',
        note_update_schema())]


def execute(config,payload,client,upstream,frames,inspect,emit,cancelled,original,base_build,mode,runtime):
    body=config['_request_body'];build_request=reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets'))
    ctx=context(config,body,runtime);info=config['_turn_info'];folder=directory(config,info['session_id'])
    path=folder/'state_notes.md';before=ctx['note']['content'];usages={}
    def version(section,action,data):emit('version_event',version='Q2a_trait_background',section=section,action=action,data=data)
    def subagent(kind,label,user,schema):
        if cancelled():raise Cancelled('Q2a_trait_background 상태 처리 전에 중단되었습니다.')
        role=(HERE/(kind+'.md')).read_text(encoding='utf-8')
        req=build_request(config,{'system_prompt':role,'messages':[{'role':'user','content':user}],
                                 '_reasoning_budget_role':'subagent'},[],False)
        req['structured_outputs']={'json':schema}
        aid=kind+'_'+uuid.uuid4().hex[:12];result={}
        meta={'agent_id':aid,'agent_name':label,'agent_kind':kind,'parent_agent_id':'main',
              'parent_call_id':None,'depth':1,'role_instruction':role,'role_source':'developer'}
        def event(event_type,**data):
            if event_type=='done':result.update(data)
            emit('agent_event',**meta,event={'type':event_type,**data})
        event('request',payload=copy.deepcopy(req),iteration=1,phase=label)
        try:
            run_turn(config,req,None,upstream,frames,inspect,event,cancelled,[{'role':'user','content':user}])
            if result.get('finish_reason')!='stop':raise ValueError(label+' 응답이 정상 완료되지 않았습니다: '+str(result.get('finish_reason')))
            parsed=parse_json(result['response'])
        except Exception as error:
            event('cancelled' if isinstance(error,Cancelled) else 'error',error=str(error));raise
        usages[aid]=result.get('usage',{})
        return parsed
    version('state_notes','snapshot',{'content':before,'path':str(path),'label':'내담자 노트 · 현재 내면 / 심리상태','states':ctx['note']['states'],'inner':ctx['note']['inner']})
    router_user = user_templates.render('client', 'Q2a_trait_background', 'trait_router_user',
        basic_info=user_templates.md(runtime.markdown_data, ctx['basic']), traits=user_templates.md(runtime.markdown_data, ctx['traits']),
        backgrounds=user_templates.md(runtime.markdown_data, ctx['backgrounds']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'], state_notes=ctx['note']['prompt_content'])
    trait_schema=routing_schema(ctx['traits'],ctx['backgrounds'])
    selected=resolve_selection(subagent('trait_router','특성·배경 라우터',router_user,trait_schema),ctx['traits'],ctx['backgrounds'])
    version('traits','routed',{'selected':selected,'automatic':True,'background_routing':True,
                             'background_candidate_count':len(ctx['backgrounds'])})
    actual=payload_for(config,body,ctx,selected,runtime,build_request)
    emit('request',payload=copy.deepcopy(actual),iteration=1,phase='내담자 해석·반응 방향·발화 생성')
    staged={};final={};judgments=[];speech={};routes=[];updates=[];inner_updates=[];operations=[];patched_items=None;phase=0
    sequence=[tool['function']['name'] for tool in TOOLS]
    class Tools:
        opts={'max_calls':config.get('delegation',{}).get('max_calls',8),'max_result_chars':16000}
        requires_finalize=True
        def call(self,name,arguments):
            nonlocal phase,routes,updates,inner_updates,operations,patched_items
            if phase>=len(sequence) or name!=sequence[phase]:
                raise ValueError('현재 단계에서는 '+(sequence[phase] if phase<len(sequence) else '종료')+'만 사용할 수 있습니다.')
            if name=='stage_client_update':
                change=validate_update(arguments);staged.update(change);judgments.append(copy.deepcopy(change))
                version('appraisal','staged',change['appraisal']);version('response_plan','staged',change['response_plan'])
                result={'recorded':True,'next':'commit_client_utterance'}
            elif name=='commit_client_utterance':
                if not isinstance(arguments,dict) or set(arguments)!={'utterance'} or not isinstance(arguments['utterance'],str) or not arguments['utterance'].strip():
                    raise ValueError('utterance에 공개 발화를 작성하세요.')
                speech['utterance']=arguments['utterance'].strip()
                version('utterance','fixed',{'content':speech['utterance'],'pending':True})
                result={'utterance':speech['utterance'],'fixed':True,'saved':False,'next':'select_client_states'}
            elif name=='select_client_states':
                routes=copy.deepcopy(checked_rows(arguments,'selections',('state','reason'),STATES))
                version('state_routing','routed',{'selected':routes,'after_utterance':True,'source':'main'})
                result={'selected':routes,'next':'update_client_states','instruction':'입력 노트의 [n...] 글머리 항목을 이번 내면 경험과 비교하고 필요한 add/update/delete만 operations에 전달하세요. 선택한 심리상태 영역과 현재 내면 세 영역을 수정할 수 있습니다. 유지할 항목은 생략하고 변경이 없으면 operations를 비우세요.'}
            else:
                patched_items,operations=note_items.apply(ctx['note']['items'],arguments,{row['state'] for row in routes})
                updates=note_items.summaries(patched_items,operations,STATES,'state')
                inner_updates=note_items.summaries(patched_items,operations,INNER,'section')
                result={'accepted':True,'operations':operations,'finished':True,'saved':False}
            phase+=1
            return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}],'structuredContent':result}
        def terminal_response(self):
            return speech.get('utterance') if phase==len(sequence) else None
    def main_event(kind,**data):
        if kind=='done':final.update(data)
        else:emit(kind,**data)
    try:
        def prepare(payload,iteration):
            if phase>=len(sequence):raise ValueError('이미 완료된 턴입니다.')
            if payload.get('tool_choice')=='none':raise ValueError('필수 기록을 마치기 전에 도구 호출 한도에 도달했습니다.')
            payload['tool_choice']={'type':'function','function':{'name':sequence[phase]}}
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
                'inner_updates':inner_updates,'inner_before':ctx['note']['inner'],'inner_after':inner,
                'state_routing':routes,'state_updates':updates,'states_before':ctx['note']['states'],'states_after':values,
                'notes_before':before,'notes_after':after,'diff':diff,'note_operations':operations,
                'note_format':'items_v1','legacy_note_converted':before!=ctx['note']['prompt_content']}
        with LOCK:
            current=load(config,info['session_id'])
            if current['content']!=before or [r['turn_id'] for r in current['records']]!=[r['turn_id'] for r in ctx['note']['records']]:
                raise ValueError('다른 실행 또는 편집이 심리상태를 변경했습니다. 덮어쓰기를 중단합니다.')
            if cancelled():raise Cancelled('Q2a_trait_background 상태 확정 전에 중단되었습니다.')
            from counselor.P1.runtime import write
            record_path=folder/'appraisals'/f"{record['turn']:06d}_{info['turn_id']}.json"
            write_new(record_path,record)
            try:write(path,after)
            except Exception:
                record_path.unlink(missing_ok=True);raise
        version('state_notes','updated',{'content':after,'before':before,'path':str(path),'diff':diff,'updates':updates,
                                        'states':values,'inner':inner,'inner_updates':inner_updates,'operations':operations,'pending':False,'turn':record['turn'],'turn_id':info['turn_id']})
        version('appraisal','committed',staged['appraisal'])
        version('utterance','committed',{'content':utterance,'pending':False})
    except Exception:
        version('utterance','discarded',{'content':speech.get('utterance',''),'pending':False,'discarded':True})
        version('state_notes','discarded',{'content':before,'path':str(path),'pending':False,'discarded':True})
        raise
    total={};sub={}
    for agent,usage in usages.items():
        for key,value in usage.items():
            total[key]=total.get(key,0)+value
            if agent!='main':sub[key]=sub.get(key,0)+value
    emit('done',response=utterance,history=ctx['messages']+[{'role':'assistant','content':utterance}],finish_reason='stop',
         response_source='committed_utterance',usage=total,main_usage=usages['main'],subagent_usage=sub,agent_usage=usages,experiment_version='Q2a_trait_background')
