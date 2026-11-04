"""CARS CCD-triggered reasoning with a persistent scalar emotion and intention."""
import copy
from .common import TEXT, obj, encoded, enum, render, validate

BELIEF = obj(theme=TEXT,
             automatic_thought=obj(situation=TEXT,cognition=TEXT,reaction=TEXT),
             intermediate_belief=obj(attitude=TEXT,assumption=TEXT,rule=TEXT))
TWO = obj(first=TEXT,second=TEXT)
PROFILE_SCHEMA = obj(background=TEXT,beliefs={'type':'array','items':BELIEF,'minItems':1},
    client_characteristics_and_preferences=obj(
        possible_responses_under_different_emotions=obj(dialogue_style=TEXT,nonverbal_behavior=TEXT),
        preferred_counselor_style=obj(positive=TWO,negative=TWO),
        other_client_characteristics=obj(positive=TWO,negative=TWO)))


def fill(text, **values):
    # Single-pass substitution: profile content is never treated as a template.
    import re
    return re.sub(r'\{([A-Z_]+)\}',lambda m:encoded(values[m[1]]),text)


PIPELINE_VERSION = 'cars-validated-v2'
REVIEW_SCHEMA = obj(issues={'type':'array','items':obj(kind=enum((
    'emotion_scale','source_conflict','profile_consistency','cognitive_branch',
    'unsupported_fact','grounding','expression')), detail=TEXT)})


def review(call, prompts, template, stage, data):
    return call(stage, render(prompts,template,data=encoded(data)), REVIEW_SCHEMA)['issues']


def review_native(call, prompts, native):
    issues = review(call,prompts,'cars_profile_review','cars_profile_review',
                    {'profile':native, 'source_persona':native['persona']})
    if issues:
        raise ValueError('CARS 프로필 검증 실패: '+encoded(issues))


def prepare(call, prompts, source, messages):
    main = {key:source[key] for key in ('core_belief','intermediate_beliefs','coping_strategies',
            'situations','childhood_data') if key in source} or {'provided_profile':copy.deepcopy(source)}
    original = fill(prompts['cars_profile'],PERSONA=source,MAIN_CCD=main,STORY=messages,
        TOPIC=['session structuring','problem solving and homework','goal setting',
               'cognitive change','cognitive identification','therapeutic alliance'])
    original += '\n\n'+prompts['cars_contract']
    text = original
    for attempt in range(2):
        generated = call('session_ccd_preparation',text,PROFILE_SCHEMA)
        native = {'persona':source,'background':generated['background'],'main_ccd':main,
                  'session_ccd':generated['beliefs'],
                  'preferences':generated['client_characteristics_and_preferences'],
                  'emotion_examples':[], 'source':'project_profile_adapter'}
        issues = review(call,prompts,'cars_profile_review','cars_profile_review',
                        {'source_persona':source,'profile':native})
        if not issues:
            return native
        text = original+'\n\n이전 생성 결과와 검토 문제입니다. 문제를 수정한 전체 프로필을 다시 반환하세요.\n'+encoded({'previous':generated,'issues':issues})
    raise ValueError('CARS 프로필이 수정 후에도 검증을 통과하지 못했습니다: '+encoded(issues))


def validate_profile(value):
    if not isinstance(value,dict) or any(key not in value or not value[key] for key in
            ('persona','background','main_ccd','session_ccd','preferences')):
        raise ValueError('CARS native profile: persona/background/main_ccd/session_ccd/preferences가 필요합니다.')
    validate(value['session_ccd'],{'type':'array','items':BELIEF,'minItems':1},'session_ccd')
    return copy.deepcopy(value)


def evidence_sources(native,messages):
    sources={'persona':encoded(native['persona']), 'background':encoded(native['background'])}
    for i,message in enumerate(messages,1):
        sources['dialogue_'+str(i)] = message['content']
    return sources


def response_schema(native,sources):
    check=obj(ccd_index={'type':'integer','minimum':1,'maximum':len(native['session_ccd'])},
              situation_evidence=TEXT,consistency=enum(('confirmed','contradicted','uncertain')),
              consistency_reason=TEXT,use_as_premise={'type':'boolean'})
    return obj(Thinking=TEXT,Emotion={'type':'number','minimum':-100,'maximum':100},
        Intention=TEXT,nonverbal_behavior=TEXT,Response=TEXT,
        Reasoning=obj(topic=TEXT,branch=enum(('matched_ccd','no_matching_ccd')),
            belief_checks={'type':'array','items':check},
            emotion_reason=TEXT,intention_reason=TEXT,expression_reason=TEXT),
        Grounding={'type':'array','items':obj(claim=TEXT,source_id=enum(sources),quote=TEXT)})


def structural_issues(result,sources):
    issues=[]
    def issue(kind,detail):issues.append({'kind':kind,'detail':detail})
    reasoning=result['Reasoning'];checks=reasoning['belief_checks']
    if (reasoning['branch']=='matched_ccd') != bool(checks):
        issue('cognitive_branch','branch와 belief_checks 유무가 일치하지 않습니다.')
    ids=[c['ccd_index'] for c in checks]
    if len(ids)!=len(set(ids)):
        issue('cognitive_branch','동일 CCD가 중복 기재됐습니다.')
    for c in checks:
        if c['use_as_premise'] != (c['consistency']=='confirmed'):
            issue('cognitive_branch','confirmed일 때만 use_as_premise=true여야 합니다.')
    for g in result['Grounding']:
        if g['source_id'] not in sources or not g['quote'].strip() or g['quote'] not in sources[g['source_id']]:
            issue('grounding','인용문이 지정된 원문에 존재하지 않습니다: '+g['source_id']+
                  '\n잘못된 인용: '+encoded(g['quote'])+
                  '\n다음 원문에서 해당 문구를 한 글자도 바꾸지 않고 복사하세요: '+sources.get(g['source_id'],''))
    return issues


def generate(call, prompts, options, native, messages, before):
    state = copy.deepcopy(before)
    state.setdefault('emotion', options['initial_emotion'])
    state.setdefault('intention', '')
    examples = native.get('emotion_examples',[])
    original = fill(prompts['cars_turn'],PERSONA=native['persona'],BACKGROUND=native['background'],
                PREFERENCE=native['preferences'],MAIN_CCD=native['main_ccd'],SECONDARY_CCD=native['session_ccd'],
                EXAMPLE=examples or '예문 자료를 확보하지 못했습니다. 원본 예문을 모방했다고 주장하지 마세요.',
                DIALOGUE_HISTORY=messages,PLANNING=state['intention'],EMOTION_STATE=state['emotion'])
    sources=evidence_sources(native,messages)
    original += '\n\n'+prompts['cars_contract']+'\n\n검증용 사실 출처(공개 발화에 출력하지 않음):\n'+encoded(sources)
    schema=response_schema(native,sources);text=original;reviews=[]
    for attempt in range(2):
        result = call('cognitive_response',text,schema)
        issues = structural_issues(result,sources)
        if not issues:
            issues = review(call,prompts,'cars_response_review','cars_response_review',
                {'profile':native,'public_dialogue':messages,'prior_emotion':state['emotion'],
                 'prior_intention':state['intention'],'candidate':result,'sources':sources})
        reviews.append({'attempt':attempt+1,'issues':copy.deepcopy(issues)})
        if not issues:break
        text=original+'\n\n이전 후보와 검토 문제입니다. 문제를 수정한 전체 턴을 다시 반환하세요.\n'+encoded({'previous':result,'issues':issues})
    else:
        raise ValueError('CARS 응답이 수정 후에도 검증을 통과하지 못했습니다: '+encoded(issues))
    state.update(emotion=max(0,min(100,state['emotion']+result['Emotion'])),
                 intention=result['Intention'],nonverbal_behavior=result['nonverbal_behavior'],
                 emotion_delta=result['Emotion'],thinking=result['Thinking'],
                 reasoning=result['Reasoning'],grounding=result['Grounding'],validation=reviews,
                 pipeline_version=PIPELINE_VERSION,emotion_examples_available=bool(examples))
    state['below_posthoc_emotion_threshold'] = state['emotion'] < 10
    return result['Response'].strip(),state
