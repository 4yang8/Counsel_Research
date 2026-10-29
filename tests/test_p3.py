"""Mock transport tests: verify P3 mechanics, not clinical routing quality."""
import copy
import json
import unittest
from unittest.mock import patch

import test_p1
from test_lab import Model, close, fixture, post
from counselor.P1.runtime import read
from counselor.P2 import runtime as p2
from counselor.P3 import runtime as p3


class P3Tests(unittest.TestCase):
    get = test_p1.P1Tests.get
    manual_client = test_p1.P1Tests.manual_client
    step = test_p1.P1Tests.step

    def setUp(self):
        self.server, self.model, self.temp = fixture()
        Model.requests.clear()
        Model.p2_actions = None
        Model.p3_actions = None
        Model.dual_notes_finish = 'stop'

    def tearDown(self):
        Model.p2_actions = None
        Model.p3_actions = None
        Model.dual_notes_finish = 'stop'
        close(self.server, self.model, self.temp)

    def create(self, version='P3', **settings):
        config = self.get('/api/config')
        return json.loads(post(self.server, '/api/paired/create', {
            'scenario':config['scenario'], 'generation':config['generation'],
            'experiment_version':'state_summary', 'counselor_version':version,
            'enabled_tools':[], 'thinking_mode':'on',
            'messages':[{'role':'user','content':'오늘은 어떤 이야기를 해보고 싶으세요?'}], **settings}))

    def directory(self, session):
        return self.server.storage_dir / 'paired' / session['session_id']

    def test_p3_seeds_templates_without_overwriting_existing_notes(self):
        directory = self.directory(self.create())
        for target, (filename, _) in p2.NOTES.items():
            self.assertEqual(read(directory / filename), p3.NOTE_TEMPLATES[target].read_text(encoding='utf-8'))
        # An empty saved note is existing user data too. Only missing files
        # receive the template, including when initialization is repeated.
        (directory / 'notes.md').write_text('', encoding='utf-8')
        (directory / 'counselor_notes.md').write_text('기존 세션의 작업 기록', encoding='utf-8')
        p3.initialize(directory)
        self.assertEqual(read(directory / 'notes.md'), '')
        self.assertEqual(read(directory / 'counselor_notes.md'), '기존 세션의 작업 기록')
        (directory / 'counselor_notes.md').unlink()
        p3.initialize(directory)
        self.assertEqual(read(directory / 'notes.md'), '')
        self.assertEqual(read(directory / 'counselor_notes.md'), p3.NOTE_TEMPLATES['counselor'].read_text(encoding='utf-8'))

    def test_p3_metadata_and_tool_are_independent_of_p2(self):
        specs = {s['id']:s for s in self.get('/api/paired/counselor')['versions']}
        self.assertEqual(specs['P3']['view'], 'dual_notes')
        self.assertEqual(specs['P2'], p2.specification())
        # Same tool shape; descriptions change without adding state parameters.
        schema = copy.deepcopy(specs['P3']['tools'][0]['function']['parameters'])
        schema['properties']['target']['description'] = p2.TOOL['function']['parameters']['properties']['target']['description']
        self.assertEqual(schema, p2.TOOL['function']['parameters'])
        self.assertEqual([t['function']['name'] for t in specs['P3']['tools']], ['update_notes', 'read_counseling_guide'])
        self.assertEqual([t['function']['name'] for t in specs['P2']['tools']], ['update_notes'])
        guide_schema = specs['P3']['tools'][1]['function']['parameters']
        self.assertEqual(guide_schema['properties']['guide']['enum'], ['routing','common','mi','cbt'])
        self.assertEqual(guide_schema['required'], ['guide'])
        changed = p3.specification()
        changed['tools'][0]['function']['parameters']['required'].append('method')
        changed['tools'][1]['function']['parameters']['properties']['guide']['enum'].append('other')
        self.assertEqual(p3.TOOL['function']['parameters']['required'], ['target','edits'])
        self.assertEqual(p3.GUIDE_TOOL['function']['parameters']['properties']['guide']['enum'], ['routing','common','mi','cbt'])
        self.assertNotIn('P3', specs['P3']['system_prompt'])

    def test_guides_are_selected_by_name_and_return_the_exact_current_file(self):
        for guide, path in p3.GUIDE_FILES.items():
            result = p3.call_guide('read_counseling_guide', {'guide': guide})
            self.assertEqual(result, {'content':[{'type':'text','text':path.read_text(encoding='utf-8')}]})
        for arguments in ({}, {'guide':'../notes.md'}, {'guide':None}, {'guide':[]},
                          {'guide':'common','path':'notes.md'}, []):
            with self.assertRaises(ValueError):
                p3.call_guide('read_counseling_guide', arguments)
        with self.assertRaises(ValueError):
            p3.call_guide('unknown_tool', {'guide':'common'})

    def test_guide_results_and_note_updates_share_one_reasoning_preserving_loop(self):
        session = self.manual_client(self.create(), '발표 상황에 어떻게 대응할지 함께 정하고 싶어요.')
        note = '현재 기법: Common. 작업: 원하는 도움 협의.'
        Model.p3_actions = [
            {'name':'read_counseling_guide','arguments':{'guide':'routing'}},
            {'name':'read_counseling_guide','arguments':{'guide':'common'}},
            {'name':'update_notes','arguments':{'target':'counselor','edits':[{'old_text':read(self.directory(session) / 'counselor_notes.md'),'new_text':note}]}},
        ]
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        calls = [e['payload'] for e in events if e['type']=='request']
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(c['messages'][1] == calls[0]['messages'][1] for c in calls))
        self.assertEqual([m['role'] for m in calls[0]['messages']], ['system','user'])
        replies = [json.loads(m['content']) for m in calls[-1]['messages'] if m['role']=='tool']
        for index, guide in enumerate(('routing','common')):
            original = p3.GUIDE_FILES[guide].read_text(encoding='utf-8')
            self.assertEqual(replies[index]['content'][0]['text'], original)
            self.assertNotIn(original, calls[0]['messages'][1]['content'])
        self.assertTrue(replies[-1]['content'][0]['text'])
        self.assertEqual([m['reasoning'] for m in calls[-1]['messages'] if m.get('tool_calls')],
                         ['P3_REASON_0','P3_REASON_1','P3_REASON_2'])
        self.assertFalse(any(e['truncated'] for e in events if e['type']=='tool_result'))
        updates = [e for e in events if e['type']=='version_event' and e['action']=='updated']
        self.assertEqual([e['section'] for e in updates], ['counselor_notes'])
        self.assertEqual(read(self.directory(session) / 'counselor_notes.md'), note)
        self.assertNotIn('# Common 활용 가이드', json.dumps(events[-1]['session']['public_dialogue'], ensure_ascii=False))
        # The next independent turn gets the saved note, not previous guide
        # results or raw reasoning. Guides can be queried again when needed.
        Model.p3_actions = []
        next_session = self.manual_client(events[-1]['session'], '이어서 이야기할게요.')
        following = self.step(next_session)
        first = next(e['payload'] for e in following if e['type']=='request')
        self.assertEqual([m['role'] for m in first['messages']], ['system','user'])
        self.assertTrue(first['messages'][1]['content'].endswith(note))
        self.assertNotIn('P3_REASON', json.dumps(first['messages']))

    def test_long_guide_is_not_truncated_and_external_edits_are_read_on_each_call(self):
        path = self.server.storage_dir / 'test_common.md'
        original = '# Common 활용 가이드\n' + '원문을 그대로 반환합니다.\n' * 1500
        path.write_text(original, encoding='utf-8')
        Model.p3_actions = [{'name':'read_counseling_guide','arguments':{'guide':'common'}}]
        with patch.dict(p3.GUIDE_FILES, {'common':path}):
            session = self.manual_client(self.create(), '긴 가이드 반환 테스트')
            events = self.step(session)
            result = next(e for e in events if e['type']=='tool_result')
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            self.assertGreater(len(result['model_content']), 16000)
            self.assertFalse(result['truncated'])
            self.assertEqual(json.loads(result['model_content'])['content'][0]['text'], original)
            changed = '# 수정한 Common 가이드\n변경된 원문입니다.\n'
            path.write_text(changed, encoding='utf-8')
            session = self.manual_client(events[-1]['session'], '갱신된 가이드 반환 테스트')
            following = self.step(session)
            updated = next(e for e in following if e['type']=='tool_result')
            self.assertEqual(json.loads(updated['model_content'])['content'][0]['text'], changed)

    def test_bad_guide_arguments_and_missing_files_are_reported_as_tool_errors(self):
        session = self.manual_client(self.create(), '가이드 조회 오류 복구 테스트')
        Model.p3_actions = [
            {'name':'read_counseling_guide','arguments':{'guide':'../notes.md'}},
            {'name':'read_counseling_guide','arguments':{'guide':'common'}},
        ]
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        results = [e for e in events if e['type']=='tool_result']
        self.assertEqual([e['is_error'] for e in results], [True, False])
        self.assertEqual(read(self.directory(session) / 'notes.md'), p3.NOTE_TEMPLATES['client'].read_text(encoding='utf-8'))
        with patch.dict(p3.GUIDE_FILES, {'common':self.server.storage_dir / 'missing.md'}):
            with self.assertRaisesRegex(ValueError, '가이드 파일을 읽지 못했습니다'):
                p3.call_guide('read_counseling_guide', {'guide':'common'})

    def test_work_notes_cross_turns_without_runtime_mode_restrictions(self):
        session = self.create()
        directory = self.directory(session)
        previous_client = read(directory / 'notes.md')
        previous_counselor = read(directory / 'counselor_notes.md')
        # The mock supplies decisions. These assertions test their storage and
        # propagation, not whether a real model chooses these modes correctly.
        turns = [
            ('발표가 걱정돼요.', '현재 고민: 발표 두려움. 원하는 도움·초점: 미확인.',
             '현재 기법: Common\n작업: 발표 경험 탐색 제안(proposed).\n공개 근거: "발표가 걱정돼요."'),
            ('그때 떠오르는 생각부터 같이 보고 싶어요.', '원하는 도움: 발표 때 생각 살펴보기. 초점: 확인됨.',
             '현재 기법: CBT\n작업: 발표 당시 생각 탐색 수용(accepted), 진행.\n공개 근거: "같이 보고 싶어요."'),
            ('그런데 바꿔보고 싶으면서도 잘될지 모르겠어요.', '현재 고민: 발표 두려움. 변화에 대한 망설임을 표현함.',
             '현재 기법: MI\n조정: Temporary Shift\n보류 작업: 합의된 발표 당시 생각 탐색.\n재개 조건: 내담자 반응을 보고 재평가; 복귀 강제 없음.'),
            ('발표보다 지금 다른 고민을 이야기하고 싶어요.', '원하는 도움·초점: 다른 고민으로 방향 변경(redirected).',
             '현재 기법: Common\n조정: Switch\n기존 작업: 중단. 현재 요청을 명료화.\n공개 근거: "다른 고민을 이야기하고 싶어요."'),
        ]
        for client, client_note, counselor_note in turns:
            session = self.manual_client(session, client)
            Model.p2_actions = [
                {'target':'client','edits':[{'old_text':previous_client,'new_text':client_note}]},
                {'target':'counselor','edits':[{'old_text':previous_counselor,'new_text':counselor_note}]},
            ]
            events = self.step(session)
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            calls = [e['payload'] for e in events if e['type']=='request']
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(c['messages'][1] == calls[0]['messages'][1] for c in calls))
            self.assertTrue(calls[0]['messages'][1]['content'].endswith(previous_counselor))
            self.assertEqual([m['role'] for m in calls[0]['messages']], ['system','user'])
            self.assertEqual([m['reasoning'] for m in calls[-1]['messages'] if m.get('tool_calls')], ['P3_REASON_0','P3_REASON_1'])
            self.assertTrue(all(e['version']=='P3' for e in events if e['type']=='version_event'))
            self.assertEqual(read(directory / 'notes.md'), client_note)
            self.assertEqual(read(directory / 'counselor_notes.md'), counselor_note)
            session = events[-1]['session']
            self.assertNotIn('P3_REASON', json.dumps(session['public_dialogue']))
            self.assertIsNone(session['counselor_checkpoint'])
            previous_client, previous_counselor = client_note, counselor_note
        restored = self.get('/api/paired/session?session_id='+session['session_id'])
        self.assertEqual(restored['counselor_version'], 'P3')
        state = self.get('/api/paired/version_state?session_id='+session['session_id'])
        self.assertEqual(state['data']['counselor_notes']['current']['content'], previous_counselor)

    def test_no_write_is_allowed_and_custom_system_prompt_is_frozen(self):
        prompt = p3.specification()['system_prompt'] + '\n사용자가 편집한 추가 지침입니다.'
        session = self.manual_client(self.create(counselor_system_prompt=prompt), '안녕하세요.')
        Model.p2_actions = []
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertFalse(any(e['type']=='tool_start' for e in events))
        request = next(e['payload'] for e in events if e['type']=='request')
        self.assertEqual(request['messages'][0]['content'], prompt)
        self.assertIn(p3.NOTE_TEMPLATES['client'].read_text(encoding='utf-8'), request['messages'][1]['content'])
        self.assertTrue(request['messages'][1]['content'].endswith(p3.NOTE_TEMPLATES['counselor'].read_text(encoding='utf-8')))
        other = self.directory(self.create('P2'))
        self.assertEqual(read(other / 'notes.md'), '')
        self.assertEqual(read(other / 'counselor_notes.md'), '')

    def test_saved_notes_survive_failure_without_committing_public_response(self):
        session = self.manual_client(self.create(), '발표가 걱정돼요.')
        Model.p2_actions = [{'target':'counselor','edits':[{'old_text':'','new_text':'기법: Common. 다음 반응을 참고해 재평가.'}]}]
        Model.dual_notes_finish = 'length'
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertIn('P3 응답이 정상 완료되지', events[-1]['error'])
        self.assertIn('기법: Common. 다음 반응을 참고해 재평가.', read(self.directory(session) / 'counselor_notes.md'))
        restored = self.get('/api/paired/session?session_id='+session['session_id'])
        self.assertEqual(restored['steps'], session['steps'])
        self.assertEqual(restored['public_dialogue'], session['public_dialogue'])
        self.assertEqual(restored['next_actor'], 'counselor')
