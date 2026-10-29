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
from client.Q1.runtime import split_profile, selection, parse_json, APPRAISAL_SCHEMA as BASE_APPRAISAL_SCHEMA, APPRAISAL_FIELDS
from .states import STATES, INNER, render, parse, parse_note

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
    return {'id':'Q2a','label':'Q2a · 메인 연속 판단 / 발화·상태 기록','view':'client_states',
            'description':'메인이 내면 판단·발화 확정·상태 검토를 수행하고, 현재 내면과 10가지 심리상태를 하나의 노트에 갱신합니다. 특성 라우터만 별도 호출합니다.',
            'system_prompt':(HERE/'system.md').read_text(encoding='utf-8'),'summary_system_prompt':'',
            'required_tools':[tool['function']['name'] for tool in TOOLS],'default_reasoning_budgets':{'main':None,'subagent':None},
            'subagent_prompts':[{'kind':'trait_router','label':'특성 라우터'}],
            'tools':copy.deepcopy(TOOLS)}


def directory(config,session):
    if not isinstance(session,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',session):
        raise ValueError('올바르지 않은 Q2a session_id입니다.')
    return Path(config['_storage_dir'])/'Q2a'/session


def load(config,session):
    folder=directory(config,session) if session else None
    path=folder/'state_notes.md' if folder else None
    records=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((folder/'appraisals').glob('*.json'))] if folder else []
    content=(path if path and path.exists() else HERE/'state_notes_template.md').read_text(encoding='utf-8')
    return {'content':content,**parse_note(content),'records':records,'path':str(path) if path else None}


def context(config, body, runtime, preview=False):
    scenario = runtime.validate_scenario(body['scenario']) if 'scenario' in body else runtime.load_scenario(config)
    messages = validate_messages(body.get('messages'), allow_empty=preview)
    if any(row.get('tool_calls') or row['role'] not in ('user', 'assistant') for row in messages):
        raise ValueError('Q2a 이어가기에는 공개 발화만 사용하세요.')
    note = load(config, body.get('session_id'))
    if note['records']:
        if note['records'][-1]['scenario_hash'] != fingerprint(scenario):
            raise ValueError('Q2a 초기 자료가 달라졌습니다. 새 대화를 시작하세요.')
        expected = [row for record in note['records'] for row in (
            {'role': 'user', 'content': record['counselor_utterance']}, {'role': 'assistant', 'content': record['utterance']})]
        if not config.get('_paired_public_dialogue'):
            if len(messages) == 1:
                messages = expected + messages
            elif messages[:-1] != expected:
                raise ValueError('Q2a 공개 대화가 저장된 기록과 다릅니다.')
    basic, traits = split_profile(scenario['profile'])
    blocks = experiment_versions.input_blocks(config, body)
    if not blocks['profile']:
        basic, traits = {}, []
    if config.get('_paired_public_dialogue') is not None:
        public = config['_paired_public_dialogue'][:-1]
        if not blocks['dialogue']:
            public = public[config.get('_paired_initial_count', 0):]
        dialogue = '\n\n'.join(('상담사' if row['role'] == 'counselor' else '내담자') + ': ' + row['content'] for row in public)
    else:
        dialogue = (runtime.dialogue_script(scenario) + '\n\n' if blocks['dialogue'] else '')
        dialogue += '\n\n'.join(('상담사' if row['role'] == 'user' else '내담자') + ': ' + row['content'] for row in messages[:-1])
    return {'scenario': scenario, 'blocks': blocks, 'basic': basic, 'traits': traits, 'note': note,
            'dialogue': dialogue, 'current': messages[-1]['content'], 'messages': messages}



def payload_for(config,body,ctx,selected,runtime,base_build,preview=False):
    text = user_templates.render('client', 'Q2a', basic_info=user_templates.md(runtime.markdown_data, ctx['basic']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'],
        selected_traits=user_templates.md(runtime.markdown_data, selected), state_notes=ctx['note']['content'],
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
    return {'type':'object','properties':{key:{'type':'array','items':{
        'type':'object','properties':{name:({'type':'string','enum':list(STATES)} if name=='state' else {'type':'string'}) for name in fields},
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
    schema=array_schema('updates',('state','content','reason'))
    schema['properties']['updates']['description']='선택한 10가지 심리상태 중 변경할 항목. 변경이 없으면 빈 배열입니다.'
    schema['properties']['inner_updates']={'type':'array',
        'description':'현재 내면에서 변경할 항목. 심리상태 선택과 별개로 갱신할 수 있으며, 생략한 항목은 유지합니다. 변경이 없으면 빈 배열입니다.',
        'items':{'type':'object','properties':{
            'section':{'type':'string','enum':list(INNER),'description':' / '.join(key+': '+label+' — '+meaning for key,(label,meaning) in INNER.items())},
            'content':{'type':'string','description':'선택한 현재 내면 항목의 갱신 후 전체 본문. 다음 턴에도 이어질 구체적인 마음을 기록하며, 기록할 내용이 없어지면 (내용 없음)을 입력합니다.'},
            'reason':{'type':'string','description':'이 항목을 추가·수정·비우는 근거'}},
            'required':['section','content','reason'],'additionalProperties':False}}
    schema['required'].append('inner_updates')
    return schema


def checked_note_updates(arguments, allowed):
    if not isinstance(arguments,dict) or set(arguments)!={'updates','inner_updates'}:
        raise ValueError('updates와 inner_updates 배열을 모두 전달하세요. 각각 변경이 없으면 빈 배열입니다.')
    states=checked_rows({'updates':arguments['updates']},'updates',('state','content','reason'),allowed)
    inner=checked_rows({'inner_updates':arguments['inner_updates']},'inner_updates',('section','content','reason'),INNER,'section')
    return copy.deepcopy(states),copy.deepcopy(inner)


TOOLS = [TOOL,
    function_tool('commit_client_utterance',
        '앞서 기록한 내면 판단과 반응 방향에 맞는 공개 발화를 확정합니다. 이후 상태 검토 중에는 발화를 바꾸지 않습니다. 이 도구만 성공한 경우 전체 턴 저장은 아직 완료되지 않습니다.',
        {'type':'object','properties':{'utterance':{'type':'string','description':'상담사에게 말할 내담자 발화 원문'}},'required':['utterance'],'additionalProperties':False}),
    function_tool('select_client_states',
        '확정한 발화, 이번 내면 판단, 이전 상태를 보고 설명을 검토할 상태와 이유를 기록합니다. 선택이 변경을 강제하지 않습니다. 검토할 항목이 없으면 selections를 빈 배열로 전달합니다.',
        array_schema('selections',('state','reason'))),
    function_tool('update_client_states',
        '현재 내면과 심리상태를 하나의 내담자 노트에 기록하고 턴을 마칩니다. inner_updates에는 남아 있는 감정·걱정, 말하지 않은 속마음·바람, 변화·유지 이유의 변경을 기록합니다. updates에는 앞서 선택한 심리상태 중 실제 변경할 항목을 기록합니다. 두 영역은 독립적으로 갱신할 수 있습니다. 각 항목의 전체 본문과 근거를 작성하며, 유지할 항목은 생략하고 변경이 없는 배열은 빈 배열로 전달합니다. 모든 단계가 검증되면 발화와 노트를 함께 저장합니다.',
        note_update_schema())]


def execute(config,payload,client,upstream,frames,inspect,emit,cancelled,original,base_build,mode,runtime):
    body=config['_request_body'];build_request=reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets'))
    ctx=context(config,body,runtime);info=config['_turn_info'];folder=directory(config,info['session_id'])
    path=folder/'state_notes.md';before=ctx['note']['content'];usages={}
    def version(section,action,data):emit('version_event',version='Q2a',section=section,action=action,data=data)
    def subagent(kind,label,user,schema):
        if cancelled():raise Cancelled('Q2a 상태 처리 전에 중단되었습니다.')
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
    router_user = user_templates.render('client', 'Q2a', 'trait_router_user',
        basic_info=user_templates.md(runtime.markdown_data, ctx['basic']), traits=user_templates.md(runtime.markdown_data, ctx['traits']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'], state_notes=before)
    trait_schema={'type':'object','properties':{'selections':{'type':'array','items':{'type':'object','properties':{
        'trait_id':{'type':'string'},'reason':{'type':'string'}},'required':['trait_id','reason'],'additionalProperties':False}}},'required':['selections'],'additionalProperties':False}
    selected=selection(json.dumps(subagent('trait_router','특성 라우터',router_user,trait_schema),ensure_ascii=False),ctx['traits'])
    version('traits','routed',{'selected':selected,'automatic':True})
    actual=payload_for(config,body,ctx,selected,runtime,build_request)
    emit('request',payload=copy.deepcopy(actual),iteration=1,phase='내담자 해석·반응 방향·발화 생성')
    staged={};final={};judgments=[];speech={};routes=[];updates=[];inner_updates=[];phase=0
    sequence=[tool['function']['name'] for tool in TOOLS]
    class Tools:
        opts={'max_calls':config.get('delegation',{}).get('max_calls',8),'max_result_chars':16000}
        requires_finalize=True
        def call(self,name,arguments):
            nonlocal phase,routes,updates,inner_updates
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
                result={'selected':routes,'next':'update_client_states','instruction':'updates에는 선택한 심리상태의 변경을, inner_updates에는 현재 내면의 변경을 기록하세요. 각 영역에 변경이 없으면 해당 배열을 비우세요.'}
            else:
                updates,inner_updates=checked_note_updates(arguments,{row['state'] for row in routes})
                result={'accepted':True,'updates':updates,'inner_updates':inner_updates,'finished':True,'saved':False}
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
        values=dict(ctx['note']['states'])
        updates=[{**row,'content':row['content'].strip()} for row in updates if row['content'].strip()!=values[row['state']]]
        for row in updates:values[row['state']]=row['content']
        inner=dict(ctx['note']['inner'])
        inner_updates=[{**row,'content':row['content'].strip()} for row in inner_updates if row['content'].strip()!=inner[row['section']]]
        for row in inner_updates:inner[row['section']]=row['content']
        after=render(values,inner) if updates or inner_updates or before.startswith('# 현재 심리상태\n') else before
        parse_note(after)
        diff=list(difflib.unified_diff(before.splitlines(),after.splitlines(),fromfile='이전 상태',tofile='다음 턴 상태',lineterm=''))
        record={'turn':len(ctx['note']['records'])+1,'turn_id':info['turn_id'],'scenario_hash':fingerprint(ctx['scenario']),
                'selected_traits':selected,'counselor_utterance':ctx['current'],'appraisal':staged['appraisal'],
                'response_plan':staged['response_plan'],'judgment_history':judgments,'utterance':utterance,
                'inner_updates':inner_updates,'inner_before':ctx['note']['inner'],'inner_after':inner,
                'state_routing':routes,'state_updates':updates,'states_before':ctx['note']['states'],'states_after':values,
                'notes_before':before,'notes_after':after,'diff':diff}
        with LOCK:
            current=load(config,info['session_id'])
            if current['content']!=before or [r['turn_id'] for r in current['records']]!=[r['turn_id'] for r in ctx['note']['records']]:
                raise ValueError('다른 실행 또는 편집이 심리상태를 변경했습니다. 덮어쓰기를 중단합니다.')
            if cancelled():raise Cancelled('Q2a 상태 확정 전에 중단되었습니다.')
            from counselor.P1.runtime import write
            record_path=folder/'appraisals'/f"{record['turn']:06d}_{info['turn_id']}.json"
            write_new(record_path,record)
            try:write(path,after)
            except Exception:
                record_path.unlink(missing_ok=True);raise
        version('state_notes','updated',{'content':after,'before':before,'path':str(path),'diff':diff,'updates':updates,
                                        'states':values,'inner':inner,'inner_updates':inner_updates,'pending':False,'turn':record['turn'],'turn_id':info['turn_id']})
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
         response_source='committed_utterance',usage=total,main_usage=usages['main'],subagent_usage=sub,agent_usage=usages,experiment_version='Q2a')
