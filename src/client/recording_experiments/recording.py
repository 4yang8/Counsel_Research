"""Recording adapters. Raw text is sliced by Python; snapshots never call a model."""
import copy
import hashlib

FIELDS = ('appraisal', 'turn_goal', 'response_plan')
SELECT_SCHEMA = {'type': 'object', 'properties': {'selections': {
    'type': 'array', 'items': {'type': 'object', 'properties': {
        'source_id': {'type': 'string', 'description': '제공된 원문 출처 ID'},
        'first_line': {'type': 'integer', 'minimum': 1, 'description': '포함할 첫 줄 (1부터)'},
        'last_line': {'type': 'integer', 'minimum': 1, 'description': '포함할 마지막 줄 (양끝 포함)'}},
        'required': ['source_id', 'first_line', 'last_line'], 'additionalProperties': False}}},
    'required': ['selections'], 'additionalProperties': False}
SUMMARY_SCHEMA = {'type': 'object', 'properties': {
    'notes': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
    'trajectory': {'type': 'object', 'properties': {
        key: {'type': 'string'} for key in ('current_experience', 'grounds', 'next_condition')},
        'required': ['current_experience', 'grounds', 'next_condition'], 'additionalProperties': False}},
    'required': ['notes', 'trajectory'], 'additionalProperties': False}


def source_bundle(pairs, staged, utterance, previous_items, turn, previous_record=None):
    """Keep entire reasoning/output pairs, including rejected and superseded revisions."""
    pairs = copy.deepcopy(pairs)
    accepted = [p for p in pairs if p['name'] == 'stage_client_update' and p.get('accepted')]
    latest = accepted[-1]['call_id']
    sources = []

    def add(identity, text, kind, **meta):
        if text:
            sources.append(dict(source_id=identity, text=text, kind=kind, turn=turn, **meta))

    for pair in pairs:
        active = pair.get('accepted', False) and (pair['name'] == 'commit_client_utterance' or pair['call_id'] == latest)
        pair['status'] = 'committed' if active else 'superseded' if pair.get('accepted') else 'rejected'
        # Reasoning stays exploratory even when its accompanying output is committed.
        if active:
            add(pair['call_id'] + ':reasoning', pair['reasoning'], 'exploratory_reasoning', call_id=pair['call_id'])
    for field in FIELDS:
        for key, value in staged[field].items():
            add('judgment:' + field + '.' + key, value, 'committed_judgment', field=field + '.' + key)
    add('speech:utterance', utterance, 'actual_utterance')
    old_raw = (previous_record or {}).get('raw_selections', [])
    originals = {r['id']: selection for selection in old_raw
                 for r in raw_items([selection])['notes']}
    for item in previous_items['notes']:
        original = originals.get(item['id'])
        if original:
            add('previous:' + item['id'], original['text'], 'previous_note', item_id=item['id'],
                origin_kind=original.get('origin_kind', original['kind']),
                origin_source_id=original.get('origin_source_id', original['source_id']),
                origin_turn=original.get('origin_turn', original['turn']))
        else:
            add('previous:' + item['id'], item['content'], 'previous_note', item_id=item['id'])
    return {'turn': turn, 'pairs': pairs, 'sources': sources,
            'committed_judgment': copy.deepcopy(staged), 'actual_utterance': utterance,
            'trajectory_assessment': copy.deepcopy(staged['trajectory_assessment']),
            'meaning': 'reasoning은 탐색 과정, 마지막 성공 stage 출력은 확정 주관적 판단, commit 출력은 실제 공개 발화. 계획은 결과가 아닙니다.'}


def select(bundle, args):
    if not isinstance(args, dict) or set(args) != {'selections'} or not isinstance(args['selections'], list):
        raise ValueError('selections에 원문 ID와 줄 범위를 전달하세요.')
    sources = {s['source_id']: s for s in bundle['sources']}
    result, spans = [], {}
    for choice in args['selections']:
        if not isinstance(choice, dict) or set(choice) != {'source_id', 'first_line', 'last_line'}:
            raise ValueError('원문 ID와 first_line, last_line만 지정하세요. 새 본문은 작성하지 않습니다.')
        identity, first, last = (choice[k] for k in ('source_id', 'first_line', 'last_line'))
        if not isinstance(identity, str) or identity not in sources:
            raise ValueError('제공된 source_id를 선택하세요.')
        source = sources[identity]; lines = source['text'].splitlines(keepends=True)
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(lines):
            raise ValueError('원문의 실제 줄 범위를 지정하세요.')
        start, end = sum(map(len, lines[:first-1])), sum(map(len, lines[:last]))
        if any(start < b and a < end for a, b in spans.get(identity, [])):
            raise ValueError('같은 원문에서 겹치는 범위를 중복 선택하지 마세요.')
        spans.setdefault(identity, []).append((start, end))
        result.append({**copy.deepcopy(source), **choice, 'start': start, 'end': end,
                       'text': source['text'][start:end]})
    # A note cannot be only speculative reasoning or older notes.
    if not any(r['kind'] == 'committed_judgment' for r in result):
        raise ValueError('이번 확정 내면 판단에서 다음 반응에 필요한 내용을 하나 이상 선택하세요.')
    return result


def numbered_sources(bundle):
    return [{'source_id': s['source_id'], 'kind': s['kind'], 'turn': s['turn'],
             'lines': [{'line': i, 'text': line} for i, line in enumerate(s['text'].splitlines(keepends=True), 1)]}
            for s in bundle['sources']]


def items_from_entries(entries):
    rows = []
    for label, text in entries:
        content = label + '\n' + text
        identity = 'n' + hashlib.sha256(content.encode('utf-8')).hexdigest()[:12]
        if any(r['id'] == identity for r in rows):
            continue
        rows.append({'id': identity, 'content': content})
    return {'notes': rows}


def raw_items(selections):
    return items_from_entries([(f"[{s.get('origin_kind', s['kind'])} · 턴 {s.get('origin_turn', s['turn'])} · {s.get('origin_source_id', s['source_id'])}]", s['text'])
                               for s in selections])


def snapshot_items(staged, utterance):
    entries = [(field + '.' + key, value) for field in FIELDS for key, value in staged[field].items()]
    entries += [('실제 공개 발화', utterance)]
    return items_from_entries(entries)


def summary_items(value):
    if not isinstance(value, dict) or set(value) != {'notes', 'trajectory'}:
        raise ValueError('요약 결과의 notes와 trajectory를 확인하세요.')
    if not isinstance(value['notes'], list) or not value['notes'] or any(not isinstance(s, str) or not s.strip() for s in value['notes']):
        raise ValueError('요약 노트에는 비어 있지 않은 문자열이 필요합니다.')
    progress = value['trajectory']
    if not isinstance(progress, dict) or set(progress) != {'current_experience', 'grounds', 'next_condition'} or any(
            not isinstance(v, str) or (k != 'next_condition' and not v.strip()) for k, v in progress.items()):
        raise ValueError('요약된 궤적 경험·근거·남은 조건을 확인하세요.')
    # Summary text is stored exactly, without another model or .strip().
    return items_from_entries([('요약', text) for text in value['notes']])


def operations(before, after):
    old, new = ({r['id']: r for r in items['notes']} for items in (before, after))
    return [dict(action='delete', item_id=k, content='', before=r['content'], label='내담자 노트',
                 reason='기록 스냅샷 교체. 과거 기록은 완료 턴 이력에 보존됩니다.') for k, r in old.items() if k not in new] + [
        dict(action='add', item_id=k, content=r['content'], before='', label='내담자 노트',
             reason='선택된 원문 / 반환 요약 / 확정 필드를 해당 기록 방식으로 저장합니다.') for k, r in new.items() if k not in old]
