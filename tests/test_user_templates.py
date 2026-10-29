"""Real template saves and rendered requests, using the local mock inference server."""
import copy
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from test_lab import Model, fixture, close, post
import test_q2a
import test_p3a
import runtime.user_templates as user_templates


class UserTemplates(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        Model.requests.clear();Model.q2a_actions=None;Model.q2a_finish='stop'
        Model.p3a_actions=None;Model.p3a_child_finish=Model.dual_notes_finish='stop';Model.p1_delay=0
    def tearDown(self):
        Model.q2a_actions=None;Model.p3a_actions=None
        close(self.server,self.model,self.temp)
    def get(self,path):
        return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path))
    def source(self,role,version,kind='user'):
        return self.get('/api/prompts/source?'+urlencode(dict(role=role,version=version,kind=kind)))
    def save(self,source,content):
        return json.loads(post(self.server,'/api/prompts/save',{**source,'content':content}))
    def step(self,session):
        return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{'session_id':session['session_id'],'expected_step':len(session['steps'])}).splitlines()]
    def saved(self,s):return self.get('/api/paired/session?session_id='+s['session_id'])
    def test_all_sources_are_isolated_and_unknown_variables_rejected(self):
        for (role,version,kind),spec in user_templates.REGISTRY.items():
            with self.subTest(role=role,version=version,kind=kind):
                source=self.source(role,version,kind)
                self.assertTrue(Path(source['path']).is_relative_to(self.temp.name))
                self.assertEqual(source['variables'],spec['variables'])
                self.assertEqual(user_templates.validate(source['content'],spec['variables']),set(spec['variables']))
                before=source['content']
                for suffix in ('\n{{typo_variable}}','\n{{dialogue','\n{{dialogue.upper()}}'):
                    with self.assertRaises(HTTPError) as e:self.save(source,before+suffix)
                    self.assertEqual(e.exception.code,400)
                self.assertEqual(Path(source['path']).read_text(),before)
                saved=self.save(source,before+'\nSAVED_TEMPLATE')
                self.assertEqual(Path(source['path']).read_text(),saved['content'])
    def test_data_is_not_reinterpreted_as_template_and_live_files_are_reread(self):
        source=self.source('counselor','basic')
        self.save(source,'대화: {{ dialogue }}')
        self.assertEqual(user_templates.render('counselor','basic',dialogue='리터럴 {{unknown}}'), '대화: 리터럴 {{unknown}}')
        self.save(self.source('counselor','basic'),'변경: {{dialogue}}')
        self.assertEqual(user_templates.render('counselor','basic',dialogue='다음 턴'),'변경: 다음 턴')
    def test_q2a_main_router_same_turn_and_next_turn(self):
        main=self.source('client','Q2a');router=self.source('client','Q2a','trait_router_user')
        self.save(main,'MAIN_TEMPLATE\n'+main['content'])
        self.save(router,'ROUTER_TEMPLATE\n'+router['content'])
        s=test_q2a.Q2aTests.create(self)
        e=self.step(s);self.assertEqual(e[-1]['type'],'done',e[-1])
        texts=[next(m['content'] for m in p['messages'] if m['role']=='user') for p in Model.requests]
        self.assertTrue(texts[0].startswith('ROUTER_TEMPLATE'))
        self.assertTrue(all(t.startswith('MAIN_TEMPLATE') for t in texts[1:]))
        self.assertEqual(len(set(texts[1:])),1)
        self.assertNotIn('{{',texts[-1])
        old_user=texts[-1];s=self.saved(s)
        self.save(self.source('client','Q2a'),'NEXT_TEMPLATE\n'+main['content'])
        post(self.server,'/api/paired/manual',{'session_id':s['session_id'],'expected_step':len(s['steps']),'actor':'counselor','content':'NEXT_PUBLIC: 이번에는 어떤가요?'})
        s=self.saved(s);Model.requests.clear()
        e=self.step(s);self.assertEqual(e[-1]['type'],'done',e[-1])
        text=next(m['content'] for m in Model.requests[-1]['messages'] if m['role']=='user')
        self.assertTrue(text.startswith('NEXT_TEMPLATE'));self.assertIn('NEXT_PUBLIC',text)
        self.assertIn('선택을 존중받았다고',text)
        self.assertNotIn('NEXT_PUBLIC',old_user)
        self.assertNotIn('NEXT_TEMPLATE',self.source('client','Q2')['content'])
    def test_p3a_main_and_guide_templates_reach_model(self):
        main=self.source('counselor','P3a');child=self.source('counselor','P3a','guide_agent_user')
        self.save(main,'COUNSELOR_TEMPLATE\n'+main['content'])
        self.save(child,'GUIDE_TEMPLATE\n'+child['content'])
        s=test_p3a.P3aTests.create(self)
        s=test_p3a.P3aTests.manual_client(self,s,'공개 대화: 테스트 상담')
        Model.p3a_actions=[test_p3a.P3aTests.action(self,'common','QUESTION_MARKER: 다음 대응은?')]
        e=self.step(s);self.assertEqual(e[-1]['type'],'done',e[-1])
        main_call,child_call,resumed=Model.requests
        user=lambda p:next(m['content'] for m in p['messages'] if m['role']=='user')
        self.assertTrue(user(main_call).startswith('COUNSELOR_TEMPLATE'))
        self.assertIn('현재 파일 원문',user(main_call))
        self.assertTrue(user(child_call).startswith('GUIDE_TEMPLATE'))
        self.assertIn('QUESTION_MARKER',user(child_call));self.assertIn('상세 가이드',user(child_call))
        self.assertEqual(user(main_call),user(resumed))

if __name__=='__main__':unittest.main()
