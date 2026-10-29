"""Current notes are read from both actors' files without replaying model calls."""
import copy
import json
import unittest
from pathlib import Path
from urllib.request import urlopen
from test_lab import fixture, close, post, Model
from client.Q2a import runtime as q2a

class SessionNotesTests(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        Model.requests.clear();Model.q2a_actions=None;Model.q2a_finish='stop'
    def tearDown(self):
        close(self.server,self.model,self.temp)
    def get(self,path):
        return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path))
    def create(self):
        cfg=self.get('/api/config');scenario=copy.deepcopy(cfg['scenario'])
        scenario['dialogue_format']='messages';scenario['dialogue']=[];scenario.pop('dialogue_turns',None)
        return json.loads(post(self.server,'/api/paired/create',{
            'experiment_version':'Q2a','counselor_version':'P3a','scenario':scenario,
            'system_prompt':q2a.specification()['system_prompt'],
            'messages':[{'role':'user','content':'편하게 말씀해 주세요.'}],
            'generation':{**cfg['generation'],'max_tokens':4096}}))
    def test_current_files_both_actors_and_session_isolation(self):
        session=self.create();url='/api/session_notes?paired=1&session_id='+session['session_id']
        initial=self.get(url)
        self.assertEqual([n['actor'] for n in initial['notes']],['client','counselor','counselor'])
        self.assertEqual(Model.requests,[])
        counselor=next(n for n in initial['notes'] if n['label']=='상담사 생각 노트')
        Path(counselor['path']).write_text('CURRENT_COUNSELOR_NOTE',encoding='utf-8')
        events=[json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{'session_id':session['session_id'],'expected_step':0}).splitlines()]
        self.assertEqual(events[-1]['type'],'done',events[-1])
        actual=self.get(url)
        client=next(n for n in actual['notes'] if n['actor']=='client')
        self.assertEqual(client['content'],Path(client['path']).read_text(encoding='utf-8'))
        self.assertIn('선택을 존중받았다고',client['content'])
        self.assertTrue(any(n['content']=='CURRENT_COUNSELOR_NOTE' for n in actual['notes']))
        other=self.create();other_notes=self.get('/api/session_notes?paired=1&session_id='+other['session_id'])
        self.assertNotIn('CURRENT_COUNSELOR_NOTE',json.dumps(other_notes))
        self.assertEqual(self.get('/api/session_notes')['notes'],[])

if __name__=='__main__':unittest.main()
