내담자의 감정 반응을 바탕으로 다음 행동을 예측하세요.

## 가능한 행동
{% for key, desc in behaviors.items() %}
- {{ key }}: {{ desc }}
{% endfor %}

## 현재 상태
- 반응: {{ current_reaction }} ({{ current_intensity }}) — {{ current_reaction_desc }}
- 신뢰도: {{ trust_level }}/4.0
{% if has_blocked_content %}- 이 주제는 내담자가 아직 공유할 준비가 되지 않은 민감한 내용에 접근합니다.{% endif %}

## 대처 패턴
{% for pattern in coping_patterns %}
- {{ pattern }}
{% endfor %}

최근 행동(가장 최근 것이 마지막): {{ recent_behaviors }}

## 대화
{{ conv_history }}

## 지침
- 활성화된 내용이 없고 반응이 중립적이면 → "simple_response" 또는 "recounting".
- 반응 강도가 높고 신뢰도가 낮으면 → "resistance" 가능성이 높습니다.
- 주제가 공개 제한된 내용에 닿으면 → "resistance" 가능성이 높습니다.
- 반응이 긍정적이고 강도가 중간이면 → 치료적 행동.
- 반응이 중립적이거나 강도가 낮으면 → "simple_response" 또는 "recounting".
- 같은 행동을 세 턴 연속 반복하지 마세요.
- 치료적 행동(cognitive_exploration, affective_exploration, insight, discussing_plans)이 두 번 이상 연속되면 내담자는 자연스럽게 한발 물러납니다. 더 단순하게 답하거나, 의심하거나, 화제를 피할 수 있습니다.

가장 적절한 행동 하나를 선택하세요.
