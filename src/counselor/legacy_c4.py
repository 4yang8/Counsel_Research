"""Optional compatibility boundary for the discontinued C4 counselor."""
import sys
from project_paths import PROJECT_ROOT


def archive_root():
    return PROJECT_ROOT / 'archive/cv'


def pipeline_class():
    root = archive_root()
    for directory in (root / 'src', root / 'src_c'):
        if str(directory) not in sys.path:
            sys.path.append(str(directory))
    from C4.pipeline import DiagnosticCounselorPipeline
    return DiagnosticCounselorPipeline
