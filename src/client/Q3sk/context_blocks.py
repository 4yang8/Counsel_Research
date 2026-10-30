"""Assemble request views and record exact source parts outside the model payload."""
import copy
import re
import runtime.user_templates as user_templates
from .states import STATES

VERSION='Q3sk'
SYSTEM_SECTIONS={
    '역할과 제공 정보':('instructions.role','역할과 정보 해석 기준'),
    '내면 판단과 공개 발화':('instructions.speech','내면 판단·공개 발화 지침'),
    '내담자 노트 기록':('instructions.notes','내담자 노트 기록 기준'),
    '도구 사용 순서':('instructions.sequence','단계 진행 지침'),
}
LABELS={'basic_info':'기본 정보','dialogue':'공개 대화','counselor_utterance':'이번 상담사 발화',
        'selected_traits':'선정 특성','traits':'전체 특성 후보','state_definitions':'상태 항목의 의미','state_notes':'내담자 노트'}


def part(identity,label,value):
    return {'id':identity,'label':label,'value':value}


def split_spans(text, matches, prefix, label):
    """Split at controlled source boundaries, keeping every character verbatim."""
    result=[];cursor=0
    for i,(start,identity,title) in enumerate(matches):
        if start>cursor:result.append(part(prefix+'.preamble',label,text[cursor:start]))
        end=matches[i+1][0] if i+1<len(matches) else len(text)
        result.append(part(identity,title,text[start:end]));cursor=end
    if cursor<len(text):result.append(part(prefix+'.body',label,text[cursor:]))
    return result or [part(prefix+'.empty',label,text)]


def system_parts(text):
    matches=[];counts={}
    for match in re.finditer(r'^## ([^\n]+)\n?',text,re.M):
        title=match.group(1);identity,label=SYSTEM_SECTIONS.get(title,('instructions.custom.'+str(len(matches)),title))
        count=counts.get(identity,0);counts[identity]=count+1
        matches.append((match.start(),identity+(f'.{count}' if count else ''),label))
    return split_spans(text,matches,'instructions','시스템 제목·추가 지침')


def value_parts(key,text):
    if key=='state_definitions':
        matches=[]
        for match in re.finditer(r'^- ([a-z_]+):\n',text,re.M):
            state=match.group(1)
            if state in STATES:matches.append((match.start(),'state_definitions.'+state,STATES[state][0]+' · 의미'))
        return split_spans(text,matches,key,LABELS[key])
    if key=='state_notes':
        matches=[]
        for match in re.finditer(r'^## (현재 내면|심리상태)\n',text,re.M):
            matches.append((match.start(),'note.inner' if match.group(1)=='현재 내면' else 'note.states',match.group(1)+' · 원문'))
        return split_spans(text,matches,'note','노트 제목·원문')
    return [part('user.'+key,LABELS.get(key,key),text)]


def render_user(kind, **values):
    """Same single-pass template substitution, with semantic source boundaries."""
    spec=user_templates.descriptor('client',VERSION,kind)
    path=user_templates.HERE/spec['path']
    if path.is_symlink():raise ValueError('템플릿 파일은 심볼릭 링크일 수 없습니다.')
    template=path.read_text(encoding='utf-8')
    names=user_templates.validate(template,spec['variables'])
    if names-values.keys():raise ValueError('템플릿 입력값이 없습니다.')
    parts=[];cursor=0;counts={}
    for match in user_templates.TOKEN.finditer(template):
        key=match.group(1);count=counts.get(key,0);counts[key]=count+1
        chunks=value_parts(key,str(values[key]))
        prefix=template[cursor:match.start()]
        if key=='state_definitions':
            parts.append(part('user.heading.state_definitions'+('.'+str(count) if count else ''),'상태 항목의 의미 · 제목',prefix))
        else:chunks[0]['value']=prefix+chunks[0]['value']
        for item in chunks:
            item['variable']=key
            if count:item['id']+='.'+str(count)
        parts.extend(chunks);cursor=match.end()
    suffix=template[cursor:]
    if parts:parts[-1]['value']+=suffix
    else:parts=[part('user.template','사용자 템플릿',suffix)]
    return ''.join(item['value'] for item in parts),parts


def compose(payload, user_parts=None, selected_states=(), prune=False):
    """Mutate only a disposable request copy. The caller retains the full source."""
    groups=[];selected=set(selected_states)
    for index,message in enumerate(payload['messages']):
        role=message['role'];items=[]
        if role=='system' and isinstance(message.get('content'),str):
            items=system_parts(message['content'])
        elif role=='user' and isinstance(message.get('content'),str) and user_parts and ''.join(p['value'] for p in user_parts)==message['content']:
            items=copy.deepcopy(user_parts)
        elif isinstance(message.get('content'),str):
            items=[part(f'history.{index}.content','도구 결과' if role=='tool' else '본문',message['content'])]
        source_offset=request_offset=0
        for item in items:
            item['field']='content';item['included']=True;item['reason']='실제 입력'
            if prune and role=='system' and item['id'] in ('instructions.speech','instructions.sequence'):
                item.update(included=False,reason='노트 갱신 단계에서 완료된 판단·발화·진행 지침 제외')
            if prune and item.get('variable')=='state_definitions' and item['id'].startswith('state_definitions.'):
                state=item['id'].split('.')[1]
                if state in STATES and state not in selected:item.update(included=False,reason='이번에 선택하지 않은 상태의 상세 정의')
            item['source_range']=[source_offset,source_offset+len(item['value'])];source_offset+=len(item['value'])
            if item['included']:
                item['request_range']=[request_offset,request_offset+len(item['value'])];request_offset+=len(item['value'])
        if items:message['content']=''.join(p['value'] for p in items if p['included'])
        for field in ('reasoning','reasoning_content'):
            if field in message:items.append({**part(f'history.{index}.{field}','Reasoning · 같은 턴 기록',message[field]),'field':field,'included':True,'reason':'같은 턴 추론 보존'})
        for i,call in enumerate(message.get('tool_calls',[])):
            items.append({**part(f'history.{index}.call.{i}',call.get('function',{}).get('name','도구 호출'),copy.deepcopy(call)),
                          'field':'tool_calls','item_index':i,'included':True,'reason':'도구 호출·결과 연결 보존'})
        groups.append({'id':f'message:{index}','role':role,'message_index':index,
                       'label':{'system':'System prompt','user':'User prompt','assistant':'Assistant 기록','tool':'Tool 결과'}.get(role,role),'parts':items})
    definitions=[];source_tools=copy.deepcopy(payload.get('tools',[]));kept=[]
    for i,tool in enumerate(source_tools):
        name=tool.get('function',{}).get('name',str(i));included=not prune or name=='update_client_states'
        entry={**part('tool_definition.'+name,name,tool),'field':'tools','included':included,
               'source_index':i,'reason':'현재 단계의 사용 도구' if included else '노트 갱신 단계에서 사용하지 않는 도구 정의'}
        if included:entry['request_index']=len(kept);kept.append(tool)
        definitions.append(entry)
    if source_tools:
        payload['tools']=kept
        groups.append({'id':'tools','role':'tools','label':'사용 가능한 도구','parts':definitions})
    return {'schema_version':1,'stage':'note_update' if prune else 'full_context','groups':groups,
            'policy':'마지막 노트 갱신에서만 지침·도구 정의·선택하지 않은 상태 설명 축소. 근거 정보와 같은 턴 추론 유지.',
            'included_parts':sum(p['included'] for g in groups for p in g['parts']),
            'excluded_parts':sum(not p['included'] for g in groups for p in g['parts'])}
