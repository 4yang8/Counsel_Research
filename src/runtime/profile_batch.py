"""Durable, bounded parallel generation over frozen profile/condition pairs."""
import copy
import json
import re
import threading
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from project_paths import PROJECT_ROOT
from client import registry
from client.memory_access import INNER_VERSIONS, PAIR_VERSIONS, COMPARE_VERSION, COMPARE_VERSIONS, LEGACY_CM_VERSIONS
from counselor.rs_variants import VERSIONS as RS_VERSIONS
from counselor.rs_variants.runtime import freeze as freeze_rs
from client.memory_access.inner_router import PROMPT as ROUTER_PROMPT
from client.memory_access.runtime import load as load_memory
from client.memory_access.memory import digest
from counselor import catalog as counselors
from counselor.P1.runtime import write
from runtime import compare_branch
from runtime import job_ids
from runtime import paired_lab as pairs
from runtime.cancellation import Cancelled
from storage.file_manager import generation

DEFAULT_DIRECTORY = str(PROJECT_ROOT / 'resources/001-100')
OPENING = '오늘 상담에서 어떤 이야기를 나누고 싶으신가요?'
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted')
# P3q, P3rs and P3rs_sub return a counselor-side record with each utterance; the other choices are single prompts.
BATCH_COUNSELORS = (*counselors.SINGLE_PROMPT_PATHS, 'P3q', 'P3rs', 'P3rs_sub', *RS_VERSIONS)


def initialize(server):
    server.batch_lock = threading.RLock()
    server.batch_workers = {}
    server.batch_locations = {}


def profile_files(directory):
    if not isinstance(directory, str) or not directory.strip():
        raise ValueError('프로필 폴더 경로를 입력하세요.')
    folder = Path(directory).expanduser()
    if not folder.is_absolute():
        folder = PROJECT_ROOT / folder
    folder = folder.resolve()
    if not folder.is_relative_to(PROJECT_ROOT) or any(p in ('.git', '.codex', '.agents', '.aws') for p in folder.relative_to(PROJECT_ROOT).parts):
        raise ValueError('프로젝트 내부의 프로필 폴더를 지정하세요.')
    if not folder.is_dir():
        raise ValueError('프로필 폴더가 없습니다.')
    files = sorted(folder.glob('*.json'))
    if not files or len(files) > 1000:
        raise ValueError('폴더 바로 아래에 JSON 프로필 1~1000개가 필요합니다.')
    rows = []
    seen = set()
    for path in files:
        row = {'filename': path.name, 'error': ''}
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('일반 JSON 파일(2 MB 이하)이 필요합니다.')
            profile = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(profile, dict):
                raise ValueError('프로필은 JSON 객체여야 합니다.')
            required = {'profile_id': str, 'demographic_context': dict, 'presenting_problem': str,
                        'core_belief': dict, 'intermediate_beliefs': dict, 'coping_strategies': list,
                        'childhood_data': str, 'situations': list}
            for key, kind in required.items():
                if not isinstance(profile.get(key), kind) or not profile[key]:
                    raise ValueError('프로필 필드 확인: ' + key)
            if profile['profile_id'] in seen:
                raise ValueError('중복 profile_id: ' + profile['profile_id'])
            seen.add(profile['profile_id'])
            row.update(profile_id=profile['profile_id'], profile=profile,
                       belief=profile['core_belief'].get('core_belief_statement', ''))
        except (OSError, ValueError, TypeError) as error:
            row['error'] = str(error)
        rows.append(row)
    return str(folder), rows


def directory(server, jid):
    if not job_ids.valid(jid) and (not isinstance(jid, str) or not re.fullmatch(r'batch_[0-9a-f]{20}', jid)):
        raise ValueError('대량 작업 ID가 올바르지 않습니다.')
    if jid not in server.batch_locations:
        for base in sorted(server.storage_roots):
            candidate = Path(base) / 'profile_batches' / jid
            if (candidate / 'job.json').exists():
                server.batch_locations[jid] = candidate
                break
    return server.batch_locations.get(jid, Path(server.storage_dir) / 'profile_batches' / jid)


def save(server, job):
    folder = directory(server, job['id'])
    folder.mkdir(parents=True, exist_ok=True)
    write(folder / 'job.json', json.dumps(job, ensure_ascii=False, indent=2))


def read(server, jid):
    job = json.loads((directory(server, jid) / 'job.json').read_text())
    worker = server.batch_workers.get(jid)
    if job['status'] in ('running', 'stopping') and not (worker and worker['thread'].is_alive()):
        job['status'] = 'interrupted'
        for row in job['rows']:
            if row['status'] in ('running', 'queued'):
                row.update(status='interrupted', phase='서버 중단 · 저장된 턴부터 재개 가능')
        save(server, job)
    return job


def public_job(server, job):
    keys = ('id', 'created_at', 'status', 'options', 'rows', 'profile_directory', 'versions', 'error', 'mode')
    result = {k: copy.deepcopy(job[k]) for k in keys if k in job}
    count = len(job['rows'])
    turns = sum(r['completed_turns'] for r in job['rows'])
    total = sum(r.get('target_turns', job['options']['turns']) for r in job['rows'])
    finished = sum(r['status'] in TERMINAL for r in job['rows'])
    result.update(completed_turns=turns, total_turns=total, turn_percent=round(turns / total * 100, 1),
                  finished=finished, total=count, finished_percent=round(finished / count * 100, 1),
                  succeeded=sum(r['status'] == 'completed' for r in job['rows']),
                  failed=sum(r['status'] == 'failed' for r in job['rows']),
                  output_path=str(directory(server, job['id'])))
    return result


def integer(body, key, default, lo, hi=None):
    value = body.get(key, default)
    if type(value) is not int or value < lo or (hi is not None and value > hi):
        bounds = f'{lo} 이상' if hi is None else f'{lo}~{hi}'
        raise ValueError(f'{key}: {bounds} 정수를 입력하세요.')
    return value


def create(server, config, body, runtime, builder, persist=True):
    folder, candidates = profile_files(body.get('profile_directory', DEFAULT_DIRECTORY))
    names = body.get('filenames', [r['filename'] for r in candidates])
    if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise ValueError('중복 없이 프로필 파일을 선택하세요.')
    by_name = {r['filename']: r for r in candidates}
    if set(names) - set(by_name):
        raise ValueError('선택한 파일이 조회한 폴더에 없습니다.')
    selected = [by_name[n] for n in names]
    if any(r['error'] for r in selected):
        raise ValueError('잘못된 프로필: ' + '; '.join(r['filename'] + ': ' + r['error'] for r in selected if r['error']))
    mode = body.get('mode', 'versions')
    if mode not in ('versions', 'compare'):
        raise ValueError('생성 방식은 versions 또는 compare입니다.')
    options = {'turns': integer(body, 'turns', 20, 1, 200),
               'concurrency': integer(body, 'concurrency', 4, 1),
               'retries': integer(body, 'retries', 1, 0, 3)}
    if mode == 'compare':
        # One client version; the shared prefix and both branches come from the same settings.
        versions = [body.get('compare_version', COMPARE_VERSION)]
        if versions[0] not in COMPARE_VERSIONS:
            raise ValueError('분기 방식의 내담자 버전을 확인하세요.')
        options['fork_turn'] = integer(body, 'fork_turn', compare_branch.DEFAULT_FORK_TURN, 1, options['turns'])
    else:
        versions = body.get('versions', list(PAIR_VERSIONS))
        if not isinstance(versions, list) or not versions or any(v not in PAIR_VERSIONS for v in versions) or len(set(versions)) != len(versions):
            raise ValueError('내면 유지/미유지 버전을 선택하세요.')
    opening = body.get('opening', OPENING)
    if not isinstance(opening, str) or not opening.strip() or len(opening) > 10000:
        raise ValueError('시작 상담사 발화를 입력하세요 (1~10000자).')
    chosen = body.get('counselor_version', compare_branch.DEFAULT_COUNSELOR if mode == 'compare' else 'basic')
    if chosen not in BATCH_COUNSELORS:
        raise ValueError('대량생성에서 지원하는 상담사 버전을 선택하세요.')
    template = runtime.load_scenario(config)
    # No unrelated seed dialogue, state summary, or preset trajectory is carried over.
    template.pop('dialogue_turns', None)
    template.pop('selected_counselor_utterance', None)
    template.update(dialogue_format='messages', dialogue=[])
    request = {k: copy.deepcopy(body[k]) for k in ('model', 'vllm_url', 'generation', 'thinking_mode',
        'client_reasoning_budgets', 'counselor_reasoning_budgets', 'counselor_system_prompt',
        'counselor_intervention_condition') if k in body}
    request.setdefault('generation', copy.deepcopy(config['generation']))
    request.setdefault('model', config['model'])
    request.setdefault('vllm_url', config['vllm_url'])
    request.setdefault('counselor_system_prompt', counselors.specification(chosen)['system_prompt'])
    request.update(counselor_version=chosen, speaker_modes={'client': 'model', 'counselor': 'model'},
                   messages=[{'role': 'user', 'content': opening.strip()}],
                   system_prompt=registry.client_runtime(versions[0] if mode == 'compare' else 'Q3inner_memory').specification()['system_prompt'],
                   memory_router_prompt=ROUTER_PROMPT.read_text(),
                   memory_selector_prompt=(ROUTER_PROMPT.parent/'inner_selector.md').read_text(),
                   memory_options={'input_char_limit': integer(body, 'input_char_limit', 240000, 1000, 2000000)})
    if mode == 'compare':
        request['compare_memory_mode'] = 'provided'  # the common session; each branch sets its own
    freeze_rs(request)
    # Validate with the real preview boundary before creating any sessions or making model calls.
    probe = {**copy.deepcopy(request), 'experiment_version': versions[0],
             'scenario': {**copy.deepcopy(template), 'profile': selected[0]['profile']}}
    from runtime.connection_settings import apply
    check_config = copy.deepcopy(config)
    apply(check_config, probe)
    registry.configure(check_config, probe)
    if 'cm_prompts' in probe:
        request['cm_prompts'] = copy.deepcopy(probe['cm_prompts'])
    runtime.build(check_config, probe, [], builder, preview=True)
    if not isinstance(request['counselor_system_prompt'], str) or not request['counselor_system_prompt'].strip():
        raise ValueError('상담사 프롬프트가 비어 있습니다.')
    job = {'id': job_ids.reserve(Path(server.storage_dir) / 'profile_batches', [Path(r)/'profile_batches' for r in server.storage_roots]) if persist else None, 'created_at': datetime.now(timezone.utc).isoformat(),
           'status': 'ready', 'mode': mode, 'profile_directory': folder, 'versions': versions, 'options': options,
           'request': request, 'scenario_template': template, 'profiles': selected,
           'frozen_config': {k: copy.deepcopy(config[k]) for k in ('timeout_seconds','inspect_prompt',
                'inspection_timeout_seconds','api_key_env','chat_template_kwargs','system_role_mode','delegation') if k in config},
           'rows': [], 'error': ''}
    units = compare_branch.plan(options, versions[0]) if mode == 'compare' else [(v, None, None) for v in versions]
    for profile in selected:
        for version, branch, target in units:
            job['rows'].append({'index': len(job['rows']), 'filename': profile['filename'],
                'profile_id': profile['profile_id'], 'profile_hash': digest(profile['profile']),
                'version': version, 'status': 'queued', 'completed_turns': 0, 'session_id': None,
                'phase': '대기', 'error': '', 'retry_count': 0,
                **({'branch': branch, 'target_turns': target} if branch else {})})
    if not persist:
        return job
    save(server, job)
    return public_job(server, job)


def advance(server, config, session, backend, runtime, builder, cancelled, observe):
    """Commit public speech and private state using paired_lab's existing isolation."""
    actor = session['next_actor']
    config = copy.deepcopy(config)
    config.update(session['connection'])
    config['generation'] = copy.deepcopy(session['settings']['generation'])
    log = backend.PairedTurnLog(pairs.root(server, session['session_id']).parent.parent / 'paired/logs',
        {'session_id': session['session_id'], 'actor': actor, 'step': len(session['steps']) + 1,
         'experiment_version': session['client_version'], 'batch_job': session['batch_managed']})
    session['attempts'].append({'turn_id': log.state['turn_id'], 'actor': actor,
                               'step': len(session['steps']) + 1, 'saved_path': str(log.path)})
    pairs.save_session(server, session)

    def emit(kind, **data):
        log.append({'type': kind, 'actor': actor, **data})
        if kind == 'agent_event' and data.get('event', {}).get('type') == 'request':
            observe({'memory_selector': '과거 내면 기록 선별 중', 'rupture_observer': '상담사 관찰 담당 실행 중'}.get(
                data.get('agent_kind'), '특성·배경 선택 중'))
        elif kind == 'request':
            observe('내면·발화 생성 중' if actor == 'client' else '상담사 발화 생성 중')

    try:
        if cancelled():
            raise Cancelled('대량생성 중단')
        emit('step_start', session_id=session['session_id'], turn_id=log.state['turn_id'])
        extra = {}
        if actor == 'client':
            utterance, usage, storage = pairs.client_step(server, config, session, backend, runtime, builder,
                                                          emit, cancelled, log.state['turn_id'])
            session['client_storage_dir'] = storage
        else:
            utterance, checkpoint, usage = pairs.counselor_step(config, session, backend, builder, emit,
                                                               cancelled, pairs.root(server, session['session_id']))
            session['counselor_checkpoint'] = checkpoint
            if isinstance(checkpoint, dict) and checkpoint.get('version') == 'P3q':
                # Analysis-only mark of the revisited client turn; public_dialogue stays role + content.
                extra['revisit'] = copy.deepcopy(checkpoint['revisits'][-1])
            elif isinstance(checkpoint, dict) and checkpoint.get('version') in ('P3rs', 'P3rs_sub', *RS_VERSIONS):
                # Counselor-side note of the withdrawal marker seen and the rupture explored.
                extra['rupture'] = copy.deepcopy(checkpoint['records'][-1])
        session['public_dialogue'].append({'role': actor, 'content': utterance})
        session['steps'].append({'number': len(session['steps']) + 1, 'actor': actor, 'utterance': utterance,
                                'turn_id': log.state['turn_id'], 'saved_path': str(log.path), 'usage': usage, **extra})
        session['next_actor'] = 'counselor' if actor == 'client' else 'client'
        pairs.save_session(server, session)
        emit('done', response=utterance, usage=usage, session=session)
    except Exception as error:
        if not log.terminal:
            emit('cancelled' if isinstance(error, Cancelled) else 'error', error=str(error))
        raise
    finally:
        log.close()


def work(server, job, backend, runtime, builder, flag, shared_pool=None):
    def update(row, **values):
        with server.batch_lock:
            row.update(values)
            save(server, job)

    def new_session(row, cfg):
        profile = next(p for p in job['profiles'] if p['filename'] == row['filename'])
        scenario = {**copy.deepcopy(job['scenario_template']), 'id': 'batch_' + profile['profile_id'],
                    'profile': copy.deepcopy(profile['profile'])}
        body = {**copy.deepcopy(job['request']), 'scenario': scenario, 'experiment_version': row['version']}
        session = pairs.create(server, cfg, body, runtime, builder)
        session['batch_managed'] = job['id']
        if row.get('branch'):
            session['compare'] = {'branch': 'common', 'fork_turn': job['options']['fork_turn']}
        pairs.save_session(server, session)
        return session

    def run(row, create=new_session, done=None, offset=0):
        """Generate one session up to its target; True when the row is complete.

        offset: client turns already present when the session was created (copied common turns).
        done: extra completion condition checked once the target count is reached.
        """
        if row['status'] == 'completed':
            return True
        target = row.get('target_turns', job['options']['turns'])
        try:
            if flag.is_set():
                raise Cancelled('대량생성 중단')
            update(row, status='running', error='', phase='세션 준비')
            cfg = backend.read_config(server.config_path)
            cfg.update(copy.deepcopy(job['frozen_config']))
            if not row['session_id']:
                update(row, session_id=create(row, cfg)['session_id'])
            with pairs.lock_for(server, row['session_id']):
                failures = 0
                while True:
                    session = pairs.read_session(server, row['session_id'])
                    count = sum(s['actor'] == 'client' for s in session['steps']) - offset
                    update(row, completed_turns=count)
                    if count >= target and (done is None or done(session)):
                        update(row, status='completed', phase='완료', error='')
                        return True
                    if flag.is_set():
                        raise Cancelled('대량생성 중단')
                    try:
                        advance(server, cfg, session, backend, runtime, builder, flag.is_set,
                                lambda phase: update(row, phase=phase))
                        failures = 0
                        update(row, error='')
                    except Cancelled:
                        raise
                    except Exception as error:
                        failures += 1
                        if failures > job['options']['retries']:
                            raise
                        update(row, retry_count=row['retry_count'] + 1, phase='실패한 발화 재시도', error=str(error))
                        if flag.wait(1):
                            raise Cancelled('대량생성 중단')
        except Exception as error:
            update(row, status='cancelled' if isinstance(error, Cancelled) else 'failed',
                   phase='중단' if isinstance(error, Cancelled) else '오류', error=str(error))
            return False

    def run_compare(rows):
        """One profile: common session, then both branches forked from its final state."""
        by = {row['branch']: row for row in rows}
        common = by['common']
        if not run(common, done=compare_branch.common_done):
            for name in compare_branch.BRANCHES:
                if by[name]['status'] != 'completed':
                    update(by[name], status=common['status'], phase='공통 구간 미완료', error=common['error'])
            return
        for name in compare_branch.BRANCHES:
            run(by[name], offset=job['options']['fork_turn'] - 1,
                create=lambda row, cfg, name=name: compare_branch.fork(
                    server, pairs.read_session(server, common['session_id']), name, job['id']))

    try:
        with (nullcontext(shared_pool) if shared_pool is not None else ThreadPoolExecutor(max_workers=job['options']['concurrency'])) as pool:
            if job.get('mode') == 'compare':
                groups = {}
                for row in job['rows']:
                    groups.setdefault(row['filename'], []).append(row)
                futures = [pool.submit(run_compare, rows) for rows in groups.values()
                           if any(row['status'] != 'completed' for row in rows)]
            else:
                futures = [pool.submit(run, row) for row in job['rows'] if row['status'] != 'completed']
            for future in as_completed(futures):
                future.result()
        with server.batch_lock:
            job['status'] = ('completed' if all(r['status'] == 'completed' for r in job['rows']) else
                             'cancelled' if flag.is_set() else 'failed')
            save(server, job)
    except Exception as error:
        with server.batch_lock:
            job.update(status='failed', error=str(error))
            save(server, job)
    finally:
        with server.storage_lock:
            server.storage_jobs -= 1


def start(server, jid, backend, runtime, builder, shared_pool=None, group_id=None):
    with server.batch_lock:
        # Several jobs may run at once; only a second start of the same job is refused.
        running = server.batch_workers.get(jid)
        if running and running['thread'].is_alive():
            raise ValueError('이 작업은 이미 진행 중입니다.')
        job = read(server, jid)
        if job.get('group_id') and group_id != job['group_id']:
            raise ValueError('조합 작업에 속한 배치는 대량생성 작업 묶음에서 재개하세요.')
        if all(r['status'] == 'completed' for r in job['rows']):
            raise ValueError('이미 모든 대화가 완료되었습니다.')
        for row in job['rows']:
            if row['status'] != 'completed':
                row.update(status='queued', phase='재개 대기', error='')
        job.update(status='running', error='')
        flag = threading.Event()
        thread = threading.Thread(target=work, args=(server, job, backend, runtime, builder, flag, shared_pool), daemon=True)
        server.batch_workers[jid] = {'thread': thread, 'flag': flag, 'job': job}
        with server.storage_lock:
            server.storage_jobs += 1
        try:
            save(server, job)
            thread.start()
        except Exception:
            with server.storage_lock:
                server.storage_jobs -= 1
            raise
        return public_job(server, job)


def handle_get(handler):
    parts = urlsplit(handler.path)
    if not parts.path.startswith('/api/profile_batch/'):
        return False
    try:
        args = parse_qs(parts.query)
        server = handler.server
        with server.batch_lock:
            if parts.path.endswith('/options'):
                from app.server import read_config
                config = read_config(server.config_path)
                result = {'profile_directory': DEFAULT_DIRECTORY, 'opening': OPENING,
                          'versions': [registry.client_runtime(v).specification() for v in PAIR_VERSIONS],
                          'counselors': [s for s in counselors.specifications() if s['id'] in BATCH_COUNSELORS],
                          'compare': {'version': registry.client_runtime(COMPARE_VERSION).specification(),
                                      'versions': [registry.client_runtime(v).specification() for v in COMPARE_VERSIONS if v not in LEGACY_CM_VERSIONS],
                                      'fork_turn': compare_branch.DEFAULT_FORK_TURN,
                                      'counselor': compare_branch.DEFAULT_COUNSELOR,
                                      'branches': compare_branch.LABELS},
                          'model': config['model'], 'models': list(dict.fromkeys([config['model'], *registry.MODEL_CHOICES])),
                          'vllm_url': config['vllm_url'], 'generation': config['generation']}
            elif parts.path.endswith('/profiles'):
                folder, rows = profile_files(args.get('directory', [DEFAULT_DIRECTORY])[0])
                result = {'directory': folder, 'profiles': [{k: v for k, v in r.items() if k != 'profile'} for r in rows]}
            elif parts.path.endswith('/list'):
                jobs = []
                for root in sorted(server.storage_roots):
                    for path in (Path(root) / 'profile_batches').glob('*/job.json'):
                        server.batch_locations[path.parent.name] = path.parent
                        j = read(server, path.parent.name)
                        jobs.append({k: j[k] for k in ('id', 'created_at', 'status')})
                result = {'jobs': sorted(jobs, key=lambda j: j['created_at'], reverse=True)}
            else:
                job = read(server, args.get('id', [''])[0])
                if parts.path.endswith('/job'):
                    result = public_job(server, job)
                elif parts.path.endswith('/detail'):
                    sid = args.get('session_id', [''])[0]
                    if not any(r['session_id'] == sid for r in job['rows']):
                        raise ValueError('이 작업의 세션이 아닙니다.')
                    session = pairs.read_session(server, sid)
                    storage = session.get('client_storage_dir')
                    records = load_memory({'_storage_dir': storage}, session['client_version'], sid)['records'] if storage else []
                    result = {'session': session, 'records': [{k: r[k] for k in ('turn','inner','utterance','memory_input','selected_traits') if k in r} for r in records]}
                else:
                    raise ValueError('지원하지 않는 대량생성 경로입니다.')
        handler.json_response(result)
    except (ValueError, OSError, KeyError, TypeError) as error:
        handler.json_response({'error': str(error)}, 400)
    return True


def handle_post(handler, backend, runtime, builder):
    if not handler.path.startswith('/api/profile_batch/'):
        return False
    try:
        if handler.headers.get('X-Gemma-Request') != '1':
            raise ValueError('요청 헤더가 없습니다.')
        size = int(handler.headers.get('Content-Length', 0))
        if not 0 < size <= 4 * 1024 * 1024:
            raise ValueError('요청 크기를 확인하세요.')
        body = json.loads(handler.rfile.read(size))
        if not isinstance(body, dict):
            raise ValueError('JSON 객체가 필요합니다.')
        server = handler.server
        if handler.path.endswith('/create'):
            with generation(server), server.batch_lock:
                result = create(server, backend.read_config(server.config_path), body, runtime, builder)
        elif handler.path.endswith('/start'):
            result = start(server, body.get('id'), backend, runtime, builder)
        elif handler.path.endswith('/stop'):
            with server.batch_lock:
                job = read(server, body.get('id'))
                worker = server.batch_workers.get(job['id'])
                if worker and worker['thread'].is_alive():
                    worker['flag'].set()
                    worker['job']['status'] = 'stopping'
                    save(server, worker['job'])
                    job = worker['job']
                result = public_job(server, job)
        else:
            raise ValueError('지원하지 않는 대량생성 경로입니다.')
        handler.json_response(result)
    except (ValueError, OSError, KeyError, TypeError) as error:
        handler.json_response({'error': str(error)}, 400)
    return True
