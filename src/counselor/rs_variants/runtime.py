"""Public-only counselor experiments, staged locally and committed by paired_lab.

Every stage has a persisted input/output trace. Only the final utterance is public.
No action is forced at a prescribed turn; the common first-check rule is a prompt.
"""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

from client.Q3.tool_loop import run_turn
from counselor.P3q.runtime import numbered
from counselor.P3rs.runtime import SCHEMA, MOVES, KINDS, INTERVENTIONS, asked, notes, record, call
from counselor.P3rs_sub.runtime import OBSERVER_SCHEMA, MAIN_SCHEMA, advice_text, followed
from runtime.experiment_schema import obj, array, enum, TEXT, TURN, validate, wire
from runtime.cancellation import Cancelled
from . import VERSIONS

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    'P3rs_sub_gate': '관찰 권고 · continue 조언 차단',
    'P3rs_observe': '관찰만 분리 · 메인이 행동 결정',
    'P3rs_ledger': '사건별 공개 반응 상태 관리',
    'P3rs_tools': '자율 조회·검색·메모 툴',
    'P3rs_dual': '개입 후보 / 확인 후보 병렬 제안',
    'P3rs_hypothesis': '공개 반응의 대안 해석 유지',
    'P3rs_review': '발화 초안 검토 · 최대 한 번 수정',
    'P3rs_full': '사건 관리 + 병렬 후보 + 발화 검토',
}
STAGES = {
    'P3rs_sub_gate': ('observer',), 'P3rs_observe': ('observe',),
    'P3rs_ledger': ('ledger',), 'P3rs_tools': (),
    'P3rs_dual': ('intervention', 'checking'), 'P3rs_hypothesis': ('hypothesis',),
    'P3rs_review': ('review',),
    'P3rs_full': ('ledger', 'intervention', 'checking', 'review'),
}
EVIDENCE = obj(turn=TURN, role=enum('counselor', 'client'), quote=TEXT)
OBSERVE = obj(observations=array(obj(description=TEXT, evidence=array(EVIDENCE, 8)), 8), uncertainty=TEXT)
UPDATE = obj(event_turn=TURN, status=enum('unconfirmed','asked','answered','deferred'),
             public_response=TEXT, unresolved=TEXT, evidence=array(EVIDENCE, 8))
LEDGER = obj(updates=array(UPDATE, 20))
HYPOTHESIS = obj(hypotheses=array(obj(event_turn=TURN, alternatives=array(
    obj(interpretation=TEXT, support=array(EVIDENCE, 6), against=array(EVIDENCE, 6)), 4), unknown=TEXT), 12))
CANDIDATE = obj(action=enum('none','suggest','interpret','task','check_in','rupture_explore'),
                target_turn={'type':'integer','minimum':0}, proposal=TEXT, reason=TEXT, evidence=array(EVIDENCE, 8))
REVIEW = obj(decision=enum('keep','revise'), issues=array(obj(problem=TEXT, correction=TEXT), 6))
EMPTY_MARKER = {'found':False,'kind':'none','quote':''}


def bundle(version):
    return {p.stem:p.read_text(encoding='utf-8') for p in sorted((ROOT/version).glob('*.md')) if p.name!='README.md'}


def freeze(request):
    version = request.get('counselor_version')
    if version in VERSIONS:
        if 'rs_prompt_bundle' not in request:
            request['rs_prompt_bundle'] = bundle(version)
        required = {'system','user',*STAGES[version]} | ({'guide'} if version=='P3rs_sub_gate' else set())
        if not isinstance(request['rs_prompt_bundle'],dict) or not required <= set(request['rs_prompt_bundle']):
            raise ValueError('상담사 단계 프롬프트 묶음이 누락됐습니다.')
        if any(not isinstance(v,str) or not v.strip() for v in request['rs_prompt_bundle'].values()):
            raise ValueError('상담사 단계 프롬프트가 비어 있습니다.')


def evidence_ok(items, public):
    for e in items:
        rows = [m['content'] for m in public if m['role']==e['role']]
        if not 1 <= e['turn'] <= len(rows) or not e['quote'].strip() or e['quote'] not in rows[e['turn']-1]:
            raise ValueError('관찰 근거의 턴 또는 인용이 공개 대화와 다릅니다.')


def update_ledger(events, updates, public):
    result = copy.deepcopy(events)
    by_turn = {e['event_turn']:e for e in result}
    seen = set()
    for u in updates:
        t=u['event_turn']
        if t not in by_turn or t in seen:
            raise ValueError('사건 갱신 대상이 없거나 중복됐습니다.')
        seen.add(t)
        evidence_ok(u['evidence'],public)
        if not u['evidence']:
            raise ValueError('사건 변경에는 공개 근거가 필요합니다.')
        # The original counselor utterance is immutable.
        by_turn[t].update(copy.deepcopy(u), updated_after_client_turn=sum(m['role']=='client' for m in public))
    return result


def commit_ledger(events, parsed, counselor_turn):
    result=copy.deepcopy(events)
    target=parsed['check_turn'] if parsed['move']=='check_in' else parsed['rupture_turn'] if parsed['move']=='rupture_explore' else 0
    for e in result:
        if e['event_turn']==target:
            e['status']='asked'  # asking is not receiving an answer or resolving the issue
            e.setdefault('checks',[]).append(counselor_turn)
    if parsed['move'] in INTERVENTIONS:
        result.append({'event_turn':counselor_turn,'move':parsed['move'],'utterance':parsed['utterance'],
                       'status':'unconfirmed','public_response':'','unresolved':'이 말이 어떻게 받아들여졌는지',
                       'evidence':[],'checks':[]})
    return result


def validate_speech(parsed, public):
    validate(parsed, SCHEMA)
    latest=sum(m['role']=='client' for m in public)
    for key, move in [('check_turn','check_in'),('rupture_turn','rupture_explore')]:
        if parsed['move']==move:
            if not 1 <= parsed[key] <= latest: raise ValueError('확인 대상이 현재 또는 미래 턴입니다.')
        elif parsed[key]!=0: raise ValueError('선택하지 않은 행동의 대상은 0이어야 합니다.')
    mark=parsed['marker']
    if mark['found']:
        if not latest or mark['kind']=='none':raise ValueError('표지 종류를 확인하세요.')
        evidence_ok([{'turn':latest,'role':'client','quote':mark['quote']}],public)
    return parsed


class Variant:
    def __init__(self, version):
        self.version=version
        self.runtime=self  # prompt editor's existing module interface
        self.SUBAGENT_PROMPTS={s:{'label':s,'filename':s+'.md'} for s in STAGES[version]}

    def specification(self):
        return {'id':self.version,'label':self.version+' · '+LABELS[self.version], 'view':'none',
                'description':LABELS[self.version]+' · 공개 정보만 사용; 원본 단계 기록 저장.',
                'system_prompt':(ROOT/self.version/'system.md').read_text(encoding='utf-8'),
                'subagent_prompts':[{'kind':s,'label':d['label']} for s,d in self.SUBAGENT_PROMPTS.items()]}

    def run(self, config, session, backend, base_build, emit, cancelled, directory):
        version=self.version
        public=copy.deepcopy(session['public_dialogue'])
        settings=copy.deepcopy(session['settings']);freeze(settings)
        prompts=settings['rs_prompt_bundle']
        previous=session.get('counselor_checkpoint') or {}
        if previous and previous.get('version')!=version:
            raise ValueError('다른 상담사 버전의 상태로 계속할 수 없습니다.')
        records=copy.deepcopy(previous.get('records',[]))
        pending=copy.deepcopy(previous.get('pending',[]));unchecked=copy.deepcopy(previous.get('unchecked',[]))
        ledger=copy.deepcopy(previous.get('events',[]))
        hypotheses=copy.deepcopy(previous.get('hypotheses',[]))
        traces=[];usages={};event_lock=threading.RLock()
        def send(kind,**data):
            with event_lock:emit(kind,**data)
        base={'public_dialogue':public,'numbered_dialogue':numbered(public),'checks_so_far':asked(records)}
        memo={'notes':notes(pending,unchecked,asked(records))}

        def ask(stage, data, schema, main=False):
            if cancelled():raise Cancelled('상담사 단계 시작 전 중단')
            system=settings['counselor_system_prompt'] if main else prompts[stage]
            # user templates are frozen along with the role prompts.
            user=prompts['user'].replace('{{input}}',json.dumps(data,ensure_ascii=False))
            stage_id=stage
            def event(kind,**values):
                send('agent_event',agent_id=stage_id,agent_name=version+' '+stage,
                     agent_kind=stage,parent_agent_id=None if main else 'main',depth=0 if main else 1,
                     event={'type':'done' if kind=='model_result' else kind,**values})
            parsed,usage=call(config,backend,base_build,event,cancelled,system,user,wire(schema),stage,label=version,
                               body={'_counselor_budget_role':'main' if main else 'subagent'})
            validate(parsed,schema)
            with event_lock:
                usages[stage]=usage
                traces.append({'stage':stage,'input':copy.deepcopy(data),'output':copy.deepcopy(parsed)})
            return parsed

        observed=None
        if version=='P3rs_sub_gate':
            observed=ask('observer',{**base,**memo,'guide':prompts['guide']},OBSERVER_SCHEMA)
            if observed['marker']['found']:
                evidence_ok([{'turn':sum(m['role']=='client' for m in public),'role':'client','quote':observed['marker']['quote']}],public)
            shown=advice_text(observed) if observed['recommend']!='continue' else '권하는 행동: 지금 상담을 이어 가기. 상담 방향은 메인이 결정합니다.'
            parsed=ask('main',{**base,'observer_guidance':shown},MAIN_SCHEMA,True)
            parsed['marker']=observed['marker']
        elif version=='P3rs_tools':
            parsed,ledger,tool_trace,tool_usage=self.tools_turn(config,settings,base,prompts,ledger,public,backend,base_build,send,cancelled)
            traces.extend(tool_trace);usages['main_tools']=tool_usage
        else:
            data={**base,**memo}
            if version=='P3rs_observe':
                observations=ask('observe',{**base,**memo},OBSERVE)
                for o in observations['observations']:evidence_ok(o['evidence'],public)
                # No action recommendation, advice or private records enter this handoff.
                data={**base,'observations':observations}
            if version in ('P3rs_ledger','P3rs_full'):
                updates=ask('ledger',{**base,'events':ledger},LEDGER)
                ledger=update_ledger(ledger,updates['updates'],public)
                data={**base,'events':ledger}
            if version=='P3rs_hypothesis':
                result=ask('hypothesis',{**base,'events':ledger,'previous_hypotheses':hypotheses},HYPOTHESIS)
                known={e['event_turn'] for e in ledger};seen=set()
                for h in result['hypotheses']:
                    if h['event_turn'] not in known or h['event_turn'] in seen:raise ValueError('대안 해석 대상 오류')
                    seen.add(h['event_turn'])
                    for a in h['alternatives']:evidence_ok(a['support']+a['against'],public)
                hypotheses=result['hypotheses']
                data={**base,'events':ledger,'hypotheses':hypotheses}
            if version in ('P3rs_dual','P3rs_full'):
                candidate_input=copy.deepcopy(data)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures={s:pool.submit(copy_context().run,ask,s,candidate_input,CANDIDATE) for s in ('intervention','checking')}
                    candidates={s:f.result() for s,f in futures.items()}
                for stage,c in candidates.items():
                    evidence_ok(c['evidence'],public)
                    allowed=('none','suggest','interpret','task') if stage=='intervention' else ('none','check_in','rupture_explore')
                    if c['action'] not in allowed:raise ValueError('담당 범위를 벗어난 후보 행동')
                    if stage=='checking' and c['action']!='none' and not 1<=c['target_turn']<=sum(m['role']=='client' for m in public):raise ValueError('확인 후보의 대상 턴 오류')
                data={**data,'candidates':candidates}
            parsed=ask('draft' if version in ('P3rs_review','P3rs_full') else 'main',data,SCHEMA,True)
            validate_speech(parsed,public)
            if version in ('P3rs_review','P3rs_full'):
                recent=[{k:r[k] for k in ('counselor_turn','move','check','explore') if k in r} for r in records[-3:]]
                review=ask('review',{**base,'draft':parsed,'events':ledger,'recent_actions':recent},REVIEW)
                if review['decision']=='revise':
                    parsed=ask('revision',{**data,'draft':parsed,'review':review},SCHEMA,True)
        validate_speech(parsed,public)
        row,pending,unchecked=record(parsed,public,pending,unchecked)
        if observed:
            row['observer']={**observed,'followed':followed(observed,parsed),'shown':shown}
        ledger=commit_ledger(ledger,parsed,row['counselor_turn'])
        row['pipeline']={'version':version,'stages':traces,'llm_calls':sum(1 for t in traces if t.get('type')!='tool'),
                         'tool_calls':sum(t.get('type')=='tool' for t in traces),'agent_usage':usages}
        checkpoint={'version':version,'records':records+[row],'pending':pending,'unchecked':unchecked,
                    'events':ledger,'hypotheses':hypotheses,'prompt_bundle':prompts}
        total={}
        for usage in usages.values():
            for k,v in usage.items():
                if type(v) in (int,float):total[k]=total.get(k,0)+v
        send('version_event',version=version,section='rupture',action='recorded',data=row)
        return parsed['utterance'].strip(),checkpoint,total

    def tools_turn(self,config,settings,base,prompts,ledger,public,backend,builder,emit,cancelled):
        schemas={
            'list_open_events':obj(),
            'read_public_turns':obj(turns=array(TURN,6)),
            'update_event':UPDATE,
            'finish_counselor_utterance':SCHEMA,
        }
        descriptions={'list_open_events':'아직 확인·정리되지 않은 자기 제안·해석 사건을 읽습니다.',
                      'read_public_turns':'지정한 턴의 공개 대화만 읽습니다.',
                      'update_event':'공개 근거로 사건 하나를 갱신합니다. 이번 턴 최대 한 번. 발화 확정 시에만 저장됩니다.',
                      'finish_counselor_utterance':'관찰·행동·대상과 최종 상담사 발화를 확정합니다.'}
        definitions=[{'type':'function','function':{'name':n,'description':descriptions[n],'parameters':s}} for n,s in schemas.items()]
        output={};trace=[];done={};working=copy.deepcopy(ledger);used=[]
        user={**base,'open_event_count':sum(e['status'] not in ('answered',) for e in ledger),'tool_budget':3}
        payload=builder(config,{'system_prompt':settings['counselor_system_prompt'],
            'messages':[{'role':'user','content':prompts['user'].replace('{{input}}',json.dumps(user,ensure_ascii=False))}],
            'generation':copy.deepcopy(config['generation']),'_counselor_budget_role':'main'},definitions,False)
        class Tools:
            opts={'max_calls':4,'max_result_chars':4000000};requires_finalize=True
            def call(self,name,args):
                nonlocal working
                if name not in names():raise ValueError('현재 허용되지 않는 상담사 도구')
                validate(args,schemas[name])
                if name=='list_open_events':result={'events':[e for e in working if e['status']!='answered']}
                elif name=='read_public_turns':
                    rows=[]
                    for t in args['turns']:
                        selected=public[2*(t-1):2*t]
                        if not selected:raise ValueError('존재하지 않는 공개 턴')
                        rows.append({'turn':t,'messages':selected})
                    result={'turns':rows}
                elif name=='update_event':
                    working=update_ledger(working,[args],public);result={'staged':True}
                else:
                    validate_speech(args,public);output.update(copy.deepcopy(args));result={'accepted':True}
                used.append(name);trace.append({'type':'tool','name':name,'arguments':copy.deepcopy(args),'result':copy.deepcopy(result)})
                return {'structuredContent':result}
            def terminal_response(self):return output.get('utterance')
        def names():
            if len(used)>=3:return ['finish_counselor_utterance']
            return [n for n in schemas if n!='update_event' or n not in used]
        def prepare(req,iteration):
            if iteration>4:raise ValueError('상담사 도구 호출 한도 초과')
            allowed=names();req['tools']=[d for d in definitions if d['function']['name'] in allowed]
            req['tool_choice']={'type':'function','function':{'name':allowed[0]}} if len(allowed)==1 else 'required'
            req['parallel_tool_calls']=False
            trace.append({'stage':'main_tools','input':copy.deepcopy(req)})
            if iteration==1:emit('request',payload=copy.deepcopy(req),phase='자율 메모 도구',iteration=iteration)
        def event(kind,**data):
            if kind=='done':done.update(data)
            else:emit(kind,**data)
        run_turn({**config,'_prepare_payload':prepare},payload,Tools(),backend.upstream,backend.stream_events,backend.inspect_prompt,event,cancelled,[])
        if not output:raise ValueError('상담사 발화가 확정되지 않았습니다.')
        return output,working,trace,done.get('usage',{})


RUNTIMES={v:Variant(v) for v in VERSIONS}
