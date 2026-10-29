"""Editable user prompt templates. Single-pass substitution; no expression evaluation."""
from pathlib import Path
import re

from project_paths import PROJECT_ROOT
HERE = PROJECT_ROOT
TOKEN = re.compile(r'{{\s*([a-z][a-z0-9_]*)\s*}}')
REGISTRY = {('client', 'Q1', 'user'): {'path': 'src/client/Q1/user.md',
                            'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                          'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                          'counselor_utterance': '이번 상담사 발화',
                                          'selected_traits': '라우터가 선택한 관련 특성 (Markdown 본문)',
                                          'state_notes': '이전 턴까지 저장된 현재 노트 원문'}},
 ('client', 'Q1', 'trait_router_user'): {'path': 'src/client/Q1/trait_router_user.md',
                                         'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                                       'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                                       'counselor_utterance': '이번 상담사 발화',
                                                       'state_notes': '이전 턴까지 저장된 현재 노트 원문',
                                                       'traits': '프로필의 전체 특성 목록 (Markdown 본문)'}},
 ('client', 'Q1a', 'user'): {'path': 'src/client/Q1a/user.md',
                             'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                           'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                           'counselor_utterance': '이번 상담사 발화',
                                           'selected_traits': '라우터가 선택한 관련 특성 (Markdown 본문)',
                                           'state_notes': '이전 턴까지 저장된 현재 노트 원문'}},
 ('client', 'Q1a', 'trait_router_user'): {'path': 'src/client/Q1a/trait_router_user.md',
                                          'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                                        'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                                        'counselor_utterance': '이번 상담사 발화',
                                                        'state_notes': '이전 턴까지 저장된 현재 노트 원문',
                                                        'traits': '프로필의 전체 특성 목록 (Markdown 본문)'}},
 ('client', 'Q1_s1', 'user'): {'path': 'src/client/Q1_s1/user.md',
                               'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                             'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                             'counselor_utterance': '이번 상담사 발화',
                                             'selected_traits': '라우터가 선택한 관련 특성 (Markdown 본문)',
                                             'state_notes': '이전 턴까지 저장된 현재 노트 원문'}},
 ('client', 'Q1_s1', 'trait_router_user'): {'path': 'src/client/Q1_s1/trait_router_user.md',
                                            'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                                          'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                                          'counselor_utterance': '이번 상담사 발화',
                                                          'state_notes': '이전 턴까지 저장된 현재 노트 원문',
                                                          'traits': '프로필의 전체 특성 목록 (Markdown 본문)'}},
 ('client', 'Q1_s2', 'user'): {'path': 'src/client/Q1_s2/user.md',
                               'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                             'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                             'counselor_utterance': '이번 상담사 발화',
                                             'selected_traits': '라우터가 선택한 관련 특성 (Markdown 본문)',
                                             'state_notes': '이전 턴까지 저장된 현재 노트 원문'}},
 ('client', 'Q1_s2', 'trait_router_user'): {'path': 'src/client/Q1_s2/trait_router_user.md',
                                            'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                                          'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                                          'counselor_utterance': '이번 상담사 발화',
                                                          'state_notes': '이전 턴까지 저장된 현재 노트 원문',
                                                          'traits': '프로필의 전체 특성 목록 (Markdown 본문)'}},
 ('client', 'Q2', 'user'): {'path': 'src/client/Q2/user.md',
                            'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                          'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                          'counselor_utterance': '이번 상담사 발화',
                                          'selected_traits': '라우터가 선택한 관련 특성 (Markdown 본문)',
                                          'state_notes': '이전 턴까지 저장된 현재 노트 원문'}},
 ('client', 'Q2', 'trait_router_user'): {'path': 'src/client/Q2/trait_router_user.md',
                                         'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                                       'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                                       'counselor_utterance': '이번 상담사 발화',
                                                       'state_notes': '이전 턴까지 저장된 현재 노트 원문',
                                                       'traits': '프로필의 전체 특성 목록 (Markdown 본문)'}},
 ('client', 'Q2a', 'user'): {'path': 'src/client/Q2a/user.md',
                             'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                           'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                           'counselor_utterance': '이번 상담사 발화',
                                           'selected_traits': '라우터가 선택한 관련 특성 (Markdown 본문)',
                                           'state_notes': '이전 턴까지 저장된 현재 노트 원문',
                                           'state_definitions': '10가지 심리상태의 이름과 의미'}},
 ('client', 'Q2a', 'trait_router_user'): {'path': 'src/client/Q2a/trait_router_user.md',
                                          'variables': {'basic_info': '기본 프로필 (Markdown 본문)',
                                                        'dialogue': '이번 상담사 발화 이전의 공개 대화',
                                                        'counselor_utterance': '이번 상담사 발화',
                                                        'state_notes': '이전 턴까지 저장된 현재 노트 원문',
                                                        'traits': '프로필의 전체 특성 목록 (Markdown 본문)'}},
 ('client', 'Q2', 'state_router_user'): {'path': 'src/client/Q2/state_router_user.md',
                                         'variables': {'evidence': '기본 정보·관련 특성·상태 정의·판단 기록 (제목 포함 Markdown)',
                                                       'state_notes': '이전 심리상태 노트 원문',
                                                       'dialogue': '공개 대화',
                                                       'counselor_utterance': '이번 상담사 발화',
                                                       'utterance': '완성된 내담자 발화'}},
 ('client', 'Q2', 'state_updater_user'): {'path': 'src/client/Q2/state_updater_user.md',
                                          'variables': {'evidence': '기본 정보·관련 특성·상태 정의·판단 기록 (제목 포함 '
                                                                    'Markdown)',
                                                        'state_notes': '이전 심리상태 노트 원문',
                                                        'dialogue': '공개 대화',
                                                        'counselor_utterance': '이번 상담사 발화',
                                                        'utterance': '완성된 내담자 발화',
                                                        'selected_states': '상태 라우터가 선택한 상태 (Markdown 본문)'}},
 ('client', 'Q1a', 'draft_reviewer_user'): {'path': 'src/client/Q1a/draft_reviewer_user.md',
                                            'variables': {'main_context': '메인에 전달했던 user prompt 전체',
                                                          'draft': '검토할 초안 JSON',
                                                          'question': '메인이 요청한 검토 질문',
                                                          'comparisons': '명시적으로 요청한 비교 후보 (있을 때만 제목과 JSON '
                                                                         '포함)'}},
 ('counselor', 'basic', 'user'): {'path': 'resources/prompts/basic_counselor_user.md',
                                  'variables': {'dialogue': '현재까지의 공개 대화 (상담사/내담자 스크립트)'}},
 ('counselor', 'P1', 'user'): {'path': 'src/counselor/P1/user.md',
                               'variables': {'dialogue': '현재까지의 공개 대화 (상담사/내담자 스크립트)',
                                             'client_notes': '현재 상담 노트 원문'}},
 ('counselor', 'P2', 'user'): {'path': 'src/counselor/P2/user.md',
                               'variables': {'dialogue': '현재까지의 공개 대화 (상담사/내담자 스크립트)',
                                             'client_notes': '현재 내담자 노트 원문',
                                             'counselor_notes': '현재 상담사 생각 노트 원문'}},
 ('counselor', 'P3', 'user'): {'path': 'src/counselor/P3/user.md',
                               'variables': {'dialogue': '현재까지의 공개 대화 (상담사/내담자 스크립트)',
                                             'client_notes': '현재 내담자 노트 원문',
                                             'counselor_notes': '현재 상담사 생각 노트 원문'}},
 ('counselor', 'P3a', 'user'): {'path': 'src/counselor/P3a/user.md',
                                'variables': {'dialogue': '현재까지의 공개 대화 (상담사/내담자 스크립트)',
                                              'client_notes': '현재 내담자 노트 원문',
                                              'counselor_notes': '현재 상담사 생각 노트 원문'}},
 ('counselor', 'P3a_s1', 'user'): {'path': 'src/counselor/P3a_s1/user.md',
                                   'variables': {'dialogue': '현재까지의 공개 대화 (상담사/내담자 스크립트)',
                                                 'client_notes': '현재 내담자 노트 원문',
                                                 'counselor_notes': '현재 상담사 생각 노트 원문'}},
 ('counselor', 'P3a', 'guide_agent_user'): {'path': 'src/counselor/P3a/guide_agent_user.md',
                                            'variables': {'guide': '메인이 요청한 가이드 종류',
                                                          'request': '메인이 작성한 상황과 질문',
                                                          'guidelines': '상세 가이드 전체 (각 문서 제목 포함)'}},
 ('counselor', 'P3a_s1', 'guide_agent_user'): {'path': 'src/counselor/P3a_s1/guide_agent_user.md',
                                               'variables': {'guide': '메인이 요청한 가이드 종류',
                                                             'request': '메인이 작성한 상황과 질문',
                                                             'guidelines': '상세 가이드 전체 (각 문서 제목 포함)'}},
 ('counselor', 'P3a_s1', 'guide_grounded_user'): {'path': 'src/counselor/P3a_s1/guide_grounded_user.md',
                                                  'variables': {'guide': '메인이 요청한 가이드 종류',
                                                                'request': '메인이 작성한 상황과 질문',
                                                                'guidelines': '상세 가이드 전체 (각 문서 제목 포함)',
                                                                'dialogue': '실제 공개 대화'}},
 ('client', 'basic', 'user'): {'path': 'resources/prompts/basic_client_user.md',
                               'variables': {'initial_context': '활성화한 초기 자료: 과제·대화·프로필·상태·taxonomy (제목 포함 '
                                                                'Markdown)',
                                             'counselor_utterance': '이번 상담사 발화'}},
 ('client', 'state_summary', 'user'): {'path': 'src/client/state_summary/user.md',
                                       'variables': {'task': '활성화한 과제 지침 (뒤에 빈 줄 포함)',
                                                     'dialogue': '공개 대화',
                                                     'profile_states_taxonomy': '활성 프로필·현재 상태·taxonomy (제목 '
                                                                                '포함 Markdown)',
                                                     'trajectory': '누적 내면 변화의 통합 요약',
                                                     'available_records': '조회 가능한 내면 기록 수 안내',
                                                     'counselor_utterance': '이번 상담사 발화'}},
 ('client', 'state_summary', 'summary_user'): {'path': 'src/client/state_summary/summary_user.md',
                                               'variables': {'state_definitions': '현재 상태 항목의 이름 (Markdown '
                                                                                  '본문)',
                                                             'records': '전체 누적 내면 반응 기록 (Markdown 본문)'}}}


# Independent editable templates for the variable-context experiment.
for _kind in ('user','trait_router_user'):
    _base=REGISTRY[('client','Q2a',_kind)]
    REGISTRY[('client','Q2a_variant_context',_kind)]={'path':_base['path'].replace('/Q2a/','/Q2a_variant_context/'),'variables':dict(_base['variables'])}



# Independent trait/background routing experiment; baseline templates stay unchanged.
for _kind in ('user','trait_router_user'):
    _base=REGISTRY[('client','Q2a',_kind)]
    REGISTRY[('client','Q2a_trait_background',_kind)]={'path':_base['path'].replace('/Q2a/','/Q2a_trait_background/'),'variables':dict(_base['variables'])}
REGISTRY[('client','Q2a_trait_background','trait_router_user')]['variables']['backgrounds']='성장 경험·상황 사례 후보의 ID와 원문'
REGISTRY[('client','Q2a_trait_background','user')]['variables']['selected_traits']='선택한 특성·배경 원문과 관련성 설명'


# Independent prompt sources for the router prefill experiment.
for _kind in ('user', 'trait_router_user'):
    _base=REGISTRY[('client','Q2a_trait_background',_kind)]
    REGISTRY[('client','Q2a_prefill_test',_kind)]={'path':_base['path'].replace('/Q2a_trait_background/','/Q2a_prefill_test/'),'variables':dict(_base['variables'])}


from client.memory_experiments.catalog import SPECS as MEMORY_EXPERIMENTS
for _version in MEMORY_EXPERIMENTS:
    for _kind in ('user','trait_router_user'):
        _base=REGISTRY[('client','Q2a_trait_background',_kind)]
        REGISTRY[('client',_version,_kind)]={'path':_base['path'].replace('/Q2a_trait_background/','/'+_version+'/'),'variables':dict(_base['variables'])}

REGISTRY[('client','Q2a_agentic_memory_F','user')]['variables'].pop('state_definitions', None)

REGISTRY[('client','Q3','user')]['variables'].pop('state_definitions', None)
REGISTRY[('client','Q3','user')]['variables'].update(trajectory='사전 변화 궤적 원문', trajectory_progress='이전 턴까지 저장된 궤적 진행 기록')

def descriptor(role, version, kind):
    try:
        return REGISTRY[(role, version, kind)]
    except KeyError:
        raise ValueError('지원하지 않는 user prompt 템플릿입니다.') from None


def validate(content, variables):
    names = set(TOKEN.findall(content))
    unknown = names - set(variables)
    if unknown:
        raise ValueError('알 수 없는 템플릿 변수: ' + ', '.join(sorted(unknown)))
    remainder = TOKEN.sub('', content)
    if '{{' in remainder or '}}' in remainder:
        raise ValueError('변수는 {{variable_name}} 형식으로 입력하세요.')
    return names


def render(role, version, kind='user', **values):
    spec = descriptor(role, version, kind)
    path = HERE / spec['path']
    if path.is_symlink():
        raise ValueError('템플릿 파일은 심볼릭 링크일 수 없습니다.')
    text = path.read_text(encoding='utf-8')
    names = validate(text, spec['variables'])
    if names - values.keys():
        raise ValueError('템플릿 입력값이 없습니다: ' + ', '.join(sorted(names - values.keys())))
    # Inserted data is never reparsed as template syntax.
    return TOKEN.sub(lambda match: str(values[match.group(1)]), text)


def md(render_markdown, value):
    """Use the existing Markdown serializer without its artificial outer heading."""
    text = render_markdown({'value': value})
    return '' if text == '## value' else text.removeprefix('## value\n\n')

REGISTRY[('client','Q3a','user')]['variables'].pop('state_definitions', None)
REGISTRY[('client','Q3a','user')]['variables'].update(trajectory='사전 변화 궤적 원문', trajectory_progress='이전 턴까지 저장된 궤적 진행 기록')

# Q3b has independent user/trajectory sources.
REGISTRY[('client','Q3b','user')]['variables'].pop('state_definitions', None)
REGISTRY[('client','Q3b','user')]['variables'].update(trajectory='사전 변화 궤적 원문', trajectory_progress='이전 턴까지 저장된 궤적 진행 기록')
