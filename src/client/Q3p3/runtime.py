"""Q3p3: 당시 확정 기록과 사후 해석의 차이."""
from pathlib import Path
from client.claim_experiments import create_runtime
_runtime = create_runtime(__package__.split(".")[-1], Path(__file__).resolve().parent)
def __getattr__(name):
    return getattr(_runtime, name)
