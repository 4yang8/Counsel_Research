"""Q3 recording experiments: isolated runtime globals, shared decision pipeline."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODES = {'Q3n': ('raw', '원문 선택 기록'),
         'Q3sb': ('summary', '요약 서브에이전트 기록'),
         'Q3x': ('snapshot', 'Python 판단 스냅샷')}


def create_runtime(version, here):
    mode, label = MODES[version]
    spec = spec_from_file_location(__name__ + '._' + version, Path(__file__).with_name('runtime.py'))
    module = module_from_spec(spec)
    module.VERSION, module.HERE, module.MODE = version, here, mode
    module.OPTIONS = dict(condition=version, label=version + ' · ' + label,
                          select_notes=True, retrieve=True, turn_goal=True)
    spec.loader.exec_module(module)
    return module
