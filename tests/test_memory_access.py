"""HTTP tests of information boundaries, live tool choice, snapshots and rollback."""
import copy
import json
from http.server import BaseHTTPRequestHandler
from unittest.mock import patch
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError

from test_lab import fixture, close, post
from client import registry
from client.memory_access import VERSIONS
from client.memory_access import memory
from client.memory_access.runtime import STAGE, load, DEFAULTS, validate_seed


def sample(schema):
    if 'enum' in schema:return schema['enum'][0]
    kind=schema['type']
    if kind=='object':return {k:sample(v) for k,v in schema['properties'].items()}
    if kind=='array':return []
    if kind in ('number','integer'):return 1
    return 'CURRENT_PRIVATE_THOUGHT'


class MemoryModel(BaseHTTPRequestHandler):
    requests=[];search=False;double_search=False;fail=False;selected=[2];query='과거 동의의 이유';invalid_select=False
    def log_message(self,*args):pass
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(copy.deepcopy(data))
        if self.path=='/v1/embeddings':
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'data':[{'index':i,'embedding':[1.,0.]} for i,_ in enumerate(data['input'])]}).encode());return
        schema=data.get('structured_outputs',{}).get('json')
        if schema:
            delta={'content':json.dumps({'turns':[999] if self.invalid_select else self.selected})};finish='stop'
        elif data.get('tools'):
            names=[t['function']['name'] for t in data['tools']]
            if self.search and 'search_private_turns' in names:
                name='search_private_turns';args={'query':self.query}
            elif self.double_search and any(m.get('role')=='tool' and 'remaining_searches' in m['content'] for m in data['messages']) and 'stage_client_update' in names:
                name='search_private_turns';args={'query':'another'}
            elif 'stage_client_update' in names:
                name='stage_client_update';args=sample(STAGE['function']['parameters'])
            else:
                name='commit_client_utterance';args={'utterance':'아직은 부담이 있어요.'}
            delta={'tool_calls':[{'index':0,'id':'call_'+str(len(self.requests)), 'type':'function',
                'function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}
            finish='length' if self.fail else 'tool_calls'
        else:delta={'content':'조금 더 이야기해 주세요.'};finish='stop'
        chunk={'choices':[{'index':0,'delta':delta,'finish_reason':finish}],
               'usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}}
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())


def seed():
    public=[];records=[]
    for n in range(1,5):
        q='상담 질문 '+str(n);a='내담자 공개 답 '+str(n)
        public.extend([{'role':'counselor','content':q},{'role':'client','content':a}])
        records.append({'turn':n,'inner':{'reason':'PRIVATE_HISTORY_'+str(n)},'utterance':a,'counselor_utterance':q})
    return {'public_dialogue':public,'records':records}


class MemoryAccessTests(unittest.TestCase):
    def setUp(self):
        MemoryModel.requests=[];MemoryModel.search=False;MemoryModel.fail=False;MemoryModel.double_search=False;MemoryModel.invalid_select=False
        MemoryModel.selected=[2];MemoryModel.query='과거 동의의 이유'
        self.server,self.model,self.temp=fixture(model_handler=MemoryModel)
        self.config=self.get('/api/config')
    def tearDown(self):close(self.server,self.model,self.temp)
    def get(self,path):return json.loads(urlopen('http://127.0.0.1:'+str(self.server.server_port)+path).read())
    def body(self,version,**options):
        return {'experiment_version':version,'counselor_version':'basic','speaker_modes':{'client':'model','counselor':'manual'},
                'scenario':copy.deepcopy(self.config['scenario']), 'generation':self.config['generation'],
                'messages':[{'role':'user','content':'그때 동의한 이유를 이야기해 주세요.'}],
                'memory_options':{'seed':seed(),**options}}
    def create(self,version,**options):return json.loads(post(self.server,'/api/paired/create',self.body(version,**options)))
    def step(self,session):
        return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{'session_id':session['session_id'],'expected_step':len(session['steps'])}).splitlines()]
    def success(self,session):
        events=self.step(session);self.assertEqual(events[-1]['type'],'done',events[-1]);return events[-1]['session'],events
    def state(self,s):return load({'_storage_dir':s['client_storage_dir']},s['client_version'],s['session_id'])
    def manual(self,s,text='지금은 어떻게 생각하세요?'):
        return json.loads(post(self.server,'/api/paired/manual',{'session_id':s['session_id'],'expected_step':len(s['steps']),'actor':'counselor','content':text}))['session']
    def test_catalog_preview_and_source_routes(self):
        catalog=self.get('/api/versions')
        self.assertTrue(set(VERSIONS)<={v['id'] for v in catalog['versions']})
        for version in VERSIONS:
            self.get('/api/prompts/source?role=client&version='+version+'&kind=system')
            post(self.server,'/api/preview',self.body(version,oracle_turns=[2]))
        self.get('/api/prompts/source?role=client&version=Q3select&kind=memory_selector')
        self.assertEqual(MemoryModel.requests,[])
    def test_public_records_inner_but_never_reinjects_it(self):
        s=self.create('Q3public');s,_=self.success(s)
        inputs=MemoryModel.requests
        self.assertNotIn('PRIVATE_HISTORY',json.dumps(inputs))
        self.assertIn('CURRENT_PRIVATE_THOUGHT',json.dumps(self.state(s)['records'][-1]['inner']))
        # A fresh turn contains neither saved private thoughts nor old tool transcripts.
        MemoryModel.requests=[];s,_=self.success(self.manual(s))
        first=MemoryModel.requests[0]
        self.assertNotIn('PRIVATE_HISTORY',json.dumps(first))
        self.assertNotIn('CURRENT_PRIVATE_THOUGHT',json.dumps(first))
        self.assertEqual([m['role'] for m in first['messages']],['system','user'])
        self.assertEqual(self.state(s)['records'][-1]['memory_input'],[])
        self.assertEqual(self.state(s)['records'][-1]['turn'],6)
        self.assertNotIn('PRIVATE_',json.dumps(s['public_dialogue']))
    def test_full_oracle_same_public_input_and_selected_sources(self):
        public=[]
        for version,expected in [('Q3public',[]),('Q3oracle',[2]),('Q3full',[1,2,3,4])]:
            MemoryModel.requests=[];s=self.create(version,oracle_turns=[2]);s,_=self.success(s)
            initial=json.loads(MemoryModel.requests[0]['messages'][-1]['content']);public.append(initial['public_dialogue'])
            self.assertEqual([r['turn'] for r in initial['private_turns']],expected)
            self.assertEqual(self.state(s)['records'][-1]['metrics']['injected_turns'],expected)
        self.assertEqual(public[0],public[1]);self.assertEqual(public[1],public[2])
    def test_oracle_per_turn_plan_and_unavailable_rejected(self):
        s=self.create('Q3oracle',oracle_turns=[1],oracle_plan={'5':[2],'6':[5]});s,_=self.success(s)
        self.assertEqual(self.state(s)['records'][-1]['metrics']['injected_turns'],[2])
        s,_=self.success(self.manual(s));self.assertEqual(self.state(s)['records'][-1]['metrics']['injected_turns'],[5])
        with self.assertRaises(HTTPError):self.create('Q3oracle',oracle_turns=[99])
    def test_selection_only_exact_selected_chunks_reach_main(self):
        s=self.create('Q3select');s,_=self.success(s)
        self.assertIn('PRIVATE_HISTORY_1',json.dumps(MemoryModel.requests[0]))
        self.assertIn('PRIVATE_HISTORY_2',json.dumps(MemoryModel.requests[1]))
        self.assertNotIn('PRIVATE_HISTORY_1',json.dumps(MemoryModel.requests[1]))
        self.assertEqual(self.state(s)['records'][-1]['memory_input'],[memory.chunk(seed()['records'][1])])
        self.assertEqual(s['steps'][-1]['usage']['total_tokens'],60)
    def test_selector_wire_compatibility_keeps_local_duplicate_check(self):
        MemoryModel.selected=[2,2]
        s=self.create('Q3select');events=self.step(s)
        wire=MemoryModel.requests[0]['structured_outputs']['json']
        self.assertNotIn('uniqueItems',wire['properties']['turns'])
        self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])

    def test_selector_invalid_result_is_not_fallback(self):
        MemoryModel.invalid_select=True;s=self.create('Q3select');events=self.step(s)
        self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])
    def test_rag_optional_one_search_top3_and_no_second_search(self):
        options={'embedding_backend':'openai','embedding_url':'http://127.0.0.1:'+str(self.model.server_port)}
        s=self.create('Q3ragtool',**options)
        s,_=self.success(s);self.assertEqual(self.state(s)['records'][-1]['metrics']['search_calls'],0)
        MemoryModel.requests=[];MemoryModel.search=True
        s,_=self.success(self.manual(s));rec=self.state(s)['records'][-1]
        self.assertEqual(rec['metrics']['search_calls'],1)
        self.assertEqual(len(rec['memory_input']),3)
        chat=[r for r in MemoryModel.requests if 'messages' in r]
        self.assertNotIn('PRIVATE_HISTORY',json.dumps(chat[0]))
        self.assertTrue(any('PRIVATE_HISTORY' in json.dumps(r) for r in chat[1:]))
        self.assertFalse(any(t['function']['name']=='search_private_turns' for r in chat[1:] for t in r.get('tools',[])))
        self.assertIsNone(rec['retrievals'][0]['similarity_threshold'])
    def test_rag_search_failure_rolls_back(self):
        MemoryModel.search=True;s=self.create('Q3ragtool')
        with patch('client.memory_access.runtime.retrieve',side_effect=RuntimeError('embedding offline')):
            events=self.step(s)
        self.assertEqual(events[-1]['type'],'error')
        self.assertIn('embedding offline',events[-1]['error'])
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])
        MemoryModel.search=False;s,_=self.success(s);self.assertEqual(self.state(s)['revision'],1)
    def test_failed_generation_not_committed_and_retry_clean(self):
        s=self.create('Q3full');MemoryModel.fail=True;events=self.step(s)
        self.assertEqual(events[-1]['type'],'error')
        MemoryModel.fail=False;s,_=self.success(s);self.assertEqual(self.state(s)['revision'],1)
    def test_snapshot_reload_and_common_fork(self):
        s,_=self.success(self.create('Q3full'))
        exported=self.get('/api/memory_access/state?paired=1&session_id='+s['session_id'])
        validate_seed(exported['snapshot'])
        self.assertNotIn('model_requests',json.dumps(exported['snapshot']))
        other=self.create('Q3oracle',seed=exported['snapshot'],oracle_turns=[5])
        other,_=self.success(other);self.assertEqual(self.state(other)['records'][-1]['turn'],6)
        self.assertEqual(self.state(other)['records'][-1]['metrics']['injected_turns'],[5])
        notes=self.get('/api/session_notes?paired=1&session_id='+other['session_id'])
        self.assertTrue(any(n['actor']=='client' for n in notes['notes']))
    def test_invalid_seed_and_settings(self):
        invalid=seed();invalid['records'][0]['utterance']='다른 발화'
        with self.assertRaises(HTTPError):self.create('Q3public',seed=invalid)
        with self.assertRaises(HTTPError):self.create('Q3ragtool',top_k=4)
        with self.assertRaises(HTTPError):self.create('Q3oracle',oracle_plan={'5':[5]})
    def test_standalone_continuation_and_saved_option_freeze(self):
        body=self.body('Q3oracle',oracle_turns=[2])
        events=[json.loads(line) for line in post(self.server,'/api/chat',body).splitlines()]
        self.assertEqual(events[-1]['type'],'done',events[-1])
        sid=next(e['session_id'] for e in events if e['type']=='experiment_setup')
        body['session_id']=sid;body['messages']=[{'role':'user','content':'이후 마음이 달라졌나요?'}]
        events=[json.loads(line) for line in post(self.server,'/api/chat',body).splitlines()]
        self.assertEqual(events[-1]['type'],'done',events[-1])
        data=self.get('/api/memory_access/state?version=Q3oracle&session_id='+sid)
        self.assertEqual(data['state']['revision'],2)
        self.assertEqual(data['state']['records'][-1]['turn'],6)
        body['memory_options']['oracle_turns']=[3]
        with self.assertRaises(HTTPError):post(self.server,'/api/preview',body)

    def test_model_cannot_execute_second_search(self):
        s=self.create('Q3ragtool');MemoryModel.search=True;MemoryModel.double_search=True
        found={'results':[memory.chunk(seed()['records'][0])],'scores':[{'turn':1,'similarity':.8}]}
        with patch('client.memory_access.runtime.retrieve',return_value=found) as lookup:
            events=self.step(s)
        self.assertEqual(lookup.call_count,1)
        self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])

    def test_search_cosine_sort_cache_and_session_scope(self):
        memory._CACHE.clear();calls=[]
        def embeddings(texts,opts):
            calls.append(texts)
            if texts[0].startswith('Instruct:'):return [[1.,0.]]
            return [[0.,1.] if 'PRIVATE_HISTORY_1' in t else [1.,0.] for t in texts]
        opts={**DEFAULTS,'embedding_backend':'openai','embedding_url':'http://local.test/v1'}
        with patch.object(memory,'embed',side_effect=embeddings):
            a=memory.retrieve(seed()['records'],'동의',opts)
            b=memory.retrieve(seed()['records'][:1],'동의',opts)
        self.assertEqual([r['turn'] for r in a['results']],[2,3,4])
        self.assertEqual([r['turn'] for r in b['results']],[1])
        self.assertEqual(b['embedded_documents'],0)
        self.assertEqual(len(calls),3)


if __name__=='__main__':unittest.main()
