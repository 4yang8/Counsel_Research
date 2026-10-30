"""Trajectory schemas and views. The model judges progression; Python validates and persists."""
import copy

MOVEMENT={'type':'string','enum':['maintain','advance','retreat'],
          'description':'maintain: 현재 수준 유지·정체, advance: 신뢰 형성 방향으로 진행, retreat: 경계가 강화되어 후퇴. 턴 수가 아니라 상호작용에서 형성된 마음에 근거합니다.'}
PLAN={'type':'object','properties':{
    'position':{'type':'string','description':'이전 진행 기록과 현재 마음을 바탕으로 판단한 사전 궤적상의 현재 위치. 단계 사이의 혼재도 자연어로 표현합니다.'},
    'movement':copy.deepcopy(MOVEMENT),
    'grounds':{'type':'string','description':'개인 특성·현재 appraisal·실제 상호작용과 필요한 경우 조회 결과를 연결하여, 이번에 이 정도의 변화 또는 유지가 가능한 이유'}},
    'required':['position','movement','grounds'],'additionalProperties':False}
PROGRESS={'type':'object','properties':{
    'position':{'type':'string','description':'이전 기록과 앞서 생성된 현재 내면·표현 선택을 근거로 정리한 궤적상 위치. 대상·시점·확실성을 보존합니다.'},
    'movement':copy.deepcopy(MOVEMENT),
    'current_experience':{'type':'string','description':'appraisal·대화 의도·표현 선택에 이미 드러난 현재 마음을 요약합니다. 주관적 느낌과 실제 공개 범위를 구분하고 새로운 해석·상호작용 결과를 추가하지 않습니다.'},
    'grounds':{'type':'string','description':'이전 기록과 이번 내면 판단·표현 선택·실제 발화의 어떤 차이가 유지·진행·후퇴를 뒷받침하는지 설명합니다. 각 자료의 의미·시점·확실성을 유지하고 계획을 결과로 바꾸지 않습니다.'},
    'next_condition':{'type':'string','description':'앞선 판단과 표현 선택에 남아 있는 불확실성·보류 조건. 이후 확인이 필요한 내용으로 기록하고, 없으면 빈 문자열을 사용합니다.'}},
    'required':['position','movement','current_experience','grounds','next_condition'],'additionalProperties':False}

def validate(value,schema):
    if not isinstance(value,dict) or set(value)!=set(schema['required']):
        raise ValueError('궤적 기록의 항목을 확인하세요: '+', '.join(schema['required']))
    for key in schema['required']:
        if not isinstance(value[key],str) or (key!='next_condition' and not value[key].strip()):
            raise ValueError('궤적 기록 '+key+'의 내용을 확인하세요.')
    if value['movement'] not in MOVEMENT['enum']:raise ValueError('movement는 maintain/advance/retreat 중 하나입니다.')
    return copy.deepcopy(value)

def render(progress):
    if not progress:return '(내용 없음)'
    labels={'position':'현재 위치','movement':'변화','current_experience':'현재 신뢰와 경계','grounds':'판단 근거','next_condition':'다음에 살필 조건'}
    names={'maintain':'유지·정체','advance':'진행','retreat':'후퇴'}
    return '\n'.join('- '+label+': '+(names.get(progress.get(key),progress.get(key)) or '(내용 없음)') for key,label in labels.items())
