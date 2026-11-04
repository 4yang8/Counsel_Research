# 인공 기억 선별 비교 실험

- [후보 기록 60개](RECORDS.md): 모델로 대화를 생성하지 않고 직접 작성한 가상 내면 기록＋발화.
- [질문 10개](QUESTIONS.md) / [모델에 제공하지 않는 사전 정답](GOLD.md) / [사전 계획](PROTOCOL.md).
- [완료된 결과 보고서](runs/20261104_223825/REPORT.md): RAG·전체 기록 LLM 선별, 각 질문 2회, 총40개 선별 실행.
- 추가 원문 질문 검색 진단은 사후 진단으로 분리했다.

## 실행

프로젝트 루트에서:

```bash
python experiments/memory_selection_hard_20261104/run_experiment.py
python experiments/memory_selection_hard_20261104/analyze_results.py experiments/memory_selection_hard_20261104/runs/<실행시각>
```

실행할 때마다 별도 runs 하위 폴더에 입력·프롬프트·API 요청·응답·평가 결과를 저장한다. 생성 서버 기본값은 192.168.0.94:8002/v1, 임베딩 서버는 192.168.0.84:18002/v1. 명령행 옵션으로 변경 가능하다. 재생성기 build_fixture.py는 직접 작성된 기록을 JSON과 Markdown으로 출력하며 모델을 호출하지 않는다. 분석기를 재실행하면 자동 집계 보고서를 다시 만들므로 기존 수동 해석은 덮어쓸 수 있다.
