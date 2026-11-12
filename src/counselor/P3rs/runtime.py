"""P3rs: one counselor utterance plus counselor-side notes on withdrawal markers and rupture exploration.

One structured-output call per utterance. Besides exploring markers it has noted, the counselor
sometimes checks how one of its own suggestions or interpretations landed (`check_in`), marker or not.
The runtime also lists the counselor's own suggestions, interpretations and tasks that it has not yet
checked (`unchecked`), so a check is not forgotten. The counselor's own notes of markers it has seen but not
yet explored are fed back into its next input. Notes come from the public dialogue only and are
stored on the counselor side (checkpoint, and the batch step entry), never in `public_dialogue`.
"""
import copy
import json
from pathlib import Path

import runtime.user_templates as user_templates
from counselor.P3q.runtime import numbered, squash
from runtime.cancellation import Cancelled

HERE = Path(__file__).resolve().parent
VERSION = 'P3rs'
MOVES = {'explore': '탐색', 'empathy': '공감·반영', 'suggest': '관점 제안·조언', 'interpret': '해석', 'task': '과제 제안',
         'rupture_explore': '균열 탐색', 'check_in': '개입 확인', 'repair': '회복 반응', 'other': '기타'}
KINDS = {'none': '없음', 'short': '짧아진 답', 'comply': '순응하는 동의', 'shift': '화제 전환',
         'abstract': '추상적인 말', 'deflect': '질문을 비켜 감', 'partial': '일부만 받고 넘어감'}
SCHEMA = {'type': 'object', 'properties': {
    'marker': {'type': 'object', 'properties': {
        'found': {'type': 'boolean'}, 'kind': {'type': 'string', 'enum': list(KINDS)}, 'quote': {'type': 'string'}},
        'required': ['found', 'kind', 'quote'], 'additionalProperties': False},
    'move': {'type': 'string', 'enum': list(MOVES)},
    'rupture_turn': {'type': 'integer', 'minimum': 0},
    'check_turn': {'type': 'integer', 'minimum': 0},
    'utterance': {'type': 'string', 'minLength': 1}},
    'required': ['marker', 'move', 'rupture_turn', 'check_turn', 'utterance'], 'additionalProperties': False}
EMPTY = '아직 탐색하지 않은 물러남 표지가 없습니다.'
EMPTY_UNCHECKED = '아직 되묻지 않은 내 제안·해석이 없습니다.'
INTERVENTIONS = ('suggest', 'interpret', 'task')


def specification():
    return {'id': VERSION, 'label': 'P3rs · 물러남 균열 탐색과 회복', 'view': 'none',
            'description': '공개 대화만 읽고 능동적으로 상담하며, 자기 개입이 어떻게 들렸는지 가끔 되묻고, 개입 뒤 물러남 표지를 메모했다가 그 순간으로 돌아가 탐색합니다.',
            'system_prompt': (HERE / 'system.md').read_text(encoding='utf-8')}


def asked(records):
    """How many times the counselor has gone back to ask how its words landed (check-in or rupture exploration)."""
    return sum(bool((row.get('check') or {}).get('counselor_turn') or row['explore'].get('client_turn')) for row in records)


def notes(pending, unchecked=(), count=None):
    """The counselor's own memo as shown in its next input: unexplored markers, unchecked interventions,
    and (when given) how many times it has asked so far, which the first-check rule relies on."""
    marks = '\n'.join(f"- {row['client_turn']}턴 내담자 답: \"{row['quote']}\" ({KINDS[row['kind']]}) · 내 {row['client_turn']}턴 발화 뒤"
                      for row in pending) or EMPTY
    mine = '\n'.join(f"- 내 {row['counselor_turn']}턴 발화 ({MOVES[row['move']]}): \"{row['utterance']}\""
                     for row in unchecked) or EMPTY_UNCHECKED
    text = f"### 아직 탐색하지 않은 물러남 표지\n\n{marks}\n\n### 아직 어떻게 들렸는지 묻지 않은 내 말\n\n{mine}"
    if count is not None:
        text += '\n\n### 지금까지 되물은 횟수\n\n' + (f'{count}회' if count else '0회 (아직 한 번도 되묻지 않았습니다)')
    return text


def record(parsed, public, pending, unchecked=()):
    """Normalise the model's notes. An unusable note is kept as invalid, never guessed.

    Turn N = Nth counselor utterance and the client reply to it, so the marker always belongs to the
    latest client reply and the counselor utterance that preceded it; the model supplies no turn for it.
    Returns the stored row, the next pending markers and the next unchecked interventions.
    """
    clients = [row['content'] for row in public if row['role'] == 'client']
    latest = len(clients)
    mark, move, target, checked = parsed['marker'], parsed['move'], parsed['rupture_turn'], parsed['check_turn']
    row = {'counselor_turn': sum(r['role'] == 'counselor' for r in public) + 1, 'move': move,
           'marker': {'found': False, 'client_turn': None, 'kind': 'none', 'quote': '', 'quote_found': None, 'valid': True},
           'explore': {'is_explore': move == 'rupture_explore', 'client_turn': None, 'gap': None, 'noted': None, 'valid': True},
           'check': {'is_check': move == 'check_in', 'counselor_turn': None, 'gap': None, 'noted': None, 'valid': True},
           'pending_before': copy.deepcopy(pending), 'unchecked_before': copy.deepcopy(list(unchecked)),
           'raw': {'marker': copy.deepcopy(mark), 'move': move, 'rupture_turn': target, 'check_turn': checked}}
    pending, unchecked = copy.deepcopy(pending), copy.deepcopy(list(unchecked))
    if mark['found']:
        quote = mark['quote'].strip()
        if not latest or mark['kind'] == 'none':
            row['marker'].update(found=True, valid=False)
        else:
            row['marker'].update(found=True, client_turn=latest, kind=mark['kind'], quote=quote,
                                 quote_found=bool(quote) and squash(quote) in squash(clients[-1]))
            pending.append({'client_turn': latest, 'kind': mark['kind'], 'quote': quote})
    # Either question asks about the same moment (counselor utterance N and the reply to it), so it clears both memos.
    asked = None
    if row['explore']['is_explore']:
        if not 1 <= target <= latest:
            row['explore']['valid'] = False
        else:
            # gap 0 = asked in the counselor utterance right after that client reply.
            row['explore'].update(client_turn=target, gap=latest - target,
                                  noted=any(p['client_turn'] == target for p in pending))
            asked = target
    if row['check']['is_check']:
        if not 1 <= checked <= latest:
            row['check']['valid'] = False
        else:
            row['check'].update(counselor_turn=checked, gap=latest - checked,
                                noted=any(u['counselor_turn'] == checked for u in unchecked))
            asked = checked
    if asked:
        pending = [p for p in pending if p['client_turn'] != asked]
        unchecked = [u for u in unchecked if u['counselor_turn'] != asked]
    if move in INTERVENTIONS:
        unchecked.append({'counselor_turn': row['counselor_turn'], 'move': move, 'utterance': parsed['utterance'].strip()})
    return row, pending, unchecked


def call(config, backend, base_build, emit, cancelled, system, user, schema, phase, label=VERSION, body=None):
    """One streamed structured-output call. Returns the parsed JSON object and token usage."""
    payload = base_build(config, {'system_prompt': system, 'messages': [{'role': 'user', 'content': user}],
                                  'generation': copy.deepcopy(config['generation']), **(body or {})}, [], False)
    payload['structured_outputs'] = {'json': copy.deepcopy(schema)}
    if cancelled():
        raise Cancelled('상담사 생성을 중단했습니다.')
    emit('request', phase=phase, label=label, iteration=1, payload=copy.deepcopy(payload))
    if config.get('inspect_prompt', True):
        try:
            emit('inspection', phase=phase, iteration=1, **backend.inspect_prompt(config, payload))
        except Exception as error:
            emit('inspection', phase=phase, iteration=1, error=str(error))
    output, reasoning, finish, complete, usage = '', '', None, False, {}
    with backend.upstream(config, '/v1/chat/completions', payload) as response:
        for frame in backend.stream_events(response):
            if cancelled():
                raise Cancelled('상담사 생성을 중단했습니다.')
            if frame == '[DONE]':
                complete = True
                break
            chunk = json.loads(frame)
            if chunk.get('error'):
                raise ValueError(str(chunk['error']))
            emit('chunk', phase=phase, iteration=1, data=chunk)
            for choice in chunk.get('choices', []):
                if choice.get('index', 0) == 0:
                    delta = choice.get('delta', {})
                    output += delta.get('content') or ''
                    reasoning += delta.get('reasoning_content') or delta.get('reasoning') or ''
                    finish = choice.get('finish_reason') or finish
            for key, value in (chunk.get('usage') or {}).items():
                if isinstance(value, int):
                    usage[key] = usage.get(key, 0) + value
    if not complete or finish != 'stop' or not output.strip():
        raise ValueError(label + ' 모델 응답이 정상 완료되지 않았습니다: ' + str(finish))
    emit('model_result', phase=phase, iteration=1, response=output, reasoning=reasoning, finish_reason=finish)
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as error:
        raise ValueError(label + ' 응답이 JSON이 아닙니다: ' + str(error)) from None
    if not isinstance(parsed, dict):
        raise ValueError(label + ' 응답 형식이 올바르지 않습니다.')
    return parsed, usage


def valid_marker(mark):
    return (isinstance(mark, dict) and type(mark.get('found')) is bool and mark.get('kind') in KINDS
            and isinstance(mark.get('quote'), str))


def valid_move(parsed):
    return (isinstance(parsed.get('utterance'), str) and bool(parsed['utterance'].strip()) and parsed.get('move') in MOVES
            and type(parsed.get('rupture_turn')) is int and type(parsed.get('check_turn')) is int)


def state(session, version):
    """Records, pending markers and unchecked interventions carried from this version's last checkpoint."""
    previous = session.get('counselor_checkpoint') or {}
    if previous.get('version') != version:
        return [], [], []
    return previous.get('records', []), previous.get('pending', []), previous.get('unchecked', [])


def run(config, session, backend, base_build, emit, cancelled, directory):
    public = session['public_dialogue']
    records, pending, unchecked = state(session, VERSION)
    user = user_templates.render('counselor', VERSION, dialogue=numbered(public), notes=notes(pending, unchecked, asked(records)))
    parsed, usage = call(config, backend, base_build, emit, cancelled, session['settings']['counselor_system_prompt'],
                         user, SCHEMA, VERSION + ' 발화 생성')
    if not valid_move(parsed) or not valid_marker(parsed.get('marker')):
        raise ValueError(VERSION + ' 응답 형식이 올바르지 않습니다.')
    row, pending, unchecked = record(parsed, public, pending, unchecked)
    checkpoint = {'version': VERSION, 'records': [*copy.deepcopy(records), row], 'pending': pending, 'unchecked': unchecked}
    emit('version_event', version=VERSION, section='rupture', action='recorded', data=row)
    return parsed['utterance'].strip(), checkpoint, usage
