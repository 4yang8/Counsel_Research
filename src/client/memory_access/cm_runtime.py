"""cm variants: historical access is a single gate for records AND derived state.

All drafts and intermediate stages are local until the final atomic state write.
Stored history is retained in both conditions, never silently erased on a fork.
"""
import copy
import json
import time
from pathlib import Path

from client.Q1.runtime import parse_json
from client.Q3.tool_loop import run_turn
from counselor.P1.runtime import write
from runtime import reasoning_budget
from runtime.cancellation import Cancelled
from runtime.experiment_schema import obj, array, enum, TEXT, TURN, validate, wire
from .runtime import MemoryRuntime, STANCE_STAGE, COMMIT, context, source, load, path, LOCK, tool
from .memory import chunk, encode
from .inner_router import route, main_source

ROOT=Path(__file__).resolve().parent.parent
STAGES={
    'cm':(), 'cm_split':('reaction','expression'), 'cm_temporal':('temporal',),
    'cm_state':('state',), 'cm_tools':(), 'cm_review':('review',),
    'cm_full':('temporal','reaction','expression','review','state'),
}
STAGES.update({'Q3'+v:stages for v,stages in list(STAGES.items())})
PP=STANCE_STAGE['function']['parameters']['properties']['private_position']['properties']
REACTION=obj(appraisal=STANCE_STAGE['function']['parameters']['properties']['appraisal'],
             actual_wish=PP['actual_wish'], counselor_stance=PP['counselor_stance'])
EXPRESSION=obj(response_plan=STANCE_STAGE['function']['parameters']['properties']['response_plan'],
               turn_goal=STANCE_STAGE['function']['parameters']['properties']['turn_goal'],
               private_position=obj(**{k:v for k,v in PP.items() if k not in ('actual_wish','counselor_stance')}))
TEMPORAL=obj(experiences=array(obj(turn=TURN,then_experience=TEXT,current_context=TEXT,uncertainty=TEXT),3))
STATE=obj(updates=array(obj(origin_turn=TURN,current_meaning=TEXT,
                            disclosure=enum('withheld','partial','voiced','uncertain'),
                            evidence_turns=array(TURN,8),change_reason=TEXT),12))
REVIEW=obj(decision=enum('keep','revise'),issues=array(obj(problem=TEXT,correction=TEXT),6))
READ=tool('read_private_turns','과거 턴 번호로 당시 내면을 조회합니다. 최대 두 번, 중복을 제외한 총 3턴까지. 기록이 없으면 빈 목록입니다.',obj(turns=array(TURN,3)))


def bundle(version):
    return {s:(ROOT/version/(s+'.md')).read_text(encoding='utf-8') for s in STAGES[version]}


def memory_access(ctx, body):
    """Only this gate returns historical private information to a model stage."""
    provided=body['compare_memory_mode']=='provided'
    return (ctx['records'] if provided else [], ctx['saved'].get('event_state',[]) if provided else [])


def merge_state(old, updates, known_records, current_turn):
    result=copy.deepcopy(old);mapping={x['origin_turn']:x for x in result};seen=set()
    for u in updates:
        t=u['origin_turn']
        if t not in known_records or t in seen or not u['evidence_turns'] or any(n<1 or n>current_turn for n in u['evidence_turns']):
            raise ValueError('내담자 사건 갱신의 근거/대상 턴 오류')
        seen.add(t)
        if t not in mapping:
            original=known_records[t]['inner']
            e={'origin_turn':t,'original_reaction':copy.deepcopy(original.get('appraisal',{})),
               'original_position':copy.deepcopy(original.get('private_position',{}))}
            mapping[t]=e;result.append(e)
        # Original experience is immutable; only its current interpretation/status changes.
        mapping[t].update(copy.deepcopy(u),updated_turn=current_turn)
    return result


class CompareRuntime(MemoryRuntime):
    def __init__(self,version):
        super().__init__(version)
        self.kind=version[2:] if version.startswith('Q3cm') else version
        if self.kind=='cm_tools':self.TOOLS.append(copy.deepcopy(READ))
        if self.kind in ('cm_split','cm_full'):
            self.TOOLS.append(tool('choose_client_expression','이미 형성한 실제 반응을 바꾸지 않고 이번 표현을 결정합니다.',EXPRESSION))
        if self.kind in ('cm_review','cm_full'):
            self.TOOLS.append(tool('draft_client_utterance','검토할 공개 발화 초안을 만듭니다. 아직 공개/저장하지 않습니다.',COMMIT['function']['parameters']))

    def specification(self):
        spec=super().specification()
        spec['description']='compare memory · '+self.version+' · 과거 내면/파생 상태 접근만 제공·미제공으로 분기'
        agents=[{'kind':'trait_router','label':'특성·배경 라우터 (공개 정보만)'}]
        if self.kind!='cm_tools':agents.append({'kind':'memory_selector','label':'과거 내면 선별'})
        agents.extend({'kind':s,'label':'cm '+s} for s in STAGES[self.version])
        spec['subagent_prompts']=agents
        return spec

    def execute(self,config,payload,client,upstream,frames,inspect,emit,cancelled,original,base_build,mode,runtime):
        started=time.monotonic();body=config['_request_body'];options=body['memory_options']
        ctx=context(config,body,runtime)
        builder=reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets'))
        prompts=body['cm_prompts'];available,prior_events=memory_access(ctx,body)
        visible=[];requests=[];traces=[];usages={};selection={};retrievals=[]
        def event(section,action,data):emit('version_event',version=self.version,section=section,action=action,data=data)
        def size(req):
            if len(encode(req))>options['input_char_limit']:raise RuntimeError('cm 입력 길이 한도 초과. 자동 절삭하지 않았습니다.')
        def ask(stage,data,schema,as_tool=None,role='subagent',instruction=None):
            if cancelled():raise Cancelled('cm 단계 시작 전 중단')
            instruction=instruction if instruction is not None else prompts[stage]
            definitions=[tool(as_tool,instruction,schema)] if as_tool else []
            req=builder(config,{'system_prompt':instruction,'messages':[{'role':'user','content':encode(data)}],
                'generation':copy.deepcopy(body.get('generation',config.get('generation',{}))),
                '_reasoning_budget_role':role},definitions,False)
            if as_tool:
                req['tool_choice']={'type':'function','function':{'name':as_tool}};req['parallel_tool_calls']=False
            else:req['structured_outputs']={'json':wire(schema)}
            size(req);requests.append({'stage':stage,'payload':copy.deepcopy(req)})
            final={};captured={}
            class StageTool:
                opts={'max_calls':1,'max_result_chars':4000000};requires_finalize=True
                def call(self,name,args):
                    if name!=as_tool:raise ValueError('cm 단계 도구 이름 오류')
                    validate(args,schema);captured.update(copy.deepcopy(args))
                    return {'structuredContent':{'staged':True}}
                def terminal_response(self):return encode(captured) if captured else None
            def stage_event(kind,**values):
                if kind=='done':final.update(values)
                emit('agent_event',agent_id=stage,agent_name=self.version+' '+stage,agent_kind=stage,
                     parent_agent_id='main',depth=1 if role=='subagent' else 0,event={'type':kind,**values})
            stage_event('request',payload=copy.deepcopy(req),iteration=1)
            run_turn(config,req,StageTool() if as_tool else None,upstream,frames,inspect,stage_event,cancelled,[])
            if final.get('finish_reason')!='stop':raise ValueError('cm 단계 응답이 완료되지 않았습니다: '+stage)
            parsed=captured if as_tool else parse_json(final['response'])
            validate(parsed,schema);usages[stage]=final.get('usage',{})
            traces.append({'stage':stage,'input':copy.deepcopy(data),'output':copy.deepcopy(parsed)})
            return parsed

        event('memory_access','started',{'turn':ctx['turn'],'condition':self.version})
        try:
            _,req,usage=route(config,body,ctx,builder,upstream,frames,inspect,emit,cancelled,size)
            requests.append({'stage':'trait_router','payload':req});usages['trait_router']=usage
            if self.kind=='cm_tools':
                staged,speech,visible,retrievals,tool_trace,tool_requests,usage=self.tools_turn(
                    config,body,ctx,available,builder,upstream,frames,inspect,emit,cancelled,size)
                traces.extend(tool_trace);requests.extend(tool_requests);usages['main']=usage
            else:
                if available:
                    schema=obj(turns={'type':'array','items':{'type':'integer','enum':[r['turn'] for r in available]},'maxItems':3,'uniqueItems':True})
                    chosen=ask('selection',{**source(ctx,[chunk(r) for r in available]),'max_turns':3},schema,
                               instruction=body['memory_selector_prompt'])
                    visible=[chunk(r) for r in available if r['turn'] in chosen['turns']]
                    selection={'selected_turns':chosen['turns'],'source_count':len(available)}
                data=main_source(ctx,visible)
                if self.kind in ('cm_state','cm_full'):data['event_state']=copy.deepcopy(prior_events)
                if self.kind in ('cm_temporal','cm_full'):
                    temporal=ask('temporal',data,TEMPORAL)
                    if any(x['turn'] not in [r['turn'] for r in visible] for x in temporal['experiences']):
                        raise ValueError('제공되지 않은 과거 내면을 시간 구분 단계에서 확정했습니다.')
                    data['temporal_grounding']=temporal
                if self.kind in ('cm_split','cm_full'):
                    reaction=ask('reaction',data,REACTION)
                    expressed=ask('expression',{**data,'current_reaction':reaction},EXPRESSION,'choose_client_expression','main')
                    staged={'appraisal':reaction['appraisal'],'turn_goal':expressed['turn_goal'],'response_plan':expressed['response_plan'],
                            'private_position':{**expressed['private_position'],'actual_wish':reaction['actual_wish'],'counselor_stance':reaction['counselor_stance']}}
                else:
                    staged=ask('inner',data,STANCE_STAGE['function']['parameters'],'stage_client_update','main',body['system_prompt'])
                validate(staged,STANCE_STAGE['function']['parameters'])
                event('appraisal','staged',staged)
                draft_tool='draft_client_utterance' if self.kind in ('cm_review','cm_full') else 'commit_client_utterance'
                speech=ask('draft' if draft_tool.startswith('draft') else 'utterance',{**data,'current_inner':staged},COMMIT['function']['parameters'],draft_tool,'main',body['system_prompt'])['utterance'].strip()
                if self.kind in ('cm_review','cm_full'):
                    review=ask('review',{**data,'current_inner':staged,'draft':speech},REVIEW)
                    if review['decision']=='revise':
                        speech=ask('revision',{**data,'current_inner':staged,'draft':speech,'review':review},COMMIT['function']['parameters'],'commit_client_utterance','main',body['system_prompt'])['utterance'].strip()
            event('memory_access','injected',{'turn':ctx['turn'],'selected_turns':[r['turn'] for r in visible],'records':visible})
            record={'turn':ctx['turn'],'turn_id':config['_turn_info']['turn_id'],'inner':staged,'utterance':speech,
                    'counselor_utterance':ctx['public'][-1]['content'],'condition':self.version,'memory_mode':body['compare_memory_mode'],
                    'memory_input':copy.deepcopy(visible),'selection':selection,'retrievals':retrievals,'selected_traits':copy.deepcopy(ctx['selected_traits'])}
            events=copy.deepcopy(ctx['saved'].get('event_state',[]))
            if self.kind in ('cm_state','cm_full'):
                # Withheld gets no old record or derived-state contents, even at the updater.
                update_source={**main_source(ctx,visible),'previous_events':copy.deepcopy(prior_events),
                               'current_turn':ctx['turn'],'current_inner':staged,'current_utterance':speech}
                updates=ask('state',update_source,STATE)
                known={r['turn']:r for r in visible}
                known.update({r['turn']:r for r in available if r['turn'] in {e['origin_turn'] for e in prior_events}})
                known[ctx['turn']]=record
                events=merge_state(events,updates['updates'],known,ctx['turn'])
            total={};main_usage={};sub_usage={}
            main_stages={'expression','inner','draft','utterance','revision','main'}
            for stage,usage in usages.items():
                target=main_usage if stage in main_stages else sub_usage
                for k,v in usage.items():
                    if type(v) in (int,float):total[k]=total.get(k,0)+v;target[k]=target.get(k,0)+v
            record.update(model_requests=requests,agent_usage=usages,
                          state_input=copy.deepcopy(prior_events) if self.kind in ('cm_state','cm_full') else [],
                          pipeline={'version':self.version,'stages':traces,'llm_calls':len(requests)},
                          metrics={'latency_ms':round((time.monotonic()-started)*1000,2),'llm_usage':total,
                                   'injected_turns':[r['turn'] for r in visible],'search_calls':len(retrievals)})
            saved={'revision':ctx['saved']['revision']+1,'signature':ctx['signature'],'records':ctx['records']+[record],
                   'public_dialogue':ctx['public']+[{'role':'client','content':speech}], 'options':options,
                   'router_prompt':body['memory_router_prompt'],'selector_prompt':body['memory_selector_prompt'],
                   'cm_prompts':prompts,'event_state':events}
            with LOCK:
                sid=config['_turn_info']['session_id']
                if load(config,self.version,sid)!=ctx['saved']:raise ValueError('cm 상태가 다른 실행에 의해 변경됐습니다.')
                if cancelled():raise Cancelled('cm 상태 확정 전 중단')
                target=path(config,self.version,sid);target.parent.mkdir(parents=True,exist_ok=True)
                write(target,json.dumps(saved,ensure_ascii=False,indent=2))
            event('utterance','committed',{'content':speech,'pending':False})
            emit('done',response=speech,finish_reason='stop',response_source='committed_utterance',usage=total,
                 main_usage=main_usage,subagent_usage=sub_usage,agent_usage=usages,experiment_version=self.version,
                 history=[{'role':'user' if m['role']=='counselor' else 'assistant','content':m['content']} for m in saved['public_dialogue']])
        except Exception:
            event('memory_access','discarded',{'turn':ctx['turn'],'message':'cm 단계 실패: 이번 턴은 확정하지 않았습니다.'})
            raise

    def tools_turn(self,config,body,ctx,available,builder,upstream,frames,inspect,emit,cancelled,size):
        data=main_source(ctx,[]);visible=[];retrievals=[];requests=[];trace=[];staged={};speech={};final={};reads=0;requested=set()
        definitions=[copy.deepcopy(STANCE_STAGE),copy.deepcopy(COMMIT),copy.deepcopy(READ)]
        payload=builder(config,{'system_prompt':body['system_prompt'],'messages':[{'role':'user','content':encode(data)}],
            'generation':copy.deepcopy(body.get('generation',config.get('generation',{}))),'_reasoning_budget_role':'main'},definitions,False)
        lookup={r['turn']:r for r in available}
        class Tools:
            opts={'max_calls':4,'max_result_chars':4000000};requires_finalize=True
            def call(self,name,args):
                nonlocal reads
                if name not in names():raise ValueError('cm 도구 단계 오류')
                schema=next(t['function']['parameters'] for t in definitions if t['function']['name']==name)
                validate(args,schema)
                if name=='read_private_turns':
                    ids=args['turns']
                    if len(ids)!=len(set(ids)) or any(t>=ctx['turn'] for t in ids):raise ValueError('과거 턴만 중복 없이 조회하세요.')
                    union=set(ids)|requested
                    if len(union)>3:raise ValueError('이번 턴에서 조회 가능한 과거 기록은 총 3턴입니다.')
                    requested.update(ids)
                    found=[chunk(lookup[t]) for t in ids if t in lookup]
                    visible[:]=[chunk(lookup[t]) for t in sorted(union) if t in lookup]
                    reads+=1;result={'records':found,'remaining_calls':2-reads}
                    retrievals.append({'requested_turns':ids,'results':copy.deepcopy(found)})
                elif name=='stage_client_update':
                    staged.update(copy.deepcopy(args));result={'recorded':True,'next':'commit_client_utterance'}
                else:speech.update(copy.deepcopy(args));result={'accepted':True}
                trace.append({'type':'tool','name':name,'arguments':copy.deepcopy(args),'result':copy.deepcopy(result)})
                return {'structuredContent':result}
            def terminal_response(self):return speech.get('utterance')
        def names():
            if staged:return ['commit_client_utterance']
            return ['stage_client_update']+(['read_private_turns'] if reads<2 else [])
        def prepare(req,iteration):
            if iteration>4:raise ValueError('cm 조회/내면/발화 호출 한도 초과')
            allowed=names();req['tools']=[d for d in definitions if d['function']['name'] in allowed]
            req['tool_choice']={'type':'function','function':{'name':allowed[0]}} if len(allowed)==1 else 'required'
            req['parallel_tool_calls']=False;size(req)
            requests.append({'stage':'main','payload':copy.deepcopy(req)})
            if iteration==1:emit('request',payload=copy.deepcopy(req),iteration=iteration,phase='cm 기억 조회 도구')
        def event(kind,**values):
            if kind=='done':final.update(values)
            else:emit(kind,**values)
        run_turn({**config,'_prepare_payload':prepare},payload,Tools(),upstream,frames,inspect,event,cancelled,[])
        if not staged or not speech:raise ValueError('cm 조회 버전의 내면/발화가 완료되지 않았습니다.')
        return staged,speech['utterance'].strip(),visible,retrievals,trace,requests,final.get('usage',{})
