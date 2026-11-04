"""Validate the small JSON-schema subset used by memory selection and Q3 tools."""
def validate(value, schema):
    kind = schema['type']
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int}[kind]
    if not valid:
        raise ValueError('기억 실험 도구 응답의 자료형이 올바르지 않습니다: ' + kind)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('허용되지 않은 값 또는 턴 번호입니다.')
    if kind == 'object':
        if set(value) != set(schema['properties']):
            raise ValueError('기억 실험 도구 응답의 필드를 확인하세요.')
        for key, sub in schema['properties'].items():
            validate(value[key], sub)
    elif kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100000):
            raise ValueError('선택할 기억 개수의 한도를 초과했습니다.')
        if schema.get('uniqueItems') and len(value) != len(set(value)):
            raise ValueError('같은 턴을 중복 선택할 수 없습니다.')
        for row in value:
            validate(row, schema['items'])
    elif kind == 'string':
        if schema.get('minLength', 0) and len(value.strip()) < schema['minLength']:
            raise ValueError('필수 문자열이 비어 있습니다.')
        if len(value) > schema.get('maxLength', 2000000):
            raise ValueError('문자열 길이 한도를 초과했습니다.')
    elif not schema.get('minimum', value) <= value <= schema.get('maximum', value):
        raise ValueError('정수 값의 범위를 확인하세요.')
    return value
