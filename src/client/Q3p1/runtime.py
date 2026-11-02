"""Q3p1: 현재 상태·의도 포함 요약의 실패 조건."""
from pathlib import Path
from client.claim_experiments import create_runtime
_runtime = create_runtime(__package__.split(".")[-1], Path(__file__).resolve().parent)
def __getattr__(name):
    return getattr(_runtime, name)
