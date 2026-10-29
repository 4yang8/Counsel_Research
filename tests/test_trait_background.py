"""Actual request flow, source provenance, isolation and rejection of invalid IDs."""
import copy
import json
import unittest
from pathlib import Path
from urllib.request import urlopen
from test_lab import fixture, close, post, Model
from client.Q2a_trait_background import runtime as variant
from client.Q2a_trait_background.profile_context import background_candidates, routing_schema, resolve_selection
from client.Q1.runtime import split_profile

VERSION='Q2a_trait_background'
from project_paths import PROJECT_ROOT
ROOT=PROJECT_ROOT

class TraitBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        Model.requests.clear();Model.q2a_actions=None;Model.q2a_finish='stop';Model.q1_router_response=None
        self.profile=json.loads((ROOT/'resources/profiles/group1/gold_ccd_profile_001.json').read_text())
    def tearDown(self):
        Model.q1_router_response=None;Model.q2a_actions=None
        close(self.server,self.model,self.temp)
    def get(self,path):return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path))
    def create(self,version=VERSION,enabled=True,profile=None):
        cfg=self.get('/api/config');scenario=copy.deepcopy(cfg['scenario']);scenario['profile']=self.profile if profile is None else profile
        scenario['dialogue_format']='messages';scenario['dialogue']=[];scenario.pop('dialogue_turns',None)
        return json.loads(post(self.server,'/api/paired/create',{'experiment_version':version,'counselor_version':'basic','scenario':scenario,
            'input_blocks':{'profile':enabled},'messages':[{'role':'user','content':'말하기 어려운 마음을 이야기해도 괜찮아요.'}],
            'system_prompt':variant.specification()['system_prompt'],'generation':cfg['generation']}))
    def step(self,s):return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{'session_id':s['session_id'],'expected_step':len(s['steps'])}).splitlines()]
    def test_exact_sources_selected_only_and_complete_turn(self):
        tid='/intermediate_beliefs/assumption_negative'
        Model.q1_router_response=json.dumps({'selections':[{'trait_id':tid,'background_ids':['/childhood_data','/situations/1'],'relevance':'취약한 마음을 공개하는 상황과 관련된다.'}]},ensure_ascii=False)
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1]);self.assertEqual(len(Model.requests),5)
        router=Model.requests[0];main=Model.requests[1:]
        self.assertIn(self.profile['situations'][3]['situation'],router['messages'][1]['content'])
        selected=next(e['data']['selected'] for e in events if e['type']=='version_event' and e['section']=='traits')
        self.assertEqual(selected[0]['content'],self.profile['intermediate_beliefs']['assumption_negative'])
        self.assertEqual(selected[0]['backgrounds'][0]['content'],self.profile['childhood_data'])
        self.assertEqual(selected[0]['backgrounds'][1]['content'],self.profile['situations'][1])
        for payload in main:
            user=payload['messages'][1]['content']
            self.assertIn(self.profile['childhood_data'],user)
            self.assertIn(self.profile['situations'][1]['automatic_thought'],user)
            self.assertNotIn(self.profile['situations'][3]['situation'],user)
            self.assertEqual(len(payload['tools']),4)
            self.assertEqual(payload['messages'][:2],main[0]['messages'][:2])
        self.assertEqual([m['reasoning'] for m in main[-1]['messages'] if m['role']=='assistant'],['Q2a_REASON_0','Q2a_REASON_1','Q2a_REASON_2'])
        saved=self.get('/api/paired/session?session_id='+s['session_id'])
        notes=self.get('/api/client_notes?paired=1&session_id='+s['session_id'])
        self.assertEqual(notes['records'][0]['selected_traits'],selected)
        self.assertIn('/'+VERSION+'/',notes['path']);self.assertEqual(len(saved['steps']),1)
        log=json.loads(Path(saved['steps'][0]['saved_path']).read_text())
        self.assertEqual(log['model_calls'][0]['request'],main[0])
        self.assertFalse((Path(self.server.storage_dir)/'Q2a').exists())
    def test_missing_background_disabled_profile_and_empty_selection(self):
        for profile,enabled in [({'background':'대학원생','traits':['평가에 민감함']},True),(self.profile,False)]:
            with self.subTest(enabled=enabled):
                Model.requests.clear();s=self.create(profile=profile,enabled=enabled);events=self.step(s)
                self.assertEqual(events[-1]['type'],'done',events[-1])
                schema=Model.requests[0]['structured_outputs']['json'];items=schema['properties']['selections']
                self.assertEqual(items['items']['properties']['background_ids']['maxItems'],0)
                user=Model.requests[1]['messages'][1]['content'];self.assertNotIn(self.profile['childhood_data'],user)
                if not enabled:self.assertEqual(items['maxItems'],0)
        Model.q1_router_response='{"selections": []}';events=self.step(self.create());self.assertEqual(events[-1]['type'],'done')
    def test_invalid_ids_fail_without_saving_or_main_call(self):
        Model.q1_router_response=json.dumps({'selections':[{'trait_id':'/core_belief/core_belief_statement','background_ids':['/unknown'],'relevance':'관련됨'}]})
        s=self.create();events=self.step(s)
        self.assertEqual(events[-1]['type'],'error');self.assertEqual(len(Model.requests),1)
        saved=self.get('/api/paired/session?session_id='+s['session_id']);self.assertEqual(saved['steps'],[])
        self.assertFalse(list(Path(self.server.storage_dir).rglob('state_notes.md')))
    def test_candidates_and_duplicate_validation(self):
        original=copy.deepcopy(self.profile);basic,traits=split_profile(self.profile);rows=background_candidates(self.profile)
        self.assertEqual(len(rows),5);self.assertEqual(rows[1]['content'],self.profile['situations'][0])
        valid={'trait_id':traits[0]['trait_id'],'background_ids':['/situations/0'],'relevance':'관련'}
        for value in [{'selections':[valid,valid]}, {'selections':[{**valid,'background_ids':['/situations/0']*2}]}, {'selections':[{**valid,'relevance':''}]}]:
            with self.assertRaises(ValueError):resolve_selection(value,traits,rows)
        resolved=resolve_selection({'selections':[valid]},traits,rows)
        resolved[0]['backgrounds'][0]['content']['situation']='changed'
        self.assertEqual(self.profile,original)
    def test_editable_sources_are_independent_and_used(self):
        for kind in ['system','user','trait_router','trait_router_user']:
            source=self.get('/api/prompts/source?role=client&version='+VERSION+'&kind='+kind)
            marker='TEST_'+kind.upper()
            post(self.server,'/api/prompts/save',{**source,'content':source['content']+'\n'+marker})
            baseline=self.get('/api/prompts/source?role=client&version=Q2a&kind='+kind)
            self.assertNotIn(marker,baseline['content'])
        events=self.step(self.create());self.assertEqual(events[-1]['type'],'done')
        for request,markers in [(Model.requests[0],['TEST_TRAIT_ROUTER','TEST_TRAIT_ROUTER_USER']),(Model.requests[1],['TEST_SYSTEM','TEST_USER'])]:
            for i,marker in enumerate(markers):self.assertIn(marker,request['messages'][i]['content'])

if __name__=='__main__':unittest.main()
