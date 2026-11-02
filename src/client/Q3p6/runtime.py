from pathlib import Path
from client.claim_experiments import create_runtime
_runtime = create_runtime('Q3p6', Path(__file__).resolve().parent)
def __getattr__(name):
    return getattr(_runtime, name)
