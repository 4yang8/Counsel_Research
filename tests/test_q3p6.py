"""HTTP integration: shared interventions, snapshot isolation and durable resume."""
import copy
import io
import json
from pathlib import Path
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from test_lab import Model, fixture, close, post
from test_q3 import plan, progress
from test_memory_experiments import action, commit
from runtime import q3p6
from client.Q3p6.adapter import memory_view
from client.Q3p6.public_only import resolve_note_evidence
from client import registry


class BranchModel(Model):
    eligible = True
    bad_quote = False
    proposal_gate = None
    context_fit = 'natural'
    bad_public_note = False
    wrong_public_index = False
    omitted_public_quote = False

    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        data = json.loads(raw)
        props = data.get('structured_outputs', {}).get('json', {}).get('properties', {})
        if 'public_notes' in props or set(props) == {'utterance'}:
            self.requests.append(data)
            source = json.loads(data['messages'][-1]['content'])
            if 'public_notes' in props:
                i = len(source['public_dialogue']) - 1
                value = {'public_notes': [{'message_index': (0 if self.wrong_public_index else i), 'quote': 'INVALID_NOTE' if self.bad_public_note else source['public_dialogue'][i]['content']}]}
            else:
                value = {'utterance': '아직 어떻게 할지 정하지 못했어요.'}
            if self.omitted_public_quote:
                if 'public_notes' in props:
                    value['public_notes'][0]['quote'] = source['public_dialogue'][i]['content'].replace('그렇다고 실제로 행동을 바꾸었다는 뜻은 아니에요. ', '')
                else:
                    value['utterance'] = '그 장면을 떠올리면 다음에는 준비를 잘해야겠다고 느껴요. 그렇다고 실제로 행동을 바꾸었다는 뜻은 아니에요. 막상 준비할 때가 되면 여전히 예전과 같은 방식으로 하게 돼요.'
            self.respond(value); return
        if 'eligible' not in props and 'target_quote' not in props:
            self.rfile = io.BytesIO(raw)
            return super().do_POST()
        self.requests.append(data)
        if self.proposal_gate:
            self.proposal_gate.wait(5)
        source = json.loads(data['messages'][-1]['content'])
        target = source['candidates'][0]
        value = {'eligible': 'yes' if self.eligible else 'no', 'reason': '공개 수락과 철회 여부 확인',
                 'target_turn': target['turn'] if self.eligible else 0,
                 'offer_quote': source['public_dialogue'][0]['content'] if self.eligible else '',
                 'acceptance_quote': ('존재하지 않는 수락' if self.bad_quote else target['utterance']) if self.eligible else '',
                 'I1': '앞서 기록하기로 하셨는데, 언제 해볼까요?' if self.eligible else '',
                 'I2': '앞서 이야기한 기록하기가 지금은 어떻게 느껴지세요?' if self.eligible else ''}
        if 'target_quote' in props:
            value = {'target_turn': target['turn'],
                     'target_quote': '존재하지 않는 수락' if self.bad_quote else target['utterance'],
                     'response_kind': 'acceptance' if self.eligible else 'hesitation',
                     'reason': '공개 발화를 바탕으로 후속 반응 비교',
                     'relevance': 'potential' if self.eligible else 'weak',
                     'I1': '앞서 하신 말씀과 관련해서 지금은 어떻게 느끼세요?',
                     'I2': '그 이야기를 지금 이어가는 속도는 어떠세요?' if source['intervention_mode'] == 'dual' else ''}
        if 'context_fit' in props: value['context_fit'] = self.context_fit
        if 'target_quote' in props and 'I1' not in props:
            value = {k: v for k, v in value.items() if k in props}
        self.respond(value)

    def respond(self, value):
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        chunk = {'choices': [{'index': 0, 'delta': {'content': json.dumps(value, ensure_ascii=False)}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130}}
        self.wfile.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())


class BranchTests(unittest.TestCase):
    def setUp(self):
        self.server, self.model, self.temp = fixture(prompt_editing=True, model_handler=BranchModel)
        Model.requests.clear()
        Model.q1_router_response = '{"selections":[]}'
        p = plan(); p['arguments']['appraisal']['interpretation'] = 'PRIVATE_P6_ORIGINAL'
        c = commit(True); c['arguments']['utterance'] = '네, 짧게 기록해볼게요.'
        Model.q2a_actions = [p, c, action('update_client_notes', operations=[]), progress()]
        Model.q2a_finish = 'tool_calls'
        BranchModel.context_fit = 'natural'; BranchModel.bad_public_note = False; BranchModel.wrong_public_index = False; BranchModel.omitted_public_quote = False
        BranchModel.eligible = True; BranchModel.bad_quote = False; BranchModel.proposal_gate = None

    def tearDown(self):
        if BranchModel.proposal_gate: BranchModel.proposal_gate.set()
        for entry in self.server.p6_workers.values():
            entry['stop'].set(); entry['thread'].join(5)
        Model.q2a_actions = None; Model.q1_router_response = None; Model.q2a_finish = 'stop'
        BranchModel.proposal_gate = None
        close(self.server, self.model, self.temp)

    def get(self, path):
        return json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}' + path))

    def send(self, path, body):
        return json.loads(post(self.server, '/api/q3p6/' + path, body))

    def create(self, **options):
        config = self.get('/api/config')
        scenario = copy.deepcopy(config['scenario'])
        scenario['dialogue_format'] = 'messages'; scenario['dialogue'] = []; scenario.pop('dialogue_turns', None)
        return self.send('create', {'options': {'start_turn': 2, 'end_turn': 3, 'repeats': 1, 'intervention_mode': 'dual', 'include_c': False, **options},
                                  'request': {'scenario': scenario, 'generation': config['generation'],
                                              'messages': [{'role': 'user', 'content': '하루 한 줄 기록해보는 건 어떨까요?'}]}})

    def wait(self, job):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            value = self.get('/api/q3p6/job?id=' + job['id'])
            if not value['running']:
                return value
            time.sleep(.025)
        self.fail('Q3p6 worker timed out')

    def records(self, jid, sid):
        return self.get('/api/q3p6/detail?id=' + jid + '&session_id=' + sid)

    def test_intent_probe_uses_same_public_question_and_earlier_target(self):
        BranchModel.context_fit = 'forced'
        template = '이전에 “{target_quote}”라고 하셨을 때 어떤 생각이었나요?'
        job = self.create(end_turn=2, probe_mode='past_intent', intent_probe_template=template, repeats=2)
        self.assertEqual(job['options']['intervention_mode'], 'single')
        self.assertEqual(job['options']['branch_policy'], 'all')
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        cp = done['checkpoints'][0]; proposal = cp['proposal']['proposal']
        self.assertEqual(proposal['context_fit'], 'diagnostic')
        self.assertLess(proposal['target_turn'], cp['turn'])
        question = template.replace('{target_quote}', proposal['target_quote'])
        self.assertEqual(proposal['I1'], question)
        self.assertEqual(len(cp['branches']), 4)
        self.assertNotIn('PRIVATE_P6_ORIGINAL', json.dumps(cp['proposal']['request']))
        source = json.loads(cp['proposal']['request']['messages'][-1]['content'])
        self.assertTrue(all(r['turn'] < cp['turn'] for r in source['candidates']))
        public_inputs = []
        for branch in cp['branches']:
            detail = self.records(job['id'], branch['session_id'])
            self.assertEqual(detail['records'][-1]['counselor_utterance'], question)
            public_inputs.append(detail['session']['public_dialogue'][:-1])
        self.assertTrue(all(x == public_inputs[0] for x in public_inputs))

    def test_intent_probe_rejects_invalid_configuration_and_quote(self):
        for options in ({'probe_mode': 'unknown'}, {'probe_mode': 'past_intent', 'start_turn': 1},
                        {'intent_probe_template': 'no placeholder'},
                        {'intent_probe_template': '{target_quote} {private_intent}'}):
            with self.subTest(options=options), self.assertRaises(HTTPError): self.create(**options)
        BranchModel.bad_quote = True
        job = self.create(end_turn=2, probe_mode='past_intent')
        self.send('start', {'id': job['id']}); failed = self.wait(job)
        self.assertEqual(failed['status'], 'error')
        self.assertIn('인용', failed['error'])
        self.assertEqual(failed['checkpoints'][0]['branches'], [])

    def test_c_unique_wrong_index_is_audited_and_requests_are_logged(self):
        BranchModel.wrong_public_index = True
        job = self.create(start_turn=1, end_turn=1, include_c=True, trajectory_repeats=1)
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        c = next(r for r in done['trajectories'] if r['condition'] == 'C')
        detail = self.records(job['id'], c['session_id'])
        record = detail['records'][0]
        self.assertEqual(record['raw_note_evidence'][0]['message_index'], 0)
        self.assertEqual(record['note_evidence'][0]['message_index'], 1)
        self.assertEqual(record['note_index_corrections'][0]['resolved_message_index'], 1)
        messages = json.loads(record['model_requests'][-1]['messages'][-1]['content'])['public_dialogue']
        self.assertEqual([m['message_index'] for m in messages], list(range(len(messages))))
        log = json.loads(Path(detail['session']['steps'][-1]['saved_path']).read_text())
        self.assertEqual(len(log['model_calls']), 3)
        self.assertEqual(set(json.loads(log['model_calls'][-1]['response'])), {'public_notes'})
        self.assertEqual(set(json.loads(log['model_calls'][-2]['response'])), {'utterance'})

    def test_c_omitted_quote_restores_context_and_commits_with_audit(self):
        BranchModel.omitted_public_quote = True
        job = self.create(start_turn=1, end_turn=1, include_c=True, trajectory_repeats=1)
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        c = next(r for r in done['trajectories'] if r['condition'] == 'C')
        detail = self.records(job['id'], c['session_id']); record = detail['records'][0]
        omitted = '그렇다고 실제로 행동을 바꾸었다는 뜻은 아니에요.'
        self.assertNotIn(omitted, record['raw_note_evidence'][0]['quote'])
        self.assertIn(omitted, record['notes_after'])
        self.assertEqual(record['note_evidence'][0]['quote'], record['utterance'])
        self.assertEqual(record['note_corrections'][0]['reason'], 'omitted_context_restored')
        self.assertEqual(record['note_index_corrections'], [])
        self.assertEqual(detail['session']['steps'][-1]['utterance'], record['utterance'])
        log = Path(detail['session']['steps'][-1]['saved_path'])
        self.assertIn('omitted_context_restored', log.with_suffix('.events.jsonl').read_text())

    def test_independent_ac_probes_use_own_history_and_export_without_leaks(self):
        job = self.create(end_turn=2, probe_mode='past_intent', include_c=True, trajectory_repeats=2, repeats=2)
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(done['planned_trajectory_probe_responses'], 6)
        self.assertEqual(done['completed_trajectory_probe_responses'], 6)
        self.assertEqual(done['completed_trajectory_responses'], 8)
        self.assertEqual(done['completed_branch_responses'], 4)
        raw = q3p6.read(self.server, job['id'])
        for path in done['trajectories']:
            parent = self.records(job['id'], path['session_id'])
            self.assertEqual(len(parent['records']), 2)
            self.assertFalse(any(s.get('source') == 'fixed_intervention' for s in parent['session']['steps']))
            if path['condition'] == 'A' and path['repeat'] == 1:
                self.assertFalse(path.get('probes')); continue
            self.assertEqual(len(path['probes']), 1)
            cp = path['probes'][0]; self.assertNotIn('snapshot', cp)
            proposal = cp['proposal']['proposal']
            self.assertEqual(proposal['target_turn'], 1)
            self.assertIn(proposal['target_quote'], parent['records'][0]['utterance'])
            self.assertNotIn('PRIVATE_P6_ORIGINAL', json.dumps(cp['proposal']['request']))
            self.assertEqual(proposal['I1'], q3p6.DEFAULT_INTENT_TEMPLATE.replace('{target_quote}', proposal['target_quote']))
            for branch in cp['branches']:
                child = self.records(job['id'], branch['session_id'])
                self.assertEqual(len(child['records']), 3)
                self.assertEqual(child['session']['public_dialogue'][:-2], parent['session']['public_dialogue'])
                self.assertEqual(child['records'][-1]['counselor_utterance'], proposal['I1'])
                self.assertEqual(child['records'][:2], parent['records'])
                if path['condition'] == 'C':
                    self.assertNotIn('PRIVATE_P6_ORIGINAL', json.dumps(child['records']))
                    self.assertNotIn('client_storage_dir', child['session'])
                    self.assertNotEqual(child['session']['public_state_path'], parent['session']['public_state_path'])
            saved = next(r for r in raw['trajectories'] if r['session_id'] == path['session_id'])
            q3p6.assert_checkpoint(saved['probes'][0])
        blind = self.get('/api/q3p6/export?id=' + job['id'] + '&blind=1')
        exported = self.get('/api/q3p6/export?id=' + job['id'])
        self.assertEqual(len(blind['units']), 14)
        self.assertEqual(len({u['unit_id'] for u in blind['units']}), 14)
        self.assertNotIn('PRIVATE_P6_ORIGINAL', json.dumps(blind))
        self.assertTrue(all(set(u) == {'unit_id', 'public_dialogue'} for u in blind['units']))
        self.assertEqual(sum(k['comparison'] == 'independent_trajectory_probe' for k in exported['blind_key']), 6)

    def test_c_probe_failure_resume_keeps_completed_ab_and_parent(self):
        from unittest.mock import patch
        original = q3p6.step
        def failing(*args, **kwargs):
            session = args[3]
            if session.get('q3p6_probe') and session['q3p6_path']['condition'] == 'C':
                raise ValueError('SIMULATED_C_PROBE_FAILURE')
            return original(*args, **kwargs)
        job = self.create(end_turn=2, probe_mode='past_intent', include_c=True, trajectory_repeats=1)
        with patch.object(q3p6, 'step', side_effect=failing):
            self.send('start', {'id': job['id']}); failed = self.wait(job)
        self.assertEqual(failed['status'], 'error')
        c = next(r for r in failed['trajectories'] if r['condition'] == 'C')
        parent_before = self.records(job['id'], c['session_id'])
        branches_before = failed['checkpoints'][0]['branches']
        self.assertTrue(all(b['status'] == 'completed' for b in branches_before))
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(done['checkpoints'][0]['branches'], branches_before)
        self.assertEqual(self.records(job['id'], c['session_id']), parent_before)
        self.assertEqual(done['completed_trajectory_probe_responses'], 1)

    def test_c_probes_at_each_checkpoint_and_multiturn_branch_isolation(self):
        job = self.create(start_turn=2, end_turn=3, branch_turns=2, probe_mode='past_intent', include_c=True, trajectory_repeats=1)
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        c = next(r for r in done['trajectories'] if r['condition'] == 'C')
        self.assertEqual([cp['turn'] for cp in c['probes']], [2, 3])
        self.assertEqual(done['completed_trajectory_probe_responses'], 4)
        parent = self.records(job['id'], c['session_id'])
        self.assertEqual(len(parent['records']), 3)
        self.assertFalse(any(s.get('source') == 'fixed_intervention' for s in parent['session']['steps']))
        for cp in c['probes']:
            branch = cp['branches'][0]
            self.assertEqual(len(branch['responses']), 2)
            child = self.records(job['id'], branch['session_id'])
            self.assertEqual(len(child['records']), cp['turn'] + 2)
            self.assertNotIn('PRIVATE_P6_ORIGINAL', json.dumps(child['records']))

    def test_sequential_forks_same_input_private_boundary_and_common_resume(self):
        job = self.create(); self.assertEqual(len(Model.requests), 0)
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual([c['turn'] for c in done['checkpoints']], [2, 3])
        self.assertEqual(done['common_turn'], 3)
        common = self.records(job['id'], job['common_session_id'])
        self.assertEqual(len(common['records']), 3)
        self.assertTrue(all(r['claim_experiment']['memory_condition'] == 'summary_only' for r in common['records']))
        self.assertNotIn('앞서 기록하기로 하셨는데', json.dumps(common['session']['public_dialogue'], ensure_ascii=False))
        for cp in done['checkpoints']:
            self.assertEqual(len(cp['branches']), 4)
            self.assertEqual(cp['status'], 'completed')
            for intervention in ('I1', 'I2'):
                rows = [r for r in cp['branches'] if r['intervention'] == intervention]
                da, db = [self.records(job['id'], r['session_id']) for r in rows]
                a, b = da['records'][-1], db['records'][-1]
                self.assertEqual(a['notes_before'], b['notes_before'])
                self.assertEqual(a['trajectory_before'], b['trajectory_before'])
                self.assertEqual(a['selected_traits'], b['selected_traits'])
                self.assertEqual(a['counselor_utterance'], cp['proposal']['proposal'][intervention])
                self.assertEqual(a['counselor_utterance'], b['counselor_utterance'])
                self.assertEqual(a['claim_experiment']['memory_view']['visible']['episodes'], [])
                self.assertEqual(len(b['claim_experiment']['memory_view']['visible']['episodes']), cp['turn'])
                pa, pb = a['model_requests'][0], b['model_requests'][0]
                ua, ub = [next(m['content'] for m in p['messages'] if m['role'] == 'user') for p in (pa, pb)]
                marker = '\n\n# 발화 당시 확정 내면 기록\n\n'
                self.assertEqual(ua.split(marker)[0], ub.split(marker)[0])
                self.assertNotIn('PRIVATE_P6_ORIGINAL', ua)
                self.assertIn('PRIVATE_P6_ORIGINAL', ub)
                self.assertEqual(pa.get('seed'), pb.get('seed'))
                self.assertNotEqual(da['session']['client_storage_dir'], db['session']['client_storage_dir'])
        proposals = [r for r in Model.requests if 'target_quote' in r.get('structured_outputs', {}).get('json', {}).get('properties', {})]
        self.assertEqual(len(proposals), 2)
        for request in proposals:
            self.assertNotIn('PRIVATE_P6_ORIGINAL', json.dumps(request))
        # One actual router call for each common turn and each checkpoint/intervention.
        routers = [r for r in Model.requests if not r.get('tools') and 'selections' in r.get('structured_outputs', {}).get('json', {}).get('properties', {})]
        self.assertEqual(len(routers), 3 + 4)
        with self.assertRaises(HTTPError):
            post(self.server, '/api/paired/manual', {'session_id': job['common_session_id'], 'expected_step': 5, 'actor': 'counselor', 'content': '변경'})

    def test_hesitation_weak_relevance_still_branches_single_mode(self):
        BranchModel.eligible = False
        c = commit(True); c['arguments']['utterance'] = '아직 말할 준비가 안 됐어요.'
        Model.q2a_actions[1] = c
        job = self.create(intervention_mode='single'); self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual([c['status'] for c in done['checkpoints']], ['completed', 'completed'])
        self.assertEqual(done['planned_branch_responses'], 4)
        self.assertEqual(done['completed_branch_responses'], 4)
        for cp in done['checkpoints']:
            self.assertEqual(len(cp['branches']), 2)
            self.assertEqual(cp['proposal']['proposal']['response_kind'], 'hesitation')
            self.assertEqual(cp['proposal']['proposal']['relevance'], 'weak')
            self.assertEqual(cp['target_evidence']['utterance'], '아직 말할 준비가 안 됐어요.')
            self.assertEqual(cp['review']['status'], 'unreviewed')
        self.assertEqual(done['reviewed_candidates'], 0)
        reviewed = self.send('review', {'id': job['id'], 'turn': 2, 'status': 'candidate', 'note': '원문 확인'})
        self.assertEqual(reviewed['reviewed_candidates'], 1)
        self.assertEqual(reviewed['completed_branch_responses'], 4)

    def test_counselor_stream_ignores_reasoning_and_marks_failed_preview(self):
        job = self.create()
        q3p6.observe_live(self.server, job['id'], 'counselor', 'chunk', {
            'data': {'choices': [{'index': 0, 'delta': {'content': '지금은 ', 'reasoning_content': 'PRIVATE_THINKING'}}]}})
        q3p6.observe_live(self.server, job['id'], 'counselor', 'chunk', {
            'data': {'choices': [{'index': 0, 'delta': {'content': '어떠세요?'}}]}})
        preview = self.server.p6_live[job['id']]
        self.assertEqual(preview['speech'], '지금은 어떠세요?')
        self.assertNotIn('PRIVATE_THINKING', json.dumps(preview))
        q3p6.observe_live(self.server, job['id'], 'client', 'chunk', {
            'data': {'choices': [{'delta': {'content': 'PRIVATE_CLIENT_TOOL_JSON'}}]}})
        self.assertNotIn('PRIVATE_CLIENT_TOOL_JSON', json.dumps(self.server.p6_live[job['id']]))
        q3p6.observe_live(self.server, job['id'], 'counselor', 'error', {'error': 'FAILED'})
        self.assertEqual(self.server.p6_live[job['id']]['speech_status'], 'discarded')

    def test_independent_ac_paths_no_private_leak_and_blind_export(self):
        job = self.create(start_turn=2, end_turn=2, include_c=True, trajectory_repeats=2, intervention_mode='single')
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(done['completed_trajectory_responses'], 8)
        self.assertEqual(len(done['trajectories']), 4)
        common = self.records(job['id'], job['common_session_id'])['session']
        self.assertEqual(done['trajectories'][0]['session_id'], job['common_session_id'])
        for row in done['trajectories']:
            data = self.records(job['id'], row['session_id'])
            self.assertEqual(len(data['records']), 2)
            self.assertEqual(data['session']['settings']['scenario'], common['settings']['scenario'])
            self.assertEqual(data['session']['public_dialogue'][0], common['public_dialogue'][0])
            if row['condition'] != 'C': continue
            self.assertNotIn('client_storage_dir', data['session'])
            self.assertEqual(data['records'][0]['notes_before'], '(아직 공개된 정보 없음)')
            self.assertEqual(data['records'][1]['notes_before'], data['records'][0]['notes_after'])
            for record in data['records']:
                self.assertEqual(record['condition'], 'public_only')
                self.assertTrue(set(record).isdisjoint({'appraisal', 'turn_goal', 'response_plan', 'trajectory_plan'}))
                self.assertNotIn('PRIVATE_P6_ORIGINAL', json.dumps(record, ensure_ascii=False))
                self.assertEqual(len(record['model_requests']), 3)
                notes_input = json.loads(record['model_requests'][-1]['messages'][-1]['content'])
                self.assertEqual(set(notes_input), {'public_dialogue'})
        blind = self.get('/api/q3p6/export?id=' + job['id'] + '&blind=1')
        raw = self.get('/api/q3p6/export?id=' + job['id'])
        self.assertEqual(len(blind['units']), 6)
        self.assertEqual(len(raw['blind_key']), 6)
        self.assertNotIn('PRIVATE_P6_ORIGINAL', json.dumps(blind))
        for unit in blind['units']: self.assertEqual(set(unit), {'unit_id', 'public_dialogue'})
        self.assertEqual({u['unit_id'] for u in blind['units']}, {u['unit_id'] for u in raw['blind_key']})

    def test_c_note_failure_and_resume_preserves_completed_ab(self):
        BranchModel.bad_public_note = True
        job = self.create(start_turn=1, end_turn=1, include_c=True, trajectory_repeats=1, intervention_mode='single')
        self.send('start', {'id': job['id']}); failed = self.wait(job)
        self.assertEqual(failed['status'], 'error')
        self.assertIn('C 노트 인용', failed['error'])
        c = next(r for r in failed['trajectories'] if r['condition'] == 'C')
        data = self.records(job['id'], c['session_id'])
        self.assertEqual(data['records'], [])
        self.assertEqual(data['session']['steps'], [])
        branches = failed['checkpoints'][0]['branches']
        self.assertTrue(all(b['status'] == 'completed' for b in branches))
        before = [self.records(job['id'], b['session_id']) for b in branches]
        BranchModel.bad_public_note = False
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual([self.records(job['id'], b['session_id']) for b in branches], before)
        self.assertEqual(done['completed_trajectory_responses'], 2)

    def test_natural_policy_skips_forced_reconnection_not_hesitation(self):
        BranchModel.context_fit = 'forced'
        job = self.create(); self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertTrue(all(c['status'] == 'skipped' and c['skip_reason'] for c in done['checkpoints']))
        self.assertEqual(done['completed_branch_responses'], 0)
        other = self.create(end_turn=2, branch_policy='all')
        self.send('start', {'id': other['id']}); done = self.wait(other)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(done['completed_branch_responses'], 4)

    def test_default_single_and_fresh_case_removes_unrelated_history(self):
        config = self.get('/api/config')
        scenario = copy.deepcopy(config['scenario'])
        scenario['profile']['profile_id'] = 'selected_person'
        scenario['dialogue'] = [{'role': 'client', 'content': 'UNRELATED_OLD_HISTORY'}]
        scenario['dialogue_format'] = 'messages'
        scenario['dialogue_turns'] = [{'unrelated': True}]
        scenario['source_notes'] = {'unrelated': True}
        scenario['example_counselor_utterance'] = 'OLD_OPENING'
        job = self.send('create', {'request': {'scenario': scenario}})
        self.assertEqual(job['revision'], 5)
        self.assertEqual(job['options']['repeats'], 5)
        self.assertTrue(job['options']['include_c'])
        self.assertEqual(job['options']['intervention_mode'], 'single')
        session = self.records(job['id'], job['common_session_id'])['session']
        self.assertEqual(session['public_dialogue'], [{'role': 'counselor', 'content': q3p6.OPENING}])
        actual = session['settings']['scenario']
        self.assertNotIn('UNRELATED_OLD_HISTORY', json.dumps(actual))
        self.assertNotIn('dialogue_turns', actual)
        self.assertEqual(actual['profile']['profile_id'], 'selected_person')
        self.assertEqual(len(Model.requests), 0)

    def test_continuation_requires_matching_next_question(self):
        scenario = copy.deepcopy(self.get('/api/config')['scenario'])
        scenario.pop('dialogue_turns', None)
        scenario.update(dialogue_format='messages', dialogue=[{'role': 'client', 'content': '직장 이야기를 더 하고 싶어요.'}])
        body = {'case_mode': 'continuation', 'request': {'scenario': scenario, 'messages': [{'role': 'user', 'content': q3p6.OPENING}]}}
        with self.assertRaises(HTTPError): self.send('create', body)
        body['request']['messages'][0]['content'] = '그 직장에서 어떤 일이 가장 마음에 남나요?'
        job = self.send('create', body)
        self.assertEqual(job['common_dialogue'], scenario['dialogue'] + [{'role': 'counselor', 'content': body['request']['messages'][0]['content']}])

    def test_live_pending_speech_and_private_boundary(self):
        from unittest.mock import patch
        entered, release = threading.Event(), threading.Event()
        original = q3p6.observe_live
        def held(server, jid, actor, kind, data):
            original(server, jid, actor, kind, data)
            if kind == 'version_event' and data.get('section') == 'utterance' and data.get('action') == 'fixed':
                entered.set(); release.wait(10)
        job = self.create(start_turn=1, end_turn=1, intervention_mode='single')
        with patch.object(q3p6, 'observe_live', side_effect=held):
            self.send('start', {'id': job['id']})
            try:
                self.assertTrue(entered.wait(10))
                value = self.get('/api/q3p6/job?id=' + job['id'])
                self.assertTrue(value['running'])
                self.assertEqual(value['live']['stage'], 'common')
                self.assertEqual(value['live']['speech_status'], 'pending')
                self.assertEqual(value['live']['speech'], '네, 짧게 기록해볼게요.')
                self.assertNotIn('PRIVATE_P6_ORIGINAL', json.dumps(value['live']))
                self.send('pause', {'id': job['id']})
            finally: release.set()
            paused = self.wait(job)
        self.assertEqual(paused['status'], 'paused')
        self.assertEqual(paused['live'], {})
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))

    def test_failed_proposal_resume_does_not_regenerate_common_turns(self):
        BranchModel.bad_quote = True
        job = self.create(end_turn=2)
        self.send('start', {'id': job['id']}); failed = self.wait(job)
        self.assertEqual(failed['status'], 'error')
        before = self.records(job['id'], job['common_session_id'])['session']
        BranchModel.bad_quote = False
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        after = self.records(job['id'], job['common_session_id'])['session']
        self.assertEqual(before['steps'], after['steps'])
        self.assertEqual(len(done['checkpoints'][0]['branches']), 4)

    def test_pause_resume_reuses_saved_interventions(self):
        BranchModel.proposal_gate = threading.Event()
        job = self.create(end_turn=2)
        self.send('start', {'id': job['id']})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if any('target_quote' in r.get('structured_outputs', {}).get('json', {}).get('properties', {}) for r in Model.requests): break
            time.sleep(.02)
        live = self.get('/api/q3p6/job?id=' + job['id'])
        self.assertEqual(live['live']['stage'], 'proposal')
        self.assertEqual(live['live']['speech'], '')
        self.send('pause', {'id': job['id']}); BranchModel.proposal_gate.set()
        paused = self.wait(job); self.assertEqual(paused['status'], 'paused', paused.get('error'))
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        count = sum('target_quote' in r.get('structured_outputs', {}).get('json', {}).get('properties', {}) for r in Model.requests)
        self.assertEqual(count, 1)

    def test_pause_immediately_after_branch_keeps_result_visible(self):
        from unittest.mock import patch
        original = q3p6.step
        def pause_after(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[3].get('q3p6_parent', {}).get('condition') == 'summary_only':
                self.server.p6_workers[args[2]['id']]['pause'].set()
            return result
        job = self.create(start_turn=1, end_turn=1, intervention_mode='single')
        with patch.object(q3p6, 'step', side_effect=pause_after):
            self.send('start', {'id': job['id']}); paused = self.wait(job)
        self.assertEqual(paused['status'], 'paused', paused.get('error'))
        self.assertEqual(paused['common_turn'], 1)
        self.assertEqual(paused['completed_branch_responses'], 1)
        self.assertEqual(paused['checkpoints'][0]['branches'][0]['status'], 'completed')
        self.assertEqual(paused['checkpoints'][0]['branches'][0]['responses'], ['네, 짧게 기록해볼게요.'])
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(done['completed_branch_responses'], 2)

    def test_failed_branch_resume_preserves_completed_branch(self):
        from unittest.mock import patch
        original = q3p6.step
        def failing(*args, **kwargs):
            session = args[3]
            if session.get('q3p6_parent', {}).get('condition') == 'intent_linked':
                raise ValueError('SIMULATED_BRANCH_FAILURE')
            return original(*args, **kwargs)
        job = self.create(end_turn=2)
        with patch.object(q3p6, 'step', side_effect=failing):
            self.send('start', {'id': job['id']}); failed = self.wait(job)
        self.assertEqual(failed['status'], 'error')
        stale = q3p6.read(self.server, job['id']); stale['status'] = 'running'
        self.assertEqual(q3p6.public_job(self.server, stale)['status'], 'error')
        first = failed['checkpoints'][0]['branches'][0]
        self.assertEqual(first['status'], 'completed')
        before = self.records(job['id'], first['session_id'])['session']
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        after = self.records(job['id'], first['session_id'])['session']
        self.assertEqual(before, after)
        self.assertEqual(len(done['checkpoints'][0]['branches']), 4)

    def test_input_overflow_stops_without_truncating_or_committing(self):
        job = self.create(end_turn=2, input_char_limit=1000)
        self.send('start', {'id': job['id']}); failed = self.wait(job)
        self.assertEqual(failed['status'], 'error')
        self.assertIn('입력 한도 초과', failed['error'])
        data = self.records(job['id'], job['common_session_id'])
        self.assertEqual(data['records'], [])
        self.assertEqual(data['session']['steps'], [])

    def test_stop_and_resume_preserve_common_state(self):
        BranchModel.proposal_gate = threading.Event()
        job = self.create(end_turn=2)
        self.send('start', {'id': job['id']})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if any('target_quote' in r.get('structured_outputs', {}).get('json', {}).get('properties', {}) for r in Model.requests): break
            time.sleep(.02)
        self.send('stop', {'id': job['id']}); BranchModel.proposal_gate.set()
        stopped = self.wait(job)
        self.assertEqual(stopped['status'], 'stopped', stopped.get('error'))
        before = self.records(job['id'], job['common_session_id'])['session']['steps']
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(self.records(job['id'], job['common_session_id'])['session']['steps'], before)

    def test_multiple_branch_turns_and_repetitions(self):
        job = self.create(start_turn=1, end_turn=1, branch_turns=2, repeats=2)
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(done['common_turn'], 1)
        branches = done['checkpoints'][0]['branches']
        self.assertEqual(len(branches), 8)
        for branch in branches:
            self.assertEqual(len(branch['responses']), 2)
            self.assertEqual(len(self.records(job['id'], branch['session_id'])['records']), 3)
        cp = done['checkpoints'][0]
        for intervention in ('I1', 'I2'):
            for repeat in (1, 2):
                rows = [r for r in branches if r['intervention'] == intervention and r['repeat'] == repeat]
                records = [self.records(job['id'], r['session_id'])['records'][1] for r in rows]
                self.assertEqual(records[0]['model_requests'][0]['seed'], records[1]['model_requests'][0]['seed'])

    def test_resume_after_server_restart(self):
        from unittest.mock import patch
        import app.app_server as app
        original = q3p6.step
        def failing(*args, **kwargs):
            if args[3].get('q3p6_parent', {}).get('condition') == 'intent_linked':
                raise ValueError('FAIL_BEFORE_RESTART')
            return original(*args, **kwargs)
        job = self.create(end_turn=2)
        with patch.object(q3p6, 'step', side_effect=failing):
            self.send('start', {'id': job['id']}); failed = self.wait(job)
        first = failed['checkpoints'][0]['branches'][0]
        before = self.records(job['id'], first['session_id'])['session']
        old = self.server
        old.shutdown(); old.server_close()
        self.server = app.make_server(old.config_path, port=0, storage_dir=old.storage_dir)
        self.server.prompt_source_stack = old.prompt_source_stack
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.send('start', {'id': job['id']}); done = self.wait(job)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(self.records(job['id'], first['session_id'])['session'], before)

    def test_full_history_no_twelve_turn_limit(self):
        rows = [{'turn': i, 'turn_id': str(i), 'appraisal': {'interpretation': 'x'*2000}} for i in range(1, 21)]
        view = memory_view(rows, 'intent_linked')
        self.assertEqual(len(view['visible']['episodes']), 20)
        self.assertEqual(view['omitted_turns'], [])
        self.assertEqual(memory_view(rows, 'summary_only')['visible']['episodes'], [])

    def test_invalid_range_and_foreign_details_rejected(self):
        with self.assertRaises(HTTPError): self.create(start_turn=5, end_turn=2)
        job = self.create()
        with self.assertRaises(HTTPError): self.records(job['id'], '../other')
        with self.assertRaises(HTTPError): self.get('/api/q3p6/job?id=../../bad')


class CitationTests(unittest.TestCase):
    PREFIX = '지난 상담 이후로 그 장면이 떠오를 때마다 부끄러움을 느꼈어요.'
    GAP = '그렇다고 기록하기를 실제로 시도하거나 동의한 것은 아니에요.'
    SUFFIX = '다음에 준비할 때에도 같은 생각이 반복될까 봐 걱정이 돼요.'

    def test_omission_restores_original_context_including_negation(self):
        source = '솔직히 말씀드리면… ' + ' '.join((self.PREFIX, self.GAP, self.SUFFIX))
        raw = self.PREFIX + ' ' + self.SUFFIX
        rows, audit = resolve_note_evidence([{'content': source}], [{'message_index': 8, 'quote': raw}])
        self.assertEqual(rows, [{'message_index': 0, 'quote': ' '.join((self.PREFIX, self.GAP, self.SUFFIX))}])
        self.assertEqual(audit[0]['quote'], raw)
        self.assertEqual(audit[0]['resolved_quote'], source[audit[0]['source_start']:audit[0]['source_end']])
        self.assertEqual(audit[0]['original_message_index'], 8)

    def test_omission_does_not_admit_new_words_reordering_or_ambiguity(self):
        source = ' '.join((self.PREFIX, self.GAP, self.SUFFIX))
        public = [{'content': source}]
        for quote in (self.PREFIX + ' 성공적으로 해결됐어요. ' + self.SUFFIX,
                      self.SUFFIX + ' ' + self.PREFIX,
                      self.PREFIX.replace('부끄러움을', '뿌듯함을') + ' ' + self.SUFFIX):
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                resolve_note_evidence(public, [{'message_index': 0, 'quote': quote}])
        raw = self.PREFIX + ' ' + self.SUFFIX
        for ambiguous in (public * 2, [{'content': self.PREFIX + ' ' + source}]):
            with self.subTest(public=ambiguous), self.assertRaises(ValueError):
                resolve_note_evidence(ambiguous, [{'message_index': 0, 'quote': raw}])
        with self.assertRaises(ValueError):
            resolve_note_evidence([{'content': self.PREFIX}, {'content': self.SUFFIX}],
                                  [{'message_index': 0, 'quote': raw}])

    def test_exact_index_retained_and_only_unique_wrong_index_corrected(self):
        public = [{'content': '같은 말'}, {'content': '유일한 발화'}, {'content': '같은 말'}]
        rows, audit = resolve_note_evidence(public, [{'message_index': 0, 'quote': '같은 말'}])
        self.assertEqual(rows[0]['message_index'], 0); self.assertEqual(audit, [])
        rows, audit = resolve_note_evidence(public, [{'message_index': 20, 'quote': '유일한 발화'}])
        self.assertEqual(rows[0]['message_index'], 1)
        self.assertEqual(audit[0]['original_message_index'], 20)
        for row in ({'message_index': 1, 'quote': '같은 말'},
                    {'message_index': 0, 'quote': '없는 말'}, {'message_index': 0, 'quote': ' '}):
            with self.subTest(row=row), self.assertRaises(ValueError): resolve_note_evidence(public, [row])


if __name__ == '__main__':
    import sys
    if '--serve' in sys.argv:
        instance = BranchTests(); instance.setUp()
        print(f'http://127.0.0.1:{instance.server.server_port}', flush=True)
        try: threading.Event().wait()
        finally: instance.tearDown()
    else:
        unittest.main()

