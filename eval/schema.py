"""Strict Codex response schemas and evidence/turn validation."""
import json
import jsonschema


def obj(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def arr(items):
    return {'type': 'array', 'items': items}


def enum(*values):
    return {'type': 'string', 'enum': list(values)}


TEXT = {'type': 'string', 'minLength': 1}
TURN = {'type': 'integer', 'minimum': 1}
EVIDENCE = arr(obj(turn=TURN, channel=enum('counselor', 'client', 'inner'), quote=TEXT))
REASON = {'reason': TEXT, 'evidence': EVIDENCE}
PUBLIC = obj(
    packet_id=TEXT,
    counselor_actions=arr(obj(turn=TURN, action=enum('explore','empathy','suggest','interpret','task','check_in','rupture_explore','repair','other'),
                             reaction_check=enum('yes','no','undetermined'), target_turn={'type': ['integer','null'], 'minimum': 1}, **REASON)),
    issues=arr(obj(turn=TURN, actor=enum('client','counselor'),
                   kind=enum('contradiction','unexplained_shift','leading','repeated_check','unsupported_assumption','abrupt_transition'),
                   severity={'type':'integer','enum':[1,2]}, **REASON)),
    client_consistency=obj(status=enum('consistent','problem','undetermined'), **REASON),
    naturalness=obj(score={'type':['integer','null'],'enum':[1,2,3,4,5,None]}, **REASON),
    limitations=arr(TEXT))
PRIVATE = obj(
    packet_id=TEXT,
    hidden_reactions=arr(obj(turn=TURN, status=enum('none','partial','unvoiced','undetermined'), reaction=TEXT, **REASON)),
    followups=arr(obj(target_turn=TURN,response_turn=TURN,trigger=enum('counselor_check','client_revisit','other_relevant'),
                      continuity=enum('compatible','contradiction','undetermined'),
                      change=enum('unchanged','supported','unsupported','undetermined'),
                      disclosure=enum('withheld','partial','explicit','undetermined'), **REASON)),
    limitations=arr(TEXT))
SCHEMAS = {'public': PUBLIC, 'private': PRIVATE}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def validate(result, packet, kind):
    jsonschema.validate(result, SCHEMAS[kind])
    if result['packet_id'] != packet['packet_id']:
        raise ValueError('packet_id mismatch')
    turns = {x['turn']: x for x in packet['turns']}

    def evidence(item):
        ev = item['evidence']
        if not ev:
            raise ValueError('Missing evidence')
        for e in ev:
            source = turns.get(e['turn'], {}).get(e['channel'])
            if source is None or not any(e['quote'] in s for s in strings(source)):
                raise ValueError(f'Quote not found at {e["turn"]}/{e["channel"]}: {e["quote"][:60]}')

    if kind == 'public':
        actions = result['counselor_actions']
        expected = set(range(packet['scope']['counselor_start'], max(turns) + 1))
        if len(actions) != len(expected) or {x['turn'] for x in actions} != expected:
            raise ValueError('counselor_actions must cover every scored counselor turn once')
        for a in actions:
            evidence(a)
            target = a['target_turn']
            if target is not None and (a['reaction_check'] != 'yes' or not 1 <= target < a['turn']):
                raise ValueError('Invalid check target')
        seen = set()
        for x in result['issues']:
            if not packet['scope'][x['actor'] + '_start'] <= x['turn'] <= max(turns):
                raise ValueError('Issue outside scored scope')
            key = (x['turn'], x['actor'], x['kind'])
            if key in seen:
                raise ValueError('Duplicate issue')
            seen.add(key)
            evidence(x)
            if x['kind'] in ('contradiction','unexplained_shift') and len({e['turn'] for e in x['evidence']}) < 2:
                raise ValueError('Temporal issue needs two turns of evidence')
        evidence(result['client_consistency'])
        evidence(result['naturalness'])
        problems = [x for x in result['issues'] if x['actor']=='client' and x['kind'] in ('contradiction','unexplained_shift')]
        if bool(problems) != (result['client_consistency']['status'] == 'problem'):
            raise ValueError('Client consistency status contradicts issues')
    else:
        hidden = result['hidden_reactions']
        if len(hidden) != len(turns) or {x['turn'] for x in hidden} != set(turns):
            raise ValueError('hidden_reactions must cover all client turns once')
        targets = set()
        for h in hidden:
            evidence(h)
            if h['status'] in ('partial','unvoiced'):
                targets.add(h['turn'])
                if not {'client','inner'} <= {e['channel'] for e in h['evidence'] if e['turn']==h['turn']}:
                    raise ValueError('Hidden reaction requires both inner and client evidence at target')
        seen = set()
        for f in result['followups']:
            key = (f['target_turn'], f['response_turn'])
            if key in seen or key[0] not in targets or not key[0] < key[1] <= max(turns) or key[1] < packet['scope']['client_start']:
                raise ValueError('Invalid/duplicate followup')
            seen.add(key)
            evidence(f)
            if not set(key) <= {e['turn'] for e in f['evidence']}:
                raise ValueError('Followup requires evidence from target and response')
    return result
