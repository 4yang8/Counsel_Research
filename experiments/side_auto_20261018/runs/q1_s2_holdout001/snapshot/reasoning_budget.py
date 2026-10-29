"""Frozen, independent thinking budgets for main and subagent requests."""

SCOPES = ('main', 'subagent')
MAX_BUDGET = 1_000_000


def normalize(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(SCOPES):
        raise ValueError('추론 한도에는 main과 subagent만 설정하세요.')
    result = {}
    for scope in SCOPES:
        budget = value.get(scope)
        if budget is not None and (type(budget) is not int or not 1 <= budget <= MAX_BUDGET):
            label = '메인' if scope == 'main' else '서브에이전트'
            raise ValueError(label + ' 추론 한도는 1~1,000,000 정수 또는 null이어야 합니다.')
        result[scope] = budget
    return result


def wrap_builder(base_build, settings=None):
    budgets = normalize(settings)

    def build(config, body, tools=None, preview=False):
        scope = body.get('_reasoning_budget_role', body.get('_counselor_budget_role', 'main'))
        if scope not in SCOPES:
            raise ValueError('추론 한도의 호출 역할이 올바르지 않습니다.')
        payload = base_build(config, body, tools, preview)
        budget = budgets[scope]
        if budget is None:
            payload.pop('thinking_token_budget', None)
        else:
            payload['thinking_token_budget'] = budget
        return payload

    return build
