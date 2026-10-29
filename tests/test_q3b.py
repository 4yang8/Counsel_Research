"""Optional planning, fresh judgments, durable records and version isolation."""
import json
import unittest
import test_memory_experiments as base
from test_memory_experiments import Model, action, commit, post
from test_f_free_notes import query, op
from test_q3 import plan as judgment, progress
from test_q3a import change, add, start, complete, finish
import client.registry as registry


class Q3bTests(unittest.TestCase):
    setUp=base.MemoryExperiments.setUp
    tearDown=base.MemoryExperiments.tearDown
    get=base.MemoryExperiments.get
    create=base.MemoryExperiments.create
    step=base.MemoryExperiments.step
    notes=base.MemoryExperiments.notes

    def prepare(self):
        Model.q1_router_response='{"selections":[],"note_ids":[]}'
        return self.create('Q3b')

    def test_direct_path_and_empty_note_update(self):
        s=self.prepare();Model.q2a_actions=finish()
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        rec=self.notes(s)['records'][0]
        self.assertEqual(rec['planning_mode'],'direct');self.assertEqual(rec['local_plan'],{})
        self.assertEqual(rec['local_plan_history'],[]);self.assertEqual(rec['note_operations'],[])
        requests=rec['model_requests'];self.assertEqual(len(requests),4)
        self.assertEqual(requests[0]['tool_choice'],'required')
        self.assertEqual({t['function']['name'] for t in requests[0]['tools']},{'stage_client_update','retrieve_client_memory','update_turn_plan'})
        self.assertEqual(len([m for m in requests[-1]['messages'] if m.get('reasoning')]),3)
        self.assertEqual(self.notes(s)['local_plan_text'],'별도 작업 계획 없이 반응을 판단했습니다.')
        views=self.get('/api/session_notes?paired=1&session_id='+s['session_id'])['notes']
        self.assertTrue(any('별도 작업 계획 없이' in v['content'] for v in views))

    def test_optional_plan_lookup_replan_and_full_ui_history(self):
        s=self.prepare()
        Model.q2a_actions=[change(add(),add('s2'),goal='필요한 근거 확인'),change(start()),
            judgment(),action('retrieve_client_memory',**query('episodes')),
            change(complete(),{'action':'skip','step_id':'s2','result':'기록이 없어 현재 자료로 판단'},add('s3','불확실한 범위를 정한다')),
            change(start('s3')),change(complete('s3')),*finish()]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertEqual(len([e for e in events if e['type']=='tool_result' and e.get('is_error')]),1)
        rec=self.notes(s)['records'][0];self.assertEqual(rec['planning_mode'],'planned')
        self.assertEqual([r['status'] for r in rec['local_plan']['steps']],['completed','skipped','completed'])
        self.assertEqual(len(rec['local_plan_history']),5)
        receipts=[json.loads(e['model_content'])['structuredContent'] for e in events if e['type']=='tool_result' and e.get('name')=='update_turn_plan' and not e.get('is_error')]
        self.assertTrue(receipts[-1]['ready_for_judgment']);self.assertNotIn('plan',receipts[-1])
        views=[e['data']['plan'] for e in events if e['type']=='version_event' and e.get('section')=='turn_plan' and e.get('action')=='committed']
        self.assertEqual(views[-1],rec['local_plan'])

    def test_lookup_after_direct_judgment_requires_new_judgment(self):
        s=self.prepare()
        Model.q2a_actions=[judgment(),action('retrieve_client_memory',**query('episodes')),commit(True),*finish()]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertEqual(len([e for e in events if e['type']=='tool_result' and e.get('is_error')]),1)
        rec=self.notes(s)['records'][0];self.assertEqual(len(rec['judgment_history']),2)
        self.assertEqual(rec['planning_mode'],'direct');self.assertEqual(len(rec['memory_retrievals']),1)

    def test_switch_to_plan_blocks_stale_commit_and_next_turn_starts_fresh(self):
        s=self.prepare()
        Model.q2a_actions=[judgment(),change(add(),goal='Q3B_PLAN_MARKER'),commit(True),change(start()),change(complete()),commit(True),*finish()]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertEqual(len([e for e in events if e['type']=='tool_result' and e.get('is_error')]),2)
        self.assertEqual(len(self.notes(s)['records'][0]['judgment_history']),2)
        s=self.get('/api/paired/session?session_id='+s['session_id'])
        s=json.loads(post(self.server,'/api/paired/manual',{'session_id':s['session_id'],'expected_step':len(s['steps']),'actor':'counselor','content':'원하는 속도로 이야기해도 괜찮아요.'}))['session']
        Model.q2a_actions=finish();events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        rec=self.notes(s)['records'][1];self.assertEqual(rec['planning_mode'],'direct')
        self.assertNotIn('Q3B_PLAN_MARKER',json.dumps(rec['model_requests'][0],ensure_ascii=False))

    def test_failed_final_record_is_atomic(self):
        s=self.prepare();Model.q2a_actions=[judgment(),commit(True),action('update_client_notes',operations=[op('add','미완료 변경')]),progress('invalid')]
        events=self.step(s);self.assertEqual(events[-1]['type'],'error')
        note=self.notes(s);self.assertEqual(note['records'],[]);self.assertNotIn('미완료 변경',note['content'])
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])

    def test_prompt_and_tool_isolation(self):
        before=registry.client_runtime('Q3a').specification()
        for kind in ('system','user','trait_router','trait_router_user','trajectory'):
            source=self.get('/api/prompts/source?role=client&version=Q3b&kind='+kind)
            self.assertIn('/Q3b/',source['path'])
            post(self.server,'/api/prompts/save',{**source,'content':source['content']+'\n<!-- Q3b only -->\n'})
        self.assertEqual(before,registry.client_runtime('Q3a').specification())
        q3a=registry.client_runtime('Q3a');q3b=registry.client_runtime('Q3b')
        self.assertIsNot(q3a.TOOLS,q3b.TOOLS)
        self.assertIn('별도 작업 계획 없이',q3b.TOOLS[0]['function']['description'])
        import jsonschema
        for tool in q3b.TOOLS:jsonschema.Draft202012Validator.check_schema(tool['function']['parameters'])

if __name__=='__main__':unittest.main()
