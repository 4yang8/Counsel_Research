"""Small registry: basic keeps its existing runtime; new versions opt in."""
from pathlib import Path
from client.memory_experiments.catalog import SPECS as MEMORY_EXPERIMENTS, get_runtime as memory_runtime

from project_paths import PROJECT_ROOT
HERE = PROJECT_ROOT
NAMES = ('basic', 'state_summary', 'Q1', 'Q1_s1', 'Q1_s2', 'Q1a', 'Q2', 'Q2a', 'Q2a_variant_context', 'Q2a_trait_background', 'Q2a_prefill_test', *MEMORY_EXPERIMENTS)
MODEL_CHOICES = ('google/gemma-4-31B-it', 'Qwen/Qwen3.8-27B', 'Qwen/Qwen3.8-27B-FP8',
                 'Qwen/Qwen3.8-Flash-Next-FP8',
                 'bottlecapai/ThinkingCap-Qwen3.8-27B-FP8',
                 'ukisai/Swift-1.5-Qwen3.8-27b')
OPTIONAL_BLOCKS = ('system', 'task', 'profile', 'dialogue')


def client_runtime(version):
    if version in MEMORY_EXPERIMENTS:return memory_runtime(version)
    if version == 'Q1':
        from client.Q1 import runtime
        return runtime
    if version == 'Q2a_variant_context':
        from client.Q2a_variant_context import runtime
        return runtime
    if version == 'Q2a_prefill_test':
        from client.Q2a_prefill_test import runtime
        return runtime
    if version == 'Q2a_trait_background':
        from client.Q2a_trait_background import runtime
        return runtime
    if version == 'Q2a':
        from client.Q2a import runtime
        return runtime
    if version == 'Q2':
        from client.Q2 import runtime
        return runtime
    if version == 'Q1a':
        from client.Q1a import runtime
        return runtime
    if version == 'Q1_s1':
        from client.Q1_s1 import runtime
        return runtime
    if version == 'Q1_s2':
        from client.Q1_s2 import runtime
        return runtime
    return None


def input_blocks(config, body=None):
    values = (body or {}).get('input_blocks', config.get('_input_blocks', {}))
    if not isinstance(values, dict) or set(values) - set(OPTIONAL_BLOCKS) or any(type(v) is not bool for v in values.values()):
        raise ValueError('input_blocks는 system/task/profile/dialogue의 활성화 여부여야 합니다.')
    return {key: values.get(key, True) for key in OPTIONAL_BLOCKS}


def name(config, body=None):
    value = (body or {}).get('experiment_version', config.get('experiment_version', 'basic'))
    if value not in NAMES:
        raise ValueError('지원하지 않는 실험 버전입니다: ' + str(value))
    return value


def catalog(config):
    from client.Q1.runtime import specification
    return {'default': name(config), 'versions': [
        {'id': 'basic', 'label': 'basic · 기존 실습',
         'description': '초기 상태를 유지하고 기존 방식으로 반응을 생성합니다.',
         'system_prompt': config.get('system_prompt', ''), 'summary_system_prompt': ''},
        {'id': 'state_summary', 'label': 'state_summary · 내면상태 요약·조회',
         'description': '매 상담 턴 새 프롬프트를 구성하고, 누적 내면 흐름 요약과 의미 중심 원문 조회를 사용합니다.',
         'system_prompt': (HERE / 'src/client/state_summary/system.md').read_text(encoding='utf-8'),
         'summary_system_prompt': (HERE / 'src/client/state_summary/summary.md').read_text(encoding='utf-8')},
        specification(), client_runtime('Q1_s1').specification(), client_runtime('Q1_s2').specification(), client_runtime('Q1a').specification(), client_runtime('Q2').specification(), client_runtime('Q2a').specification(), client_runtime('Q2a_variant_context').specification(), {**client_runtime('Q2a_trait_background').specification(), 'label':'A · 기준선 · Q2a_trait_background', 'condition':'A'}, client_runtime('Q2a_prefill_test').specification(), *(memory_runtime(v).specification() for v in MEMORY_EXPERIMENTS)
    ]}


def configure(config, body):
    model = body.get('model', config['model'])
    if not isinstance(model, str) or model not in (*MODEL_CHOICES, config['model']):
        raise ValueError('지원하지 않는 모델 선택입니다.')
    config['model'] = model
    thinking = body.get('thinking_mode', 'default')
    if thinking not in ('default', 'on', 'off'):
        raise ValueError('thinking_mode는 default/on/off여야 합니다.')
    config['chat_template_kwargs'] = dict(config.get('chat_template_kwargs', {}))
    if thinking != 'default':
        config['chat_template_kwargs']['enable_thinking'] = thinking == 'on'
    mode = body.get('profile_context_mode', config.get('profile_context_mode', 'selective'))
    if mode not in ('full', 'selective'):
        raise ValueError('profile_context_mode는 full 또는 selective여야 합니다.')
    config['profile_context_mode'] = mode
    config['_input_blocks'] = input_blocks(config, body)
    config['_experiment_version'] = name(config, body)
    config['_request_body'] = body
    config['_defer_initial_request'] = config['_experiment_version'] == 'state_summary' or client_runtime(config['_experiment_version']) is not None
