"""A–F isolation, exact selective contexts, active lookup and transactional completion."""
import copy
import json
import unittest
from pathlib import Path
from urllib.request import urlopen
from test_lab import fixture, close, post, Model
import client.registry as registry
import runtime.paired_lab as paired_lab
from client.memory_experiments import memory
from client.Q2a_trait_background import note_items

VERSIONS=list(registry.MEMORY_EXPERIMENTS)
def action(name,**arguments):return {'name':name,'arguments':arguments}
def stage(goal=False,feeling='조심스럽다'):
    args={'appraisal':{'interpretation':'선택을 존중하는 말로 받아들였다.','immediate_feeling':feeling,'relevant_evidence':'상담사 발화와 현재 노트','uncertainty':''},'response_plan':{'action':'말할 범위를 설명한다.','reason':'부담이 남아 있다.'}}
    if goal:args['turn_goal']={'intention':'준비할 시간을 확보하고 싶다.','grounds':'말할 범위를 정하고 싶은 마음','uncertainty':''}
    return action('stage_client_update',**args)
def commit(retrieve=False,status='sufficient'):
    args={'utterance':'조금 준비할 시간이 필요해요.'}
    if retrieve:args['evidence_decision']={'status':status,'reason':'확인한 원문에 따라 표현 범위를 정했다.','remaining_gap':''}
    return action('commit_client_utterance',**args)
def finish():return [action('select_client_states',selections=[]),action('update_client_states',operations=[])]
def query(source='current',**extra):
    return {'source':source,'query':'','section':'','item_ids':[],'turns':[],'offset':0,'limit':5,'reason':'준비 시간이 필요한 이유를 확인해 공개 범위를 정한다.',**extra}

class MemoryExperiments(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        Model.requests.clear();Model.q2a_actions=None;Model.q1_router_response=None;Model.q2a_finish='tool_calls'
    def tearDown(self):
        Model.q2a_actions=None;Model.q1_router_response=None;Model.q2a_finish='stop'
        close(self.server,self.model,self.temp)
    def get(self,path):return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path))
    def create(self,v):
        cfg=self.get('/api/config');scenario=copy.deepcopy(cfg['scenario']);scenario['dialogue_format']='messages';scenario['dialogue']=[];scenario.pop('dialogue_turns',None)
        return json.loads(post(self.server,'/api/paired/create',{'experiment_version':v,'counselor_version':'basic','scenario':scenario,
            'system_prompt':registry.client_runtime(v).specification()['system_prompt'],'generation':cfg['generation'],
            'messages':[{'role':'user','content':'준비할 시간을 갖고 이야기해도 괜찮아요.'}]}))
    def step(self,s):
        return [json.loads(l)['event'] for l in post(self.server,'/api/paired/step',{'session_id':s['session_id'],'expected_step':len(s['steps'])}).splitlines()]
    def seed(self,s):
        runtime=registry.client_runtime(s['client_version']);storage=Path(self.temp.name)/('seed_'+s['session_id']);cfg={'_storage_dir':str(storage)}
        saved=paired_lab.read_session(self.server,s['session_id']);saved['client_storage_dir']=str(storage);paired_lab.save_session(self.server,saved)
        note=runtime.load(cfg,s['session_id']);items=note['items']
        items['bond']=[{'id':'n111111111111','content':'SELECTED_MARKER: 말할 시간을 주면 안심한다.'}]
        items['unspoken_and_wishes']=[{'id':'n222222222222','content':'OMITTED_MARKER: 과거에 준비 없이 말하라고 해서 부담스러웠다.'}]
        path=runtime.directory(cfg,s['session_id'])/'state_notes.md';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(note_items.render_items(items))
        return runtime,path
    def router(self,v):
        value={'selections':[]}
        if registry.MEMORY_EXPERIMENTS[v]['select_notes']:value['note_ids']=['n111111111111']
        Model.q1_router_response=json.dumps(value)
    def notes(self,s):return self.get('/api/client_notes?paired=1&session_id='+s['session_id'])
    def test_all_versions_context_boundaries_restore_and_preserve_reasoning(self):
        for v,opts in registry.MEMORY_EXPERIMENTS.items():
            if v in ('Q2a_agentic_memory_F','Q3','Q3a','Q3b','Q3sk','Q3n','Q3sb','Q3x'):continue
            with self.subTest(version=v):
                s=self.create(v);runtime,path=self.seed(s);self.router(v);Model.requests.clear()
                Model.q2a_actions=[stage(opts['turn_goal']),commit(opts['retrieve']),*finish()]
                events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
                main=[e for e in events if e['type']=='request'];self.assertEqual(len(main),4)
                for i,event in enumerate(main):
                    payload=event['payload'];text=json.dumps(payload,ensure_ascii=False)
                    self.assertIn('SELECTED_MARKER',text)
                    self.assertEqual('OMITTED_MARKER' in text,not opts['select_notes'] or i>=2)
                    self.assertNotIn('context_manifest',payload)
                    self.assertEqual(len([m for m in payload['messages'] if m.get('reasoning')]),i)
                    parts=[p for g in event['context_manifest']['groups'] for p in g['parts'] if p['id']=='note.item.n222222222222']
                    self.assertEqual(parts[0]['included'],not opts['select_notes'] or i>=2)
                    for group in event['context_manifest']['groups']:
                        if group['role']=='user':
                            self.assertEqual(''.join(p['value'] for p in group['parts'] if p['included']),payload['messages'][group['message_index']]['content'])
                saved=self.notes(s)['records'][0]
                self.assertEqual(saved['experiment_condition'],opts['condition']);self.assertEqual(saved['model_requests'],[e['payload'] for e in main])
                self.assertEqual(saved['turn_goal'] is not None,opts['turn_goal'])
                self.assertIn('OMITTED_MARKER',path.read_text());self.assertEqual(saved['note_operations'],[])
                self.assertEqual(saved['agent_usage']['main']['total_tokens'],520)
    def test_retrieve_before_stage_revise_and_lookup_original_history(self):
        for v in ('Q2a_note_retrieve','Q2a_agentic_memory'):
            s=self.create(v);self.seed(s);self.router(v);goal=registry.MEMORY_EXPERIMENTS[v]['turn_goal']
            Model.q2a_actions=[action('retrieve_client_memory',**query(item_ids=['n222222222222'])),stage(goal),
                action('retrieve_client_memory',**query(source='episodes')),stage(goal,'확인 후 조금 안심된다'),commit(True),*finish()]
            events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1]);record=self.notes(s)['records'][0]
            self.assertEqual(len(record['judgment_history']),2);self.assertEqual(record['appraisal']['immediate_feeling'],'확인 후 조금 안심된다')
            self.assertEqual(record['memory_retrievals'][0]['result']['results'][0]['item_id'],'n222222222222')
            self.assertEqual(record['memory_retrievals'][1]['result']['status'],'not_found')
            self.assertEqual(record['memory_retrievals'][1]['result']['available_completed_turns'],[])
            first=self.get('/api/paired/session?session_id='+s['session_id'])
            second=json.loads(post(self.server,'/api/paired/manual',{'session_id':s['session_id'],'expected_step':len(first['steps']),'actor':'counselor','content':'앞서 준비할 시간이 필요하다고 하셨죠.'}))['session']
            Model.q2a_actions=[action('retrieve_client_memory',**query(source='episodes',turns=[1])),stage(goal),commit(True),*finish()]
            events=self.step(second);self.assertEqual(events[-1]['type'],'done',events[-1]);record=self.notes(s)['records'][1]
            source=record['memory_retrievals'][0]['result']['results'][0]
            self.assertEqual(source['source_id'],'episode:1');self.assertEqual(source['utterance'],'조금 준비할 시간이 필요해요.')
            self.assertNotIn('model_requests',source);self.assertEqual(record['memory_retrievals'][0]['result']['available_completed_turns'],[1])
    def test_repeated_query_and_budget_closes_retrieval(self):
        v='Q2a_note_retrieve';s=self.create(v);self.seed(s);self.router(v)
        Model.q2a_actions=[*[action('retrieve_client_memory',**query()) for _ in range(4)],stage(),commit(True,'budget_exhausted'),*finish()]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1]);record=self.notes(s)['records'][0]
        self.assertEqual([r['result']['status'] for r in record['memory_retrievals']],['found']+['no_new_evidence']*3)
        main=[e['payload'] for e in events if e['type']=='request']
        self.assertNotIn('retrieve_client_memory',[t['function']['name'] for t in main[4]['tools']])
        self.assertTrue(record['evidence_decision']['lookup_limit_reached'])
    def test_invalid_selection_stops_before_main_and_keeps_note(self):
        s=self.create('Q2a_note_select');_,path=self.seed(s);before=path.read_text()
        Model.q1_router_response='{"selections":[],"note_ids":["foreign-session-id"]}'
        events=self.step(s);self.assertEqual(events[-1]['type'],'error');self.assertEqual(len(Model.requests),1)
        self.assertEqual(path.read_text(),before);self.assertEqual(self.notes(s)['records'],[])
    def test_failed_postcommit_patch_never_commits_speech_or_note(self):
        s=self.create('Q2a_agentic_memory');_,path=self.seed(s);self.router(s['client_version']);before=path.read_text()
        Model.q2a_actions=[stage(True),commit(True),action('select_client_states',selections=[]),action('update_client_states',operations=[{'section':'bond','action':'delete','item_id':'n111111111111','content':'','reason':'未選択'}])]
        events=self.step(s);self.assertEqual(events[-1]['type'],'error');self.assertEqual(path.read_text(),before)
        saved=self.get('/api/paired/session?session_id='+s['session_id']);self.assertEqual(saved['steps'],[])
    def test_independent_prompt_editors_and_storage(self):
        baseline=self.get('/api/prompts/source?role=client&version=Q2a_trait_background&kind=system')['content']
        for v in VERSIONS:
            for kind in ('system','user','trait_router','trait_router_user'):
                source=self.get('/api/prompts/source?role=client&version='+v+'&kind='+kind)
                marker='EDITOR_'+v+'_'+kind
                post(self.server,'/api/prompts/save',{**source,'content':source['content']+'\n'+marker})
            Model.requests.clear();events=self.step(self.create(v));self.assertEqual(events[-1]['type'],'done',events[-1])
            for req,kind in [(Model.requests[0],'trait_router'),(Model.requests[1],'system')]:self.assertIn('EDITOR_'+v+'_'+kind,req['messages'][0]['content'])
            for req,kind in [(Model.requests[0],'trait_router_user'),(Model.requests[1],'user')]:self.assertIn('EDITOR_'+v+'_'+kind,req['messages'][1]['content'])
        self.assertEqual(self.get('/api/prompts/source?role=client&version=Q2a_trait_background&kind=system')['content'],baseline)

class ForkIsolationTests(unittest.TestCase):
    def test_f_owns_implementation_and_storage(self):
        e=registry.client_runtime('Q2a_agentic_memory')
        f=registry.client_runtime('Q2a_agentic_memory_F')
        self.assertIsNot(e.TOOLS,f.TOOLS)
        self.assertIsNot(e.memory,f.memory)
        self.assertIsNot(e.note_items,f.note_items)
        self.assertIsNot(e.run_turn,f.run_turn)
        self.assertIsNot(e.routing_schema,f.routing_schema)
        self.assertEqual(f.OPTIONS['condition'],'F')
        self.assertNotEqual(e.directory({'_storage_dir':'/tmp'},'same'),f.directory({'_storage_dir':'/tmp'},'same'))

class LookupTests(unittest.TestCase):
    def test_changes_deleted_item_sources_and_filter_boundaries(self):
        note={'items':{'bond':[]},'records':[{'turn':1,'turn_id':'one','utterance':'대화','counselor_utterance':'질문','appraisal':{},'response_plan':{},'note_operations':[{'section':'bond','item_id':'n111111111111','action':'delete','content':'','before':'삭제 전 걱정','reason':'정정 근거'}]}]}
        result=memory.lookup(note,query('changes',item_ids=['n111111111111']))
        self.assertEqual(result['results'][0]['before'],'삭제 전 걱정');self.assertEqual(result['results'][0]['after'],'')
        self.assertEqual(memory.lookup(note,query('changes',turns=[2]))['status'],'not_found')
        self.assertEqual(memory.lookup(note,query('current'))['status'],'not_found')
        with self.assertRaises(ValueError):memory.lookup(note,query('episodes',item_ids=['n111111111111']))
    def test_pagination_exact_originals_and_not_found(self):
        note={'items':{'bond':[{'id':'n'+str(i).zfill(12),'content':'원문 '+str(i)} for i in range(8)]},'records':[]}
        a=memory.lookup(note,query(limit=3));b=memory.lookup(note,query(offset=a['next_offset'],limit=5))
        self.assertEqual(len(a['results']),3);self.assertEqual(len(b['results']),5);self.assertFalse(b['has_more'])
        self.assertEqual([r['content'] for r in a['results']+b['results']],[r['content'] for r in note['items']['bond']])
        self.assertEqual(memory.lookup(note,query(query='없는단어'))['status'],'not_found')

if __name__=='__main__':unittest.main()
