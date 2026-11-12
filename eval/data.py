"""Read-only adapter for completed compare-fork batches. No simulator imports."""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def discover(root, counselors=(), clients=(), batches=(), storage_roots=None):
    """Latest by creation time, per counselor/client pair, never by file mtime.

    Only completed compare batches with all rows completed are eligible. Other
    layouts are listed as excluded; they are not silently treated as compare.
    """
    candidates, excluded = defaultdict(list), []
    found_ids = set()
    paths = {p.resolve() for storage in (storage_roots or [root / 'runs'])
             for p in (Path(storage) / 'profile_batches').glob('*/job.json')}
    for path in sorted(paths):
        job = read(path)
        bid = job['id']
        found_ids.add(bid)
        if batches and bid not in batches:
            continue
        counselor = job.get('request', {}).get('counselor_version')
        versions = job.get('versions', [])
        if counselors and counselor not in counselors:
            continue
        if clients and not set(clients).intersection(versions):
            continue
        reason = None
        if job.get('mode') != 'compare':
            reason = 'unsupported_mode: currently compare-fork only'
        elif job.get('status') != 'completed' or not job.get('rows') or any(r.get('status') != 'completed' for r in job['rows']):
            reason = 'incomplete_or_failed'
        elif len(versions) != 1 or not counselor:
            reason = 'ambiguous_version'
        if reason:
            excluded.append({'batch': bid, 'reason': reason})
            continue
        stamp = datetime.fromisoformat(job['created_at']).timestamp()
        candidates[(counselor, versions[0])].append((stamp, bid, path, job))
    missing = set(batches) - found_ids
    if missing:
        raise ValueError('Unknown batch IDs: ' + ', '.join(sorted(missing)))
    selected = []
    for key, items in sorted(candidates.items()):
        items.sort(key=lambda x: (x[0], x[1]), reverse=True)
        chosen = items if batches else items[:1]
        for _, _, path, job in chosen:
            selected.append((path, job))
        for _, bid, _, _ in ([] if batches else items[1:]):
            excluded.append({'batch': bid, 'reason': 'older_completed_for_same_version_pair'})
    if batches:
        chosen_ids = {j['id'] for _, j in selected}
        if set(batches) != chosen_ids:
            raise ValueError('Explicit batches are not all eligible: ' + str(excluded))
    if not selected:
        raise ValueError('No eligible completed compare batches match the filters.')
    return selected, excluded


def state_path(root, session, sid):
    """Follow the committed pointer, supporting old attempts and client_state.

    Never guess the newest attempt: it may be a failed or uncommitted one.
    """
    storage = Path(session['client_storage_dir'])
    if not storage.is_absolute():
        storage = root / storage
    path = storage / session['client_version'] / sid / 'memory_state.json'
    if not path.is_file():
        raise ValueError(f'Committed client state missing: {path}')
    return path


def session_data(root, row, job, storage_dir=None):
    sid = row['session_id']
    sp = (Path(storage_dir) if storage_dir else root / 'runs') / 'paired' / sid / 'session.json'
    session = read(sp)
    mp = state_path(root, session, sid)
    state = read(mp)
    n, total = int(job['options']['fork_turn']), int(job['options']['turns'])
    if (session['client_version'] != row['version'] or
            session['counselor_version'] != job['request']['counselor_version'] or
            session.get('compare', {}).get('branch') != row['branch'] or
            session['compare']['fork_turn'] != n):
        raise ValueError(f'Session/batch metadata mismatch: {sid}')
    public = session['public_dialogue']
    if len(public) != 2 * total:
        raise ValueError(f'Expected {total} complete turns, got {len(public)} utterances: {sid}')
    records = {r['turn']: r for r in state['records']}
    if len(records) != len(state['records']) or set(records) != set(range(1, total + 1)):
        raise ValueError(f'Missing/duplicate client turn records: {sid}')
    turns = []
    for t in range(1, total + 1):
        c, u = public[2*t-2:2*t]
        if c['role'] != 'counselor' or u['role'] != 'client':
            raise ValueError(f'Unexpected dialogue roles: {sid}/{t}')
        r = records[t]
        if r['utterance'] != u['content'] or r['counselor_utterance'] != c['content']:
            raise ValueError(f'Public/private alignment mismatch: {sid}/{t}')
        turns.append({'turn': t, 'counselor': c['content'], 'client': u['content'], 'inner': r['inner']})
    # Align steps with the suffix of public dialogue; opening is not a step.
    steps = session.get('steps', [])
    offset = len(public) - len(steps)
    if offset < 0:
        raise ValueError(f'Too many steps: {sid}')
    aligned = []
    for i, step in enumerate(steps, start=offset):
        if step['actor'] != public[i]['role'] or step['utterance'] != public[i]['content']:
            raise ValueError(f'Step alignment mismatch: {sid}/{i}')
        aligned.append((i // 2 + 1, step))
    labels, checks, usage, coverage = Counter(), [], Counter(), Counter()
    for t, step in aligned:
        start = n + 1 if step['actor'] == 'counselor' else n
        if t < start:
            continue
        coverage['steps'] += 1
        if isinstance(step.get('usage'), dict):
            coverage['steps_with_usage'] += 1
            for k, v in step['usage'].items():
                if isinstance(v, (int, float)):
                    usage[k] += v
        if step['actor'] == 'counselor':
            coverage['counselor_turns'] += 1
            meta = step.get('rupture')
            if meta:
                coverage['counselor_turns_with_labels'] += 1
                labels[meta['move']] += 1
                if meta.get('marker', {}).get('found'):
                    coverage['reported_markers'] += 1
                obs = meta.get('observer')
                if obs:
                    coverage['observer_calls'] += 1
                    coverage['observer_followed'] += int(obs.get('followed') is True)
                for kind, key, flag, target_key in [('check_in', 'check', 'is_check', 'counselor_turn'), ('rupture_explore', 'explore', 'is_explore', 'client_turn')]:
                    m = meta.get(key, {})
                    if m.get(flag):
                        target = m.get(target_key)
                        memory = records[t].get('memory_input')
                        checks.append({'turn': t, 'kind': kind, 'target': target, 'valid': m.get('valid'),
                                       'gap': m.get('gap'), 'target_injected': None if memory is None or not target else target in [x['turn'] for x in memory]})
    memory = {}
    stances = Counter()
    for t, r in records.items():
        memory[t] = None if 'memory_input' not in r else [x['turn'] for x in r['memory_input']]
        if t >= n:
            pp = r['inner'].get('private_position', {})
            if 'counselor_stance' in pp and 'stance_expression' in pp:
                coverage['client_turns_with_stance_labels'] += 1
                stances[pp['counselor_stance'] + '/' + pp['stance_expression']] += 1
    return {
        'batch': job['id'], 'created_at': job['created_at'], 'profile': row['profile_id'],
        'profile_hash': row.get('profile_hash'), 'session': sid, 'branch': row['branch'],
        'counselor': session['counselor_version'], 'client': session['client_version'],
        'fork_turn': n, 'total_turns': total, 'turns': turns,
        'scope': {'client_start': n, 'counselor_start': n + 1},
        'memory_input': memory,
        'code': {'labels': dict(labels), 'checks': checks, 'coverage': dict(coverage), 'stance_labels': dict(stances), 'usage': dict(usage)},
        'sources': [{'path': str(p.relative_to(root)), 'sha256': file_hash(p)} for p in [sp, mp]],
        'prompt_hashes': {k: digest(v) for k, v in session.get('settings', {}).items() if 'prompt' in k},
    }


def extract(root, selected, max_profiles=None):
    docs, batches = [], []
    for path, job in selected:
        groups = defaultdict(dict)
        for row in job['rows']:
            pid, branch = row['profile_id'], row.get('branch')
            if branch in groups[pid]:
                raise ValueError(f'Duplicate branch {pid}/{branch}')
            groups[pid][branch] = row
        ids = sorted(groups)
        if max_profiles:
            ids = ids[:max_profiles]
        for pid in ids:
            if set(groups[pid]) != {'common', 'provided', 'withheld'}:
                raise ValueError(f'Incomplete fork pair: {job["id"]}/{pid}')
            pair = [session_data(root, groups[pid][b], job, path.parent.parent.parent) for b in ('provided', 'withheld')]
            a, b = pair
            n = a['fork_turn']
            if a['turns'][:n-1] != b['turns'][:n-1] or a['turns'][n-1]['counselor'] != b['turns'][n-1]['counselor']:
                raise ValueError(f'Fork common prefix differs: {job["id"]}/{pid}')
            docs.extend(pair)
        batches.append({'id': job['id'], 'counselor': job['request']['counselor_version'],
                        'client': job['versions'][0], 'created_at': job['created_at'],
                        'turns': job['options']['turns'], 'fork_turn': job['options']['fork_turn'],
                        'profiles': ids, 'profile_hashes': {p: groups[p]['provided'].get('profile_hash') for p in ids},
                        'generation': {k: job['request'].get(k) for k in ('model','generation','thinking_mode')},
                        'source': str(path.relative_to(root)), 'sha256': file_hash(path)})
    return docs, batches


def packet(doc, kind, packet_id):
    turns = [{k: v for k, v in t.items() if k != 'inner' or kind == 'private'} for t in doc['turns']]
    return {'packet_id': packet_id, 'scope': doc['scope'], 'turns': turns}
