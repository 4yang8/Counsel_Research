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
    'position':{'type':'string','description':'이번 반응을 마친 현재의 궤적상 위치. 사전 궤적의 표현을 활용하되 혼재와 불확실성을 보존합니다.'},
    'movement':copy.deepcopy(MOVEMENT),
    'current_experience':{'type':'string','description':'이번 appraisal과 표현 선택에 나타난 현재 신뢰·경계의 핵심. 실제 공개한 범위와 속마음을 구분하여 간결하게 기록합니다.'},
    'grounds':{'type':'string','description':'이전 진행 기록과 이번 계획·내면 판단·확정 발화를 비교한 유지·진행·후퇴 근거. 계획한 것과 실제 형성된 것을 구분합니다.'},
    'next_condition':{'type':'string','description':'이후 진행을 판단할 때 확인할 아직 불분명한 조건. 정해진 다음 행동이 아니라 관찰할 조건이며, 없으면 빈 문자열'}},
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
