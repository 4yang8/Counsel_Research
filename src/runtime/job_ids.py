"""Korea-time, human-readable job IDs, reserved atomically across processes."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

PATTERN = r'[0-9]{4}_[0-9]{6}(?:_[0-9]{2,})?'


def valid(value):
    return isinstance(value, str) and re.fullmatch(PATTERN, value) is not None


def reserve(root, other_roots=()):
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%m%d_%H%M%S')
    index = 1
    while True:
        name = stamp if index == 1 else f'{stamp}_{index:02d}'
        if any((other / name).exists() for other in other_roots if other != root):
            index += 1
            continue
        try:
            (root / name).mkdir()
            return name
        except FileExistsError:
            index += 1
