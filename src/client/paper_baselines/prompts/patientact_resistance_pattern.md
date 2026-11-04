내담자가 저항하고 있습니다. 저항의 구체적인 형태를 판단하세요.

## 가능한 유형
{% for key, desc in resistance_patterns.items() %}
- {{ key }}: {{ desc }}
{% endfor %}

## 맥락
- 반응: {{ current_reaction }} ({{ current_intensity }})
- 신뢰도: {{ trust_level }}/4.0

## 대처 패턴
{% for pattern in coping_patterns %}
- {{ pattern }}
{% endfor %}

{% if retrieved_context.blocked %}
이 주제는 내담자가 아직 공유할 준비가 되지 않은 민감한 내용에 접근합니다.
{% endif %}

## 최근 대화
{{ conv_history }}

지금 이 내담자가 보일 저항 방식에 가장 잘 맞는 유형을 선택하세요.
