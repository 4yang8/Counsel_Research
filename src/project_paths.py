"""Repository locations and read-time relocation of historical session pointers."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESOURCE_DIR = PROJECT_ROOT / 'resources'
CONFIG_FILE = PROJECT_ROOT / 'config/app.json'
RUNS_DIR = PROJECT_ROOT / 'runs'


def relocate_path(value):
    if not isinstance(value, str):
        return value
    old = Path(value)
    if not old.is_absolute():
        return value
    # Match only the former app's storage roots; external storage stays external.
    parts = old.parts
    marker = ('gemma_test', 'counseling_test')
    for index in range(len(parts) - 2):
        if parts[index:index + 2] == marker:
            tail = parts[index + 2:]
            if tail and tail[0] in ('sessions', 'experiments'):
                prefix = 'runs' if tail[0] == 'sessions' else 'experiments'
                candidate = PROJECT_ROOT.joinpath(prefix, *tail[1:])
                if candidate.exists() or not old.exists():
                    return str(candidate)
    return value


def relocate_session(data):
    # Preserve model inputs and frozen settings; adapt filesystem pointers only.
    for key in ('storage_dir', 'client_storage_dir'):
        if key in data:
            data[key] = relocate_path(data[key])
    for collection in ('steps', 'attempts'):
        for row in data.get(collection, []):
            if 'saved_path' in row:
                row['saved_path'] = relocate_path(row['saved_path'])
    return data


def historical_storage_root(root):
    root = Path(root)
    for current, previous in (('runs', 'sessions'), ('experiments', 'experiments')):
        base = PROJECT_ROOT / current
        if root.is_relative_to(base):
            return PROJECT_ROOT / 'gemma_test/counseling_test' / previous / root.relative_to(base)
    return None
