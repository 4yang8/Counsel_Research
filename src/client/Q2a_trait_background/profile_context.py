"""Select existing profile records; never resolve model paths against the filesystem."""
import copy


def background_candidates(profile):
    rows = []
    childhood = profile.get('childhood_data')
    if childhood is not None and childhood != '' and childhood != [] and childhood != {}:
        rows.append({'background_id': '/childhood_data', 'kind': '성장 경험', 'content': copy.deepcopy(childhood)})
    situations = profile.get('situations', [])
    if isinstance(situations, list):
        for index, value in enumerate(situations):
            if value is not None and value != '' and value != {}:
                rows.append({'background_id': f'/situations/{index}', 'kind': '상황 사례', 'content': copy.deepcopy(value)})
    return rows


def routing_schema(traits, backgrounds):
    trait_ids = [row['trait_id'] for row in traits]
    background_ids = [row['background_id'] for row in backgrounds]
    trait_field = {'type': 'string', 'description': '관련된 특성 후보의 ID'}
    background_field = {'type': 'string'}
    if trait_ids: trait_field['enum'] = trait_ids
    if background_ids: background_field['enum'] = background_ids
    return {'type': 'object', 'properties': {'selections': {
        'type': 'array', 'maxItems': len(trait_ids), 'items': {
            'type': 'object', 'properties': {
                'trait_id': trait_field,
                'background_ids': {'type': 'array', 'items': background_field, 'maxItems': len(background_ids),
                                   'description': '이 특성과 이번 상황을 이해하는 데 관련된 배경 ID. 연결할 근거가 없으면 빈 배열.'},
                'relevance': {'type': 'string', 'description': '현재 입력에서 확인되는 대화 단서와 선택한 특성·배경의 연결 근거만 1~2문장으로 작성합니다. 원문에 명시된 연결과 관련성을 판단하며 추정한 연결을 구분합니다.'}},
            'required': ['trait_id', 'background_ids', 'relevance'], 'additionalProperties': False}}},
        'required': ['selections'], 'additionalProperties': False}


def resolve_selection(value, traits, backgrounds):
    if not isinstance(value, dict) or set(value) != {'selections'} or not isinstance(value['selections'], list):
        raise ValueError('라우터는 selections 배열을 반환해야 합니다.')
    trait_map = {row['trait_id']: row for row in traits}
    background_map = {row['background_id']: row for row in backgrounds}
    selected = []; seen = set()
    for row in value['selections']:
        if not isinstance(row, dict) or set(row) != {'trait_id', 'background_ids', 'relevance'}:
            raise ValueError('특성 ID, 배경 ID 목록, 관련성 설명을 반환하세요.')
        tid = row['trait_id']; ids = row['background_ids']; relevance = row['relevance']
        if not isinstance(tid, str) or tid not in trait_map or tid in seen:
            raise ValueError('알 수 없거나 중복된 특성 ID입니다.')
        if not isinstance(ids, list) or any(not isinstance(x, str) or x not in background_map for x in ids):
            raise ValueError('배경 후보 목록에 있는 ID만 선택하세요.')
        if len(ids) != len(set(ids)):
            raise ValueError('같은 특성에 중복된 배경 ID가 있습니다.')
        if not isinstance(relevance, str) or not relevance.strip():
            raise ValueError('이번 상황과의 관련성을 작성하세요.')
        seen.add(tid)
        selected.append({**copy.deepcopy(trait_map[tid]), 'background_ids': list(ids),
                         'backgrounds': [copy.deepcopy(background_map[x]) for x in ids],
                         'relevance': relevance})
    return selected
