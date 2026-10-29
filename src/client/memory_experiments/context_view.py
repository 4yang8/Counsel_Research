"""Exact request decomposition; omitted note originals are UI metadata only."""
import copy
import re
from client.Q2a_variant_context.context_blocks import compose
from client.Q2a_trait_background.note_items import SECTIONS


def manifest(payload,note=None,stage='router'):
    view=compose(copy.deepcopy(payload))
    view['stage']=stage
    view['policy']='발화 전에는 선택된 노트 원문, 발화 확정 후에는 전체 최신 노트. 회색 원문은 표시용 메타데이터이며 모델에 전송하지 않습니다.'
    for group in view['groups']:
        if group['role']!='user':continue
        text=payload['messages'][group['message_index']].get('content','')
        if not isinstance(text,str):continue
        starts=[(m.start(),m.group(0)) for m in re.finditer(r'^#{1,3} [^\n]+|^- \[(n[a-f0-9]+)\] [^\n]+',text,re.M)]
        if not starts:continue
        if starts[0][0]:starts.insert(0,(0,'User prompt'))
        parts=[];seen=set()
        for i,(start,title) in enumerate(starts):
            end=starts[i+1][0] if i+1<len(starts) else len(text)
            match=re.match(r'- \[(n[a-f0-9]+)\]',title)
            identity='note.item.'+match[1] if match else 'user.section.'+str(i)
            if match:seen.add(match[1])
            parts.append({'id':identity,'label':title[:100],'value':text[start:end],'field':'content',
                          'included':True,'reason':'실제 요청에 포함된 원문','request_range':[start,end],'source_range':[start,end]})
        if note:
            for section,items in note['items'].items():
                for item in items:
                    if item['id'] in seen:continue
                    parts.append({'id':'note.item.'+item['id'],'label':SECTIONS[section][0]+' · '+item['id'],
                        'value':'- ['+item['id']+'] '+item['content']+'\n','field':'content',
                        'included':False,'reason':'현재 노트에 저장되어 있으나 이번 요청에는 포함되지 않은 항목'})
        group['parts']=parts
    view['included_parts']=sum(p['included'] for g in view['groups'] for p in g['parts'])
    view['excluded_parts']=sum(not p['included'] for g in view['groups'] for p in g['parts'])
    return view
