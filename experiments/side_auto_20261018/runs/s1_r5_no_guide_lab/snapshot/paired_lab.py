"""Alternating public dialogue, isolated client memory and the repository's real C4."""
import copy
import json
import re
import select
import shutil
import socket
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import experiment_versions
import reasoning_budget
import state_summary
from counselors import catalog as counselor_catalog
from mcp_client import Cancelled

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
BASIC_COUNSELOR_PROMPT = HERE / 'prompts/basic_counselor.md'


def speaker_modes(value=None):
    value = {'client': 'model', 'counselor': 'model'} if value is None else value
    if not isinstance(value, dict) or set(value) != {'client', 'counselor'} or any(
            mode not in ('model', 'manual') for mode in value.values()):
        raise ValueError('발화 방식은 상담사·내담자 각각 model 또는 manual이어야 합니다.')
    return copy.deepcopy(value)


def c4_class():
    for directory in (PROJECT / 'src', PROJECT / 'src_c'):
        if str(directory) not in sys.path:
            sys.path.append(str(directory))
    from C4.pipeline import DiagnosticCounselorPipeline
    return DiagnosticCounselorPipeline


def root(server, session):
    if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', session):
        raise ValueError('올바르지 않은 대화 세션 ID입니다.')
    with server.paired_registry_lock:
        location = server.paired_locations.get(session)
        if location is None:
            preferred = Path(server.storage_dir)
            choices = [preferred, *(Path(path) for path in sorted(server.storage_roots) if Path(path) != preferred)]
            location = next((base for base in choices if (base / 'paired' / session / 'session.json').is_file()), preferred)
            server.paired_locations[session] = location
    return Path(location) / 'paired' / session


def new_session_id(server):
    stamp = datetime.now(timezone(timedelta(hours=9))).strftime('%y%m%d_%H%M%S')
    with server.paired_registry_lock:
        preferred = Path(server.storage_dir)
        roots = {preferred, *(Path(path) for path in server.storage_roots)}
        number = 1
        while True:
            session = stamp if number == 1 else f'{stamp}_{number}'
            number += 1
            if session in server.paired_locations or any((base / 'paired' / session).exists() for base in roots):
                continue
            try:
                (preferred / 'paired' / session).mkdir(parents=True)
            except FileExistsError:
                continue
            server.paired_locations[session] = preferred
            return session


def read_session(server, session):
    path = root(server, session) / 'session.json'
    if not path.is_file():
        raise ValueError('저장된 양방향 대화가 없습니다.')
    data = json.loads(path.read_text(encoding='utf-8'))
    data.setdefault('storage_dir', str(path.parent.parent.parent))
    data['retry_manual'] = retry_manual(server, data)
    return data


def retry_manual(server, session):
    """Allow replacing only the last manual speech after its reply failed."""
    if not session.get('steps') or not session.get('attempts'):
        return None
    step, attempt = session['steps'][-1], session['attempts'][-1]
    actor = session['next_actor']
    if (step.get('source') != 'manual' or step['actor'] == actor
            or attempt['actor'] != actor or attempt['step'] != len(session['steps']) + 1
            or session.get('speaker_modes', {}).get(actor, 'model') != 'model'):
        return None
    path = root(server, session['session_id']).parent.parent / 'paired/logs' / session['session_id'] / (attempt['turn_id'] + '.json')
    if not path.is_file():
        return None
    result = json.loads(path.read_text(encoding='utf-8'))
    if result.get('status') not in ('failed', 'cancelled'):
        return None
    return {'actor': step['actor'], 'turn_id': step['turn_id'], 'content': step['utterance']}


def save_session(server, session):
    state_summary.write_new(root(server, session['session_id']) / 'session.json', session)


def lock_for(server, session):
    with server.paired_registry_lock:
        return server.paired_locks.setdefault(session, threading.Lock())


def counselor_info():
    language = __import__('os').environ.get('CONVERSATION_PROMPT_LANGUAGE', 'ko').strip().lower()
    if language not in ('ko', 'en'):
        raise ValueError('C4 프롬프트 언어는 ko/en이어야 합니다.')
    directory = PROJECT / ('prompt' if language == 'ko' else 'prompt_en') / 'C4'
    return {'version': 'C4', 'language': language, 'source': 'src_c/C4/pipeline.py',
            'default': 'basic', 'versions': counselor_catalog.specifications(),
            'config': json.loads((PROJECT / 'config/C4/pipeline.json').read_text()),
            'prompts': {str(path.relative_to(directory)): path.read_text() for path in
                        [directory/'completion_codex/information.md', directory/'continuity_codex/work_record.md',
                         directory/'continuity_codex/u8_observer.md', directory/'continuity_codex/free_action.md',
                         directory/'continuity_codex/u2.md', directory/'continuity_codex/u8_selector.md',
                         directory/'continuity_codex/u11_selector.md', directory/'diagnostic_codex/v4_selector.md',
                         directory/'completion_codex/executor.md']}}


def public_client_response(response):
    """Never hand analysis/JSON to the counselor as if it were spoken dialogue."""
    text = response.strip()
    candidate = text
    if candidate.startswith('```') and candidate.endswith('```'):
        candidate = candidate.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get('utterance'), str) and data['utterance'].strip():
        return data['utterance'].strip()
    # Basic asks for an actual utterance, but does not prescribe one field label.
    # Parse explicit speech sections; never treat the whole analysis as dialogue.
    prefix = r'^\s*(?:#{1,6}\s+)?(?:(?:[-+*]|\d+[.)])\s+)?(?:\*\*|__)?'
    suffix = r'(?:\*\*|__)?\s*(?:[:：]\s*(?:\*\*|__)?\s*(.*)|$)'
    speech = re.compile(prefix + r'(?:실제\s+내담자\s*발화|내담자\s*발화|실제\s*발화)' + suffix)
    boundary = re.compile(prefix + r'(?:느낌|판단\s*이유|내면\s*상태|반응\s*근거|해석|분석|상담[사자]\s*발화|feelings|reason|inner_state)' + suffix)
    sections, current = [], None
    for line in candidate.splitlines():
        header = speech.fullmatch(line)
        if header:
            current = [header.group(1) or '']
            sections.append(current)
        elif boundary.fullmatch(line):
            current = None
        elif current is not None:
            current.append(line)
    values = ['\n'.join(section).strip() for section in sections]
    if len(values) == 1 and values[0]:
        return values[0]
    raise ValueError('basic 응답에서 공개 내담자 발화를 분리할 수 없습니다. utterance JSON 또는 실제 발화:/내담자 발화: 구획 하나가 필요합니다.')


def create(server, config, body, runtime, base_build):
    request = copy.deepcopy(body)
    request.pop('request_id', None)
    request['session_id'] = None
    counselor = request.get('counselor_version', 'C4')
    spec = counselor_catalog.specification(counselor)
    if spec is None:
        raise ValueError('지원하지 않는 상담사 버전입니다.')
    request['counselor_version'] = counselor
    request['counselor_reasoning_budgets'] = reasoning_budget.normalize(request.get('counselor_reasoning_budgets'))
    if spec.get('system_prompt') is not None:
        prompt = request.get('counselor_system_prompt', spec['system_prompt'])
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('상담사의 System prompt를 입력하세요.')
        request['counselor_system_prompt'] = prompt
    experiment_versions.configure(config, request)
    if experiment_versions.client_runtime(request['experiment_version']) is not None:
        request['client_reasoning_budgets'] = reasoning_budget.normalize(request.get('client_reasoning_budgets'))
    scenario = runtime.validate_scenario(request['scenario']) if 'scenario' in request else runtime.load_scenario(config)
    # Freeze the submitted speaker text into the final selected pair before any call.
    current = request.get('messages', [{}])[-1].get('content', '').strip()
    if not current:
        raise ValueError('시작할 상담사 발화를 입력하세요.')
    if scenario.get('dialogue_turns'):
        next(row for row in reversed(scenario['dialogue_turns']) if row['enabled'])['counselor'] = current
        scenario = runtime.validate_scenario(scenario)
    request['scenario'] = scenario
    request['messages'] = [{'role': 'user', 'content': current}]
    runtime.build(copy.deepcopy(config), request, [], base_build, preview=True)
    public = copy.deepcopy(scenario['dialogue'])
    if scenario.get('dialogue_format', 'pairs') == 'pairs':
        public = [{'role': role, 'content': row[role]} for row in public for role in ('counselor', 'client')]
    public.append({'role': 'counselor', 'content': current})
    modes = speaker_modes(request.get('speaker_modes'))
    session = {'session_id': new_session_id(server), 'client_version': request['experiment_version'], 'counselor_version': counselor,
               'speaker_modes': modes,
               'connection': {key: copy.deepcopy(config[key]) for key in
                              ('model', 'vllm_url', 'timeout_seconds', 'inspect_prompt', 'inspection_timeout_seconds', 'api_key_env', 'chat_template_kwargs') if key in config},
               'settings': request, 'public_dialogue': public, 'next_actor': 'client', 'steps': [],
               'attempts': [], 'counselor_checkpoint': None, 'initial_public_count': len(public), 'storage_dir': str(server.storage_dir)}
    save_session(server, session)
    counselor_catalog.initialize(counselor, root(server, session['session_id']))
    return session


def phase(label):
    if label == 'basic_counselor':
        return '기본 상담사 발화 생성'
    if 'information_observer' in label:
        return '관찰·정보 갱신'
    if 'action_selector' in label:
        return '상담 행동 선택'
    if 'artifact_executor' in label:
        return '상담사 발화 생성'
    return label  # Includes C4's existing validation/repair calls, not invented stages.


def counselor_step(config, session, backend, base_build, emit, cancelled, directory=None):
    # Only public utterances enter C4, never scenario/profile/state/summary/tool records.
    history = [{'role': 'user' if row['role'] == 'client' else 'assistant', 'content': row['content']}
               for row in session['public_dialogue']]
    usage = {}
    build = reasoning_budget.wrap_builder(base_build, session['settings'].get('counselor_reasoning_budgets'))
    version = session.get('counselor_version', 'C4')
    runner = counselor_catalog.runner(version)
    if runner:
        return runner(config, session, backend, build, emit, cancelled, directory)
    calls = 0
    def call_model(system, user, label, temperature, max_tokens):
        nonlocal calls
        if cancelled():
            raise Cancelled('상담사 생성을 중단했습니다.')
        calls += 1
        generation = {**config['generation'], 'temperature': temperature, 'max_tokens': max_tokens}
        payload = build(config, {'system_prompt': system, 'messages': [{'role': 'user', 'content': user}],
                                      'generation': generation}, [], False)
        emit('request', phase=phase(label), label=label, iteration=calls, payload=copy.deepcopy(payload))
        if config.get('inspect_prompt', True):
            try:
                emit('inspection', phase=phase(label), iteration=calls, **backend.inspect_prompt(config, payload))
            except Exception as error:
                emit('inspection', phase=phase(label), iteration=calls, error=str(error))
        output, reasoning, finish, complete = '', '', None, False
        with backend.upstream(config, '/v1/chat/completions', payload) as response:
            for frame in backend.stream_events(response):
                if cancelled():
                    raise Cancelled('상담사 생성을 중단했습니다.')
                if frame == '[DONE]':
                    complete = True
                    break
                chunk = json.loads(frame)
                if chunk.get('error'):
                    raise ValueError(str(chunk['error']))
                emit('chunk', phase=phase(label), iteration=calls, data=chunk)
                for choice in chunk.get('choices', []):
                    if choice.get('index', 0) == 0:
                        output += choice.get('delta', {}).get('content') or ''
                        delta = choice.get('delta', {})
                        reasoning += delta.get('reasoning_content') or delta.get('reasoning') or ''
                        finish = choice.get('finish_reason') or finish
                for key, value in (chunk.get('usage') or {}).items():
                    if isinstance(value, int):
                        usage[key] = usage.get(key, 0) + value
        if not complete or finish != 'stop' or not output.strip():
            raise ValueError(('C4' if version == 'C4' else '기본 상담사') + ' 모델 응답이 정상 완료되지 않았습니다: ' + str(finish))
        emit('model_result', phase=phase(label), iteration=calls, response=output, reasoning=reasoning, finish_reason=finish)
        return output
    if version == 'basic':
        public = '\n\n'.join(('내담자' if row['role'] == 'client' else '상담사') + ': ' + row['content']
                              for row in session['public_dialogue'])
        utterance = call_model(session['settings']['counselor_system_prompt'], '## 공개 대화\n\n' + public,
                               'basic_counselor', config['generation']['temperature'], config['generation']['max_tokens']).strip()
        emit('basic_counselor_record', version='basic', response=utterance)
        return utterance, None, usage
    pipeline = c4_class()(call_model, 'C4')
    if session['counselor_checkpoint']:
        pipeline.restore(session['counselor_checkpoint'])
    result = pipeline.generate(history)
    emit('counselor_record', record=result.record, checkpoint=pipeline.snapshot())
    return result.utterance, pipeline.snapshot(), usage


def client_step(server, config, session, backend, runtime, base_build, emit, cancelled, attempt):
    body = copy.deepcopy(session['settings'])
    body['session_id'] = session['session_id']
    current = session['public_dialogue'][-1]['content']
    body['messages'] = [{'role': 'user', 'content': current}]
    if session['client_version'] == 'basic':
        scenario = body['scenario']
        scenario.pop('dialogue_turns', None)
        scenario.pop('selected_counselor_utterance', None)
        scenario['dialogue_format'] = 'messages'
        scenario['dialogue'] = copy.deepcopy(session['public_dialogue'][:-1])
    cfg = copy.deepcopy(config)
    # Private client records live under this pair, independent of stand-alone sessions.
    working = root(server, session['session_id']) / 'client_attempts' / attempt
    if session.get('client_storage_dir'):
        shutil.copytree(session['client_storage_dir'], working)
    working.mkdir(parents=True, exist_ok=True)
    cfg['_storage_dir'] = str(working)
    cfg['_paired_public_dialogue'] = copy.deepcopy(session['public_dialogue'])
    cfg['_paired_initial_count'] = session.get('initial_public_count', len(session['public_dialogue']) - len(session['steps']))
    experiment_versions.configure(cfg, body)
    payload = runtime.build(cfg, body, [], base_build)
    cfg['_turn_info'] = {'session_id': session['session_id'], 'turn_id': attempt,
                         'saved_path': str(working),
                         'url': backend.base_url(config) + '/v1/chat/completions'}
    cfg['_experience_case_id'] = body['scenario'].get('id')
    cfg['_request_states'] = copy.deepcopy(body['scenario']['states'])
    final = {}
    def event(kind, **data):
        if kind == 'done':
            final.update(data)
            emit('client_record', **data)
        else:
            emit(kind, **data)
    if session['client_version'] == 'basic':
        emit('request', payload=copy.deepcopy(payload), iteration=1)
    runtime.execute(cfg, payload, None, backend.upstream, backend.stream_events, backend.inspect_prompt,
                    event, cancelled, body['messages'], base_build, 'dynamic')
    if final.get('finish_reason') != 'stop':
        raise ValueError('내담자 발화가 정상 완료되지 않았습니다.')
    utterance = final['response'] if session['client_version'] == 'state_summary' or experiment_versions.client_runtime(session['client_version']) is not None else public_client_response(final['response'])
    return utterance, final.get('usage', {}), str(working)


def handle_get(handler):
    parts = urlsplit(handler.path)
    if parts.path == '/api/paired/counselor':
        handler.json_response(counselor_info())
        return True
    if parts.path not in ('/api/paired/session', '/api/paired/events', '/api/paired/version_state'):
        return False
    params = parse_qs(parts.query)
    if params.get('key'):
        import session_browser
        location, kind, sid = session_browser.resolve(handler.server, params['key'][0])
        if kind != 'paired' or sid != params.get('session_id', [''])[0]:
            raise ValueError('선택한 세션 정보가 일치하지 않습니다.')
        with handler.server.paired_registry_lock:
            handler.server.paired_locations[sid] = location
    session = read_session(handler.server, params.get('session_id', [''])[0])
    if parts.path.endswith('/version_state'):
        version = session.get('counselor_version', 'C4')
        handler.json_response({'version': version, 'data': counselor_catalog.view_state(version, root(handler.server, session['session_id']))})
    elif parts.path.endswith('/session'):
        handler.json_response(session)
    else:
        attempt = params.get('attempt', [''])[0]
        if attempt not in [row['turn_id'] for row in session['attempts']]:
            raise ValueError('이 세션의 실행 기록이 아닙니다.')
        path = root(handler.server, session['session_id']).parent.parent / 'paired/logs' / session['session_id'] / (attempt + '.events.jsonl')
        handler.json_response({'events': [json.loads(line) for line in path.read_text().splitlines()]})
    return True


def handle_post(handler, backend, runtime, base_build):
    if handler.path not in ('/api/paired/create', '/api/paired/step', '/api/paired/manual', '/api/paired/controls'):
        return False
    streaming, active_id, lock = False, None, None
    log = None
    try:
        if handler.headers.get('X-Gemma-Request') != '1':
            raise ValueError('요청 헤더가 없습니다.')
        size = int(handler.headers.get('Content-Length', 0))
        if not 0 < size <= 4 * 1024 * 1024:
            raise ValueError('요청 크기를 확인하세요.')
        body = json.loads(handler.rfile.read(size))
        config = backend.read_config(handler.server.config_path)
        if handler.path.endswith('/create'):
            handler.json_response(create(handler.server, config, body, runtime, base_build))
            return True
        sid = body.get('session_id')
        root(handler.server, sid)
        lock = lock_for(handler.server, sid)
        if not lock.acquire(blocking=False):
            lock = None
            raise ValueError('같은 세션의 생성이 이미 진행 중입니다.')
        session = read_session(handler.server, sid)
        if type(body.get('expected_step')) is not int or body['expected_step'] != len(session['steps']):
            raise ValueError('대화 단계가 달라졌습니다. 저장된 세션을 다시 불러오세요.')
        if handler.path.endswith('/controls'):
            session['speaker_modes'] = speaker_modes(body.get('speaker_modes'))
            save_session(handler.server, session)
            handler.json_response(session)
            return True
        if handler.path.endswith('/manual'):
            actor, content = body.get('actor'), body.get('content')
            if actor not in ('client', 'counselor') or not isinstance(content, str) or not content.strip() or len(content) > 100000:
                raise ValueError('직접 입력할 역할과 발화를 확인하세요.')
            content = content.strip()
            if body.get('replace_turn_id') is not None:
                retry = retry_manual(handler.server, session)
                if (not retry or retry['actor'] != actor or retry['turn_id'] != body['replace_turn_id']
                        or retry['content'] != body.get('replace_content')):
                    raise ValueError('상대 응답이 실패한 마지막 직접 입력 발화만 수정할 수 있습니다.')
                step = session['steps'][-1]
                session.setdefault('manual_revisions', []).append({
                    'turn_id': step['turn_id'], 'before': step['utterance'], 'after': content,
                    'failed_attempt': session['attempts'][-1]['turn_id']})
                step['utterance'] = content
                session['public_dialogue'][-1]['content'] = content
                session['retry_manual'] = None
                save_session(handler.server, session)
                handler.json_response({'session': session, 'step': step})
                return True
            # The initial counselor target may be replaced before the first reply.
            if not session['steps'] and actor == 'counselor':
                session['public_dialogue'][-1]['content'] = content
                session['settings']['messages'][-1]['content'] = content
                save_session(handler.server, session)
                handler.json_response({'session': session, 'step': None})
                return True
            if actor != session['next_actor']:
                raise ValueError('현재 차례는 ' + ('내담자' if session['next_actor'] == 'client' else '상담사') + '입니다.')
            step = {'number': len(session['steps']) + 1, 'actor': actor, 'utterance': content,
                    'source': 'manual', 'turn_id': 'manual_' + uuid.uuid4().hex,
                    'saved_path': str(root(handler.server, sid) / 'session.json')}
            session['public_dialogue'].append({'role': actor, 'content': content})
            session['steps'].append(step)
            session['next_actor'] = 'client' if actor == 'counselor' else 'counselor'
            session['retry_manual'] = None
            save_session(handler.server, session)
            handler.json_response({'session': session, 'step': step})
            return True
        active_id = body.get('request_id') or uuid.uuid4().hex
        if not isinstance(active_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', active_id):
            active_id = None
            raise ValueError('잘못된 request_id입니다.')
        with handler.server.active_lock:
            if active_id in handler.server.active:
                active_id = None
                raise ValueError('이미 진행 중인 request_id입니다.')
            flag = threading.Event()
            handler.server.active[active_id] = flag
        def cancelled():
            if flag.is_set():
                return True
            if select.select([handler.connection], [], [], 0)[0]:
                return not handler.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
            return False
        role = session['next_actor']
        if session.get('speaker_modes', {}).get(role, 'model') == 'manual':
            raise ValueError('현재 차례는 직접 입력으로 설정되어 있습니다.')
        log = backend.PairedTurnLog(root(handler.server, sid).parent.parent / 'paired/logs', {'session_id': sid, 'actor': role,
                              'step': len(session['steps']) + 1, 'experiment_version': session['client_version']})
        # Persist the frozen settings actually used, not edits made during a running pair.
        config['generation'] = session['settings']['generation']
        config.update(session['connection'])
        session['attempts'].append({'turn_id': log.state['turn_id'], 'actor': role,
                                    'step': len(session['steps']) + 1, 'saved_path': str(log.path)})
        save_session(handler.server, session)
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        handler.send_header('Cache-Control', 'no-store');handler.send_header('X-Accel-Buffering', 'no')
        handler.end_headers();streaming = True
        def send(kind, **data):
            logged = log.append({'type': kind, 'actor': role, **data})
            handler.send_event({'type': 'role_event', 'actor': role, 'event': logged})
        send('step_start', step=len(session['steps']) + 1, session_id=sid, turn_id=log.state['turn_id'], public_dialogue=session['public_dialogue'])
        if role == 'counselor':
            utterance, checkpoint, usage = counselor_step(config, session, backend, base_build, send, cancelled,
                                                          root(handler.server, sid))
            session['counselor_checkpoint'] = checkpoint
        else:
            utterance, usage, storage = client_step(handler.server, config, session, backend, runtime, base_build, send, cancelled, log.state['turn_id'])
            session['client_storage_dir'] = storage
        session['public_dialogue'].append({'role': role, 'content': utterance})
        step = {'number': len(session['steps']) + 1, 'actor': role, 'utterance': utterance,
                'turn_id': log.state['turn_id'], 'usage': usage, 'saved_path': str(log.path)}
        session['steps'].append(step)
        session['next_actor'] = 'counselor' if role == 'client' else 'client'
        session['retry_manual'] = None
        save_session(handler.server, session)
        send('done', response=utterance, usage=usage, step=step, session=session)
    except Exception as error:
        if streaming:
            try:
                kind = 'cancelled' if isinstance(error, Cancelled) else 'error'
                logged = log.append({'type': kind, 'error': backend.error_text(error)})
                handler.send_event({'type': 'role_event', 'actor': log.state['input']['actor'], 'event': logged})
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            handler.json_response({'error': backend.error_text(error)}, 409 if lock is None and active_id else 400)
    finally:
        if active_id:
            with handler.server.active_lock:
                handler.server.active.pop(active_id, None)
        if lock:
            lock.release()
    return True
