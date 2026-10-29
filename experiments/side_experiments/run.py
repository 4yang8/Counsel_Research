"""Reproducible side experiments using the same paired APIs and durable logs as UI."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import shutil
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from project_paths import PROJECT_ROOT, relocate_path, relocate_session
LAB = PROJECT_ROOT
sys.path.insert(0, str(LAB / 'src'))
import app.app_server as app_server
import client.registry as experiment_versions
from counselor import catalog

OUT = LAB / 'experiments/side_auto_20261018'


def read(path):
    value = json.loads(Path(relocate_path(str(path))).read_text())
    return relocate_session(value) if isinstance(value, dict) and 'session_id' in value else value


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))


def api(route, body=None, port=8892):
    request = Request(f'http://127.0.0.1:{port}' + route,
                      data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json', 'X-Gemma-Request': '1'})
    return urlopen(request, timeout=600)


def configuration():
    cfg = read(LAB / 'config/app.json')
    cfg.update(read(LAB / 'runs/paired/261018_204046/session.json')['connection'])
    cfg.update(timeout_seconds=300, inspect_prompt=True, experiment_version='Q1_s1',
               storage_dir=str(OUT / 'storage'))
    cfg['generation'] = {'temperature': .7, 'top_p': .95, 'max_tokens': 4096, 'seed': 20261018}
    write(OUT / 'config.json', cfg)
    return cfg


def compact(session, manifest):
    result = {'session_id': session['session_id'], 'dialogue': session['public_dialogue'], 'attempts': []}
    for attempt in session['attempts']:
        log = read(attempt['saved_path'])
        row = {**attempt, 'status': log.get('status'), 'error': log.get('error'),
               'response': log.get('response'), 'calls': [], 'tools': [], 'children': []}
        for call in log.get('model_calls', []):
            row['calls'].append({'iteration': call['iteration'], 'usage': call.get('usage'),
                                 'reasoning_chars': len(call.get('reasoning', '')),
                                 'max_tokens': call['request'].get('max_tokens'),
                                 'budget': call['request'].get('thinking_token_budget')})
        for event in log.get('tool_events', []):
            if event['type'] == 'tool_start':
                try:
                    arguments = json.loads(event['arguments'])
                except ValueError:
                    arguments = event['arguments']
                row['tools'].append({'name': event['name'], 'arguments': arguments})
            elif event['type'] == 'tool_result':
                row['tools'][-1].update(error=event.get('is_error'), result=event.get('model_content'))
        for wrapper in log.get('subagent_events', []):
            event = wrapper['event']
            if event['type'] == 'done':
                row['children'].append({'kind': wrapper.get('agent_kind'), 'response': event.get('response'),
                                        'usage': event.get('usage'), 'reasoning_chars': len(event.get('reasoning', ''))})
        result['attempts'].append(row)
    write(Path(manifest['directory']) / 'compact.json', result)
    lines = ['# ' + manifest['name'], '']
    for row in session['public_dialogue']:
        lines += [('상담사' if row['role'] == 'counselor' else '내담자') + ': ' + row['content'], '']
    (Path(manifest['directory']) / 'dialogue.md').write_text('\n'.join(lines))


def snapshot_sources():
    """Capture current executable dependencies without archived results or caches."""
    paths = [LAB / relative for relative in (
        'src', 'resources', 'config/app.json', 'experiments/side_experiments',
        'pyproject.toml', 'scripts/run_counseling.sh')]
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f'Snapshot source does not exist: {path}')
    return paths


def save_snapshot(folder):
    sources = snapshot_sources()
    destination = Path(folder) / 'snapshot'
    destination.mkdir(parents=True, exist_ok=False)
    for source in sources:
        target = destination / source.relative_to(LAB)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                '__pycache__', '*.pyc', '.pytest_cache'))
        else:
            shutil.copy2(source, target)
    return destination


def batch(args):
    name = args.name or args.condition + '_' + args.case + '_' + datetime.now(timezone.utc).strftime('%H%M%S')
    folder = OUT / 'runs' / name
    folder.mkdir(parents=True, exist_ok=False)
    body = copy.deepcopy(read(LAB / 'runs/paired/261018_204046/session.json')['settings'])
    client = args.client or ('Q1' if args.condition == 'baseline' else 'Q1_s1')
    counselor = args.counselor or ('P3a' if args.condition == 'baseline' else 'P3a_s1')
    body.update(experiment_version=client, counselor_version=counselor, session_id=None,
                model='Qwen/Qwen3.8-27B-FP8', speaker_modes={'client': 'model', 'counselor': 'model'},
                system_prompt=experiment_versions.client_runtime(client).specification()['system_prompt'],
                counselor_system_prompt=catalog.specification(counselor)['system_prompt'],
                client_reasoning_budgets={'main': args.client_budget, 'subagent': args.router_budget},
                counselor_reasoning_budgets={'main': 512, 'subagent': 512},
                generation={'temperature': .7, 'top_p': .95, 'max_tokens': args.max_tokens, 'seed': args.seed})
    if args.note_mode:
        body['side_note_mode'] = args.note_mode
    if args.trait_units:
        body['side_trait_units'] = args.trait_units
    if args.router_reason:
        body['side_router_reason'] = args.router_reason
    if args.guide_context:
        body['side_guide_context'] = args.guide_context
        if args.guide_context == 'source_only':
            body['counselor_system_prompt'] = (LAB / 'src/counselor' / counselor / 'system_source_only.md').read_text()
    if args.guide_policy == 'off':
        body['side_guide_policy'] = 'off'
        body['counselor_system_prompt'] = (LAB / 'src/counselor' / counselor / 'system_no_guide.md').read_text()
    elif args.guide_policy == 'optional':
        body['side_guide_policy'] = 'optional'
        body['counselor_system_prompt'] = (LAB / 'src/counselor' / counselor / 'system_optional_guide.md').read_text()
    if args.prompt_file:
        body['counselor_system_prompt'] = (LAB / 'src/counselor' / counselor / args.prompt_file).read_text()
        if args.guide_policy == 'off':
            first, rest = body['counselor_system_prompt'].split('# 가이드 자문\n', 1)
            body['counselor_system_prompt'] = first + '# 노트 관리\n' + rest.split('# 노트 관리\n', 1)[1]
    body.pop('session_key', None)
    if args.case != 'lab':
        profile = read(LAB / 'resources/profiles/group1' / ('gold_ccd_profile_' + args.case + '.json'))
        body['scenario']['profile'] = profile
        body['scenario']['id'] = 'side_profile_' + args.case
    scenario = body['scenario']
    scenario.pop('dialogue_turns', None)
    scenario.pop('selected_counselor_utterance', None)
    scenario['dialogue_format'] = 'messages'
    scenario['dialogue'] = []
    body['messages'] = [{'role': 'user', 'content': '오늘 상담에서 가장 먼저 다뤄보고 싶은 일이 있나요?'}]
    manifest = {'name': name, 'directory': str(folder), 'created_at': datetime.now(timezone.utc).isoformat(),
                'condition': args.condition, 'client': client, 'counselor': counselor, 'case': args.case,
                'planned_pairs': args.rounds, 'settings': body, 'completed_steps': 0, 'failures': []}
    # Preserve the current source, shared dependencies, and prompt inputs.
    save_snapshot(folder)
    write(folder / 'manifest.json', manifest)
    with api('/api/paired/create', body, args.port) as response:
        session = json.load(response)
    manifest['session_id'] = session['session_id']
    write(folder / 'manifest.json', manifest)
    print('CREATED', name, session['session_id'], client, counselor, flush=True)
    for step in range(args.rounds * 2):
        started = time.monotonic()
        last = None
        with api('/api/paired/step', {'session_id': session['session_id'], 'expected_step': len(session['steps'])}, args.port) as response:
            for line in response:
                if not line.strip():
                    continue
                wrapper = json.loads(line)
                event = wrapper.get('event', wrapper)
                if event['type'] in ('done', 'error', 'cancelled'):
                    last = event
        with api('/api/paired/session?session_id=' + session['session_id'], port=args.port) as response:
            session = json.load(response)
        compact(session, manifest)
        if not last or last['type'] != 'done':
            manifest['failures'].append({'step': step + 1, 'event': last})
            write(folder / 'manifest.json', manifest)
            print('FAILED', name, step + 1, last, flush=True)
            return
        manifest['completed_steps'] = len(session['steps'])
        write(folder / 'manifest.json', manifest)
        print('STEP', name, step + 1, last['step']['actor'], round(time.monotonic() - started, 1),
              last['response'].replace('\n', ' ')[:220], flush=True)
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    write(folder / 'manifest.json', manifest)
    print('COMPLETED', name, manifest['completed_steps'], flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['serve', 'batch'])
    p.add_argument('--port', type=int, default=8892)
    p.add_argument('--condition', choices=['baseline', 's1'], default='s1')
    p.add_argument('--name')
    p.add_argument('--case', default='lab')
    p.add_argument('--client')
    p.add_argument('--counselor')
    p.add_argument('--rounds', type=int, default=6)
    p.add_argument('--client-budget', type=int)
    p.add_argument('--router-budget', type=int)
    p.add_argument('--max-tokens', type=int, default=4096)
    p.add_argument('--seed', type=int, default=20261018)
    p.add_argument('--fast-log', action='store_true')
    p.add_argument('--note-mode', choices=['edits', 'replace'])
    p.add_argument('--trait-units', choices=['leaf', 'semantic'])
    p.add_argument('--router-reason', choices=['main', 'log_only'])
    p.add_argument('--guide-context', choices=['request', 'public', 'source_only'])
    p.add_argument('--guide-policy', choices=['on', 'off', 'optional'], default='on')
    p.add_argument('--prompt-file')
    args = p.parse_args()
    if args.command == 'serve':
        configuration()
        from side_experiments.side_log import log_factory
        server = app_server.make_server(OUT / 'config.json', host='127.0.0.1', port=args.port,
                                       storage_dir=OUT / 'storage', log_factory=log_factory if args.fast_log else None)
        print('SIDE UI http://127.0.0.1:' + str(server.server_port), flush=True)
        server.serve_forever()
    else:
        batch(args)
