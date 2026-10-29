"""Four fixed client inputs, original P3a, independent thinking-on/off sessions."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from urllib.request import Request, urlopen

OUT = Path(__file__).resolve().parent
LAB = OUT.parents[1]
sys.path.insert(0, str(LAB))
import app_server
from counselors import catalog
from side_experiments.side_log import log_factory

PORT = 8898
INPUTS = [
    '오늘 교수님과 연구 진행 상황을 이야기하다가, 제가 맡은 실험의 비교 기준을 제대로 설명하지 못했어요. 준비가 부족했던 건 맞는데, 그 뒤로 교수님이 저를 무능한 학생으로 보셨을 것 같아서 그 장면이 계속 떠올라요.',
    '준비가 부족했던 부분은 제가 보완해야 한다고 생각해요. 그런데 자꾸 그 일을 넘어서 나는 연구에 소질이 없는 사람이라는 결론으로 가요. 사실 교수님이 저를 무능하다고 직접 말씀하신 건 아니에요. 그건 저도 아는데 마음은 계속 그쪽으로 가네요.',
    '제 생각이 어떻게 이어지는지는 좀 알겠는데, 자꾸 그 장면과 생각을 자세히 들여다보니까 오히려 더 지쳐요. 지금은 제 재능이 있는지 없는지 결론 내리기보다, 이 생각에 덜 끌려다니고 싶어요.',
    '자료에서 빠진 부분은 내일 확인해서 정리할 수 있을 것 같아요. 그렇다고 불안이 바로 없어지진 않네요. 오늘 밤은 조금 쉬고 싶은데, 생각이 또 떠오르더라도 그걸 제가 못난 사람이라는 증거처럼 받아들이지 않았으면 좋겠어요.',
]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def api(route, data=None):
    req = Request(f'http://127.0.0.1:{PORT}' + route,
                  data=None if data is None else json.dumps(data, ensure_ascii=False).encode(),
                  headers={'Content-Type': 'application/json', 'X-Gemma-Request': '1'})
    return urlopen(req, timeout=600)


def read_api(route, data=None):
    with api(route, data) as response:
        return json.load(response)


def tokens(text, model, endpoint):
    if not text:
        return 0
    req = Request(endpoint.removesuffix('/v1').rstrip('/') + '/tokenize',
                  data=json.dumps({'model': model, 'prompt': text, 'add_special_tokens': False}).encode(),
                  headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=30) as response:
        return json.load(response)['count']


def extract(log, elapsed, condition, turn, sid, cfg):
    calls = []
    for c in log.get('model_calls', []):
        calls.append({'role': 'main', 'iteration': c['iteration'], 'request': c['request'],
                      'reasoning': c.get('reasoning', ''), 'response': c.get('response', ''),
                      'usage': c.get('usage', {}),
                      'inspection': {k: v for k, v in c.get('inspection', {}).items()
                                     if k in ('token_count', 'max_model_len', 'error')}})
    for wrapper in log.get('subagent_events', []):
        e = wrapper['event']
        if e['type'] == 'request':
            calls.append({'role': 'subagent', 'agent_id': wrapper['agent_id'],
                          'request': e['payload']})
        elif e['type'] == 'done':
            c = next(c for c in reversed(calls) if c.get('agent_id') == wrapper['agent_id'])
            c.update(reasoning=e.get('reasoning', ''), response=e.get('response', ''), usage=e.get('usage', {}))
        elif e['type'] in ('error', 'cancelled'):
            c = next(c for c in reversed(calls) if c.get('agent_id') == wrapper['agent_id'])
            c.update(error=e.get('error'))
    for c in calls:
        c['reasoning_tokens_retokenized'] = tokens(c.get('reasoning', ''), cfg['model'], cfg['vllm_url'])
        c['thinking_enabled'] = c['request'].get('chat_template_kwargs', {}).get('enable_thinking')
    tools = []
    for e in log.get('tool_events', []):
        if e['type'] == 'tool_start':
            try:
                args = json.loads(e['arguments'])
            except ValueError:
                args = e['arguments']
            tools.append({'name': e['name'], 'arguments': args, 'iteration': e.get('iteration')})
        elif e['type'] == 'tool_result':
            tools[-1].update(error=e.get('is_error'), result=e.get('model_content'))
    return {'condition': condition, 'turn': turn, 'session_id': sid, 'elapsed_seconds': elapsed,
            'status': log['status'], 'error': log.get('error'), 'response': log.get('response'),
            'calls': calls, 'tools': tools, 'usage': log.get('usage'), 'turn_id': log['turn_id']}


def main():
    if (OUT / 'manifest.json').exists():
        raise SystemExit('Run already exists; do not mix or overwrite it.')
    original = json.loads((LAB / 'sessions/paired/261018_204046/session.json').read_text())
    cfg = json.loads((LAB / 'config.json').read_text())
    cfg.update(original['connection'])
    cfg.update(model='Qwen/Qwen3.8-27B-FP8', timeout_seconds=300, inspect_prompt=True,
               experiment_version='Q1', storage_dir=str(OUT / 'storage'))
    cfg['generation'] = {'temperature': .7, 'top_p': .95, 'max_tokens': 8192, 'seed': 20261019}
    cfg['filesystem'] = {'enabled': False}
    cfg['scenario_file'] = str(LAB / cfg.get('scenario_file', 'scenario.json'))
    cfgpath = OUT / 'config.json'
    cfg['chat_template_kwargs'] = {'enable_thinking': True}
    write(cfgpath, cfg)
    prompt_hashes = {}
    for source in [LAB / 'counselors' / name for name in ['P1', 'P2', 'P3', 'P3a']] + [
            LAB / name for name in ['tool_loop.py', 'paired_lab.py', 'server.py', 'app_server.py',
                                   'reasoning_budget.py', 'side_experiments/side_log.py']]:
        destination = OUT / 'snapshot' / source.relative_to(LAB)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(source, destination)
    for p in (OUT / 'snapshot').rglob('*'):
        if p.is_file():
            prompt_hashes[str(p.relative_to(OUT / 'snapshot'))] = hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'counselor': 'P3a',
                'client': 'fixed_manual_inputs', 'planned_turns_per_condition': 4,
                'independent_factor': 'enable_thinking for BOTH main and guide subagent',
                'generation': cfg['generation'], 'budget': {'main': None, 'subagent': None},
                'inputs': INPUTS, 'hashes': prompt_hashes, 'conditions': {}, 'completed': []}
    write(OUT / 'manifest.json', manifest)
    (OUT / 'PLAN.md').write_text(
        '# Reasoning 켜기/끄기 · 고정 내담자 입력 4턴\n\n'
        '- 질문: reasoning이 도구 선택·노트 정확성·상담 진행과 비용에 어떤 차이를 만드는가?\n'
        '- 원본 P3a 프롬프트·상세 가이드·도구 유지. 메인과 자문 모두 같은 Qwen 모델 사용.\n'
        '- 조건별 새 세션, 같은 시작 발화·빈 양식 노트·내담자 입력 4개. reasoning 외 설정 동일.\n'
        '- 이전 공개 발화와 노트는 각 조건에서 누적. 따라서 2턴부터 모델 입력은 경로에 따라 달라짐.\n'
        '- 순서: 1·3턴 on→off, 2·4턴 off→on. 서버 캐시·부하에 따른 시간 차이는 완전히 통제 못 함.\n'
        '- reasoning budget 없음, max_tokens 8192, seed 20261019, temperature .7, top_p .95.\n'
        '- 각 조건 1세션. 메인·자문 각각의 효과 분리, 자유로운 내담자의 후속 반응, 통계적 우위는 검증하지 않음.\n'
        '- 원본 코드·프롬프트는 수정하지 않음. 실제 요청·응답·reasoning·노트·오류를 보존.\n')
    server = app_server.make_server(cfgpath, host='127.0.0.1', port=PORT,
                                    storage_dir=OUT / 'storage', log_factory=log_factory)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    sessions = {}
    try:
        for name, enabled in [('on', True), ('off', False)]:
            cfg['chat_template_kwargs'] = {'enable_thinking': enabled}
            write(cfgpath, cfg)
            body = copy.deepcopy(original['settings'])
            body.update(session_id=None, experiment_version='Q1', counselor_version='P3a',
                        thinking_mode=name,
                        counselor_system_prompt=catalog.specification('P3a')['system_prompt'],
                        counselor_reasoning_budgets={'main': None, 'subagent': None},
                        generation=cfg['generation'], speaker_modes={'client': 'manual', 'counselor': 'model'})
            body['scenario'].pop('dialogue_turns', None)
            body['scenario'].pop('selected_counselor_utterance', None)
            body['scenario'].update(dialogue_format='messages', dialogue=[])
            body['messages'] = [{'role': 'user', 'content': '오늘 상담에서 가장 먼저 다뤄보고 싶은 일이 있나요?'}]
            session = read_api('/api/paired/create', body)
            assert session['connection']['chat_template_kwargs']['enable_thinking'] is enabled
            sessions[name] = session
            manifest['conditions'][name] = {'session_id': session['session_id'], 'enabled': enabled, 'settings': body}
            write(OUT / 'manifest.json', manifest)
            print('CREATED', name, session['session_id'], flush=True)
        for turn, utterance in enumerate(INPUTS, 1):
            for name in (['on', 'off'] if turn % 2 else ['off', 'on']):
                session = sessions[name]
                if session.get('_failed'):
                    continue
                sid = session['session_id']
                manual = read_api('/api/paired/manual', {'session_id': sid, 'expected_step': len(session['steps']),
                                                         'actor': 'client', 'content': utterance})
                session = manual['session']
                terminal = None
                started = time.monotonic()
                with api('/api/paired/step', {'session_id': sid, 'expected_step': len(session['steps']),
                                              'request_id': f'ab_{name}_{turn}'}) as response:
                    for line in response:
                        if not line.strip():
                            continue
                        e = json.loads(line).get('event', {})
                        if e.get('type') in ('done', 'error', 'cancelled'):
                            terminal = e
                elapsed = time.monotonic() - started
                session = read_api('/api/paired/session?session_id=' + sid)
                attempt = session['attempts'][-1]
                log = json.loads(Path(attempt['saved_path']).read_text())
                row = extract(log, elapsed, name, turn, sid, cfg)
                assert all(c['thinking_enabled'] is (name == 'on') for c in row['calls'])
                row['saved_path'] = attempt['saved_path']
                row['terminal'] = None if terminal is None else terminal['type']
                write(OUT / name / f'turn_{turn}.json', row)
                for filename in ['notes.md', 'counselor_notes.md']:
                    shutil.copy2(OUT / 'storage/paired' / sid / filename, OUT / name / f'turn_{turn}_{filename}')
                sessions[name] = session
                if not terminal or terminal['type'] != 'done':
                    sessions[name]['_failed'] = True
                else:
                    manifest['completed'].append({'condition': name, 'turn': turn})
                write(OUT / 'manifest.json', manifest)
                print('TURN', name, turn, row['status'], round(elapsed, 1),
                      'calls', len(row['calls']), 'reasoning_tokens', sum(c['reasoning_tokens_retokenized'] for c in row['calls']),
                      'tools', [(t['name'], t.get('error')) for t in row['tools']],
                      'RESPONSE', (row['response'] or '')[:180].replace('\n', ' '), flush=True)
        manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
        write(OUT / 'manifest.json', manifest)
        print('FINISHED', len(manifest['completed']), '/8 counselor turns', flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
