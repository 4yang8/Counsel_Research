"""Fixed psychological constructs; contents are qualitative, not scalar scores."""
import re

STATES = {
    'goal_alignment': ('상담 목표에 대한 합의', '이번 상담에서 무엇을 얻고 싶은지, 현재 다루는 목표를 얼마나 자신의 목표로 받아들이는지.'),
    'task_alignment': ('상담 방식과 활동에 대한 합의', '상담사가 제안한 대화 방식이나 활동을 어떻게 이해하고 받아들이는지.'),
    'bond': ('상담사와의 관계적 유대', '상담사가 자신을 이해하고 존중한다고 느끼는지, 무엇을 믿거나 경계하는지.'),
    'disclosure_willingness': ('자기개방 의향', '어떤 개인적 내용을 이야기할 준비가 되어 있고, 어떤 내용은 왜 보류하고 싶은지.'),
    'change_self_efficacy': ('변화 자기효능감', '생활 속 어떤 변화를 자신이 해낼 수 있다고 생각하며 무엇에는 자신이 없는지.'),
    'autonomous_change_motivation': ('자율적 변화 동기', '외부 요구와 구분되는 자신의 변화 이유, 원함과 망설임이 무엇인지.'),
    'problem_clarity': ('문제와 패턴에 대한 명료성', '자신의 어려움과 반복되는 패턴을 어떻게 이해하며 무엇은 아직 불명확한지.'),
    'treatment_expectancy': ('상담에 대한 기대', '상담 과정이 어떤 도움을 줄 것이라고 기대하거나 의심하는지.'),
    'therapeutic_agency': ('상담 과정에서의 주체성', '상담의 주제·속도·방식을 직접 선택하거나 조정할 수 있다고 느끼는지.'),
    'emotion_regulation_capacity': ('정서를 견디고 조절하는 역량', '어려운 감정을 경험하면서 대화에 머물거나 조절할 수 있는 조건과 어려움. 현재 감정의 강도와는 구분한다.'),
}


def render(values):
    if set(values) != set(STATES):
        raise ValueError('심리상태 노트에는 고정된 10개 항목이 필요합니다.')
    return '# 현재 심리상태\n\n' + '\n\n'.join('## '+title+'\n'+values[key].strip() for key,(title,_) in STATES.items())+'\n'


def parse(text):
    parts=re.split(r'^## ',text,flags=re.M)
    if parts[0].strip() != '# 현재 심리상태' or len(parts)!=11:
        raise ValueError('심리상태 노트의 제목과 10개 항목을 유지하세요.')
    values={}
    for part,(key,(title,_)) in zip(parts[1:],STATES.items()):
        name,sep,content=part.partition('\n')
        if not sep or name.strip()!=title or not content.strip() or re.search(r'^#{1,6} ',content,re.M):
            raise ValueError('심리상태 항목 이름·순서와 비어 있지 않은 본문을 확인하세요: '+title)
        values[key]=content.strip()
    return values
