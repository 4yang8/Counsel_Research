"""Shared Q3 decision pipeline with raw, summary and deterministic recording adapters."""
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
from client.Q3.tool_loop import run_turn, validate_messages
from client.Q1.runtime import split_profile, parse_json, APPRAISAL_FIELDS
from client.Q3 import runtime as q3
from client.Q3.profile_context import background_candidates, routing_schema, resolve_selection
from client.Q3.states import STATES, INNER, parse_note
from client.Q3 import note_items
from client.recording_experiments import memory
from client.Q3 import trajectory
from client.Q3.context_view import manifest

from client.recording_experiments import recording
LOCK = threading.Lock()
APPRAISAL_SCHEMA = copy.deepcopy(q3.APPRAISAL_SCHEMA)


def specification():
    return {'id':VERSION,'label':OPTIONS['label'],'condition':OPTIONS['condition'],'view':'client_states',
            'description':'Q3 기반 기록 실험. 진행 판단은 내면 판단 단계에서 생성하고, 발화 확정 후 '+OPTIONS['label']+'으로 저장합니다.',
            'system_prompt':(HERE/'system.md').read_text(encoding='utf-8'),'summary_system_prompt':'',
            'required_tools':[tool['function']['name'] for tool in TOOLS],'default_reasoning_budgets':{'main':None,'subagent':None},
            'subagent_prompts':[{'kind':'trait_router','label':'특성·배경·노트 라우터'}]+([{'kind':'note_summarizer','label':'기록 요약 서브에이전트'}] if MODE=='summary' else []),
            'tools':copy.deepcopy(TOOLS)}


def directory(config,session):
    if not isinstance(session,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',session):
        raise ValueError('올바르지 않은 내담자 실험 session_id입니다.')
    return Path(config['_storage_dir'])/VERSION/session


def load(config,session):
    folder=directory(config,session) if session else None
    path=folder/'state_notes.md' if folder else None
    records=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((folder/'appraisals').glob('*.json'))] if folder else []
    with (path if path and path.exists() else HERE/'state_notes_template.md').open(encoding='utf-8',newline='') as stream:
        content=stream.read()
    items=copy.deepcopy(records[-1]['note_items_after']) if records and content==records[-1]['notes_after'] else note_items.read_items(content)
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
    if not isinstance(args,dict) or set(args)!={'appraisal','response_plan','turn_goal','trajectory_plan','trajectory_assessment'}:
        raise ValueError('appraisal, response_plan, turn_goal, trajectory_plan, trajectory_assessment를 기록하세요.')
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
    trajectory.validate(args['trajectory_assessment'],trajectory.PROGRESS)
    return copy.deepcopy(args)


def function_tool(name, description, schema):
    return {'type':'function','function':{'name':name,'description':description,'parameters':schema}}


# Reuse Q3 definitions without changing the baseline or any version's mutable tools.
TOOLS = [copy.deepcopy(t) for t in q3.TOOLS
         if t['function']['name'] in ('stage_client_update','commit_client_utterance','retrieve_client_memory')]

# Progress is a committed decision made before speech, never a post-speech inference.
assessment=copy.deepcopy(trajectory.PROGRESS)
assessment['properties']['position']['description']='이번 상담사 말을 받아들인 현재 마음의 궤적상 위치. 앞으로의 계획과 구분합니다.'
assessment['properties']['movement']['description']='이전 기록 대비 현재 마음의 유지·진행·후퇴. 현재 상담사 발화까지의 경험과 appraisal을 근거로 판단합니다.'
assessment['properties']['current_experience']['description']='현재 주관적 신뢰·경계·감정. 아직 생성하지 않은 자기 발화나 이후 상대 반응의 결과는 포함하지 않습니다.'
assessment['properties']['grounds']['description']='현재 판단의 근거와 의미. 이번 입력·appraisal과 이전 상태의 어떤 차이가 현재 마음을 뒷받침하는지.'
assessment['properties']['next_condition']['description']='아직 남아 있는 불확실성·보류 조건. 없으면 빈 문자열.'
TOOLS[0]['function']['parameters']['properties']['trajectory_assessment']=assessment
TOOLS[0]['function']['parameters']['required'].append('trajectory_assessment')
TOOLS[0]['function']['description']='상담사 말을 받아들인 appraisal과 현재 진행 판단(trajectory_assessment), 앞으로 표현할 방향(trajectory_plan), turn_goal, response_plan을 구분해 잠정 기록합니다. 필요한 과거 근거를 조회하고 수정한 뒤 발화를 확정합니다.'
if MODE=='raw':
    TOOLS.append(function_tool('select_client_note_sources',
        '발화 확정 후 제공되는 출처 목록에서 다음 반응에 필요한 주요 원문의 ID와 줄 범위만 선택합니다. Python이 원문을 그대로 복사합니다. 확정 판단과 탐색 reasoning·실제 발화·이전 노트를 구분하고 필요한 불확실성·보류 조건도 선택하세요. 전체를 누적하지 않고 현재 선택본으로 교체하며 이력은 보존됩니다. 궤적 판단은 stage의 원문 전체를 자동 보존합니다.',copy.deepcopy(recording.SELECT_SCHEMA)))
TOOL = TOOLS[0]  # Singular-tool compatibility used by the common definitions API.


def selected_note(items,ids):
    selected=set(ids)
    visible={key:[r for r in rows if r['id'] in selected] for key,rows in items.items()}
    text=note_items.render_items(visible)
    return text if any(visible.values()) else text.replace('(내용 없음)','(선택된 항목 없음)')


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
    router_request={};subagent_requests={};subagent_outputs={}
    def subagent(kind,label,user,schema):
        if cancelled():raise Cancelled('내담자 실험 상태 처리 전에 중단되었습니다.')
        role=(HERE/(kind+'.md')).read_text(encoding='utf-8')
        req=build_request(config,{'system_prompt':role,'messages':[{'role':'user','content':user}],
                                 '_reasoning_budget_role':'subagent'},[],False)
        req['structured_outputs']={'json':schema}
        subagent_requests[kind]=copy.deepcopy(req)
        if kind=='trait_router':router_request.update(copy.deepcopy(req))
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
        subagent_outputs[kind]=copy.deepcopy(parsed)
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
    sequence=['stage_client_update','commit_client_utterance']+(['select_client_note_sources'] if MODE=='raw' else [])
    pairs=[];bundle={};selections=[];summary_output={}
    retrievals=[];queries=set();decision={};precommit_calls=0;progress={} 
    max_precommit=8
    max_calls=20 if OPTIONS['retrieve'] else config.get('delegation',{}).get('max_calls',8)
    request_log=[]
    class Tools:
        opts={'max_calls':max_calls,'max_result_chars':24000}
        requires_finalize=True
        def call(self,name,arguments):
            nonlocal phase,routes,updates,inner_updates,operations,patched_items,precommit_calls,bundle,selections
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
                version('trajectory_assessment','staged',change['trajectory_assessment'])
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
                speech['utterance']=arguments['utterance']
                version('utterance','fixed',{'content':speech['utterance'],'pending':True})
                result={'utterance':speech['utterance'],'fixed':True,'saved':False,'next':'select_client_note_sources' if MODE=='raw' else 'runtime_recording','instruction':'공개 발화는 고정되었습니다. 진행 판단은 마지막 stage의 trajectory_assessment를 보존합니다.'}
            elif name=='select_client_note_sources':
                selections=recording.select(bundle,arguments)
                patched_items=recording.raw_items(selections)
                result={'accepted':True,'selected_count':len(selections),'finished':True,'saved':False}
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
        nonlocal bundle
        if kind=='tool_start' and data['name'] in ('stage_client_update','commit_client_utterance'):
            pairs.append({'call_id':data['call_id'],'iteration':data['iteration'],'name':data['name'],
                          'reasoning':data['assistant_message'].get('reasoning',''),
                          'assistant_message':copy.deepcopy(data['assistant_message']),
                          'arguments_raw':data['arguments'],'accepted':False})
        if kind=='tool_result' and data['name'] in ('stage_client_update','commit_client_utterance'):
            pair=next(p for p in reversed(pairs) if p['call_id']==data['call_id'])
            pair['accepted']=not data['is_error']
            pair['result']=copy.deepcopy(data['result'])
            if pair['accepted']:pair['output']=json.loads(pair['arguments_raw'])
            if pair['accepted'] and data['name']=='commit_client_utterance':
                bundle=recording.source_bundle(pairs,staged,speech['utterance'],ctx['note']['items'],len(ctx['note']['records'])+1,
                    ctx['note']['records'][-1] if ctx['note']['records'] else None)
                version('recording_sources','collected',copy.deepcopy(bundle))
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
            if phase>=2 and MODE=='raw':
                # Refresh only the catalog message; prior reasoning/tool transcript stays intact.
                heading='# 기록할 원문 출처 · 줄 번호\n\n'
                payload['messages'][:]=[m for m in payload['messages'] if not (m['role']=='user' and isinstance(m.get('content'),str) and m['content'].startswith(heading))]
                payload['messages'].append({'role':'user','content':heading+json.dumps(recording.numbered_sources(bundle),ensure_ascii=False)})
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
        if cancelled():raise Cancelled('내담자 기록 생성 전에 중단되었습니다.')
        progress=copy.deepcopy(staged['trajectory_assessment'])
        if MODE=='summary':
            summary_user=user_templates.render('client',VERSION,'note_summarizer_user',
                source_bundle=json.dumps(bundle,ensure_ascii=False,indent=2),previous_notes=ctx['note']['prompt_content'])
            summary_output=subagent('note_summarizer','기록 요약 서브에이전트',summary_user,recording.SUMMARY_SCHEMA)
            patched_items=recording.summary_items(summary_output)
            progress.update(copy.deepcopy(summary_output['trajectory']))
        elif MODE=='snapshot':
            patched_items=recording.snapshot_items(staged,speech['utterance'])
        operations=recording.operations(ctx['note']['items'],patched_items)
        recording_data={'mode':MODE,'selections':selections,'summary_output':summary_output,
                        'trajectory_assessment':copy.deepcopy(staged['trajectory_assessment']),
                        'trajectory_progress':copy.deepcopy(progress),'actual_utterance':speech['utterance']}
        version('recording','prepared',{**copy.deepcopy(recording_data),'pending':True})
        after=note_items.render_items(patched_items)
        parsed=parse_note(after);values=parsed['states'];inner=parsed['inner']
        diff=list(difflib.unified_diff(before.splitlines(),after.splitlines(),fromfile='이전 상태',tofile='다음 턴 상태',lineterm=''))
        record={'turn':len(ctx['note']['records'])+1,'turn_id':info['turn_id'],'scenario_hash':fingerprint(ctx['scenario']),
                'selected_traits':selected,'counselor_utterance':ctx['current'],'appraisal':staged['appraisal'],
                'response_plan':staged['response_plan'],'judgment_history':judgments,'utterance':utterance,
                'trajectory_source':ctx['note']['trajectory'],'trajectory_plan':staged['trajectory_plan'],
                'trajectory_before':ctx['note']['trajectory_progress'],'trajectory_progress':copy.deepcopy(progress),
                'trajectory_assessment':copy.deepcopy(staged['trajectory_assessment']),
                'trajectory_assessment_phase':'stage_client_update','trajectory_actual_utterance':utterance,
                'recording_mode':MODE,'recording_sources':bundle,'raw_selections':selections,
                'summary_output':summary_output,'note_items_after':copy.deepcopy(patched_items),
                'subagent_requests':subagent_requests,'subagent_outputs':subagent_outputs,
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
        version('recording','updated',{**copy.deepcopy(recording_data),'pending':False})
        version('appraisal','committed',staged['appraisal'])
        version('utterance','committed',{'content':utterance,'pending':False})
    except Exception:
        version('recording','discarded',{'mode':MODE,'pending':False,'discarded':True})
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
