"""One response × independent rubrics, configured by run_eval.sh environment."""
from datetime import datetime
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid

HERE = Path(__file__).resolve().parent
RUBRICS = {
    '01': ('01_과거이유의충실성', 'past_reason_fidelity'),
    '02': ('02_과거와현재의구분', 'past_present_distinction'),
    '03': ('03_상담개입반응의타당성', 'intervention_response_validity'),
    '04': ('04_공개보류의적절성', 'disclosure_appropriateness'),
}
STATUSES = ('scored', 'insufficient_evidence', 'not_applicable')


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_input(data):
    require(isinstance(data, dict), '입력은 JSON 객체여야 합니다.')
    require(set(data) == {'case_id', 'response_id', 'profile', 'history',
                         'reference_state_before_target', 'target'},
            '평가기용 입력 JSON을 사용하세요. 원본 사례·제작자 메모·정답은 입력하지 않습니다.')
    require(text(data['case_id']) and text(data['response_id']), '사례·답변 ID가 필요합니다.')
    require(isinstance(data['profile'], (dict, str)), 'profile 형식이 잘못됐습니다.')
    require(isinstance(data['history'], list), 'history는 배열이어야 합니다.')
    target = data['target']
    require(isinstance(target, dict) and set(target) == {
        'turn', 'counselor_utterance', 'client_utterance', 'generated_inner'}, 'target 필드를 확인하세요.')
    require(type(target['turn']) is int and target['turn'] > 0, '대상 턴 번호가 잘못됐습니다.')
    require(all(text(target[k]) for k in ('counselor_utterance', 'client_utterance')), '대상 발화가 비어 있습니다.')
    require(target['generated_inner'] is None or isinstance(target['generated_inner'], (dict, str)), 'generated_inner 형식 오류')
    previous = 0
    for row in data['history']:
        require(isinstance(row, dict) and set(row) == {
            'turn', 'counselor_utterance', 'client_utterance', 'private_inner'}, 'history 항목의 필드를 확인하세요.')
        require(type(row['turn']) is int and previous < row['turn'] < target['turn'],
                '과거 턴은 중복·미래 참조 없이 시간순이어야 합니다.')
        require(all(text(row[k]) for k in ('counselor_utterance', 'client_utterance')), '과거 발화가 비어 있습니다.')
        require(row['private_inner'] is None or isinstance(row['private_inner'], (dict, str)), 'private_inner 형식 오류')
        previous = row['turn']
    state = data['reference_state_before_target']
    require(state is None or (isinstance(state, dict) and set(state) == {'content', 'source'}
                             and text(state['source'])), '별도 상태 자료에는 content와 source가 필요합니다.')
    return data


# Translate structural keys, never rewrite or summarize the underlying record text.
RECORD_LABELS = {
    'name': '이름', 'age': '나이', 'occupation': '직업', 'gender': '성별',
    'presenting_problem': '상담 주제', 'session_context': '상담 상황',
    'background': '배경', 'personality': '성격', 'traits': '특성', 'goals': '목표',
    'appraisal': '상황 해석', 'interpretation': '해석',
    'immediate_feeling': '즉각적인 감정', 'relevant_evidence': '판단 근거',
    'uncertainty': '불확실한 점', 'turn_goal': '이번 발화의 목표',
    'intention': '의도', 'grounds': '의도의 근거', 'response_plan': '응답 계획',
    'action': '표현할 내용과 방식', 'reason': '이유', 'content': '내용', 'source': '출처',
}


def record_text(value, depth=0):
    """Render nested records with readable labels, keeping every value in order."""
    if value is None:
        return '제공되지 않음'
    if isinstance(value, dict):
        if not value:
            return '제공되지 않음'
        lines = []
        for key, item in value.items():
            label = RECORD_LABELS.get(key)
            if label is None:
                # Allow human-readable Korean labels; never silently drop unknown fields.
                require(bool(re.search(r'[가-힣]', key)) and not re.search(r'[_.\[\]]', key),
                        '자연어 항목명 매핑이 필요합니다: ' + key + ' (RECORD_LABELS에 추가하거나 한국어 항목명을 사용하세요.)')
                label = key
            indent = '  ' * depth
            if isinstance(item, (dict, list)) and item:
                lines.append(indent + label + ':\n' + record_text(item, depth + 1))
            else:
                lines.append(indent + label + ': ' + record_text(item, depth + 1))
        return '\n'.join(lines)
    if isinstance(value, list):
        return '\n'.join('  ' * depth + '- ' + record_text(item, depth + 1)
                         for item in value) if value else '제공되지 않음'
    if isinstance(value, bool):
        return '예' if value else '아니요'
    return str(value)


def format_evaluation_input(data):
    """Build the shared model input from content only; IDs stay in the runner."""
    lines = ['[내담자 프로필]', record_text(data['profile']), '', '[과거 대화와 당시 내면 기록]']
    if not data['history']:
        lines.append('제공되지 않음')
    for row in data['history']:
        lines.extend(['', f"[{row['turn']}턴]",
                      '상담사 발화: ' + row['counselor_utterance'],
                      '내담자 발화: ' + row['client_utterance'],
                      '당시 내면 기록:\n' + record_text(row['private_inner'])])
    lines.extend(['', '[별도로 확보한 평가 대상 직전 상태]'])
    state = data['reference_state_before_target']
    if state is None:
        lines.append('제공되지 않음')
    else:
        lines.extend(['내용:\n' + record_text(state['content']), '출처: ' + state['source']])
    target = data['target']
    lines.extend(['', f"[평가 대상: {target['turn']}턴]",
                  '상담사 발화: ' + target['counselor_utterance'],
                  '평가할 내담자 발화: ' + target['client_utterance'],
                  '이번 발화의 내면 기록（생성 결과·보조 자료）:\n' + record_text(target['generated_inner'])])
    return '\n'.join(lines)


def schema_for():
    string = {'type': 'string'}
    fields = {
        'status': {'type': 'string', 'enum': list(STATUSES)},
        'score': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 5},
        'rationale': string,
    }
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def validate_result(result):
    require(isinstance(result, dict) and set(result) == set(schema_for()['properties']), '평가 결과 필드가 잘못됐습니다.')
    require(result['status'] in STATUSES, '판정 상태가 잘못됐습니다.')
    if result['status'] == 'scored':
        require(type(result['score']) is int and 1 <= result['score'] <= 5, '채점 결과는 1~5 정수여야 합니다.')
    else:
        require(result['score'] is None, '판정 불가·해당 없음의 점수는 null이어야 합니다.')
    require(text(result['rationale']), '판단 이유가 비어 있습니다.')
    return result


def parse_result(raw):
    raw = raw.strip()
    if raw.startswith('```json\n') and raw.endswith('```'):
        raw = raw[8:-3].strip()
    elif raw.startswith('```\n') and raw.endswith('```'):
        raw = raw[4:-3].strip()
    return json.loads(raw)


def codex_env():
    env = os.environ.copy()
    # This backend intentionally reuses ChatGPT login, not an API billing override.
    for key in ('CODEX_API_KEY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL'):
        env.pop(key, None)
    return env


def codex_call(config, system, user, schema, folder):
    save(folder / 'output_schema.json', schema)
    with tempfile.TemporaryDirectory(prefix='counsel-eval-') as isolated:
        command = [config['codex_bin'], 'exec', '--ignore-user-config', '--ephemeral',
                   '--skip-git-repo-check', '--sandbox', 'read-only', '-C', isolated,
                   '-m', config['model'], '-c', 'model_provider="openai"',
                   '-c', 'model_reasoning_effort=' + json.dumps(config['effort']),
                   '-c', 'developer_instructions=' + json.dumps(system, ensure_ascii=False),
                   '-c', 'project_doc_max_bytes=0', '-c', 'web_search="disabled"',
                   '-c', 'features.shell_tool=false', '-c', 'features.multi_agent=false',
                   '-c', 'features.view_image=false',
                   '--output-schema', str(folder / 'output_schema.json'),
                   '--output-last-message', str(folder / 'raw_response.txt'), '--json', '-']
        save(folder / 'command.json', command)
        instruction = ('제공된 평가 데이터와 지침만 사용하세요. 외부 검색, 파일 조회, 도구 호출은 하지 마세요. '
                       '지정된 평가 JSON만 최종 반환하세요.\n\n' + user)
        with (folder / 'events.jsonl').open('w') as stdout, (folder / 'stderr.log').open('w') as stderr:
            result = subprocess.run(command, input=instruction, text=True, stdout=stdout, stderr=stderr,
                                    env=codex_env(), timeout=config['timeout'])
        if result.returncode != 0:
            detail = (folder / 'stderr.log').read_text(errors='replace').strip().splitlines()
            raise ValueError('codex exec 실패(exit=' + str(result.returncode) + '): ' +
                             (detail[-1][:400] if detail else '오류 설명 없음'))
        for line in (folder / 'events.jsonl').read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') in ('turn.failed', 'error'):
                detail = event.get('error', event.get('message', '응답 생성 실패'))
                if isinstance(detail, dict):
                    detail = detail.get('message', '응답 생성 실패')
                raise ValueError('Codex 오류: ' + str(detail)[:400])
            item = event.get('item') or {}
            require(item.get('type') not in ('command_execution', 'mcp_tool_call', 'web_search', 'file_change'),
                    '평가 외 도구 사용이 관찰되어 이 판정을 제외합니다.')
        return (folder / 'raw_response.txt').read_text()


def vllm_call(config, system, user, schema, folder):
    payload = {'model': config['model'], 'messages': [{'role': 'system', 'content': system},
               {'role': 'user', 'content': user}], 'temperature': config['temperature'],
               'max_tokens': config['max_tokens'], 'stream': False, 'structured_outputs': {'json': schema}}
    save(folder / 'request.json', payload)
    headers = {'Content-Type': 'application/json'}
    if config['api_key_env']:
        key = os.environ.get(config['api_key_env'])
        require(bool(key), '설정한 API 키 환경변수가 비어 있습니다.')
        headers['Authorization'] = 'Bearer ' + key
    request = Request(config['url'] + '/chat/completions',
                      data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
    try:
        with urlopen(request, timeout=config['timeout']) as response:
            result = json.load(response)
    except HTTPError as error:
        detail = error.read(2000).decode(errors='replace')
        try:
            parsed = json.loads(detail)
            detail = parsed.get('error', parsed)
            if isinstance(detail, dict):
                detail = detail.get('message', detail)
        except ValueError:
            pass
        raise ValueError('vLLM HTTP ' + str(error.code) + ': ' + str(detail)[:400]) from error
    save(folder / 'raw_response.json', result)
    choice = result['choices'][0]
    require(choice.get('finish_reason') == 'stop', 'vLLM 응답 미완료: ' + str(choice.get('finish_reason')))
    raw = choice['message']['content']
    require(text(raw), 'vLLM 평가 응답이 비어 있습니다.')
    (folder / 'raw_response.txt').write_text(raw)
    return raw


def settings():
    mode = os.environ.get('EVAL_MODE', 'codex')
    require(mode in ('codex', 'vllm'), 'EVAL_MODE는 codex 또는 vllm입니다.')
    selection = os.environ.get('CRITERIA', 'all').strip()
    criteria = list(RUBRICS) if selection == 'all' else [x.strip() for x in selection.split(',')]
    require(bool(criteria) and len(set(criteria)) == len(criteria) and all(x in RUBRICS for x in criteria), 'CRITERIA는 all 또는 01,02,03,04입니다.')
    dry = os.environ.get('DRY_RUN', 'false').lower()
    require(dry in ('true', 'false'), 'DRY_RUN은 true 또는 false입니다.')
    config = {'mode': mode, 'criteria': criteria, 'dry_run': dry == 'true',
              'timeout': int(os.environ.get('TIMEOUT_SECONDS', '600')),
              'input': str(Path(os.environ.get('INPUT_FILE', str(HERE / '입력예시_01_response_1.json'))).expanduser().resolve()),
              'output': str(Path(os.environ.get('OUTPUT_DIR', str(HERE / 'results'))).expanduser().resolve())}
    require(config['timeout'] > 0, 'TIMEOUT_SECONDS는 양수입니다.')
    if mode == 'codex':
        config.update(codex_bin=os.environ.get('CODEX_BIN', 'codex'), model=os.environ.get('CODEX_MODEL', 'gpt-6-luna'),
                      effort=os.environ.get('CODEX_EFFORT', 'max'))
        require(config['effort'] in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'), 'CODEX_EFFORT 설정을 확인하세요.')
    else:
        url = os.environ.get('VLLM_URL', 'http://127.0.0.1:18001/v1').rstrip('/')
        parts = urlsplit(url)
        require(parts.scheme in ('http', 'https') and bool(parts.hostname) and not parts.username
                and not parts.password and not parts.query and not parts.fragment, 'VLLM_URL 형식을 확인하세요.')
        if not parts.path or parts.path == '/':
            url += '/v1'
        config.update(url=url, model=os.environ.get('VLLM_MODEL', ''),
                      temperature=float(os.environ.get('VLLM_TEMPERATURE', '0')),
                      max_tokens=int(os.environ.get('VLLM_MAX_TOKENS', '8192')),
                      api_key_env=os.environ.get('VLLM_API_KEY_ENV', ''))
        require(config['max_tokens'] > 0 and 0 <= config['temperature'] <= 2, 'vLLM 생성 설정을 확인하세요.')
    require(text(config['model']), '평가 모델 이름이 비어 있습니다.')
    return config


def md_cell(value):
    return html.escape(str(value), quote=False).replace('\\', '\\\\').replace('|', '\\|').replace('\r\n', '\n').replace('\n', '<br>')


def report_filename(case_id):
    # Input IDs are display data, never arbitrary output paths.
    safe = re.sub(r'[^\w.-]+', '_', case_id).strip('._')[:100] or 'case'
    return safe + '.md'


def write_report(path, data, config, entries):
    labels = dict(zip((item[1] for item in RUBRICS.values()),
                      ('과거 이유의 충실성', '과거와 현재의 구분', '상담 개입 반응의 타당성', '공개·보류의 적절성')))
    statuses = {'not_applicable': '해당 없음', 'insufficient_evidence': '판정 불가'}
    lines = ['# 평가 결과 · ' + md_cell(data['case_id']), '',
             '- 평가 모델: ' + md_cell(config['mode']) + ' / ' + md_cell(config['model']),
             '- Effort: ' + md_cell(config.get('effort', '해당 없음')), '',
             '## ' + str(data['target']['turn']) + '턴 · ' + md_cell(data['response_id']), '',
             '| 평가 항목 | 점수·판정 | 이유 |', '|---|---|---|']
    for row in entries:
        if row['execution'] == 'success':
            result = row['result']
            verdict = str(result['score']) + '점' if result['status'] == 'scored' else statuses[result['status']]
            reason = result['rationale']
        elif row['execution'] in ('failed', 'interrupted'):
            verdict = '실행 실패' if row['execution'] == 'failed' else '중단'
            reason = row['error']
        elif row['execution'] == 'prepared':
            verdict, reason = '호출 안 함', '입력·프롬프트 구성 확인만 진행했습니다.'
        elif row['execution'] == 'running':
            verdict, reason = '평가 중', ''
        else:
            verdict, reason = '대기', ''
        lines.append('| ' + ' | '.join(md_cell(x) for x in
                     (labels[row['criterion_id']], verdict, reason)) + ' |')
    path.write_text('\n'.join(lines) + '\n')


def error_reason(error, config):
    if isinstance(error, subprocess.TimeoutExpired):
        return '평가 호출이 제한 시간 ' + str(config['timeout']) + '초를 초과했습니다.'
    return type(error).__name__ + ': ' + str(error)[:600]


def main():
    config = settings()
    data = validate_input(json.loads(Path(config['input']).read_text()))
    evaluation_text = format_evaluation_input(data)
    prompts = []
    for number in config['criteria']:
        stem, criterion = RUBRICS[number]
        system = (HERE / (stem + '_system.md')).read_text()
        template = (HERE / (stem + '_user.md')).read_text()
        require(template.count('{{evaluation_input_text}}') == 1, 'user prompt의 입력 자리표시자는 하나여야 합니다.')
        prompts.append((number, criterion, system, template.replace('{{evaluation_input_text}}', evaluation_text)))
    if config['mode'] == 'codex' and not config['dry_run']:
        result = subprocess.run([config['codex_bin'], 'login', 'status'], capture_output=True, text=True, env=codex_env(), timeout=30)
        require(result.returncode == 0 and 'ChatGPT' in result.stdout + result.stderr,
                'ChatGPT 로그인 확인이 필요합니다. codex login 또는 codex login --device-auth를 실행하세요.')
    out = Path(config['output']) / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + config['mode'] + '_' + uuid.uuid4().hex[:6])
    out.mkdir(parents=True, exist_ok=False)
    report = out / report_filename(data['case_id'])
    print('결과 파일:', report, flush=True)
    entries = [{'criterion_id': criterion, 'execution': 'pending'}
               for _, criterion, _, _ in prompts]
    write_report(report, data, config, entries)
    for index, (number, criterion, system, user) in enumerate(prompts, 1):
        row = entries[index - 1]
        row['execution'] = 'running'
        write_report(report, data, config, entries)
        print(f'[{index}/{len(prompts)}] {criterion} 시작', flush=True)
        try:
            if config['dry_run']:
                row['execution'] = 'prepared'
            else:
                # Backend transport files live only for this call, including on failure.
                with tempfile.TemporaryDirectory(prefix='counsel-eval-artifacts-') as work:
                    folder = Path(work)
                    raw = (codex_call if config['mode'] == 'codex' else vllm_call)(
                        config, system, user, schema_for(), folder)
                    result = validate_result(parse_result(raw))
                    result = {**result, 'criterion_id': criterion,
                              'case_id': data['case_id'], 'response_id': data['response_id']}
                    row.update(execution='success', result=result)
        except KeyboardInterrupt:
            row.update(execution='interrupted', error='사용자가 평가를 중단했습니다.')
            write_report(report, data, config, entries)
            raise
        except Exception as error:
            row.update(execution='failed', error=error_reason(error, config))
        write_report(report, data, config, entries)
        print(f'[{index}/{len(prompts)}] {row["execution"]} · {round(index / len(prompts) * 100)}%', flush=True)
    print('평가 결과:', report, flush=True)
    return 1 if any(r['execution'] == 'failed' for r in entries) else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('실행 오류:', error, file=sys.stderr)
        sys.exit(2)
