"""HTTP tests for private-memory boundaries and durable parallel profile jobs."""
import copy
import json
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from test_lab import fixture, close, post
from test_memory_access import sample, seed
from client.memory_access.runtime import STAGE, load
from runtime import profile_batch


class BatchModel(BaseHTTPRequestHandler):
    requests = []
    lock = threading.Lock()
    active = 0
    peak = 0
    fail_profile = None
    fail_once = False
    delay = .02
    selection = None

    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        cls = type(self)
        with cls.lock:
            cls.requests.append(copy.deepcopy(data))
            cls.active += 1
            cls.peak = max(cls.peak, cls.active)
        try:
            time.sleep(cls.delay)
            source = json.loads(next(m['content'] for m in data['messages'] if m['role'] == 'user')) if data.get('tools') or data.get('structured_outputs') else {}
            profile = source.get('basic_profile', {}).get('profile_id')
            if data.get('structured_outputs'):
                if 'private_turns' in source:
                    payload = {'turns': cls.selection if cls.selection is not None else [r['turn'] for r in source['private_turns'][:3]]}
                else:
                    candidate = source['traits'][0]
                    payload = {'selections': [{'trait_id': candidate['trait_id'], 'background_ids': [], 'relevance': '현재 공개 질문과 관련된 특성'}]}
                delta = {'content': json.dumps(payload)}
                finish = 'stop'
            elif data.get('tools'):
                tool = data['tools'][0]['function']['name']
                args = sample(STAGE['function']['parameters']) if tool == 'stage_client_update' else {'utterance': '지금은 더 생각해 보고 싶어요.'}
                if tool == 'stage_client_update':
                    args['appraisal']['interpretation'] = 'SECRET_' + str(profile)
                delta = {'tool_calls': [{'index': 0, 'id': 'call_' + uuid.uuid4().hex, 'type': 'function',
                         'function': {'name': tool, 'arguments': json.dumps(args)}}]}
                finish = 'tool_calls'
                with cls.lock:
                    fail = (cls.fail_profile is not None and profile == cls.fail_profile) or cls.fail_once
                    cls.fail_once = False
                if fail:
                    finish = 'length'
            else:
                delta = {'content': '아까 말씀하실 때 어떤 마음이셨나요?'}
                finish = 'stop'
            frame = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
                     'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}}
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with cls.lock:
                cls.active -= 1


class ProfileBatchTests(unittest.TestCase):
    def setUp(self):
        BatchModel.requests = []
        BatchModel.active = BatchModel.peak = 0
        BatchModel.fail_profile = None
        BatchModel.fail_once = False
        BatchModel.delay = .02
        BatchModel.selection = None
        self.server, self.model, self.temp = fixture(model_handler=BatchModel)
        self.config = self.get('/api/config')

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
        body = dict(profile_directory=profile_batch.DEFAULT_DIRECTORY,
                    filenames=['gold_ccd_profile_001.json', 'gold_ccd_profile_011.json'],
                    versions=['Q3inner_memory', 'inner_memory_no'], turns=2, concurrency=2, retries=0)
        body.update(values)
        return self.post('create', body)

    def wait(self, jid, predicate=lambda j: j['status'] not in ('running', 'stopping')):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            result = self.get('/api/profile_batch/job?id=' + jid)
            if predicate(result):
                return result
            time.sleep(.1)
        self.fail('작업 완료 대기 초과: ' + str(result))

    def test_parallel_generation_memory_boundaries_and_snapshot(self):
        found = self.get('/api/profile_batch/profiles')
        self.assertEqual(len(found['profiles']), 100)
        self.assertFalse(any(p['error'] for p in found['profiles']))
        options = self.get('/api/profile_batch/options')
        self.assertEqual([v['id'] for v in options['versions']], ['Q3inner_memory', 'inner_memory_no'])
        job = self.job()
        self.assertEqual(BatchModel.requests, [])
        self.post('start', {'id': job['id']})
        result = self.wait(job['id'])
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual((result['completed_turns'], result['succeeded'], result['turn_percent']), (8, 4, 100))
        self.assertGreater(BatchModel.peak, 1)
        self.assertLessEqual(BatchModel.peak, 2)
        self.assertEqual(len(set(r['session_id'] for r in result['rows'])), 4)
        for row in result['rows']:
            detail = self.get('/api/profile_batch/detail?id=' + job['id'] + '&session_id=' + row['session_id'])
            session = detail['session']
            self.assertEqual(len(session['public_dialogue']), 4)
            state = load({'_storage_dir': session['client_storage_dir']}, row['version'], row['session_id'])
            self.assertEqual(len(state['records']), 2)
            first, second = state['records']
            self.assertEqual(first['memory_input'], [])
            self.assertEqual(len(second['memory_input']), 1 if row['version'] == 'Q3inner_memory' else 0)
            for record in state['records']:
                router = record['model_requests'][0]['payload']
                self.assertNotIn('SECRET_', json.dumps(router))
                self.assertNotIn('private_turns', router['messages'][-1]['content'])
                self.assertTrue(record['selected_traits'])
                main = next(r['payload'] for r in record['model_requests'] if r['stage'] == 'main')
                text = json.dumps(main)
                if row['version'] == 'inner_memory_no':
                    self.assertNotIn('SECRET_', text)
                self.assertNotIn('trajectory_progress', text)
                # No other client's private data is ever injected.
                other = 'gold_011' if row['profile_id'] == 'gold_001' else 'gold_001'
                self.assertNotIn('SECRET_' + other, text)
            self.assertIn('SECRET_' + row['profile_id'], json.dumps(second['inner']))
            with self.assertRaises(HTTPError):
                post(self.server, '/api/paired/manual', {'session_id': row['session_id'], 'expected_step': len(session['steps']), 'actor': 'counselor', 'content': '수동 입력'})
        stored = profile_batch.read(self.server, job['id'])
        self.assertEqual(len(stored['profiles']), 2)
        self.assertIn('memory_router_prompt', stored['request'])
        self.assertIn('memory_selector_prompt', stored['request'])
        self.assertEqual(self.server.storage_jobs, 0)

    def test_inner_selection_reads_all_history_but_only_injects_chosen_originals(self):
        job = self.job(filenames=['gold_ccd_profile_001.json'], turns=5)
        self.post('start', {'id': job['id']})
        result = self.wait(job['id'])
        self.assertEqual(result['status'], 'completed', result)
        for row in result['rows']:
            session = self.get('/api/paired/session?session_id=' + row['session_id'])
            records = load({'_storage_dir': session['client_storage_dir']}, row['version'], row['session_id'])['records']
            last = records[-1]
            selection = [q for q in last['model_requests'] if q['stage'] == 'selection']
            if row['version'] == 'inner_memory_no':
                self.assertFalse(selection)
                self.assertEqual(last['memory_input'], [])
                continue
            self.assertEqual(len(selection), 1)
            source = json.loads(selection[0]['payload']['messages'][-1]['content'])
            self.assertEqual([r['turn'] for r in source['private_turns']], [1, 2, 3, 4])
            expected = [{k:r[k] for k in ('turn', 'inner', 'utterance')} for r in records[:3]]
            self.assertEqual(last['memory_input'], expected)
            self.assertEqual(last['selection']['selected_turns'], [1, 2, 3])
            for q in last['model_requests']:
                if q['stage'] == 'main':
                    data = json.loads(next(m['content'] for m in q['payload']['messages'] if m['role'] == 'user'))
                    self.assertEqual(data['private_turns'], expected)
            self.assertIn('memory_selector', last['agent_usage'])
        self.get('/api/prompts/source?role=client&version=Q3inner_memory&kind=memory_selector')

    def test_inner_selection_empty_is_valid_but_invalid_ids_do_not_commit(self):
        for chosen, expected_status, completed in [([], 'completed', 2), ([999], 'failed', 1), ([1, 1], 'failed', 1)]:
            with self.subTest(chosen=chosen):
                BatchModel.selection = chosen
                job = self.job(filenames=['gold_ccd_profile_001.json'], versions=['Q3inner_memory'], turns=2)
                self.post('start', {'id': job['id']})
                result = self.wait(job['id'])
                self.assertEqual(result['rows'][0]['status'], expected_status, result)
                self.assertEqual(result['completed_turns'], completed)
                session = self.get('/api/paired/session?session_id=' + result['rows'][0]['session_id'])
                records = load({'_storage_dir': session['client_storage_dir']}, 'Q3inner_memory', session['session_id'])['records']
                self.assertEqual(len(records), completed)
                if not chosen:
                    self.assertEqual(records[-1]['memory_input'], [])
                    self.assertEqual(records[-1]['selection']['selected_turns'], [])

    def test_failure_does_not_stop_other_profiles_and_resume_only_unfinished(self):
        BatchModel.fail_profile = 'gold_011'
        job = self.job(versions=['inner_memory_no'], turns=1)
        self.post('start', {'id': job['id']})
        result = self.wait(job['id'])
        self.assertEqual((result['succeeded'], result['failed']), (1, 1))
        completed_sid = result['rows'][0]['session_id']
        failed_sid = result['rows'][1]['session_id']
        BatchModel.fail_profile = None
        self.post('start', {'id': job['id']})
        result = self.wait(job['id'])
        self.assertEqual(result['succeeded'], 2)
        self.assertEqual([r['session_id'] for r in result['rows']], [completed_sid, failed_sid])
        session = self.get('/api/paired/session?session_id=' + completed_sid)
        self.assertEqual(len(session['steps']), 1)

    def test_stop_restart_detection_resume_and_retry(self):
        BatchModel.delay = .12
        job = self.job(turns=3, concurrency=1)
        self.post('start', {'id': job['id']})
        result = self.wait(job['id'], lambda j: j['completed_turns'] >= 1)
        self.post('stop', {'id': job['id']})
        result = self.wait(job['id'])
        self.assertEqual(result['status'], 'cancelled')
        prior_turns = result['completed_turns']
        self.assertGreaterEqual(prior_turns, 1)
        stored = profile_batch.read(self.server, job['id'])
        stored['status'] = 'running'
        stored['rows'][-1]['status'] = 'queued'
        profile_batch.save(self.server, stored)
        self.assertEqual(self.get('/api/profile_batch/job?id=' + job['id'])['status'], 'interrupted')
        BatchModel.delay = .005
        self.post('start', {'id': job['id']})
        self.assertEqual(self.wait(job['id'])['completed_turns'], 12)
        BatchModel.fail_once = True
        retry = self.job(filenames=['gold_ccd_profile_001.json'], versions=['inner_memory_no'], turns=1, retries=1)
        self.post('start', {'id': retry['id']})
        result = self.wait(retry['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['rows'][0]['retry_count'], 1)

    def test_validation_and_standalone_private_seed_is_not_visible_to_router(self):
        for changes in ({'concurrency': 0}, {'versions': ['Q3']}, {'filenames': ['missing.json']}, {'profile_directory': '/etc'}):
            with self.assertRaises(HTTPError):
                self.job(**changes)
        for version in ('Q3inner_memory', 'inner_memory_no'):
            body = {'experiment_version': version, 'counselor_version': 'basic',
                    'speaker_modes': {'client': 'model', 'counselor': 'manual'},
                    'scenario': copy.deepcopy(self.config['scenario']), 'generation': self.config['generation'],
                    'messages': [{'role': 'user', 'content': '그때 왜 동의하셨나요?'}],
                    'memory_options': {'seed': seed()}}
            session = json.loads(post(self.server, '/api/paired/create', body))
            raw = post(self.server, '/api/paired/step', {'session_id': session['session_id'], 'expected_step': 0})
            events = [json.loads(line)['event'] for line in raw.splitlines()]
            self.assertEqual(events[-1]['type'], 'done', events[-1])
            state = load({'_storage_dir': events[-1]['session']['client_storage_dir']}, version, session['session_id'])
            record = state['records'][-1]
            self.assertNotIn('PRIVATE_HISTORY', json.dumps(record['model_requests'][0]))
            main = next(r for r in record['model_requests'] if r['stage'] == 'main')
            self.assertEqual('PRIVATE_HISTORY' in json.dumps(main), version == 'Q3inner_memory')


if __name__ == '__main__':
    unittest.main()
