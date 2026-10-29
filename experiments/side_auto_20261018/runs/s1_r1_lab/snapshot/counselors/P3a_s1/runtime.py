"""P3a: a separate model selects guidance for the main counselor's request."""
import copy
import hashlib
from pathlib import Path
import uuid

from mcp_client import Cancelled
from side_experiments.tool_loop import run_turn
from ..P1.runtime import write
from .._side.runtime import NOTES, initialize as initialize_notes, run_notes, view_state
from ..P3.runtime import TOOL as NOTES_TOOL

HERE = Path(__file__).resolve().parent
GUIDE_NAMES = ('routing', 'common', 'mi', 'cbt')
GUIDE_FILES = {name: HERE / 'guides' / (name + '.md') for name in GUIDE_NAMES}
SUBAGENT_PROMPTS = {
    'guide_agent': {'label': '상담 가이드 서브에이전트', 'filename': 'guide_agent.md'},
}
NOTE_TEMPLATES = {target: HERE / filename for target, filename in (
    ('client', 'notes_template.md'), ('counselor', 'counselor_notes_template.md'))}
TOOL = copy.deepcopy(NOTES_TOOL)
GUIDE_TOOL = {'type': 'function', 'function': {
    'name': 'consult_counseling_guide',
    'description': (
        '현재 상황의 상담 진행 방법을 별도 서브에이전트에게 자문합니다. '
        '서브에이전트는 전달받은 요청과 상세 가이드 전체를 읽고 상담 진행 방향과 다음 행동을 간결하게 제안합니다. '
        '기법 선택·유지·전환 판단이 애매하면 routing을, 기법 선택 후에는 매 턴 해당 활용 가이드를 요청하세요. '
        'request에는 필요한 공개 대화·내담자 반응·현재 작업과 궁금한 점을 담으세요. '
        '메인의 전체 대화와 노트는 자동으로 공유되지 않습니다. '
        '같은 턴에 받은 결과로 충분하면 재사용할 수 있습니다.'),
    'parameters': {'type': 'object', 'properties': {
        'guide': {'type': 'string', 'enum': list(GUIDE_NAMES),
                  'description': '요청의 중심: routing은 기법 선택, common / mi / cbt는 해당 기법의 활용.'},
        'request': {'type': 'string',
                    'description': '현재 상황과 관련 공개 발화·이전 반응·진행 중인 작업을 포함한 구체적인 상담 방법 질문.'}},
        'required': ['guide', 'request'], 'additionalProperties': False}}}


def initialize(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for target, (filename, _) in NOTES.items():
        path = directory / filename
        if not path.exists():
            write(path, NOTE_TEMPLATES[target].read_text(encoding='utf-8'))
    return initialize_notes(directory)


def specification():
    return {'id': 'P3a_s1', 'label': 'P3a_s1 · 상세 가이드 / 서브에이전트', 'view': 'dual_notes',
            'description': 'P3의 두 노트를 유지하며, 같은 모델의 별도 서브에이전트가 상세 가이드에서 상황에 필요한 지침을 선별합니다.',
            'system_prompt': (HERE / 'system.md').read_text(encoding='utf-8'),
            'subagent_prompts': [{'kind': kind, 'label': item['label']} for kind, item in SUBAGENT_PROMPTS.items()],
            'tools': copy.deepcopy([TOOL, GUIDE_TOOL])}


def guidance_input(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {'guide', 'request'}:
        raise ValueError('guide와 request를 입력하세요.')
    guide, request = arguments['guide'], arguments['request']
    if not isinstance(guide, str) or guide not in GUIDE_NAMES:
        raise ValueError('guide는 routing, common, mi, cbt 중 하나여야 합니다.')
    if not isinstance(request, str) or not request.strip():
        raise ValueError('request에 현재 상황과 상담 방법에 대한 질문을 입력하세요.')
    # Every consultation sees all four current files. No keyword filtering,
    # character truncation, or Python choice of the applicable guideline.
    documents, sources = [], []
    for name, path in GUIDE_FILES.items():
        try:
            raw = path.read_bytes()
            content = raw.decode('utf-8')
        except (OSError, UnicodeError) as error:
            raise ValueError('상세 가이드 파일을 읽지 못했습니다: ' + name) from error
        if not content.strip():
            raise ValueError('상세 가이드 파일이 비어 있습니다: ' + name)
        documents.append('## 상세 가이드 · ' + name + '\n\n' + content)
        sources.append({'guide': name, 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()})
    user = ('# 메인 상담사의 요청\n\n## 요청의 중심\n' + guide + '\n\n## 상황과 질문\n' + request
            + '\n\n# 참고 가이드 전체\n\n' + '\n\n'.join(documents))
    return user, sources


def run(config, session, backend, base_build, emit, cancelled, directory):
    initialize(directory)
    current_call = {}
    child_usage = {}

    def forward(kind, **data):
        if kind == 'tool_start':
            current_call.update(call_id=data['call_id'], iteration=data.get('iteration'))
        emit(kind, **data)

    def consult(name, arguments):
        if name != GUIDE_TOOL['function']['name']:
            raise ValueError('도구는 update_notes 또는 consult_counseling_guide를 사용하세요.')
        if cancelled():
            raise Cancelled('가이드 서브에이전트 실행 전에 중단했습니다.')
        user, sources = guidance_input(arguments)
        role = (HERE / 'guide_agent.md').read_text(encoding='utf-8')
        payload = base_build(config, {'system_prompt': role, 'messages': [{'role': 'user', 'content': user}],
                                      '_counselor_budget_role': 'subagent'}, [], False)
        meta = {'agent_id': 'guide_' + uuid.uuid4().hex, 'agent_name': '상담 가이드 선별 담당',
                'agent_kind': 'counseling_guide', 'parent_agent_id': 'main',
                'parent_call_id': current_call.get('call_id'), 'depth': 1,
                'role_instruction': role, 'role_source': 'developer'}
        final = {}

        def send(kind, **data):
            if kind == 'chunk':
                for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                    value = (data['data'].get('usage') or {}).get(key)
                    if isinstance(value, int):
                        child_usage[key] = child_usage.get(key, 0) + value
            if kind == 'done':
                final.update(data)
                if data.get('finish_reason') != 'stop':
                    return
            emit('agent_event', **meta, event={'type': kind, **data})

        send('request', phase='상황에 필요한 상세 가이드 선별', iteration=1,
             payload=copy.deepcopy(payload), guide_sources=sources)
        try:
            child_config = {key: value for key, value in config.items() if key != '_prepare_payload'}
            run_turn(child_config, payload, None, backend.upstream, backend.stream_events,
                     backend.inspect_prompt, send, cancelled, [{'role': 'user', 'content': user}])
            if final.get('finish_reason') != 'stop':
                raise ValueError('가이드 서브에이전트 응답이 정상 완료되지 않았습니다: ' + str(final.get('finish_reason')))
        except Cancelled as error:
            send('cancelled', error=str(error))
            raise
        except Exception as error:
            send('error', error=str(error))
            raise
        guidance = final['response']
        return {'content': [{'type': 'text', 'text': guidance}],
                'structuredContent': {'agent_id': meta['agent_id'], 'guide': arguments['guide']}}

    response, checkpoint, usage = run_notes(config, session, backend, base_build, forward, cancelled, directory,
                                          version='P3a_s1', tool=TOOL, extra_tools=[GUIDE_TOOL], extra_tool_call=consult)
    total = dict(usage)
    for key, value in child_usage.items():
        total[key] = total.get(key, 0) + value
    return response, checkpoint, total
