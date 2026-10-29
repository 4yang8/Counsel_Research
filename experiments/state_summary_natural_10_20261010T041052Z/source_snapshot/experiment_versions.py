"""Small registry: basic keeps its existing runtime; new versions opt in."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = ('basic', 'state_summary')


def name(config, body=None):
    value = (body or {}).get('experiment_version', config.get('experiment_version', 'basic'))
    if value not in NAMES:
        raise ValueError('지원하지 않는 실험 버전입니다: ' + str(value))
    return value


def catalog(config):
    return {'default': name(config), 'versions': [
        {'id': 'basic', 'label': 'basic · 기존 실습',
         'description': '초기 상태를 유지하고 기존 방식으로 반응을 생성합니다.',
         'system_prompt': config.get('system_prompt', ''), 'summary_system_prompt': ''},
        {'id': 'state_summary', 'label': 'state_summary · 내면상태 요약·조회',
         'description': '매 상담 턴 새 프롬프트를 구성하고, 누적 내면 흐름 요약과 의미 중심 원문 조회를 사용합니다.',
         'system_prompt': (HERE / 'versions/state_summary/system.md').read_text(encoding='utf-8'),
         'summary_system_prompt': (HERE / 'versions/state_summary/summary.md').read_text(encoding='utf-8')}
    ]}


def configure(config, body):
    config['_experiment_version'] = name(config, body)
    config['_request_body'] = body
    config['_defer_initial_request'] = config['_experiment_version'] == 'state_summary'
