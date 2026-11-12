"""A persistent Cartesian product of ordinary compare batches, sharing one pool."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from counselor.P1.runtime import write
from client.memory_access import COMPARE_VERSIONS
from runtime import profile_batch as batches, job_ids
from storage.file_manager import generation


def initialize(server):
    server.matrix_workers = {}
    server.matrix_locations = {}


def directory(server, jid):
    if not job_ids.valid(jid):
        raise ValueError('조합 작업 ID가 올바르지 않습니다.')
    for root in sorted(server.storage_roots):
        path = Path(root) / 'batch_groups' / jid
        if (path / 'job.json').is_file():
            return path
    return Path(server.storage_dir) / 'batch_groups' / jid


def save(server, job):
    write(directory(server, job['id']) / 'job.json', json.dumps(job, ensure_ascii=False, indent=2))


def read(server, jid):
    job = json.loads((directory(server, jid) / 'job.json').read_text())
    worker = server.matrix_workers.get(jid)
    if job['status'] in ('running', 'stopping') and not (worker and worker['thread'].is_alive()):
        job['status'] = 'interrupted'
        save(server, job)
    return job


def selection(body, name, allowed):
    values = body.get(name)
    if not isinstance(values, list) or not values or any(not isinstance(v, str) or v not in allowed for v in values) or len(set(values)) != len(values):
        raise ValueError(name + ': 지원하는 버전을 중복 없이 하나 이상 선택하세요.')
    return values


def create(server, config, body, runtime, builder):
    clients = selection(body, 'clients', COMPARE_VERSIONS)
    counselors = selection(body, 'counselors', batches.BATCH_COUNSELORS)
    # Validate every combination before persisting any jobs; prompts are frozen per version.
    prepared = []
    conditions = body.get('counselor_conditions', {})
    if not isinstance(conditions, dict):
        raise ValueError('상담사 개입 조건 형식을 확인하세요.')
    for client in clients:
        for counselor in counselors:
            request = {k: copy.deepcopy(v) for k, v in body.items() if k not in ('counselor_system_prompt','counselor_intervention_condition')}
            request.update(mode='compare', compare_version=client, counselor_version=counselor)
            if counselor in conditions:
                request['counselor_intervention_condition'] = conditions[counselor]
            prepared.append(batches.create(server, config, request, runtime, builder, persist=False))
    gid = job_ids.reserve(Path(server.storage_dir) / 'batch_groups', [Path(r)/'batch_groups' for r in server.storage_roots])
    group = {'id': gid, 'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'ready',
             'clients': clients, 'counselors': counselors, 'concurrency': prepared[0]['options']['concurrency'],
             'batches': [], 'error': '', 'model_servers': prepared[0]['model_servers']}
    save(server, group)
    server.model_servers.configure(pool_key(server, group), group['model_servers'])
    try:
        for job in prepared:
            job['id'] = job_ids.reserve(Path(server.storage_dir) / 'profile_batches', [Path(r)/'profile_batches' for r in server.storage_roots])
            job['group_id'] = gid
            batches.save(server, job)
            group['batches'].append(job['id'])
            save(server, group)
    except Exception as exc:
        group.update(status='failed', error=str(exc))
        save(server, group)
        raise
    return public(server, group)


def pool_key(server, group):
    fallback = group.get('model_servers')
    if fallback is None:
        child = batches.read(server, group['batches'][0])
        fallback = child.get('model_servers') or batches.model_servers.normalize(None, child['request']['vllm_url'])
    return server.model_servers.scope(directory(server, group['id']) / 'model_servers.json', fallback)


def configure_servers(server, gid, values):
    with server.batch_lock:
        group = read(server, gid)
        server.model_servers.configure(pool_key(server, group), values)
        return public(server, group)


def public(server, group):
    result = copy.deepcopy(group)
    result['routing'] = server.model_servers.snapshot(pool_key(server, group))
    children = []
    for jid in group['batches']:
        job = batches.read(server, jid)
        item = batches.public_job(server, job)
        item.pop('rows')
        item.update(client=job['versions'][0], counselor=job['request']['counselor_version'])
        children.append(item)
    result['children'] = children
    for key in ('completed_turns', 'total_turns', 'succeeded', 'total', 'failed', 'finished'):
        result[key] = sum(c[key] for c in children)
    result['turn_percent'] = round(100 * result['completed_turns'] / max(1, result['total_turns']), 1)
    result['finished_percent'] = round(100 * result['finished'] / max(1, result['total']), 1)
    result['output_path'] = str(directory(server, group['id']))
    return result


def start(server, gid, backend, runtime, builder, servers=None):
    with server.batch_lock:
        previous = server.matrix_workers.get(gid)
        if previous and previous['thread'].is_alive():
            raise ValueError('이미 진행 중인 조합 작업입니다.')
        group = read(server, gid)
        jobs = [batches.read(server, jid) for jid in group['batches']]
        if not jobs or group.get('error'):
            raise ValueError('생성이 완성되지 않은 작업 묶음입니다. 오류를 확인하고 새로 만드세요.')
        if all(j['status'] == 'completed' for j in jobs):
            raise ValueError('모든 조합이 완료되었습니다.')
        if any(server.batch_workers.get(j['id'], {}).get('thread') and server.batch_workers[j['id']]['thread'].is_alive() for j in jobs):
            raise ValueError('조합의 하위 배치가 아직 종료되지 않았습니다.')
        if servers is not None:
            server.model_servers.configure(pool_key(server, group), servers)
        flag = threading.Event()
        def work():
            try:
                with ThreadPoolExecutor(max_workers=group['concurrency']) as pool:
                    started = []
                    for job in jobs:
                        with server.batch_lock:
                            if flag.is_set(): break
                            if job['status'] == 'completed': continue
                            batches.start(server, job['id'], backend, runtime, builder, shared_pool=pool, group_id=gid)
                            started.append(server.batch_workers[job['id']]['thread'])
                    for thread in started: thread.join()
                with server.batch_lock:
                    group['status'] = ('cancelled' if flag.is_set() else 'completed' if all(batches.read(server, j['id'])['status']=='completed' for j in jobs) else 'failed')
                    save(server, group)
            except Exception as exc:
                with server.batch_lock:
                    group.update(status='failed', error=str(exc)); save(server, group)
            finally:
                with server.storage_lock: server.storage_jobs -= 1
        thread = threading.Thread(target=work, daemon=True)
        server.matrix_workers[gid] = {'thread': thread, 'flag': flag, 'job': group}
        group.update(status='running', error='')
        with server.storage_lock: server.storage_jobs += 1
        try:
            save(server, group); thread.start()
        except Exception:
            with server.storage_lock: server.storage_jobs -= 1
            raise
        return public(server, group)


def stop(server, gid):
    with server.batch_lock:
        group = read(server, gid)
        worker = server.matrix_workers.get(gid)
        if worker and worker['thread'].is_alive():
            worker['flag'].set()
            group = worker['job']; group['status'] = 'stopping'; save(server, group)
        for jid in group['batches']:
            child = server.batch_workers.get(jid)
            if child and child['thread'].is_alive():
                child['flag'].set(); child['job']['status'] = 'stopping'; batches.save(server, child['job'])
        return public(server, group)


def handle_get(handler):
    parts = urlsplit(handler.path)
    if not parts.path.startswith('/api/batch_matrix/'): return False
    try:
        with handler.server.batch_lock:
            if parts.path.endswith('/list'):
                rows = [read(handler.server, p.parent.name) for root in sorted(handler.server.storage_roots)
                        for p in (Path(root)/'batch_groups').glob('*/job.json')]
                result = {'jobs': sorted(rows, key=lambda r:r['created_at'], reverse=True)}
            elif parts.path.endswith('/job'):
                result = public(handler.server, read(handler.server, parse_qs(parts.query).get('id',[''])[0]))
            else: raise ValueError('지원하지 않는 조합 작업 경로입니다.')
        handler.json_response(result)
    except (ValueError, OSError, KeyError, TypeError) as exc: handler.json_response({'error':str(exc)},400)
    return True


def handle_post(handler, backend, runtime, builder):
    if not handler.path.startswith('/api/batch_matrix/'): return False
    try:
        if handler.headers.get('X-Gemma-Request') != '1': raise ValueError('요청 헤더가 없습니다.')
        size = int(handler.headers.get('Content-Length',0))
        if not 0 < size <= 4*1024*1024: raise ValueError('요청 크기를 확인하세요.')
        body = json.loads(handler.rfile.read(size))
        if not isinstance(body,dict): raise ValueError('JSON 객체가 필요합니다.')
        if handler.path.endswith('/create'):
            with generation(handler.server), handler.server.batch_lock:
                result = create(handler.server, backend.read_config(handler.server.config_path), body, runtime, builder)
        elif handler.path.endswith('/start'): result = start(handler.server, body.get('id'), backend, runtime, builder, servers=body.get('model_servers'))
        elif handler.path.endswith('/servers'): result = configure_servers(handler.server, body.get('id'), body.get('model_servers'))
        elif handler.path.endswith('/stop'): result = stop(handler.server, body.get('id'))
        else: raise ValueError('지원하지 않는 조합 작업 경로입니다.')
        handler.json_response(result)
    except (ValueError, OSError, KeyError, TypeError) as exc: handler.json_response({'error':str(exc)},400)
    return True
