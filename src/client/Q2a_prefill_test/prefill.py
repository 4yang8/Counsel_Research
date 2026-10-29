"""Router-only reasoning prefill using the running server's own rendered template.

The chat payload is a context view. wire_request is the exact /v1/completions
request. Supplied prefill is input, never counted or displayed as model output.
"""
import copy
import json
import re
from runtime.cancellation import Cancelled


class ThinkingSplit:
    """Incremental </think> parser, including delimiters split across SSE frames."""
    marker = '</think>'

    def __init__(self):
        self.pending = ''
        self.closed = False

    def feed(self, text, final=False):
        if self.closed:
            return '', text
        self.pending += text
        at = self.pending.find(self.marker)
        if at >= 0:
            reasoning, content = self.pending[:at], self.pending[at + len(self.marker):]
            self.pending = ''
            self.closed = True
            return reasoning, content
        keep = 0
        if not final:
            for size in range(1, len(self.marker)):
                if self.pending.endswith(self.marker[:size]):
                    keep = size
        end = len(self.pending) - keep
        reasoning, self.pending = self.pending[:end], self.pending[end:]
        return reasoning, ''


def run_prefilled_router(config, payload, prefix, upstream, frames, inspect, emit, cancelled, label):
    def check():
        if cancelled():
            raise Cancelled('특성 라우터 prefill 생성을 중단했습니다.')
    check()
    if not prefix or '<think>' in prefix or '</think>' in prefix:
        raise ValueError('reasoning prefill은 추론 구분자 없이 본문만 작성하세요.')
    if payload.get('tools'):
        raise ValueError('이 prefill 경로는 도구를 사용하지 않는 특성 라우터 전용입니다.')
    # Always render using the server tokenizer, even when optional inspection is off.
    base = inspect(config, payload)
    prompt = base.get('prompt', '')
    if not re.search(r'<think>\s*$', prompt):
        raise ValueError('현재 템플릿이 열린 <think>로 끝나지 않아 reasoning prefill을 적용할 수 없습니다. '
                         'thinking 활성화와 모델 템플릿을 확인하세요.')
    prompt += prefix
    wire = {key: copy.deepcopy(payload[key]) for key in (
        'model', 'temperature', 'top_p', 'max_tokens', 'seed', 'stream_options',
        'structured_outputs', 'thinking_token_budget') if key in payload}
    wire.update(prompt=prompt, stream=True, add_special_tokens=False)
    view = copy.deepcopy(payload)
    view['messages'].append({'role': 'assistant', 'content': '', 'reasoning': prefix})
    emit('request', payload=view, wire_request=copy.deepcopy(wire), endpoint='/v1/completions',
         reasoning_prefill=prefix, iteration=1, phase=label,
         context_format='messages는 입력 블록 표현이며 실제 전송 본문은 wire_request입니다.')
    check()
    with upstream(config, '/tokenize', {'model': wire['model'], 'prompt': prompt,
                                      'add_special_tokens': False, 'return_token_strs': True},
                  config.get('inspection_timeout_seconds', 10)) as response:
        tokens = json.load(response)
    emit('inspection', iteration=1, prompt=prompt, token_count=tokens.get('count'),
         token_ids=tokens.get('tokens'), token_strs=tokens.get('token_strs'),
         max_model_len=tokens.get('max_model_len'), reasoning_prefill=prefix,
         endpoint='/v1/completions')
    split = ThinkingSplit()
    reasoning, content, raw = '', '', ''
    finish, usage, complete = None, {}, False

    def output(r, c, finish_reason=None, token_usage=None):
        nonlocal reasoning, content
        reasoning += r
        content += c
        delta = {}
        if r:
            delta['reasoning'] = r
        if c:
            delta['content'] = c
        chunk = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}]}
        if token_usage:
            chunk['usage'] = token_usage
        emit('chunk', iteration=1, data=chunk)

    with upstream(config, '/v1/completions', wire) as response:
        for frame in frames(response):
            check()
            if frame == '[DONE]':
                complete = True
                break
            chunk = json.loads(frame)
            if 'error' in chunk:
                raise ValueError(json.dumps(chunk['error'], ensure_ascii=False))
            if chunk.get('usage'):
                usage = chunk['usage']
            sent = False
            for choice in chunk.get('choices', []):
                if choice.get('index', 0) != 0:
                    continue
                text = choice.get('text') or ''
                raw += text
                finish = choice.get('finish_reason') or finish
                r, c = split.feed(text, final=bool(finish))
                output(r, c, choice.get('finish_reason'), chunk.get('usage'))
                sent = True
            if chunk.get('usage') and not sent:
                output('', '', token_usage=chunk['usage'])
    if not complete:
        raise ValueError('prefill 라우터의 vLLM 스트림이 완료 신호 없이 끊겼습니다.')
    if finish != 'stop' or not split.closed or not content.strip():
        raise ValueError('prefill 라우터가 reasoning과 JSON 응답을 완료하지 못했습니다: '
                         + str(finish) + '. 최대 출력 토큰과 추론 예산을 확인하세요.')
    check()
    emit('done', response=content, reasoning=reasoning, reasoning_prefill=prefix,
         raw_completion=raw, usage=usage, finish_reason=finish, tool_calls_used=0,
         history=[*copy.deepcopy(payload['messages']),
                  {'role': 'assistant', 'content': content, 'reasoning': prefix + reasoning}])
