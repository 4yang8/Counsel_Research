"""Matched JSON/Markdown input probes; production lab files stay untouched."""
import copy
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = pathlib.Path(__file__).resolve().parent
SOURCE = ROOT / 'experiments/state_required_5_20261008'
sys.path.insert(0, str(ROOT))
import app_server
import server as backend


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def data_fields(scenario):
    return {key: scenario[key] for key in
            ('dialogue', 'profile', 'states', 'taxonomy', 'source_notes') if key in scenario}


def markdown_data(data):
    """Keep original field names, list order, values, and nesting."""
    lines = []

    def render(value, depth):
        items = value.items() if isinstance(value, dict) else enumerate(value, 1)
        for key, child in items:
            prefix = '  ' * depth + '- ' + str(key) + ':'
            if isinstance(child, (dict, list)):
                lines.append(prefix)
                render(child, depth + 1)
            else:
                text = child if isinstance(child, str) else json.dumps(child, ensure_ascii=False)
                lines.append(prefix + ' ' + text)

    for key, value in data.items():
        lines.extend(['## ' + key, ''])
        render(value, 0)
        lines.append('')
    return '\n'.join(lines).rstrip()


JSON_CONTEXT = app_server.runtime.initial_context


def md_context(scenario):
    return (scenario['task'] + '\n\n[초기 자료: 이번 상담자 발화 직전]\n'
            + markdown_data(data_fields(scenario)))


def canonical_request(request, scenario, fmt):
    request = copy.deepcopy(request)
    user = next(message for message in request['messages'] if message['role'] == 'user')
    context = JSON_CONTEXT(scenario) if fmt == 'json' else md_context(scenario)
    expected = context + '\n\n[이번 상담자 발화]\n' + scenario['example_counselor_utterance']
    assert user['content'] == expected, 'Unexpected content passed to model'
    user['content'] = '<INITIAL DATA FORMAT ONLY>'
    return request


def main():
    refresh = sys.argv[1:] == ['--refresh-md']
    if not refresh and ((OUT / 'results.json').exists() or list(OUT.glob('pair_*/*/sessions/*/*.json'))):
        raise RuntimeError('Existing experiment cannot be overwritten')
    prior = json.loads((OUT / 'results.json').read_text()) if refresh else []
    if refresh:
        assert len(prior) == 6 and all(row['status'] == 'completed' for row in prior)
        assert not any((OUT / f'pair_{pair:02d}' / 'md_compact').exists() for pair in range(1, 4))
        dump(OUT / 'preliminary_results.json', prior)
    protected = ['runtime.py', 'tools.json', 'config.json', 'cases/lab_meeting.json']
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in protected}
    manifest = {
        'hypothesis': 'Initial-data serialization alone may affect state appraisal, tool selection, and utterances.',
        'source': str(SOURCE),
        'pairs': [1, 4, 2],
        'pair_order': [['json', 'md'], ['md', 'json'], ['json', 'md']],
        'system_prompt': '주어진 툴을 적절히 활용하세요.',
        'seed': 731,
        'scope': 'Three independent continuations, one matched sample per format and state combination.',
        'changed': 'Only initial dialogue/profile/states/taxonomy/source_notes serialization; keys, values and order retained.',
        'unchanged': 'Task, system prompt, tools, tool result format, generation parameters and counselor utterance.',
        'limitations': 'Small exploratory probe; no repeated seeds, causal verdict or broader generalization.',
        'production_hashes_before': hashes,
        'harness': 'Direct invocation of the same runtime/build/tool-loop/logging functions; no listening server.',
        'markdown_layout': 'Top-level headings, nested bullet lists with two-space indentation; original field names retained.',
        'excluded_preliminary_outputs': 3 if refresh else 0,
        'refinement': 'First Markdown layout had ambiguous parent scope after nested headings. Retain its three outputs as preliminary; rerun Markdown only with explicit nested bullet scope.' if refresh else None,
    }
    dump(OUT / 'manifest.json', manifest)
    snapshot = OUT / 'code_snapshot'
    snapshot.mkdir(exist_ok=True)
    for name in ['runtime.py', 'tool_loop.py', 'server.py', 'app_server.py', 'tools.json']:
        (snapshot / name).write_bytes((ROOT / name).read_bytes())
    results = [row for row in prior if row['format'] == 'json']
    for pair, source_id in enumerate(manifest['pairs'], 1):
        folder = OUT / f'pair_{pair:02d}'
        folder.mkdir(exist_ok=True)
        source = SOURCE / f'case_{source_id:02d}'
        scenario = json.loads((source / 'scenario.json').read_text())
        config = json.loads((source / 'config.json').read_text())
        dump(folder / 'scenario.json', scenario)
        dump(folder / 'state_basis.json', json.loads((source / 'state_basis.json').read_text()))
        config.update(scenario_file=str(folder / 'scenario.json'),
                      state_basis_file=str(folder / 'state_basis.json'))
        config['generation']['seed'] = 731
        dump(folder / 'config.json', config)
        body = {'messages': [{'role': 'user', 'content': scenario['example_counselor_utterance']}],
                'system_prompt': manifest['system_prompt']}
        dump(folder / 'input.json', body)
        reference = None
        if refresh:
            original_json = next(row for row in results if row['pair'] == pair)
            saved = json.loads(pathlib.Path(original_json['log']).read_text())
            reference = canonical_request(saved['model_calls'][0]['request'], scenario, 'json')
        for fmt in (['md'] if refresh else manifest['pair_order'][pair - 1]):
            target = folder / ('md_compact' if refresh else fmt)
            target.mkdir(exist_ok=True)
            app_server.runtime.initial_context = JSON_CONTEXT if fmt == 'json' else md_context
            (target / 'initial_context.txt').write_text(app_server.runtime.initial_context(scenario), encoding='utf-8')
            print(json.dumps({'started': [pair, fmt], 'levels': [v['level'] for v in scenario['states'].values()]}, ensure_ascii=False), flush=True)
            try:
                cfg = backend.read_config(folder / 'config.json')
                cfg['scenario'] = app_server.runtime.load_scenario(cfg)
                cfg['_experience_case_id'] = scenario['id']
                cfg['_request_states'] = copy.deepcopy(scenario['states'])
                payload = app_server.runtime.build(cfg, body, [], backend.build_payload)
                journal = backend.TurnLog(target / 'sessions', body)

                def emit(kind, **fields):
                    event = {'type': kind, **fields}
                    if kind == 'agent_event':
                        journal.state.setdefault('subagent_events', []).append(copy.deepcopy(event))
                    journal.append(event)

                emit('request', payload=copy.deepcopy(payload), iteration=1,
                     url=backend.base_url(cfg) + '/v1/chat/completions',
                     saved_path=str(journal.path), session_id=journal.state['session_id'])
                try:
                    app_server.runtime.execute(
                        cfg, copy.deepcopy(payload), None, backend.upstream,
                        backend.stream_events, backend.inspect_prompt, emit, lambda: False,
                        backend.validate_messages(body['messages']), backend.build_payload, 'dynamic')
                except Exception as error:
                    emit('error', error=backend.error_text(error))
                log = journal.path
                session = json.loads(log.read_text())
                first = session['model_calls'][0]
                normalized = canonical_request(first['request'], scenario, fmt)
                if reference is None:
                    reference = normalized
                assert normalized == reference, 'Other input fields changed between formats'
                starts = [e for e in session.get('tool_events', []) if e['type'] == 'tool_start']
                errors = [e for e in session.get('tool_events', []) if e['type'] == 'tool_result' and e.get('is_error')]
                row = {
                    'pair': pair, 'format': fmt, 'levels': {k: v['level'] for k, v in scenario['states'].items()},
                    'status': session['status'], 'response': session.get('response'),
                    'finish_reason': session.get('finish_reason'), 'error': session.get('error'),
                    'tool_calls': [{'name': e['name'], 'arguments': e.get('arguments')} for e in starts],
                    'tool_errors': len(errors), 'model_calls': len(session.get('model_calls', [])),
                    'first_input_tokens': first.get('usage', {}).get('prompt_tokens'),
                    'inspection_tokens': first.get('inspection', {}).get('token_count'),
                    'usage': session.get('usage'), 'log': str(log), 'format_only_verified': True,
                }
                dump(target / 'result.json', row)
                results.append(row)
                dump(OUT / 'results.json', results)
                print(json.dumps(row, ensure_ascii=False), flush=True)
            finally:
                app_server.runtime.initial_context = JSON_CONTEXT
    after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in protected}
    assert after == hashes, 'Production files changed'
    results.sort(key=lambda row: (row['pair'], row['format']))
    dump(OUT / 'results.json', results)
    manifest.update(completed=len(results), completed_successfully=sum(row['status'] == 'completed' for row in results), production_files_unchanged=True)
    dump(OUT / 'manifest.json', manifest)


if __name__ == '__main__':
    main()
