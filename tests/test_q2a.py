"""Q2a: one main context; fixed speech followed by state tools."""
import copy
import json
import unittest
from urllib.request import urlopen
from test_lab import Model, fixture, close, post
from client.Q2a import runtime as q2a
from client.Q2a.states import STATES, INNER, EMPTY, parse_note, render

class Q2aTests(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        Model.requests.clear();Model.q2a_actions=None;Model.q2a_finish='stop';Model.p1_delay=0
    def tearDown(self):
        Model.q2a_actions=None;Model.q2a_finish='stop';close(self.server,self.model,self.temp)
    def get(self,path):return json.loads(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path).read())
    def create(self):
        cfg=self.get('/api/config');scenario=copy.deepcopy(cfg['scenario'])
        scenario['dialogue_format']='messages';scenario['dialogue']=[];scenario.pop('dialogue_turns',None)
        return json.loads(post(self.server,'/api/paired/create',{'experiment_version':'Q2a','counselor_version':'basic',
            'scenario':scenario,'system_prompt':q2a.specification()['system_prompt'],
            'messages':[{'role':'user','content':'말하기 부담스러우면 지금 말하지 않아도 괜찮아요.'}],
            'generation':{**cfg['generation'],'max_tokens':4096}}))
    def step(self,s):return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{'session_id':s['session_id'],'expected_step':len(s['steps'])}).splitlines()]
    def saved(self,s):return self.get('/api/paired/session?session_id='+s['session_id'])
    def notes(self,s):return self.get('/api/client_notes?paired=1&session_id='+s['session_id'])
    def actions(self):
        return [
          {'name':'stage_client_update','arguments':{'appraisal':{'interpretation':'걱정의 의미를 이해한다.','immediate_feeling':'망설임','relevant_evidence':'내면','uncertainty':''},'response_plan':{'action':'말하지 않는다.','reason':'아직 조심스럽다.'}}},
          {'name':'commit_client_utterance','arguments':{'utterance':'지금은 조금만 말하고 싶어요.'}},
          {'name':'select_client_states','arguments':{'selections':[]}},
          {'name':'update_client_states','arguments':{'updates':[],'inner_updates':[]}}]

    def test_same_context_sequence_and_next_turn_memory(self):
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertEqual(len(Model.requests),5)
        self.assertEqual([e['agent_kind'] for e in events if e['type']=='agent_event' and e['event']['type']=='request'],['trait_router'])
        main=Model.requests[1:]
        names=[t['function']['name'] for t in q2a.TOOLS]
        self.assertEqual([p['tool_choice']['function']['name'] for p in main],names)
        for i,request in enumerate(main):
            self.assertEqual(request['messages'][:2],main[0]['messages'][:2])
            self.assertEqual([m['reasoning'] for m in request['messages'] if m['role']=='assistant'],['Q2a_REASON_'+str(j) for j in range(i)])
            self.assertEqual(len([m for m in request['messages'] if m['role']=='tool']),i)
        self.assertIn('걱정은 알고 있지만',json.dumps(main[-1]['messages'],ensure_ascii=False))
        note=self.notes(s);self.assertEqual(note['version'],'Q2a');self.assertIn('/Q2a/',note['path'])
        r=note['records'][0];self.assertEqual(r['utterance'],self.saved(s)['public_dialogue'][-1]['content'])
        self.assertIn('보류하고 싶다',r['inner_after']['unspoken_and_wishes'])
        self.assertIn('## 현재 내면',r['notes_after'])
        for key in set(STATES)-{'bond'}:self.assertEqual(r['states_before'][key],r['states_after'][key])
        record=next(e for e in events if e['type']=='client_record');self.assertEqual(record['response_source'],'committed_utterance')
        starts=[e['name'] for e in events if e['type']=='tool_start'];self.assertEqual(starts,names)
        s=self.saved(s);post(self.server,'/api/paired/manual',{'session_id':s['session_id'],'expected_step':len(s['steps']),'actor':'counselor','content':'어떤 부분이 부담스러운가요?'})
        Model.requests.clear();events=self.step(self.saved(s));self.assertEqual(events[-1]['type'],'done')
        first=Model.requests[1]['messages'];self.assertIn(r['notes_after'],first[-1]['content']);self.assertNotIn('Q2a_REASON',json.dumps(first));self.assertEqual(len(self.notes(s)['records']),2)

    def test_empty_review_and_no_change_are_valid(self):
        for selected in ([],[{'state':'bond','reason':'검토했지만 유지'}]):
            Model.q2a_actions=self.actions();Model.q2a_actions[2]['arguments']['selections']=selected
            s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
            r=self.notes(s)['records'][0];self.assertEqual(r['notes_before'],r['notes_after']);self.assertEqual(r['state_updates'],[])

    def test_inner_updates_independent_of_state_changes_and_preserved(self):
        Model.q2a_actions=self.actions()
        Model.q2a_actions[-1]['arguments']['inner_updates']=[{'section':'feelings_and_concerns','content':'말로는 괜찮다고 했지만 평가받을까 하는 걱정이 남아 있다.','reason':'이번 질문을 평가로 받아들였다.'}]
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        first=self.notes(s)['records'][0]
        self.assertEqual(first['states_before'],first['states_after'])
        self.assertIn('걱정이 남아',first['inner_after']['feelings_and_concerns'])
        self.assertEqual(first['state_updates'],[])
        s=self.saved(s);post(self.server,'/api/paired/manual',{'session_id':s['session_id'],'expected_step':len(s['steps']),'actor':'counselor','content':'지금 어떤 마음인가요?'})
        Model.q2a_actions=self.actions();Model.requests.clear()
        events=self.step(self.saved(s));self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertEqual(self.notes(s)['records'][-1]['inner_after'],first['inner_after'])
        self.assertIn(first['notes_after'],Model.requests[0]['messages'][-1]['content'])
        self.assertIn(first['notes_after'],Model.requests[1]['messages'][-1]['content'])
        # The counselor still sees public speech only.
        self.assertNotIn(first['inner_after']['feelings_and_concerns'],json.dumps(self.saved(s)['public_dialogue'],ensure_ascii=False))

    def test_invalid_inner_update_does_not_commit_state_or_speech(self):
        Model.q2a_actions=self.actions()
        Model.q2a_actions[-1]['arguments']['inner_updates']=[{'section':'unknown','content':'변경','reason':'근거'}]
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(self.saved(s)['steps'],[]);self.assertEqual(self.notes(s)['records'],[])

    def test_note_structure_and_legacy_compatibility(self):
        initial=q2a.load({},None);self.assertEqual(set(initial['inner']),set(INNER))
        self.assertEqual(set(initial['inner'].values()),{EMPTY})
        self.assertEqual(parse_note(render(initial['states'],initial['inner']))['inner'],initial['inner'])
        legacy='# 현재 심리상태\n\n'+'\n\n'.join('## '+title+'\n기존 내용 '+key for key,(title,_) in STATES.items())+'\n'
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'Q2a'/'legacy';folder.mkdir(parents=True);path=folder/'state_notes.md';path.write_text(legacy)
            loaded=q2a.load({'_storage_dir':tmp},'legacy')
            self.assertEqual(path.read_text(),legacy)
            self.assertEqual(loaded['states']['bond'],'기존 내용 bond')
            self.assertEqual(set(loaded['inner'].values()),{EMPTY})
            combined=render(loaded['states'],loaded['inner'])
            self.assertEqual(parse_note(combined)['states'],loaded['states'])
        for bad in [initial['content'].replace('### 자기개방 의향','### 잘못된 제목'),initial['content'].replace('(내용 없음)','',1)]:
            with self.assertRaises(ValueError):parse_note(bad)

    def test_unselected_state_cannot_be_saved(self):
        Model.q2a_actions=self.actions();Model.q2a_actions[-1]['arguments']['updates']=[{'state':'bond','content':'잘못된 변경','reason':'선택하지 않음'}]
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'error')
        self.assertTrue(any(e['type']=='tool_result' and e['is_error'] for e in events));self.assertEqual(self.saved(s)['steps'],[]);self.assertEqual(self.notes(s)['records'],[])

    def test_incomplete_or_truncated_turn_does_not_commit(self):
        for actions,finish in ((self.actions()[:2],'stop'),(self.actions(),'length')):
            Model.q2a_actions=actions;Model.q2a_finish=finish
            s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'error');self.assertEqual(self.saved(s)['steps'],[]);self.assertEqual(self.notes(s)['records'],[])

    def test_out_of_order_tool_rejected(self):
        Model.q2a_actions=[self.actions()[1]];Model.q2a_finish='tool_calls'
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'error');self.assertEqual(self.notes(s)['records'],[])
        self.assertTrue(any(e['type']=='tool_result' and e['is_error'] for e in events))

    def test_prompt_sources_are_separate_from_q2(self):
        specs=self.get('/api/versions')['versions'];spec=next(s for s in specs if s['id']=='Q2a')
        self.assertEqual([s['kind'] for s in spec['subagent_prompts']],['trait_router'])
        original=self.get('/api/prompts/source?role=client&version=Q2&kind=system')['content']
        for kind in ['system','trait_router']:
            src=self.get('/api/prompts/source?role=client&version=Q2a&kind='+kind)
            result=json.loads(post(self.server,'/api/prompts/save',{**src,'content':src['content']+'\nQ2a_EDIT'}))
            self.assertTrue(result['content'].endswith('Q2a_EDIT'))
        self.assertEqual(self.get('/api/prompts/source?role=client&version=Q2&kind=system')['content'],original)

    def test_standalone_notes_and_storage_discovery(self):
        cfg=self.get('/api/config');events=[json.loads(line) for line in post(self.server,'/api/chat',{'experiment_version':'Q2a','scenario':cfg['scenario'],'system_prompt':q2a.specification()['system_prompt'],'generation':cfg['generation'],'messages':[{'role':'user','content':'편한 만큼 말하세요.'}]}).splitlines()]
        self.assertEqual(events[-1]['type'],'done',events[-1])
        row=next(r for r in self.get('/api/sessions')['sessions'] if r['kind']=='Q2a')
        note=self.get('/api/client_notes?version=Q2a&session_id='+row['session_id']+'&key='+row['key']);self.assertEqual(len(note['records']),1)

if __name__=='__main__':unittest.main()
