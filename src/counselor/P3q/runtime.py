"""P3q: one counselor utterance plus an analysis-only mark of which earlier client turn it revisits.

The mark is stored on the counselor side (checkpoint, and the batch step entry). It is never part
of `public_dialogue`, so it cannot reach the client input.
"""
import copy
import json
from pathlib import Path
import re

import runtime.user_templates as user_templates
from runtime.cancellation import Cancelled

HERE = Path(__file__).resolve().parent
VERSION = 'P3q'
SCHEMA = {'type': 'object', 'properties': {
    'utterance': {'type': 'string', 'minLength': 1},
    'revisit': {'type': 'object', 'properties': {
        'is_revisit': {'type': 'boolean'},
        'client_turn': {'type': 'integer', 'minimum': 0},
        'quote': {'type': 'string'}},
        'required': ['is_revisit', 'client_turn', 'quote'], 'additionalProperties': False}},
    'required': ['utterance', 'revisit'], 'additionalProperties': False}


def specification():
    return {'id': VERSION, 'label': 'P3q · 앞서 한 말 되짚어 묻기', 'view': 'none',
            'description': '공개 대화만 읽고 내담자가 앞서 한 말을 되짚어 당시 마음을 묻습니다. 되짚은 턴은 분석용으로만 기록합니다.',
            'system_prompt': (HERE / 'system.md').read_text(encoding='utf-8')}


def numbered(public):
    """Turn N = Nth counselor utterance and the client reply to it."""
    lines, turn = [], 0
    for row in public:
        if row['role'] == 'counselor':
            turn += 1
        lines.append(f"[{turn}턴] {'내담자' if row['role'] == 'client' else '상담사'}: {row['content']}")
    return '\n\n'.join(lines)


def squash(text):
    return re.sub(r'\s+', '', text)


def record(parsed, public):
    """Normalise the model's mark. An unusable mark is kept as invalid, never guessed."""
    clients = [row['content'] for row in public if row['role'] == 'client']
    mark = parsed['revisit']
    row = {'counselor_turn': sum(r['role'] == 'counselor' for r in public) + 1,
           'is_revisit': bool(mark['is_revisit']), 'client_turn': None, 'quote': mark['quote'].strip(),
           'quote_found': None, 'valid': True, 'raw': copy.deepcopy(mark)}
    if not row['is_revisit']:
        row['quote'] = ''
        return row
    turn = mark['client_turn']
    # A revisit points before the latest client utterance.
    if not 1 <= turn < len(clients):
        row['valid'] = False
        return row
    row['client_turn'] = turn
    row['quote_found'] = bool(row['quote']) and squash(row['quote']) in squash(clients[turn - 1])
    return row


def run(config, session, backend, base_build, emit, cancelled, directory):
    public = session['public_dialogue']
    phase = VERSION + ' 발화 생성'
    payload = base_build(config, {'system_prompt': session['settings']['counselor_system_prompt'],
        'messages': [{'role': 'user', 'content': user_templates.render('counselor', VERSION, dialogue=numbered(public))}],
        'generation': copy.deepcopy(config['generation'])}, [], False)
    payload['structured_outputs'] = {'json': copy.deepcopy(SCHEMA)}
    if cancelled():
        raise Cancelled('상담사 생성을 중단했습니다.')
    emit('request', phase=phase, label=VERSION, iteration=1, payload=copy.deepcopy(payload))
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
        raise ValueError(VERSION + ' 모델 응답이 정상 완료되지 않았습니다: ' + str(finish))
    emit('model_result', phase=phase, iteration=1, response=output, reasoning=reasoning, finish_reason=finish)
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as error:
        raise ValueError(VERSION + ' 응답이 JSON이 아닙니다: ' + str(error)) from None
    mark = parsed.get('revisit') if isinstance(parsed, dict) else None
    if (not isinstance(parsed, dict) or not isinstance(parsed.get('utterance'), str) or not parsed['utterance'].strip()
            or not isinstance(mark, dict) or type(mark.get('is_revisit')) is not bool
            or type(mark.get('client_turn')) is not int or not isinstance(mark.get('quote'), str)):
        raise ValueError(VERSION + ' 응답 형식이 올바르지 않습니다.')
    utterance = parsed['utterance'].strip()
    row = record(parsed, public)
    previous = session.get('counselor_checkpoint') or {}
    checkpoint = {'version': VERSION, 'revisits': [*copy.deepcopy(previous.get('revisits', [])), row]}
    emit('version_event', version=VERSION, section='revisit', action='recorded', data=row)
    return utterance, checkpoint, usage
