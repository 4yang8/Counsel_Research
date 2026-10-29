"""Fork an exact public/private checkpoint and test the complete downstream handoff."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import LAB, OUT, api, read, write, compact


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    parser.add_argument('--before-step', type=int, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--port', type=int, default=8892)
    parser.add_argument('--speeches', type=int, default=2)
    parser.add_argument('--guide-context', default='source_only')
    parser.add_argument('--prompt-file')
    parser.add_argument('--guide-policy', choices=['on', 'off', 'optional'], default='on')
    parser.add_argument('--client-budget', type=int)
    args = parser.parse_args()
    source_manifest = read(OUT / 'runs' / args.run / 'manifest.json')
    source_compact = read(OUT / 'runs' / args.run / 'compact.json')
    parent = next(a for a in source_compact['attempts'] if a['step'] == args.before_step)
    if parent['actor'] != 'counselor':
        raise ValueError('Choose a counselor checkpoint')
    folder = OUT / 'runs' / args.name
    folder.mkdir(parents=True, exist_ok=False)
    body = copy.deepcopy(source_manifest['settings'])
    body['session_id'] = None
    body.update(side_note_mode='replace', side_guide_context=args.guide_context)
    if args.guide_context == 'source_only':
        body['counselor_system_prompt'] = (LAB / 'src/counselor' / body['counselor_version'] / 'system_source_only.md').read_text()
    if args.guide_policy == 'off':
        body['side_guide_policy'] = 'off'
    elif args.guide_policy == 'optional':
        body['side_guide_policy'] = 'optional'
        body['counselor_system_prompt'] = (LAB / 'src/counselor' / body['counselor_version'] / 'system_optional_guide.md').read_text()
    if args.client_budget:
        body['client_reasoning_budgets'] = {'main': args.client_budget, 'subagent': args.client_budget}
    if args.prompt_file:
        body['counselor_system_prompt'] = (LAB / 'src/counselor' / body['counselor_version'] / args.prompt_file).read_text()
        if args.guide_policy == 'off':
            first, rest = body['counselor_system_prompt'].split('# 가이드 자문\n', 1)
            body['counselor_system_prompt'] = first + '# 노트 관리\n' + rest.split('# 노트 관리\n', 1)[1]
    body['messages'] = [{'role': 'user', 'content': source_compact['dialogue'][0]['content']}]
    manifest = {'name': args.name, 'directory': str(folder), 'client': body['experiment_version'],
                'counselor': body['counselor_version'], 'settings': body, 'planned_pairs': args.speeches / 2,
                'completed_steps': 0, 'failures': [], 'source_run': args.run,
                'source_before_step': args.before_step, 'condition': 'checkpoint_replay'}
    with api('/api/paired/create', body, args.port) as response:
        session = json.load(response)
    sid = session['session_id']
    root = OUT / 'storage/paired' / sid
    session['public_dialogue'] = copy.deepcopy(source_compact['dialogue'][:args.before_step])
    session['initial_public_count'] = len(session['public_dialogue'])
    session['next_actor'] = 'counselor'
    # Restore BOTH counselor notes as they were before the source turn.
    snapshots = {}
    for line in Path(parent['saved_path']).with_suffix('.events.jsonl').read_text().splitlines():
        event = json.loads(line)
        if event['type'] == 'version_event' and event.get('action') == 'snapshot':
            snapshots.setdefault(event['section'], event['data']['content'])
    for section, filename in [('client_notes', 'notes.md'), ('counselor_notes', 'counselor_notes.md')]:
        (root / filename).write_text(snapshots[section])
    # Restore client state after the preceding client response, not session end.
    previous = next(a for a in reversed(source_compact['attempts'])
                    if a['actor'] == 'client' and a['status'] == 'completed' and a['step'] < args.before_step)
    source_sid = source_manifest['session_id']
    source_private = OUT / 'storage/paired' / source_sid / 'client_attempts' / previous['turn_id']
    destination = root / 'imported_client_checkpoint'
    shutil.copytree(source_private, destination)
    version = session['client_version']
    (destination / version / source_sid).rename(destination / version / sid)
    session['client_storage_dir'] = str(destination)
    write(root / 'session.json', session)
    manifest['session_id'] = sid
    write(folder / 'manifest.json', manifest)
    for src in [LAB / 'src/counselor' / body['counselor_version'], LAB / 'src/client' / body['experiment_version']]:
        dest = folder / 'snapshot' / src.relative_to(LAB)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    print('REPLAY_CREATED', args.name, sid, flush=True)
    for number in range(args.speeches):
        last = None
        with api('/api/paired/step', {'session_id': sid, 'expected_step': len(session['steps']),
                                     'request_id': args.name + '_' + str(number)}, args.port) as response:
            for line in response:
                if line.strip():
                    event = json.loads(line).get('event', {})
                    if event.get('type') in ('done', 'error', 'cancelled'):
                        last = event
        with api('/api/paired/session?session_id=' + sid, port=args.port) as response:
            session = json.load(response)
        compact(session, manifest)
        if not last or last['type'] != 'done':
            manifest['failures'].append(last)
            write(folder / 'manifest.json', manifest)
            print('REPLAY_FAILED', args.name, last, flush=True)
            break
        manifest['completed_steps'] = len(session['steps'])
        write(folder / 'manifest.json', manifest)
        print('REPLAY_STEP', args.name, number + 1, last['response'], flush=True)
    else:
        from datetime import datetime, timezone
        manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
        write(folder / 'manifest.json', manifest)
        print('REPLAY_COMPLETED', args.name, flush=True)
