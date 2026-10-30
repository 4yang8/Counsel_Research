"""Recording fidelity, full-pair handoff, deterministic snapshots and rollback."""
import copy
import json
import unittest
from unittest.mock import patch

import client.registry as registry
from client.recording_experiments import recording
from client.Q3 import memory
import test_memory_experiments as base
from test_memory_experiments import Model, action, commit, post
from test_q3 import plan, progress, query

VERSIONS = ('Q3n', 'Q3sb', 'Q3x')


def judgment(feeling='조심스러움과 안도', movement='maintain'):
    result = plan('advance')
    result['arguments']['appraisal']['immediate_feeling'] = feeling
    result['arguments']['trajectory_assessment'] = progress(movement)['arguments']
    return result


def selection(source='judgment:appraisal.immediate_feeling', first=1, last=1):
    return action('select_client_note_sources', selections=[
        dict(source_id=source, first_line=first, last_line=last)])


class RecordingExperiments(unittest.TestCase):
    setUp = base.MemoryExperiments.setUp
    get = base.MemoryExperiments.get
    create = base.MemoryExperiments.create
    step = base.MemoryExperiments.step
    notes = base.MemoryExperiments.notes

    def tearDown(self):
        Model.q3_summary_response = None
        Model.q3_summary_finish = 'stop'
        base.MemoryExperiments.tearDown(self)

    def continue_session(self, session):
        saved = self.get('/api/paired/session?session_id=' + session['session_id'])
        return json.loads(post(self.server, '/api/paired/manual', {
            'session_id': saved['session_id'], 'expected_step': len(saved['steps']),
            'actor': 'counselor', 'content': '지금 마음은 어떤가요?'}))['session']

    def test_three_flows_share_precommit_decisions_and_store_assessment_not_plan(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                Model.q1_router_response = '{"selections":[],"note_ids":[]}'
                s = self.create(version)
                staged = judgment()
                Model.q2a_actions = [staged, commit(True)] + ([selection()] if version == 'Q3n' else [])
                events = self.step(s)
                self.assertEqual(events[-1]['type'], 'done', events[-1])
                record = self.notes(s)['records'][0]
                self.assertEqual(record['trajectory_assessment'], staged['arguments']['trajectory_assessment'])
                self.assertEqual(record['trajectory_progress']['movement'], 'maintain')
                self.assertEqual(record['trajectory_plan']['movement'], 'advance')
                self.assertEqual(record['trajectory_actual_utterance'], record['utterance'])
                self.assertEqual(record['trajectory_assessment_phase'], 'stage_client_update')
                tools = [e['name'] for e in events if e['type'] == 'tool_start']
                self.assertEqual(tools, ['stage_client_update', 'commit_client_utterance'] +
                                 (['select_client_note_sources'] if version == 'Q3n' else []))
                agents = {e['agent_kind'] for e in events if e['type'] == 'agent_event'}
                self.assertEqual(agents, {'trait_router', 'note_summarizer'} if version == 'Q3sb' else {'trait_router'})
                self.assertEqual(len(record['recording_sources']['pairs']), 2)
                self.assertEqual(record['recording_sources']['pairs'][-1]['reasoning'], 'Q2a_REASON_1')
                requests = [e for e in events if e['type'] == 'request']
                self.assertEqual(len(requests), 3 if version == 'Q3n' else 2)
                for event in requests:
                    for group in event['context_manifest']['groups']:
                        if group['role'] == 'user':
                            self.assertEqual(''.join(p['value'] for p in group['parts'] if p['included']),
                                             event['payload']['messages'][group['message_index']]['content'])
                self.assertEqual(record['agent_usage']['main']['total_tokens'], len(requests) * 130)
                subagent_tokens=sum(usage['total_tokens'] for agent,usage in record['agent_usage'].items() if agent!='main')
                self.assertEqual(subagent_tokens,260 if version=='Q3sb' else 130)

    def test_raw_unicode_multiline_exact_copy_and_no_paraphrase_argument(self):
        Model.q1_router_response = '{"selections":[],"note_ids":[]}'
        s = self.create('Q3n')
        raw = '  조금 안도🙂  \r\n그래도 말하기는 망설임 (내용 없음)\r\n'
        Model.q2a_actions = [judgment(raw), commit(True), selection(first=1, last=2)]
        events = self.step(s)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        note = self.notes(s); rec = note['records'][0]
        selected = rec['raw_selections'][0]
        self.assertEqual(selected['text'], raw)
        self.assertEqual(raw[selected['start']:selected['end']], raw)
        self.assertEqual(note['items'], rec['note_items_after'])
        self.assertTrue(note['items']['notes'][0]['content'].endswith(raw))
        selected_note=registry.client_runtime('Q3n').selected_note(note['items'],[note['items']['notes'][0]['id']])
        self.assertIn('(내용 없음)',selected_note)
        self.assertNotIn('(선택된 항목 없음)',selected_note)
        catalog = next(m['content'] for m in rec['model_requests'][-1]['messages']
                       if m['role'] == 'user' and m['content'].startswith('# 기록할 원문 출처'))
        self.assertIn('first_line', json.dumps(registry.client_runtime('Q3n').TOOLS))
        self.assertIn('exploratory_reasoning', catalog)
        bundle = rec['recording_sources']
        for invalid in ({'selections': [{'source_id':'invented','first_line':1,'last_line':1}]},
                        {'selections':[{'source_id':selected['source_id'],'first_line':True,'last_line':1}]},
                        {'selections':[{'source_id':selected['source_id'],'first_line':1,'last_line':99}]},
                        {'selections':[{'source_id':selected['source_id'],'first_line':1,'last_line':1,'content':'왜곡'}]}):
            with self.assertRaises(ValueError):recording.select(bundle, invalid)
        overlap = selection(first=1, last=2)['arguments']
        overlap['selections'].append(selection()['arguments']['selections'][0])
        with self.assertRaises(ValueError):recording.select(bundle, overlap)

    def test_summary_gets_all_pairs_with_revisions_and_committed_speech_then_stores_directly(self):
        Model.q1_router_response = '{"selections":[],"note_ids":[]}'
        Model.q3_summary_response = {'notes':['  반환 원문🙂\n추가 줄  '], 'trajectory':{
            'current_experience':'  주저한다.  ', 'grounds':'이미 생성한 근거', 'next_condition':'남은 조건'}}
        s = self.create('Q3sb')
        invalid = judgment(); invalid['arguments'].pop('trajectory_assessment')
        final = judgment('수정된 마음')
        Model.q2a_actions = [invalid, judgment('수정 전 마음'), final, commit(True)]
        events = self.step(s)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        rec = self.notes(s)['records'][0]
        pairs = rec['recording_sources']['pairs']
        self.assertEqual([p['status'] for p in pairs], ['rejected', 'superseded', 'committed', 'committed'])
        req = rec['subagent_requests']['note_summarizer']
        user = req['messages'][-1]['content']
        payload_bundle = json.loads(user.split('# 이번 턴 판단·발화의 reasoning과 출력 원문 전체\n\n')[1])
        self.assertEqual(payload_bundle, rec['recording_sources'])
        self.assertEqual([p['reasoning'] for p in payload_bundle['pairs']], ['Q2a_REASON_' + str(i) for i in range(4)])
        self.assertEqual(pairs[-1]['output'], commit(True)['arguments'])
        self.assertEqual(rec['summary_output'], Model.q3_summary_response)
        self.assertTrue(rec['note_items_after']['notes'][0]['content'].endswith(Model.q3_summary_response['notes'][0]))
        self.assertEqual(rec['trajectory_progress']['current_experience'], '  주저한다.  ')
        self.assertEqual(rec['trajectory_progress']['movement'], final['arguments']['trajectory_assessment']['movement'])
        self.assertNotEqual(rec['router_request'], req)
        # No main request follows the summarizer request.
        summary_index = next(i for i,e in enumerate(events) if e['type']=='agent_event' and e['agent_kind']=='note_summarizer')
        self.assertFalse(any(e['type']=='request' for e in events[summary_index:]))

    def test_snapshot_replaces_current_note_but_retains_retrievable_history(self):
        Model.q1_router_response = '{"selections":[],"note_ids":[]}'
        s = self.create('Q3x')
        Model.q2a_actions = [judgment('OLD_ONLY'), commit(True)]
        self.assertEqual(self.step(s)[-1]['type'], 'done')
        s = self.continue_session(s)
        staged = judgment('NEW_ONLY\n  그대로  ')
        Model.q2a_actions = [staged, commit(True)]
        events = self.step(s); self.assertEqual(events[-1]['type'], 'done', events[-1])
        note = self.notes(s); rec = note['records'][-1]
        self.assertNotIn('OLD_ONLY', note['content'])
        self.assertIn('NEW_ONLY', note['content'])
        for field in recording.FIELDS:
            for key,value in staged['arguments'][field].items():
                self.assertIn({'id': next(r['id'] for r in rec['note_items_after']['notes'] if r['content']==field+'.'+key+'\n'+value),
                               'content':field+'.'+key+'\n'+value}, note['items']['notes'])
        found = memory.lookup(note, {**query('episodes'), 'turns':[1]})
        self.assertIn('OLD_ONLY', json.dumps(found, ensure_ascii=False))
        self.assertEqual(len(rec['model_requests']), 2)
        self.assertEqual(len(rec['note_items_after']['notes']), len(note['records'][0]['note_items_after']['notes']))

    def test_raw_carry_preserves_original_text_and_does_not_nest_wrappers(self):
        Model.q1_router_response = '{"selections":[],"note_ids":[]}'
        s = self.create('Q3n')
        Model.q2a_actions = [judgment('이전 조건'), commit(True), selection()]
        self.assertEqual(self.step(s)[-1]['type'], 'done')
        first = self.notes(s)['items']['notes'][0]
        for _ in range(2):
            s = self.continue_session(s)
            pick = selection()['arguments']['selections'] + [dict(source_id='previous:'+first['id'],first_line=1,last_line=1)]
            Model.q2a_actions = [judgment('현재 마음'),commit(True),action('select_client_note_sources',selections=pick)]
            events = self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
            items = self.notes(s)['items']['notes']
            self.assertIn(first, items)
            self.assertEqual(len(items), 2)

    def test_invalid_selection_and_summary_failure_leave_no_partial_turn(self):
        Model.q1_router_response = '{"selections":[],"note_ids":[]}'
        for version in ('Q3n','Q3sb'):
            with self.subTest(version=version):
                s = self.create(version)
                Model.q2a_actions = [judgment(),commit(True)]
                if version=='Q3n':Model.q2a_actions.append(selection('invented'))
                else:Model.q3_summary_finish='length'
                events=self.step(s);self.assertEqual(events[-1]['type'],'error',events[-1])
                self.assertEqual(self.notes(s)['records'],[])
                self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])
        Model.q3_summary_finish='stop'
        s=self.create('Q3sb')
        Model.q3_summary_response={'notes':['不正'],'trajectory':{'position':'改変','movement':'advance'}}
        self.assertEqual(self.step(s)[-1]['type'],'error')
        self.assertEqual(self.notes(s)['records'],[])

    def test_cancellation_after_commit_discards_auto_recording(self):
        Model.q1_router_response = '{"selections":[],"note_ids":[]}'
        s=self.create('Q3x');Model.q2a_actions=[judgment(),commit(True)]
        from runtime.cancellation import Cancelled
        # Cancellation at the save boundary must discard committed-but-unsaved speech.
        rt=registry.client_runtime('Q3x')
        with patch.object(rt,'write_new',side_effect=Cancelled('검증 취소')):
            events=self.step(s)
        self.assertIn(events[-1]['type'],('cancelled','error'))
        self.assertEqual(self.notes(s)['records'],[])
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])

    def test_registration_editable_summary_prompt_and_version_isolation(self):
        runtimes=[registry.client_runtime(v) for v in VERSIONS]
        self.assertEqual(len({id(r.LOCK) for r in runtimes}),3)
        for version in VERSIONS:
            tools=json.loads(post(self.server,'/api/tools',{'experiment_version':version}))['tools']
            self.assertEqual([t['function']['name'] for t in tools],registry.client_runtime(version).specification()['required_tools'])
            for kind in ('system','user','trait_router','trait_router_user','trajectory'):
                source=self.get('/api/prompts/source?role=client&version='+version+'&kind='+kind)
                self.assertIn('/'+version+'/',source['path'])
        before=self.get('/api/prompts/source?role=client&version=Q3&kind=system')['content']
        source=self.get('/api/prompts/source?role=client&version=Q3sb&kind=note_summarizer')
        edited=source['content']+'\nSUMMARY_EDIT_MARKER\n'
        post(self.server,'/api/prompts/save',{**source,'content':edited})
        Model.q1_router_response='{"selections":[],"note_ids":[]}'
        s=self.create('Q3sb');Model.q2a_actions=[judgment(),commit(True)]
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertIn('SUMMARY_EDIT_MARKER',self.notes(s)['records'][0]['subagent_requests']['note_summarizer']['messages'][0]['content'])
        self.assertEqual(before,self.get('/api/prompts/source?role=client&version=Q3&kind=system')['content'])


if __name__=='__main__':unittest.main()
