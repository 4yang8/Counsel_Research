상담사의 가장 최근 메시지에 대한 내담자의 감정 반응을 판단하세요.

## 가능한 반응
{% for key, desc in reactions.items() %}
- {{ key }}: {{ desc }}
{% endfor %}

{% if prev_reaction %}## 이전 반응: {{ prev_reaction }}{% endif %}

## 대화
{{ conv_history }}

{% if has_activated_content %}
## 이 말이 내담자 안에서 활성화하는 내용
{% for item in retrieved_context.items %}
- {{ item }}
{% endfor %}
{% if retrieved_context.has_triggers %}
참고: [trigger] 항목은 괴로움을 직접 촉발하므로 반응이 더 강할 수 있습니다.
{% endif %}
{% if retrieved_context.blocked %}
- [민감한 영역] 이 주제는 내담자가 아직 이야기할 준비가 되지 않은 내용에 접근합니다.
{% endif %}
{% else %}
## 내담자의 신념 체계나 과거 경험에서 특별히 활성화되는 내용이 없습니다.
{% endif %}

{% if prev_reaction %}이전 반응: {{ prev_reaction }}{% endif %}

반응과 그 강도(low=낮음, moderate=중간, high=높음)를 판단하세요. 활성화된 내용이 없다면 강도가 low인 "no_reaction"일 가능성이 가장 높습니다.
