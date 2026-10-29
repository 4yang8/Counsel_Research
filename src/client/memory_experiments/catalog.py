"""Independent experiment runtimes, backed by one shared implementation."""
import threading
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
from client.Q2a_agentic_memory_F.settings import OPTIONS as F_OPTIONS

SPECS = {
    'Q2a_note_select': {'condition':'B','label':'B · 관련 노트 선택','select_notes':True,'retrieve':False,'turn_goal':False},
    'Q2a_note_retrieve': {'condition':'C','label':'C · 노트 선택 + 능동 조회','select_notes':True,'retrieve':True,'turn_goal':False},
    'Q2a_turn_goal': {'condition':'D','label':'D · 대화 의도 / 표현 계획','select_notes':False,'retrieve':False,'turn_goal':True},
    'Q2a_agentic_memory': {'condition':'E','label':'E · 노트 선택 + 조회 + 대화 의도','select_notes':True,'retrieve':True,'turn_goal':True},
    'Q2a_agentic_memory_F': dict(F_OPTIONS),
    'Q3b': {'condition':'Q3b','label':'Q3b · 선택적 계획·기록 최적화','select_notes':True,'retrieve':True,'turn_goal':True},
    'Q3a': {'condition':'Q3a','label':'Q3a · 턴별 로컬 플래닝','select_notes':True,'retrieve':True,'turn_goal':True},
    'Q3': {'condition':'Q3','label':'Q3 · 신뢰 형성 궤적','select_notes':True,'retrieve':True,'turn_goal':True},
}

_RUNTIMES={}
_LOCK=threading.RLock()

def get_runtime(version):
    with _LOCK:
        if version not in _RUNTIMES:_RUNTIMES[version]=_create_runtime(version)
        return _RUNTIMES[version]

def _create_runtime(version):
    if version=='Q3b':
        from client.Q3b import runtime
        return runtime
    if version=='Q3a':
        from client.Q3a import runtime
        return runtime
    if version=='Q3':
        from client.Q3 import runtime
        return runtime
    if version=='Q2a_agentic_memory_F':
        from client.Q2a_agentic_memory_F import runtime
        return runtime
    options=SPECS[version]
    source=Path(__file__).with_name('runtime.py')
    spec=spec_from_file_location('client.memory_experiments.'+version,source)
    module=module_from_spec(spec)
    # Each version owns its globals and lock; no mutable cross-version switches.
    module.VERSION=version
    module.OPTIONS=dict(options)
    module.HERE=source.parent.parent/version
    spec.loader.exec_module(module)
    return module
