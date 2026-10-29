"""Read-only Q2 notes: live file plus committed per-turn snapshots."""
from urllib.parse import urlsplit, parse_qs
from pathlib import Path
from client.registry import client_runtime


def handle_get(handler):
    parts=urlsplit(handler.path)
    if parts.path=='/api/session_notes':
        try:handler.json_response(session_notes(handler,parse_qs(parts.query)))
        except (ValueError,OSError,KeyError,TypeError) as error:handler.json_response({'error':str(error)},400)
        return True
    if parts.path!='/api/client_notes':return False
    args=parse_qs(parts.query);sid=args.get('session_id',[''])[0]
    try:
        storage=handler.server.storage_dir
        version=args.get('version',['Q2'])[0]
        if args.get('paired')==['1'] and sid:
            import runtime.paired_lab as paired_lab
            session=paired_lab.read_session(handler.server,sid)
            version=session['client_version']
            storage=session.get('client_storage_dir')
        elif args.get('key'):
            import storage.session_browser as session_browser
            storage,kind,found=session_browser.resolve(handler.server,args['key'][0])
            if found!=sid or kind!=version or kind not in ('Q2','Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'):raise ValueError('선택한 심리상태 세션이 일치하지 않습니다.')
        if version not in ('Q2','Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'):
            handler.json_response({'version':version,'available':False,'message':'이 노트 화면은 자연어 심리상태를 사용하는 Q2·Q2a용입니다.'});return True
        # No successful generated client turn means no committed private state yet.
        q2=client_runtime(version)
        with q2.LOCK:
            note=q2.load({'_storage_dir':str(storage)},sid if storage and sid else None)
        handler.json_response({'version':version,'available':True,'state_labels':{key:value[0] for key,value in q2.STATES.items()},'inner_labels':{key:value[0] for key,value in getattr(q2,'INNER',{}).items()},**note})
    except (ValueError,OSError,KeyError,TypeError) as error:
        handler.json_response({'error':str(error)},400)
    return True


def session_notes(handler, args):
    """Read current persisted notes; never initialize files or expose pending drafts."""
    sid=args.get('session_id',[''])[0]
    if not sid:
        return {'notes':[], 'message':'대화를 선택하거나 새 대화를 시작하면 현재 노트가 표시됩니다.'}
    storage=handler.server.storage_dir
    version=args.get('version',['basic'])[0]
    notes=[]
    if args.get('paired')==['1']:
        import runtime.paired_lab as paired_lab
        from counselor import catalog
        session=paired_lab.read_session(handler.server,sid)
        version=session['client_version'];storage=session.get('client_storage_dir')
        counselor=session.get('counselor_version','basic')
        data=catalog.view_state(counselor,paired_lab.root(handler.server,sid))
        for key,label in [('notes','상담 노트'),('client_notes','내담자 정보 노트'),('counselor_notes','상담사 생각 노트')]:
            current=data.get(key,{}).get('current')
            if current is not None:
                notes.append({'actor':'counselor','version':counselor,'label':label,
                              'content':current.get('content',''),'path':current.get('path')})
    elif args.get('key'):
        import storage.session_browser as session_browser
        storage,kind,found=session_browser.resolve(handler.server,args['key'][0])
        if found!=sid or kind!=version:raise ValueError('선택한 세션 정보가 일치하지 않습니다.')
    if version in ('Q1','Q1a','Q1_s1','Q1_s2','Q2','Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'):
        module=client_runtime(version)
        with module.LOCK:
            note=module.load({'_storage_dir':str(storage)},sid if storage else None)
        if version in ('Q3','Q3a','Q3b'):
            notes.extend([
                {'actor':'client','version':version,'label':'사전 궤적 · 다음 생성에 사용할 파일','content':note['trajectory'],'path':str(module.HERE/'trajectory.md')},
                {'actor':'client','version':version,'label':'현재 궤적 진행 · 마지막 완료 턴','content':note['trajectory_progress_text'],'path':None},
            ])
        if version in ('Q3a','Q3b'):
            notes.append({'actor':'client','version':version,'label':'로컬 작업 계획 · 마지막 완료 턴 / 다음 턴에는 새로 작성','content':note['local_plan_text'],'path':None})
        notes.insert(0,{'actor':'client','version':version,
                        'label':'내담자 노트 · 자유 글머리' if version in ('Q2a_agentic_memory_F','Q3','Q3a','Q3b') else '내담자 노트 · 현재 내면 / 심리상태' if version in ('Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b') else '심리상태 노트' if version=='Q2' else '비공개 내면 노트',
                        'content':note['content'],'path':note.get('path')})
    return {'session_id':sid,'notes':notes,'message':'현재 저장된 노트 원문 · 내담자 변경안은 턴 저장 완료 후 반영'}
