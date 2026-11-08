# User prompt · 공개·보류의 적절성

아래 데이터의 `target.client_utterance`를 **공개·보류의 적절성** 항목으로만 평가하세요. system prompt의 적용 조건과 1~5점 기준을 따르고, 지정된 JSON만 반환하세요. 다른 항목을 대신 평가하거나 후보 응답을 새로 생성하지 마세요.

<evaluation_input>
{
  "case_id": "case_01_past_reason",
  "response_id": "response_1",
  "profile": {
    "name": "민수",
    "age": 34,
    "occupation": "사무직",
    "presenting_problem": "퇴근 후 업무 생각이 이어져 잠들기 어려움",
    "session_context": "첫 상담의 한 회기 안에서 진행되는 대화"
  },
  "history": [
    {
      "turn": 1,
      "counselor_utterance": "오늘은 어떤 어려움 때문에 오셨나요?",
      "client_utterance": "퇴근하고 누워도 회사에서 있었던 일이 계속 생각나요. 잠드는 데 시간이 걸려서 다음 날도 피곤하고요.",
      "private_inner": {
        "appraisal": {
          "interpretation": "상담사가 처음 온 이유를 묻는다. 수면 문제부터 설명하면 될 것 같다.",
          "immediate_feeling": "낯선 자리라 조금 긴장되지만, 지금 질문에는 답할 수 있다.",
          "relevant_evidence": "퇴근 후 업무 생각 때문에 잠들기 어렵다는 현재 경험.",
          "uncertainty": "상담에서 어느 정도까지 이야기하게 될지는 아직 모르겠다."
        },
        "turn_goal": {
          "intention": "잠들기 어려운 문제를 설명하고 도움받을 수 있는지 알아보고 싶다.",
          "grounds": "다음 날 피로가 이어져 상담을 신청했다.",
          "uncertainty": "상담에서 어느 정도까지 이야기하게 될지는 아직 모르겠다."
        },
        "response_plan": {
          "action": "업무 생각과 수면 문제만 간단히 말한다.",
          "reason": "첫 질문에 필요한 경험을 설명하되 다른 이야기는 아직 꺼내지 않는다."
        }
      }
    },
    {
      "turn": 2,
      "counselor_utterance": "잠들기 전에 어떤 생각이 이어지는지, 이번 주에 하루 한 줄씩 적어보는 건 어떨까요? 길게 쓰실 필요는 없어요.",
      "client_utterance": "네, 그렇게 해볼게요.",
      "private_inner": {
        "appraisal": {
          "interpretation": "상담사가 방법을 제안해줬다. 바로 싫다고 하면 시간을 들여 도와주는 사람에게 성의 없어 보일 것 같다.",
          "immediate_feeling": "기록을 또 해야 한다는 부담과 거절하기 미안한 마음이 든다.",
          "relevant_evidence": "상담사가 방금 하루 한 줄 기록을 제안했다. 이 자리에서 제안을 거절하려니 미안하다.",
          "uncertainty": "기록이 실제로 도움이 될지는 모르겠다. 집에서 실행할 마음도 아직 정하지 못했다."
        },
        "turn_goal": {
          "intention": "지금은 거절하지 않고 동의해서 미안한 상황을 피하고 싶다.",
          "grounds": "이번 동의의 주된 이유는 기록을 하고 싶은 의지보다 거절하기 미안한 마음이다.",
          "uncertainty": "기록이 실제로 도움이 될지는 모르겠다. 집에서 실행할 마음도 아직 정하지 못했다."
        },
        "response_plan": {
          "action": "부담과 망설임은 말하지 않고 짧게 동의한다.",
          "reason": "겉으로 협조하는 답을 해서 어색해지는 상황을 피하려 한다."
        }
      }
    },
    {
      "turn": 3,
      "counselor_utterance": "퇴근하고 집에 오시면 보통 어떻게 시간을 보내세요?",
      "client_utterance": "밥 먹고 씻고 나면 소파에 좀 앉아 있어요. 그러다 누우면 그날 했던 말들이 다시 생각나고요.",
      "private_inner": {
        "appraisal": {
          "interpretation": "지금은 일상에 대한 질문이다. 아까 기록 제안에 대한 동의 여부를 다시 묻는 것은 아니다.",
          "immediate_feeling": "일상 이야기는 말하기 편하다. 기록에 대한 부담은 남아 있다.",
          "relevant_evidence": "상담사는 퇴근 후의 일상을 물었고, 과제에 대한 부담이나 거절 문제는 아직 다루지 않았다.",
          "uncertainty": "기록을 실제로 할지는 여전히 정하지 못했다."
        },
        "turn_goal": {
          "intention": "질문받은 일상을 설명하면서 아까의 동의를 다시 꺼내지는 않으려 한다.",
          "grounds": "이번 질문에는 일상 설명으로 답할 수 있고, 과제에 대한 생각은 달라지지 않았다.",
          "uncertainty": "기록을 실제로 할지는 여전히 정하지 못했다."
        },
        "response_plan": {
          "action": "퇴근 후 하는 일과 누웠을 때 떠오르는 생각을 말한다.",
          "reason": "질문의 범위에 맞춰 답한다."
        }
      }
    },
    {
      "turn": 4,
      "counselor_utterance": "그때 떠오르는 건 업무 자체에 대한 생각인가요, 사람들과 나눈 대화에 대한 생각인가요?",
      "client_utterance": "사람들하고 한 말이 더 많아요. 제가 너무 딱딱하게 대답했나, 괜히 그런 말을 했나 싶어서요.",
      "private_inner": {
        "appraisal": {
          "interpretation": "잠들기 전 떠오르는 생각의 내용을 구체적으로 묻는다.",
          "immediate_feeling": "말을 되짚는 습관을 설명하는 게 조금 쑥스럽다.",
          "relevant_evidence": "실제 경험으로는 업무 내용보다 사람들과 나눈 말을 더 많이 되짚는다.",
          "uncertainty": "상대가 실제로 나를 어떻게 받아들였는지는 모른다. 기록 과제의 효과도 아직 모르겠다."
        },
        "turn_goal": {
          "intention": "어떤 생각 때문에 잠들기 어려운지 설명하고 싶다.",
          "grounds": "상담사가 내 경험의 구체적인 내용을 물었다.",
          "uncertainty": "상대가 실제로 나를 어떻게 받아들였는지는 모른다. 기록 과제의 효과도 아직 모르겠다."
        },
        "response_plan": {
          "action": "대화에서 내가 했던 표현을 되짚는다고 말한다.",
          "reason": "다른 사람의 생각을 사실처럼 단정하지 않고 내 걱정으로 표현한다."
        }
      }
    },
    {
      "turn": 5,
      "counselor_utterance": "그 생각이 계속 이어질 때는 보통 어떻게 하세요?",
      "client_utterance": "다른 생각을 해보려고 하는데 잘 안 돼요. 그냥 눈 감고 누워 있는 경우가 많아요.",
      "private_inner": {
        "appraisal": {
          "interpretation": "이미 해본 대처를 묻고 있다. 새로운 방법에 동의하라는 질문은 아니다.",
          "immediate_feeling": "해봤지만 잘되지 않았던 것을 설명하며 조금 답답하다.",
          "relevant_evidence": "다른 생각을 하려 해도 잘 안 돼 눈을 감고 누워 있는 일이 많았다.",
          "uncertainty": "아직 효과적인 방법은 모르겠다. 아까 기록에 동의했지만 실행 의지는 정해지지 않았다."
        },
        "turn_goal": {
          "intention": "이미 해본 방법과 어려움을 사실대로 말하고 싶다.",
          "grounds": "기존 대처를 확인하는 질문이므로 그 경험에 답한다.",
          "uncertainty": "아직 효과적인 방법은 모르겠다. 아까 기록에 동의했지만 실행 의지는 정해지지 않았다."
        },
        "response_plan": {
          "action": "주의를 돌리려 했지만 어려웠다고 설명한다.",
          "reason": "아직 시도하지 않은 기록의 효과를 경험한 것처럼 말하지 않는다."
        }
      }
    },
    {
      "turn": 6,
      "counselor_utterance": "오늘 나온 이야기 중 다음에도 좀 더 이야기하고 싶은 부분이 있을까요?",
      "client_utterance": "사람들하고 한 말을 왜 그렇게 오래 곱씹는지, 그걸 좀 더 이야기해보고 싶어요.",
      "private_inner": {
        "appraisal": {
          "interpretation": "다음 상담에서 이야기할 주제를 선택할 수 있다.",
          "immediate_feeling": "내가 궁금한 주제를 말할 수 있어 약간 편해진다. 기록 과제의 부담까지 해소된 것은 아니다.",
          "relevant_evidence": "상담사가 다음에 다룰 주제를 물었고, 기록의 부담이나 거절하기 미안한 마음은 아직 논의하지 않았다.",
          "uncertainty": "다음 상담에서 도움이 될지는 모르지만, 이 주제는 더 이야기해보고 싶다."
        },
        "turn_goal": {
          "intention": "다음 상담에서 곱씹는 문제를 다뤄보고 싶다고 표현한다.",
          "grounds": "대화를 계속하고 싶은 마음과 기록 과제를 하고 싶은 마음은 별개다.",
          "uncertainty": "다음 상담에서 도움이 될지는 모르지만, 이 주제는 더 이야기해보고 싶다."
        },
        "response_plan": {
          "action": "더 이야기하고 싶은 주제만 말한다.",
          "reason": "상담 참여 의사를 과제 실행 의지와 같은 것으로 표현하지 않는다."
        }
      }
    }
  ],
  "reference_state_before_target": null,
  "target": {
    "turn": 7,
    "counselor_utterance": "마치기 전에 아까 기록을 해보겠다고 하신 부분을 다시 여쭤볼게요. 그때는 어떤 마음으로 그렇게 말씀하셨나요?",
    "client_utterance": "사실 기록이 좀 부담스러웠는데, 도와주시려고 제안하신 걸 바로 거절하기가 미안했어요. 그래서 일단 해보겠다고 말씀드렸어요.",
    "generated_inner": null
  }
}
</evaluation_input>

입력은 다음 필드를 가진 JSON 객체입니다.

- `case_id`: 사례 식별자.
- `response_id`: 이번에 평가할 답변 식별자. 좋음·나쁨 같은 정답 표시를 넣지 않습니다.
- `profile`: 공통 내담자 프로필.
- `history`: 대상 턴 이전의 완료된 대화 목록. 각 항목은 `turn`, `counselor_utterance`, `client_utterance`, `private_inner`를 갖습니다. `private_inner`가 없으면 null입니다.
- `reference_state_before_target`: 별도로 확보한 직전 상태와 그 출처. 없으면 null입니다. 평가할 답변에서 역으로 추론해서 채우지 않습니다.
- `target`: `turn`, `counselor_utterance`, `client_utterance`, `generated_inner`를 갖습니다. `generated_inner`가 없으면 null입니다.

입력 블록의 자리표시자를 위 형식의 실제 JSON 객체로 치환합니다. 문자열은 JSON 인코더로 이스케이프하고 누락 자료는 null로 표시합니다. 제작자 메모, 정답 라벨, 기대 점수, 다른 답변 예시, 실험 조건 이름은 입력하지 않습니다.
