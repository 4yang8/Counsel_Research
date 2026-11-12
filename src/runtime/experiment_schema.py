"""Small strict schema validation shared by the new experimental pipelines."""
import copy


def obj(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def array(items, maximum=100):
    return {'type': 'array', 'items': items, 'maxItems': maximum}


def enum(*values):
    return {'type': 'string', 'enum': list(values)}


TEXT = {'type': 'string', 'maxLength': 5000}
TURN = {'type': 'integer', 'minimum': 1}


def validate(value, schema):
    kind = schema['type']
    types = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool, 'number': (int, float)}
    if not isinstance(value, types[kind]) or kind in ('integer', 'number') and isinstance(value, bool):
        raise ValueError('실험 응답 자료형 오류: ' + kind)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('실험 응답 선택지 오류: ' + str(value))
    if kind == 'object':
        if set(value) != set(schema['properties']):
            raise ValueError('실험 응답 필드 오류: ' + str(set(value) ^ set(schema['properties'])))
        for k, s in schema['properties'].items():
            validate(value[k], s)
    elif kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10000):
            raise ValueError('실험 응답 목록 길이 오류')
        if schema.get('uniqueItems') and len(value) != len({str(x) for x in value}):
            raise ValueError('중복 항목')
        for x in value:
            validate(x, schema['items'])
    elif kind == 'string':
        if not schema.get('minLength', 0) <= len(value.strip()) <= schema.get('maxLength', 2000000):
            raise ValueError('실험 응답 문자열 길이 오류')
    elif kind in ('integer', 'number'):
        if not schema.get('minimum', value) <= value <= schema.get('maximum', value):
            raise ValueError('실험 응답 숫자 범위 오류')
    return value


def wire(schema):
    """Grammar-compatible copy; uniqueness remains enforced locally."""
    result = copy.deepcopy(schema)
    def visit(x):
        if isinstance(x, dict):
            x.pop('uniqueItems', None)
            for v in x.values(): visit(v)
        elif isinstance(x, list):
            for v in x: visit(v)
    visit(result)
    return result
