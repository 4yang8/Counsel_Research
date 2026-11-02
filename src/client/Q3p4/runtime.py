"""Q3p4: 과거 의도 기억과 상담 개입에 대한 반응."""
from pathlib import Path
from client.claim_experiments import create_runtime
_runtime = create_runtime(__package__.split(".")[-1], Path(__file__).resolve().parent)
def __getattr__(name):
    return getattr(_runtime, name)
