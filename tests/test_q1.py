"""Q1 transport, information boundaries, persistence and rollback over HTTP."""
import copy
import json
from pathlib import Path
import threading
import time
import unittest
from urllib.request import urlopen

from test_lab import Model, close, fixture, post
from client.Q1 import runtime as q1


class Q1Tests(unittest.TestCase):
    def setUp(self):
        self.server, self.model, self.temp = fixture(prompt_editing=True)
        Model.requests.clear()
        Model.q1_router_response = Model.q1_update = None
        Model.q1_router_finish = Model.q1_main_finish = 'stop'
        Model.q1_skip_update = False
        Model.p1_delay = 0

    def tearDown(self):
        Model.q1_router_response = Model.q1_update = None
        Model.q1_router_finish = Model.q1_main_finish = 'stop'
        Model.q1_skip_update = False
        Model.p1_delay = 0
        close(self.server, self.model, self.temp)

    def get(self, path):
        return json.loads(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path).read())

    def create(self, **settings):
        config = self.get('/api/config')
        scenario = copy.deepcopy(config['scenario'])
        scenario['profile'] = {'background':'BASIC_Q1', 'presenting_problem':'걱정을 상담하고 싶다.',
                               'traits':['SELECTED_Q1_TRAIT','UNSELECTED_Q1_TRAIT'],
                               'childhood_data':'PRIVATE_UNUSED_MEMORY'}
        request = {'experiment_version':'Q1','counselor_version':'basic','scenario':scenario,
                   'system_prompt':q1.specification()['system_prompt'],
                   'generation':config['generation'],'messages':[{'role':'user','content':'편한 만큼 이야기해 주세요.'}]}
        request.update(settings)
        return json.loads(post(self.server,'/api/paired/create',request))

    def step(self, session, **extra):
        return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{
            'session_id':session['session_id'],'expected_step':len(session['steps']),**extra}).splitlines()]

    def next_counselor(self, session, content='서두르지 않아도 괜찮아요.'):
        return json.loads(post(self.server,'/api/paired/manual',{'session_id':session['session_id'],
            'expected_step':len(session['steps']),'actor':'counselor','content':content}))['session']

    def stored(self, session):
        return Path(session['client_storage_dir']) / 'Q1' / session['session_id']

    def test_routing_precedes_main_and_only_original_selected_traits_enter_main(self):
        events = self.step(self.create(client_reasoning_budgets={'main':256,'subagent':512}))
        self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertEqual(len(Model.requests),3)
        router, main, resumed = Model.requests
        self.assertEqual(router['thinking_token_budget'],512)
        self.assertEqual(main['thinking_token_budget'],256)
        self.assertEqual(resumed['thinking_token_budget'],256)
        self.assertIn('UNSELECTED_Q1_TRAIT',router['messages'][1]['content'])
        self.assertIn('SELECTED_Q1_TRAIT',main['messages'][1]['content'])
        for value in ('UNSELECTED_Q1_TRAIT','PRIVATE_UNUSED_MEMORY','## states','## taxonomy',
                      'taxonomy에서 느낌','다음 턴 자동 주입되지 않는 appraisal'):
            self.assertNotIn(value,main['messages'][1]['content'])
        self.assertEqual(resumed['messages'][-2]['reasoning'],'Q1_MAIN_REASON')
        self.assertNotIn('_reasoning_budget_role',router)
        self.assertEqual(events[-1]['usage'],{'prompt_tokens':300,'completion_tokens':90,'total_tokens':390})
        session=events[-1]['session']
        path=self.stored(session)
        self.assertIn('PRIVATE_Q1_NOTE',(path/'inner_notes.md').read_text())
        records=list((path/'appraisals').glob('*.json'))
        self.assertEqual(len(records),1)
        record=json.loads(records[0].read_text())
        self.assertEqual(record['utterance'],events[-1]['response'])
        self.assertNotIn('PRIVATE_Q1_NOTE',session['public_dialogue'][-1]['content'])
        q1_events=[e for e in events if e['type']=='version_event']
        self.assertTrue(any(e['action']=='staged' for e in q1_events))
        self.assertTrue(any(e['action']=='updated' for e in q1_events))
        self.assertTrue((self.server.storage_dir/'paired/logs'/session['session_id']).is_dir())

    def test_new_turn_reads_latest_file_without_previous_appraisal_reasoning_or_tools(self):
        session=self.step(self.create())[-1]['session']
        path=self.stored(session)/'inner_notes.md'
        path.write_text('MANUALLY_REFRESHED_Q1_NOTE',encoding='utf-8')
        session=self.next_counselor(session)
        Model.requests.clear()
        Model.q1_router_response='{"selections":[]}'
        events=self.step(session)
        self.assertEqual(events[-1]['type'],'done',events[-1])
        router,main,resumed=Model.requests
        self.assertIn('MANUALLY_REFRESHED_Q1_NOTE',router['messages'][1]['content'])
        self.assertIn('MANUALLY_REFRESHED_Q1_NOTE',main['messages'][1]['content'])
        self.assertNotIn('SELECTED_Q1_TRAIT',main['messages'][1]['content'])
        self.assertNotIn('Q1_MAIN_REASON',main['messages'][1]['content'])
        self.assertNotIn('stage_client_update',main['messages'][1]['content'])
        self.assertNotIn('상담사의 공개 발화와 이전 내면 노트.',main['messages'][1]['content'])
        self.assertEqual(len(list((self.stored(events[-1]['session'])/'appraisals').glob('*.json'))),2)

    def test_null_keeps_note_but_records_new_appraisal(self):
        Model.q1_update={'appraisal':dict(zip(q1.APPRAISAL_FIELDS,['의미','감정','근거',''])), 'inner_notes':None}
        events=self.step(self.create())
        self.assertEqual(events[-1]['type'],'done',events[-1])
        path=self.stored(events[-1]['session'])
        self.assertEqual((path/'inner_notes.md').read_text(),(q1.HERE/'inner_notes_template.md').read_text())
        self.assertEqual(len(list((path/'appraisals').glob('*.json'))),1)

    def test_length_and_missing_stage_discard_changes_and_allow_retry(self):
        for mode in ('length','missing'):
            with self.subTest(mode=mode):
                Model.q1_main_finish='length' if mode=='length' else 'stop'
                Model.q1_skip_update=mode=='missing'
                session=self.create()
                events=self.step(session)
                self.assertEqual(events[-1]['type'],'error',events[-1])
                stored=self.get('/api/paired/session?session_id='+session['session_id'])
                self.assertEqual(stored['steps'],[])
                self.assertNotIn('client_storage_dir',stored)
                base=self.server.storage_dir/'paired'/session['session_id']/'client_attempts'
                self.assertEqual(list(base.rglob('inner_notes.md')),[])
                self.assertEqual(list(base.rglob('appraisals/*.json')),[])
                Model.q1_main_finish='stop';Model.q1_skip_update=False
                retry=self.step(stored)
                self.assertEqual(retry[-1]['type'],'done',retry[-1])

    def test_invalid_router_id_or_truncated_router_never_calls_main(self):
        for response,finish in (('{"selections":[{"trait_id":"missing","reason":"x"}]}','stop'),
                                ('{"selections":[]}','length')):
            Model.q1_router_response=response;Model.q1_router_finish=finish
            Model.requests.clear()
            events=self.step(self.create())
            self.assertEqual(events[-1]['type'],'error')
            self.assertEqual(len(Model.requests),1)

    def test_cancellation_during_main_does_not_commit(self):
        session=self.create();Model.p1_delay=.4
        collected=[]
        worker=threading.Thread(target=lambda:collected.extend(self.step(session,request_id='q1_cancel')))
        worker.start()
        deadline=time.monotonic()+10
        while len(Model.requests)<3 and time.monotonic()<deadline:time.sleep(.02)
        self.assertGreaterEqual(len(Model.requests),3)
        self.assertTrue(json.loads(post(self.server,'/api/cancel',{'request_id':'q1_cancel'}))['cancelled'])
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(collected[-1]['type'],'cancelled')
        base=self.server.storage_dir/'paired'/session['session_id']/'client_attempts'
        self.assertEqual(list(base.rglob('inner_notes.md')),[])
        self.assertEqual(list(base.rglob('appraisals/*.json')),[])

    def test_private_information_does_not_enter_counselor_requests(self):
        session=self.step(self.create())[-1]['session']
        Model.requests.clear()
        events=self.step(session)
        self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertTrue(Model.requests)
        for request in Model.requests:
            text=json.dumps(request,ensure_ascii=False)
            for private in ('PRIVATE_Q1_NOTE','SELECTED_Q1_TRAIT','PRIVATE_UNUSED_MEMORY','stage_client_update','Q1_ROUTER_REASON'):
                self.assertNotIn(private,text)

    def test_router_prompt_edit_is_isolated_and_used_on_next_turn(self):
        path='/api/prompts/source?role=client&version=Q1&kind=trait_router'
        before=self.get(path)
        self.assertTrue(before['path'].startswith('/tmp/'))
        session=self.step(self.create())[-1]['session']
        result=json.loads(post(self.server,'/api/prompts/save',{'role':'client','version':'Q1','kind':'trait_router',
            'revision':before['revision'],'content':before['content']+'\nQ1_ROUTER_EDIT'}))
        self.assertTrue(result['saved'])
        session=self.next_counselor(session)
        Model.requests.clear()
        events=self.step(session)
        self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertIn('Q1_ROUTER_EDIT',Model.requests[0]['messages'][0]['content'])
        self.assertNotIn('Q1_ROUTER_EDIT',Model.requests[1]['messages'][0]['content'])

    def test_standalone_is_discoverable_and_can_continue(self):
        config=self.get('/api/config')
        body={'experiment_version':'Q1','scenario':config['scenario'],'system_prompt':q1.specification()['system_prompt'],
              'generation':config['generation'],'messages':[{'role':'user','content':'편한 만큼 말씀하세요.'}]}
        first=[json.loads(line) for line in post(self.server,'/api/chat',body).splitlines()]
        self.assertEqual(first[-1]['type'],'done',first[-1])
        body['session_id']=first[0]['session_id']
        body['messages']=[{'role':'user','content':'어떤 부분이 아직 조심스러운가요?'}]
        second=[json.loads(line) for line in post(self.server,'/api/chat',body).splitlines()]
        self.assertEqual(second[-1]['type'],'done',second[-1])
        rows=self.get('/api/sessions')['sessions']
        saved=next(row for row in rows if row['kind']=='Q1')
        self.assertEqual(saved['count'],2)


if __name__=='__main__':unittest.main()
