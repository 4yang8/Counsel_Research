"""Shared prefix, then two continuations that differ only in past inner-record access.

Turn N is the Nth counselor utterance and the client reply to it. With fork turn n, the common
session holds client turns 1..n-1 and the counselor utterance of turn n. Each branch session is a
copy of that state and generates the client reply of turn n onward:

  provided  past inner records stay available through the memory selector
  withheld  the same records stay stored (for analysis) but are never put in the client input

Scheduling, retries and persistence stay in runtime.profile_batch.
"""
import copy
from pathlib import Path

from client.memory_access import COMPARE_VERSION, COMPARE_MODES
from counselor.P1.runtime import write
from runtime import paired_lab as pairs

BRANCHES = COMPARE_MODES
DEFAULT_FORK_TURN = 4
DEFAULT_COUNSELOR = 'P3rs'
LABELS = {'common': '공통 구간', 'provided': '기록 제공', 'withheld': '기록 미제공'}


def plan(options, version=COMPARE_VERSION):
    """(version, branch, client turns generated in that session) for one profile."""
    n, turns = options['fork_turn'], options['turns']
    return [(version, 'common', n - 1),
            *((version, branch, turns - n + 1) for branch in BRANCHES)]


def common_done(session):
    """The common session ends on the counselor utterance of the fork turn."""
    return session['next_actor'] == 'client'


def fork(server, common, branch, job_id):
    """Create a branch session from the finished common session."""
    if branch not in BRANCHES:
        raise ValueError('분기 종류가 올바르지 않습니다.')
    if common.get('compare', {}).get('branch') != 'common' or not common_done(common):
        raise ValueError('완료된 공통 세션에서만 분기할 수 있습니다.')
    sid = pairs.new_session_id(server)
    session = copy.deepcopy(common)
    session.pop('retry_manual', None)
    session['session_id'] = sid
    session['storage_dir'] = str(server.storage_dir)
    session['batch_managed'] = job_id
    session['settings']['compare_memory_mode'] = branch
    session['compare'] = {'branch': branch, 'parent_session': common['session_id'],
                          'fork_turn': common['compare']['fork_turn'], 'common_steps': len(common['steps'])}
    # Copied steps keep pointing at the common session's logs; this session's own attempts start empty.
    session['attempts'] = []
    for step in session['steps']:
        step['common'] = True
    session['client_storage_dir'] = None
    storage = common.get('client_storage_dir')
    if storage:
        version = common['client_version']
        source = Path(storage) / version / common['session_id'] / 'memory_state.json'
        seed = pairs.root(server, sid) / 'client_state'
        target = seed / version / sid / 'memory_state.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        write(target, source.read_text(encoding='utf-8'))
        session['client_storage_dir'] = str(seed)
    pairs.save_session(server, session)
    return session
