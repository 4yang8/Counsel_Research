"""Stable Markdown item IDs and atomic, validated note patches for this version."""
import copy
import hashlib
import re
import uuid
from .states import EMPTY

# Internal container only; never a model-selectable category.
SECTIONS = {'notes': ('내담자 노트', '')}
ITEM = re.compile(r'^- \[([^\]]+)\] (.+)$')
ID = re.compile(r'n[a-f0-9]{12}')

def read_items(text):
    """Flatten previous F headings without dropping content or rewriting files."""
    rows, seen = [], set()
    pending=[]
    def flush():
        if pending:
            content='\n'.join(pending).strip();pending.clear()
            identity='n'+hashlib.sha256(content.encode()).hexdigest()[:12]
            while identity in seen:
                identity='n'+hashlib.sha256((identity+content).encode()).hexdigest()[:12]
            seen.add(identity);rows.append({'id':identity,'content':content})
    for line in text.splitlines():
        if not line.strip() or line.strip()==EMPTY or re.match(r'^#{1,6} ',line):
            flush();continue
        match=ITEM.fullmatch(line)
        if match:
            flush();identity,content=match.groups()
            if not ID.fullmatch(identity) or identity in seen:
                raise ValueError('노트 항목 ID가 올바르지 않거나 중복되었습니다: '+identity)
            seen.add(identity);rows.append({'id':identity,'content':content})
        elif line.startswith('  ') and rows and not pending:
            rows[-1]['content']+='\n'+line[2:]
        else:pending.append(line)
    flush()
    return {'notes':rows}

def render_items(items):
    body='\n'.join('- ['+r['id']+'] '+r['content'].replace('\n','\n  ') for r in items['notes']) or EMPTY
    return '# 내담자 노트\n\n'+body+'\n'

def normalize(text):
    return render_items(read_items(text))


def schema():
    fields = {
        'action': {'type':'string','enum':['add','update','delete'], 'description':'add: 새 내용 추가, update: 기존 항목 내용 수정, delete: 더 이상 유효하지 않은 항목 삭제. 유지할 항목은 보내지 않습니다.'},
        'item_id': {'type':'string','description':'update/delete는 입력 노트의 [n...] ID. add는 빈 문자열이며 Python이 새 ID를 부여합니다.'},
        'content': {'type':'string','description':'add/update는 다음 반응에 이어 줄 현재 마음이나 조건 하나를 간결하게 기록합니다. 기본 정보·프로필에 더해 이번 내면 판단과 표현 선택에서 확인되는 내용을 담고, 그 판단의 대상·구체성·확실성을 유지합니다. 배경은 현재 마음을 이해하는 데 필요한 부분만 함께 적습니다. 항목의 새 본문만 한 줄로 쓰고 ID·글머리는 제외합니다. delete는 빈 문자열입니다.'},
        'reason': {'type':'string','description':'이전 항목과 이번 appraisal·turn_goal·response_plan·확정 발화를 비교하여, 어느 자료의 어떤 내용이 추가·수정·삭제를 뒷받침하는지 짧게 설명합니다. 처음 기록하는 경우 새 마음을 파악한 근거를 적습니다. 실행 이력에 저장되며 다음 턴의 현재 노트에는 자동 추가되지 않습니다.'},
    }
    return {'type':'object','properties':{'operations':{'type':'array',
        'description':'필요한 글머리 항목 변경만 전달합니다. 유지할 항목은 생략하고, 변경이 없으면 빈 배열입니다.',
        'items':{'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}}},
        'required':['operations'],'additionalProperties':False}


def apply(items, arguments):
    """Validate the entire batch before returning; never mutate input on failure."""
    if not isinstance(arguments, dict) or set(arguments) != {'operations'} or not isinstance(arguments['operations'], list):
        raise ValueError('operations 배열에 add/update/delete를 전달하세요. 변경이 없으면 빈 배열입니다.')
    result, changes, touched = copy.deepcopy(items), [], set()
    existing = {row['id']:(section,row) for section,rows in items.items() for row in rows}
    ids = set(existing)
    fields = {'action','item_id','content','reason'}
    for raw in arguments['operations']:
        if not isinstance(raw,dict) or set(raw)!=fields or any(not isinstance(raw[k],str) for k in fields):
            raise ValueError('각 변경에는 action/item_id/content/reason 문자열이 필요합니다.')
        op = {k:v.strip() for k,v in raw.items()}
        section, action, item_id = 'notes', op['action'], op['item_id']
        if action not in ('add','update','delete') or not op['reason']:
            raise ValueError('올바른 작업과 변경 근거를 지정하세요.')
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
                raise ValueError('기존 노트 항목 ID를 지정하세요: '+item_id)
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
            raise ValueError('노트에 동일한 본문을 중복 기록할 수 없습니다: '+section)
    return result, changes

