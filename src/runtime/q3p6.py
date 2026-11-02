"""Durable sequential checkpoint experiments; no real calls until explicit start."""
import copy
import hashlib
import json
from pathlib import Path
import re
import secrets
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from client import registry
from client.Q3p6 import public_only
from client.state_summary.runtime import write_new, fingerprint
from client.Q3.tool_loop import run_turn
from client.claim_experiments.runtime import object_schema, validate
from runtime.cancellation import Cancelled
from runtime import paired_lab as pairs
from storage.file_manager import generation
from project_paths import PROJECT_ROOT

PROMPTS = PROJECT_ROOT / 'src/counselor/Q3p6_counsel'
TEXT = {'type': 'string'}
PROPOSAL_SCHEMA = object_schema({'eligible': {'type': 'string', 'enum': ['yes', 'no']},
    'reason': TEXT, 'target_turn': {'type': 'integer', 'minimum': 0}, 'offer_quote': TEXT,
    'acceptance_quote': TEXT, 'I1': TEXT, 'I2': TEXT})

FOLLOWUP_SCHEMA = object_schema({
    'target_turn': {'type': 'integer', 'minimum': 1}, 'target_quote': TEXT,
    'response_kind': {'type': 'string', 'enum': ['acceptance', 'hesitation', 'refusal', 'disclosure', 'other']},
    'reason': TEXT, 'relevance': {'type': 'string', 'enum': ['potential', 'weak']},
    'I1': TEXT, 'I2': TEXT})
FOLLOWUP_V3_SCHEMA = copy.deepcopy(FOLLOWUP_SCHEMA)
FOLLOWUP_V3_SCHEMA['properties']['context_fit'] = {'type': 'string', 'enum': ['natural', 'forced']}
FOLLOWUP_V3_SCHEMA['required'].append('context_fit')
INTENT_TARGET_SCHEMA = object_schema({k: copy.deepcopy(v) for k, v in FOLLOWUP_SCHEMA['properties'].items()
                                      if k not in ('I1', 'I2')})
INTENT_TARGET_SCHEMA['properties']['target_quote']['maxLength'] = 500
DEFAULT_INTENT_TEMPLATE = '앞서 “{target_quote}”라고 말씀하셨는데, 그때 어떤 생각으로 그렇게 말씀하셨나요?'
OPENING = '오늘 상담에서 가장 먼저 다뤄보고 싶은 일이 있나요?'


def intervention_ids(job):
    return ['I1'] if job['options'].get('intervention_mode') == 'single' else ['I1', 'I2']


def fresh_scenario(scenario):
    # Keep only generic task/state definitions and the selected person's profile.
    # In particular, do not carry another case's public history or source metadata.
    if not isinstance(scenario, dict) or not isinstance(scenario.get('profile'), dict):
        raise ValueError('초기 사례와 프로필은 JSON 객체여야 합니다.')
    value = {k: copy.deepcopy(scenario[k]) for k in ('task', 'profile', 'states', 'taxonomy')}
    value.update(id='q3p6_fresh_' + str(value['profile'].get('profile_id', 'custom')),
                 dialogue=[], dialogue_format='messages',
                 source_notes={'case_origin': 'Q3p6 새 상담 · 초기 대화 없음'})
    return value


def live_update(server, jid, **values):
    with server.p6_lock:
        server.p6_live.setdefault(jid, {}).update(copy.deepcopy(values), updated_at=time.time())


def observe_live(server, jid, actor, kind, data):
    # Never publish raw client tool JSON or hidden reasoning as public speech.
    if kind == 'request' and data.get('phase'):
        live_update(server, jid, phase=data['phase'])
    elif kind == 'chunk' and actor == 'counselor':
        text = ''.join(c.get('delta', {}).get('content') or ''
                       for c in data.get('data', {}).get('choices', []) if c.get('index', 0) == 0)
        if text:
            with server.p6_lock:
                old = server.p6_live.get(jid, {}).get('speech', '')
                live_update(server, jid, speech=old + text, speech_status='generating')
    elif kind == 'version_event':
        section, action = data.get('section'), data.get('action')
        if section == 'utterance':
            live_update(server, jid, speech=data.get('data', {}).get('content', ''),
                        speech_status={'fixed': 'pending', 'committed': 'committed', 'discarded': 'discarded'}.get(action, action),
                        phase='노트·궤적 저장' if action == 'fixed' else '발화 ' + str(action))
        else:
            phases = {'trait_router': '특성 선택', 'appraisal': '상황 해석', 'turn_goal': '발화 의도',
                      'response_plan': '표현 계획', 'claim_memory': '기억 입력 구성',
                      'memory_retrieval': '현재 노트 조회', 'trajectory_plan': '궤적 판단'}
            if section in phases: live_update(server, jid, phase=phases[section])
    elif kind == 'tool_start':
        phases = {'update_client_notes': '노트 저장', 'update_trajectory_progress': '궤적 저장',
                  'commit_client_utterance': '발화 확정', 'stage_client_update': '내면 판단 기록'}
        if data.get('name') in phases: live_update(server, jid, phase=phases[data['name']])
    elif kind in ('error', 'cancelled'):
        live_update(server, jid, phase='중단', speech_status='discarded', error=data.get('error', ''))


def initialize(server):
    server.p6_lock = threading.RLock()
    server.p6_workers = {}
    server.p6_live = {}


def job_dir(server, jid):
    if not isinstance(jid, str) or not re.fullmatch(r'p6_[a-f0-9]{16}', jid):
        raise ValueError('올바른 Q3p6 실험 ID가 필요합니다.')
    for base in [str(server.storage_dir), *sorted(server.storage_roots)]:
        p = Path(base) / 'q3p6' / jid
        if (p / 'job.json').is_file():
            return p
    return Path(server.storage_dir) / 'q3p6' / jid


def read(server, jid):
    return json.loads((job_dir(server, jid) / 'job.json').read_text())


def save(server, job):
    write_new(job_dir(server, job['id']) / 'job.json', job)


def settings(body):
    if not isinstance(body, dict):
        raise ValueError('실험 설정은 객체여야 합니다.')
    defaults = {'start_turn': 2, 'end_turn': 20, 'branch_turns': 1,
                'repeats': 5, 'trajectory_repeats': 5, 'input_char_limit': 240000}
    limits = {'start_turn': (1, 100), 'end_turn': (1, 100), 'branch_turns': (1, 5),
              'repeats': (1, 20), 'trajectory_repeats': (1, 20), 'input_char_limit': (1000, 2000000)}
    result = {}
    for key, default in defaults.items():
        value = body.get(key, default)
        lo, hi = limits[key]
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(f'{key}: {lo}~{hi} 정수를 입력하세요.')
        result[key] = value
    if result['end_turn'] < result['start_turn']:
        raise ValueError('마지막 분기 턴은 시작 턴 이상이어야 합니다.')
    mode = body.get('intervention_mode', 'single')
    if mode not in ('single', 'dual'):
        raise ValueError('개입 수는 single 또는 dual이어야 합니다.')
    result['intervention_mode'] = mode
    include_c = body.get('include_c', True)
    policy = body.get('branch_policy', 'natural')
    if type(include_c) is not bool or policy not in ('natural', 'all'):
        raise ValueError('C 포함 여부와 분기 방식을 확인하세요.')
    probe_mode = body.get('probe_mode', 'contextual')
    if probe_mode not in ('contextual', 'past_intent'):
        raise ValueError('분기 질문 모드를 확인하세요.')
    template = body.get('intent_probe_template', DEFAULT_INTENT_TEMPLATE)
    if not isinstance(template, str) or not template.strip() or len(template) > 600 or template.count('{target_quote}') != 1 or any(c in template.replace('{target_quote}', '') for c in '{}'):
        raise ValueError('분기 질문에는 {target_quote}를 정확히 한 번 넣고 다른 중괄호는 사용하지 마세요. 최대 600자입니다.')
    if probe_mode == 'past_intent':
        if result['start_turn'] < 2:
            raise ValueError('당시 의도 확인 모드는 과거 발화를 묻기 위해 2턴 이후부터 분기합니다.')
        # Diagnostic probes deliberately revisit history at every checkpoint.
        result['intervention_mode'], policy = 'single', 'all'
    result.update(include_c=include_c, branch_policy=policy, probe_mode=probe_mode,
                  intent_probe_template=template.strip())
    return result


def source_snapshot():
    files = []
    for folder in ('src/client/Q3p6', 'src/client/Q3', 'src/counselor/Q3p6_counsel'):
        files.extend(p for p in (PROJECT_ROOT / folder).iterdir()
                     if p.suffix in ('.py', '.md', '.json') and p.name not in ('README.md', 'DESIGN_DISCUSSION.md'))
    files.extend(PROJECT_ROOT / p for p in ('src/runtime/q3p6.py', 'src/runtime/paired_lab.py',
                                           'src/runtime/user_templates.py'))
    return {str(p.relative_to(PROJECT_ROOT)): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                                              **({'content': p.read_text()} if p.suffix == '.md' else {})}
            for p in files}


def assert_sources(job):
    for name, expected in job['sources'].items():
        if hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest() != expected['sha256']:
            raise ValueError('실험 생성 후 실행 소스가 변경되었습니다: ' + name + '. 원본 복원 또는 새 실험이 필요합니다.')


def create(server, config, body, runtime, builder):
    opts = settings(body.get('options', {}))
    request = copy.deepcopy(body.get('request', {}))
    if not isinstance(request, dict):
        raise ValueError('내담자 설정은 객체여야 합니다.')
    request.update(experiment_version='Q3p6', claim_memory_condition='summary_only',
                   counselor_version='Q3p6_counsel', speaker_modes={'client': 'model', 'counselor': 'model'})
    request.setdefault('system_prompt', registry.client_runtime('Q3p6').specification()['system_prompt'])
    request.setdefault('counselor_system_prompt', (PROMPTS / 'system.md').read_text())
    request.setdefault('generation', copy.deepcopy(config['generation']))
    request.setdefault('scenario', copy.deepcopy(config['scenario']))
    case_mode = body.get('case_mode', 'fresh')
    if case_mode not in ('fresh', 'continuation'):
        raise ValueError('새 상담 또는 직접 작성한 이어가기 사례를 선택하세요.')
    if case_mode == 'fresh':
        request['scenario'] = fresh_scenario(request['scenario'])
        request.setdefault('messages', [{'role': 'user', 'content': OPENING}])
    else:
        request['scenario'] = runtime.validate_scenario(request['scenario'])
        history = request['scenario']['dialogue']
        if request['scenario'].get('dialogue_format', 'pairs') == 'pairs':
            history = [{'role': role, 'content': row[role]} for row in history for role in ('counselor', 'client')]
        opening = request.get('messages', [{}])[-1].get('content', '').strip()
        if not history or history[-1]['role'] != 'client' or not opening or opening in (OPENING, '오늘 어떤 고민을 이야기하고 싶으신가요?'):
            raise ValueError('이어가기는 내담자 발화로 끝나는 초기 대화와 맥락에 맞는 다음 상담사 발화가 필요합니다.')
        for key in ('dialogue_turns', 'selected_counselor_utterance', 'dialogue_options'):
            request['scenario'].pop(key, None)
        request['scenario'].update(dialogue=history, dialogue_format='messages')
    request.pop('session_id', None)
    request.pop('session_key', None)
    request['input_blocks'] = {key: True for key in registry.OPTIONAL_BLOCKS}
    common = pairs.create(server, config, request, runtime, builder)
    jid = 'p6_' + uuid.uuid4().hex[:16]
    common['q3p6_managed'] = jid
    pairs.save_session(server, common)
    job = {'id': jid, 'revision': 5, 'case_mode': case_mode, 'status': 'ready', 'options': opts, 'common_session_id': common['session_id'],
           'common_turn': 0, 'checkpoints': [], 'error': '', 'sources': source_snapshot(),
           'initial_request': copy.deepcopy(request), 'blind_salt': uuid.uuid4().hex,
           'trajectories': ([{'condition': mode, 'repeat': r, 'session_id': common['session_id'] if mode == 'A' and r == 1 else None,
                              'turns': 0, 'status': 'waiting'} for r in range(1, opts['trajectory_repeats'] + 1) for mode in ('A', 'C')]
                            if opts['include_c'] else []),
           'base_seed': request['generation'].get('seed') if type(request['generation'].get('seed')) is int else secrets.randbelow(1000000000),
           'frozen_config': {key: copy.deepcopy(value) for key, value in config.items()
                             if key in ('delegation', 'timeout_seconds', 'inspect_prompt', 'inspection_timeout_seconds', 'system_role_mode')},
           'proposal_prompt': (PROMPTS / ('intent_target.md' if opts['probe_mode'] == 'past_intent' else 'interventions.md')).read_text()}
    save(server, job)
    return public_job(server, job)


def public_job(server, job):
    value = copy.deepcopy(job)
    value.pop('sources', None)
    value.pop('frozen_config', None)
    value.pop('initial_request', None)
    value.pop('blind_salt', None)
    for cp in value['checkpoints'] + [c for r in value.get('trajectories', []) for c in r.get('probes', [])]:
        cp.pop('snapshot', None)
    with server.p6_lock:
        active = server.p6_workers.get(job['id'])
        alive = bool(active and active['thread'].is_alive())
    if value['status'] == 'running' and not alive:
        # A read can race with the worker's final save and exit. Refresh the
        # terminal snapshot before labelling it as an interrupted process.
        latest = read(server, job['id'])
        if latest['status'] != 'running':
            return public_job(server, latest)
        value['status'] = 'interrupted'
    value['running'] = alive
    n = job['options']['end_turn'] - job['options']['start_turn'] + 1
    value['planned_branch_responses'] = n * 2 * len(intervention_ids(job)) * job['options']['repeats'] * job['options']['branch_turns']
    with server.p6_lock:
        value['live'] = copy.deepcopy(server.p6_live.get(job['id'], {})) if alive else {}
    session = pairs.read_session(server, job['common_session_id'])
    value['common_dialogue'] = session['public_dialogue']
    value['completed_branch_responses'] = sum(len(r.get('responses', [])) for c in job['checkpoints'] for r in c['branches'])
    value['planned_trajectory_responses'] = len(job.get('trajectories', [])) * job['options']['end_turn']
    for row in value.get('trajectories', []):
        if row['session_id'] == job['common_session_id']:
            row['turns'] = client_turns(session)
            row['status'] = 'completed' if row['turns'] >= job['options']['end_turn'] else ('running' if alive else job['status'])
    value['completed_trajectory_responses'] = sum(r['turns'] for r in value.get('trajectories', []))
    extra_probes = [c for r in job.get('trajectories', []) for c in r.get('probes', [])]
    value['planned_trajectory_probe_responses'] = ((len(job.get('trajectories', [])) - 1) * n * job['options']['repeats'] * job['options']['branch_turns'] if trajectory_probes_enabled(job) else 0)
    value['completed_trajectory_probe_responses'] = sum(len(b.get('responses', [])) for c in extra_probes for b in c['branches'])
    value['reviewed_candidates'] = sum(c.get('review', {}).get('status') == 'candidate' for c in job['checkpoints'])
    return value


def config_for(base, job, session):
    cfg = copy.deepcopy(base)
    cfg.update(copy.deepcopy(job['frozen_config']))
    cfg.update(copy.deepcopy(session['connection']))
    cfg['generation'] = copy.deepcopy(session['settings']['generation'])
    cfg['_p6_control'] = {'input_char_limit': job['options']['input_char_limit'], 'job_id': job['id']}
    return cfg


def client_turns(session):
    return sum(s['actor'] == 'client' for s in session['steps'])


def step(server, base, job, session, backend, runtime, builder, cancelled, router_cache=None, seed=None):
    """Same commit boundary as paired_lab, but the experiment owns this session."""
    if cancelled():
        raise Cancelled('Q3p6 생성이 중단되었습니다.')
    cfg = config_for(base, job, session)
    if router_cache:
        cfg['_p6_control']['router_cache'] = str(router_cache)
    if seed is not None:
        cfg['generation']['seed'] = seed
    # client_step restores settings into its body, so set the paired generation
    # too; this is a fixed per-repetition seed, identical for A and B.
    session['settings']['generation'] = copy.deepcopy(cfg['generation'])
    actor = session['next_actor']
    parent = session.get('q3p6_parent', {})
    advancing = any(c['turn'] == client_turns(session) and c['status'] in ('completed', 'skipped') for c in job['checkpoints'])
    path_info = session.get('q3p6_path', {})
    live_update(server, job['id'], stage='trajectory_probe' if session.get('q3p6_probe') else ('trajectory' if path_info else ('branch' if parent else ('advance' if advancing else 'common'))), actor=actor,
                session_id=session['session_id'], turn=parent.get('turn', client_turns(session) + 1),
                condition=path_info.get('condition', parent.get('condition')), intervention=parent.get('intervention'), repeat=parent.get('repeat', path_info.get('repeat')), path_repeat=path_info.get('repeat'),
                phase='모델 응답 대기', speech='', speech_status='waiting', error='')
    log = backend.PairedTurnLog(pairs.root(server, session['session_id']).parent.parent / 'paired/logs',
        {'session_id': session['session_id'], 'actor': actor, 'experiment_version': 'Q3p6',
         'step': len(session['steps']) + 1, 'q3p6_job': job['id']})
    attempt = log.state['turn_id']
    session['attempts'].append({'turn_id': attempt, 'actor': actor,
                               'step': len(session['steps']) + 1, 'saved_path': str(log.path)})
    pairs.save_session(server, session)
    def emit(kind, **data):
        log.append({'type': kind, 'actor': actor, **data})
        observe_live(server, job['id'], actor, kind, data)
    try:
        emit('step_start', session_id=session['session_id'], turn_id=attempt)
        if actor == 'client' and path_info.get('condition') == 'C':
            destination = pairs.root(server, session['session_id']) / 'public_attempts' / attempt / 'state.json'
            utterance, usage, storage = public_only.generate(cfg, session, backend, runtime, builder, emit, cancelled, destination)
            session['public_state_path'] = storage
        elif actor == 'client':
            utterance, usage, storage = pairs.client_step(server, cfg, session, backend, runtime, builder,
                                                        emit, cancelled, attempt)
            session['client_storage_dir'] = storage
        else:
            utterance, checkpoint, usage = pairs.counselor_step(cfg, session, backend, builder, emit,
                                                               cancelled, pairs.root(server, session['session_id']))
            session['counselor_checkpoint'] = checkpoint
        # client_step has already committed its isolated state. Publish the
        # completed speech even if cancellation arrives just after that commit.
        entry = {'number': len(session['steps']) + 1, 'actor': actor, 'utterance': utterance,
                 'turn_id': attempt, 'usage': usage, 'saved_path': str(log.path)}
        session['steps'].append(entry)
        session['public_dialogue'].append({'role': actor, 'content': utterance})
        session['next_actor'] = 'counselor' if actor == 'client' else 'client'
        pairs.save_session(server, session)
        live_update(server, job['id'], speech=utterance, speech_status='committed', phase='발화 저장 완료')
        emit('done', response=utterance, usage=usage, session=session)
    except Exception as error:
        if not log.terminal:
            emit('cancelled' if isinstance(error, Cancelled) else 'error', error=str(error))
        raise
    finally:
        log.close()
    return session


def validate_proposal(value, public, candidates):
    validate(value, PROPOSAL_SCHEMA)
    if not value['reason'].strip():
        raise ValueError('개입 적합성의 공개 근거가 없습니다.')
    if value['eligible'] == 'no':
        if value['target_turn'] != 0 or any(value[k] for k in ('offer_quote', 'acceptance_quote', 'I1', 'I2')):
            raise ValueError('미수행 개입의 대상과 발화는 비워야 합니다.')
        return value
    target = next((r for r in candidates if r['turn'] == value['target_turn']), None)
    if target is None or not value['acceptance_quote'] or value['acceptance_quote'] not in target['utterance']:
        raise ValueError('개입 대상 수락 인용이 실제 내담자 발화와 다릅니다.')
    # Only counselor speech at/before the target may justify the offer.
    before = public[:target['public_index']]
    if not value['offer_quote'] or not any(value['offer_quote'] in r['content']
                                          for r in before if r['role'] == 'counselor'):
        raise ValueError('제안 인용이 대상 수락 이전 공개 대화에 없습니다.')
    if value['I1'] == value['I2'] or any(not value[k].strip() or len(value[k]) > 1200 for k in ('I1', 'I2')):
        raise ValueError('서로 다른 두 개입 문장이 필요합니다.')
    return value


def validate_followup(value, candidates, dual, revision=2):
    validate(value, FOLLOWUP_V3_SCHEMA if revision >= 3 else FOLLOWUP_SCHEMA)
    target = next((r for r in candidates if r['turn'] == value['target_turn']), None)
    if target is None or not value['target_quote'].strip() or value['target_quote'] not in target['utterance']:
        raise ValueError('후속 질문 대상 인용이 실제 내담자 발화와 다릅니다.')
    if not value['reason'].strip() or not value['I1'].strip() or len(value['I1']) > 1200:
        raise ValueError('대상 선택 이유와 후속 질문이 필요합니다.')
    if dual:
        if not value['I2'].strip() or len(value['I2']) > 1200 or value['I1'] == value['I2']:
            raise ValueError('두 개입 모드에는 서로 다른 후속 질문이 필요합니다.')
    elif value['I2']:
        raise ValueError('단일 개입 모드에서는 I2를 비워야 합니다.')
    return value


def target_evidence(snapshot, proposal):
    rows = registry.client_runtime('Q3p6').load(
        {'_storage_dir': snapshot['client_storage_dir']}, snapshot['session_id'])['records']
    row = next((r for r in rows if r['turn'] == proposal['target_turn']), {})
    # Inspection only, collected AFTER public-only intervention generation.
    fields = ('turn', 'turn_id', 'utterance', 'counselor_utterance', 'appraisal', 'turn_goal', 'response_plan', 'trajectory_plan')
    return {key: copy.deepcopy(row.get(key)) for key in fields}


def propose(server, base, job, snapshot, backend, builder, path, cancelled):
    """Public data only. Cache the exact two sentences before any A/B call."""
    if path.exists():
        return json.loads(path.read_text())
    cfg = config_for(base, job, snapshot)
    candidates = []
    # Stored Q3 turns map to generated paired steps, not to any initial examples.
    for i, item in enumerate(snapshot['steps']):
        if item['actor'] == 'client':
            candidates.append({'turn': len(candidates) + 1, 'utterance': item['utterance'],
                               'public_index': snapshot['initial_public_count'] + i})
    intent_probe = job['options'].get('probe_mode') == 'past_intent'
    if intent_probe:
        # Exclude the latest utterance: this probe asks about a prior turn.
        candidates = candidates[:-1]
        if not candidates:
            raise ValueError('당시 의도 확인에 필요한 과거 내담자 발화가 없습니다.')
    source = {'public_dialogue': snapshot['public_dialogue'], 'candidates': candidates}
    modern = job.get('revision', 1) >= 2
    if modern:
        source['intervention_mode'] = job['options']['intervention_mode']
    path_info = snapshot.get('q3p6_path', {})
    live_update(server, job['id'], stage='trajectory_probe' if path_info else 'proposal', turn=client_turns(snapshot), actor='counselor',
                condition=path_info.get('condition'), intervention=None, repeat=None, path_repeat=path_info.get('repeat'), phase='공개 이력으로 대상·후속 질문 준비',
                speech='', speech_status='waiting', error='')
    request = builder(cfg, {'system_prompt': job['proposal_prompt'],
                          'messages': [{'role': 'user', 'content': json.dumps(source, ensure_ascii=False)}]}, [], False)
    request['structured_outputs'] = {'json': INTENT_TARGET_SCHEMA if intent_probe else (FOLLOWUP_V3_SCHEMA if job.get('revision', 1) >= 3 else (FOLLOWUP_SCHEMA if modern else PROPOSAL_SCHEMA))}
    result = {}
    raw = path.with_name('proposal_' + uuid.uuid4().hex + '.events.jsonl')
    with raw.open('w', encoding='utf-8') as f:
        def emit(kind, **data):
            if kind == 'done':
                result.update(data)
            f.write(json.dumps({'type': kind, **data}, ensure_ascii=False) + '\n'); f.flush()
        emit('request', payload=request)
        try:
            run_turn(cfg, request, None, backend.upstream, backend.stream_events, backend.inspect_prompt,
                     emit, cancelled, [])
            if result.get('finish_reason') != 'stop':
                raise ValueError('개입 준비 응답이 정상 완료되지 않았습니다.')
            from client.Q1.runtime import parse_json
            value = parse_json(result['response'])
            if intent_probe:
                validate(value, INTENT_TARGET_SCHEMA)
                # Render once, from a verified public quote; no hidden intent enters the question.
                value.update(I1=job['options']['intent_probe_template'].replace('{target_quote}', value['target_quote']), I2='')
                proposal = validate_followup(value, candidates, False, revision=2)
                proposal['context_fit'] = 'diagnostic'
            else:
                proposal = (validate_followup(value, candidates, len(intervention_ids(job)) == 2, job.get('revision', 1))
                            if modern else validate_proposal(value, source['public_dialogue'], candidates))
        except Exception as error:
            emit('error', error=str(error)); raise
    stored = {'proposal': proposal, 'request': request, 'response': result['response'],
              'usage': result.get('usage', {}), 'events': str(raw),
              'validation': 'schema_and_quotes_checked; semantic_adherence_requires_review'}
    write_new(path, stored)
    return stored


def checkpoint_hash(snapshot):
    if snapshot.get('q3p6_path', {}).get('condition') == 'C':
        return fingerprint({'snapshot': snapshot, 'public_state': hashlib.sha256(Path(snapshot['public_state_path']).read_bytes()).hexdigest()})
    module = registry.client_runtime('Q3p6')
    folder = module.directory({'_storage_dir': snapshot['client_storage_dir']}, snapshot['session_id'])
    files = [folder / 'state_notes.md', *sorted((folder / 'appraisals').glob('*.json'))]
    return fingerprint({'snapshot': snapshot,
                        'files': {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})


def assert_checkpoint(cp):
    if checkpoint_hash(cp['snapshot']) != cp['state_hash']:
        raise ValueError('저장한 분기 시점의 원본 상태가 변경되었습니다. 기존 결과와 섞어 재개할 수 없습니다.')


def fork(server, job, cp, intervention, mode, repeat):
    """Copy only checkpoint state; do not mutate or rewrite inherited records."""
    assert_checkpoint(cp)
    parent = cp['snapshot']
    sid = pairs.new_session_id(server)
    child = copy.deepcopy(parent)
    child.update(session_id=sid, attempts=[], q3p6_managed=job['id'],
                 q3p6_parent={'session_id': parent['session_id'], 'turn': cp['turn'],
                              'intervention': intervention, 'condition': mode, 'repeat': repeat})
    child['settings']['claim_memory_condition'] = 'summary_only' if mode == 'public_only' else mode
    child['settings'].pop('session_id', None)
    import shutil
    if mode == 'public_only':
        dest = pairs.root(server, sid) / 'seed' / 'public_state.json'
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(parent['public_state_path'], dest)
        child['public_state_path'] = str(dest)
    else:
        module = registry.client_runtime('Q3p6')
        original = module.directory({'_storage_dir': parent['client_storage_dir']}, parent['session_id'])
        dest = pairs.root(server, sid) / 'seed' / 'Q3p6' / sid
        shutil.copytree(original, dest)
        child['client_storage_dir'] = str(dest.parent.parent)
    if parent.get('q3p6_path'):
        child['q3p6_probe'] = True
    text = cp['proposal']['proposal'][intervention]
    child['public_dialogue'].append({'role': 'counselor', 'content': text})
    child['steps'].append({'number': len(child['steps']) + 1, 'actor': 'counselor', 'utterance': text,
                           'source': 'fixed_intervention', 'turn_id': 'intervention_' + uuid.uuid4().hex})
    child['next_actor'] = 'client'
    pairs.save_session(server, child)
    return sid


def trajectory_probes_enabled(job):
    return (job.get('revision', 1) >= 5 and job['options'].get('include_c', False)
            and job['options'].get('probe_mode') == 'past_intent')


def advance_trajectory_probe(server, base, job, path_row, session, backend, runtime, builder, flags):
    """Probe a copy of each independent path; never feed its answer back."""
    n = client_turns(session)
    if (not trajectory_probes_enabled(job) or session['session_id'] == job['common_session_id']
            or session['next_actor'] != 'counselor' or n < job['options']['start_turn']):
        return False
    probes = path_row.setdefault('probes', [])
    cp = next((c for c in probes if c['turn'] == n), None)
    if cp is None:
        cp = {'turn': n, 'snapshot': copy.deepcopy(session), 'state_hash': checkpoint_hash(session),
              'status': 'preparing', 'branches': []}
        probes.append(cp); save(server, job)
    if cp['status'] == 'completed': return False
    path_row['status'] = 'running'
    assert_checkpoint(cp)
    folder = job_dir(server, job['id']) / f"trajectory_{path_row['condition']}_{path_row['repeat']}" / f'checkpoint_{n}'
    folder.mkdir(parents=True, exist_ok=True)
    job['activity'] = f"독립 {path_row['condition']} · 경로 반복 {path_row['repeat']} · {n}턴 재질문"
    if 'proposal' not in cp:
        cp['proposal'] = propose(server, base, job, cp['snapshot'], backend, builder,
                                  folder / 'proposal.json', flags['stop'].is_set)
        cp['target_lag'] = n - cp['proposal']['proposal']['target_turn']
        if path_row['condition'] == 'A':
            cp['target_evidence'] = target_evidence(cp['snapshot'], cp['proposal']['proposal'])
        cp['status'] = 'running'; save(server, job); return True
    if not cp['branches']:
        mode = 'public_only' if path_row['condition'] == 'C' else 'summary_only'
        cp['branches'] = [{'intervention': 'I1', 'condition': mode, 'repeat': r,
                           'session_id': None, 'status': 'waiting', 'responses': []}
                          for r in range(1, job['options']['repeats'] + 1)]
        save(server, job)
    row = next((r for r in cp['branches'] if r['status'] != 'completed'), None)
    if row is None:
        cp['status'] = 'completed'; save(server, job); return True
    if row['session_id'] is None:
        row['session_id'] = fork(server, job, cp, 'I1', row['condition'], row['repeat'])
        save(server, job)
    branch = pairs.read_session(server, row['session_id'])
    count = client_turns(branch) - n
    if count < job['options']['branch_turns']:
        row['status'] = 'running'; save(server, job)
        branch = step(server, base, job, branch, backend, runtime, builder, flags['stop'].is_set,
                      seed=(job['base_seed'] + (path_row['repeat'] - 1) * 1000000 + n * 10000 + row['repeat'] * 100 + count) % 2147483647)
    row['responses'] = [s['utterance'] for s in branch['steps'][len(cp['snapshot']['steps']):] if s['actor'] == 'client']
    if len(row['responses']) >= job['options']['branch_turns']: row['status'] = 'completed'
    if all(r['status'] == 'completed' for r in cp['branches']): cp['status'] = 'completed'
    save(server, job)
    return True


def advance_trajectory(server, base, job, backend, runtime, builder, flags):
    """A/C start from the same initial case, never from an A/B checkpoint."""
    for row in job.get('trajectories', []):
        if row['status'] == 'completed': continue
        if row['session_id'] is None:
            cfg = copy.deepcopy(base); cfg.update(copy.deepcopy(job['frozen_config']))
            request = copy.deepcopy(job['initial_request'])
            if row['condition'] == 'C':
                request['system_prompt'] = (public_only.HERE/'public_only.md').read_text()
            session = pairs.create(server, cfg, request, runtime, builder)
            session.update(q3p6_managed=job['id'], q3p6_path={'condition': row['condition'], 'repeat': row['repeat']})
            pairs.save_session(server, session)
            row['session_id'] = session['session_id']; save(server, job)
        session = pairs.read_session(server, row['session_id'])
        row['turns'] = client_turns(session)
        if advance_trajectory_probe(server, base, job, row, session, backend, runtime, builder, flags):
            return True
        if row['turns'] >= job['options']['end_turn']:
            row['status'] = 'completed'; save(server, job); continue
        row['status'] = 'running'
        job['activity'] = f"독립 {row['condition']} 경로 · 반복 {row['repeat']} · {row['turns'] + 1}턴 · {session['next_actor']}"
        save(server, job)
        seed = (job['base_seed'] + (row['repeat'] - 1) * 1000000 + row['turns']) % 2147483647
        session = step(server, base, job, session, backend, runtime, builder, flags['stop'].is_set, seed=seed)
        row['turns'] = client_turns(session)
        if row['turns'] >= job['options']['end_turn'] and not trajectory_probes_enabled(job): row['status'] = 'completed'
        save(server, job)
        return True
    return False


def work(server, jid, backend, runtime, builder, flags):
    job = read(server, jid)
    try:
        with generation(server):
            base = backend.read_config(server.config_path)
            while True:
                if flags['stop'].is_set():
                    raise Cancelled('사용자가 실행을 중단했습니다.')
                if flags['pause'].is_set():
                    job['status'] = 'paused'; break
                assert_sources(job)
                common = pairs.read_session(server, job['common_session_id'])
                n = client_turns(common)
                job['common_turn'] = n
                cp = next((r for r in job['checkpoints'] if r['turn'] == n), None)
                if n >= job['options']['start_turn'] and cp is None:
                    cp = {'turn': n, 'snapshot': copy.deepcopy(common), 'status': 'preparing', 'branches': [],
                          'state_hash': checkpoint_hash(common), 'review': {'status': 'unreviewed', 'note': ''}}
                    job['checkpoints'].append(cp); save(server, job)
                if cp is not None and cp['status'] not in ('completed', 'skipped'):
                    assert_checkpoint(cp)
                    folder = job_dir(server, jid) / ('checkpoint_' + str(n))
                    folder.mkdir(parents=True, exist_ok=True)
                    if 'proposal' not in cp:
                        cp['proposal'] = propose(server, base, job, cp['snapshot'], backend, builder,
                                                  folder / 'proposal.json', flags['stop'].is_set)
                        proposal = cp['proposal']['proposal']
                        cp['status'] = 'running' if job.get('revision', 1) >= 2 or proposal['eligible'] == 'yes' else 'skipped'
                        if job.get('revision', 1) >= 3 and job['options']['branch_policy'] == 'natural' and proposal['context_fit'] == 'forced':
                            cp['status'] = 'skipped'
                            cp['skip_reason'] = '과거 주제 재연결이 현재 흐름에서 부자연스러움: ' + proposal['reason']
                        if job.get('revision', 1) >= 2:
                            cp['target_evidence'] = target_evidence(cp['snapshot'], proposal)
                            cp['target_lag'] = n - proposal['target_turn']
                        save(server, job); continue
                    if not cp['branches']:
                        cp['branches'] = [{'intervention': i, 'condition': m, 'repeat': r,
                                           'session_id': None, 'status': 'waiting', 'responses': []}
                                          for i in intervention_ids(job) for r in range(1, job['options']['repeats'] + 1)
                                          for m in ('summary_only', 'intent_linked')]
                        save(server, job)
                    row = next((r for r in cp['branches'] if r['status'] != 'completed'), None)
                    if row is None:
                        cp['status'] = 'completed'; save(server, job); continue
                    if row['session_id'] is None:
                        row['session_id'] = fork(server, job, cp, row['intervention'], row['condition'], row['repeat'])
                        save(server, job)
                    branch = pairs.read_session(server, row['session_id'])
                    count = client_turns(branch) - n
                    row['responses'] = [s['utterance'] for s in branch['steps'][len(cp['snapshot']['steps']):]
                                        if s['actor'] == 'client']
                    if count >= job['options']['branch_turns']:
                        row['status'] = 'completed'; save(server, job); continue
                    row['status'] = 'running'
                    job['activity'] = f"공통 {n}턴 뒤 · {row['intervention']} · {row['condition']} · 반복 {row['repeat']} · 추가 {count + 1}턴"
                    save(server, job)
                    branch = step(server, base, job, branch, backend, runtime, builder, flags['stop'].is_set,
                                  router_cache=folder / (row['intervention'] + '_router.json') if count == 0 else None,
                                  seed=(job['base_seed'] + n * 10000 + row['repeat'] * 100 + count) % 2147483647)
                    # Publish committed responses before honoring a pause/stop on
                    # the next iteration, so the UI never hides a finished turn.
                    row['responses'] = [s['utterance'] for s in branch['steps'][len(cp['snapshot']['steps']):]
                                        if s['actor'] == 'client']
                    if len(row['responses']) >= job['options']['branch_turns']:
                        row['status'] = 'completed'
                    if all(r['status'] == 'completed' for r in cp['branches']):
                        cp['status'] = 'completed'
                    save(server, job)
                    continue
                if n >= job['options']['end_turn']:
                    if advance_trajectory(server, base, job, backend, runtime, builder, flags):
                        continue
                    job['status'] = 'completed'; break
                job['activity'] = '공통 경로 ' + str(n + 1) + '턴 · ' + common['next_actor']
                save(server, job)
                common = step(server, base, job, common, backend, runtime, builder, flags['stop'].is_set,
                              seed=(job['base_seed'] + n) % 2147483647)
                job['common_turn'] = client_turns(common)
                save(server, job)
    except Exception as error:
        job['status'] = 'stopped' if isinstance(error, Cancelled) else 'error'
        job['error'] = str(error)
    finally:
        save(server, job)


def start(server, jid, backend, runtime, builder):
    with server.p6_lock:
        old = server.p6_workers.get(jid)
        if old and old['thread'].is_alive():
            raise ValueError('이미 실행 중인 Q3p6 실험입니다.')
        job = read(server, jid)
        if job['status'] == 'completed':
            raise ValueError('이미 완료된 실험입니다.')
        assert_sources(job)
        server.p6_live[jid] = {}
        flags = {'pause': threading.Event(), 'stop': threading.Event()}
        thread = threading.Thread(target=work, args=(server, jid, backend, runtime, builder, flags), daemon=True)
        server.p6_workers[jid] = {**flags, 'thread': thread}
        job.update(status='running', error=''); save(server, job)
        thread.start()
    return public_job(server, job)


def detail(server, job, sid):
    allowed = {job['common_session_id']} | {r['session_id'] for c in job['checkpoints'] for r in c['branches']} | {r['session_id'] for r in job.get('trajectories', [])}
    allowed |= {b['session_id'] for r in job.get('trajectories', []) for c in r.get('probes', []) for b in c['branches']}
    if sid not in allowed:
        raise ValueError('이 실험의 세션이 아닙니다.')
    session = pairs.read_session(server, sid)
    records = []
    if session.get('q3p6_path', {}).get('condition') == 'C':
        records = public_only.load(session)['records']
    elif session.get('client_storage_dir'):
        records = registry.client_runtime('Q3p6').load({'_storage_dir': session['client_storage_dir']}, sid)['records']
    return {'session': session, 'records': records}


def export_data(server, job, blind=False):
    if public_job(server, job)['running']:
        raise ValueError('내보내기는 일시정지 또는 완료 후 실행하세요.')
    units, key = [], []
    def add(sid, meta):
        if not sid: return
        data = detail(server, job, sid)
        uid = hashlib.sha256((job.get('blind_salt', job['id']) + sid).encode()).hexdigest()[:16]
        key.append({'unit_id': uid, 'session_id': sid, **meta})
        if blind:
            units.append({'unit_id': uid, 'public_dialogue': data['session']['public_dialogue']})
        else:
            units.append({'unit_id': uid, **meta, **data})
    add(job['common_session_id'], {'comparison': 'common', 'condition': 'A', 'repeat': 1})
    for cp in job['checkpoints']:
        for row in cp['branches']:
            add(row['session_id'], {'comparison': 'same_checkpoint', 'checkpoint_turn': cp['turn'],
                                   'condition': 'A' if row['condition'] == 'summary_only' else 'B',
                                   'repeat': row['repeat'], 'intervention': row['intervention']})
    for row in job.get('trajectories', []):
        if row['session_id'] == job['common_session_id']: continue
        add(row['session_id'], {'comparison': 'independent_trajectory', 'condition': row['condition'], 'repeat': row['repeat']})
    for path_row in job.get('trajectories', []):
        for cp in path_row.get('probes', []):
            for row in cp['branches']:
                add(row['session_id'], {'comparison': 'independent_trajectory_probe',
                    'condition': path_row['condition'], 'path_repeat': path_row['repeat'],
                    'checkpoint_turn': cp['turn'], 'repeat': row['repeat'], 'intervention': 'I1',
                    'target_turn': cp['proposal']['proposal']['target_turn']})
    units.sort(key=lambda u: u['unit_id'])
    if blind:
        return {'format': 'q3p6_public_blind_v1', 'units': units,
                'instructions': '공개 대화만으로 진전 해석을 평가하세요. 내면 원문이나 조건은 제공하지 않습니다.'}
    return {'format': 'q3p6_research_v1', 'job': job, 'units': units, 'blind_key': key}


def handle_get(handler):
    parts = urlsplit(handler.path)
    if not parts.path.startswith('/api/q3p6/'):
        return False
    try:
        params = parse_qs(parts.query)
        if parts.path == '/api/q3p6/options':
            result = {'models': list(registry.MODEL_CHOICES), 'intervention_prompt': (PROMPTS / 'interventions.md').read_text(), 'intent_target_prompt': (PROMPTS / 'intent_target.md').read_text(), 'intent_probe_template': DEFAULT_INTENT_TEMPLATE, 'opening': OPENING, 'research_profile': json.loads((public_only.HERE/'research_case.json').read_text())}
        elif parts.path == '/api/q3p6/list':
            jobs = {}
            for base in handler.server.storage_roots:
                for p in (Path(base) / 'q3p6').glob('p6_*/job.json'):
                    j = json.loads(p.read_text()); jobs[j['id']] = public_job(handler.server, j)
            result = {'jobs': sorted(jobs.values(), key=lambda j: j['id'])}
        else:
            job = read(handler.server, params.get('id', [''])[0])
            if parts.path == '/api/q3p6/job':
                result = public_job(handler.server, job)
            elif parts.path == '/api/q3p6/export':
                result = export_data(handler.server, job, params.get('blind', ['0'])[0] == '1')
            elif parts.path == '/api/q3p6/detail':
                result = detail(handler.server, job, params.get('session_id', [''])[0])
            else:
                raise ValueError('지원하지 않는 Q3p6 경로입니다.')
        handler.json_response(result)
    except (ValueError, OSError, KeyError) as error:
        handler.json_response({'error': str(error)}, 400)
    return True


def handle_post(handler, backend, runtime, builder):
    if not handler.path.startswith('/api/q3p6/'):
        return False
    try:
        if handler.headers.get('X-Gemma-Request') != '1':
            raise ValueError('요청 헤더가 없습니다.')
        size = int(handler.headers.get('Content-Length', 0))
        if not 0 < size <= 4 * 1024 * 1024:
            raise ValueError('요청 크기를 확인하세요.')
        body = json.loads(handler.rfile.read(size))
        if not isinstance(body, dict):
            raise ValueError('요청은 JSON 객체여야 합니다.')
        if handler.path == '/api/q3p6/create':
            with generation(handler.server):
                result = create(handler.server, backend.read_config(handler.server.config_path), body, runtime, builder)
        elif handler.path == '/api/q3p6/review':
            with handler.server.p6_lock:
                jid = body.get('id'); job = read(handler.server, jid)
                worker = handler.server.p6_workers.get(jid)
                if worker and worker['thread'].is_alive():
                    raise ValueError('검토 기록은 일시정지 후 저장하세요.')
                status = body.get('status'); note = body.get('note', '')
                if status not in ('unreviewed', 'candidate', 'unsuitable') or not isinstance(note, str) or len(note) > 5000:
                    raise ValueError('검토 상태와 메모를 확인하세요.')
                cp = next((c for c in job['checkpoints'] if c['turn'] == body.get('turn')), None)
                if cp is None: raise ValueError('검토할 분기 시점이 없습니다.')
                cp['review'] = {'status': status, 'note': note}
                save(handler.server, job); result = public_job(handler.server, job)
        elif handler.path == '/api/q3p6/start':
            result = start(handler.server, body.get('id'), backend, runtime, builder)
        elif handler.path in ('/api/q3p6/pause', '/api/q3p6/stop'):
            with handler.server.p6_lock:
                flags = handler.server.p6_workers.get(body.get('id'))
                if flags and flags['thread'].is_alive():
                    flags['pause' if handler.path.endswith('/pause') else 'stop'].set()
            result = public_job(handler.server, read(handler.server, body.get('id')))
        else:
            raise ValueError('지원하지 않는 Q3p6 경로입니다.')
        handler.json_response(result)
    except (ValueError, OSError, KeyError, TypeError) as error:
        handler.json_response({'error': str(error)}, 400)
    return True
