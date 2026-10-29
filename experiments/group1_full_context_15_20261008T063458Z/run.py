"""Generate 15 independent client continuations through the unchanged lab backend."""
import copy
import hashlib
import json
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import app_server

LEVELS = ('낮음', '중간', '높음')
STATES = ('trust', 'disclosure', 'change_confidence')
PLAN = {
    '001': [(0,0,0), (1,1,1), (2,2,2)],
    '002': [(0,1,2), (1,2,0), (2,0,1)],
    '005': [(0,2,1), (1,0,2), (2,1,0)],
    '007': [(1,1,0), (2,2,1), (0,0,2)],
    '008': [(2,0,0), (0,1,1), (1,2,2)],
}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def post(server, route, body):
    request = Request('http://127.0.0.1:' + str(server.server_port) + route,
                      data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={'X-Gemma-Request':'1', 'Content-Type':'application/json'})
    return urlopen(request, timeout=1200)


def main():
    if (OUT / 'manifest.json').exists():
        raise RuntimeError('This batch already exists; do not overwrite or silently regenerate it.')
    snapshot = OUT / 'snapshot'
    sources = ['app_server.py', 'history_cases.py', 'runtime.py', 'server.py', 'tool_loop.py', 'mcp_client.py',
               'config.json', 'tools.json', 'cases/lab_meeting.json', 'cases/group1/histories.json',
               'cases/lab_meeting_experiences.json', 'cases/lab_meeting_current_event.json',
               'cases/lab_meeting_constraints.json', 'cases/lab_meeting_state_basis.json']
    hashes = {}
    for relative in sources:
        source = ROOT / relative
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    for number in PLAN:
        relative = 'gold_ccd_profile_' + number + '.json'
        source = ROOT.parent / 'profile/group1' / relative
        target = snapshot / 'profiles' / relative
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        hashes['profiles/' + relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    config = json.loads((snapshot / 'config.json').read_text())
    config['scenario_file'] = str(snapshot / 'cases/lab_meeting.json')
    for key, filename in [('experience_file','lab_meeting_experiences.json'),
                          ('current_event_file','lab_meeting_current_event.json'),
                          ('constraints_file','lab_meeting_constraints.json'),
                          ('state_basis_file','lab_meeting_state_basis.json')]:
        config[key] = str(snapshot / 'cases' / filename)
    dump(OUT / 'config.json', config)
    catalog = app_server.load_catalog(app_server.runtime.load_scenario(config), app_server.runtime.validate_scenario)
    tools = [tool['function']['name'] for tool in app_server.runtime.definitions()]
    manifest = {'run_id': OUT.name, 'code_condition':'current counseling_test working-tree snapshot',
                'started_at':datetime.now(timezone.utc).isoformat(), 'profiles':list(PLAN),
                'expected_generations':15, 'repeats_per_input':1, 'system_prompt':config['system_prompt'],
                'generation':config['generation'], 'model':config['model'], 'vllm_url':config['vllm_url'],
                'enabled_tools':tools, 'source_sha256':hashes,
                'state_order':list(STATES), 'state_plan':{p:[[LEVELS[n] for n in row] for row in rows] for p,rows in PLAN.items()},
                'scope':'All profile content except created_at/meta/start_rapport in initial Markdown prompt; four complete enabled pairs plus fifth counselor utterance; fifth reference client answer withheld.',
                'analysis_method':'Human-readable artifact audit by Codex; no additional model judge.',
                'limitations':['Different histories receive different states; this is not a matched causal test of states.',
                               'No retrieval-only comparison in this batch.', 'Group1 external records are not registered; selected tools can return not_recorded.']}
    dump(OUT / 'manifest.json', manifest)
    server = app_server.make_server(OUT / 'config.json', port=0, storage_dir=OUT / 'sessions')
    server.RequestHandlerClass.log_message = lambda *args: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    try:
        # Read-only connectivity check before generation.
        with urlopen(config['vllm_url'].rstrip('/') + '/models', timeout=15) as response:
            model_ids = [item['id'] for item in json.load(response)['data']]
        if config['model'] not in model_ids:
            raise RuntimeError('Configured model is not served: ' + config['model'])
        print(json.dumps({'ready':True, 'batch':str(OUT), 'model':config['model'], 'cases':15},ensure_ascii=False),flush=True)
        for entry in catalog['profiles']:
            for h_index, history in enumerate(entry['histories']):
                scenario = copy.deepcopy(history['scenario'])
                for key, index in zip(STATES, PLAN[entry['number']][h_index]):
                    level = LEVELS[index]
                    scenario['states'][key].update(level=level, meaning=scenario['state_levels'][key][level])
                folder = OUT / ('case_' + history['id'])
                folder.mkdir()
                body = {'scenario':scenario, 'system_prompt':config['system_prompt'],
                        'enabled_tools':tools, 'generation':config['generation'],
                        'messages':[{'role':'user','content':scenario['selected_counselor_utterance']}]}
                dump(folder / 'scenario.json', scenario)
                dump(folder / 'input.json', body)
                with post(server, '/api/preview', body) as response:
                    preview = json.load(response)['payload']
                dump(folder / 'payload.json', preview)
                prompt = preview['messages'][1]['content']
                if history['turns'][-1]['client'] in prompt or not prompt.endswith(scenario['selected_counselor_utterance']):
                    raise RuntimeError('Reference answer boundary is broken for ' + history['id'])
                start = time.monotonic()
                events = []
                transport_error = None
                try:
                    with post(server, '/api/chat', body) as response:
                        for line in response:
                            if line.strip():
                                events.append(json.loads(line))
                except Exception as error:
                    transport_error = str(error)
                dump(folder / 'events.json', events)
                saved_path = next((e.get('saved_path') for e in events if e['type']=='request'), None)
                log = json.loads(Path(saved_path).read_text()) if saved_path else {}
                result = {'case_id':history['id'], 'profile':entry['number'], 'history_title':history['title'],
                          'states':{key:scenario['states'][key]['level'] for key in STATES},
                          'status':log.get('status', 'transport_error'), 'finish_reason':log.get('finish_reason'),
                          'latency_seconds':round(time.monotonic()-start,2), 'response':log.get('response',''),
                          'reasoning':log.get('reasoning',''), 'usage':log.get('usage'),
                          'model_calls':len(log.get('model_calls',[])),
                          'tools':[{'name':e['name'],'arguments':e.get('arguments'),'is_error':e.get('is_error'),
                                    'model_content':e.get('model_content')} for e in log.get('tool_events',[])],
                          'subagent_events':len(log.get('subagent_events',[])), 'log':saved_path,
                          'error':transport_error or log.get('error'),
                          'reference_client_not_sent':history['turns'][-1]['client']}
                dump(folder / 'result.json', result)
                results.append(result)
                dump(OUT / 'results.json', results)
                print(json.dumps({k:result[k] for k in ('case_id','states','status','finish_reason','latency_seconds','model_calls','subagent_events')},ensure_ascii=False),flush=True)
                if result['status'] not in ('completed', 'empty'):
                    print('Case failed; preserved evidence. Continuing planned independent cases.',flush=True)
        manifest.update(finished_at=datetime.now(timezone.utc).isoformat(), completed=sum(r['status']=='completed' for r in results))
        dump(OUT / 'manifest.json', manifest)
    finally:
        server.shutdown()
        server.server_close()

if __name__=='__main__':
    main()
