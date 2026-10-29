"""Message validation and streamed model → MCP → model execution."""
import copy
import json
import uuid
from runtime.cancellation import Cancelled


def validate_messages(history, allow_empty=False):
    if not isinstance(history, list) or not history:
        raise ValueError('대화 메시지가 없습니다.')
    messages, pending, state, seen = [], set(), 'user', set()
    for msg in history:
        if not isinstance(msg, dict):
            raise ValueError('메시지는 객체여야 합니다.')
        role = msg.get('role')
        content = msg.get('content')
        if role == 'user' and state == 'user' and isinstance(content, str):
            messages.append({'role':role,'content':content})
            state = 'assistant'
        elif role == 'assistant' and state == 'assistant':
            calls = msg.get('tool_calls')
            if content is not None and not isinstance(content, str):
                raise ValueError('assistant content는 문자열이어야 합니다.')
            clean = {'role':role,'content':content or ''}
            if calls:
                if not isinstance(calls, list):
                    raise ValueError('tool_calls는 배열이어야 합니다.')
                for call in calls:
                    if not isinstance(call, dict) or not isinstance(call.get('id'), str) or not call['id'] or call['id'] in seen:
                        raise ValueError('도구 호출 ID가 없거나 중복입니다.')
                    fn = call.get('function', {})
                    if call.get('type') != 'function' or not isinstance(fn, dict) or not isinstance(fn.get('name'), str) or not isinstance(fn.get('arguments'), str):
                        raise ValueError('잘못된 도구 호출입니다.')
                    pending.add(call['id']); seen.add(call['id'])
                clean['tool_calls'] = copy.deepcopy(calls)
                state = 'tool'
            else:
                state = 'user'
            messages.append(clean)
        elif role == 'tool' and state == 'tool' and isinstance(content, str) and msg.get('tool_call_id') in pending:
            pending.remove(msg['tool_call_id'])
            messages.append({'role':'tool','tool_call_id':msg['tool_call_id'],'content':content})
            if not pending:
                state = 'assistant'
        else:
            raise ValueError('대화 역할 순서 또는 도구 결과 ID가 올바르지 않습니다.')
    if messages[-1]['role'] != 'user' or (not allow_empty and not messages[-1]['content'].strip()):
        raise ValueError('마지막 메시지는 내용이 있는 user여야 합니다.')
    return messages


def run_turn(config, payload, client, upstream, frames, inspect, emit, cancelled, original):
    transcript = copy.deepcopy(original)
    used = 0
    iteration = 1
    total_usage = {}
    while True:
        if cancelled():
            raise Cancelled('생성을 중단했습니다. 이미 실행된 파일 변경은 유지됩니다.')
        if config.get('_prepare_payload'):
            config['_prepare_payload'](payload, iteration)
        if iteration > 1:
            emit('request', payload=copy.deepcopy(payload), iteration=iteration)
        if config.get('inspect_prompt', True):
            try:
                inspection = inspect(config, payload)
            except Exception as error:
                inspection = {'error':str(error)}
            emit('inspection', iteration=iteration, **inspection)
        content, reasoning, calls, finish, complete = '', '', {}, None, False
        with upstream(config, '/v1/chat/completions', payload) as response:
            for frame in frames(response):
                if cancelled():
                    raise Cancelled('생성을 중단했습니다. 이미 실행된 파일 변경은 유지됩니다.')
                if frame == '[DONE]':
                    complete = True
                    break
                chunk = json.loads(frame)
                if 'error' in chunk:
                    raise ValueError(json.dumps(chunk['error'], ensure_ascii=False))
                emit('chunk', iteration=iteration, data=chunk)
                for choice in chunk.get('choices', []):
                    if choice.get('index', 0) != 0:
                        continue
                    delta = choice.get('delta', {})
                    content += delta.get('content') or ''
                    reasoning += delta.get('reasoning_content') or delta.get('reasoning') or ''
                    if choice.get('finish_reason'):
                        finish = choice['finish_reason']
                    for fragment in delta.get('tool_calls') or []:
                        idx = fragment.get('index', 0)
                        if type(idx) is not int or not 0 <= idx < 50:
                            raise ValueError('잘못된 도구 호출 index입니다.')
                        target = calls.setdefault(idx, {'id':'','type':'function','function':{'name':'','arguments':''}})
                        if fragment.get('id'):
                            target['id'] = fragment['id']
                        for key in ('name','arguments'):
                            target['function'][key] += fragment.get('function', {}).get(key) or ''
                        if len(target['function']['arguments']) > 1000000:
                            raise ValueError('도구 인자 크기 제한 초과')
                usage = chunk.get('usage')
                if usage:
                    for key in ('prompt_tokens','completion_tokens','total_tokens'):
                        total_usage[key] = total_usage.get(key, 0) + (usage.get(key) or 0)
            if not complete:
                raise ValueError('vLLM 스트림이 완료 신호 없이 끊겼습니다.')
        if not calls:
            if getattr(client,'requires_finalize',False):
                raise ValueError('공개 발화 확정과 상태 기록 도구를 모두 완료해야 합니다.')
            if finish == 'tool_calls':
                raise ValueError('도구 호출 종료 신호가 있지만 파싱된 호출이 없습니다. vLLM parser/template 설정을 확인하세요.')
            if not content:
                raise ValueError('텍스트 응답이 비어 있습니다. reasoning 또는 max_tokens 설정을 확인하세요.')
            assistant = {'role':'assistant','content':content}
            transcript.append(assistant)
            emit('done', response=content, reasoning=reasoning, history=transcript,
                 usage=total_usage, finish_reason=finish, tool_calls_used=used)
            return
        if finish != 'tool_calls':
            # vLLM's explicitly named function mode can finish a complete
            # constrained function response with `stop`. Preserve the raw
            # stream, and accept only that named, single, complete JSON call.
            choice=payload.get('tool_choice')
            forced=choice.get('function',{}).get('name') if isinstance(choice,dict) and choice.get('type')=='function' else None
            valid_named=False
            if finish=='stop' and forced and len(calls)==1:
                item=next(iter(calls.values()))
                try:
                    valid_named=(item['function']['name']==forced and isinstance(json.loads(item['function']['arguments']),dict))
                except (ValueError,KeyError,TypeError):
                    pass
            if not valid_named:
                raise ValueError('도구 호출이 정상 완료되지 않았습니다. 불완전한 호출은 실행하지 않습니다.')
        if client is None:
            raise ValueError('Filesystem이 비활성화되어 도구를 실행할 수 없습니다.')
        if used + len(calls) > client.opts['max_calls']:
            raise ValueError('한 턴의 최대 도구 호출 횟수를 초과했습니다.')
        tool_calls = [calls[key] for key in sorted(calls)]
        if getattr(client,'requires_finalize',False) and len(tool_calls)!=1:
            raise ValueError('각 단계의 도구 결과를 확인한 후 다음 도구를 하나씩 호출하세요.')
        ids = {call['id'] for m in transcript for call in m.get('tool_calls', [])}
        for call in tool_calls:
            if not call['id']:
                call['id'] = 'call_' + uuid.uuid4().hex
            if call['id'] in ids:
                raise ValueError('중복된 도구 호출 ID입니다.')
            ids.add(call['id'])
        assistant = {'role':'assistant','content':content,'tool_calls':tool_calls}
        if reasoning:
            # Current vLLM accepts `reasoning` on input; old response aliases
            # are normalized above. Its chat parser passes this to the template.
            assistant['reasoning'] = reasoning
        payload['messages'].append(assistant)
        transcript.append(copy.deepcopy(assistant))
        for call in tool_calls:
            if cancelled():
                raise Cancelled('도구 실행 전에 중단했습니다.')
            used += 1
            name, raw_args = call['function']['name'], call['function']['arguments']
            emit('tool_start', iteration=iteration, call_id=call['id'], name=name, arguments=raw_args,
                 assistant_message=copy.deepcopy(assistant))
            try:
                arguments = json.loads(raw_args)
                result = client.call(name, arguments)
            except (ValueError, json.JSONDecodeError) as error:
                result = {'isError':True,'content':[{'type':'text','text':str(error)}]}
            # Timeout/process errors stop the turn: never automatically retry a
            # write whose execution status is unknown.
            full = json.dumps(result, ensure_ascii=False)
            limit = client.opts['max_result_chars']
            visible = full if len(full) <= limit else json.dumps({
                'isError':result.get('isError', False), 'truncated':True,
                'text':full[:limit], 'note':'결과 크기 제한. 작은 파일 또는 head/tail로 다시 조회하세요.'}, ensure_ascii=False)
            message = {'role':'tool','tool_call_id':call['id'],'content':visible}
            payload['messages'].append(message)
            transcript.append(message)
            emit('tool_result', iteration=iteration, call_id=call['id'], name=name,
                 result=result, model_content=visible, truncated=len(full)>limit,
                 is_error=bool(result.get('isError')))
        terminal=getattr(client,'terminal_response',lambda:None)()
        if terminal is not None:
            if cancelled():raise Cancelled('상태 기록 확정 전에 중단되었습니다.')
            emit('done',response=terminal,reasoning=reasoning,history=transcript,usage=total_usage,
                 finish_reason='stop',response_source='committed_utterance',tool_calls_used=used)
            return
        if used >= client.opts['max_calls']:
            payload['tool_choice'] = 'none'
        iteration += 1
