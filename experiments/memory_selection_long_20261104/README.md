# 30턴 장문 기억 선별 실험

- [긴 비공개 기록 30턴](RECORDS_LONG.md): 턴당 평균 약1114자. 모델 호출 없이 직접 작성.
- [같은 30턴의 짧은 대조 기록](RECORDS_SHORT.md): 턴당 평균 약137자.
- [질문10개](QUESTIONS.md), [사전 정답](GOLD.md), [사전 계획](PROTOCOL.md).
- [결과 보고서](REPORT.md): 두 조건 완료 후 생성. 실제 요청·응답은 runs 아래 각 조건 폴더에 보존.
- [문맥 한도 확인](token_preflight.json), [이전60턴→현재30턴 ID 대응](turn_mapping.json).

## 재실행

프로젝트 루트에서 실행한다. 기존 결과를 덮어쓰지 않고 매번 새 실행 폴더를 만든다.

```bash
python experiments/memory_selection_long_20261104/run_experiment.py --condition short
python experiments/memory_selection_long_20261104/run_experiment.py --condition long
python experiments/memory_selection_long_20261104/analyze_comparison.py --short <짧은조건 실행폴더> --long <긴조건 실행폴더>
```

기록은 JSON과 Markdown으로 함께 제공한다. build_fixture.py는 직접 작성한 내용을 출력하는 재생성 스크립트이며 LLM으로 내용을 만들지 않는다. 원래 실험과 앱의 코드·프롬프트·설정은 수정하지 않았다.
