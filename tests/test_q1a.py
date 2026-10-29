"""Q1a HTTP lifecycle, advisory review, private state and terminal selection."""
import copy
import json
from pathlib import Path
import threading
import time
import unittest
from urllib.request import urlopen
from test_lab import Model, close, fixture, post
from client.Q1 import runtime as q1
from client.Q1a import runtime as q1a


class Q1aTests(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        self.reset_model()

    def reset_model(self):
        Model.requests.clear();Model.q1a_actions=Model.q1a_review_response=Model.q1_router_response=None
        Model.q1a_main_finish='tool_calls';Model.q1a_review_finish=Model.q1_router_finish='stop';Model.p1_delay=0

    def tearDown(self):
        self.reset_model();close(self.server,self.model,self.temp)

    def get(self,path):
        return json.loads(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path).read())

    def create(self,**settings):
        cfg=self.get('/api/config');scenario=copy.deepcopy(cfg['scenario'])
        scenario['profile']={'background':'BASIC_Q1a','traits':['SELECTED_Q1a_TRAIT','UNSELECTED_Q1a_TRAIT'],
                             'core_belief':{'category':'DOMAIN_Q1a','statement':'BELIEF_Q1a'},'childhood_data':'PRIVATE_UNUSED_MEMORY'}
        body={'experiment_version':'Q1a','counselor_version':'basic','scenario':scenario,
              'system_prompt':q1a.specification()['system_prompt'],'generation':{**cfg['generation'],'max_tokens':4096},
              'messages':[{'role':'user','content':'편한 만큼 말씀해 주세요.'}]}
        body.update(settings)
        return json.loads(post(self.server,'/api/paired/create',body))

    def step(self,session,**extra):
        return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{
            'session_id':session['session_id'],'expected_step':len(session['steps']),**extra}).splitlines()]

    def staged(self,utterance='최종 공개 발화',note='FINAL_Q1a_NOTE',revises=None):
        return {'name':'stage_client_update','arguments':{'utterance':utterance,'inner_notes':note,'revises':revises,
            'appraisal':dict(zip(q1a.APPRAISAL_FIELDS,['의미','감정','근거','']))}}

    def finish(self,draft='draft_1'):
        return {'name':'finalize_client_draft','arguments':{'draft_id':draft,'reason':'현재 맥락과 내면에 맞는 반응입니다.'}}

    def storage(self,session):
        return Path(session['client_storage_dir'])/'Q1a'/session['session_id']

    def test_original_q1_split_and_routing_reason_preserved_and_defaults_unlimited(self):
        profile={'background':'basic','core_belief':{'category':'x','statement':'y'},'traits':['z']}
        self.assertEqual(q1a.split_profile(profile),q1.split_profile(profile))
        Model.q1a_actions=[self.staged(),self.finish()]
        events=self.step(self.create())
        self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertEqual(len(Model.requests),3)  # router, stage, terminal selection; no post-final call
        router,main,final=Model.requests
        self.assertIn('UNSELECTED_Q1a_TRAIT',router['messages'][1]['content'])
        self.assertIn('SELECTED_Q1a_TRAIT',main['messages'][1]['content'])
        self.assertIn('이번 상황의 평가에 대한 걱정과 관련됨',main['messages'][1]['content'])
        for text in ['UNSELECTED_Q1a_TRAIT','PRIVATE_UNUSED_MEMORY','## states','## taxonomy']:
            self.assertNotIn(text,main['messages'][1]['content'])
        self.assertTrue(all('thinking_token_budget' not in req for req in Model.requests))
        self.assertEqual(final['messages'][-2]['reasoning'],'Q1a_MAIN_REASON')
        self.assertEqual(events[-1]['response'],'최종 공개 발화')
        self.assertFalse(any(e['type']=='agent_event' and e.get('agent_kind')=='draft_reviewer' for e in events))

    def test_review_revision_finalization_commits_exact_chosen_bundle_and_usage(self):
        events=self.step(self.create(client_reasoning_budgets={'main':256,'subagent':768}))
        self.assertEqual(events[-1]['type'],'done',events[-1])
        session=events[-1]['session'];folder=self.storage(session)
        record=json.loads(next((folder/'appraisals').glob('*.json')).read_text())
        self.assertEqual(record['finalized_draft_id'],'draft_2')
        self.assertEqual(record['utterance'],events[-1]['response'])
        self.assertEqual(record['notes_after'],(folder/'inner_notes.md').read_text())
        self.assertEqual(record['notes_after'],'PRIVATE_Q1a_FINAL_NOTE')
        self.assertEqual(len(record['drafts']),2);self.assertEqual(len(record['reviews']),1)
        self.assertEqual(record['drafts'][1]['revises'],'draft_1')
        self.assertEqual([a['action'] for a in record['actions']],['stage','review','stage','finalize'])
        self.assertEqual(len(Model.requests),6)
        self.assertEqual(events[-1]['usage'],{'prompt_tokens':600,'completion_tokens':180,'total_tokens':780})
        reviewer=next(e for e in events if e['type']=='agent_event' and e.get('agent_kind')=='draft_reviewer' and e['event']['type']=='request')
        self.assertEqual(reviewer['parent_call_id'],'q1a_call_1')
        review_request=reviewer['event']['payload']
        self.assertEqual(review_request['thinking_token_budget'],768)
        self.assertEqual(review_request['model'],Model.requests[0]['model'])
        self.assertNotIn('UNSELECTED_Q1a_TRAIT',json.dumps(review_request,ensure_ascii=False))
        self.assertTrue(all(req['thinking_token_budget']==256 for req in Model.requests if req.get('tools')))
        Model.requests.clear();counselor_events=self.step(session)
        self.assertEqual(counselor_events[-1]['type'],'done',counselor_events[-1])
        for req in Model.requests:
            for private in ['PRIVATE_Q1a_DRAFT_NOTE','PRIVATE_Q1a_FINAL_NOTE','Q1a_REVIEW_REASON','SELECTED_Q1a_TRAIT','review_client_draft']:
                self.assertNotIn(private,json.dumps(req,ensure_ascii=False))

    def test_review_is_advisory_can_choose_unmodified_or_earlier_candidate(self):
        Model.q1a_actions=[self.staged('처음 발화','CHOSEN_NOTE'),self.staged('다른 후보','UNSELECTED_NOTE'),
            {'name':'review_client_draft','arguments':{'draft_id':'draft_1','question':'draft_2와 비교해 주세요.'}},self.finish('draft_1')]
        events=self.step(self.create())
        self.assertEqual(events[-1]['type'],'done',events[-1]);self.assertEqual(events[-1]['response'],'처음 발화')
        folder=self.storage(events[-1]['session']);record=json.loads(next((folder/'appraisals').glob('*.json')).read_text())
        self.assertEqual(record['reviews'][0]['assessment'],'revision_suggested')
        self.assertEqual((folder/'inner_notes.md').read_text(),'CHOSEN_NOTE')
        reviewer=next(r for r in Model.requests if '# 검토할 내담자 초안\n' in r['messages'][-1]['content'])
        self.assertIn('# 요청에서 지정한 비교 후보',reviewer['messages'][-1]['content'])

    def test_disabled_optional_review_and_unknown_id_can_be_corrected(self):
        Model.q1a_actions=[self.staged(),self.finish('missing'),self.finish()]
        events=self.step(self.create(enabled_tools=list(q1a.REQUIRED_TOOLS)))
        self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertTrue(any(e['type']=='tool_result' and e['is_error'] for e in events))
        self.assertFalse(any(r.get('tools') and any(t['function']['name']=='review_client_draft' for t in r['tools']) for r in Model.requests))

    def test_execution_limit_and_unfinalized_text_and_truncation_do_not_commit(self):
        scenarios=[('limit',[self.staged() for _ in range(8)],'tool_calls'),('no_finish',[self.staged()],'tool_calls'),
                   ('truncated',[self.staged()],'length')]
        for label,actions,finish in scenarios:
            with self.subTest(label=label):
                Model.q1a_actions=actions;Model.q1a_main_finish=finish
                session=self.create();events=self.step(session)
                self.assertEqual(events[-1]['type'],'error',events[-1])
                saved=self.get('/api/paired/session?session_id='+session['session_id']);self.assertEqual(saved['steps'],[])
                base=self.server.storage_dir/'paired'/session['session_id']/'client_attempts'
                self.assertEqual(list(base.rglob('inner_notes.md')),[]);self.assertEqual(list(base.rglob('appraisals/*.json')),[])
        Model.q1a_main_finish='tool_calls'

    def test_cancellation_does_not_publish_or_commit(self):
        session=self.create();Model.p1_delay=.3;events=[]
        worker=threading.Thread(target=lambda:events.extend(self.step(session,request_id='q1a_cancel')));worker.start()
        deadline=time.monotonic()+10
        while len(Model.requests)<3 and time.monotonic()<deadline:time.sleep(.02)
        self.assertGreaterEqual(len(Model.requests),3)
        self.assertTrue(json.loads(post(self.server,'/api/cancel',{'request_id':'q1a_cancel'}))['cancelled'])
        worker.join(10);self.assertFalse(worker.is_alive());self.assertEqual(events[-1]['type'],'cancelled')
        saved=self.get('/api/paired/session?session_id='+session['session_id']);self.assertEqual(saved['steps'],[])

    def test_next_turn_reads_chosen_note_without_old_drafts_or_reviews(self):
        events=self.step(self.create());session=events[-1]['session']
        session=json.loads(post(self.server,'/api/paired/manual',{'session_id':session['session_id'],
            'expected_step':len(session['steps']),'actor':'counselor','content':'지금은 어떤 마음이 드시나요?'}))['session']
        Model.requests.clear();Model.q1a_actions=[self.staged(note=None),self.finish()]
        events=self.step(session);self.assertEqual(events[-1]['type'],'done',events[-1])
        text=Model.requests[1]['messages'][1]['content']
        self.assertIn('PRIVATE_Q1a_FINAL_NOTE',text)
        for old in ['PRIVATE_Q1a_DRAFT_NOTE','Q1a_REVIEW_REASON','Q1a_MAIN_REASON','draft_1','초안이 안도만 표현함']:
            self.assertNotIn(old,text)
        self.assertEqual((self.storage(events[-1]['session'])/'inner_notes.md').read_text(),'PRIVATE_Q1a_FINAL_NOTE')

    def test_reviewer_prompt_file_isolated_editable_and_used(self):
        source=self.get('/api/prompts/source?role=client&version=Q1a&kind=draft_reviewer')
        self.assertTrue(source['path'].startswith('/tmp/'))
        result=json.loads(post(self.server,'/api/prompts/save',{'role':'client','version':'Q1a','kind':'draft_reviewer',
            'revision':source['revision'],'content':source['content']+'\nEDITED_Q1a_REVIEWER'}));self.assertTrue(result['saved'])
        events=self.step(self.create());self.assertEqual(events[-1]['type'],'done',events[-1])
        reviewer=next(r for r in Model.requests if '# 검토할 내담자 초안\n' in r['messages'][-1]['content'])
        self.assertTrue(reviewer['messages'][0]['content'].endswith('EDITED_Q1a_REVIEWER'))

    def test_standalone_completion_and_discovery(self):
        cfg=self.get('/api/config');Model.q1a_actions=[self.staged(),self.finish()]
        body={'experiment_version':'Q1a','scenario':cfg['scenario'],'system_prompt':q1a.specification()['system_prompt'],
              'generation':cfg['generation'],'messages':[{'role':'user','content':'편한 만큼 말씀하세요.'}]}
        events=[json.loads(l) for l in post(self.server,'/api/chat',body).splitlines()]
        self.assertEqual(events[-1]['type'],'done',events[-1]);self.assertEqual(events[-1]['response'],'최종 공개 발화')
        self.assertTrue(any(row['kind']=='Q1a' for row in self.get('/api/sessions')['sessions']))


if __name__=='__main__':unittest.main()
