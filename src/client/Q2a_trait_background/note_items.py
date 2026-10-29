"""Stable Markdown item IDs and atomic, validated note patches for this version."""
import copy
import hashlib
import re
import uuid
from .states import STATES, INNER, EMPTY, parse_note, render

SECTIONS = {**STATES, **INNER}
ITEM = re.compile(r'^- \[(n[a-f0-9]{12})\] (.+)$')


def read_items(text):
    """Read old paragraphs without guessing their meaning or editing the source file."""
    parsed = parse_note(text)
    result, seen = {}, set()
    for section, body in {**parsed['states'], **parsed['inner']}.items():
        rows = []
        if body != EMPTY:
            lines = body.splitlines()
            if any(ITEM.fullmatch(line) for line in lines):
                for line in lines:
                    match = ITEM.fullmatch(line)
                    if match:
                        item_id, content = match.groups()
                        if item_id in seen:
                            raise ValueError('중복된 노트 항목 ID입니다: ' + item_id)
                        seen.add(item_id)
                        rows.append({'id': item_id, 'content': content})
                    elif line.startswith('  ') and rows:
                        rows[-1]['content'] += '\n' + line[2:]
                    else:
                        raise ValueError('노트 글머리 항목 형식을 확인하세요: ' + section)
            else:
                # One legacy paragraph remains one item; no LLM or semantic rewrite.
                item_id = 'n' + hashlib.sha256((section+'\0'+body).encode()).hexdigest()[:12]
                if item_id in seen:
                    raise ValueError('중복된 노트 항목 ID입니다: ' + item_id)
                seen.add(item_id)
                rows = [{'id': item_id, 'content': body}]
        result[section] = rows
    return result


def bodies(items):
    def body(rows):
        return '\n'.join('- ['+row['id']+'] '+row['content'].replace('\n','\n  ') for row in rows) or EMPTY
    return {section: body(items[section]) for section in SECTIONS}


def render_items(items):
    text = bodies(items)
    return render({k:text[k] for k in STATES}, {k:text[k] for k in INNER})


def normalize(text):
    return render_items(read_items(text))


def schema():
    fields = {
        'section': {'type':'string','enum':list(SECTIONS), 'description':'수정할 노트 영역. '+ ' / '.join(k+': '+v[0] for k,v in SECTIONS.items())},
        'action': {'type':'string','enum':['add','update','delete'], 'description':'add: 새 내용 추가, update: 기존 항목 내용 수정, delete: 더 이상 유효하지 않은 항목 삭제. 유지할 항목은 보내지 않습니다.'},
        'item_id': {'type':'string','description':'update/delete는 입력 노트의 [n...] ID. add는 빈 문자열이며 Python이 새 ID를 부여합니다.'},
        'content': {'type':'string','description':'add/update는 글머리 한 항목의 새 본문만 작성합니다. 하나의 마음이나 조건을 간결하게 적고 ID·글머리·줄바꿈은 제외합니다. delete는 빈 문자열입니다.'},
        'reason': {'type':'string','description':'이 항목을 추가·수정·삭제할 이유를 이전 기록과 이번 내면 경험에 연결해 짧게 설명합니다. 실행 이력에 저장되며 다음 턴의 현재 노트에는 자동 추가되지 않습니다.'},
    }
    return {'type':'object','properties':{'operations':{'type':'array',
        'description':'필요한 항목 변경만 전달합니다. 심리상태는 select_client_states로 선택한 영역만, 현재 내면의 세 영역은 별도로 수정할 수 있습니다. 변경이 없으면 빈 배열입니다.',
        'items':{'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}}},
        'required':['operations'],'additionalProperties':False}


def apply(items, arguments, selected_states):
    """Validate the entire batch before returning; never mutate input on failure."""
    if not isinstance(arguments, dict) or set(arguments) != {'operations'} or not isinstance(arguments['operations'], list):
        raise ValueError('operations 배열에 add/update/delete를 전달하세요. 변경이 없으면 빈 배열입니다.')
    allowed = set(selected_states) | set(INNER)
    result, changes, touched = copy.deepcopy(items), [], set()
    existing = {row['id']:(section,row) for section,rows in items.items() for row in rows}
    ids = set(existing)
    fields = {'section','action','item_id','content','reason'}
    for raw in arguments['operations']:
        if not isinstance(raw,dict) or set(raw)!=fields or any(not isinstance(raw[k],str) for k in fields):
            raise ValueError('각 변경에는 section/action/item_id/content/reason 문자열이 필요합니다.')
        op = {k:v.strip() for k,v in raw.items()}
        section, action, item_id = op['section'], op['action'], op['item_id']
        if section not in allowed or section not in SECTIONS or action not in ('add','update','delete') or not op['reason']:
            raise ValueError('선택한 심리상태 또는 현재 내면 영역과 올바른 작업·근거를 지정하세요.')
        if action == 'add':
            if item_id:
                raise ValueError('add의 item_id는 빈 문자열이어야 합니다.')
            item_id = 'n'+uuid.uuid4().hex[:12]
            while item_id in ids:
                item_id = 'n'+uuid.uuid4().hex[:12]
            ids.add(item_id)
            before = ''
        else:
            if item_id not in existing or existing[item_id][0]!=section:
                raise ValueError('해당 영역의 기존 노트 항목 ID를 지정하세요: '+item_id)
            if item_id in touched:
                raise ValueError('한 요청에서 같은 항목을 여러 번 변경할 수 없습니다: '+item_id)
            touched.add(item_id)
            before = existing[item_id][1]['content']
        if action=='delete':
            if op['content']:
                raise ValueError('delete의 content는 빈 문자열이어야 합니다.')
        elif (not op['content'] or op['content']==EMPTY or '\n' in op['content'] or '\r' in op['content']
              or re.match(r'^(?:#{1,6}\s|[-*+]\s|\[n[a-f0-9]+\])',op['content'])):
            raise ValueError('글머리 한 항목의 본문만 한 줄로 작성하세요. 내용 제거에는 delete를 사용하세요.')
        if action=='update' and before==op['content']:
            continue
        if action=='add':
            result[section].append({'id':item_id,'content':op['content']})
        elif action=='delete':
            result[section] = [row for row in result[section] if row['id']!=item_id]
        else:
            next(row for row in result[section] if row['id']==item_id)['content'] = op['content']
        changes.append({**op,'item_id':item_id,'label':SECTIONS[section][0],'before':before})
    for section,rows in result.items():
        contents = [re.sub(r'\s+',' ',row['content']).strip() for row in rows]
        if len(set(contents)) != len(contents):
            raise ValueError('같은 영역에 동일한 본문을 중복 기록할 수 없습니다: '+section)
    return result, changes


def summaries(items, changes, definitions, key):
    """Keep existing UI/history contracts; the model emits only item operations."""
    text = bodies(items)
    sections = dict.fromkeys(row['section'] for row in changes if row['section'] in definitions)
    return [{key:section,'content':text[section],
             'reason':' / '.join(row['reason'] for row in changes if row['section']==section)} for section in sections]
