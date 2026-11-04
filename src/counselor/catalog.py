"""Version metadata selects settings and a pluggable UI renderer."""
from pathlib import Path
from . import P1, P2, P3, P3a, P3a_s1, Q3calmit_counsel

from project_paths import PROJECT_ROOT
HERE = PROJECT_ROOT
PRACTICE_VERSIONS = {'P1': P1, 'P2': P2, 'P3': P3, 'P3a': P3a, 'P3a_s1': P3a_s1, 'Q3calmit_counsel': Q3calmit_counsel}
SINGLE_PROMPT_PATHS = {
    'Q3p6_counsel': 'src/counselor/Q3p6_counsel/system.md',
    'basic': 'resources/prompts/basic_counselor.md',
    'Q3p1_counsel': 'src/counselor/Q3p1_counsel/system.md',
    'Q3p2_counsel': 'src/counselor/Q3p2_counsel/system.md',
    'Q3p4_counsel': 'src/counselor/Q3p4_counsel/system.md',
}


def specifications():
    return [
        {'id': 'Q3p6_counsel', 'label': 'Q3p6_counsel · 공통 경로', 'view': 'none',
         'description': '공개 정보로 공통 상담을 진행합니다. 분기 개입은 별도 실행기가 고정합니다.',
         'system_prompt': (HERE / SINGLE_PROMPT_PATHS['Q3p6_counsel']).read_text(encoding='utf-8')},
        {'id': 'basic', 'label': '기본 상담사 · 단일 프롬프트', 'view': 'none',
         'description': '공개 대화와 시스템 지침으로 상담사 발화 하나를 생성합니다.',
         'system_prompt': (HERE / SINGLE_PROMPT_PATHS['basic']).read_text(encoding='utf-8')},
        {'id': 'C4', 'label': 'C4 · 관찰 / 행동 선택 / 발화', 'view': 'c4',
         'description': '공개 정보 관찰 → 상담 행동 선택 → 발화 생성과 작업 기록을 표시합니다.'},
        {'id': 'Q3p1_counsel', 'label': 'Q3p1_counsel · 단일 프롬프트', 'view': 'none',
         'description': '공개 대화 속 과거 동의·망설임의 당시 의미와 현재 마음을 자연스럽게 확인합니다.',
         'system_prompt': (HERE / SINGLE_PROMPT_PATHS['Q3p1_counsel']).read_text(encoding='utf-8')},
        {'id': 'Q3p2_counsel', 'label': 'Q3p2_counsel · 단일 프롬프트', 'view': 'none',
         'description': '서로 다른 장면의 비슷한 동의·보류 표현을 구분해 당시 의미와 후속 의향을 확인합니다.',
         'system_prompt': (HERE / SINGLE_PROMPT_PATHS['Q3p2_counsel']).read_text(encoding='utf-8')},
        {'id': 'Q3p4_counsel', 'label': 'Q3p4_counsel · 단일 프롬프트', 'view': 'none',
         'description': '제안과 반응, 간격 대화 후 선택한 개입으로 과거 반응을 후속 상담에 연결합니다.',
         'intervention_conditions': [
             {'id': 'A', 'label': 'A · 실행 의향을 전제하고 구체화'},
             {'id': 'B', 'label': 'B · 현재 의향을 확인하고 조정'}],
         'default_intervention_condition': 'A',
         'system_prompt': (HERE / SINGLE_PROMPT_PATHS['Q3p4_counsel']).read_text(encoding='utf-8')},
        *[module.specification() for module in PRACTICE_VERSIONS.values()]]


def specification(version):
    return next((row for row in specifications() if row['id'] == version), None)


def runner(version):
    module = PRACTICE_VERSIONS.get(version)
    return module.run if module else None


def initialize(version, directory):
    module = PRACTICE_VERSIONS.get(version)
    if module and hasattr(module, 'initialize'):
        module.initialize(directory)


def view_state(version, directory):
    module = PRACTICE_VERSIONS.get(version)
    return module.view_state(directory) if module and hasattr(module, 'view_state') else {}
