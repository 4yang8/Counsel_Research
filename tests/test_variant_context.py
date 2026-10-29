"""Variable-context requests retain evidence/history and isolate the baseline."""
import copy
import json
import unittest
from pathlib import Path
from urllib.request import urlopen
from test_lab import fixture, close, post, Model
from client.Q2a_variant_context import runtime as variant
from client.Q2a_variant_context.context_blocks import compose, render_user
from client.Q2a import runtime as baseline

VERSION='Q2a_variant_context'

class VariantContextTests(unittest.TestCase):
    def setUp(self):
        self.server,self.model,self.temp=fixture(prompt_editing=True)
        Model.requests.clear();Model.q2a_actions=None;Model.q2a_finish='stop'
    def tearDown(self):
        Model.q2a_actions=None;close(self.server,self.model,self.temp)
    def get(self,path):return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path))
    def create(self,version=VERSION):
        cfg=self.get('/api/config');scenario=copy.deepcopy(cfg['scenario'])
        scenario['dialogue_format']='messages';scenario['dialogue']=[];scenario.pop('dialogue_turns',None)
        return json.loads(post(self.server,'/api/paired/create',{'experiment_version':version,'counselor_version':'basic','scenario':scenario,
            'system_prompt':variant.specification()['system_prompt'],'messages':[{'role':'user','content':'지금 말하기 어려운 부분은 보류해도 괜찮아요.'}],'generation':{**cfg['generation'],'max_tokens':4096}}))
    def step(self,s):return [json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{'session_id':s['session_id'],'expected_step':len(s['steps'])}).splitlines()]
    def assert_manifest(self,event):
        payload=event['payload'];manifest=event['context_manifest']
        self.assertNotIn('context_manifest',payload)
        for group in manifest['groups']:
            if group['id']=='tools':
                self.assertEqual(payload['tools'],[p['value'] for p in group['parts'] if p['included']]);continue
            message=payload['messages'][group['message_index']]
            content=[p for p in group['parts'] if p['field']=='content']
            if content:
                self.assertEqual(message['content'],''.join(p['value'] for p in content if p['included']))
                for part in content:
                    if part['included']:
                        start,end=part['request_range'];self.assertEqual(message['content'][start:end],part['value'])
        return manifest
    def test_final_call_only_prunes_and_metadata_matches_wire(self):
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        main=[e for e in events if e['type']=='request'];self.assertEqual(len(main),4)
        self.assertEqual(len(Model.requests),5)
        for i,e in enumerate(main):
            manifest=self.assert_manifest(e);self.assertEqual(e['payload'],Model.requests[i+1])
            if i<3:self.assertEqual(manifest['excluded_parts'],0)
        final=main[-1]['payload'];manifest=main[-1]['context_manifest']
        excluded=[p['id'] for g in manifest['groups'] for p in g['parts'] if not p['included']]
        self.assertIn('instructions.speech',excluded);self.assertIn('instructions.sequence',excluded)
        self.assertIn('state_definitions.goal_alignment',excluded);self.assertNotIn('state_definitions.bond',excluded)
        self.assertEqual([t['function']['name'] for t in final['tools']],['update_client_states'])
        self.assertEqual(final['messages'][2:6],main[2]['payload']['messages'][2:6])
        self.assertEqual([m['reasoning'] for m in final['messages'] if m['role']=='assistant'],['Q2a_REASON_0','Q2a_REASON_1','Q2a_REASON_2'])
        self.assertEqual([m['tool_call_id'] for m in final['messages'] if m['role']=='tool'],[m['tool_calls'][0]['id'] for m in final['messages'] if m.get('tool_calls')])
        for g in manifest['groups']:
            for p in g['parts']:
                if p['id'].startswith(('user.basic_info','user.dialogue','user.counselor_utterance','note.','user.selected_traits')):self.assertTrue(p['included'],p['id'])
        child=next(e['event'] for e in events if e['type']=='agent_event' and e['event']['type']=='request');self.assert_manifest(child)
        saved=self.get('/api/paired/session?session_id='+s['session_id']);self.assertEqual(len(saved['steps']),1)
        log=json.loads(Path(saved['steps'][0]['saved_path']).read_text());self.assertEqual(log['model_calls'][-1]['context_manifest'],manifest)
        notes=self.get('/api/session_notes?paired=1&session_id='+s['session_id']);self.assertIn('/'+VERSION+'/',notes['notes'][0]['path'])
        # Baseline requests retain their complete system/user/tools at every stage.
        Model.requests.clear();other=self.create('Q2a');base_events=self.step(other);self.assertEqual(base_events[-1]['type'],'done')
        requests=Model.requests[1:];self.assertTrue(all(p['messages'][:2]==requests[0]['messages'][:2] for p in requests));self.assertTrue(all(len(p['tools'])==4 for p in requests))
    def test_retry_uses_full_source_again_and_preserves_pairs(self):
        from test_q2a import Q2aTests
        actions=Q2aTests.actions(self)
        good=copy.deepcopy(actions[-1]);actions[-1]['arguments']['inner_updates']=[{'section':'invalid','content':'변경','reason':'잘못된 항목'}]
        Model.q2a_actions=actions+[good]
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        main=[e for e in events if e['type']=='request'];self.assertEqual(len(main),5)
        for e in main:self.assert_manifest(e)
        self.assertEqual(main[-1]['payload']['messages'][:2],main[-2]['payload']['messages'][:2])
        for e in main[-2:]:
            ids=[p['id'] for g in e['context_manifest']['groups'] for p in g['parts'] if not p['included']]
            self.assertIn('instructions.speech',ids)
        self.assertEqual(len([m for m in main[-1]['payload']['messages'] if m['role']=='tool']),4)

    def test_standalone_discovery_and_note_version(self):
        cfg=self.get('/api/config')
        events=[json.loads(line) for line in post(self.server,'/api/chat',{'experiment_version':VERSION,'scenario':cfg['scenario'],
            'system_prompt':variant.specification()['system_prompt'],'generation':cfg['generation'],'messages':[{'role':'user','content':'편한 만큼 말해 주세요.'}]}).splitlines()]
        self.assertEqual(events[-1]['type'],'done',events[-1])
        row=next(r for r in self.get('/api/sessions')['sessions'] if r['kind']==VERSION)
        note=self.get('/api/client_notes?version='+VERSION+'&session_id='+row['session_id']+'&key='+row['key'])
        self.assertEqual(len(note['records']),1)
        for event in events:
            if event['type']=='request':self.assert_manifest(event)

    def test_template_edits_and_original_source_survive_pruning(self):
        src=self.get('/api/prompts/source?role=client&version='+VERSION+'&kind=user')
        text=src['content']+'\nCUSTOM_SUFFIX {{basic_info}}'
        post(self.server,'/api/prompts/save',{**src,'content':text})
        self.assertNotEqual(text,self.get('/api/prompts/source?role=client&version=Q2a&kind=user')['content'])
        s=self.create();events=self.step(s);self.assertEqual(events[-1]['type'],'done')
        main=[e for e in events if e['type']=='request']
        self.assertIn('CUSTOM_SUFFIX',main[-1]['payload']['messages'][1]['content'])
        for event in main:self.assert_manifest(event)
        first=copy.deepcopy(main[0]['payload']);source=copy.deepcopy(first)
        compose(first,prune=True);self.assertEqual(source,main[0]['payload'])
        restored=copy.deepcopy(source);compose(restored);self.assertEqual(restored,source)

if __name__=='__main__':unittest.main()
