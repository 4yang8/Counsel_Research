"""Item-level patches: preservation, legacy input, validation and real request flow."""
import copy
import json
import unittest
import tempfile
from pathlib import Path
from urllib.request import urlopen
from test_lab import fixture, close, post, Model
from client.Q2a_trait_background import note_items as notes, runtime
from client.Q2a_trait_background.states import STATES, INNER, EMPTY, render


def operation(action, section='bond', item_id='', content='', reason='이번 경험으로 달라진 마음'):
    return dict(action=action, section=section, item_id=item_id, content=content, reason=reason)


class ItemTests(unittest.TestCase):
    def setUp(self):
        self.empty=notes.read_items(render(dict.fromkeys(STATES,EMPTY)))

    def test_add_update_delete_preserves_unmentioned_ids_and_contents(self):
        items,changes=notes.apply(self.empty,{'operations':[
            operation('add',content='마음 A'),operation('add',content='마음 B'),
            operation('add','unspoken_and_wishes',content='아직 말하지 않은 걱정')]},{'bond'})
        a,b=[r['id'] for r in items['bond']];unspoken=copy.deepcopy(items['unspoken_and_wishes'])
        patched,changes=notes.apply(items,{'operations':[operation('update',item_id=a,content='달라진 마음 A'),
            operation('delete',item_id=b),operation('add',content='새 마음 C')]},{'bond'})
        self.assertEqual(patched['bond'][0],{'id':a,'content':'달라진 마음 A'})
        self.assertNotIn(b,[r['id'] for r in patched['bond']])
        self.assertEqual(patched['unspoken_and_wishes'],unspoken)
        self.assertEqual(changes[1]['before'],'마음 B')
        self.assertEqual(notes.read_items(notes.render_items(patched)),patched)
        self.assertEqual(items['bond'][0]['content'],'마음 A')
        self.assertNotIn('이번 경험으로',notes.render_items(patched))

    def test_empty_and_noop(self):
        items,_=notes.apply(self.empty,{'operations':[operation('add',content='마음')]},{'bond'})
        same,log=notes.apply(items,{'operations':[operation('update',item_id=items['bond'][0]['id'],content='마음')]},{'bond'})
        self.assertEqual(same,items);self.assertEqual(log,[])
        empty,log=notes.apply(items,{'operations':[operation('delete',item_id=items['bond'][0]['id'])]},{'bond'})
        self.assertIn('### 상담사와의 관계적 유대\n(내용 없음)',notes.render_items(empty))
        self.assertEqual(notes.apply(items,{'operations':[]},set()),(items,[]))

    def test_legacy_paragraph_is_preserved_with_stable_id(self):
        values=dict.fromkeys(STATES,EMPTY);values['bond']='첫 문단의 기존 마음.\n다음 줄의 조건.'
        legacy=render(values);items=notes.read_items(legacy)
        self.assertEqual(items['bond'][0]['content'],values['bond'])
        self.assertEqual(notes.read_items(legacy),items)
        normalized=notes.render_items(items)
        self.assertEqual(notes.read_items(normalized),items)
        self.assertEqual(notes.normalize(normalized),normalized)
        old='# 현재 심리상태\n\n'+'\n\n'.join('## '+label+'\n'+values[k] for k,(label,_) in STATES.items())+'\n'
        self.assertEqual(notes.read_items(old),items)

    def test_loading_legacy_file_does_not_modify_it(self):
        with tempfile.TemporaryDirectory() as temp:
            config={'_storage_dir':temp};path=runtime.directory(config,'legacy')/'state_notes.md'
            path.parent.mkdir(parents=True)
            values=dict.fromkeys(STATES,EMPTY);values['bond']='기존 마음.\n기존 조건.'
            original=render(values);path.write_text(original)
            loaded=runtime.load(config,'legacy')
            self.assertEqual(path.read_text(),original)
            self.assertEqual(loaded['content'],original)
            self.assertIn('- [n',loaded['prompt_content'])
            self.assertEqual(loaded['items']['bond'][0]['content'],values['bond'])

    def test_invalid_batch_is_atomic(self):
        items,_=notes.apply(self.empty,{'operations':[operation('add',content='A'),operation('add',content='B')]},{'bond'})
        original=copy.deepcopy(items);a,b=[r['id'] for r in items['bond']]
        cases=[
            [operation('update',item_id='n000000000000',content='X')],
            [operation('update','problem_clarity',a,'X')],
            [operation('add','problem_clarity',content='X')],
            [operation('delete',item_id=a,content='비어야 함')],
            [operation('add',item_id=a,content='X')],
            [operation('add',content='A')],
            [operation('update',item_id=a,content='X'),operation('delete',item_id=a)],
            [operation('update',item_id=a,content='X'),operation('delete',item_id='missing')],
            [operation('add',content='A\nB')],
            [operation('add',content='- 다른 글머리')],
            [operation('update',item_id=a,content=EMPTY)],
        ]
        for operations in cases:
            with self.subTest(operations=operations),self.assertRaises(ValueError):notes.apply(items,{'operations':operations},{'bond'})
            self.assertEqual(items,original)
        with self.assertRaises(ValueError):notes.apply(items,{'updates':[],'inner_updates':[]},{'bond'})
        normalized=notes.render_items(items).replace('['+b+']','['+a+']')
        with self.assertRaises(ValueError):notes.read_items(normalized)


class ItemFlowTests(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        Model.requests.clear();Model.q2a_actions=None;Model.q2a_finish='stop';Model.q1_router_response='{"selections":[]}'
    def tearDown(self):
        Model.q2a_actions=None;Model.q1_router_response=None;close(self.server,self.model,self.temp)
    def get(self,path):return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path))
    def test_multi_turn_patch_and_failed_batch_preserve_committed_state(self):
        cfg=self.get('/api/config');scenario=copy.deepcopy(cfg['scenario']);scenario['dialogue_format']='messages';scenario['dialogue']=[];scenario.pop('dialogue_turns',None)
        session=json.loads(post(self.server,'/api/paired/create',{'experiment_version':'Q2a_trait_background','counselor_version':'basic',
            'speaker_modes':{'client':'model','counselor':'manual'},'scenario':scenario,'system_prompt':runtime.specification()['system_prompt'],
            'messages':[{'role':'user','content':'무엇이 마음에 남아 있나요?'}],'generation':cfg['generation']}))
        sid=session['session_id']
        def generate(count):return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{'session_id':sid,'expected_step':count}).splitlines()]
        def manual(count):post(self.server,'/api/paired/manual',{'session_id':sid,'expected_step':count,'actor':'counselor','content':'그 마음을 더 이야기해도 괜찮아요.'})
        def read():return self.get('/api/client_notes?paired=1&session_id='+sid)
        events=generate(0);self.assertEqual(events[-1]['type'],'done',events[-1]);first=read()
        self.assertEqual(len(Model.requests),5)
        self.assertEqual(len(first['records'][0]['note_operations']),2)
        original_item=first['items']['bond'][0];inner=first['items']['unspoken_and_wishes'][0]
        manual(1)
        Model.q2a_actions=[{'name':'stage_client_update','arguments':{'appraisal':{'interpretation':'조금 더 이해받았다','immediate_feeling':'안도','relevant_evidence':'이번 말','uncertainty':''},'response_plan':{'action':'걱정을 조금 말한다','reason':'내 속도를 인정받음'}}},
            {'name':'commit_client_utterance','arguments':{'utterance':'조금 더 이야기하고 싶어요.'}},
            {'name':'select_client_states','arguments':{'selections':[{'state':'bond','reason':'조금 더 이해받음'}]}},
            {'name':'update_client_states','arguments':{'operations':[
                operation('update',item_id=original_item['id'],content='더 이야기할 마음이 생겼다.'),
                operation('add',content='민감한 주제는 여전히 조심스럽다.'),
                operation('delete','unspoken_and_wishes',inner['id'])]}}]
        events=generate(2);self.assertEqual(events[-1]['type'],'done',events[-1]);second=read()
        self.assertEqual(second['items']['bond'][0]['id'],original_item['id'])
        self.assertEqual(second['inner']['unspoken_and_wishes'],EMPTY)
        self.assertEqual([o['action'] for o in second['records'][-1]['note_operations']],['update','add','delete'])
        self.assertTrue(any(e['type']=='version_event' and e.get('action')=='updated' and len(e['data'].get('operations',[]))==3 for e in events))
        second_request=Model.requests[6]
        self.assertIn('['+original_item['id']+']',second_request['messages'][1]['content'])
        self.assertIn('operations',next(t['function']['parameters']['properties'] for t in second_request['tools'] if t['function']['name']=='update_client_states'))
        manual(3);Model.q2a_actions[-1]['arguments']['operations']=[operation('update',item_id=original_item['id'],content='저장되면 안 됨'),operation('delete',item_id='missing')]
        events=generate(4);self.assertEqual(events[-1]['type'],'error');failed=read()
        self.assertEqual(failed['content'],second['content']);self.assertEqual(len(failed['records']),2)
        self.assertEqual(len(self.get('/api/paired/session?session_id='+sid)['steps']),4)

if __name__=='__main__':unittest.main()
