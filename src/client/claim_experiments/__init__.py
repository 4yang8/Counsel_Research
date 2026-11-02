"""Q3-based, separately registered research-claim experiments."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

CONDITIONS = {
    'summary_only': '현재 상태·목표·의도 포함 노트',
    'intent_linked': '발화·시점에 연결된 당시 의도',
    'intent_unlinked': '동일한 당시 의도 · 명시적 발화 연결 제거',
    'linked_prose': '발화 연결을 유지한 서술형 기록',
    'retrospective': '공개 대화로 사후 재구성한 의도',
}
SPECS = {
    'Q3p6': ('C6', '공통 경로 자동 분기와 당시 내면 기억', 'summary_only',
             ('summary_only', 'intent_linked')),
    'Q3p1': ('C1', '요약의 실패 조건', 'summary_only',
             ('summary_only', 'intent_linked', 'linked_prose')),
    'Q3p2': ('C2', '발화–의도 연결', 'intent_linked',
             ('intent_linked', 'intent_unlinked', 'linked_prose')),
    'Q3p3': ('C3', '당시 기록과 사후 해석', 'retrospective',
             ('retrospective', 'intent_linked')),
    'Q3p4': ('C4', '상담 개입 반응', 'intent_linked',
             ('summary_only', 'intent_linked')),
    'Q3p5': ('C5', '상담 진전 평가', 'intent_linked',
             ('summary_only', 'intent_linked')),
}


def condition(version, body):
    value = body.get('claim_memory_condition', SPECS[version][2])
    if not isinstance(value, str) or value not in SPECS[version][3]:
        raise ValueError(version + '의 기억 대조 조건을 확인하세요.')
    return value


def create_runtime(version, here):
    # Execute the unchanged Q3 pipeline in an isolated module. Relative imports
    # keep using Q3's helpers; locks, tool definitions and globals are per version.
    source = Path(__file__).parent.parent / 'Q3' / 'runtime.py'
    spec = spec_from_file_location('client.Q3._' + version, source)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    claim, label, _, _ = SPECS[version]
    module.VERSION, module.HERE = version, here
    module.OPTIONS = dict(condition=version, label=version + ' · ' + label,
                          select_notes=False, retrieve=True, turn_goal=True)
    if version == 'Q3p6':
        from client.Q3p6.adapter import install
    else:
        from .runtime import install
    install(module, claim)
    return module
