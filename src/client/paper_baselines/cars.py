"""CARS CCD-triggered reasoning with a persistent scalar emotion and intention."""
import copy
from .common import TEXT, obj, encoded

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


def prepare(call, prompts, source, messages):
    main = {key:source[key] for key in ('core_belief','intermediate_beliefs','coping_strategies',
            'situations','childhood_data') if key in source}
    if not main:
        main = {'provided_profile':copy.deepcopy(source)}
    generated = call('session_ccd_preparation',fill(prompts['cars_profile'],
        PERSONA=source,MAIN_CCD=main,STORY=messages,
        TOPIC=['session structuring','problem solving and homework','goal setting',
               'cognitive change','cognitive identification','therapeutic alliance']),PROFILE_SCHEMA)
    return {'persona':source,'background':generated['background'],'main_ccd':main,
            'session_ccd':generated['beliefs'],
            'preferences':generated['client_characteristics_and_preferences'],
            'emotion_examples':[], 'source':'project_profile_adapter'}


def validate_profile(value):
    if not isinstance(value,dict) or any(key not in value or not value[key] for key in
            ('persona','background','main_ccd','session_ccd','preferences')):
        raise ValueError('CARS native profile: persona/background/main_ccd/session_ccd/preferences가 필요합니다.')
    return copy.deepcopy(value)


def generate(call, prompts, options, native, messages, before):
    state = copy.deepcopy(before)
    state.setdefault('emotion', options['initial_emotion'])
    state.setdefault('intention', '')
    examples = native.get('emotion_examples',[])
    text = fill(prompts['cars_turn'],PERSONA=native['persona'],BACKGROUND=native['background'],
                PREFERENCE=native['preferences'],MAIN_CCD=native['main_ccd'],SECONDARY_CCD=native['session_ccd'],
                EXAMPLE=examples or '예문 자료를 확보하지 못했습니다. 원본 예문을 모방했다고 주장하지 마세요.',
                DIALOGUE_HISTORY=messages,PLANNING=state['intention'],EMOTION_STATE=state['emotion'])
    result = call('cognitive_response',text,obj(Thinking=TEXT,
                  Emotion={'type':'number','minimum':-100,'maximum':100},
                  Intention=TEXT,nonverbal_behavior=TEXT,Response=TEXT))
    state.update(emotion=max(0,min(100,state['emotion']+result['Emotion'])),
                 intention=result['Intention'],nonverbal_behavior=result['nonverbal_behavior'],
                 emotion_delta=result['Emotion'],thinking=result['Thinking'],
                 emotion_examples_available=bool(examples))
    # Paper uses a post-hoc <10 criterion. Keep the raw run and expose it without
    # silently turning it into an online stopping threshold.
    state['below_posthoc_emotion_threshold'] = state['emotion'] < 10
    return result['Response'].strip(),state
