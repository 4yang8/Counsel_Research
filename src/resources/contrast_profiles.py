"""Read-only trust contrast catalog. Original group1 catalogs remain unchanged."""
import copy
import json
from pathlib import Path

from project_paths import PROJECT_ROOT
HERE=PROJECT_ROOT
PROFILE_ROOT=HERE/'resources/profiles'
CASES={
    'trust_011_guarded':{'number':'011_guarded','label':'011 · 불신 중심 (원본)',
        'path':'group2/gold_ccd_profile_011.json','profile_id':'gold_011'},
    'trust_011_open':{'number':'011_open','label':'011 대조 · 신뢰 회복에 열린 편',
        'path':'trust_contrast/profile_011_open.json','profile_id':'trust_011_open'},
}
OPENING='오늘 상담에서 가장 먼저 다뤄보고 싶은 일이 있나요?'


def read_profile(case_id):
    entry=CASES[case_id];path=PROFILE_ROOT/entry['path']
    profile=json.loads(path.read_text(encoding='utf-8'))
    if profile.get('profile_id')!=entry['profile_id']:raise ValueError('신뢰 대조 프로필 ID가 일치하지 않습니다.')
    return path,profile


def profiles():
    rows=[]
    for case_id,entry in CASES.items():
        path,profile=read_profile(case_id)
        rows.append({'number':entry['number'],'label':entry['label'],'filename':path.name,
            'source':'resources/profiles/'+entry['path'],'group':'trust_contrast','profile':profile})
    return rows


def histories(base_scenario,validate,context_mode='selective'):
    from resources.profile_retrieval import initial_profile
    rows=[]
    for case_id,entry in CASES.items():
        path,profile=read_profile(case_id)
        scenario=copy.deepcopy(base_scenario)
        for key in ('dialogue_turns','dialogue_options','selected_counselor_utterance'):scenario.pop(key,None)
        scenario.update(id=case_id,profile=initial_profile(profile,context_mode),dialogue=[],dialogue_format='messages',
            counselor_presets=[],example_counselor_utterance=OPENING,
            source_notes={'profile_source':'resources/profiles/'+entry['path'],'case_origin':'신뢰 대조 · 초기 대화 없음'})
        rows.append({'number':entry['number'],'label':entry['label'],'group':'trust_contrast','profile_id':profile['profile_id'],
            'source_profile':initial_profile(profile,'full'),
            'histories':[{'id':case_id,'title':'초기 대화 없음 · 동일한 상담 시작',
                'description':'신뢰 대조 실험. 과거 상담 발화를 미리 넣지 않고 같은 상담 시작 문장으로 진행합니다.',
                'turns':[],'scenario':validate(scenario)}]})
    return rows
