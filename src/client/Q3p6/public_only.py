"""C: independent public-only memory, no explicit inner-state generation."""
import copy
from difflib import SequenceMatcher
import json
import re
from pathlib import Path

from client.Q1.runtime import split_profile, parse_json
from client.Q3.profile_context import background_candidates, routing_schema, resolve_selection
from client.Q3.tool_loop import run_turn
from client.claim_experiments.runtime import object_schema, validate
from client.state_summary.runtime import write_new
from runtime import user_templates, reasoning_budget
from runtime.cancellation import Cancelled

HERE = Path(__file__).resolve().parent
TEXT = {'type': 'string'}
SPEECH = object_schema({'utterance': TEXT})
NOTES = object_schema({'public_notes': {'type': 'array', 'maxItems': 128, 'items': object_schema({
    'message_index': {'type': 'integer', 'minimum': 0}, 'quote': TEXT})}})


def load(session):
    path = session.get('public_state_path')
    return json.loads(Path(path).read_text()) if path else {'notes': '(아직 공개된 정보 없음)', 'records': []}


def restore_omitted_quote(source, quote):
    """Restore an original span, never accept a stitched quote as evidence.

    Only exact, ordered token blocks separated by source omissions qualify.
    Long unique anchors prevent matching scattered words or ambiguous repeats.
    The returned span includes every omitted word, including negations.
    """
    spans = list(re.finditer(r'\S+', source))
    original = [m.group() for m in spans]
    quoted = quote.split()
    edits = SequenceMatcher(None, quoted, original, autojunk=False).get_opcodes()
    if any(kind in ('delete', 'replace') for kind, *_ in edits):
        return None
    blocks = [(i, j, x, y) for kind, i, j, x, y in edits if kind == 'equal']
    if len(blocks) < 2:
        return None
    for i, j, x, y in blocks:
        anchor = quoted[i:j]
        if len(anchor) < 2 or sum(c.isalnum() for c in ''.join(anchor)) < 12:
            return None
        if sum(original[k:k + len(anchor)] == anchor for k in range(len(original) - len(anchor) + 1)) != 1:
            return None
    start, end = spans[blocks[0][2]].start(), spans[blocks[-1][3] - 1].end()
    return {'quote': source[start:end], 'start': start, 'end': end}


def resolve_note_evidence(public, rows):
    """Resolve exact citations or restore uniquely grounded omitted context."""
    evidence, corrections = [], []
    for row in rows:
        original, quote = row['message_index'], row['quote']
        i, resolved_quote = original, quote
        if not quote.strip():
            raise ValueError('C 노트 인용이 비어 있습니다.')
        if not (0 <= i < len(public) and quote in public[i]['content']):
            matches = [j for j, message in enumerate(public) if quote in message['content']]
            if len(matches) == 1:
                i = matches[0]
                corrections.append({'original_message_index': original, 'resolved_message_index': i,
                                    'quote': quote, 'reason': 'unique_exact_quote_match'})
            elif matches:
                raise ValueError('C 노트 인용이 공개 대화와 다릅니다: 일치 위치가 여러 개입니다.')
            else:
                restored = [(j, span) for j, message in enumerate(public)
                            if (span := restore_omitted_quote(message['content'], quote)) is not None]
                if len(restored) != 1:
                    raise ValueError('C 노트 인용이 공개 대화와 다릅니다: 원문을 유일하게 확인할 수 없습니다.')
                i, span = restored[0]
                resolved_quote = span['quote']
                corrections.append({'original_message_index': original, 'resolved_message_index': i,
                                    'quote': quote, 'resolved_quote': resolved_quote,
                                    'source_start': span['start'], 'source_end': span['end'],
                                    'reason': 'omitted_context_restored'})
        evidence.append({'message_index': i, 'quote': resolved_quote})
    return evidence, corrections


def generate(config, session, backend, runtime, builder, emit, cancelled, destination):
    state = load(session)
    requests, usage = [], {}
    build = reasoning_budget.wrap_builder(builder, session['settings'].get('client_reasoning_budgets'))

    def call(prompt, source, schema, phase, role='main'):
        if cancelled(): raise Cancelled('C 생성 중단')
        req = build(config, {'system_prompt': prompt, '_reasoning_budget_role': role,
                             'messages': [{'role': 'user', 'content': source}]}, [], False)
        req['structured_outputs'] = {'json': schema}
        if len(json.dumps(req, ensure_ascii=False)) > config['_p6_control']['input_char_limit']:
            raise ValueError('Q3p6 C 입력 한도 초과: 내용을 절삭하지 않았습니다.')
        requests.append(copy.deepcopy(req)); result = {}
        def event(kind, **data):
            if kind == 'done':
                result.update(data)
            elif kind == 'request':
                emit(kind, phase=phase, **data)
            else:
                emit(kind, **data)
        emit('request', payload=copy.deepcopy(req), phase=phase, iteration=1)
        run_turn(config, req, None, backend.upstream, backend.stream_events, backend.inspect_prompt,
                 event, cancelled, [])
        emit('model_result', phase=phase, iteration=1, response=result.get('response', ''),
             reasoning=result.get('reasoning', ''), finish_reason=result.get('finish_reason'))
        if cancelled(): raise Cancelled('C 생성 중단')
        if result.get('finish_reason') != 'stop': raise ValueError(phase + ' 응답이 정상 완료되지 않았습니다.')
        for k, v in result.get('usage', {}).items():
            if isinstance(v, (int, float)): usage[k] = usage.get(k, 0) + v
        value = parse_json(result['response']); validate(value, schema)
        return value

    scenario = session['settings']['scenario']
    public = copy.deepcopy(session['public_dialogue'])
    basic, traits = split_profile(scenario['profile'])
    backgrounds = background_candidates(scenario['profile'])
    router_user = user_templates.render('client', 'Q3p6', 'trait_router_user',
        basic_info=user_templates.md(runtime.markdown_data, basic),
        traits=user_templates.md(runtime.markdown_data, traits),
        backgrounds=user_templates.md(runtime.markdown_data, backgrounds),
        dialogue='\n\n'.join(('상담사' if m['role'] == 'counselor' else '내담자') + ': ' + m['content'] for m in public[:-1]),
        counselor_utterance=public[-1]['content'], state_notes=state['notes'])
    routed = call((HERE/'trait_router.md').read_text(), router_user,
                  routing_schema(traits, backgrounds), 'C 프로필 선택', 'subagent')
    selected = resolve_selection(routed, traits, backgrounds)
    source = {'basic_info': basic, 'selected_traits': selected, 'initial_states': scenario['states'],
              'public_dialogue': public, 'public_notes': state['notes']}
    value = call((HERE/'public_only.md').read_text(), json.dumps(source, ensure_ascii=False), SPEECH, 'C 공개 발화')
    utterance = value['utterance'].strip()
    if not utterance: raise ValueError('C 발화가 비어 있습니다.')
    emit('version_event', version='Q3p6', section='utterance', action='fixed', data={'content': utterance, 'pending': True})
    public.append({'role': 'client', 'content': utterance})
    result = call((HERE/'public_notes.md').read_text(), json.dumps({'public_dialogue': [dict(m, message_index=i) for i, m in enumerate(public)]}, ensure_ascii=False), NOTES, 'C 공개 노트')
    evidence, corrections = resolve_note_evidence(public, result['public_notes'])
    if corrections:
        emit('version_event', version='Q3p6', section='public_notes', action='citation_corrected',
             data={'corrections': corrections})
    lines = [f"- 공개 {r['message_index']} · {public[r['message_index']]['role']}: {r['quote']}" for r in evidence]
    notes = '\n'.join(lines) or '(선택한 공개 정보 없음)'
    record = {'turn': len(state['records']) + 1, 'condition': 'public_only',
              'utterance': utterance, 'counselor_utterance': public[-2]['content'],
              'notes_before': state['notes'], 'notes_after': notes, 'note_evidence': evidence,
              'raw_note_evidence': result['public_notes'], 'note_corrections': corrections,
              'note_index_corrections': [c for c in corrections if c['original_message_index'] != c['resolved_message_index']],
              'selected_traits': selected, 'model_requests': requests, 'usage': usage}
    if cancelled(): raise Cancelled('C 저장 전 중단')
    write_new(destination, {'notes': notes, 'records': state['records'] + [record]})
    return utterance, usage, str(destination)
