"""Exercise persisted counselor budgets across real HTTP/tool handoffs."""
import json
import unittest
from urllib.error import HTTPError

import test_p3
import test_p3a
from test_lab import Model, post


class ReasoningBudgetTests(unittest.TestCase):
    setUp = test_p3a.P3aTests.setUp
    tearDown = test_p3a.P3aTests.tearDown
    get = test_p3a.P3aTests.get
    create = test_p3a.P3aTests.create
    directory = test_p3a.P3aTests.directory
    manual_client = test_p3a.P3aTests.manual_client
    step = test_p3a.P3aTests.step
    action = test_p3a.P3aTests.action

    def generate(self, **settings):
        session = self.manual_client(self.create(**settings), '지금 상황에서 어떻게 진행할지 확인하고 싶어요.')
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        return events

    def test_unset_is_omitted_from_both_main_and_child(self):
        Model.p3a_actions = [self.action()]
        events = self.generate()
        self.assertEqual(len(Model.requests), 3)
        self.assertTrue(all('thinking_token_budget' not in row for row in Model.requests))
        self.assertEqual(events[-1]['session']['settings']['counselor_reasoning_budgets'],
                         {'main': None, 'subagent': None})

    def test_subagent_only_limit_does_not_limit_main(self):
        Model.p3a_actions = [self.action()]
        events = self.generate(counselor_reasoning_budgets={'subagent': 512})
        main, child, resumed = Model.requests
        self.assertNotIn('thinking_token_budget', main)
        self.assertEqual(child['thinking_token_budget'], 512)
        self.assertNotIn('thinking_token_budget', resumed)
        saved = events[-1]['session']
        self.assertEqual(saved['settings']['counselor_reasoning_budgets'], {'main': None, 'subagent': 512})
        restored = self.get('/api/paired/session?session_id=' + saved['session_id'])
        self.assertEqual(restored['settings']['counselor_reasoning_budgets'], saved['settings']['counselor_reasoning_budgets'])
        child_request = next(e['event']['payload'] for e in events
                             if e['type'] == 'agent_event' and e['event']['type'] == 'request')
        self.assertEqual(child_request['thinking_token_budget'], 512)
        self.assertEqual(child['max_tokens'], saved['settings']['generation']['max_tokens'])
        self.assertTrue(all('_counselor_budget_role' not in row for row in Model.requests))

    def test_main_only_does_not_inherit_into_unlimited_child(self):
        Model.p3a_actions = [self.action()]
        self.generate(counselor_reasoning_budgets={'main': 256})
        self.assertEqual([row.get('thinking_token_budget') for row in Model.requests], [256, None, 256])
        self.assertEqual(Model.requests[-1]['messages'][-2]['reasoning'], 'P3a_REASON_0')

    def test_independent_limits_survive_multiple_tool_continuations_and_next_turn(self):
        budgets = {'main': 256, 'subagent': 512}
        Model.p3a_actions = [self.action('routing'), self.action('cbt')]
        events = self.generate(counselor_reasoning_budgets=budgets)
        self.assertEqual([row.get('thinking_token_budget') for row in Model.requests], [256, 512, 256, 512, 256])
        saved = self.manual_client(events[-1]['session'], '앞서 이야기한 내용을 이어가고 싶어요.')
        Model.requests.clear()
        Model.p3a_actions = [self.action()]
        followup = self.step(saved)
        self.assertEqual(followup[-1]['type'], 'done', followup[-1])
        self.assertEqual([row.get('thinking_token_budget') for row in Model.requests], [256, 512, 256])
        self.assertEqual(followup[-1]['session']['settings']['counselor_reasoning_budgets'], budgets)

    def test_client_generation_does_not_receive_counselor_budgets(self):
        session = self.create(counselor_reasoning_budgets={'main': 256, 'subagent': 512})
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        self.assertTrue(Model.requests)
        self.assertTrue(all('thinking_token_budget' not in row for row in Model.requests))

    def test_basic_and_c4_counselor_calls_use_main_budget(self):
        for version in ('basic', 'C4'):
            with self.subTest(version=version):
                session = test_p3.P3Tests.create(self, version=version,
                    counselor_reasoning_budgets={'main': 128})
                session = self.manual_client(session, '사람들 앞에서 말하는 일이 걱정돼요.')
                Model.requests.clear()
                events = self.step(session)
                self.assertEqual(events[-1]['type'], 'done', events[-1])
                self.assertTrue(Model.requests)
                self.assertTrue(all(row.get('thinking_token_budget') == 128 for row in Model.requests))

    def test_legacy_session_without_budget_setting_stays_unlimited(self):
        session = self.manual_client(self.create(), '이전에 저장한 대화입니다.')
        path = self.directory(session) / 'session.json'
        stored = json.loads(path.read_text())
        stored['settings'].pop('counselor_reasoning_budgets')
        path.write_text(json.dumps(stored, ensure_ascii=False), encoding='utf-8')
        Model.p3a_actions = [self.action()]
        events = self.step(session)
        self.assertEqual(events[-1]['type'], 'done', events[-1])
        self.assertTrue(all('thinking_token_budget' not in row for row in Model.requests))

    def test_invalid_budgets_are_rejected_before_session_creation_or_inference(self):
        before = set((self.server.storage_dir / 'paired').glob('*/session.json'))
        bad = [False, [], '512', {'other': 512}]
        bad.extend({scope: value} for scope in ('main', 'subagent')
                   for value in (True, 0, -1, 1.5, '512', 1_000_001))
        for value in bad:
            with self.subTest(value=value), self.assertRaises(HTTPError) as error:
                self.create(counselor_reasoning_budgets=value)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(Model.requests, [])
        self.assertEqual(set((self.server.storage_dir / 'paired').glob('*/session.json')), before)


if __name__ == '__main__':
    unittest.main()
