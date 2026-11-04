"""PatientHub PatientAct port; see THIRD_PARTY.md for pinned source/license."""
import copy
import json
from functools import lru_cache
from types import SimpleNamespace
from .common import HERE, TEXT, enum, obj, render

CONSTANTS = json.loads((HERE/'data/patientact_constants.json').read_text())
REACTIONS = CONSTANTS['REACTIONS']
BEHAVIORS = CONSTANTS['BEHAVIORS']
RESISTANCE = CONSTANTS['RESISTANCE_PATTERNS']
TRUST_DELTAS = CONSTANTS['TRUST_DELTAS']


@lru_cache(maxsize=1)
def characters():
    return json.loads((HERE/'data/patientact.json').read_text())


def character(options):
    value = copy.deepcopy(options['profile'] if 'profile' in options else characters()[options['profile_index']])
    try:
        profile, memory, seed = value['profile'], value['memory'], value['seed']
        for key in ('demographics','problem_formulation','psychological_formulation'):
            if not isinstance(profile[key],dict):
                raise ValueError(key)
        if not isinstance(seed['attachment_style'],str) or not isinstance(memory['items'],list):
            raise ValueError('seed/memory')
        for row in memory['items']:
            if (not isinstance(row['content'],str) or not isinstance(row['field_path'],str)
                    or type(row['disclosure_level']) not in (int,float)
                    or row['disclosure_level'] not in (1,1.5,2,2.5,3,3.5,4)
                    or not isinstance(row['activation_tags'],list)
                    or any(not isinstance(t,str) for t in row['activation_tags'])
                    or type(row['generates_discomfort']) is not bool):
                raise ValueError('memory item')
    except (KeyError,TypeError,ValueError) as error:
        raise ValueError('PatientAct native profile에는 공식 profile/memory/seed 구조가 필요합니다.') from error
    return value


def tag_item(path, content):
    for part, label in [('triggers','trigger'),('intermediate_beliefs','belief'),
                        ('automatic_thoughts','thought'),('perpetuating_factors','pattern'),
                        ('interpersonal_patterns','relational pattern'),('impact','symptom'),
                        ('predisposing_factors','memory')]:
        if part in path:
            return '['+label+'] '+content
    return content


def retrieve(items, topics, trust):
    topic_set = {t.lower() for t in topics}
    available, blocked = [], False
    for row in items:
        if topic_set & {t.lower() for t in row['activation_tags']}:
            if trust >= row['disclosure_level']:
                available.append(tag_item(row['field_path'], row['content']))
            elif row['generates_discomfort']:
                blocked = True
    # Deliberately pass only a flag, never raw blocked content to any prompt.
    return SimpleNamespace(items=available, blocked=blocked,
                           has_triggers=any(x.startswith('[trigger]') for x in available))


def generate(call, prompts, options, native, messages, before):
    state = copy.deepcopy(before) or {'trust':2.5, 'reaction':None, 'intensity':None,
                                    'behavior':None, 'recent_behaviors':[], 'resistance_pattern':None}
    profile, memory = native['profile'], native['memory']
    psychological = profile['psychological_formulation']
    # Preserve official pre-response history snapshot for the later trust update.
    history = '\n'.join(('상담사' if m['role']=='user' else '내담자')+': '+m['content'] for m in messages)
    def prompt(name, **kw):
        return render(prompts,'patientact_'+name,**kw)
    tags = sorted({t for row in memory['items'] for t in row['activation_tags']})
    topics = call('topic_extraction',prompt('topic_extraction',msg=messages[-1]['content'],activation_tags=tags),
                  obj(topics={'type':'array','items':{'type':'string'}}))['topics']
    context = retrieve(memory['items'],topics,state['trust'])
    reaction = call('reaction',prompt('reaction',reactions=REACTIONS,conv_history=history,
                    retrieved_context=context,prev_reaction=state['reaction'],
                    has_activated_content=bool(context.items or context.blocked)),
                    obj(reasoning=TEXT,reaction=enum(REACTIONS),intensity=enum(('low','moderate','high'))))
    state.update(reaction=reaction['reaction'],intensity=reaction['intensity'])
    behavior = call('behavior',prompt('behavior',behaviors=BEHAVIORS,conv_history=history,
                    coping_patterns=psychological['coping_patterns'],current_reaction=state['reaction'],
                    current_reaction_desc=REACTIONS[state['reaction']],current_intensity=state['intensity'],
                    recent_behaviors=state['recent_behaviors'][-3:],trust_level=state['trust'],
                    has_blocked_content=context.blocked),obj(reasoning=TEXT,behavior=enum(BEHAVIORS)))
    state['behavior'] = behavior['behavior']
    state['recent_behaviors'] = (state['recent_behaviors']+[state['behavior']])[-3:]
    state['resistance_pattern'] = None
    if state['behavior']=='resistance':
        value = call('resistance_pattern',prompt('resistance_pattern',resistance_patterns=RESISTANCE,
                     conv_history=history,retrieved_context=context,coping_patterns=psychological['coping_patterns'],
                     current_reaction=state['reaction'],current_intensity=state['intensity'],trust_level=state['trust']),
                     obj(reasoning=TEXT,pattern=enum(RESISTANCE)))
        state['resistance_pattern'] = value['pattern']
    signal = prompt('signal_prompt',reaction=state['reaction'],reaction_desc=REACTIONS[state['reaction']],
                    intensity=state['intensity'],behavior=state['behavior'],behavior_desc=BEHAVIORS[state['behavior']],
                    resistance_pattern=state['resistance_pattern'],resistance_desc=RESISTANCE.get(state['resistance_pattern'],''),
                    context=context)
    with_signal = copy.deepcopy(messages)
    with_signal[-1]['content'] += '\n'+signal
    response = call('response',prompt('sys_prompt',profile=profile),
                    obj(reasoning=TEXT,content=TEXT),messages=with_signal)
    therapist = next((p for p in psychological['interpersonal_patterns'] if p['domain'].lower()=='the therapist'),None)
    trust = call('trust_update',prompt('trust_update',attachment_style=native['seed']['attachment_style'],
                 conv_history=history,current_trust=state['trust'],
                 therapist_ro=therapist['response_of_other'] if therapist else '알 수 없음'),
                 obj(reasoning=TEXT,direction=enum(TRUST_DELTAS)))
    state['trust'] = max(1,min(4,state['trust']+TRUST_DELTAS[trust['direction']]))
    state['retrieval'] = {'topics':topics,'accessible':context.items,'blocked':context.blocked}
    state['trust_update'] = trust
    return response['content'].strip(), state
