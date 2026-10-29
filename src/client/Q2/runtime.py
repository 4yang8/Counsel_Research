"""Q1-based client with ten qualitative states, updated only after public speech."""
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
from client.Q1.runtime import split_profile, selection, parse_json, APPRAISAL_SCHEMA, APPRAISAL_FIELDS
from .states import STATES, render, parse

HERE = Path(__file__).resolve().parent
LOCK = threading.Lock()
TOOL = {'type':'function','function':{
    'name':'stage_client_update',
    'description':'최종 발화 전에 이번 상담사 말을 받아들인 의미·감정·근거와 반응 방향을 비공개로 기록합니다. 심리상태 노트는 이 도구로 수정하지 않습니다. 발화 완료 후 별도 상태 검토 단계가 이 판단과 실제 발화를 함께 참고합니다. 다시 제출하면 최종 채택 판단을 교체합니다.',
    'parameters':{'type':'object','properties':{
        'appraisal':copy.deepcopy(APPRAISAL_SCHEMA),
        'response_plan':{'type':'object','properties':{'action':{'type':'string','description':'이번에 무엇을 말하거나 보류하고 어떻게 반응할지'},'reason':{'type':'string','description':'현재 상태·해석·대화 맥락에 근거한 선택 이유'}},'required':['action','reason'],'additionalProperties':False}},
        'required':['appraisal','response_plan'],'additionalProperties':False}}}


def specification():
    return {'id':'Q2','label':'Q2 · 자연어 심리상태 / 발화 후 갱신','view':'client_states',
            'description':'10개 자연어 심리상태를 참고해 발화하고, 발화 완료 후 검토할 상태를 선정·갱신합니다.',
            'system_prompt':(HERE/'system.md').read_text(encoding='utf-8'),'summary_system_prompt':'',
            'required_tools':['stage_client_update'],'default_reasoning_budgets':{'main':None,'subagent':None},
            'subagent_prompts':[{'kind':'trait_router','label':'특성 라우터'},
                                {'kind':'state_router','label':'발화 후 상태 라우터'},
                                {'kind':'state_updater','label':'자연어 상태 갱신 담당'}],
            'tools':[copy.deepcopy(TOOL)]}


def directory(config,session):
    if not isinstance(session,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',session):
        raise ValueError('올바르지 않은 Q2 session_id입니다.')
    return Path(config['_storage_dir'])/'Q2'/session


def load(config,session):
    folder=directory(config,session) if session else None
    path=folder/'state_notes.md' if folder else None
    records=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((folder/'appraisals').glob('*.json'))] if folder else []
    content=(path if path and path.exists() else HERE/'state_notes_template.md').read_text(encoding='utf-8')
    return {'content':content,'states':parse(content),'records':records,'path':str(path) if path else None}


def context(config, body, runtime, preview=False):
    scenario = runtime.validate_scenario(body['scenario']) if 'scenario' in body else runtime.load_scenario(config)
    messages = validate_messages(body.get('messages'), allow_empty=preview)
    if any(row.get('tool_calls') or row['role'] not in ('user', 'assistant') for row in messages):
        raise ValueError('Q2 이어가기에는 공개 발화만 사용하세요.')
    note = load(config, body.get('session_id'))
    if note['records']:
        if note['records'][-1]['scenario_hash'] != fingerprint(scenario):
            raise ValueError('Q2 초기 자료가 달라졌습니다. 새 대화를 시작하세요.')
        expected = [row for record in note['records'] for row in (
            {'role': 'user', 'content': record['counselor_utterance']}, {'role': 'assistant', 'content': record['utterance']})]
        if not config.get('_paired_public_dialogue'):
            if len(messages) == 1:
                messages = expected + messages
            elif messages[:-1] != expected:
                raise ValueError('Q2 공개 대화가 저장된 기록과 다릅니다.')
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
    text = user_templates.render('client', 'Q2', basic_info=user_templates.md(runtime.markdown_data, ctx['basic']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'],
        selected_traits=user_templates.md(runtime.markdown_data, selected), state_notes=ctx['note']['content'])
    request={**body,'messages':[{'role':'user','content':text}]}
    request.setdefault('system_prompt',specification()['system_prompt'])
    if not ctx['blocks']['system']:request['system_prompt']=''
    payload=base_build(config,request,[copy.deepcopy(TOOL)],preview)
    payload['tool_choice']={'type':'function','function':{'name':'stage_client_update'}}
    return payload


def build(config,body,runtime,base_build,preview=False):
    ctx=context(config,body,runtime,preview)
    return payload_for(config,body,ctx,[],runtime,reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets')),preview)


def array_schema(key,fields):
    return {'type':'object','properties':{key:{'type':'array','items':{
        'type':'object','properties':{name:({'type':'string','enum':list(STATES)} if name=='state' else {'type':'string'}) for name in fields},
        'required':list(fields),'additionalProperties':False}}},'required':[key],'additionalProperties':False}


def checked_rows(value,key,fields,allowed):
    if not isinstance(value,dict) or set(value)!={key} or not isinstance(value[key],list):
        raise ValueError(key+' 배열을 반환해야 합니다.')
    seen=set()
    for row in value[key]:
        if (not isinstance(row,dict) or set(row)!=set(fields)
            or any(not isinstance(row[k],str) or not row[k].strip() for k in fields)
            or row['state'] not in allowed or row['state'] in seen):
            raise ValueError('알 수 없거나 중복된 상태, 빈 본문·근거를 반환했습니다.')
        if 'content' in row and re.search(r'^#{1,6} ',row['content'],re.M):
            raise ValueError('상태 본문에는 항목 제목을 넣지 마세요.')
        seen.add(row['state'])
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


def execute(config,payload,client,upstream,frames,inspect,emit,cancelled,original,base_build,mode,runtime):
    body=config['_request_body'];build_request=reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets'))
    ctx=context(config,body,runtime);info=config['_turn_info'];folder=directory(config,info['session_id'])
    path=folder/'state_notes.md';before=ctx['note']['content'];usages={}
    def version(section,action,data):emit('version_event',version='Q2',section=section,action=action,data=data)
    def subagent(kind,label,user,schema):
        if cancelled():raise Cancelled('Q2 상태 처리 전에 중단되었습니다.')
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
    version('state_notes','snapshot',{'content':before,'path':str(path),'label':'현재 심리상태','states':ctx['note']['states']})
    router_user = user_templates.render('client', 'Q2', 'trait_router_user',
        basic_info=user_templates.md(runtime.markdown_data, ctx['basic']), traits=user_templates.md(runtime.markdown_data, ctx['traits']),
        dialogue=ctx['dialogue'], counselor_utterance=ctx['current'], state_notes=before)
    trait_schema={'type':'object','properties':{'selections':{'type':'array','items':{'type':'object','properties':{
        'trait_id':{'type':'string'},'reason':{'type':'string'}},'required':['trait_id','reason'],'additionalProperties':False}}},'required':['selections'],'additionalProperties':False}
    selected=selection(json.dumps(subagent('trait_router','특성 라우터',router_user,trait_schema),ensure_ascii=False),ctx['traits'])
    version('traits','routed',{'selected':selected,'automatic':True})
    actual=payload_for(config,body,ctx,selected,runtime,build_request)
    emit('request',payload=copy.deepcopy(actual),iteration=1,phase='내담자 해석·반응 방향·발화 생성')
    staged={};final={};judgments=[]
    class Tools:
        opts={'max_calls':config.get('delegation',{}).get('max_calls',8),'max_result_chars':16000}
        def call(self,name,arguments):
            if name!='stage_client_update':raise ValueError('Q2에서는 stage_client_update만 사용할 수 있습니다.')
            change=validate_update(arguments);staged.clear();staged.update(change);judgments.append(copy.deepcopy(change))
            version('appraisal','staged',change['appraisal']);version('response_plan','staged',change['response_plan'])
            return {'content':[{'type':'text','text':'이번 판단을 기록했습니다. 이제 내담자 발화를 생성하세요. 심리상태 노트는 발화 완료 후 검토합니다.'}],
                    'structuredContent':{'staged':True,'saved':False,'note_changed':False}}
    def main_event(kind,**data):
        if kind=='done':
            final.update(data);emit('model_result',response=data['response'],reasoning=data.get('reasoning',''),finish_reason=data['finish_reason'])
        else:emit(kind,**data)
    try:
        def prepare(payload,iteration):
            if not staged:
                payload['tool_choice']={'type':'function','function':{'name':'stage_client_update'}}
            elif payload.get('tool_choice')!='none':
                payload['tool_choice']='auto'
        main_config={**config,'_prepare_payload':prepare}
        run_turn(main_config,actual,Tools(),upstream,frames,inspect,main_event,cancelled,ctx['messages'])
        if final.get('finish_reason')!='stop' or not final.get('response','').strip():raise ValueError('Q2 내담자 발화가 정상 완료되지 않았습니다.')
        if not staged:raise ValueError('이번 판단 기록이 없습니다. stage_client_update 호출 후 발화를 생성해야 합니다.')
        utterance=final['response'].strip();usages['main']=final.get('usage',{})
        evidence={'기본 정보':ctx['basic'],'관련 특성':selected,'상태 정의':{k:{'name':v[0],'meaning':v[1]} for k,v in STATES.items()},
                  '최종 채택 판단':staged,'이전 제출 판단 (최종 판단으로 대체됨)':judgments[:-1]}
        post_values = dict(evidence=runtime.markdown_data(evidence), state_notes=before,
                           dialogue=ctx['dialogue'], counselor_utterance=ctx['current'], utterance=utterance)
        post = user_templates.render('client', 'Q2', 'state_router_user', **post_values)
        routes=checked_rows(subagent('state_router','발화 후 상태 라우터',post,array_schema('selections',('state','reason'))),
                            'selections',('state','reason'),STATES)
        version('state_routing','routed',{'selected':routes,'after_utterance':True})
        updates=[]
        if routes:
            update_user=user_templates.render('client', 'Q2', 'state_updater_user', **post_values,
                selected_states=user_templates.md(runtime.markdown_data, routes))
            update_schema=array_schema('updates',('state','content','reason'))
            update_schema['properties']['updates']['items']['properties']['state']['enum']=[row['state'] for row in routes]
            updates=checked_rows(subagent('state_updater','자연어 상태 갱신 담당',update_user,update_schema),
                                 'updates',('state','content','reason'),{row['state'] for row in routes})
        values=dict(ctx['note']['states'])
        updates=[{**row,'content':row['content'].strip()} for row in updates if row['content'].strip()!=values[row['state']]]
        for row in updates:values[row['state']]=row['content']
        after=render(values) if updates else before;parse(after)
        diff=list(difflib.unified_diff(before.splitlines(),after.splitlines(),fromfile='이전 상태',tofile='다음 턴 상태',lineterm=''))
        record={'turn':len(ctx['note']['records'])+1,'turn_id':info['turn_id'],'scenario_hash':fingerprint(ctx['scenario']),
                'selected_traits':selected,'counselor_utterance':ctx['current'],'appraisal':staged['appraisal'],
                'response_plan':staged['response_plan'],'judgment_history':judgments,'utterance':utterance,
                'state_routing':routes,'state_updates':updates,'states_before':ctx['note']['states'],'states_after':values,
                'notes_before':before,'notes_after':after,'diff':diff}
        with LOCK:
            current=load(config,info['session_id'])
            if current['content']!=before or [r['turn_id'] for r in current['records']]!=[r['turn_id'] for r in ctx['note']['records']]:
                raise ValueError('다른 실행 또는 편집이 심리상태를 변경했습니다. 덮어쓰기를 중단합니다.')
            if cancelled():raise Cancelled('Q2 상태 확정 전에 중단되었습니다.')
            from counselor.P1.runtime import write
            record_path=folder/'appraisals'/f"{record['turn']:06d}_{info['turn_id']}.json"
            write_new(record_path,record)
            try:write(path,after)
            except Exception:
                record_path.unlink(missing_ok=True);raise
        version('state_notes','updated',{'content':after,'before':before,'path':str(path),'diff':diff,'updates':updates,
                                        'states':values,'pending':False,'turn':record['turn'],'turn_id':info['turn_id']})
        version('appraisal','committed',staged['appraisal'])
    except Exception:
        version('state_notes','discarded',{'content':before,'path':str(path),'pending':False,'discarded':True})
        raise
    total={};sub={}
    for agent,usage in usages.items():
        for key,value in usage.items():
            total[key]=total.get(key,0)+value
            if agent!='main':sub[key]=sub.get(key,0)+value
    emit('done',response=utterance,history=ctx['messages']+[{'role':'assistant','content':utterance}],finish_reason='stop',
         usage=total,main_usage=usages['main'],subagent_usage=sub,agent_usage=usages,experiment_version='Q2')
