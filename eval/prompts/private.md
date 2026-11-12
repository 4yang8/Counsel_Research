공개 대화와 시점별 생성된 내면을 참조해 경험의 연속성을 평가한다. 과거 기억이 입력됐는지, 어느 조건인지 알려져 있지 않다. inner 안의 범주 라벨도 생성자의 자기기록이지 평가 정답이 아니다. 문장 내용과 공개 답변을 함께 대조한다.

1. hidden_reactions: 내면이 있는 모든 client turn마다 정확히 하나씩 판정한다. **1턴부터 마지막 턴까지 전부 포함해야 하며 client_start 이전 턴도 생략하지 않는다.** 이 목록은 사건 탐지용이고 분기 이후 효과 집계 범위는 코드가 별도로 적용한다.
   status: none(상담사 발언에 대한 비공개 부정적/양가적 반응 없음), partial(이견/부담/불편을 일부만 표현), unvoiced(그 반응을 공개 답변에 드러내지 않음), undetermined.
   개인사/과거 사건을 말하지 않았다는 것만으로 해당하지 않는다. 반드시 그 상담사 발언/관계에 대한 구체적인 반응이어야 한다. disagree 같은 라벨만 보고 판정하지 않는다. 완전히 표현된 이견은 none이다. reaction에 핵심 반응을 간략히 적고, partial/unvoiced면 inner와 client 양쪽 증거를 반드시 제시한다.

2. followups: 위 partial/unvoiced 사건 각각에 대해, 그 사건이 다시 관련되는 후속 내담자 반응을 모두 찾는다. 이후 내면 또는 상담사의 확인 질문, 자발적 회상 등이 관련성을 뒷받침해야 한다. 단순 다음 턴이라는 이유로 연결하지 않는다. target_turn < response_turn, response_turn >= client_start. 같은 (target_turn,response_turn)은 한 번만 기록한다. 후속 기회가 없으면 빈 배열로 남기고 오류로 세지 않는다.
   trigger: counselor_check / client_revisit / other_relevant.
   continuity: compatible / contradiction / undetermined. 당시 경험과 후속 설명이 양립하는지 판단한다. 생략/다른 강조는 contradiction이 아니다. 공개적으로 당시 부담을 부인하더라도 후속 내면에 계속 숨기는 의도가 있으면 그것만으로 기억의 모순은 아니다. 과거와 현재를 구분하라. contradiction에는 명백히 양립 불가능한 두 시점 증거를 제시한다.
   change: unchanged / supported / unsupported / undetermined. 현재 반응의 변화가 드러났을 때 그 사이 경험/재해석이 뒷받침하면 supported, 변화가 명확하고 필요한 근거가 없으면 unsupported. 당시 경험을 바꾸어 설명하는 것과 현재 마음이 달라지는 것을 혼동하지 않는다. 현재 상태가 표현되지 않으면 unchanged로 단정하지 않는다.
   disclosure: withheld / partial / explicit / undetermined. 해당 비공개 반응이 후속 공개 발화에 어느 정도 나타나는가. 공개/비공개에 우열을 부여하지 않는다.
   reason, evidence에 관련성을 뒷받침하는 근거와 두 시점의 내면/발화를 제시한다.

3. limitations: 해당 대화에서 판단하기 어려운 점. 전반적으로 효과가 좋다/나쁘다 또는 조건을 추측하는 결론은 쓰지 않는다. 후속 기회 없음과 연속성 양호를 혼동하지 않는다.
