"""Read-only replay of recording calls; write new artifacts, never session state."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / 'src'))
from client.Q3sk import runtime


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def skill_body(content):
    return content.split('---', 2)[2].lstrip('\r\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    output = HERE / ('probes_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
    output.mkdir()
    session = json.loads((PROJECT / 'runs/paired/261030_132004/session.json').read_text())
    records = [json.loads(p.read_text()) for p in sorted(
        (Path(session['client_storage_dir']) / 'Q3sk' / session['session_id'] / 'appraisals').glob('*.json'))]
    source = Path('src/client/Q3sk')
    before_system = (HERE / 'before' / source / 'system.md').read_text()
    after_system = (HERE / 'after' / source / 'system.md').read_text()
    before_skill = skill_body((HERE / 'before' / source / 'skills/client-note-update/SKILL.md').read_text())
    after_skill = skill_body((HERE / 'after' / source / 'skills/client-note-update/SKILL.md').read_text())
    tools = {tool['function']['name']: tool for tool in runtime.TOOLS}
    cases = [('progress_turn2', records[1], 'update_trajectory_progress'),
             ('notes_turn3', records[2], 'update_client_notes')]
    tasks = []
    for name, record, tool in cases:
        original = next(req for req in record['model_requests']
                        if isinstance(req.get('tool_choice'), dict)
                        and req['tool_choice']['function']['name'] == tool)
        for condition in ('before', 'after'):
            for repeat in range(args.repeats):
                payload = copy.deepcopy(original)
                if condition == 'after':
                    system = next(m for m in payload['messages'] if m['role'] == 'system')
                    assert before_system in system['content']
                    system['content'] = system['content'].replace(before_system, after_system, 1)
                    user = next(m for m in payload['messages'] if m['role'] == 'user')
                    assert before_skill in user['content']
                    user['content'] = user['content'].replace(before_skill, after_skill, 1)
                    payload['tools'] = [copy.deepcopy(tools[tool])]
                payload['stream'] = False
                payload.pop('stream_options', None)
                payload['seed'] = 101 + repeat
                tasks.append((name, condition, repeat, tool, record, payload))
    write(output / 'manifest.json', {
        'source_session': session['session_id'], 'model': session['connection']['model'],
        'endpoint': session['connection']['vllm_url'], 'repeats': args.repeats,
        'scope': 'Frozen recording calls; prior appraisal, committed speech and same-turn history are held fixed.',
        'caveat': 'notes_turn3 retains the old upstream progress/appraisal; this probes new recording claims, not removal of all pre-existing claims.',
        'seeds': [101 + i for i in range(args.repeats)],
        'evaluation': 'Structural validation plus direct source/output review; no keyword grader.'})

    def run(task):
        name, condition, repeat, tool, record, payload = task
        folder = output / f'{name}_{condition}_{repeat + 1}'
        folder.mkdir()
        write(folder / 'request.json', payload)
        headers = {'Content-Type': 'application/json'}
        key = os.environ.get(session['connection'].get('api_key_env', 'VLLM_API_KEY'))
        if key:
            headers['Authorization'] = 'Bearer ' + key
        url = session['connection']['vllm_url'].rstrip('/') + '/chat/completions'
        start = time.monotonic()
        result = {'case': name, 'condition': condition, 'repeat': repeat + 1,
                  'source_turn_id': record['turn_id'], 'tool': tool}
        try:
            request = Request(url, json.dumps(payload, ensure_ascii=False).encode(), headers)
            with urlopen(request, timeout=180) as response:
                raw = json.load(response)
            write(folder / 'response.json', raw)
            choice = raw['choices'][0]
            calls = choice['message'].get('tool_calls') or []
            if len(calls) != 1 or calls[0]['function']['name'] != tool:
                raise ValueError('Expected one named tool call')
            arguments = json.loads(calls[0]['function']['arguments'])
            if tool == 'update_trajectory_progress':
                runtime.trajectory.validate(arguments, runtime.trajectory.PROGRESS)
            else:
                runtime.note_items.apply(runtime.note_items.read_items(record['notes_before']), arguments)
            result.update(status='completed', arguments=arguments, usage=raw.get('usage'),
                          finish_reason=choice.get('finish_reason'))
        except Exception as error:
            result.update(status='failed', error=str(error))
        result['seconds'] = round(time.monotonic() - start, 3)
        result['request_sha256'] = hashlib.sha256((folder / 'request.json').read_bytes()).hexdigest()
        write(folder / 'result.json', result)
        print(json.dumps({k: result[k] for k in ('case', 'condition', 'repeat', 'status', 'seconds')}, ensure_ascii=False), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, tasks))
    write(output / 'results.json', results)
    print('OUTPUT', output, flush=True)


if __name__ == '__main__':
    main()
