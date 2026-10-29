"""Ten adaptive probes in one session; preserve failures and do not retry automatically."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
LAB = ROOT.parents[1]
sys.path.insert(0, str(LAB))
import app_server as app


def save(name, data):
    (ROOT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    config = json.loads((LAB / 'config.json').read_text(encoding='utf-8'))
    save('config.json', config)
    prompt_dir = ROOT / 'prompts'
    prompt_dir.mkdir(exist_ok=True)
    for name in ('system.md', 'summary.md'):
        (prompt_dir / name).write_bytes((LAB / 'versions/state_summary' / name).read_bytes())
    source_files = ['state_summary.py', 'runtime.py', 'tool_loop.py', 'experiment_versions.py',
                    'versions/state_summary/system.md', 'versions/state_summary/summary.md']
    save('manifest.json', {'version': 'state_summary', 'summary_mode': 'integrated_trajectory', 'protocol': '8 autonomous + 2 system-directed; counselor utterances only',
         'model': config['model'], 'generation': config['generation'], 'attempts_planned': 10,
         'source_sha256': {name: hashlib.sha256((LAB/name).read_bytes()).hexdigest() for name in source_files}})
    for name in source_files + ['tools.json', 'profile_retrieval.py']:
        target = ROOT / 'source_snapshot' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((LAB/name).read_bytes())
    catalog = app.load_catalog(app.runtime.load_scenario(config), app.runtime.validate_scenario,
                               config.get('profile_context_mode', 'selective'))
    scenario = copy.deepcopy(catalog['profiles'][0]['histories'][0]['scenario'])
    server = app.make_server(ROOT / 'config.json', port=0, storage_dir=ROOT / 'sessions')
    threading.Thread(target=server.serve_forever, daemon=True).start()
    history, session, results = [], None, []
    print('READY: profile 001 / history 1; enter one therapist utterance per line.', flush=True)
    try:
        for turn in range(1, 11):
            print(f'INPUT {turn}/10 (JSON probe):', flush=True)
            probe = json.loads(sys.stdin.readline())
            save(f'probe_{turn}.json', probe)
            utterance = probe['utterance'].strip()
            if session and not history:
                utterance = scenario['selected_counselor_utterance']
            if not utterance:
                raise ValueError('A nonempty therapist utterance is required; no inference was retried.')
            if not history:
                scenario['dialogue_turns'][-1]['counselor'] = utterance
                scenario = app.runtime.validate_scenario(scenario)
                save('scenario.json', scenario)
            body = {'experiment_version': 'state_summary', 'scenario': scenario,
                    'enabled_tools': ['retrieve_inner_state', 'retrieve_childhood', 'retrieve_situation'],
                    'system_prompt': (prompt_dir / 'system.md').read_text(encoding='utf-8'),
                    'summary_system_prompt': (prompt_dir / 'summary.md').read_text(encoding='utf-8'),
                    'messages': [{'role': 'user', 'content': utterance}]}
            directive = probe.get('system_directive', '')
            if directive:
                body['system_prompt'] += '\n\n## 이번 생성의 실험 지시\n' + directive
            (ROOT / f'system_{turn}.md').write_text(body['system_prompt'], encoding='utf-8')
            if session:
                body['session_id'] = session
            save(f'input_{turn}.json', body)
            events, started = [], time.monotonic()
            request = Request(f'http://127.0.0.1:{server.server_port}/api/chat',
                              data=json.dumps(body, ensure_ascii=False).encode(),
                              headers={'X-Gemma-Request': '1', 'Content-Type': 'application/json'})
            try:
                with urlopen(request, timeout=300) as response:
                    for raw in response:
                        event = json.loads(raw)
                        events.append(event)
                        # Preserve partial events even if inference fails.
                        with (ROOT / f'events_{turn}.jsonl').open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
                result = {'turn': turn, 'elapsed_seconds': round(time.monotonic()-started, 2),
                          'last_event': events[-1] if events else None}
            except Exception as error:
                result = {'turn': turn, 'elapsed_seconds': round(time.monotonic()-started, 2), 'error': str(error)}
            results.append(result)
            save('progress.json', results)
            if events:
                session = events[0]['session_id']
            if not events or events[-1]['type'] != 'done':
                print('FAILED (no retry, next probe uses last committed history): ' + json.dumps(result, ensure_ascii=False), flush=True)
                continue
            done = events[-1]
            session = events[0]['session_id']
            history = done['history']
            summary = next(e for e in events if e['type'] == 'state_summary')
            print(json.dumps({'turn': turn, 'session_id': session, 'utterance': done['response'],
                  'inner_state': done['inner_state'], 'feelings': json.loads(done['model_response'])['feelings'], 'summary': summary.get('summary'),
                  'summary_source_turns': summary.get('source_turns', []),
                  'usage': done.get('usage'), 'elapsed_seconds': result['elapsed_seconds'],
                  'tools': [e.get('name') for e in events if e['type'] == 'tool_result']},
                  ensure_ascii=False, indent=2), flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
