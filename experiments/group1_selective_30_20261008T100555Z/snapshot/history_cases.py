"""Authored histories with the original Korean group1 profile as their source."""
import copy
import json
from pathlib import Path
from profile_retrieval import initial_profile

HERE = Path(__file__).resolve().parent
NUMBERS = ('001', '002', '005', '007', '008')


def load_catalog(base_scenario, validate, context_mode='selective'):
    path = HERE / 'cases/group1/histories.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    if [p['number'] for p in data['profiles']] != list(NUMBERS):
        raise ValueError('그룹1 히스토리의 프로필 구성을 확인하세요.')
    for entry in data['profiles']:
        number = entry['number']
        filename = 'gold_ccd_profile_' + number + '.json'
        profile = json.loads((HERE.parent / 'profile/group1' / filename).read_text(encoding='utf-8'))
        if profile['profile_id'] != entry['profile_id'] or len(entry['histories']) != 3:
            raise ValueError('프로필 번호와 히스토리 구성을 확인하세요.')
        for history in entry['histories']:
            if len(history['turns']) != 5:
                raise ValueError('초기 히스토리는 5턴이어야 합니다.')
            scenario = copy.deepcopy(base_scenario)
            scenario['id'] = 'group1_' + history['id']
            scenario['profile'] = initial_profile(profile, context_mode)
            scenario['dialogue_turns'] = [{**turn, 'enabled': True} for turn in history['turns']]
            scenario['source_notes'] = {'case_origin': data['provenance'],
                                      'profile_source': 'gemma_test/profile/group1/' + filename,
                                      'history_source': 'cases/group1/histories.json',
                                      'history_id': history['id']}
            scenario['counselor_presets'] = []
            scenario['example_counselor_utterance'] = history['turns'][-1]['counselor']
            history['scenario'] = validate(scenario)
    return data
