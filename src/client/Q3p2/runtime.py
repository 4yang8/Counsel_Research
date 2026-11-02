"""Q3p2: 동일한 의도 내용에서 발화·시점 연결의 효과."""
from pathlib import Path
from client.claim_experiments import create_runtime
_runtime = create_runtime(__package__.split(".")[-1], Path(__file__).resolve().parent)
def __getattr__(name):
    return getattr(_runtime, name)
