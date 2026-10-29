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


INNER = {
    'feelings_and_concerns': ('지금 마음에 남아 있는 감정과 걱정', '무엇이 마음에 걸리고 어떤 감정이 남아 있는지 구체적인 대상과 함께 기록합니다.'),
    'unspoken_and_wishes': ('말하지 않은 속마음과 현재 바라는 것', '공개하지 않은 마음과 지금 원하거나 보류하고 싶은 내용을 기록합니다.'),
    'continuity_reason': ('그 마음이 변화하거나 유지된 이유', '현재 마음이 이전과 달라지거나 이어지는 이유를 이번 경험과 연결해 기록합니다.'),
}
EMPTY = '(내용 없음)'


def empty_inner():
    return dict.fromkeys(INNER, EMPTY)


def render(values, inner=None):
    inner = empty_inner() if inner is None else inner
    if set(values) != set(STATES) or set(inner) != set(INNER):
        raise ValueError('현재 내면 3개 항목과 심리상태 10개 항목이 필요합니다.')
    def section(title, definitions, contents):
        return '## '+title+'\n\n'+'\n\n'.join('### '+label+'\n'+contents[key].strip() for key,(label,_) in definitions.items())
    return '# 내담자 노트\n\n'+section('현재 내면',INNER,inner)+'\n\n'+section('심리상태',STATES,values)+'\n'


def _parse_sections(text, prefix, heading, definitions):
    parts=re.split(r'^'+re.escape(heading)+' ', text, flags=re.M)
    if parts[0].strip()!=prefix or len(parts)!=len(definitions)+1:
        raise ValueError('노트의 고정 제목과 항목을 유지하세요: '+prefix)
    values={}
    for part,(key,(title,_)) in zip(parts[1:],definitions.items()):
        name,sep,content=part.partition('\n')
        if not sep or name.strip()!=title or not content.strip() or re.search(r'^#{1,6} ',content,re.M):
            raise ValueError('노트 항목 이름·순서와 비어 있지 않은 본문을 확인하세요: '+title)
        values[key]=content.strip()
    return values


def parse_note(text):
    # Older sessions remain readable without modifying their persisted files.
    if text.startswith('# 현재 심리상태\n'):
        return {'states':_parse_sections(text,'# 현재 심리상태','##',STATES),'inner':empty_inner()}
    parts=re.split(r'^## ',text,flags=re.M)
    if len(parts)!=3 or parts[0].strip()!='# 내담자 노트':
        raise ValueError('내담자 노트의 현재 내면·심리상태 영역을 유지하세요.')
    return {'inner':_parse_sections(parts[1],'현재 내면','###',INNER),
            'states':_parse_sections(parts[2],'심리상태','###',STATES)}


def parse(text):
    return parse_note(text)['states']
