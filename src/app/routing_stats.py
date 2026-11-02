"""Read-only routing dashboard API, scoped to registered storage roots and sessions."""
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

from analysis.routing import VERSIONS, build
from client.registry import client_runtime
from storage import session_browser
from project_paths import relocate_session


def catalog(server):
    entries=[]
    for row in session_browser.discover(server)[0]:
        if row['kind']!='paired' or row['version'] not in VERSIONS:continue
        try:
            session=session_browser.read(Path(row['storage_dir'])/'paired'/row['session_id']/'session.json')
            settings=session.get('settings',{})
            entries.append({**row,'batch_id':settings.get('comparison',{}).get('batch_id'),
                'profile_id':settings.get('scenario',{}).get('profile',{}).get('profile_id',''),
                'client_turns':sum(s.get('actor')=='client' and not s.get('manual') for s in session.get('steps',[]))})
        except (OSError,ValueError,KeyError,TypeError):continue
    return {'sessions':entries}


def analyze(server,key,start=1,end=None):
    storage,kind,sid=session_browser.resolve(server,key)
    if kind!='paired':raise ValueError('양방향 대화 세션을 선택하세요.')
    session=relocate_session(session_browser.read(storage/'paired'/sid/'session.json'))
    version=session['client_version']
    if version not in VERSIONS:raise ValueError('현재 분석 대상은 Q3 계열입니다.')
    module=client_runtime(version);records=[];sources=[]
    # One final committed memory snapshot includes all earlier completed records.
    # It avoids double-counting the copies held under each previous attempt.
    if session.get('client_storage_dir'):
        folder=module.directory({'_storage_dir':session['client_storage_dir']},sid)/'appraisals'
        if folder.is_symlink():raise ValueError('기록 폴더를 확인하세요.')
        for path in sorted(folder.glob('*.json')):
            try:records.append((session_browser.read(path),path))
            except (OSError,ValueError):continue
        sources.append(str(folder))
    return build(session,records,module.note_items.read_items,start,end,key,sources)


def handle_get(handler):
    parts=urlsplit(handler.path)
    if parts.path not in ('/api/routing/catalog','/api/routing'):return False
    try:
        params=parse_qs(parts.query)
        if parts.path.endswith('/catalog'):result=catalog(handler.server)
        else:
            start=int(params.get('from',['1'])[0]);end=int(params['to'][0]) if params.get('to') else None
            if start<1 or (end is not None and end<start):raise ValueError('내담자 턴 범위를 확인하세요.')
            result=analyze(handler.server,params.get('key',[''])[0],start,end)
        handler.json_response(result)
    except (OSError,ValueError,KeyError,TypeError) as error:handler.json_response({'error':str(error)},400)
    return True
