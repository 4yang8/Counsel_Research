# 문맥 의존 장문 기억 선별 · 동일 N 비교

- [기록 30턴](RECORDS.md): 턴당 비공개 기록 평균 약1078자. 고유 색상·회기 제목·scene 필드를 제거하고 일부 사건을 동일 친구의 반복 대화로 변형했다.
- [12개 질문과 직전 문맥](QUESTIONS.md): 마지막 질문의 지시어를 앞선 공개 문맥으로 해석해야 한다.
- [사전 정답](GOLD.md): N=1/2/3 각4문항. 원본 ID는 모델에 제공하지 않는다.
- [사전 계획](PROTOCOL.md): 양쪽 모두 정확히N개 선택. 두 번 반복, 총48회.
- [결과 보고서](runs/20261104_234603/REPORT.md): 실행 완료 후 생성. 요청·응답·실패도 같은 폴더에 보존.

프로젝트 루트에서 실행:

```bash
python experiments/memory_selection_context_hard_20261104/run_experiment.py
python experiments/memory_selection_context_hard_20261104/analyze_results.py experiments/memory_selection_context_hard_20261104/runs/<실행시각>
```

새 실행은 별도 폴더를 만든다. build_fixture.py는 직접 작성한 자료를 출력하는 스크립트이며 모델로 상담이나 기록을 생성하지 않는다. 이전 실험과 앱 코드·설정은 수정하지 않았다.
