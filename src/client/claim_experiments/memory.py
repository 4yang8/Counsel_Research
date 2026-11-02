"""Controlled memory views. Raw archives remain intact and never bypass a view."""
import copy
import hashlib
import json
from client.Q3 import memory as q3_memory

WINDOW = 12
MAX_CHARS = 18000


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def selected_episodes(records):
    """Select whole episodes once, before changing representation; no excerpts."""
    result, omitted, size = [], [], 0
    for record in reversed(records[-WINDOW:]):
        row = {key: copy.deepcopy(record.get(key)) for key in (
            'turn', 'turn_id', 'counselor_utterance', 'utterance',
            'appraisal', 'turn_goal', 'response_plan')}
        length = len(encode(row))
        if size + length > MAX_CHARS:
            omitted.append(row['turn'])
        else:
            result.append(row)
            size += length
    return list(reversed(result)), sorted(omitted)


def private_content(row):
    return {key: copy.deepcopy(row[key]) for key in ('appraisal', 'turn_goal', 'response_plan')}


def make_view(records, mode, reconstructions=None):
    rows, omitted = selected_episodes(records)
    common = dict(condition=mode, selected_turns=[r['turn'] for r in rows],
                  omitted_turns=omitted, window=WINDOW, selection_char_budget=MAX_CHARS)
    if mode == 'summary_only':
        visible = {'provenance': 'current_notes_only', 'episodes': []}
    elif mode == 'intent_linked':
        visible = {'provenance': 'committed_at_utterance_time', 'episodes': rows}
    elif mode == 'intent_unlinked':
        # Same intent objects, without explicit turn, utterance, or positional
        # pairing. Wording can still imply its source: this is not anonymization.
        contents = [private_content(r) for r in rows]
        contents.sort(key=lambda x: hashlib.sha256(encode(x).encode()).hexdigest())
        visible = {'provenance': 'committed_at_utterance_time', 'unbound_intents': contents}
    elif mode == 'linked_prose':
        visible = {'provenance': 'committed_at_utterance_time', 'paragraphs': [
            f"완료 턴 {r['turn']} ({r['turn_id']})에서 상담사는 {encode(r['counselor_utterance'])}라고 말했고, "
            f"내담자는 {encode(r['utterance'])}라고 답했다. 당시 확정 내면 기록은 {encode(private_content(r))}이다."
            for r in rows]}
    elif mode == 'retrospective':
        visible = {'provenance': 'retrospective_inference_not_original_intent',
                   'status': 'ready' if reconstructions is not None else 'pending_generation',
                   'episodes': copy.deepcopy(reconstructions or [])}
    else:
        raise ValueError('알 수 없는 의도 기억 조건입니다.')
    return {**common, 'visible': visible, 'visible_chars': len(encode(visible))}


class CurrentOnlyMemory:
    """Identical current-note lookup for every condition; archives are view-only."""
    MAX_LOOKUPS = q3_memory.MAX_LOOKUPS

    @staticmethod
    def validate(args):
        q3_memory.validate(args)

    @staticmethod
    def lookup(note, args):
        q3_memory.validate(args)
        if args['source'] != 'current':
            return {'source': args['source'], 'status': 'blocked_by_condition',
                    'results': [], 'matched_count': 0, 'has_more': False, 'next_offset': None,
                    'scope': '현재 노트만 조회 가능. 과거 자료는 고정된 실험 기억 입력으로만 전달합니다.'}
        return q3_memory.lookup({**note, 'records': []}, args)
