"""Read-only memory experiment trace and portable common-history snapshots."""
from urllib.parse import urlsplit, parse_qs
from client.memory_access import VERSIONS
from client.memory_access.runtime import load, snapshot


def handle_get(handler):
    parts=urlsplit(handler.path)
    if parts.path!='/api/memory_access/state':return False
    try:
        args=parse_qs(parts.query)
        sid=args.get('session_id',[''])[0]
        version=args.get('version',[''])[0]
        storage=handler.server.storage_dir
        if args.get('paired')==['1']:
            from runtime.paired_lab import read_session
            session=read_session(handler.server,sid)
            version=session['client_version'];storage=session.get('client_storage_dir')
        elif args.get('key'):
            from storage.session_browser import resolve
            storage,kind,found=resolve(handler.server,args['key'][0])
            if kind!=version or found!=sid:raise ValueError('세션이 일치하지 않습니다.')
        if version not in VERSIONS:raise ValueError('기억 접근 실험 세션을 선택하세요.')
        cfg={'_storage_dir':str(storage)}
        value=load(cfg,version,sid if storage else None)
        portable=snapshot(cfg,version,sid) if storage else {'public_dialogue':[],'records':[]}
        handler.json_response({'version':version,'state':value,'snapshot':portable})
    except (ValueError,OSError,KeyError,TypeError) as error:
        handler.json_response({'error':str(error)},400)
    return True
