"""Version-owned prompts and isolated Q3 recording runtime."""
from pathlib import Path
from client.recording_experiments import create_runtime
_runtime=create_runtime(__package__.split('.')[-1],Path(__file__).resolve().parent)
def __getattr__(name):
    return getattr(_runtime,name)
