"""P2: independent client facts and counselor working notes, refreshed per turn."""
import runtime.user_templates as user_templates
import copy
import json
from pathlib import Path

from ..P1.runtime import TOOL as P1_TOOL, apply_edits, read, revision, write
from runtime.recovery_tool_loop import run_turn

HERE = Path(__file__).resolve().parent
NOTES = {
    'client': ('notes.md', '내담자 노트'),
    'counselor': ('counselor_notes.md', '상담사 생각 노트'),
}
TOOL = copy.deepcopy(P1_TOOL)
TOOL['function']['description'] = (
    '현재 세션의 내담자 노트(client) 또는 상담사 생각 노트(counselor)에 내용을 추가·수정·삭제합니다. '
    'client에는 대화에서 확인된 내담자 정보를, counselor에는 상담사의 현재 이해·미확인 사항·이어갈 계획을 기록합니다. '
    '두 노트 원문은 기본 입력 하단에 제공되어 있으므로 별도 조회가 필요하지 않습니다. '
    '변경이 필요한 노트 하나를 target으로 선택하고 변경할 부분만 edits에 작성하세요. '
    '같은 턴에서 이미 수정했다면 도구 결과의 변경 내용을 반영한 원문을 기준으로 수정하세요. '
    '변경이 없으면 호출하지 않아도 됩니다. 저장 결과를 확인한 뒤 상담 발화를 이어갑니다.'
)
TOOL['function']['parameters']['properties']['target'] = {
    'type': 'string', 'enum': list(NOTES),
    'description': '수정할 노트: client는 내담자 정보, counselor는 상담사 생각과 계획',
}
TOOL['function']['parameters']['required'] = ['target', 'edits']


def specification():
    return {'id': 'P2', 'label': 'P2 · 내담자 / 상담사 생각 노트', 'view': 'dual_notes',
            'description': '매 턴 두 노트 원문을 읽고, 모델이 필요한 파일을 선택해 수정합니다. 이전 턴 reasoning은 재입력하지 않습니다.',
            'system_prompt': (HERE / 'system.md').read_text(encoding='utf-8'),
            'tools': [copy.deepcopy(TOOL)]}


def initialize(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for target, (filename, _) in NOTES.items():
        path = directory / filename
        if not path.exists():
            write(path, '')
        read(path)
        paths[target] = path
    return paths


def view_state(directory):
    result = {}
    for target, (filename, label) in NOTES.items():
        path = Path(directory) / filename
        content = read(path)
        result[target + '_notes'] = {'current': {
            'target': target, 'label': label, 'path': str(path),
            'content': content, 'revision': revision(content)}, 'history': []}
    return result


def run(config, session, backend, base_build, emit, cancelled, directory):
    return run_notes(config, session, backend, base_build, emit, cancelled, directory, version='P2', tool=TOOL)


def run_notes(config, session, backend, base_build, emit, cancelled, directory, *, version, tool,
              extra_tools=(), extra_tool_call=None):
    """Shared file/tool mechanics; each version supplies its own instructions."""
    replace = session['settings'].get('side_note_mode') == 'replace'
    if replace:
        tool = copy.deepcopy(tool)
        tool['function']['description'] = (
            '내담자 노트(client) 또는 상담사 생각 노트(counselor)의 전체 내용을 갱신합니다. '
            'client에는 공개 대화에서 확인한 내담자 정보와 요청·경계를, counselor에는 현재 이해·접근·진행 상황·다음 대응을 기록하세요. '
            '현재 원문은 입력 하단에 있습니다. 기존의 유효한 내용과 고정 제목을 유지하면서 추가·정정·불필요한 내용 정리를 반영한 전체 원문을 content에 제출하세요. '
            '상담사의 가설은 내담자가 확인한 사실과 구분하세요. 변경할 필요가 없으면 호출하지 않아도 됩니다. 저장 결과를 확인한 뒤 발화를 이어가세요.')
        tool['function']['parameters'] = {'type': 'object', 'properties': {
            'target': {'type': 'string', 'enum': ['client', 'counselor']},
            'content': {'type': 'string', 'description': '선택한 노트의 갱신 후 전체 원문. 고정 제목과 유효한 기존 내용을 유지하세요.'}},
            'required': ['target', 'content'], 'additionalProperties': False}
    paths = initialize(directory)
    public = '\n\n'.join(('내담자' if row['role'] == 'client' else '상담사') + ': ' + row['content']
                         for row in session['public_dialogue'])
    user = '## 공개 대화\n\n' + public
    payload = base_build(config, {'system_prompt': session['settings']['counselor_system_prompt'],
                                 'messages': [{'role': 'user', 'content': user}]},
                         copy.deepcopy([tool, *extra_tools]), False)
    expected = {}

    def prepare(current, iteration):
        if iteration != 1:
            return
        values = {'dialogue': public}
        for target, path in paths.items():
            note = read(path)
            expected[target] = revision(note)
            label = NOTES[target][1]
            values[target + '_notes'] = note
            emit('version_event', version=version, section=target + '_notes', action='snapshot', iteration=iteration,
                 data={'target': target, 'label': label, 'path': str(path), 'content': note, 'revision': expected[target]})
        next(message for message in current['messages'] if message['role'] == 'user')['content'] = user_templates.render('counselor', version, **values)
        emit('request', phase=version + ' 상담사 · 발화 / 두 노트' + ('·가이드' if extra_tools else '') + ' 도구 선택',
             iteration=iteration, payload=copy.deepcopy(current))

    class Notes:
        opts = {'max_calls': config.get('delegation', {}).get('max_calls', 8), 'max_result_chars': 16000}

        def call(self, name, arguments):
            self.opts['max_result_chars'] = 16000
            if name != 'update_notes':
                if extra_tool_call is None:
                    raise ValueError(version + '는 update_notes만 제공합니다.')
                result = extra_tool_call(name, arguments)
                # Document readers return the entire file, including files that
                # exceed the shared loop's default result-display limit.
                self.opts['max_result_chars'] = max(16000, len(json.dumps(result, ensure_ascii=False)))
                return result
            fields = {'target', 'content'} if replace else {'target', 'edits'}
            if not isinstance(arguments, dict) or set(arguments) != fields:
                raise ValueError('target과 ' + ('content' if replace else 'edits') + '를 입력하세요.')
            target = arguments['target']
            if not isinstance(target, str) or target not in paths:
                raise ValueError('target은 client 또는 counselor여야 합니다.')
            if replace:
                content = arguments['content']
                if not isinstance(content, str) or not content.strip() or len(content) > 16000:
                    raise ValueError('content에는 비어 있지 않은 간결한 전체 노트 원문을 입력하세요 (16,000자 이하).')
                edits = [{'old_text': read(paths[target]), 'new_text': content}]
            else:
                edits = arguments['edits']
            change = apply_edits(paths[target], {'edits': edits}, expected[target])
            expected[target] = change['revision']
            emit('version_event', version=version, section=target + '_notes', action='updated',
                 data={'target': target, 'label': NOTES[target][1], 'path': str(paths[target]), **change})
            result = {'saved': True, 'target': target, 'revision': change['revision']}
            result.update({'content': arguments['content']} if replace else {'edits': arguments['edits']})
            return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}]}

    final = {}

    def event(kind, **data):
        if kind == 'done':
            final.update(data)
            emit('model_result', iteration=data.get('iteration'), response=data['response'],
                 reasoning=data.get('reasoning', ''), finish_reason=data['finish_reason'])
        else:
            emit(kind, **data)

    run_turn({**config, '_prepare_payload': prepare}, payload, Notes(), backend.upstream,
             backend.stream_events, backend.inspect_prompt, event, cancelled,
             [{'role': 'user', 'content': user}])
    if final.get('finish_reason') != 'stop':
        raise ValueError(version + ' 응답이 정상 완료되지 않았습니다. 이미 저장한 노트 변경은 유지됩니다.')
    return final['response'].strip(), None, final.get('usage', {})
