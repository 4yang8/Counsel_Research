"""Q3 HTTP lifecycle, context preservation, history lookup and rollback (mock model)."""
import copy
import json
import unittest
from urllib.parse import urlencode
import test_memory_experiments as base
from test_memory_experiments import Model, action, stage, commit, post
from test_f_free_notes import op, query
import client.registry as registry
from client.Q3 import trajectory, memory


def plan(movement='maintain'):
    result=stage(True)
    result['arguments']['trajectory_plan']={'position':'초반','movement':movement,'grounds':'개인적 경계와 존중받았다는 현재 해석을 연결했다.'}
    return result

def progress(movement='maintain'):
    return action('update_trajectory_progress',position='초반',movement=movement,
                  current_experience='말할 시간을 주어 안심하지만 아직 경계한다.',
                  grounds='이전 기록과 이번 해석·발화를 비교하면 조심스러움은 유지된다.',next_condition='평가받지 않고 이야기할 수 있을지')

class Q3Tests(unittest.TestCase):
    setUp=base.MemoryExperiments.setUp
    tearDown=base.MemoryExperiments.tearDown
    get=base.MemoryExperiments.get
    create=base.MemoryExperiments.create
    step=base.MemoryExperiments.step
    notes=base.MemoryExperiments.notes

    def test_two_turns_trajectory_lookup_revision_and_same_turn_reasoning(self):
        s=self.create('Q3');r=registry.client_runtime('Q3')
        Model.q1_router_response='{"selections":[],"note_ids":[]}'
        initial=self.notes(s)
        self.assertEqual(initial['trajectory_progress'],{})
        Model.q2a_actions=[plan(),commit(True),action('update_client_notes',operations=[op('add','말할 시간을 원한다.'),op('add','아직 평가가 걱정된다.')]),progress()]
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
        saved=self.notes(s);rec=saved['records'][0];ids=[x['id'] for x in saved['items']['notes']]
        self.assertEqual(rec['trajectory_progress'],progress()['arguments'])
        self.assertEqual(rec['trajectory_before'],{})
        main=[e for e in events if e['type']=='request'];self.assertEqual(len(main),4)
        self.assertIn('사전 변화 궤적',main[0]['payload']['messages'][1]['content'])
        self.assertEqual([len([m for m in e['payload']['messages'] if m.get('reasoning')]) for e in main],[0,1,2,3])
        self.assertEqual(main[-1]['payload']['tool_choice']['function']['name'],'update_trajectory_progress')
        # Reload and continue through the same user-facing API.
        s=self.get('/api/paired/session?session_id='+s['session_id'])
        s=json.loads(post(self.server,'/api/paired/manual',{'session_id':s['session_id'],'expected_step':len(s['steps']),'actor':'counselor','content':'지금은 말하고 싶은 만큼만 이야기해도 괜찮아요.'}))['session']
        Model.q1_router_response=json.dumps({'selections':[],'note_ids':[ids[0]]})
        Model.q2a_actions=[plan(),action('retrieve_client_memory',**{**query('trajectory'),'turns':[1]}),plan('advance'),commit(True),action('update_client_notes',operations=[]),progress('advance')]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        saved=self.notes(s);rec=saved['records'][1]
        self.assertEqual(len(rec['judgment_history']),2)
        self.assertEqual(rec['trajectory_plan']['movement'],'advance')
        self.assertEqual(rec['trajectory_before'],progress()['arguments'])
        lookup=rec['memory_retrievals'][0]['result']['results'][0]
        self.assertEqual(lookup['source_id'],'trajectory:1');self.assertEqual(lookup['trajectory_progress'],progress()['arguments'])
        main=[e for e in events if e['type']=='request']
        self.assertNotIn(ids[1],main[0]['payload']['messages'][1]['content'])
        self.assertIn(ids[1],main[-1]['payload']['messages'][1]['content'])
        self.assertIn(progress()['arguments']['current_experience'],main[0]['payload']['messages'][1]['content'])
        for event in main:
            for group in event['context_manifest']['groups']:
                if group['role']=='user':self.assertEqual(''.join(p['value'] for p in group['parts'] if p['included']),event['payload']['messages'][group['message_index']]['content'])
        views=self.get('/api/session_notes?paired=1&session_id='+s['session_id'])['notes']
        self.assertEqual(len([n for n in views if n['actor']=='client']),3)
        self.assertTrue(any('실제 궤적' in n['label'] or '현재 궤적' in n['label'] for n in views))

    def test_invalid_progress_rolls_back_notes_and_public_utterance(self):
        s=self.create('Q3');Model.q1_router_response='{"selections":[],"note_ids":[]}'
        Model.q2a_actions=[plan(),commit(True),action('update_client_notes',operations=[op('add','저장되면 안 되는 변경안')]),progress('invalid')]
        events=self.step(s);self.assertEqual(events[-1]['type'],'error')
        note=self.notes(s);self.assertEqual(note['records'],[]);self.assertEqual(note['trajectory_progress'],{})
        self.assertNotIn('저장되면',note['content'])
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])

    def test_trajectory_edit_applies_next_turn_and_records_original_source(self):
        f_before=self.get('/api/prompts/source?role=client&version=Q2a_agentic_memory_F&kind=system')['content']
        source=self.get('/api/prompts/source?role=client&version=Q3&kind=trajectory')
        marker='\n### 수동 수정 검증\n관계의 속도를 확인한다.\n'
        post(self.server,'/api/prompts/save',{**source,'content':source['content']+marker})
        s=self.create('Q3');Model.q1_router_response='{"selections":[],"note_ids":[]}'
        Model.q2a_actions=[plan(),commit(True),action('update_client_notes',operations=[]),progress()]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        first=self.notes(s)['records'][0]
        self.assertIn(marker,first['trajectory_source'])
        changed=self.get('/api/prompts/source?role=client&version=Q3&kind=trajectory')
        post(self.server,'/api/prompts/save',{**changed,'content':source['content']})
        note=self.notes(s);self.assertNotIn(marker,note['trajectory']);self.assertIn(marker,note['records'][0]['trajectory_source'])
        self.assertEqual(f_before,self.get('/api/prompts/source?role=client&version=Q2a_agentic_memory_F&kind=system')['content'])
        self.assertIsNot(registry.client_runtime('Q3').TOOLS,registry.client_runtime('Q2a_agentic_memory_F').TOOLS)

    def test_schema_and_empty_history(self):
        with self.assertRaises(ValueError):trajectory.validate({'movement':'advance'},trajectory.PROGRESS)
        result=memory.lookup({'items':{'notes':[]},'records':[]},query('trajectory'))
        self.assertEqual(result['status'],'not_found')
        with self.assertRaises(ValueError):memory.lookup({'items':{},'records':[]},{**query('trajectory'),'item_ids':['n111111111111']})

if __name__=='__main__':unittest.main()
