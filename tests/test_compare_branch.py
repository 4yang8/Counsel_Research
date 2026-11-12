"""HTTP tests for the shared-prefix fork (Q3compare_memory) and the counselor-side records of P3q and P3rs."""
import copy
import json
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError
from urllib.request import urlopen

from test_lab import fixture, close, post
from test_memory_access import sample
from client.memory_access import COMPARE_VERSION, COMPARE_VERSIONS, LEGACY_CM_VERSIONS
from client.memory_access.runtime import load
from counselor.P3q.runtime import record
from counselor.P3rs import runtime as p3rs
from counselor.P3rs_sub import runtime as p3rs_sub
from runtime import profile_batch, compare_branch

UTTERANCE = '지금은 더 생각해 보고 싶어요.'


class CompareModel(BaseHTTPRequestHandler):
    requests = []
    lock = threading.Lock()
    delay = .01

    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        cls = type(self)
        with cls.lock:
            cls.requests.append(copy.deepcopy(data))
        time.sleep(cls.delay)
        user = next(m['content'] for m in data['messages'] if m['role'] == 'user')
        schema = (data.get('structured_outputs') or {}).get('json', {})
        finish = 'stop'
        if 'revisit' in schema.get('properties', {}):  # P3q counselor: numbered public dialogue, not JSON
            clients = user.count('] 내담자: ')
            mark = ({'is_revisit': True, 'client_turn': 1, 'quote': '더 생각'} if clients >= 2
                    else {'is_revisit': False, 'client_turn': 0, 'quote': ''})
            delta = {'content': json.dumps({'utterance': f'상담사 발화 {clients + 1}', 'revisit': mark}, ensure_ascii=False)}
        elif 'marker' in schema.get('properties', {}) or 'check_turn' in schema.get('properties', {}):
            # P3rs: marks client turn 2, explores it one turn later, suggests at 4 replies and checks that at 5.
            # P3rs_sub splits this into an observer call (marker + recommendation) and a main call (move + utterance).
            clients = user.count('] 내담자: ')
            marker = {'found': True, 'kind': 'comply', 'quote': '더 생각'} if clients == 2 else {'found': False, 'kind': 'none', 'quote': ''}
            move = {3: 'rupture_explore', 4: 'suggest', 5: 'check_in'}.get(clients, 'explore')
            main = {'move': move, 'rupture_turn': 2 if clients == 3 else 0, 'check_turn': 5 if clients == 5 else 0,
                    'utterance': f'상담사 발화 {clients + 1}'}
            properties = schema['properties']
            if 'recommend' in properties:
                value = {'marker': marker, 'recommend': move if move in ('rupture_explore', 'check_in') else 'continue',
                         'target_turn': main['rupture_turn'] or main['check_turn'], 'advice': 'ADVICE_' + str(clients)}
            else:
                value = {**main, 'marker': marker} if 'marker' in properties else main
            delta = {'content': json.dumps(value, ensure_ascii=False), 'reasoning_content': 'COUNSELOR_REASON'}
        elif data.get('structured_outputs'):
            source = json.loads(user)
            if 'private_turns' in source:
                payload = {'turns': [r['turn'] for r in source['private_turns'][:3]]}
            else:
                payload = {'selections': [{'trait_id': source['traits'][0]['trait_id'], 'background_ids': [],
                                           'relevance': '현재 공개 질문과 관련된 특성'}]}
            delta = {'content': json.dumps(payload)}
        elif data.get('tools'):
            source = json.loads(user)
            tool = data['tools'][0]['function']['name']
            if tool == 'stage_client_update':
                turn = sum(m['role'] == 'client' for m in source['public_dialogue']) + 1
                args = sample(data['tools'][0]['function']['parameters'])
                secret = f"SECRET_{source['basic_profile']['profile_id']}_T{turn}"
                args['appraisal']['interpretation'] = secret
                args['private_position']['withheld_content'] = secret + '_WITHHELD'
            else:
                args = {'utterance': UTTERANCE}
            delta = {'tool_calls': [{'index': 0, 'id': 'call_' + uuid.uuid4().hex, 'type': 'function',
                                     'function': {'name': tool, 'arguments': json.dumps(args)}}]}
            finish = 'tool_calls'
        else:
            delta = {'content': '조금 더 들려주실 수 있을까요?'}
        frame = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
                 'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}}
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


def is_counselor(request):
    properties = (request.get('structured_outputs') or {}).get('json', {}).get('properties', {})
    return 'revisit' in properties or 'marker' in properties or 'check_turn' in properties


class CompareBranchTests(unittest.TestCase):
    def setUp(self):
        CompareModel.requests = []
        CompareModel.delay = .01
        self.server, self.model, self.temp = fixture(model_handler=CompareModel)

    def tearDown(self):
        for worker in self.server.batch_workers.values():
            worker['flag'].set()
            worker['thread'].join(10)
        close(self.server, self.model, self.temp)

    def get(self, path):
        return json.loads(urlopen('http://127.0.0.1:' + str(self.server.server_port) + path).read())

    def post(self, path, value):
        return json.loads(post(self.server, '/api/profile_batch/' + path, value))

    def job(self, **values):
        body = dict(profile_directory=profile_batch.DEFAULT_DIRECTORY, filenames=['gold_ccd_profile_001.json'],
                    mode='compare', counselor_version='P3q', turns=4, fork_turn=3, concurrency=1, retries=0)
        body.update(values)
        return self.post('create', body)

    def wait(self, jid, predicate=lambda j: j['status'] not in ('running', 'stopping')):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = self.get('/api/profile_batch/job?id=' + jid)
            if predicate(result):
                return result
            time.sleep(.05)
        self.fail('작업 완료 대기 초과: ' + str(result))

    def run_job(self, **values):
        job = self.job(**values)
        self.post('start', {'id': job['id']})
        result = self.wait(job['id'])
        self.assertEqual(result['status'], 'completed', result)
        return result

    def session(self, row):
        return self.get('/api/paired/session?session_id=' + row['session_id'])

    def records(self, session):
        return load({'_storage_dir': session['client_storage_dir']}, COMPARE_VERSION, session['session_id'])['records']

    def mains(self, rec):
        return [q['payload'] for q in rec['model_requests'] if q['stage'] == 'main']

    def test_fork_shares_prefix_and_only_memory_access_differs(self):
        result = self.run_job()
        self.assertEqual(result['mode'], 'compare')
        self.assertEqual(result['options']['fork_turn'], 3)
        rows = {r['branch']: r for r in result['rows']}
        self.assertEqual(set(rows), {'common', 'provided', 'withheld'})
        self.assertEqual({b: (r['version'], r['target_turns'], r['completed_turns']) for b, r in rows.items()},
                         {b: (COMPARE_VERSION, 2, 2) for b in rows})
        self.assertEqual((result['completed_turns'], result['total_turns'], result['turn_percent'], result['succeeded']), (6, 6, 100, 3))
        self.assertEqual(len({r['session_id'] for r in result['rows']}), 3)

        common = self.session(rows['common'])
        # opening + (client 1, counselor 2, client 2, counselor 3): ends on the fork turn's counselor utterance
        self.assertEqual([m['role'] for m in common['public_dialogue']], ['counselor', 'client', 'counselor', 'client', 'counselor'])
        self.assertEqual(common['compare'], {'branch': 'common', 'fork_turn': 3})
        self.assertEqual(common['settings']['compare_memory_mode'], 'provided')
        base = self.records(common)
        self.assertEqual([r['turn'] for r in base], [1, 2])
        self.assertEqual([r['memory_mode'] for r in base], ['provided', 'provided'])

        for branch in compare_branch.BRANCHES:
            session = self.session(rows[branch])
            self.assertEqual(len(session['public_dialogue']), 8)
            self.assertEqual(session['public_dialogue'][:5], common['public_dialogue'])
            self.assertTrue(all(set(m) == {'role', 'content'} for m in session['public_dialogue']))
            self.assertEqual(session['compare'], {'branch': branch, 'parent_session': common['session_id'],
                                                  'fork_turn': 3, 'common_steps': 4})
            self.assertEqual(session['settings']['compare_memory_mode'], branch)
            self.assertEqual([bool(s.get('common')) for s in session['steps']], [True] * 4 + [False] * 3)
            recs = self.records(session)
            self.assertEqual([r['turn'] for r in recs], [1, 2, 3, 4])
            self.assertEqual(recs[:2], base)  # the shared records are the same objects of analysis in both branches
            self.assertEqual([r['memory_mode'] for r in recs[2:]], [branch, branch])
            for rec in recs[2:]:
                selection = [q for q in rec['model_requests'] if q['stage'] == 'selection']
                router = rec['model_requests'][0]['payload']
                self.assertNotIn('SECRET_', json.dumps(router))
                text = json.dumps(self.mains(rec))
                data = json.loads(next(m['content'] for m in self.mains(rec)[0]['messages'] if m['role'] == 'user'))
                if branch == 'provided':
                    self.assertEqual(len(selection), 1)
                    self.assertEqual([r['turn'] for r in rec['memory_input']], list(range(1, min(rec['turn'], 4))))
                    self.assertIn('SECRET_gold_001_T1', text)
                    self.assertEqual(data['private_turns'], rec['memory_input'])
                else:
                    self.assertFalse(selection)
                    self.assertEqual(rec['memory_input'], [])
                    # The current turn's own inner record is used for its utterance; no earlier one is.
                    self.assertNotIn('SECRET_', json.dumps(self.mains(rec)[0]))
                    for past in range(1, rec['turn']):
                        self.assertNotIn(f'SECRET_gold_001_T{past}', text)
                    self.assertIn(f"SECRET_gold_001_T{rec['turn']}", text)
                    self.assertEqual(data['private_turns'], [])
            # Counselor-side marks: one per generated counselor utterance, kept out of the public dialogue.
            counselor_steps = [s for s in session['steps'] if s['actor'] == 'counselor']
            self.assertEqual(len(counselor_steps), 3)
            self.assertTrue(all('revisit' in s for s in counselor_steps))
            self.assertEqual(session['counselor_checkpoint']['version'], 'P3q')
            self.assertEqual(session['counselor_checkpoint']['revisits'], [s['revisit'] for s in counselor_steps])
            first, second = counselor_steps[0]['revisit'], counselor_steps[1]['revisit']
            self.assertEqual((first['counselor_turn'], first['is_revisit'], first['client_turn']), (2, False, None))
            self.assertEqual((second['counselor_turn'], second['is_revisit'], second['client_turn'], second['valid'], second['quote_found']),
                             (3, True, 1, True, True))
            detail = self.get('/api/profile_batch/detail?id=' + result['id'] + '&session_id=' + rows[branch]['session_id'])
            self.assertEqual(len(detail['records']), 4)
            with self.assertRaises(HTTPError):
                post(self.server, '/api/paired/manual', {'session_id': rows[branch]['session_id'],
                     'expected_step': len(session['steps']), 'actor': 'counselor', 'content': '수동 입력'})
        # The counselor only ever receives public speech.
        counselor_requests = [r for r in CompareModel.requests if is_counselor(r)]
        self.assertEqual(len(counselor_requests), 2 + 2)  # turns 2-3 once in common, turn 4 once per branch
        for request in counselor_requests:
            text = json.dumps(request, ensure_ascii=False)
            self.assertNotIn('SECRET_', text)
            self.assertNotIn('private_position', text)
            self.assertIn('[1턴] 상담사: ', text)
        # The rejected manual POST above may still be leaving its request scope on the server thread.
        deadline = time.monotonic() + 5
        while self.server.storage_jobs and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(self.server.storage_jobs, 0)

    def test_fork_at_first_turn_and_validation(self):
        result = self.run_job(turns=2, fork_turn=1)
        rows = {r['branch']: r for r in result['rows']}
        self.assertEqual({b: r['target_turns'] for b, r in rows.items()}, {'common': 0, 'provided': 2, 'withheld': 2})
        common = self.session(rows['common'])
        self.assertEqual(common['steps'], [])
        self.assertEqual(len(common['public_dialogue']), 1)
        for branch in compare_branch.BRANCHES:
            session = self.session(rows[branch])
            self.assertEqual(session['public_dialogue'][0], common['public_dialogue'][0])
            recs = self.records(session)
            self.assertEqual(len(recs), 2)
            self.assertEqual(len(recs[1]['memory_input']), 1 if branch == 'provided' else 0)
        for changes in ({'fork_turn': 0}, {'fork_turn': 5}, {'fork_turn': '3'}, {'mode': 'other'}, {'counselor_version': 'C4'}):
            with self.assertRaises(HTTPError):
                self.job(**changes)

    def test_stop_and_resume_keeps_common_session(self):
        CompareModel.delay = .08
        job = self.job(turns=5, fork_turn=3)
        self.post('start', {'id': job['id']})
        result = self.wait(job['id'], lambda j: j['rows'][0]['status'] == 'completed' and j['rows'][1]['completed_turns'] >= 1)
        self.post('stop', {'id': job['id']})
        result = self.wait(job['id'])
        self.assertEqual(result['status'], 'cancelled')
        rows = {r['branch']: r for r in result['rows']}
        self.assertEqual(rows['common']['status'], 'completed')
        common_sid, provided_sid = rows['common']['session_id'], rows['provided']['session_id']
        common_steps = len(self.session(rows['common'])['steps'])
        CompareModel.delay = .005
        self.post('start', {'id': job['id']})
        result = self.wait(job['id'])
        self.assertEqual(result['status'], 'completed', result)
        rows = {r['branch']: r for r in result['rows']}
        self.assertEqual((rows['common']['session_id'], rows['provided']['session_id']), (common_sid, provided_sid))
        self.assertEqual(len(self.session(rows['common'])['steps']), common_steps)  # never advanced past the fork
        base = self.records(self.session(rows['common']))
        for branch in compare_branch.BRANCHES:
            recs = self.records(self.session(rows[branch]))
            self.assertEqual(len(recs), 5)
            self.assertEqual(recs[:2], base)
        self.assertEqual(result['completed_turns'], result['total_turns'])

    def test_options_two_condition_mode_and_revisit_record(self):
        options = self.get('/api/profile_batch/options')
        self.assertEqual([v['id'] for v in options['versions']], ['Q3inner_memory', 'inner_memory_no'])
        self.assertEqual(options['compare']['version']['id'], COMPARE_VERSION)
        self.assertEqual((options['compare']['fork_turn'], options['compare']['counselor']), (4, 'P3rs'))
        self.assertIn('P3q', [c['id'] for c in options['counselors']])
        self.get('/api/prompts/source?role=counselor&version=P3q&kind=system')
        self.get('/api/prompts/source?role=counselor&version=P3q&kind=user')
        self.get('/api/prompts/source?role=client&version=Q3compare_memory&kind=memory_selector')
        # P3q also works in the existing two-condition mode, which is otherwise unchanged.
        result = self.run_job(mode='versions', versions=['inner_memory_no'], counselor_version='P3q', turns=2)
        self.assertEqual((result['mode'], result['total_turns'], len(result['rows'])), ('versions', 2, 1))
        self.assertNotIn('branch', result['rows'][0])
        session = self.session(result['rows'][0])
        self.assertNotIn('compare', session)
        self.assertEqual([('revisit' in s) for s in session['steps']], [False, True, False])
        public = [{'role': 'counselor', 'content': '시작'}, {'role': 'client', 'content': '괜찮아요, 별일 아니에요.'},
                  {'role': 'counselor', 'content': '그렇군요.'}, {'role': 'client', 'content': '네.'}]
        def mark(is_revisit, turn, quote):
            return record({'utterance': 'x', 'revisit': {'is_revisit': is_revisit, 'client_turn': turn, 'quote': quote}}, public)
        self.assertEqual({k: mark(True, 1, '괜찮아요')[k] for k in ('counselor_turn', 'client_turn', 'valid', 'quote_found')},
                         {'counselor_turn': 3, 'client_turn': 1, 'valid': True, 'quote_found': True})
        self.assertFalse(mark(True, 1, '힘들어요')['quote_found'])
        for turn in (0, 2, 9):  # the latest client turn (2) is not a revisit target
            row = mark(True, turn, '네')
            self.assertEqual((row['valid'], row['client_turn']), (False, None))
        self.assertEqual({k: mark(False, 0, '무시')[k] for k in ('is_revisit', 'client_turn', 'quote', 'valid')},
                         {'is_revisit': False, 'client_turn': None, 'quote': '', 'valid': True})

    def test_p3rs_notes_are_fed_back_and_stay_on_the_counselor_side(self):
        options = self.get('/api/profile_batch/options')
        self.assertIn('P3rs', [c['id'] for c in options['counselors']])
        self.get('/api/prompts/source?role=counselor&version=P3rs&kind=system')
        self.get('/api/prompts/source?role=counselor&version=P3rs&kind=user')
        result = self.run_job(counselor_version='P3rs', turns=5, fork_turn=3)
        rows = {r['branch']: r for r in result['rows']}
        common = self.session(rows['common'])
        # Common part: counselor turn 3 notes a marker in client turn 2 and leaves it pending.
        self.assertEqual(common['counselor_checkpoint']['pending'], [{'client_turn': 2, 'kind': 'comply', 'quote': '더 생각'}])
        for branch in compare_branch.BRANCHES:
            session = self.session(rows[branch])
            steps = [s for s in session['steps'] if s['actor'] == 'counselor']
            self.assertEqual([s['rupture']['counselor_turn'] for s in steps], [2, 3, 4, 5])
            self.assertEqual(session['counselor_checkpoint']['records'], [s['rupture'] for s in steps])
            self.assertEqual(session['counselor_checkpoint']['pending'], [])
            noted, explored, after = (s['rupture'] for s in steps[1:])
            self.assertEqual(noted['marker'], {'found': True, 'client_turn': 2, 'kind': 'comply', 'quote': '더 생각',
                                               'quote_found': True, 'valid': True})
            self.assertEqual(explored['explore'], {'is_explore': True, 'client_turn': 2, 'gap': 1, 'noted': True, 'valid': True})
            self.assertEqual(explored['pending_before'], common['counselor_checkpoint']['pending'])
            self.assertEqual((after['move'], after['explore']['is_explore'], after['pending_before']), ('suggest', False, []))
            self.assertEqual(session['counselor_checkpoint']['unchecked'], [{'counselor_turn': 5, 'move': 'suggest', 'utterance': '상담사 발화 5'}])
            self.assertTrue(all(set(m) == {'role', 'content'} for m in session['public_dialogue']))
        counselor = [r for r in CompareModel.requests if is_counselor(r)]
        self.assertEqual(len(counselor), 2 + 2 * 2)
        for request in counselor:
            text = next(m['content'] for m in request['messages'] if m['role'] == 'user')
            clients = text.count('] 내담자: ')
            # The note appears only between the turn it was written and the turn it was explored.
            self.assertEqual('2턴 내담자 답: "더 생각" (순응하는 동의)' in text, clients == 3)
            self.assertEqual(p3rs.EMPTY in text, clients != 3)
            self.assertNotIn('SECRET_', json.dumps(request, ensure_ascii=False))
        for request in CompareModel.requests:
            if not is_counselor(request):
                text = json.dumps(request, ensure_ascii=False)
                self.assertNotIn('순응하는 동의', text)
                self.assertNotIn('rupture', text)

    def test_p3rs_sub_observer_runs_first_and_its_advice_reaches_only_the_main_counselor(self):
        options = self.get('/api/profile_batch/options')
        self.assertIn('P3rs_sub', [c['id'] for c in options['counselors']])
        for kind in ('system', 'user', 'observer', 'observer_user'):
            self.get('/api/prompts/source?role=counselor&version=P3rs_sub&kind=' + kind)
        result = self.run_job(counselor_version='P3rs_sub', turns=6, fork_turn=6)
        rows = {r['branch']: r for r in result['rows']}
        session = self.session(rows['provided'])
        notes = [s['rupture'] for s in session['steps'] if s['actor'] == 'counselor']
        self.assertEqual([n['counselor_turn'] for n in notes], [2, 3, 4, 5, 6])
        self.assertEqual([n['move'] for n in notes], ['explore', 'explore', 'rupture_explore', 'suggest', 'check_in'])
        self.assertEqual([n['observer']['recommend'] for n in notes], ['continue', 'continue', 'rupture_explore', 'continue', 'check_in'])
        self.assertTrue(all(n['observer']['followed'] for n in notes))
        self.assertEqual(notes[1]['marker']['kind'], 'comply')  # the marker comes from the observer
        self.assertEqual(notes[4]['check'], {'is_check': True, 'counselor_turn': 5, 'gap': 0, 'noted': True, 'valid': True})
        self.assertEqual(notes[4]['unchecked_before'], [{'counselor_turn': 5, 'move': 'suggest', 'utterance': '상담사 발화 5'}])
        self.assertEqual(session['counselor_checkpoint']['unchecked'], [])
        self.assertTrue(all(set(m) == {'role', 'content'} for m in session['public_dialogue']))
        counselor = [r for r in CompareModel.requests if is_counselor(r)]
        observers = [r for r in counselor if 'recommend' in r['structured_outputs']['json']['properties']]
        mains = [r for r in counselor if r not in observers]
        self.assertEqual((len(observers), len(mains)), (5, 5))  # all counselor turns are in the common part
        self.assertEqual([counselor.index(r) % 2 for r in observers], [0] * 5)  # observer first, every turn
        guide = p3rs_sub.GUIDE.read_text(encoding='utf-8')
        for request in observers:
            text = next(m['content'] for m in request['messages'] if m['role'] == 'user')
            self.assertIn(guide.strip(), text)
            self.assertIn('### 아직 어떻게 들렸는지 묻지 않은 내 말', text)
            # The count is 0 until the exploration at the third client reply, then 1.
            self.assertIn('되물은 횟수\n\n' + ('0회' if text.count('] 내담자: ') <= 3 else '1회'), text)
            self.assertNotIn('ADVICE_', text)
        for request in mains:
            text = next(m['content'] for m in request['messages'] if m['role'] == 'user')
            self.assertIn('ADVICE_' + str(text.count('] 내담자: ')), text)
            self.assertNotIn('물러남 균열과 회복 가이드', text)
        self.assertIn('권하는 행동: 개입 확인 (내 5턴 발화가 어떻게 들렸는지)', next(m['content'] for m in mains[-1]['messages'] if m['role'] == 'user'))
        for request in counselor:
            self.assertNotIn('SECRET_', json.dumps(request, ensure_ascii=False))
            self.assertNotIn('private_position', json.dumps(request, ensure_ascii=False))
        for request in CompareModel.requests:
            if not is_counselor(request):
                text = json.dumps(request, ensure_ascii=False)
                self.assertNotIn('ADVICE_', text)
                self.assertNotIn('관찰 담당', text)

    def test_stance_version_adds_fields_and_forks_like_the_base_version(self):
        stance = 'Q3compare_memory_private_position'
        options = self.get('/api/profile_batch/options')
        self.assertEqual([v['id'] for v in options['compare']['versions']], [v for v in COMPARE_VERSIONS if v not in LEGACY_CM_VERSIONS])
        for kind in ('system', 'memory_selector', 'trait_router'):
            self.get(f'/api/prompts/source?role=client&version={stance}&kind={kind}')
        with self.assertRaises(HTTPError):
            self.job(compare_version='Q3inner_memory')
        result = self.run_job(compare_version=stance)
        rows = {r['branch']: r for r in result['rows']}
        self.assertEqual({r['version'] for r in result['rows']}, {stance})
        fields = lambda request: request['tools'][0]['function']['parameters']['properties']['private_position']['properties']
        for branch in compare_branch.BRANCHES:
            session = self.session(rows[branch])
            recs = load({'_storage_dir': session['client_storage_dir']}, stance, session['session_id'])['records']
            self.assertEqual([r['turn'] for r in recs], [1, 2, 3, 4])
            self.assertEqual([r['memory_mode'] for r in recs], ['provided', 'provided', branch, branch])
            self.assertEqual([len(r['memory_input']) > 0 for r in recs[2:]], [branch == 'provided'] * 2)
            for rec in recs:
                self.assertEqual((rec['inner']['private_position']['counselor_stance'], rec['inner']['private_position']['stance_expression']),
                                 ('agree', 'voiced'))
                self.assertIn('counselor_stance', fields(self.mains(rec)[0]))
        # The base version's inner record is unchanged.
        CompareModel.requests = []
        base = self.run_job()
        session = self.session({r['branch']: r for r in base['rows']}['provided'])
        for rec in self.records(session):
            self.assertNotIn('counselor_stance', rec['inner']['private_position'])
            self.assertNotIn('counselor_stance', fields(self.mains(rec)[0]))

    def test_p3rs_record_rules(self):
        public = [{'role': 'counselor', 'content': '시작'}, {'role': 'client', 'content': '요즘 회사 일이 많이 힘들어요.'},
                  {'role': 'counselor', 'content': '직접 말해 보면 어떨까요?'}, {'role': 'client', 'content': '네, 그래 볼게요.'}]
        def run(found, kind, quote, move, turn, pending=()):
            return p3rs.record({'marker': {'found': found, 'kind': kind, 'quote': quote}, 'move': move,
                                'rupture_turn': turn if move != 'check_in' else 0,
                                'check_turn': turn if move == 'check_in' else 0, 'utterance': 'x'}, public, list(pending))[:2]
        row, pending = run(True, 'comply', '그래 볼게요', 'empathy', 0)
        self.assertEqual((row['counselor_turn'], row['marker']['client_turn'], row['marker']['quote_found']), (3, 2, True))
        self.assertEqual(pending, [{'client_turn': 2, 'kind': 'comply', 'quote': '그래 볼게요'}])
        self.assertFalse(run(True, 'short', '없는 말', 'explore', 0)[0]['marker']['quote_found'])
        # A marker without a kind is kept as invalid and not carried forward.
        row, pending = run(True, 'none', '네', 'explore', 0)
        self.assertEqual((row['marker']['valid'], row['marker']['client_turn'], pending), (False, None, []))
        # Immediate exploration (gap 0) of a marker noted in the same call.
        row, pending = run(True, 'comply', '네', 'rupture_explore', 2)
        self.assertEqual((row['explore']['gap'], row['explore']['noted'], pending), (0, True, []))
        # Exploring a turn that was never noted is recorded as such; other notes stay.
        earlier = [{'client_turn': 2, 'kind': 'short', 'quote': '네'}]
        row, pending = run(False, 'none', '', 'rupture_explore', 1, earlier)
        self.assertEqual((row['explore']['client_turn'], row['explore']['gap'], row['explore']['noted'], pending), (1, 1, False, earlier))
        self.assertEqual(row['pending_before'], earlier)
        for turn in (0, 3):
            row, pending = run(False, 'none', '', 'rupture_explore', turn, earlier)
            self.assertEqual((row['explore']['valid'], row['explore']['client_turn'], pending), (False, None, earlier))
        # rupture_turn is ignored unless the move is a rupture exploration.
        row, pending = run(False, 'none', '', 'suggest', 2, earlier)
        self.assertEqual((row['explore']['is_explore'], pending), (False, earlier))
        # A check-in names the counselor's own utterance; it also clears a note on the reply to it.
        row, pending = run(False, 'none', '', 'check_in', 2, earlier)
        self.assertEqual((row['check'], row['explore']['is_explore'], pending),
                         ({'is_check': True, 'counselor_turn': 2, 'gap': 0, 'noted': False, 'valid': True}, False, []))
        row, pending = run(False, 'none', '', 'check_in', 1, earlier)
        self.assertEqual((row['check']['gap'], pending), (1, earlier))
        row, pending = run(False, 'none', '', 'check_in', 3, earlier)
        self.assertEqual((row['check']['valid'], row['check']['counselor_turn'], pending), (False, None, earlier))
        self.assertFalse(run(False, 'none', '', 'explore', 0)[0]['check']['is_check'])
        # Interventions are listed until asked about; a check or an exploration of that turn clears the entry.
        def step(move, turn=0, unchecked=()):
            return p3rs.record({'marker': {'found': False, 'kind': 'none', 'quote': ''}, 'move': move,
                                'rupture_turn': turn if move == 'rupture_explore' else 0,
                                'check_turn': turn if move == 'check_in' else 0, 'utterance': ' 해 보면 어때요 '}, public, [], unchecked)
        mine = [{'counselor_turn': 2, 'move': 'suggest', 'utterance': '직접 말해 보면 어떨까요?'}]
        row, _, unchecked = step('interpret', unchecked=mine)
        self.assertEqual(unchecked, [*mine, {'counselor_turn': 3, 'move': 'interpret', 'utterance': '해 보면 어때요'}])
        self.assertEqual(row['unchecked_before'], mine)
        self.assertEqual(step('empathy', unchecked=mine)[2], mine)
        row, _, unchecked = step('check_in', 2, mine)
        self.assertEqual((row['check']['noted'], unchecked), (True, []))
        self.assertEqual(step('rupture_explore', 2, mine)[2], [])
        self.assertEqual(step('check_in', 1, mine)[2], mine)
        text = p3rs.notes([], mine)
        self.assertIn(p3rs.EMPTY, text)
        self.assertIn('- 내 2턴 발화 (관점 제안·조언): "직접 말해 보면 어떨까요?"', text)
        self.assertIn(p3rs.EMPTY_UNCHECKED, p3rs.notes([]))
        self.assertNotIn('되물은 횟수', p3rs.notes([]))
        self.assertIn('### 지금까지 되물은 횟수\n\n0회 (아직 한 번도 되묻지 않았습니다)', p3rs.notes([], [], 0))
        # Valid check-ins and rupture explorations both count as having asked; invalid ones do not.
        rows = [step('check_in', 2, mine)[0], step('rupture_explore', 1)[0], step('check_in', 3)[0], step('suggest')[0]]
        self.assertEqual(p3rs.asked(rows), 2)
        self.assertTrue(p3rs.notes([], [], 2).endswith('2회'))


if __name__ == '__main__':
    unittest.main()
