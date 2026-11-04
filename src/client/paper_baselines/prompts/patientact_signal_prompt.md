<signal>
반응: {{ reaction }} ({{ intensity }}) — {{ reaction_desc }}
행동: {{ behavior }} — {{ behavior_desc }}
{% if resistance_pattern %}저항: {{ resistance_pattern }} — {{ resistance_desc }}{% endif %}

{% if context.items %}
활성화된 내용:
{% for item in context.items %}
- {{ item }}
{% endfor %}
{% endif %}

{% if context.blocked %}
⚠ 이 주제는 당신이 아직 공유할 준비가 되지 않은 민감한 내용에 접근합니다. 그 내용을 공개하지 마세요. 화제를 바꾸거나, 모호하게 답하거나, 말이 없어지거나, 질문을 피해 불편함을 드러내세요.
{% endif %}
</signal>
