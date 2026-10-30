"""Batch metadata stays auditable without changing model-visible conditions."""
import copy
import json
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from test_lab import fixture, close, post, Model
from runtime.paired_lab import comparison_metadata

class ComparisonControls(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture()
        self.config=self.get('/api/config');Model.requests.clear()
    def tearDown(self):close(self.server,self.model,self.temp)
    def get(self,path):return json.load(urlopen('http://127.0.0.1:'+str(self.server.server_port)+path))
    def payload(self):
        return {'experiment_version':'basic','counselor_version':'basic','scenario':self.config['scenario'],
                'generation':self.config['generation'],'system_prompt':'내담자로 응답하세요.',
                'messages':[{'role':'user','content':'지금 마음은 어떤가요?'}]}
    def test_invalid_group_does_not_create_session(self):
        metadata={'batch_id':'group_1','schedule':'sequential','requested_utterances':2}
        invalids=[{}, {**metadata,'batch_id':'../other'}, {**metadata,'schedule':'unknown'},
                  {**metadata,'requested_utterances':True},{**metadata,'requested_utterances':0},
                  {**metadata,'requested_utterances':101},{**metadata,'extra':'ignored?'}]
        for value in invalids:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):comparison_metadata(value)
                with self.assertRaises(HTTPError) as caught:post(self.server,'/api/paired/create',{**self.payload(),'comparison':value})
                self.assertEqual(caught.exception.code,400)
        self.assertFalse(list(self.server.storage_dir.glob('paired/*/session.json')))
    def test_creation_and_continuation_retain_separate_batch_ids_and_frozen_generation(self):
        first={'batch_id':'first_group','schedule':'sequential','requested_utterances':1}
        original=copy.deepcopy(first)
        session=json.loads(post(self.server,'/api/paired/create',{**self.payload(),'comparison':first}))
        first['batch_id']='mutated'
        self.assertEqual(session['settings']['comparison'],original)
        self.assertEqual(Model.requests,[])
        later={'batch_id':'later_group','schedule':'parallel','requested_utterances':3}
        events=[json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{
            'session_id':session['session_id'],'expected_step':0,'comparison':later}).splitlines()]
        self.assertEqual(events[-1]['type'],'done',events[-1])
        saved=self.get('/api/paired/session?session_id='+session['session_id'])
        self.assertEqual(saved['settings']['comparison'],original)
        self.assertEqual(saved['steps'][0]['comparison'],later)
        self.assertEqual(saved['attempts'][0]['comparison'],later)
        self.assertEqual(saved['settings']['generation'],self.config['generation'])
        self.assertNotIn('later_group',json.dumps(Model.requests))
        self.assertNotIn('first_group',json.dumps(Model.requests))

if __name__=='__main__':unittest.main()
