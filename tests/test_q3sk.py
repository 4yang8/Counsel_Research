"""Q3 skill timing, source snapshots, context boundaries and rollback via HTTP."""
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import client.registry as registry
import runtime.paired_lab as paired_lab
import test_memory_experiments as base
from test_memory_experiments import Model, action, commit, post
from test_f_free_notes import op
from test_q3 import plan, progress
from client.Q3sk import skills_runtime


def finish(operations=None):
    return [plan(), commit(True),
            action('update_client_notes', operations=operations or []), progress()]


class SkillFileTests(unittest.TestCase):
    def test_q3_instructions_and_tools_are_preserved(self):
        q3 = registry.client_runtime('Q3')
        sk = registry.client_runtime('Q3sk')
        text = (q3.HERE / 'system.md').read_text()
        original = text[text.index('## 내담자 노트 갱신\n'):text.index('## 판단·조회·발화 확정\n')]
        loaded = skills_runtime.snapshot(sk.HERE, 3)
        self.assertEqual(skills_runtime.instructions(loaded).split('\n\n', 1)[1], original)
        self.assertNotIn(original, sk.specification()['system_prompt'])
        self.assertEqual(q3.TOOLS, sk.TOOLS)
        self.assertIsNot(q3.TOOLS, sk.TOOLS)
        for filename in ('trait_router.md', 'trait_router_user.md', 'user.md', 'trajectory.md'):
            self.assertEqual((q3.HERE / filename).read_bytes(), (sk.HERE / filename).read_bytes())

    def test_invalid_skill_headers_and_empty_instructions(self):
        for text in ('', '---\nname: other\ndescription: text\n---\nbody',
                     '---\nname: client-note-update\ndescription: text\n---\n',
                     '---\nname: client-note-update\ndescription: text\nbody',
                     '---\nname: client-note-update\nname: client-note-update\n---\nbody'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                skills_runtime.validate(text)


class Q3skTests(unittest.TestCase):
    setUp = base.MemoryExperiments.setUp
    tearDown = base.MemoryExperiments.tearDown
    get = base.MemoryExperiments.get
    create = base.MemoryExperiments.create
    step = base.MemoryExperiments.step
    notes = base.MemoryExperiments.notes

    def prepare(self):
        Model.q1_router_response = '{"selections":[],"note_ids":[]}'
        return self.create('Q3sk')

    def skill_source(self):
        return self.get('/api/prompts/source?role=client&version=Q3sk&kind=skill_notes')

    def continue_session(self, session):
        saved = self.get('/api/paired/session?session_id=' + session['session_id'])
        return json.loads(post(self.server, '/api/paired/manual', {
            'session_id': session['session_id'], 'expected_step': len(saved['steps']),
            'actor': 'counselor', 'content': '話せる範囲で大丈夫です。'}))['session']

    def test_skill_only_after_commit_and_next_turn_starts_without_body(self):
        session = self.prepare()
        source = self.skill_source()
        marker = '\nSKILL_BODY_SENTINEL\n'
        post(self.server, '/api/prompts/save', {**source, 'content': source['content'] + marker})
        Model.q2a_actions = finish([op('add', '選択する心配'), op('add', '保持する別の心配')])
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        note = self.notes(session)
        record = note['records'][0]
        self.assertEqual(record['skill_mode'], 'phase_loaded')
        self.assertEqual(len(record['skill_loads']), 1)
        skill = record['skill_loads'][0]
        self.assertEqual(skill['iteration'], 3)
        self.assertEqual(skill['sha256'], hashlib.sha256(skill['content'].encode()).hexdigest())
        requests = record['model_requests']
        self.assertEqual(len(requests), 4)
        self.assertEqual([sum(bool(m.get('reasoning')) for m in r['messages']) for r in requests], [0, 1, 2, 3])
        for index, req in enumerate(requests):
            text = '\n'.join(m.get('content') or '' for m in req['messages'])
            self.assertEqual(marker.strip() in text, index >= 2)
            self.assertEqual(text.count(marker.strip()), int(index >= 2))
        self.assertNotIn(marker.strip(), json.dumps(record['router_request'], ensure_ascii=False))
        self.assertLess(next(i for i, e in enumerate(events) if e['type'] == 'tool_result' and e['name'] == 'commit_client_utterance'),
                        next(i for i, e in enumerate(events) if e['type'] == 'version_event' and e.get('section') == 'skills' and e['action'] == 'loaded'))
        session = self.continue_session(session)
        selected, omitted = note['items']['notes']
        Model.q1_router_response = json.dumps({'selections': [], 'note_ids': [selected['id']]})
        Model.q2a_actions = finish()
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        record = self.notes(session)['records'][1]
        requests = record['model_requests']
        self.assertNotIn(marker.strip(), json.dumps(requests[0], ensure_ascii=False))
        first_user = next(m['content'] for m in requests[0]['messages'] if m['role'] == 'user')
        last_user = next(m['content'] for m in requests[-1]['messages'] if m['role'] == 'user')
        self.assertNotIn(omitted['content'], first_user)
        self.assertIn(omitted['content'], last_user)
        self.assertFalse(any(m.get('reasoning') for m in requests[0]['messages']))
        for event in (e for e in events if e['type'] == 'request'):
            for group in event['context_manifest']['groups']:
                if group['role'] == 'user':
                    actual = event['payload']['messages'][group['message_index']]['content']
                    self.assertEqual(''.join(p['value'] for p in group['parts'] if p['included']), actual)

    def test_skill_edit_is_live_but_completed_source_and_q3_are_unchanged(self):
        q3_before = registry.client_runtime('Q3').specification()
        session = self.prepare()
        Model.q2a_actions = finish()
        self.assertEqual(self.step(session)[-1]['type'], 'done')
        original_record = self.notes(session)['records'][0]['skill_loads'][0]
        source = self.skill_source()
        post(self.server, '/api/prompts/save', {**source, 'content': source['content'] + '\nNEXT_TURN_SKILL\n'})
        session = self.continue_session(session)
        Model.q2a_actions = finish()
        self.assertEqual(self.step(session)[-1]['type'], 'done')
        records = self.notes(session)['records']
        self.assertEqual(records[0]['skill_loads'][0], original_record)
        self.assertIn('NEXT_TURN_SKILL', records[1]['skill_loads'][0]['content'])
        self.assertNotEqual(records[0]['skill_loads'][0]['sha256'], records[1]['skill_loads'][0]['sha256'])
        self.assertEqual(q3_before, registry.client_runtime('Q3').specification())
        for kind in ('system', 'user', 'trait_router', 'trait_router_user', 'trajectory', 'skill_notes'):
            source = self.get('/api/prompts/source?role=client&version=Q3sk&kind=' + kind)
            self.assertIn('/Q3sk/', source['path'])

    def test_retries_keep_one_skill_snapshot_and_failure_rolls_back(self):
        session = self.prepare()
        sk = registry.client_runtime('Q3sk')
        source = skills_runtime.path(sk.HERE)
        original = skills_runtime.snapshot
        def load_once(directory, iteration):
            snapshot = original(directory, iteration)
            source.write_text(snapshot['content'] + '\nEXTERNAL_EDIT_AFTER_LOAD\n')
            return snapshot
        Model.q2a_actions = [plan(), commit(True),
            action('update_client_notes', operations=[op('delete', item_id='nffffffffffff')]),
            action('update_client_notes', operations=[]), progress()]
        with patch.object(skills_runtime, 'snapshot', side_effect=load_once) as loader:
            events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        self.assertEqual(loader.call_count, 1)
        record = self.notes(session)['records'][0]
        self.assertEqual(len(record['model_requests']), 5)
        self.assertNotIn('EXTERNAL_EDIT_AFTER_LOAD', json.dumps(record, ensure_ascii=False))
        session = self.continue_session(session)
        before = self.notes(session)['content']
        Model.q2a_actions = [plan(), commit(True),
            action('update_client_notes', operations=[op('add', '失敗した変更')]), progress('invalid')]
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(len(self.notes(session)['records']), 1)
        self.assertEqual(self.notes(session)['content'], before)
        self.assertTrue(any(e.get('section') == 'skills' and e.get('action') == 'discarded' for e in events))
        saved = self.get('/api/paired/session?session_id=' + session['session_id'])
        self.assertEqual(len(saved['steps']), 2)

    def test_missing_skill_after_commit_rolls_back_without_publication(self):
        session = self.prepare()
        Model.q2a_actions = finish([op('add', '未保存')])
        with patch.object(skills_runtime, 'snapshot', side_effect=ValueError('スキルがありません')):
            events = self.step(session)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(self.notes(session)['records'], [])
        self.assertEqual(self.get('/api/paired/session?session_id=' + session['session_id'])['steps'], [])

    def test_system_disabled_and_prepend_user_are_supported(self):
        session = self.prepare()
        saved = paired_lab.read_session(self.server, session['session_id'])
        saved['settings']['input_blocks'] = {'system': False}
        paired_lab.save_session(self.server, saved)
        Model.q2a_actions = finish()
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        record = self.notes(session)['records'][0]
        self.assertEqual(record['skill_loads'], [])
        self.assertEqual(record['skill_mode'], 'disabled')
        session = self.prepare()
        config_path = Path(self.server.config_path)
        config = json.loads(config_path.read_text())
        config['system_role_mode'] = 'prepend_user'
        config_path.write_text(json.dumps(config))
        Model.q2a_actions = finish()
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        requests = self.notes(session)['records'][0]['model_requests']
        self.assertFalse(any(m['role'] == 'system' for m in requests[-1]['messages']))
        self.assertIn('# 실행 스킬', requests[-1]['messages'][0]['content'])

    def test_invalid_skill_save_rejected_and_symlink_is_not_loaded(self):
        source = self.skill_source()
        with self.assertRaises(HTTPError) as error:
            post(self.server, '/api/prompts/save', {**source, 'content': 'invalid skill'})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.skill_source()['revision'], source['revision'])
        sk = registry.client_runtime('Q3sk')
        path = skills_runtime.path(sk.HERE)
        other = Path(self.temp.name) / 'external-skill.md'
        other.write_text(source['content'])
        path.unlink()
        path.symlink_to(other)
        with self.assertRaises(ValueError):
            skills_runtime.describe(sk.HERE)


if __name__ == '__main__':
    unittest.main()
