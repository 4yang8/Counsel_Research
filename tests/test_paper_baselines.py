"""Real HTTP/SSE integration, atomic resume, trust gates and role boundaries."""
import copy
import json
from pathlib import Path
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError
from urllib.request import urlopen

from test_lab import fixture, close, post
from client import registry
from client.paper_baselines import common, patientact, calmit, cars


def cars_reply():
    return {'Thinking':'PRIVATE_CARS_THINKING','Emotion':-7,
            'Intention':'PRIVATE_CARS_GOAL','nonverbal_behavior':'눈을 피함','Response':'조심스럽네요.',
            'Reasoning':{'topic':'일반 질문','branch':'no_matching_ccd','belief_checks':[],
                         'emotion_reason':'선호에 맞지 않아 경계한다.','intention_reason':'아직 조심스럽다.',
                         'expression_reason':'짧게 표현한다.'},'Grounding':[]}


class PaperModel(BaseHTTPRequestHandler):
    requests = []
    overrides = {}
    fail_stage = None
    delay_stage = None

    def log_message(self,*args):
        pass

    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(data)
        schema=data.get('structured_outputs',{}).get('json',{})
        stage=schema.get('title','basic')
        def sample(s):
            if 'enum' in s:
                return s['enum'][0]
            if s['type']=='object':
                return {k:sample(v) for k,v in s['properties'].items()}
            if s['type']=='array':
                if s.get('uniqueItems'):
                    return s['items']['enum'][:s.get('minItems',0)]
                return [sample(s['items']) for _ in range(s.get('minItems',0))]
            if s['type']=='boolean':
                return False
            if s['type'] in ('integer','number'):
                return max(0,s.get('minimum',0))
            return 'PRIVATE_STAGE_OUTPUT'
        result=sample(schema) if schema else '공개 상담사 응답'
        defaults={
            'topic_extraction':{'topics':['sensitive','open']},
            'reaction':{'reasoning':'PRIVATE_REACTION','reaction':'scared','intensity':'high'},
            'behavior':{'reasoning':'PRIVATE_BEHAVIOR','behavior':'resistance'},
            'resistance_pattern':{'reasoning':'PRIVATE_RESISTANCE','pattern':'minimal_talk'},
            'response':{'reasoning':'PRIVATE_RESPONSE_REASON','content':'아직 말하기 어렵네요.'},
            'trust_update':{'reasoning':'PRIVATE_TRUST','direction':'decreased_slightly'},
            'cognitive_response':cars_reply(),
            'client_appraisal':{'rating':'BAD','reason':'I feel pushed.'},
            'client_rapport':{'value':-.05},'counselor_rapport':{'value':.02},
            'client_action':{'value':'Hesitate'},'counselor_background':{'value':'preferences: slower pace'},
            'client_candidate_selector':{'index':1,'reason':'natural'},
            'counselor_candidate_selector':{'index':2,'reason':'natural'},
        }
        for role in ('client','counselor'):
            for i in range(1,4):
                defaults[role+'_candidate_'+str(i)]={'value':role+' public candidate '+str(i)}
        result=self.overrides.get(stage,defaults.get(stage,result))
        if isinstance(result,list):
            result=result.pop(0)
        if self.delay_stage==stage:
            time.sleep(.3)
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream')
        self.end_headers()
        chunk={'choices':[{'index':0,'delta':{'content':json.dumps(result,ensure_ascii=False) if schema else result},
                            'finish_reason':'length' if self.fail_stage==stage else 'stop'}],
               'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}
        try:
            self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
        except (BrokenPipeError,ConnectionResetError):
            pass


class PaperTests(unittest.TestCase):
    def setUp(self):
        PaperModel.requests=[]
        PaperModel.overrides={}
        PaperModel.fail_stage=PaperModel.delay_stage=None
        self.server,self.model,self.temp=fixture(model_handler=PaperModel)
        self.config=self.get('/api/config')

    def tearDown(self):
        close(self.server,self.model,self.temp)

    def get(self,path):
        return json.loads(urlopen('http://127.0.0.1:'+str(self.server.server_port)+path).read())

    def body(self,version='Q3patientact',**extra):
        body={'experiment_version':version,'counselor_version':'basic',
              'scenario':copy.deepcopy(self.config['scenario']),
              'system_prompt':registry.client_runtime(version).specification()['system_prompt'],
              'generation':self.config['generation'],
              'messages':[{'role':'user','content':'편하게 이야기해 주세요.'}]}
        body.update(extra)
        return body

    def create(self,version='Q3patientact',**extra):
        return json.loads(post(self.server,'/api/paired/create',self.body(version,**extra)))

    def step(self,session,**extra):
        return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{
            'session_id':session['session_id'],'expected_step':len(session['steps']),**extra}).splitlines()]

    def success(self,session):
        events=self.step(session)
        self.assertEqual(events[-1]['type'],'done',events[-1])
        return events[-1]['session']

    def state(self,session):
        return common.load({'_storage_dir':session['client_storage_dir']},session['client_version'],session['session_id'])

    def next_counselor(self,session):
        return json.loads(post(self.server,'/api/paired/manual',{'session_id':session['session_id'],
            'expected_step':len(session['steps']),'actor':'counselor','content':'천천히 해도 괜찮아요.'}))['session']

    def stages(self):
        return [r.get('structured_outputs',{}).get('json',{}).get('title') for r in PaperModel.requests]

    def native_patient(self):
        native=copy.deepcopy(patientact.characters()[0])
        native['profile']['demographics']['name']='PRIVATE_NATIVE_NAME'
        native['memory']['items']=[
            {'content':'NEVER_DISCLOSE_SECRET','field_path':'predisposing_factors.psychological',
             'activation_tags':['sensitive'],'disclosure_level':4,'generates_discomfort':True},
            {'content':'ACCESSIBLE_MEMORY','field_path':'automatic_thoughts',
             'activation_tags':['open'],'disclosure_level':2,'generates_discomfort':False}]
        return native

    def test_patientact_exact_order_gating_and_resume(self):
        session=self.create(paper_options={'profile':self.native_patient()})
        self.assertEqual(len(session['public_dialogue']),1)
        session=self.success(session)
        self.assertEqual(self.stages(),['topic_extraction','reaction','behavior','resistance_pattern','response','trust_update'])
        requests=json.dumps(PaperModel.requests,ensure_ascii=False)
        self.assertNotIn('NEVER_DISCLOSE_SECRET',requests)
        self.assertIn('ACCESSIBLE_MEMORY',requests)
        self.assertNotIn('아직 말하기 어렵네요.',json.dumps(PaperModel.requests[-1],ensure_ascii=False))
        self.assertEqual(self.state(session)['state']['trust'],2.25)
        self.assertNotIn('<signal>',json.dumps(session['public_dialogue']))
        self.assertNotIn('PRIVATE_',json.dumps(session['public_dialogue']))
        saved=self.get('/api/paired/session?session_id='+session['session_id'])
        resumed=self.success(self.next_counselor(saved))
        self.assertEqual(self.state(resumed)['state']['trust'],2)
        self.assertEqual(self.state(resumed)['revision'],2)
        self.assertEqual(session['steps'][-1]['usage']['total_tokens'],72)
        notes=self.get('/api/session_notes?paired=1&session_id='+session['session_id'])
        self.assertTrue(notes['notes'])

    def test_no_resistance_has_five_calls(self):
        PaperModel.overrides['behavior']={'reasoning':'neutral','behavior':'simple_response'}
        self.success(self.create())
        self.assertEqual(len(PaperModel.requests),5)
        self.assertNotIn('resistance_pattern',self.stages())

    def test_trust_update_failure_and_invalid_json_leave_no_state(self):
        session=self.create()
        PaperModel.fail_stage='trust_update'
        error=self.step(session)[-1]
        self.assertEqual(error['type'],'error')
        self.assertIn('finish_reason=length',error['error'])
        self.assertIn('max_tokens=',error['error'])
        saved=self.get('/api/paired/session?session_id='+session['session_id'])
        self.assertEqual(saved['steps'],[])
        self.assertNotIn('client_storage_dir',saved)
        self.assertEqual(list(Path(self.temp.name).rglob('paper_state.json')),[])
        PaperModel.fail_stage=None
        PaperModel.overrides['trust_update']={'reasoning':'bad','direction':'invented'}
        self.assertEqual(self.step(saved)[-1]['type'],'error')
        PaperModel.overrides={}
        self.assertEqual(self.state(self.success(saved))['state']['trust'],2.25)

    def test_failure_after_existing_turn_preserves_committed_state(self):
        first=self.success(self.create())
        before=self.state(first)
        next_session=self.next_counselor(first)
        PaperModel.fail_stage='response'
        self.assertEqual(self.step(next_session)[-1]['type'],'error')
        current=self.get('/api/paired/session?session_id='+first['session_id'])
        self.assertEqual(current['client_storage_dir'],first['client_storage_dir'])
        self.assertEqual(self.state(current),before)

    def test_cancel_late_stage_preserves_state(self):
        session=self.create()
        PaperModel.delay_stage='trust_update'
        results=[]
        worker=threading.Thread(target=lambda:results.extend(self.step(session,request_id='cancel_paper')))
        worker.start()
        deadline=time.monotonic()+10
        while 'trust_update' not in self.stages() and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIn('trust_update',self.stages())
        self.assertTrue(json.loads(post(self.server,'/api/cancel',{'request_id':'cancel_paper'}))['cancelled'])
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[-1]['type'],'cancelled')
        self.assertEqual(list(Path(self.temp.name).rglob('paper_state.json')),[])

    def test_cars_adapter_once_and_emotion_persists(self):
        first=self.success(self.create('Q3cars'))
        self.assertEqual(self.stages(),['session_ccd_preparation','cars_profile_review','cognitive_response','cars_response_review'])
        self.assertEqual(self.state(first)['state']['emotion'],43)
        self.assertIn('native_profile',self.state(first)['state'])
        PaperModel.requests=[]
        second=self.success(self.next_counselor(first))
        self.assertEqual(self.stages(),['cognitive_response','cars_response_review'])
        self.assertEqual(self.state(second)['state']['emotion'],36)
        self.assertNotIn('PRIVATE_CARS',json.dumps(second['public_dialogue']))
        self.assertFalse(self.state(second)['state']['emotion_examples_available'])

    def test_native_cars_avoids_preparation(self):
        native=dict(persona='p',background='b',main_ccd='m',session_ccd=[{'theme':'theme','automatic_thought':{'situation':'s','cognition':'c','reaction':'r'},'intermediate_belief':{'attitude':'a','assumption':'a','rule':'r'}}],preferences='prefs',
                    emotion_examples=['Example'])
        session=self.success(self.create('Q3cars',paper_options={'profile':native,'initial_emotion':3}))
        self.assertEqual(self.stages(),['cars_profile_review','cognitive_response','cars_response_review'])
        state=self.state(session)['state']
        self.assertEqual(state['emotion'],0)
        self.assertTrue(state['below_posthoc_emotion_threshold'])
        self.assertTrue(state['emotion_examples_available'])
        self.assertFalse(session.get('terminated',False))

    def test_cars_profile_repair_is_bounded_and_logged(self):
        bad={'issues':[{'kind':'emotion_scale','detail':'높은 점수를 분노로 정의함'}]}
        PaperModel.overrides['cars_profile_review']=[bad,{'issues':[]}]
        first=self.success(self.create('Q3cars'))
        self.assertEqual(self.stages().count('session_ccd_preparation'),2)
        self.assertEqual(self.stages().count('cars_profile_review'),2)
        requests=[r for r in PaperModel.requests if r['structured_outputs']['json']['title']=='session_ccd_preparation']
        self.assertIn('높은 점수를 분노로 정의함',json.dumps(requests[1],ensure_ascii=False))
        self.assertEqual(self.state(first)['state']['pipeline_version'],cars.PIPELINE_VERSION)

    def test_cars_repair_only_commits_accepted_response_and_delta(self):
        bad={'issues':[{'kind':'unsupported_fact','detail':'원문에 없는 수면 시간'}]}
        PaperModel.overrides['cars_response_review']=[bad,{'issues':[]}]
        first=self.success(self.create('Q3cars'))
        state=self.state(first)['state']
        self.assertEqual(state['emotion'],43)  # rejected candidate must not also change state
        self.assertEqual(len(state['validation']),2)
        self.assertEqual(state['validation'][0]['issues'],bad['issues'])
        self.assertEqual(state['validation'][1]['issues'],[])

    def test_cars_persistent_review_failure_preserves_committed_state(self):
        first=self.success(self.create('Q3cars'));before=self.state(first)
        PaperModel.requests=[]
        PaperModel.overrides['cars_response_review']={'issues':[{'kind':'unsupported_fact','detail':'근거 없는 행동'}]}
        self.assertEqual(self.step(self.next_counselor(first))[-1]['type'],'error')
        current=self.get('/api/paired/session?session_id='+first['session_id'])
        self.assertEqual(self.state(current),before)
        self.assertEqual(self.stages(),['cognitive_response','cars_response_review']*2)

    def test_cars_structural_error_cannot_be_approved_by_judge(self):
        reply=cars_reply()
        reply['Grounding']=[{'claim':'한두 시간씩 깨어 있음','source_id':'background','quote':'DOES_NOT_EXIST'}]
        PaperModel.overrides['cognitive_response']=reply
        session=self.create('Q3cars')
        self.assertEqual(self.step(session)[-1]['type'],'error')
        self.assertNotIn('cars_response_review',self.stages())
        self.assertEqual(self.stages().count('cognitive_response'),2)
        self.assertEqual(list(Path(self.temp.name).rglob('paper_state.json')),[])
        feedback=cars.structural_issues(reply,{'background':'EXACT_SOURCE'})[0]['detail']
        self.assertIn('DOES_NOT_EXIST',feedback)
        self.assertIn('EXACT_SOURCE',feedback)
        reply=cars_reply();reply['Reasoning']['branch']='matched_ccd'
        self.assertEqual(cars.structural_issues(reply,{} )[0]['kind'],'cognitive_branch')
        reply['Reasoning']['belief_checks']=[{'ccd_index':1,'situation_evidence':'s',
            'consistency':'contradicted','consistency_reason':'r','use_as_premise':True}]
        self.assertTrue(cars.structural_issues(reply,{}))

    def test_old_cars_prompt_bundle_requires_new_session(self):
        body=self.body('Q3cars')
        body['paper_prompts']={k:v for k,v in common.bundle('Q3cars').items() if k in ('cars_profile','cars_turn')}
        with self.assertRaisesRegex(ValueError,'새 대화'):
            common.configure({},body)

    def test_calmit_two_role_pipeline_and_private_boundary(self):
        native={'background':'PRIVATE_CALMIT_BACKGROUND','cognitive_model':'PRIVATE_CALMIT_COGNITION',
                'session_goal':'PRIVATE_CALMIT_GOAL'}
        first=self.success(self.create('Q3calmit',counselor_version='Q3calmit_counsel',
                           paper_options={'profile':native,'random_seed':0}))
        state=self.state(first)['state']
        self.assertEqual(first['public_dialogue'][-1]['content'],'client public candidate 2')
        self.assertEqual(state['rapport'],-.05)
        self.assertEqual(self.stages()[0],'client_summary')
        self.assertEqual(sum('client_candidate_' in s and 'selector' not in s for s in self.stages()),3)
        self.assertEqual(state['selection']['policy'],'llm_judge')
        PaperModel.requests=[]
        second=self.success(first)
        self.assertEqual(second['public_dialogue'][-1]['content'],'counselor public candidate 3')
        self.assertNotIn('PRIVATE_CALMIT',json.dumps(PaperModel.requests))
        self.assertNotIn('native_profile',json.dumps(PaperModel.requests))
        self.assertEqual(self.stages()[:7],['counselor_summary','counselor_background','counselor_emotion',
                          'counselor_change','counselor_goal','counselor_rapport','counselor_stagnation'])
        self.assertAlmostEqual(second['counselor_checkpoint']['rapport'],.02)

    def test_calmit_pivot_and_first_candidate_policy(self):
        first=self.success(self.create('Q3calmit',counselor_version='Q3calmit_counsel',
                                      paper_options={'selector':'first_candidate'}))
        self.assertNotIn('client_candidate_selector',self.stages())
        PaperModel.requests=[]
        PaperModel.overrides['counselor_stagnation']={'value':True}
        second=self.success(first)
        self.assertIn('counselor_pivot',self.stages())
        self.assertNotIn('counselor_strategy',self.stages())
        self.assertNotIn('counselor_candidate_selector',self.stages())
        self.assertEqual(second['public_dialogue'][-1]['content'],'counselor public candidate 1')

    def test_calmit_end_prevents_more_calls(self):
        PaperModel.overrides['client_action']={'value':'Terminate'}
        session=self.success(self.create('Q3calmit'))
        self.assertTrue(session['terminated'])
        count=len(PaperModel.requests)
        with self.assertRaises(HTTPError):
            self.step(session)
        self.assertEqual(len(PaperModel.requests),count)

    def test_calmit_invalid_delta_and_early_plan_fail_without_commit(self):
        for stage,value in [('client_rapport',{'value':.2}),('client_action',{'value':'Plan'})]:
            PaperModel.overrides={stage:value}
            session=self.create('Q3calmit')
            self.assertEqual(self.step(session)[-1]['type'],'error')
        self.assertEqual(list(Path(self.temp.name).rglob('paper_state.json')),[])

    def test_korean_prefixes_no_update_and_termination(self):
        PaperModel.overrides.update({
            'client_candidate_2':{'value':'내담자: 아직은 조금 망설여져요.'},
            'counselor_candidate_3':{'value':'상담사: <END>'},
            'counselor_background':{'value':'갱신된 배경:\n(갱신 없음)'}})
        first=self.success(self.create('Q3calmit',counselor_version='Q3calmit_counsel'))
        self.assertEqual(first['public_dialogue'][-1]['content'],'아직은 조금 망설여져요.')
        second=self.success(first)
        self.assertEqual(second['public_dialogue'][-1]['content'],'<END>')
        self.assertEqual(second['counselor_checkpoint']['background'],'')
        self.assertTrue(second['terminated'])
        self.assertEqual(first['settings']['paper_provenance']['prompt_language'],'ko')
        for request in PaperModel.requests:
            text=json.dumps(request['messages'],ensure_ascii=False)
            self.assertIn('출력 규약:',text)
            self.assertNotIn('Transport contract:',text)
            self.assertNotIn('Perform the task.',text)

    def test_korean_defaults_keep_taxonomy_identifiers_and_tags(self):
        native=self.native_patient()
        self.success(self.create(paper_options={'profile':native}))
        request=json.dumps(PaperModel.requests,ensure_ascii=False)
        self.assertIn('상담사의 가장 최근 메시지',request)
        self.assertIn('상담사: 편하게 이야기해 주세요.',request)
        self.assertIn('신뢰도에 따른 공개 제한',request)
        self.assertIn('sensitive',request)
        self.assertNotIn('NEVER_DISCLOSE_SECRET',request)
        for group in ('REACTIONS','BEHAVIORS','RESISTANCE_PATTERNS'):
            for description in patientact.CONSTANTS[group].values():
                self.assertRegex(description,'[가-힣]')
        for version in common.VERSIONS:
            for prompt in common.bundle(version).values():
                self.assertRegex(prompt,'[가-힣]')

    def test_standalone_discovery_and_continuation(self):
        body=self.body()
        first=[json.loads(line) for line in post(self.server,'/api/chat',body).splitlines()]
        self.assertEqual(first[-1]['type'],'done',first[-1])
        sid=first[0]['session_id']
        body['session_id']=sid
        second=[json.loads(line) for line in post(self.server,'/api/chat',body).splitlines()]
        self.assertEqual(second[-1]['type'],'done',second[-1])
        self.assertEqual(second[-1]['paper_state']['trust'],2)
        found=self.get('/api/sessions')['sessions']
        self.assertTrue(any(r['session_id']==sid and r['kind']=='Q3patientact' for r in found))
        self.assertEqual(len(second[-1]['history']),4)

    def test_invalid_options_fail_before_calls(self):
        for options in ({'profile_index':40},{'selector':'original_ranker'},{'initial_rapport':9}):
            with self.assertRaises(HTTPError):
                self.create(paper_options=options)
        self.assertEqual(PaperModel.requests,[])

    def test_exact_case_insensitive_gate_and_limits(self):
        items=self.native_patient()['memory']['items']
        ctx=patientact.retrieve(items,['SENSITIVE','OPEN'],2.5)
        self.assertTrue(ctx.blocked)
        self.assertEqual(ctx.items,['[thought] ACCESSIBLE_MEMORY'])
        self.assertEqual(patientact.retrieve(items,['sensit'],4).items,[])
        self.assertIn('[memory] NEVER_DISCLOSE_SECRET',patientact.retrieve(items,['sensitive'],4).items)
        for native in patientact.characters():
            patientact.character({'profile':native,'profile_index':0})
            common.render(common.bundle('Q3patientact'),'patientact_sys_prompt',profile=native['profile'])


if __name__=='__main__':
    if '--serve' in __import__('sys').argv:
        server,model,temp=fixture(model_handler=PaperModel)
        print('http://127.0.0.1:'+str(server.server_port),flush=True)
        threading.Event().wait()
    else:
        unittest.main()
