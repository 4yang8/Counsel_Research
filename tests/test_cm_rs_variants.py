"""Closed-loop mock-server tests for distinct pipelines and private-input isolation."""
import copy
import json
from pathlib import Path
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler
from urllib.request import urlopen
from unittest.mock import patch

from test_lab import fixture, close, post
from client.memory_access import CM_VERSIONS
from client.memory_access.runtime import load
from client.memory_access.cm_runtime import memory_access, merge_state
from counselor.rs_variants import VERSIONS as RS_VERSIONS
from counselor.rs_variants.runtime import update_ledger, commit_ledger, freeze
from runtime import profile_batch

PUBLIC='지금은 더 생각해 보고 싶어요.'


def sample(s):
    if 'enum' in s:return s['enum'][0]
    if s['type']=='object':return {k:sample(v) for k,v in s['properties'].items()}
    if s['type']=='array':return []
    if s['type']=='boolean':return False
    if s['type']=='integer':return s.get('minimum',0)
    return '기록'


class VariantModel(BaseHTTPRequestHandler):
    requests=[];lock=threading.Lock();fail_review=False
    def log_message(self,*args):pass
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with self.lock:type(self).requests.append(copy.deepcopy(request))
        user=next((m['content'] for m in request.get('messages',[]) if m['role']=='user'),'')
        try:source=json.loads(user[user.index('{'):])
        except (ValueError,TypeError):source={}
        public=source.get('public_dialogue',[])
        latest=sum(m['role']=='client' for m in public) if public else user.count('] 내담자: ')
        turn=latest+1
        schema=request.get('structured_outputs',{}).get('json',{})
        name=None
        if request.get('tools'):
            names=[t['function']['name'] for t in request['tools']]
            previous=[c['function']['name'] for m in request['messages'] for c in m.get('tool_calls',[])]
            if 'finish_counselor_utterance' in names:
                if 'list_open_events' not in previous and 'list_open_events' in names:name='list_open_events'
                elif 'read_public_turns' not in previous and 'read_public_turns' in names:name='read_public_turns'
                elif 'update_event' in names and latest>=2:name='update_event'
                else:name='finish_counselor_utterance'
            elif 'read_private_turns' in names and latest and 'read_private_turns' not in previous:name='read_private_turns'
            else:name=names[0]
            schema=next(t['function']['parameters'] for t in request['tools'] if t['function']['name']==name)
        value=sample(schema) if schema else {}
        props=schema.get('properties',{})
        if 'selections' in props:
            value={'selections':[{'trait_id':source['traits'][0]['trait_id'],'background_ids':[],'relevance':'공개 맥락'}] if source['traits'] else []}
        elif 'turns' in props and 'private_turns' in source:value={'turns':[r['turn'] for r in source['private_turns'][:3]]}
        elif 'recommend' in props:
            value.update(marker={'found':False,'kind':'none','quote':''},recommend='check_in' if latest>=2 else 'continue',
                         target_turn=2 if latest>=2 else 0,advice='CONTINUE_DIRECTION_SENTINEL' if latest<2 else '대상 발언을 확인하세요.')
        elif 'move' in props:
            value.update(move='suggest' if latest<2 else 'check_in',rupture_turn=0,check_turn=2 if latest>=2 else 0,
                         utterance=f'상담사 발화 {turn}')
            if 'marker' in props:value['marker']={'found':False,'kind':'none','quote':''}
        elif 'action' in props:
            intervention='제안·해석·실천 제안 후보' in request['messages'][0]['content']
            value.update(action='suggest' if intervention else 'check_in' if latest>=2 else 'none',
                         target_turn=0 if intervention or latest<2 else 2,proposal='후보',reason='공개 근거',evidence=[])
        elif 'decision' in props:
            if self.fail_review:
                value={'invalid':True}
            else:value={'decision':'revise','issues':[{'problem':'표현 점검','correction':'현재 의도를 유지하세요.'}]}
        elif 'updates' in props:
            if 'current_inner' in source:
                value={'updates':[{'origin_turn':source['current_turn'],'current_meaning':f'STATE_SECRET_T{source["current_turn"]}',
                                   'disclosure':'withheld','evidence_turns':[source['current_turn']],'change_reason':'현재 근거'}]}
            elif source.get('events'):
                value={'updates':[{'event_turn':source['events'][0]['event_turn'],'status':'unconfirmed','public_response':PUBLIC,
                                   'unresolved':'수용 정도','evidence':[{'turn':latest,'role':'client','quote':PUBLIC}]}]}
        if 'appraisal' in value:value['appraisal']['interpretation']=f'SECRET_T{turn}'
        if 'private_position' in value:value['private_position']['withheld_content']=f'SECRET_T{turn}_WITHHELD'
        if 'utterance' in props and 'move' not in props:value={'utterance':PUBLIC}
        if name=='read_private_turns':value={'turns':[latest]}
        if name=='read_public_turns':value={'turns':[1]}
        if name=='update_event':value={'event_turn':2,'status':'unconfirmed','public_response':PUBLIC,'unresolved':'수용 정도',
                                      'evidence':[{'turn':latest,'role':'client','quote':PUBLIC}]}
        if name:
            delta={'tool_calls':[{'index':0,'id':'call_'+uuid.uuid4().hex,'type':'function','function':{'name':name,'arguments':json.dumps(value,ensure_ascii=False)}}]}
            finish='tool_calls'
        else:delta={'content':json.dumps(value,ensure_ascii=False),'reasoning_content':'MOCK_REASON'};finish='stop'
        frame={'choices':[{'index':0,'delta':delta,'finish_reason':finish}], 'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
        try:
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(('data: '+json.dumps(frame)+'\n\ndata: [DONE]\n\n').encode())
        except (BrokenPipeError,ConnectionResetError):pass


class VariantPipelineTests(unittest.TestCase):
    def setUp(self):
        VariantModel.requests=[];VariantModel.fail_review=False
        self.server,self.model,self.temp=fixture(model_handler=VariantModel)
    def tearDown(self):
        for worker in self.server.batch_workers.values():
            worker['flag'].set();worker['thread'].join(10)
        close(self.server,self.model,self.temp)
    def get(self,path):return json.loads(urlopen('http://127.0.0.1:'+str(self.server.server_port)+path).read())
    def job(self,client,counselor='P3rs'):
        body={'profile_directory':profile_batch.DEFAULT_DIRECTORY,'filenames':['gold_ccd_profile_001.json'],
              'mode':'compare','compare_version':client,'counselor_version':counselor,'turns':3,'fork_turn':2,'concurrency':1,'retries':0}
        job=json.loads(post(self.server,'/api/profile_batch/create',body))
        post(self.server,'/api/profile_batch/start',{'id':job['id']})
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            job=self.get('/api/profile_batch/job?id='+job['id'])
            if job['status'] not in ('running','stopping'):return job
            time.sleep(.03)
        self.fail('batch timeout')
    def detail(self,job,branch):
        row=next(r for r in job['rows'] if r['branch']==branch)
        data=self.get('/api/profile_batch/detail?id='+job['id']+'&session_id='+row['session_id'])
        session=data['session']
        data['records']=load({'_storage_dir':session['client_storage_dir']},session['client_version'],session['session_id'])['records']
        return data

    def test_all_clients_fork_and_gate_all_historical_inputs(self):
        for version in CM_VERSIONS:
            with self.subTest(version=version):
                job=self.job(version)
                self.assertEqual(job['status'],'completed',job['rows'])
                provided=self.detail(job,'provided');withheld=self.detail(job,'withheld')
                self.assertEqual(provided['records'][0]['inner'],withheld['records'][0]['inner'])
                for branch,data in [('provided',provided),('withheld',withheld)]:
                    rec=data['records'][-1]
                    self.assertEqual(rec['memory_mode'],branch)
                    if branch=='withheld':
                        self.assertEqual(rec['memory_input'],[])
                        self.assertEqual(rec.get('state_input',[]),[])
                        for req in rec['model_requests']:
                            text=json.dumps(req,ensure_ascii=False)
                            for t in (1,2):self.assertNotIn(f'SECRET_T{t}',text)
                    else:self.assertTrue(rec['memory_input'])
                stages=[r['stage'] for r in provided['records'][-1]['model_requests']]
                expected={'Q3cm_split':['reaction','expression'],'Q3cm_temporal':['temporal'],'Q3cm_state':['state'],
                          'Q3cm_review':['review','revision'],'Q3cm_full':['temporal','reaction','expression','review','revision','state']}
                for s in expected.get(version,[]):self.assertIn(s,stages)
                if version in ('Q3cm_split','Q3cm_full'):
                    trace={s['stage']:s for s in provided['records'][-1]['pipeline']['stages']}
                    rec=provided['records'][-1]
                    self.assertEqual(rec['inner']['appraisal'],trace['reaction']['output']['appraisal'])
                    self.assertEqual(rec['inner']['private_position']['actual_wish'],trace['reaction']['output']['actual_wish'])
                if version in ('Q3cm_state','Q3cm_full'):
                    state=load({'_storage_dir':withheld['session']['client_storage_dir']},version,withheld['session']['session_id'])
                    self.assertEqual({e['origin_turn'] for e in state['event_state']},{1,2,3})
                if version=='Q3cm_tools':self.assertTrue(withheld['records'][-1]['retrievals'])

    def test_legacy_cm_full_still_runs_with_original_identity(self):
        job=self.job('cm_full')
        self.assertEqual(job['status'],'completed',job['rows'])
        data=self.detail(job,'provided')
        self.assertEqual(data['session']['client_version'],'cm_full')
        self.assertTrue(data['records'][-1]['memory_input'])
        self.assertIn('state',[r['stage'] for r in data['records'][-1]['model_requests']])

    def test_all_counselors_execute_distinct_stages_without_client_private_data(self):
        for version in RS_VERSIONS:
            with self.subTest(version=version):
                job=self.job('Q3cm_full',version)
                self.assertEqual(job['status'],'completed',job['rows'])
                detail=self.detail(job,'provided');session=detail['session']
                row=session['counselor_checkpoint']['records'][-1]
                self.assertIn('pipeline',row)
                stages=row['pipeline']['stages'];names=[s.get('stage') for s in stages]
                self.assertNotIn('SECRET_T',json.dumps(stages,ensure_ascii=False))
                if version=='P3rs_sub_gate':
                    first=session['counselor_checkpoint']['records'][0]
                    self.assertEqual(first['observer']['recommend'],'continue')
                    main=next(s for s in first['pipeline']['stages'] if s.get('stage')=='main')
                    self.assertNotIn('CONTINUE_DIRECTION_SENTINEL',json.dumps(main['input']))
                if version=='P3rs_observe':
                    main=next(s for s in stages if s.get('stage')=='main')
                    self.assertIn('observations',main['input']);self.assertNotIn('advice',main['input'])
                if version in ('P3rs_dual','P3rs_full'):
                    for s in ('intervention','checking'):
                        self.assertIn(s,names)
                        self.assertNotIn('candidates',next(x for x in stages if x.get('stage')==s)['input'])
                if version in ('P3rs_review','P3rs_full'):
                    self.assertIn('revision',names)
                    review=next(s for s in stages if s.get('stage')=='review')
                    self.assertNotIn('pipeline',json.dumps(review['input']['recent_actions']))
                if version=='P3rs_tools':
                    self.assertGreater(row['pipeline']['tool_calls'],1)
                    self.assertIn('update_event',[s.get('name') for s in stages])
                self.assertEqual(session['settings']['rs_prompt_bundle'],session['counselor_checkpoint']['prompt_bundle'])

    def test_failed_review_does_not_commit_a_draft(self):
        VariantModel.fail_review=True
        job=self.job('Q3cm_review')
        self.assertEqual(job['status'],'failed')
        row=next(r for r in job['rows'] if r['branch']=='common')
        detail=self.get('/api/profile_batch/detail?id='+job['id']+'&session_id='+row['session_id'])
        self.assertEqual(detail['records'],[])
        self.assertEqual(len(detail['session']['public_dialogue']),1)

    def test_version_lists_and_prompt_sources(self):
        options=self.get('/api/profile_batch/options')
        self.assertTrue(set(CM_VERSIONS)<={r['id'] for r in options['compare']['versions']})
        self.assertTrue(set(RS_VERSIONS)<={r['id'] for r in options['counselors']})
        for version in CM_VERSIONS:
            self.get('/api/prompts/source?role=client&version='+version+'&kind=system')
        for version in RS_VERSIONS:
            for kind in ('system','user'):
                self.get('/api/prompts/source?role=counselor&version='+version+'&kind='+kind)


class VariantStateTests(unittest.TestCase):
    def test_private_gate_includes_derived_state(self):
        ctx={'records':[{'secret':'original'}],'saved':{'event_state':[{'secret':'derived'}]}}
        self.assertEqual(memory_access(ctx,{'compare_memory_mode':'withheld'}),([],[]))
        self.assertEqual(memory_access(ctx,{'compare_memory_mode':'provided'}),(ctx['records'],ctx['saved']['event_state']))

    def test_state_origins_are_immutable_and_unavailable_origins_rejected(self):
        old=[{'origin_turn':1,'original_reaction':{'feeling':'then'},'original_position':{}}]
        update={'origin_turn':1,'current_meaning':'now','disclosure':'voiced','evidence_turns':[2],'change_reason':'new context'}
        got=merge_state(old,[update],{1:{'inner':{}}},2)
        self.assertEqual(got[0]['original_reaction'],{'feeling':'then'})
        self.assertNotIn('current_meaning',old[0])
        with self.assertRaises(ValueError):merge_state(old,[update],{},2)

    def test_counselor_notes_need_public_evidence_and_asking_is_not_resolution(self):
        public=[{'role':'counselor','content':'제안'},{'role':'client','content':'네'}]
        events=[{'event_turn':1,'utterance':'제안','status':'unconfirmed'}]
        update={'event_turn':1,'status':'answered','public_response':'네','unresolved':'','evidence':[{'turn':1,'role':'client','quote':'invented'}]}
        with self.assertRaises(ValueError):update_ledger(events,[update],public)
        parsed={'move':'check_in','check_turn':1,'rupture_turn':0,'utterance':'어땠나요?'}
        self.assertEqual(commit_ledger(events,parsed,2)[0]['status'],'asked')
        self.assertEqual(events[0]['status'],'unconfirmed')

    def test_freeze_does_not_reload_changed_aux_prompts(self):
        request={'counselor_version':'P3rs_full'};freeze(request)
        original=copy.deepcopy(request)
        with patch('counselor.rs_variants.runtime.bundle',return_value={'system':'changed'}):freeze(request)
        self.assertEqual(request,original)


if __name__=='__main__':unittest.main()
