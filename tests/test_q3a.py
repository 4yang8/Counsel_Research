"""Q3a plan execution, replanning, context isolation and atomic completion."""
import copy
import json
import unittest
import test_memory_experiments as base
from test_memory_experiments import Model, action, commit, post
from test_f_free_notes import query, op
from test_q3 import plan as judgment, progress
from client.Q3a import turn_plan
import client.registry as registry


def change(*ops,goal='',reason='이번 발화에 필요한 판단 결과를 확인한다.'):
    return action('update_turn_plan',goal=goal,reason=reason,operations=list(ops))

def add(sid='s1',task='이번 상담사 제안과 내면의 경계를 비교한다.'):
    return {'action':'add','step_id':sid,'task':task,'completion_criterion':'무엇을 받아들이고 어떤 공개 범위를 유지할지 근거를 설명할 수 있다.'}

def start(sid='s1'):return {'action':'start','step_id':sid}
def complete(sid='s1',result='안도하지만 세부 내용은 조심스러워한다.'):
    return {'action':'complete','step_id':sid,'result':result,'sources':['이번 상담사 발화: 말할 속도를 존중함','내담자 노트: 평가에 대한 경계']}
def prefix(marker='LOCAL_PLAN_TURN_ONE'):
    return [change(add(),goal=marker),change(start()),change(complete())]
def finish():return [judgment(),commit(True),action('update_client_notes',operations=[]),progress()]


class PlanUnitTests(unittest.TestCase):
    def test_atomic_validation_and_lifecycle(self):
        p=turn_plan.apply({},change(add(),goal='반응을 정한다')['arguments'])
        self.assertFalse(turn_plan.ready(p));old=copy.deepcopy(p)
        with self.assertRaises(ValueError):turn_plan.apply(p,change(complete())['arguments'])
        self.assertEqual(p,old)
        p=turn_plan.apply(p,change(start())['arguments'])
        with self.assertRaises(ValueError):turn_plan.apply(p,change({'action':'complete','step_id':'s1','result':'완료','sources':[]})['arguments'])
        p=turn_plan.apply(p,change(complete())['arguments']);self.assertTrue(turn_plan.ready(p))
        p=turn_plan.apply(p,change({**add(),'action':'revise'})['arguments']);self.assertFalse(turn_plan.ready(p))
        self.assertEqual(p['steps'][0]['result'],'')
        with self.assertRaises(ValueError):turn_plan.apply({},change(add(),complete('s2'),goal='bad')['arguments'])
        with self.assertRaises(ValueError):turn_plan.apply(p,change({'action':'skip','step_id':'s1','result':'불필요'})['arguments'])

    def test_goal_change_and_one_active_step(self):
        p=turn_plan.apply({},change(add(),add('s2'),goal='목표')['arguments'])
        with self.assertRaises(ValueError):turn_plan.apply(p,change(start(),start('s2'))['arguments'])
        p=turn_plan.apply(p,change(start())['arguments'])
        with self.assertRaises(ValueError):turn_plan.apply(p,change(complete(),goal='다른 목표')['arguments'])
        p=turn_plan.apply(p,change(complete(),start('s2'))['arguments'])
        self.assertEqual(p['steps'][1]['status'],'in_progress')


class Q3aTests(unittest.TestCase):
    setUp=base.MemoryExperiments.setUp
    tearDown=base.MemoryExperiments.tearDown
    get=base.MemoryExperiments.get
    create=base.MemoryExperiments.create
    step=base.MemoryExperiments.step
    notes=base.MemoryExperiments.notes

    def prepare(self):
        Model.q1_router_response='{"selections":[],"note_ids":[]}'
        return self.create('Q3a')

    def test_execute_lookup_replan_and_next_turn_isolation(self):
        s=self.prepare()
        Model.q2a_actions=[change(add(),add('s2','이전 변화의 이유를 확인한다'),goal='LOCAL_PLAN_TURN_ONE'),change(start()),
            action('retrieve_client_memory',**query('episodes')),
            change(complete(),{'action':'skip','step_id':'s2','result':'저장된 과거 턴이 없어 현재 자료로 판단한다.'},add('s3','부족한 근거를 유보한 채 표현 범위를 정한다.'),reason='조회 결과 기록이 없어 판단 범위를 조정한다.'),
            change(start('s3')),change(complete('s3','현재 드러난 마음까지만 이야기한다.')), *finish()]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        results=[e for e in events if e['type']=='tool_result' and not e.get('is_error')]
        self.assertTrue(results)
        for event in results:
            # UI/journal retains both representations; only the wire content is deduplicated.
            self.assertIn('content',event['result'])
            wire=json.loads(event['model_content'])
            self.assertEqual(wire['structuredContent'],event['result']['structuredContent'])
            self.assertNotIn('content',wire)
        requests=[e['payload'] for e in events if e['type']=='request']
        self.assertTrue(any(m['role']=='tool' for m in requests[-1]['messages']))
        for request in requests:
            for message in request['messages']:
                if message['role']=='tool':self.assertNotIn('content',json.loads(message['content']))
        rec=self.notes(s)['records'][0]
        self.assertEqual([x['status'] for x in rec['local_plan']['steps']],['completed','skipped','completed'])
        self.assertEqual(len(rec['local_plan_history']),5)
        history={h['revision']:h['plan'] for h in rec['local_plan_history']}
        plan_results=[e for e in events if e['type']=='tool_result' and e.get('name')=='update_turn_plan' and not e.get('is_error')]
        self.assertEqual(len(plan_results),len(history))
        for event in plan_results:
            receipt=json.loads(event['model_content'])['structuredContent']
            self.assertEqual(set(receipt),{'accepted','revision','steps','ready_for_judgment','remaining_plan_updates'})
            full=history[receipt['revision']]
            self.assertEqual(receipt['steps'],{step['id']:step['status'] for step in full['steps']})
            self.assertEqual(receipt['ready_for_judgment'],turn_plan.ready(full))
            self.assertEqual(receipt['remaining_plan_updates'],turn_plan.MAX_UPDATES-full['revision'])
            self.assertNotIn('LOCAL_PLAN_TURN_ONE',event['model_content'])
        # Full tasks, results and sources remain available to UI/history and in
        # assistant tool-call arguments even though tool acknowledgements are short.
        self.assertEqual(rec['local_plan']['steps'][0]['result'],complete()['result'])
        self.assertEqual(rec['local_plan']['steps'][0]['sources'],complete()['sources'])
        plan_views=[e['data']['plan'] for e in events if e['type']=='version_event' and e.get('section')=='turn_plan' and e.get('action')=='updated']
        self.assertEqual(plan_views[-1],rec['local_plan'])
        self.assertEqual(rec['memory_retrievals'][0]['result']['status'],'not_found')
        requests=rec['model_requests']
        self.assertEqual(requests[0]['tool_choice']['function']['name'],'update_turn_plan')
        self.assertEqual(len([m for m in requests[-1]['messages'] if m.get('reasoning')]),len(requests)-1)
        self.assertIn('LOCAL_PLAN_TURN_ONE',json.dumps(requests[-1],ensure_ascii=False))
        self.assertIn(complete()['result'],json.dumps(requests[-1],ensure_ascii=False))
        for request in requests:
            if any(t['function']['name']=='retrieve_client_memory' for t in request['tools']):
                results=[json.loads(m['content']) for m in request['messages'] if m['role']=='tool']
                plans=[r['structuredContent'] for r in results if 'steps' in r.get('structuredContent',{})]
                self.assertIn('in_progress',plans[-1]['steps'].values())
        views=self.get('/api/session_notes?paired=1&session_id='+s['session_id'])['notes']
        self.assertEqual(len([n for n in views if n['actor']=='client']),4)
        self.assertTrue(any('로컬 작업 계획' in n['label'] for n in views))
        s=self.get('/api/paired/session?session_id='+s['session_id'])
        s=json.loads(post(self.server,'/api/paired/manual',{'session_id':s['session_id'],'expected_step':len(s['steps']),'actor':'counselor','content':'원하는 속도로 이야기해도 괜찮아요.'}))['session']
        Model.q2a_actions=prefix('LOCAL_PLAN_TURN_TWO')+finish()
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        rec=self.notes(s)['records'][1]
        self.assertNotIn('LOCAL_PLAN_TURN_ONE',json.dumps(rec['model_requests'][0],ensure_ascii=False))
        self.assertNotIn('LOCAL_PLAN_TURN_ONE',json.dumps(rec['router_request'],ensure_ascii=False))
        self.assertEqual(rec['local_plan']['goal'],'LOCAL_PLAN_TURN_TWO')

    def test_cannot_skip_plan_or_commit_after_replanning_without_new_judgment(self):
        s=self.prepare()
        Model.q2a_actions=[judgment(),commit(True)]
        events=self.step(s);self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(self.notes(s)['records'],[])
        s=self.prepare()
        Model.q2a_actions=prefix()+[judgment(),change({**add(),'action':'revise'}),change(start()),change(complete()),commit(True),judgment(),commit(True),action('update_client_notes',operations=[]),progress()]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        errors=[e for e in events if e['type']=='tool_result' and e.get('is_error')]
        self.assertEqual(len(errors),1)
        self.assertEqual(len(self.notes(s)['records'][0]['judgment_history']),2)

    def test_failed_final_record_does_not_save_plan_or_notes(self):
        s=self.prepare();Model.q2a_actions=prefix()+[judgment(),commit(True),action('update_client_notes',operations=[op('add','미완료 변경')]),progress('invalid')]
        events=self.step(s);self.assertEqual(events[-1]['type'],'error')
        n=self.notes(s);self.assertEqual(n['records'],[]);self.assertEqual(n['local_plan'],{})
        self.assertNotIn('미완료 변경',n['content'])
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])

    def test_independent_prompts_tools_and_editing(self):
        q3=self.get('/api/prompts/source?role=client&version=Q3&kind=system')['content']
        for kind in ('system','user','trait_router','trait_router_user','trajectory'):
            source=self.get('/api/prompts/source?role=client&version=Q3a&kind='+kind)
            self.assertIn('/Q3a/',source['path'])
            post(self.server,'/api/prompts/save',{**source,'content':source['content']+'\n<!-- Q3a only -->\n'})
        self.assertEqual(q3,self.get('/api/prompts/source?role=client&version=Q3&kind=system')['content'])
        self.assertNotIn('update_turn_plan',[t['function']['name'] for t in registry.client_runtime('Q3').TOOLS])
        self.assertIn('update_turn_plan',[t['function']['name'] for t in registry.client_runtime('Q3a').TOOLS])

if __name__=='__main__':unittest.main()
