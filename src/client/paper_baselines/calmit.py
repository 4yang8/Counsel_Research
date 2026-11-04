"""CALM-IT A1/A2; explicitly approximate ranker and unspecified prompt details."""
import copy
import random
import re
from .common import TEXT, encoded, enum, obj, render

CHANGE = ('Precontemplation','Contemplation','Preparation','Action','Maintenance','Termination')
ACTIONS = ('Deny','Downplay','Blame','Hesitate','Doubt','Engage','Inform','Acknowledge',
           'Accept','Reject','Plan','Terminate','Desire','Commitment')
STRATEGIES = ('ASKING OPEN QUESTIONS','ELICIT CHANGE TALK','ASKING ELUCIDATING QUESTIONS',
              'NORMALIZING','BUILDING RAPPORT','SIMPLE REFLECTION','COMPLEX REFLECTION',
              'DOUBLE-SIDED REFLECTION','DECISIONAL BALANCING','COLUMBO APPROACH',
              'SUPPORTING SELF-EFFICACY','ASSESSING READINESS TO CHANGE','AFFIRMATIONS',
              'ADVICE (ELICIT–PROVIDE–ELICIT)','SUMMARIES','THERAPEUTIC PARADOX')
PIVOTS = ('EVOKING VALUES AND STRENGTHS','NORMALIZE AND REFRAME','ACKNOWLEDGE AND CHANGE COURSE',
          'STRATEGIC SUMMARY AND REFOCUS','SHIFT THE LENS WITH METAPHORS')
RAPPORT = {'type':'number','minimum':-0.10,'maximum':0.05}


def profile(source):
    return dict(background=copy.deepcopy(source),
                cognitive_model={k:copy.deepcopy(source[k]) for k in
                    ('core_belief','intermediate_beliefs','coping_strategies','situations') if k in source},
                session_goal=source.get('desired_help') or source.get('presenting_problem') or
                    '내 속도에 맞춰 현재 고민을 탐색하고 싶어요.',
                source='project_profile_adapter')


def validate_profile(value):
    if not isinstance(value,dict) or any(k not in value for k in ('background','cognitive_model','session_goal')):
        raise ValueError('CALM-IT native profile: background/cognitive_model/session_goal이 필요합니다.')
    return copy.deepcopy(value)


def initial(options, native=None):
    return dict(summary='',background=copy.deepcopy(native['background']) if native else '',
                cognitive_model=copy.deepcopy(native['cognitive_model']) if native else {},
                session_goal=copy.deepcopy(native['session_goal']) if native else '',
                goal=copy.deepcopy(native['session_goal']) if native else '',
                change_stage='Precontemplation',emotion='알 수 없음',rapport=options['initial_rapport'],
                action='Engage',therapy_stage='Engaging',strategies=[],pivot='',topic='',profile={})


def variables(state, messages, options):
    public = '\n'.join(('상담사' if m['role']=='user' else '내담자')+': '+m['content'] for m in messages)
    recent = messages[-options['recent_messages']:]
    values = {**state, 'dialogue':public,'recent':recent,
              'last_client':next((m['content'] for m in reversed(messages) if m['role']=='assistant'),''),
              'last_counselor':next((m['content'] for m in reversed(messages) if m['role']=='user'),''),
              'turn':len(messages)+1,'turn_length':'짧게',
              'min_plan_turn':options['min_plan_turn'],'min_end_turn':options['min_end_turn'],
              'initial_rapport':len(messages)<options['min_plan_turn']}
    return {k:encoded(v) if isinstance(v,(dict,list)) else v for k,v in values.items()}


def ended(text):
    return any(marker in text for marker in ('<SESSION TERMINATION>','<END>'))


def candidates(call, prompts, options, state, messages, role, strategies=None):
    outputs = []
    for i in range(3):
        v = variables(state,messages,options)
        if strategies:
            v['strategy'] = strategies[i]
        candidate = call(role+'_candidate_'+str(i+1),render(prompts,'calmit_'+role+'_turn',**v),
                         obj(value=TEXT))['value'].strip()
        candidate = re.sub(r'^(?:내담자|상담사|Patient|Client|Therapist):\s*','',candidate,flags=re.I)
        if not candidate:
            raise ValueError('빈 CALM-IT 후보입니다.')
        outputs.append(candidate)
    if options['selector']=='first_candidate':
        selected = {'index':0,'reason':'순위 없는 근사 조건: 원본 순위 모델이 없어 첫 후보를 선택했습니다.'}
    else:
        selected = call(role+'_candidate_selector',render(prompts,'calmit_selector',
                        role='내담자' if role=='client' else '상담사',state=encoded(state),dialogue=encoded(messages),candidates=encoded(outputs)),
                        obj(index={'type':'integer','minimum':0,'maximum':2},reason=TEXT))
    state['selection'] = {'policy':options['selector'],'original_ranker':False,
                          'candidates':outputs,**selected}
    state['terminated'] = (ended(outputs[selected['index']]) or state.get('action')=='Terminate'
                           or len(messages)+1 >= options['max_speaker_turns'])
    state['turn_counter'] = len(messages)+1
    return outputs[selected['index']]


def generate(call, prompts, options, native, messages, before):
    state = copy.deepcopy(before) or initial(options,native)
    def step(name, key, schema=TEXT):
        result = call('client_'+name,render(prompts,'calmit_'+name if name=='summary' else 'calmit_client_'+name,
                      **variables(state,messages,options)),obj(value=schema))['value']
        state[key] = result
        return result
    step('summary','summary')
    sampled = random.Random(str(options['random_seed'])+':'+str(len(messages))).random()<0.5
    state['system1_sampled'] = sampled
    if sampled:
        value = call('client_appraisal',render(prompts,'calmit_client_appraisal',
                     **variables(state,messages,options)),
                     obj(rating=enum(('VERY BAD','BAD','NEUTRAL','GOOD','VERY GOOD')),reason=TEXT))
        state['appraisal'] = value['rating']+', '+value['reason']
    else:
        state['appraisal'] = 'NEUTRAL'
    step('change','change_stage',enum(CHANGE))
    step('emotion','emotion')
    prior = state['rapport']
    delta = step('rapport','rapport_delta',RAPPORT)
    state['rapport'] = max(-1,min(1,prior+delta))
    step('goal','goal')
    action = step('action','action',enum(ACTIONS))
    if action=='Plan' and len(messages)+1 < options['min_plan_turn']:
        raise ValueError('CALM-IT client selected Plan before configured minimum turn.')
    utterance = candidates(call,prompts,options,state,messages,'client')
    return utterance,state


def counselor(call, prompts, options, messages, before):
    # No native client profile, cognitive model, state or retrieval results enter here.
    state = copy.deepcopy(before) or initial(options)
    def step(name, key, schema=TEXT):
        value = call('counselor_'+name,render(prompts,'calmit_'+name if name=='summary' else 'calmit_counselor_'+name,
                     **variables(state,messages,options)),obj(value=schema))['value']
        state[key] = value
        return value
    step('summary','summary')
    previous = state['background']
    updates = step('background','background_updates')
    if updates.strip() not in ('(갱신 없음)','갱신된 배경:\n(갱신 없음)',
                               '(no updates)','Updated Background:\n(no updates)'):
        state['background'] = (previous+'\n'+updates).strip()
    step('emotion','emotion')
    step('change','change_stage',enum(CHANGE))
    step('goal','goal')
    prior = state['rapport']
    delta = step('rapport','rapport_delta',RAPPORT)
    state['rapport'] = max(-1,min(1,prior+delta))
    repetitive = step('stagnation','stagnation',{'type':'boolean'})
    stage = step('stage','therapy_stage',enum(('Engaging','Focusing','Evoking','Planning')))
    if stage=='Planning' and len(messages)+1 < options['min_plan_turn']:
        raise ValueError('CALM-IT counselor selected Planning before configured minimum turn.')
    if repetitive:
        pivot = step('pivot','pivot',enum(PIVOTS))
        strategies = [pivot]*3
    else:
        state['pivot']=''
        strategies = step('strategy','proposed_strategies',{'type':'array','minItems':3,'maxItems':3,
                          'uniqueItems':True,'items':enum(STRATEGIES)})
        if len(messages)+1<20 and 'ASSESSING READINESS TO CHANGE' in strategies:
            raise ValueError('CALM-IT readiness assessment is forbidden before turn 20.')
    utterance = candidates(call,prompts,options,state,messages,'counselor',strategies)
    state['strategies'] = (state['strategies']+[strategies[state['selection']['index']]])[-10:]
    return utterance,state
