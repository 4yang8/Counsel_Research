import copy
import json
import unittest
from pathlib import Path
from urllib.request import urlopen
from test_lab import fixture, close, post, Model
from client.Q2a_prefill_test import runtime
from client.Q2a_prefill_test.prefill import ThinkingSplit

from project_paths import PROJECT_ROOT
ROOT=PROJECT_ROOT
VERSION='Q2a_prefill_test'
PREFIX=(ROOT/'src/client'/VERSION/'trait_router_prefill.txt').read_text().strip()

class PrefillTests(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        Model.requests.clear();Model.q1_router_response=None;Model.q1_router_finish='stop';Model.q2a_actions=None
    def tearDown(self):
        Model.q1_router_response=None;Model.q1_router_finish='stop'
        close(self.server,self.model,self.temp)
    def get(self,path):return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path))
    def create(self):
        cfg=self.get('/api/config');scenario=copy.deepcopy(cfg['scenario'])
        scenario['profile']=json.loads((ROOT/'resources/profiles/group1/gold_ccd_profile_001.json').read_text())
        scenario['dialogue_format']='messages';scenario['dialogue']=[];scenario.pop('dialogue_turns',None)
        return json.loads(post(self.server,'/api/paired/create',{'experiment_version':VERSION,'counselor_version':'basic',
            'scenario':scenario,'messages':[{'role':'user','content':'어디서부터 이야기해도 괜찮아요. 지금 마음에 걸리는 일을 알려주세요.'}],
            'system_prompt':runtime.specification()['system_prompt'],'generation':cfg['generation'],
            'client_reasoning_budgets':{'main':512,'subagent':256}}))
    def step(self,s):return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{'session_id':s['session_id'],'expected_step':0}).splitlines()]
    def test_router_only_real_wire_and_full_turn(self):
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertEqual(len(Model.requests),5)
        router=Model.requests[0];main=Model.requests[1:]
        self.assertEqual(router['thinking_token_budget'],256)
        self.assertTrue(router['prompt'].endswith('<think>\n'+PREFIX))
        self.assertFalse(router['add_special_tokens']);self.assertIn('structured_outputs',router)
        sub=[e['event'] for e in events if e['type']=='agent_event']
        request=next(e for e in sub if e['type']=='request');inspection=next(e for e in sub if e['type']=='inspection');done=next(e for e in sub if e['type']=='done')
        self.assertEqual(request['wire_request'],router)
        self.assertEqual(request['endpoint'],'/v1/completions')
        self.assertEqual(inspection['prompt'],router['prompt'])
        self.assertEqual(request['payload']['messages'][-1]['reasoning'],PREFIX)
        self.assertEqual(done['reasoning'],'PREFILL_ROUTER_REASON');self.assertEqual(done['reasoning_prefill'],PREFIX)
        self.assertEqual(json.loads(done['response'])['selections'][0]['background_ids'],['/childhood_data'])
        for req in main:
            self.assertNotIn('prompt',req);self.assertNotIn(PREFIX,json.dumps(req));self.assertEqual(req['thinking_token_budget'],512)
        self.assertEqual(len([m for m in main[-1]['messages'] if m.get('reasoning')]),3)
        notes=self.get('/api/client_notes?paired=1&session_id='+s['session_id']);self.assertIn('/'+VERSION+'/',notes['path'])
        saved=self.get('/api/paired/session?session_id='+s['session_id']);log=json.loads(Path(saved['steps'][0]['saved_path']).read_text())
        logged=next(e['event'] for e in log['subagent_events'] if e['event']['type']=='request')
        self.assertEqual(logged['wire_request'],router)
    def test_truncation_stops_before_main_or_note_save(self):
        Model.q1_router_finish='length';s=self.create();events=self.step(s)
        self.assertEqual(events[-1]['type'],'error');self.assertEqual(len(Model.requests),1)
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])
    def test_sources_independent(self):
        for kind in ['system','user','trait_router','trait_router_user']:
            own=self.get('/api/prompts/source?role=client&version='+VERSION+'&kind='+kind)
            baseline=self.get('/api/prompts/source?role=client&version=Q2a_trait_background&kind='+kind)
            self.assertEqual(own['content'], Path(own['path']).read_text())
            self.assertNotEqual(own['path'], baseline['path'])
            updated = own['content'] + '\nPREFILL_SOURCE_ISOLATION\n'
            post(self.server, '/api/prompts/save', {**{key: own[key] for key in ('role', 'version', 'kind', 'revision')}, 'content': updated})
            self.assertEqual(self.get('/api/prompts/source?role=client&version=Q2a_trait_background&kind='+kind)['content'], baseline['content'])
            self.assertIn('/'+VERSION+'/',own['path'])
    def test_split_all_boundaries(self):
        raw='some reasoning</think>{"selections":[]}'
        for cut in range(len(raw)+1):
            split=ThinkingSplit();r1,c1=split.feed(raw[:cut]);r2,c2=split.feed(raw[cut:],final=True)
            self.assertEqual(r1+r2,'some reasoning');self.assertEqual(c1+c2,'{"selections":[]}');self.assertTrue(split.closed)

if __name__=='__main__':unittest.main()
