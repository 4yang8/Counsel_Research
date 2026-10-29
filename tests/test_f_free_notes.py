"""F free-note patches, historical compatibility and real HTTP generation paths."""
import copy
import json
import unittest
from pathlib import Path
import test_memory_experiments as base
from test_memory_experiments import Model, action, stage, commit, post
import client.registry as registry
from client.Q2a_agentic_memory_F import note_items as notes, memory

F='Q2a_agentic_memory_F'
def op(action,content='',item_id='',reason='이번 경험에 따른 변경'):
    return dict(action=action,content=content,item_id=item_id,reason=reason)
def query(source='current',**kw):
    return dict(source=source,query='',item_ids=[],turns=[],offset=0,limit=5,reason='기록된 마음을 확인한다.',**kw)

class FreeNoteUnitTests(unittest.TestCase):
    def test_changes_preserve_untouched_ids_and_validate_atomically(self):
        original={'notes':[{'id':'n111111111111','content':'처음 걱정'},{'id':'n222222222222','content':'계속 유효한 마음'},{'id':'n333333333333','content':'해소된 걱정'}]}
        patched,changes=notes.apply(original,{'operations':[op('update','변화한 걱정','n111111111111'),op('delete',item_id='n333333333333'),op('add','새로 생긴 바람')]})
        self.assertEqual(patched['notes'][1],original['notes'][1]);self.assertEqual(len(patched['notes']),3)
        self.assertEqual(notes.read_items(notes.render_items(patched)),patched)
        before=copy.deepcopy(original)
        with self.assertRaises(ValueError):notes.apply(original,{'operations':[op('delete',item_id='n111111111111'),op('delete',item_id='nffffffffffff')]})
        self.assertEqual(before,original)
        with self.assertRaises(ValueError):notes.apply(original,{'operations':[dict(op('add','마음'),section='bond')]})
    def test_legacy_headings_flatten_without_losing_items(self):
        text='# 내담자 노트\n\n## 현재 내면\n\n### 마음\n- [n111111111111] 속마음\n\n## 심리상태\n\n### 유대\n- [n222222222222] 조심스럽다\n\n### 목표\n(내용 없음)\n'
        result=notes.read_items(text)
        self.assertEqual([r['content'] for r in result['notes']],['속마음','조심스럽다'])
        self.assertNotIn('###',notes.render_items(result))
        self.assertEqual(notes.render_items({'notes':[]}),'# 내담자 노트\n\n(내용 없음)\n')
    def test_lookup_changes_and_episodes_without_section_argument(self):
        note={'items':{'notes':[{'id':'n111111111111','content':'현재 마음'}]},'records':[{'turn':1,'turn_id':'one','utterance':'공개 발화','note_operations':[dict(op('delete',item_id='n222222222222'),before='과거 마음')]}]}
        self.assertEqual(memory.lookup(note,query())['results'][0]['content'],'현재 마음')
        self.assertEqual(memory.lookup(note,query('changes'))['results'][0]['before'],'과거 마음')
        self.assertEqual(memory.lookup(note,query('episodes'))['results'][0]['utterance'],'공개 발화')

class FreeNoteRuntimeTests(unittest.TestCase):
    setUp=base.MemoryExperiments.setUp
    tearDown=base.MemoryExperiments.tearDown
    get=base.MemoryExperiments.get
    create=base.MemoryExperiments.create
    step=base.MemoryExperiments.step
    notes=base.MemoryExperiments.notes
    def test_empty_note_three_steps_and_second_turn_selective_context(self):
        s=self.create(F);r=registry.client_runtime(F)
        self.assertEqual(self.notes(s)['content'],'# 내담자 노트\n\n(내용 없음)\n')
        schema=r.TOOLS[2]['function']['parameters'];self.assertNotIn('section',schema['properties']['operations']['items']['properties'])
        self.assertNotIn('state_definitions',self.get('/api/prompts/source?role=client&version='+F+'&kind=user')['content'])
        Model.q1_router_response='{"selections":[],"note_ids":[]}'
        Model.q2a_actions=[stage(True),commit(True),action('update_client_notes',operations=[op('add','SELECTED 心配'),op('add','OMITTED 継続')])]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        main=[e for e in events if e['type']=='request'];self.assertEqual(len(main),3)
        self.assertNotIn('select_client_states',json.dumps([e['payload'] for e in main]))
        note=self.notes(s);rows=note['items']['notes'];self.assertEqual(len(rows),2)
        saved=self.get('/api/paired/session?session_id='+s['session_id'])
        s=json.loads(post(self.server,'/api/paired/manual',{'session_id':s['session_id'],'expected_step':len(saved['steps']),'actor':'counselor','content':'少し話せそうですか。'}))['session']
        Model.q1_router_response=json.dumps({'selections':[],'note_ids':[rows[0]['id']]})
        Model.q2a_actions=[action('retrieve_client_memory',**query('changes')),stage(True),commit(True),action('update_client_notes',operations=[op('update','SELECTED 変化',rows[0]['id'])])]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        main=[e for e in events if e['type']=='request']
        self.assertNotIn('OMITTED',main[0]['payload']['messages'][1]['content'])
        self.assertIn('OMITTED',main[-1]['payload']['messages'][1]['content'])
        self.assertEqual(len([m for m in main[-1]['payload']['messages'] if m.get('reasoning')]),3)
        result=self.notes(s);self.assertEqual(result['items']['notes'][1],rows[1]);self.assertEqual(result['records'][-1]['note_format'],'free_items_v1')
        self.assertEqual(len(result['records'][-1]['memory_retrievals'][0]['result']['results']),2)
    def test_invalid_patch_does_not_commit_utterance_or_note(self):
        s=self.create(F);before=self.notes(s)['content'];Model.q1_router_response='{"selections":[],"note_ids":[]}'
        Model.q2a_actions=[stage(True),commit(True),action('update_client_notes',operations=[op('delete',item_id='nffffffffffff')])]
        events=self.step(s);self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(self.notes(s)['content'],before);self.assertEqual(self.notes(s)['records'],[])
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])

if __name__=='__main__':unittest.main()
