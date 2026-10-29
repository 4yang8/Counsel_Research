"""New Q2: qualitative states and post-utterance selection, separate from Q1a."""
import copy
import json
from pathlib import Path
import unittest
from urllib.request import urlopen
from test_lab import Model, fixture, close, post
from client.Q2 import runtime as q2
from client.Q2.states import STATES, parse


class Q2Tests(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        Model.requests.clear();Model.q2_routes=Model.q2_updates=None;Model.q2_post_finish='stop';Model.q2_tool_finish='tool_calls';Model.p1_delay=0
    def tearDown(self):
        Model.q2_routes=Model.q2_updates=None;Model.q2_post_finish='stop';Model.q2_tool_finish='tool_calls';close(self.server,self.model,self.temp)
    def get(self,path):return json.loads(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path).read())
    def create(self):
        cfg=self.get('/api/config');scenario=copy.deepcopy(cfg['scenario'])
        scenario['dialogue_format']='messages';scenario['dialogue']=[];scenario.pop('dialogue_turns',None)
        return json.loads(post(self.server,'/api/paired/create',{'experiment_version':'Q2','counselor_version':'basic',
            'scenario':scenario,'system_prompt':q2.specification()['system_prompt'],
            'messages':[{'role':'user','content':'말하기 부담스러우면 지금 말하지 않아도 괜찮아요.'}],
            'generation':{**cfg['generation'],'max_tokens':4096}}))
    def step(self,session):return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{'session_id':session['session_id'],'expected_step':len(session['steps'])}).splitlines()]
    def saved(self,session):return self.get('/api/paired/session?session_id='+session['session_id'])
    def notes(self,session):return self.get('/api/client_notes?paired=1&session_id='+session['session_id'])

    def test_order_sources_next_turn_and_private_notes(self):
        session=self.create();events=self.step(session)
        self.assertEqual(events[-1]['type'],'done',events[-1]);self.assertEqual(len(Model.requests),5)
        trait,main,speech,router,updater=Model.requests
        self.assertIn('전체 특성 목록',trait['messages'][-1]['content'])
        self.assertIn('response_plan',str(main['tools']))
        self.assertEqual(main['tool_choice'],{'type':'function','function':{'name':'stage_client_update'}})
        self.assertEqual(speech['tool_choice'],'auto')
        self.assertIn('Q2_MAIN_REASON',speech['messages'][-2]['reasoning'])
        self.assertIn('## 완성된 내담자 발화',router['messages'][-1]['content'])
        self.assertIn('안도는 표현하되',router['messages'][-1]['content'])
        self.assertIn('조금 안심되지만 자세한',router['messages'][-1]['content'])
        self.assertTrue(updater['messages'][0]['content'].startswith('# 내담자 심리상태 기록 지침'))
        result=self.notes(session);self.assertEqual(len(parse(result['content'])),10)
        record=result['records'][0];self.assertEqual([u['state'] for u in record['state_updates']],['bond'])
        for key in set(STATES)-{'bond'}:self.assertEqual(record['states_before'][key],record['states_after'][key])
        self.assertNotIn('현재 심리상태',self.saved(session)['public_dialogue'][-1]['content'])
        phases=[e.get('agent_kind') for e in events if e['type']=='agent_event' and e['event']['type']=='request']
        self.assertEqual(phases,['trait_router','state_router','state_updater'])
        pos=next(i for i,e in enumerate(events) if e['type']=='model_result')
        self.assertTrue(all(i>pos for i,e in enumerate(events) if e.get('agent_kind') in ('state_router','state_updater') and e['event']['type']=='request'))
        saved=self.saved(session)
        post(self.server,'/api/paired/manual',{'session_id':session['session_id'],'expected_step':len(saved['steps']),'actor':'counselor','content':'어떤 부분이 아직 부담스러우세요?'})
        saved=self.saved(session);Model.requests.clear();Model.q2_routes=[]
        events=self.step(saved);self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertEqual(len(Model.requests),4)
        self.assertIn(record['notes_after'],Model.requests[1]['messages'][-1]['content'])
        self.assertNotIn('Q2_MAIN_REASON',Model.requests[1]['messages'][-1]['content'])
        self.assertEqual(len(self.notes(session)['records']),2)

    def test_endpoint_switch_applies_to_entire_turn_and_persists_per_attempt(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.error import HTTPError
        from runtime.connection_settings import normalize_url
        class AlternateModel(Model):
            hits = 0
            def do_POST(self):
                type(self).hits += 1
                super().do_POST()
        alternate = ThreadingHTTPServer(('127.0.0.1', 0), AlternateModel)
        Thread(target=alternate.serve_forever, daemon=True).start()
        try:
            session = self.create()
            original = session['connection']['vllm_url']
            other = f'http://127.0.0.1:{alternate.server_port}/v1'
            def run(url):
                current = self.saved(session)
                return [json.loads(line)['event'] for line in post(self.server, '/api/paired/step', {
                    'session_id': session['session_id'], 'expected_step': len(current['steps']), 'vllm_url': url}).splitlines()]
            events = run(other)
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            self.assertEqual(AlternateModel.hits, 5)  # trait, main twice, state router, updater
            self.assertEqual(events[0]['vllm_url'], other)
            self.assertEqual(self.saved(session)['steps'][0]['vllm_url'], other)
            log = json.loads(Path(self.saved(session)['attempts'][0]['saved_path']).read_text())
            self.assertEqual(log['input']['vllm_url'], other)
            events = run(original)
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            self.assertEqual(AlternateModel.hits, 5)
            current = self.saved(session)
            self.assertEqual(current['connection']['vllm_url'], normalize_url(original))
            self.assertEqual([a['vllm_url'] for a in current['attempts']], [other, normalize_url(original)])
            with self.assertRaises(HTTPError):
                run('http://127.0.0.1:99999')
            self.assertEqual(len(self.saved(session)['attempts']), 2)
            self.assertEqual(self.get('/api/config')['vllm_url'], original)
        finally:
            alternate.shutdown(); alternate.server_close()

    def test_empty_selection_skips_update_without_rewriting(self):
        Model.q2_routes=[];s=self.create();events=self.step(s)
        self.assertEqual(events[-1]['type'],'done');self.assertEqual(len(Model.requests),4)
        r=self.notes(s)['records'][0];self.assertEqual(r['notes_before'],r['notes_after']);self.assertEqual(r['diff'],[])
    def test_selected_state_can_stay_unchanged(self):
        Model.q2_updates=[];s=self.create();events=self.step(s)
        self.assertEqual(events[-1]['type'],'done');r=self.notes(s)['records'][0]
        self.assertTrue(r['state_routing']);self.assertEqual(r['notes_before'],r['notes_after'])
    def test_unselected_update_fails_without_committing_speech_or_notes(self):
        Model.q2_updates=[{'state':'task_alignment','content':'새 설명','reason':'근거'}]
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'error',events[-1])
        self.assertEqual(self.saved(s)['steps'],[]);self.assertEqual(self.notes(s)['records'],[])
        self.assertTrue(any(e.get('action')=='discarded' for e in events))
    def test_postprocessing_truncation_not_silently_saved(self):
        Model.q2_post_finish='length';s=self.create();events=self.step(s)
        self.assertEqual(events[-1]['type'],'error');self.assertEqual(self.notes(s)['records'],[])
    def test_standalone_session_key_uses_correct_storage_root(self):
        cfg=self.get('/api/config')
        events=[json.loads(line) for line in post(self.server,'/api/chat',{
            'experiment_version':'Q2','scenario':cfg['scenario'],'system_prompt':q2.specification()['system_prompt'],
            'generation':cfg['generation'],'messages':[{'role':'user','content':'편한 만큼 말씀해 주세요.'}]}).splitlines()]
        self.assertEqual(events[-1]['type'],'done',events[-1])
        row=next(r for r in self.get('/api/sessions')['sessions'] if r['kind']=='Q2')
        note=self.get('/api/client_notes?version=Q2&session_id='+row['session_id']+'&key='+row['key'])
        self.assertEqual(len(note['records']),1)
        self.assertIn('선택을 존중받았다고',note['content'])

    def test_named_tool_stop_accepted_but_length_rejected(self):
        Model.q2_tool_finish='stop';s=self.create();events=self.step(s)
        self.assertEqual(events[-1]['type'],'done',events[-1]);self.assertEqual(len(self.notes(s)['records']),1)
        Model.q2_tool_finish='length';s=self.create();events=self.step(s)
        self.assertEqual(events[-1]['type'],'error');self.assertEqual(self.notes(s)['records'],[])

    def test_prompt_editors_and_initial_note(self):
        versions=self.get('/api/versions')['versions'];self.assertIn('Q1a',[v['id'] for v in versions]);self.assertIn('Q2',[v['id'] for v in versions])
        for kind in ['system','trait_router','state_router','state_updater']:
            source=self.get('/api/prompts/source?role=client&version=Q2&kind='+kind)
            result=json.loads(post(self.server,'/api/prompts/save',{**source,'content':source['content']+'\nQ2_TEST_EDIT'}))
            self.assertTrue(result['saved']);self.assertTrue(result['content'].endswith('Q2_TEST_EDIT'))
        note=self.get('/api/client_notes?version=Q2');self.assertEqual(len(note['states']),10)
        self.assertEqual(set(note['states'].values()),{'현재 판단할 정보가 부족함.'})
        for name in ['client_notes.js','client_notes.css']:
            self.assertEqual(urlopen(f'http://127.0.0.1:{self.server.server_port}/'+name).status,200)

if __name__=='__main__':unittest.main()
