"""P3: model-owned approach selection and work continuity through two notes."""
import copy
from pathlib import Path

from ..P1.runtime import write
from ..P2.runtime import NOTES, TOOL as P2_TOOL, initialize as initialize_notes, run_notes, view_state

HERE = Path(__file__).resolve().parent
NOTE_TEMPLATES = {target: HERE / filename for target, filename in (
    ('client', 'notes_template.md'), ('counselor', 'counselor_notes_template.md'))}
GUIDE_DIR = HERE.parents[1] / 'docs' / 'counseling_guides'
GUIDE_FILES = {name: GUIDE_DIR / (name + '.md') for name in ('routing', 'common', 'mi', 'cbt')}
TOOL = copy.deepcopy(P2_TOOL)
TOOL['function']['description'] = (
    '새 정보·정정 또는 상담사의 판단·계획 변경을 노트에 추가·수정·삭제할 때 사용합니다. '
    '두 노트 원문은 기본 입력 하단에 있으므로 별도 조회가 필요하지 않습니다. '
    'target으로 노트를 선택하고 변경할 부분만 edits에 작성하세요. '
    '같은 턴에 저장한 변경은 도구 결과를 반영한 원문을 기준으로 수정하세요. '
    '달라진 내용이 없으면 호출하지 않아도 됩니다. 저장 결과를 확인한 뒤 상담 발화를 이어갑니다.'
)
TOOL['function']['parameters']['properties']['target']['description'] = (
    'client: 내담자 상태 노트(확인된 내담자 정보). counselor: 상담사 생각 노트(판단·기법·작업 상태·계획).'
)
GUIDE_TOOL = {'type': 'function', 'function': {
    'name': 'read_counseling_guide',
    'description': (
        '선택한 상담 가이드 파일의 원문 전체를 반환합니다. '
        'routing은 기법 선택·유지·전환 판단이 애매할 때 조회합니다. '
        '기법 선택 후에는 매 턴 common, mi, cbt 중 해당 활용 가이드를 반드시 조회하세요. '
        '단순하거나 익숙한 상황도 생략하지 않으며, 같은 턴에 이미 읽은 결과는 재사용할 수 있습니다.'),
    'parameters': {'type': 'object', 'properties': {
        'guide': {'type': 'string', 'enum': list(GUIDE_FILES),
                  'description': 'routing: 기법 선택 기준. common / mi / cbt: 선택한 기법의 행동 선택·수행·재평가 가이드.'}},
        'required': ['guide'], 'additionalProperties': False}}}


def initialize(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for target, (filename, _) in NOTES.items():
        path = directory / filename
        if not path.exists():
            write(path, NOTE_TEMPLATES[target].read_text(encoding='utf-8'))
    return initialize_notes(directory)


def call_guide(name, arguments):
    if name != GUIDE_TOOL['function']['name']:
        raise ValueError('도구는 update_notes 또는 read_counseling_guide를 사용하세요.')
    if not isinstance(arguments, dict) or set(arguments) != {'guide'}:
        raise ValueError('guide만 입력하세요: routing, common, mi, cbt.')
    guide = arguments['guide']
    if not isinstance(guide, str) or guide not in GUIDE_FILES:
        raise ValueError('guide는 routing, common, mi, cbt 중 하나여야 합니다.')
    try:
        content = GUIDE_FILES[guide].read_text(encoding='utf-8')
    except (OSError, UnicodeError) as error:
        raise ValueError('가이드 파일을 읽지 못했습니다: ' + guide) from error
    return {'content': [{'type': 'text', 'text': content}]}


def specification():
    return {'id': 'P3', 'label': 'P3 · Common / MI / CBT · 작업 연속성', 'view': 'dual_notes',
            'description': '같은 상담사 모델이 두 노트와 대화를 보고 기법을 재평가하고, 가이드 원문을 조회해 다음 행동을 정하며 작업의 연속성을 관리합니다.',
            'system_prompt': (HERE / 'system.md').read_text(encoding='utf-8'),
            'tools': copy.deepcopy([TOOL, GUIDE_TOOL])}


def run(config, session, backend, base_build, emit, cancelled, directory):
    initialize(directory)
    return run_notes(config, session, backend, base_build, emit, cancelled, directory, version='P3', tool=TOOL,
                     extra_tools=[GUIDE_TOOL], extra_tool_call=call_guide)
