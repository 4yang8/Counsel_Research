"""Q3 profile routing with public-only inputs in both memory conditions."""
import copy
from pathlib import Path

from client.Q1.runtime import split_profile, parse_json
from client.Q3.profile_context import background_candidates, routing_schema, resolve_selection
from client.Q3.tool_loop import run_turn
from .memory import encode

PROMPT = Path(__file__).with_name('inner_router.md')


def inputs(ctx):
    basic, traits = split_profile(ctx['profile'])
    backgrounds = background_candidates(ctx['profile'])
    return basic, traits, backgrounds


def main_source(ctx, visible):
    basic, _, _ = inputs(ctx)
    return {'basic_profile': basic, 'selected_traits': ctx.get('selected_traits', []),
            'public_dialogue': ctx['public'], 'private_turns': visible}


def route(config, body, ctx, build, upstream, frames, inspect, emit, cancelled, check_size):
    basic, traits, backgrounds = inputs(ctx)
    user = {'basic_profile': basic, 'traits': traits, 'backgrounds': backgrounds,
            'public_dialogue': ctx['public']}
    request = build(config, {'system_prompt': body['memory_router_prompt'],
        'messages': [{'role': 'user', 'content': encode(user)}],
        'generation': copy.deepcopy(body.get('generation', config.get('generation', {}))),
        '_reasoning_budget_role': 'subagent'}, [], False)
    request['structured_outputs'] = {'json': routing_schema(traits, backgrounds)}
    check_size(request)
    result = {}

    def event(kind, **data):
        if kind == 'done':
            result.update(data)
        emit('agent_event', agent_id='trait_router', agent_name='특성·배경 라우터',
             agent_kind='trait_router', parent_agent_id='main', depth=1,
             event={'type': kind, **data})

    event('request', payload=copy.deepcopy(request), iteration=1, phase='공개 대화 기반 특성·배경 선택')
    run_turn(config, request, None, upstream, frames, inspect, event, cancelled, [])
    if result.get('finish_reason') != 'stop':
        raise ValueError('특성 라우터 응답이 정상 완료되지 않았습니다.')
    selected = resolve_selection(parse_json(result['response']), traits, backgrounds)
    ctx['selected_traits'] = selected
    return selected, request, result.get('usage', {})
