공개 대화만 평가한다. 내담자의 숨은 심리를 추정해 사실로 판정하지 않는다.

1. counselor_actions: counselor_start부터 끝까지 모든 상담사 턴을 한 번씩 분류한다.
   action: explore(내용 탐색), empathy(공감/반영), suggest(관점/조언), interpret(해석), task(실천 제안), check_in(자기 발언이 어떻게 들렸는지 확인), rupture_explore(관계의 불편/물러남을 짚는 탐색), repair(반응을 받아들이고 방향 조정), other. 복수 기능이면 주된 하나를 선택한다.
   reaction_check: yes/no/undetermined. 행동 라벨과 별도로 실제로 상담사의 이전 발언에 대한 반응을 묻는지 판정한다. 단순 '어떤 감정이 드나요?' 또는 과거 생활 사건 질문은 자동으로 yes가 아니다.
   target_turn: yes일 때 질문이 가리키는 이전 상담사 발언의 turn. 특정 불가능하면 null. 현재 확인 질문 턴보다 작아야 한다. 생성자의 라벨은 제공되지 않는다.

2. issues: 평가 구간의 구체적인 문제 사건을 기록한다. 동일 문제를 여러 종류/턴으로 중복 집계하지 말고 최초로 확인 가능한 구간 내 턴에 한 사건으로 기록하라.
   kind: contradiction(양립 불가능한 공개 발언), unexplained_shift(해석에 영향을 주는 입장 전환이 있으나 경과 설명 부족), leading(특정 숨은 반응/정답을 유도), repeated_check(이미 충분히 답한 확인을 이유 없이 반복), unsupported_assumption(상담사가 내담자의 숨은 반응을 근거 없이 단정), abrupt_transition(맥락 연결 없는 전환).
   actor는 문제를 낸 client 또는 counselor. 문제 없으면 빈 배열. 애매한 의심은 issues에 넣지 말고 limitations에 남긴다. contradiction/unexplained_shift는 두 시점의 근거가 필요하다. 질문에 새 정보를 보탠 것, 관점이 다양하거나 복잡한 것은 자체로 모순이 아니다.

3. client_consistency: consistent/problem/undetermined. 평가 구간 내 내담자의 공개적 모순/설명 없는 입장 변경이 실제 확인되면 problem; 판단 가능한데 그런 문제가 없으면 consistent; 부족하면 undetermined. issues와 일치시킨다.
4. naturalness: 평가 구간의 상호작용 흐름에 대한 1~5 또는 null, reason, evidence. 모든 기준을 하나의 임의 종합점수로 합치지 않는다.
