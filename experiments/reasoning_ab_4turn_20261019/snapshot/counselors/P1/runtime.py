"""P1: model-chosen note edits, injected from disk before every model call."""
import copy
import difflib
import hashlib
import json
import os
from pathlib import Path
import uuid

from tool_loop import run_turn

HERE = Path(__file__).resolve().parent
LIMIT = 65536
TOOL = {'type': 'function', 'function': {
    'name': 'update_notes',
    'description': '현재 세션의 상담 노트 파일에 새 내용을 추가하거나 기존 내용을 수정·삭제합니다. '
                   '현재 노트 원문은 기본 입력 하단에 이미 제공되어 있으므로 별도 조회가 필요하지 않습니다. '
                   '기존 노트를 보고 새로운 기록이나 변경이 필요하다고 판단할 때 사용하세요. '
                   '이미 같은 내용이 기록되어 있고 바뀐 내용이 없으면 호출하지 않아도 됩니다. '
                   '변경할 부분만 edits에 작성하세요. 저장 결과를 확인한 뒤 상담 발화를 이어갑니다.',
    'parameters': {'type': 'object', 'properties': {
        'edits': {'type': 'array', 'minItems': 1, 'maxItems': 30, 'description': '순서대로 적용할 노트 변경 목록',
                  'items': {'type': 'object', 'properties': {
                      'old_text': {'type': 'string', 'description': '변경할 기존 원문을 정확히 복사합니다. 빈 문자열이면 노트 끝에 추가합니다.'},
                      'new_text': {'type': 'string', 'description': '새로 추가하거나 대체할 내용. 빈 문자열이면 old_text를 삭제합니다.'}},
                      'required': ['old_text', 'new_text'], 'additionalProperties': False}}},
        'required': ['edits'], 'additionalProperties': False}}}


def specification():
    return {'id': 'P1', 'label': 'P1 · 파일 기반 노트 실습', 'view': 'notes',
            'description': '매 호출 현재 노트를 입력하고, 모델이 필요한 노트 추가·수정을 도구로 실행합니다.',
            'system_prompt': (HERE / 'system.md').read_text(encoding='utf-8'), 'tools': [copy.deepcopy(TOOL)]}


def view_state(directory):
    path = Path(directory) / 'notes.md'
    content = read(path)
    return {'notes': {'current': {'path': str(path), 'content': content, 'revision': revision(content)}, 'history': []}}


def read(path):
    if path.is_symlink():
        raise ValueError('노트 파일은 심볼릭 링크일 수 없습니다.')
    content = path.read_text(encoding='utf-8')
    if len(content.encode('utf-8')) > LIMIT:
        raise ValueError('노트 파일 크기는 64 KB 이하여야 합니다.')
    return content


def revision(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def write(path, content):
    if path.is_symlink():
        raise ValueError('노트 파일은 심볼릭 링크일 수 없습니다.')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def initialize(directory):
    path = Path(directory) / 'notes.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write(path, '')
    read(path)
    return path


def change_set(before, after):
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile='이전 노트', tofile='변경된 노트', lineterm=''))
    return {'diff': lines,
            'added': [line[1:] for line in lines if line.startswith('+') and not line.startswith('+++')],
            'deleted': [line[1:] for line in lines if line.startswith('-') and not line.startswith('---')]}


def apply_edits(path, arguments, expected):
    if not isinstance(arguments, dict) or set(arguments) != {'edits'}:
        raise ValueError('edits만 입력하세요.')
    edits = arguments['edits']
    if not isinstance(edits, list) or not 1 <= len(edits) <= 30:
        raise ValueError('edits는 1~30개 변경 목록이어야 합니다.')
    before = read(path)
    if revision(before) != expected:
        raise ValueError('모델 입력 이후 노트 파일이 변경되었습니다. 갱신된 입력의 원문을 확인하세요.')
    after = before
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'old_text', 'new_text'} or any(not isinstance(v, str) for v in edit.values()):
            raise ValueError('각 변경에는 old_text/new_text 문자열이 필요합니다.')
        old, new = edit['old_text'], edit['new_text']
        if not old and not new:
            raise ValueError('빈 변경은 저장하지 않습니다.')
        if not old:
            after += ('\n' if after and not after.endswith('\n') else '') + new
        elif after.count(old) != 1:
            raise ValueError('old_text는 현재 노트에 정확히 한 번 존재해야 합니다.')
        else:
            after = after.replace(old, new, 1)
    if len(after.encode('utf-8')) > LIMIT:
        raise ValueError('노트 파일 크기는 64 KB 이하여야 합니다.')
    write(path, after)
    return {'before': before, 'content': after, 'revision': revision(after), **change_set(before, after)}


def run(config, session, backend, base_build, emit, cancelled, directory):
    path = initialize(directory)
    public = '\n\n'.join(('내담자' if row['role'] == 'client' else '상담사') + ': ' + row['content'] for row in session['public_dialogue'])
    user = '## 공개 대화\n\n' + public
    prompt = session['settings']['counselor_system_prompt']
    payload = base_build(config, {'system_prompt': prompt, 'messages': [{'role': 'user', 'content': user}]}, [TOOL], False)
    expected = None

    def prepare(current, iteration):
        nonlocal expected
        # Freeze the base note for this counselor turn. Tool replies carry edits;
        # the next counselor turn reads the updated file into its new base input.
        if iteration != 1:
            return
        content = read(path);expected = revision(content)
        next(message for message in current['messages'] if message['role'] == 'user')['content'] = user + '\n\n## 상담 노트 · 현재 파일 원문\n\n' + content
        emit('version_event', version='P1', section='notes', action='snapshot', iteration=iteration,
             data={'path': str(path), 'content': content, 'revision': expected})
        if iteration == 1:
            emit('request', phase='P1 상담사 · 발화 / 노트 도구 선택', iteration=iteration, payload=copy.deepcopy(current))

    class Notes:
        opts = {'max_calls': config.get('delegation', {}).get('max_calls', 8), 'max_result_chars': 16000}
        def call(self, name, arguments):
            nonlocal expected
            if name != 'update_notes':
                raise ValueError('P1은 update_notes만 제공합니다.')
            change = apply_edits(path, arguments, expected)
            expected = change['revision']
            emit('version_event', version='P1', section='notes', action='updated',
                 data={'path': str(path), **change})
            # Keep this turn's base input fixed; report the saved edits via role:tool.
            return {'content': [{'type': 'text', 'text': json.dumps({'saved': True, 'revision': change['revision'], 'edits': arguments['edits']}, ensure_ascii=False)}]}

    final = {}
    def event(kind, **data):
        if kind == 'done':
            final.update(data)
            emit('model_result', iteration=data.get('iteration'), response=data['response'], reasoning=data.get('reasoning', ''), finish_reason=data['finish_reason'])
        else:
            emit(kind, **data)

    cfg = {**config, '_prepare_payload': prepare}
    run_turn(cfg, payload, Notes(), backend.upstream, backend.stream_events, backend.inspect_prompt, event, cancelled,
             [{'role': 'user', 'content': user}])
    if final.get('finish_reason') != 'stop':
        raise ValueError('P1 응답이 정상 완료되지 않았습니다. 이미 저장한 노트 변경은 유지됩니다.')
    return final['response'].strip(), None, final.get('usage', {})
