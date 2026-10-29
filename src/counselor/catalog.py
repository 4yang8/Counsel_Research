"""Version metadata selects settings and a pluggable UI renderer."""
from pathlib import Path
from . import P1, P2, P3, P3a, P3a_s1

from project_paths import PROJECT_ROOT
HERE = PROJECT_ROOT
PRACTICE_VERSIONS = {'P1': P1, 'P2': P2, 'P3': P3, 'P3a': P3a, 'P3a_s1': P3a_s1}


def specifications():
    return [
        {'id': 'basic', 'label': '기본 상담사 · 단일 프롬프트', 'view': 'none',
         'description': '공개 대화와 시스템 지침으로 상담사 발화 하나를 생성합니다.',
         'system_prompt': (HERE / 'resources/prompts/basic_counselor.md').read_text(encoding='utf-8')},
        {'id': 'C4', 'label': 'C4 · 관찰 / 행동 선택 / 발화', 'view': 'c4',
         'description': '공개 정보 관찰 → 상담 행동 선택 → 발화 생성과 작업 기록을 표시합니다.'},
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
