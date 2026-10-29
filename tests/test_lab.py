import copy
from contextlib import ExitStack
import json
import re
import shutil
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from project_paths import PROJECT_ROOT
ROOT = PROJECT_ROOT
sys.path.insert(0, str(ROOT / 'src'))
import app.app_server as app


class Model(BaseHTTPRequestHandler):
    requests = []
    c4_fail = False
    c4_gate = None
    basic_label = '내담자 발화'
    basic_counselor_finish = 'stop'
    state_json_fence = False
    p1_gate = None
    p1_delay = 0
    p1_repeat = False
    p2_actions = None
    p3_actions = None
    p3a_actions = None
    p3a_child_response = '## CBT-07 · 생각의 근거와 대안\n전달된 상황에서 사실과 해석을 함께 구분하세요.'
    p3a_child_finish = 'stop'
    dual_notes_finish = 'stop'
    q1_router_response = None
    q1_router_finish = 'stop'
    q1_main_finish = 'stop'
    q1_update = None
    q1_skip_update = False
    q2_tool_finish = "stop"
    q2_routes = None
    q2_updates = None
    q2_post_finish = "stop"
    q1a_actions = None
    q1a_review_finish = 'stop'
    q1a_review_response = None
    q1a_main_finish = 'tool_calls'
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path in ('/tokenize','/detokenize'):
            if self.path=='/detokenize':
                result={'prompt':''.join(chr(i) for i in data['tokens'])}
            else:
                prompt=data.get('prompt')
                if prompt is None:
                    prompt=''.join('<|im_start|>'+m['role']+'\n'+m.get('content','')+'<|im_end|>\n' for m in data['messages'])+'<|im_start|>assistant\n<think>\n'
                result={'tokens':[ord(c) for c in prompt],'count':len(prompt),'max_model_len':32768}
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps(result).encode());return
        self.requests.append(data)
        if self.path=='/v1/completions':
            props=data['structured_outputs']['json']['properties']['selections']['items']['properties']
            traits=props['trait_id'].get('enum',[]);backgrounds=props['background_ids']['items'].get('enum',[])
            selected={'selections':[{'trait_id':traits[0],'background_ids':backgrounds[:1],'relevance':'현재 이야기와 개인 배경의 연결'}] if traits else []}
            value=self.q1_router_response if self.q1_router_response is not None else json.dumps(selected,ensure_ascii=False)
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            for text in ['PREFILL_ROUTER_REASON','</thi','nk>',value]:
                chunk={'choices':[{'index':0,'text':text,'finish_reason':None}]}
                self.wfile.write(('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n').encode());self.wfile.flush()
            chunk={'choices':[{'index':0,'text':'','finish_reason':self.q1_router_finish}],
                   'usage':{'prompt_tokens':130,'completion_tokens':30,'total_tokens':160}}
            self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode());return
        messages = data['messages']
        is_q1a_main = any(tool.get('function', {}).get('name')=='finalize_client_draft' for tool in data.get('tools', []))
        is_q1a_reviewer = not data.get('tools') and '# 검토할 내담자 초안\n' in messages[-1]['content']
        if is_q1a_main or is_q1a_reviewer:
            if is_q1a_reviewer:
                value = self.q1a_review_response if self.q1a_review_response is not None else {
                    'assessment':'revision_suggested', 'findings':[{'aspect':'내면 연속성','issue':'초안이 안도만 표현함',
                    'evidence':'기존 내면과 appraisal에는 망설임이 함께 남아 있음','suggestion':'망설임이 남는 표현을 검토하세요.'}], 'uncertainty':''}
                delta={'content':value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)}
                finish=self.q1a_review_finish
            else:
                appraisal={'interpretation':'내 속도를 존중해 준다고 느낀다.','immediate_feeling':'안도와 망설임이 함께 남는다.',
                           'relevant_evidence':'상담사의 공개 발화와 이전 내면 노트.','uncertainty':''}
                first={'utterance':'조금 안심돼요.','appraisal':appraisal,'inner_notes':'PRIVATE_Q1a_DRAFT_NOTE','revises':None}
                second={**first,'utterance':'조금 안심되지만, 아직은 조심스러워요.','inner_notes':'PRIVATE_Q1a_FINAL_NOTE','revises':'draft_1'}
                actions=self.q1a_actions if self.q1a_actions is not None else [
                    {'name':'stage_client_update','arguments':first},
                    {'name':'review_client_draft','arguments':{'draft_id':'draft_1','question':'남아 있는 망설임과 발화가 맞는지 확인해 주세요.'}},
                    {'name':'stage_client_update','arguments':second},
                    {'name':'finalize_client_draft','arguments':{'draft_id':'draft_2','reason':'검토 의견에 따라 망설임을 반영했고 내면과 발화가 맞습니다.'}}]
                count=sum(message['role']=='tool' for message in messages)
                if count<len(actions):
                    action=actions[count]
                    delta={'tool_calls':[{'index':0,'id':'q1a_call_'+str(count),'type':'function','function':{
                        'name':action['name'],'arguments':json.dumps(action['arguments'],ensure_ascii=False)}}]}
                    finish=self.q1a_main_finish
                else:
                    delta={'content':'Q1a_UNFINALIZED_TEXT'};finish='stop'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            reasoning='Q1a_REVIEW_REASON' if is_q1a_reviewer else 'Q1a_MAIN_REASON'
            self.wfile.write(('data: '+json.dumps({'choices':[{'index':0,'delta':{'reasoning_content':reasoning}}]})+'\n\n').encode());self.wfile.flush()
            if self.p1_delay:time.sleep(self.p1_delay)
            chunk={'choices':[{'index':0,'delta':delta,'finish_reason':finish}], 'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}}
            self.wfile.write(('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode());return
        if any(t.get('function',{}).get('name')in ('commit_client_utterance','update_client_states','update_client_notes','retrieve_client_memory','update_trajectory_progress','update_turn_plan') for t in data.get('tools',[])) or (data.get('tools') and messages[0]['content'].startswith('# 내담자 반응과 심리상태 기록 지침')):
            completed=[m for m in messages if m['role']=='tool']
            actions=getattr(self,'q2a_actions',None)
            if actions is None:
                actions=[
                    {'name':'stage_client_update','arguments':{'appraisal':{'interpretation':'선택을 존중받았다','immediate_feeling':'안도와 망설임','relevant_evidence':'이번 상담사 발화','uncertainty':''},'response_plan':{'action':'깊은 이야기는 보류한다.','reason':'걱정은 알고 있지만 아직 공개하지 않는다.'}}},
                    {'name':'commit_client_utterance','arguments':{'utterance':'조금 안심되지만 자세한 이야기는 아직 부담스러워요.'}},
                    {'name':'select_client_states','arguments':{'selections':[{'state':'bond','reason':'선택을 존중받았다는 내면 판단'}]}},
                    {'name':'update_client_states','arguments':{'updates':[{'state':'bond','content':'선택을 존중받았다고 느끼지만 자세한 이야기는 아직 조심스럽다.','reason':'안도와 망설임이 함께 있다.'}],'inner_updates':[{'section':'unspoken_and_wishes','content':'조금 안심되었지만 지금은 자세한 이야기를 보류하고 싶다.','reason':'말할 범위를 스스로 정하고 싶다.'}]}}
                ]
            item_tool=next((t['function'] for t in data.get('tools',[]) if t['function']['name']=='update_client_states'),{})
            if getattr(self,'q2a_actions',None) is None and 'operations' in item_tool.get('parameters',{}).get('properties',{}):
                operations=[]
                for section,content in [('bond','선택을 존중받았다고 느끼지만 자세한 이야기는 아직 조심스럽다.'),('unspoken_and_wishes','조금 안심되었지만 지금은 자세한 이야기를 보류하고 싶다.')]:
                    # Mock uses the actual IDs supplied to this model request on later turns.
                    label='상담사와의 관계적 유대' if section=='bond' else '말하지 않은 속마음과 현재 바라는 것'
                    match=re.search(r'### '+label+r'\n- \[(n[a-f0-9]{12})\]',messages[1]['content'])
                    operations.append({'section':section,'action':'update' if match else 'add','item_id':match[1] if match else '',
                                       'content':content,'reason':'안도와 망설임이 함께 있다.'})
                actions[-1]={'name':'update_client_states','arguments':{'operations':operations}}
            if getattr(self,'q2a_actions',None) is None:
                stage=next((t['function']['parameters']['properties'] for t in data.get('tools',[]) if t['function']['name']=='stage_client_update'),{})
                commit=next((t['function']['parameters']['properties'] for t in data.get('tools',[]) if t['function']['name']=='commit_client_utterance'),{})
                if 'turn_goal' in stage:actions[0]['arguments']['turn_goal']={'intention':'말할 범위를 지키고 싶다.','grounds':'조심스러운 마음','uncertainty':''}
                if 'evidence_decision' in commit:actions[1]['arguments']['evidence_decision']={'status':'sufficient','reason':'현재 정보로 반응을 정할 수 있다.','remaining_gap':''}
            if getattr(self,'q2a_actions',None) is None and messages[0]['content'].startswith('# 내담자 반응과 노트 기록 지침'):
                rows=re.findall(r'^- \[(n[a-f0-9]{12})\] (.+)$',messages[1]['content'],re.M)
                operations=[]
                for i,content in enumerate(['선택을 존중받아 조금 안심된다.','아직 자세한 이야기를 할 준비는 되지 않았다.']):
                    operations.append({'action':'update' if i<len(rows) else 'add','item_id':rows[i][0] if i<len(rows) else '', 'content':content,'reason':'이번 내면 경험을 반영한다.'})
                actions=actions[:2]+[{'name':'update_client_notes','arguments':{'operations':operations}}]
            if getattr(self,'q2a_actions',None) is None and '## 궤적을 참고한 이번 반응 계획' in messages[0]['content']:
                actions[0]['arguments']['trajectory_plan']={'position':'초반','movement':'maintain','grounds':'신뢰 형성의 방향과 조심스러운 현재 마음을 연결한다.'}
                actions.append({'name':'update_trajectory_progress','arguments':{'position':'초반','movement':'maintain','current_experience':'시간을 주어 조금 안심하나 자세한 이야기는 조심스럽다.','grounds':'이번 해석과 표현 범위가 초기 경계를 유지한다.','next_condition':'속도를 존중하는 반응이 이어지는지'}})
            if getattr(self,'q2a_actions',None) is None and '## 판단 경로 선택' in messages[0]['content']:
                actions[1]['arguments']['evidence_decision']={'status':'sufficient','reason':'현재 정보로 반응을 정할 수 있다.','remaining_gap':''}
            if getattr(self,'q2a_actions',None) is None and '## 이번 턴의 로컬 계획과 수행' in messages[0]['content']:
                actions[0]['arguments']['turn_goal']={'intention':'말할 범위를 지키고 싶다.','grounds':'조심스러운 마음','uncertainty':''}
                actions[1]['arguments']['evidence_decision']={'status':'sufficient','reason':'현재 정보로 반응을 정할 수 있다.','remaining_gap':''}
                prefix=[{'name':'update_turn_plan','arguments':{'goal':'경계와 안도를 함께 반영하는 발화 만들기','reason':'이번 발화의 마음과 공개 범위를 정하기 위해','operations':[{'action':'add','step_id':'s1','task':'현재 마음과 공개 범위를 비교한다','completion_criterion':'안도와 경계가 함께 가능한 근거를 설명한다'}]}},
                        {'name':'update_turn_plan','arguments':{'goal':'','reason':'제공된 자료를 비교한다','operations':[{'action':'start','step_id':'s1'}]}},
                        {'name':'update_turn_plan','arguments':{'goal':'','reason':'자료 비교 결과를 기록한다','operations':[{'action':'complete','step_id':'s1','result':'속도를 존중받아 안도하나 자세한 이야기는 보류하고 싶다','sources':['이번 상담사 발화: 원하는 만큼 말해도 된다는 제안']} ]}}]
                actions=prefix+actions
            idx=len(completed)
            if idx<len(actions):
                action=actions[idx]
                delta={'tool_calls':[{'index':0,'id':'q2a_'+str(idx),'type':'function','function':{'name':action['name'],'arguments':json.dumps(action['arguments'],ensure_ascii=False)}}]}
                finish='tool_calls' if data.get('tool_choice')=='required' else getattr(self,'q2a_finish','stop')
            else:delta={'content':'Q2a_UNFINISHED'};finish='stop'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(('data: '+json.dumps({'choices':[{'index':0,'delta':{'reasoning_content':'Q2a_REASON_'+str(idx)}}]})+'\n\n').encode());self.wfile.flush()
            if self.p1_delay:time.sleep(self.p1_delay)
            self.wfile.write(('data: '+json.dumps({'choices':[{'index':0,'delta':delta,'finish_reason':finish}],'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}},ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode());return
        is_q2_main = any('response_plan' in tool.get('function',{}).get('parameters',{}).get('properties',{}) for tool in data.get('tools',[]))
        is_q2_router = messages[0]['content'].startswith('# 심리상태 검토 대상 선정')
        is_q2_updater = messages[0]['content'].startswith('# 내담자 심리상태 기록 지침')
        if is_q2_main or is_q2_router or is_q2_updater:
            finish='stop'
            if is_q2_main:
                if messages[-1]['role']=='tool':
                    delta={'content':'조금 안심되지만 자세한 이야기는 아직 부담스러워요.'}
                else:
                    value={'appraisal':{'interpretation':'선택을 존중한다고 느낀다.','immediate_feeling':'안도와 망설임','relevant_evidence':'이번 상담사 발화','uncertainty':''},
                           'response_plan':{'action':'안도는 표현하되 공개 범위는 제한한다.','reason':'조심스러운 마음이 남아 있다.'}}
                    delta={'tool_calls':[{'index':0,'id':'q2_stage','type':'function','function':{'name':'stage_client_update','arguments':json.dumps(value,ensure_ascii=False)}}]};finish=self.q2_tool_finish
            else:
                if is_q2_router:value={'selections':self.q2_routes if self.q2_routes is not None else [{'state':'bond','reason':'상담사가 선택을 존중한다고 받아들임'}]}
                else:value={'updates':self.q2_updates if self.q2_updates is not None else [{'state':'bond','content':'선택을 존중받았다고 느끼지만 더 깊은 이야기에는 조심스럽다.','reason':'안도와 망설임이 함께 남는다고 판단했다.'}]}
                delta={'content':json.dumps(value,ensure_ascii=False)};finish=self.q2_post_finish
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            reason='Q2_STATE_ROUTER_REASON' if is_q2_router else 'Q2_STATE_UPDATER_REASON' if is_q2_updater else 'Q2_MAIN_REASON'
            self.wfile.write(('data: '+json.dumps({'choices':[{'index':0,'delta':{'reasoning_content':reason}}]})+'\n\n').encode());self.wfile.flush()
            if self.p1_delay:time.sleep(self.p1_delay)
            chunk={'choices':[{'index':0,'delta':delta,'finish_reason':finish}],'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}}
            self.wfile.write(('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode());return
        is_q1_router = any('전체 특성 목록' in message['content'] for message in messages if message['role']=='user') and not data.get('tools')
        is_q1_main = any(tool.get('function', {}).get('name')=='stage_client_update' for tool in data.get('tools', []))
        if is_q1_router or is_q1_main:
            if is_q1_router:
                ids = re.findall(r'trait_id: ([^\n]+)', messages[-1]['content'])
                text = self.q1_router_response if self.q1_router_response is not None else json.dumps({'selections':[{'trait_id':ids[0],'reason':'이번 상황의 평가에 대한 걱정과 관련됨'}] if ids else []},ensure_ascii=False)
                if self.q1_router_response is None and '# 배경 후보 목록' in messages[-1]['content']:
                    bids=re.findall(r'background_id: ([^\n]+)',messages[-1]['content'])
                    text=json.dumps({'selections':[{'trait_id':ids[0],'background_ids':bids[:1],'relevance':'이번 공개 요청과 개인 배경의 관련성'}] if ids else []},ensure_ascii=False)
                if self.q1_router_response is None and 'note_ids' in data.get('structured_outputs',{}).get('json',{}).get('properties',{}):
                    routed=json.loads(text);routed['note_ids']=re.findall(r'^- \[(n[a-f0-9]{12})\]',messages[-1]['content'],re.M)[:1];text=json.dumps(routed,ensure_ascii=False)
                delta, finish = {'content':text}, self.q1_router_finish
            elif messages[-1]['role']=='tool' or self.q1_skip_update:
                delta, finish = {'content':'그렇게 말씀해 주시니 조금 안심돼요. 그래도 아직은 조심스럽게 이야기하고 싶어요.'}, self.q1_main_finish
            else:
                update = self.q1_update if self.q1_update is not None else {
                    'appraisal':{'interpretation':'내 속도를 존중해 준다고 느낀다.','immediate_feeling':'안도와 망설임이 함께 남는다.',
                                 'relevant_evidence':'상담사의 공개 발화와 이전 내면 노트.','uncertainty':''},
                    'inner_notes':'## 상담사에 대한 현재 인식\nPRIVATE_Q1_NOTE: 속도를 존중해 주지만 아직 조심스럽다.\n'}
                delta = {'tool_calls':[{'index':0,'id':'q1_stage','type':'function','function':{
                    'name':'stage_client_update','arguments':json.dumps(update,ensure_ascii=False)}}]}
                finish = 'tool_calls'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            reason = {'choices':[{'index':0,'delta':{'reasoning_content':'Q1_ROUTER_REASON' if is_q1_router else 'Q1_MAIN_REASON'}}]}
            self.wfile.write(('data: '+json.dumps(reason)+'\n\n').encode());self.wfile.flush()
            if self.p1_delay:time.sleep(self.p1_delay)
            chunk={'choices':[{'index':0,'delta':delta,'finish_reason':finish}], 'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}}
            self.wfile.write(('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode());return
        if (not data.get('tools') and messages[-1]['role'] == 'user'
                and messages[-1]['content'].startswith('# 메인 상담사의 요청\n')
                and '# 참고 가이드 전체\n' in messages[-1]['content']):
            self.send_response(200);self.send_header('Content-Type', 'text/event-stream');self.end_headers()
            reason = {'choices':[{'index':0,'delta':{'reasoning_content':'P3A_CHILD_REASON'}}]}
            self.wfile.write(('data: '+json.dumps(reason)+'\n\n').encode());self.wfile.flush()
            if self.p1_delay:
                time.sleep(self.p1_delay)
            chunk = {'choices':[{'index':0,'delta':{'content':self.p3a_child_response},'finish_reason':self.p3a_child_finish}],
                     'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}}
            self.wfile.write(('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode());return
        if (messages[0]['content'].startswith('당신은 내담자와 대화하는 상담사입니다.')
                and 'COMPOSER_FAIL_REPLY' in messages[1]['content']):
            self.send_error(503, 'Deliberate test upstream failure')
            return
        if any('target' in tool.get('function', {}).get('parameters', {}).get('properties', {})
               and tool.get('function', {}).get('name') == 'update_notes' for tool in data.get('tools', [])):
            count = sum(message['role'] == 'tool' for message in messages)
            user = messages[1]['content']
            client = user.split('## 내담자 노트 · 현재 파일 원문\n\n')[1].split('\n\n## 상담사 생각 노트')[0]
            counselor = user.split('## 상담사 생각 노트 · 현재 파일 원문\n\n')[1]
            latest = user.split('## 내담자 노트')[0].split('내담자:')[-1]
            names = {tool.get('function', {}).get('name') for tool in data.get('tools', [])}
            version = 'P3a' if 'consult_counseling_guide' in names else 'P3' if 'read_counseling_guide' in names else 'P2'
            actions = self.p2_actions if self.p2_actions is not None else ([
                {'target':'client','edits':[{'old_text':client,'new_text':'발표 상황에서 평가받는 것이 걱정됨.'}]},
                {'target':'counselor','edits':[{'old_text':counselor,'new_text':'불안한 상황은 확인됨. 다음에는 회피 행동을 탐색한다.'}]},
            ] if 'P2_EDIT' in latest or 'P3_EDIT' in latest else [])
            actions = [{'name': 'update_notes', 'arguments': action} for action in actions]
            if version == 'P3':
                if self.p3_actions is not None:
                    actions = self.p3_actions
                elif 'P3_GUIDE' in latest:
                    actions = [{'name': 'read_counseling_guide', 'arguments': {'guide': guide}}
                               for guide in ('routing', 'common')] + actions
            if version == 'P3a':
                if self.p3a_actions is not None:
                    actions = self.p3a_actions
                elif 'P3A_GUIDE' in latest:
                    actions = [{'name':'consult_counseling_guide','arguments':{'guide':'cbt','request':latest}}] + actions
            self.send_response(200);self.send_header('Content-Type', 'text/event-stream');self.end_headers()
            reason = {'choices':[{'index':0,'delta':{'reasoning':version+'_REASON_'+str(count)}}]}
            self.wfile.write(('data: '+json.dumps(reason)+'\n\n').encode());self.wfile.flush()
            if self.p1_delay:
                time.sleep(self.p1_delay)
            if count < len(actions):
                delta = {'tool_calls':[{'index':0,'id':'p2_call_'+str(count),'type':'function','function':{
                    'name':actions[count]['name'],'arguments':json.dumps(actions[count]['arguments'],ensure_ascii=False)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'content':'그 걱정 때문에 발표를 피하기도 하나요?'}, self.dual_notes_finish
            chunk = {'choices':[{'index':0,'delta':delta,'finish_reason':finish}],
                     'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}}
            self.wfile.write(('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode());return
        if any(tool.get('function', {}).get('name') == 'update_notes' for tool in data.get('tools', [])):
            has_result = messages[-1]['role'] == 'tool'
            results = sum(message['role'] == 'tool' for message in messages)
            note = messages[1]['content'].split('## 상담 노트 · 현재 파일 원문\n\n')[-1]
            self.send_response(200);self.send_header('Content-Type', 'text/event-stream');self.end_headers()
            stage = 'BEFORE_TOOL' if not has_result else 'AFTER_TOOL' if self.p1_repeat and results == 1 else 'FINAL'
            for key, value in [('reasoning_content', 'P1_REASON_'), ('reasoning', stage)]:
                self.wfile.write(('data: '+json.dumps({'choices':[{'index':0,'delta':{key:value}}]})+'\n\n').encode());self.wfile.flush()
            if self.p1_gate is not None:
                self.p1_gate.wait(timeout=5)
            if self.p1_delay:
                time.sleep(self.p1_delay)
            latest = messages[1]['content'].split('## 상담 노트')[0].split('내담자:')[-1]
            if self.p1_repeat and results == 1:
                delta = {'tool_calls': [{'index':0,'id':'p1_call_2','type':'function','function':{'name':'update_notes','arguments':json.dumps({'edits':[{'old_text':'','new_text':'추가 기록'}]},ensure_ascii=False)}}]}
                finish = 'tool_calls'
            elif has_result or (note and 'P1_EDIT' not in latest):
                delta, finish = {'content': '말씀하신 걱정을 함께 살펴보겠습니다.'}, 'stop'
            else:
                edits = [{'old_text': note if note else '', 'new_text': '현재 걱정: 평가에 대한 불안.\n'}]
                delta = {'tool_calls': [{'index':0,'id':'p1_call','type':'function','function':{'name':'update_notes','arguments':json.dumps({'edits':edits},ensure_ascii=False)}}]}
                finish = 'tool_calls'
            chunk = {'choices':[{'index':0,'delta':delta,'finish_reason':finish}], 'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}}
            self.wfile.write(('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode());return
        if messages[0]['role'] == 'system' and messages[0]['content'].startswith('당신은 내담자와 대화하는 상담사입니다.'):
            chunk = {'choices': [{'index': 0, 'delta': {'content': '그 마음을 조금 더 들려주셔도 괜찮아요.'}, 'finish_reason': self.basic_counselor_finish}],
                     'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130}}
            self.send_response(200);self.send_header('Content-Type', 'text/event-stream');self.end_headers()
            self.wfile.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())
            return
        if messages[0]['role'] == 'system' and messages[0]['content'].startswith(('당신은 다음 상담 반응에 필요한', '당신은 다음 공개 반응에 적절한', '당신은 상담사의 다음 공개 발화를')):
            if self.c4_gate is not None:
                self.c4_gate.wait(timeout=5)
            payload = json.loads(messages[1]['content'])
            latest = payload['latest_client_utterance']
            if messages[0]['content'].startswith('당신은 다음 상담 반응에 필요한'):
                value = {'information': {'additions': [{'kind': 'fact', 'text': latest, 'evidence': latest}], 'retirements': []},
                         'work_observation': {'client_response': '현재 걱정', 'evidence': latest, 'remaining': []},
                         'task_board': payload['task_board'] or [{'id': 'T1', 'source': 'client_request', 'goal': '걱정 이해',
                         'known': [], 'remaining': ['걱정 이해'], 'boundaries': [], 'status': 'open',
                         'evidence': [{'speaker': 'user', 'quote': latest}]}]}
            elif messages[0]['content'].startswith('당신은 다음 공개 반응에 적절한'):
                value = {'action': {'target': '걱정', 'instruction': '내담자의 걱정을 인정합니다.', 'reason': '공개 발화'},
                         'task_focus': {'task_id': 'T1', 'remaining_need': '걱정 이해', 'reason': '공개 발화'},
                         'task_link': {'task_id': 'T1', 'remaining_part': '걱정 이해', 'already_available': latest, 'contribution': '걱정 인정'}}
            else:
                value = {} if self.c4_fail else {'support': '', 'working_response': '걱정이 남아 있는 부분을 조금 더 들려주셔도 괜찮아요.',
                                                'question': '', 'grounding': [latest], 'reason': '선택한 행동 수행'}
            chunk = {'choices': [{'index': 0, 'delta': {'content': json.dumps(value, ensure_ascii=False)}, 'finish_reason': 'stop'}],
                     'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130}}
            self.send_response(200);self.send_header('Content-Type', 'text/event-stream');self.end_headers()
            self.wfile.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())
            return
        is_main = messages[0]['content'] == '주어진 툴을 적절히 활용하세요.'
        has_result = messages[-1]['role'] == 'tool'
        if '당신은 누적된 내담자의 내면 흐름을 다음 턴에 전달하는 요약 담당' in messages[0]['content']:
            delta = {'content': '속도 존중에 조금 안도했지만 자세히 말하는 데에는 아직 망설임이 남아 있다.'}
            finish = 'stop'
        elif '## 내면상태와 공개 발화' in messages[0]['content']:
            current = messages[-1]['content'].split('[이번 상담자 발화]')[-1]
            if '요약 조회 테스트' in current and not has_result:
                delta = {'tool_calls': [{'index': 0, 'id': 'call_inner', 'type': 'function', 'function': {
                    'name': 'retrieve_inner_state', 'arguments': json.dumps({'query': '처음 상담사가 편한 만큼 이야기하라고 했을 때 안도하면서도 망설인 이유', 'state': 'trust', 'reason': '구체적인 걱정이 요약에 없어 확인합니다.'})}}]}
                finish = 'tool_calls'
            else:
                result = {'inner_state': {'states': {key: {'level': '높음' if key == 'trust' else '낮음',
                          'reason': 'PRIVATE_DETAIL_원문에만_있는_걱정_' + key} for key in ('trust', 'disclosure', 'change_confidence')},
                          'interpretation': '내 속도를 존중해 준다고 받아들임',
                          'response_basis': '조금 안도하지만 아직 자세한 이야기는 망설임'},
                          'feelings': ['선택권을 존중받음'], 'utterance': '조금 편해졌어요. 아직 자세히 말하기는 망설여져요.'}
                raw = json.dumps(result, ensure_ascii=False)
                if self.state_json_fence:
                    raw = '```json\n' + raw + '\n```'
                delta = {'content': 'invalid output' if '잘못된 상태 출력 테스트' in current else raw}
                finish = 'length' if '토큰 한도 테스트' in current else 'stop'
        elif messages[0]['content'].startswith('내면 반응 조회 담당'):
            ids = re.findall(r'- record_id: ([^\n]+)', messages[1]['content'])
            query = messages[1]['content'].split('## 조회 이유')[0]
            chosen = None if '없는 장면' in query else 'unknown_id' if '없는 ID' in query else ids[0] if '처음' in query else ids[-1]
            delta = {'content': json.dumps({'record_id': chosen})}
            finish = 'stop'
        elif messages[0]['content'].startswith(('성장 경험 조회 담당', '상황 기록 조회 담당')):
            records = json.loads(messages[1]['content'])['records']
            delta = {'content': json.dumps({'record_id': records[-1]['id']})}
            finish = 'stop'
        elif is_main and not has_result and '선택 조회 테스트' in messages[-1]['content']:
            name = 'retrieve_childhood' if '성장' in messages[-1]['content'] else 'retrieve_situation'
            delta = {'tool_calls': [{'index': 0, 'id': 'call_select', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps({'reason': '반응의 근거 확인', 'query': '어떤 경험인가'})}}]}
            finish = 'tool_calls'
        elif is_main and not has_result and '위임 테스트' in messages[-1]['content']:
            delta = {'content': '느낌 판단을 맡겨보겠습니다.', 'tool_calls': [{
                'index': 0, 'id': 'call_mock', 'type': 'function', 'function': {
                    'name': 'delegate_task', 'arguments': json.dumps({
                        'reason': '느낌 판단을 별도로 검토합니다.', 'role': '느낌 검토자',
                        'task': '상담자의 행동 제안에 압박을 느낄 수 있는지 검토하세요.'}, ensure_ascii=False)}}]}
            finish = 'tool_calls'
        elif is_main and not has_result and '상태 근거 테스트' in messages[-1]['content']:
            delta = {'tool_calls': [{'index': 0, 'id': 'call_state', 'type': 'function',
                'function': {'name': 'inspect_state_basis', 'arguments': json.dumps({
                    'state': 'trust', 'reason': '직전 발화와 현재 상태의 관계 확인'})}}]}
            finish = 'tool_calls'
        elif is_main and not has_result and '경험 조회 테스트' in messages[-1]['content']:
            delta = {'content': '이전 피드백 경험을 조회하겠습니다.', 'tool_calls': [{
                'index': 0, 'id': 'call_retrieve', 'type': 'function', 'function': {
                    'name': 'retrieve_experience', 'arguments': json.dumps({
                        'reason': '이전 사건의 구체적인 경위가 필요합니다.', 'query': '교수님 피드백'}, ensure_ascii=False)}}]}
            finish = 'tool_calls'
        elif is_main and not has_result and any(name in messages[-1]['content'] for name in ('inspect_current_event', 'get_current_constraints')):
            name = next(name for name in ('inspect_current_event', 'get_current_constraints') if name in messages[-1]['content'])
            delta = {'content': '현재 자료를 확인하겠습니다.', 'tool_calls': [{
                'index': 0, 'id': 'call_record', 'type': 'function', 'function': {
                    'name': name, 'arguments': json.dumps({'reason': '현재 자료 확인'}, ensure_ascii=False)}}]}
            finish = 'tool_calls'
        else:
            delta = {'content': '느낌: 이해받음, 부담·압박을 느낌\n판단 이유: 걱정을 알아주지만 실행에는 자신이 부족합니다.\n' + self.basic_label + ': 알아주셔서 감사해요. 다만 이번 주에 바로 할 수 있을지는 잘 모르겠어요.'}
            finish = 'stop'
        chunk = {'choices': [{'delta': delta, 'finish_reason': finish}],
                 'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130}}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.wfile.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())


def fixture(experiment_version='basic', prompt_editing=False):
    temp = tempfile.TemporaryDirectory()
    model = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    cfg = json.loads((ROOT / 'config/app.json').read_text())
    cfg.update(vllm_url=f'http://127.0.0.1:{model.server_port}/v1', inspect_prompt=False, experiment_version=experiment_version)
    path = Path(temp.name) / 'config.json'
    path.write_text(json.dumps(cfg))
    server = app.make_server(path, port=0, storage_dir=Path(temp.name) / 'sessions')
    if prompt_editing:
        import app.prompt_files as prompt_files
        import runtime.user_templates as user_templates
        from counselor import catalog
        source = Path(temp.name) / 'prompt_sources'
        for relative in ('resources/prompts/basic_counselor.md', 'src/client/state_summary/system.md',
                         'src/client/state_summary/summary.md',
                         'src/client/Q2a_prefill_test/trait_router_prefill.txt',
                         'src/client/Q3/trajectory.md','src/client/Q3a/trajectory.md','src/client/Q3b/trajectory.md',
                         *(f'src/client/{v}/{n}.md' for v in app.experiment_versions.MEMORY_EXPERIMENTS for n in ('system','trait_router','state_notes_template')),
                         *(f'src/client/Q2a_prefill_test/{name}.md' for name in ('system','trait_router','state_notes_template')),
                         *(f'src/client/Q2a_trait_background/{name}.md' for name in ('system','trait_router','state_notes_template')),
                         *(f'src/client/Q2a_variant_context/{name}.md' for name in ('system','trait_router','state_notes_template')),
                         *(f'src/client/Q2a/{name}.md' for name in ('system','trait_router','state_notes_template')),
                         *(f'src/client/Q2/{name}.md' for name in ('system','trait_router','state_router','state_updater','state_notes_template')),
                         'src/client/Q1/system.md','src/client/Q1/trait_router.md','src/client/Q1/inner_notes_template.md',
                         'src/client/Q1_s1/system.md','src/client/Q1_s1/trait_router.md','src/client/Q1_s1/inner_notes_template.md',
                         'src/client/Q1a/system.md','src/client/Q1a/trait_router.md','src/client/Q1a/inner_notes_template.md','src/client/Q1a/draft_reviewer.md',
                         'src/client/Q1_s2/system.md','src/client/Q1_s2/trait_router.md','src/client/Q1_s2/inner_notes_template.md',
                         *(f'src/counselor/{version}/system.md' for version in ('P1', 'P2', 'P3', 'P3a', 'P3a_s1')),
                         'src/counselor/P3a/guide_agent.md','src/counselor/P3a_s1/guide_agent.md',
                         'src/counselor/P3a_s1/guide_grounded.md'):
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        for spec in user_templates.REGISTRY.values():
            destination = source / spec['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / spec['path'], destination)
        stack = server.prompt_source_stack = ExitStack()
        for module in (prompt_files, user_templates, catalog, app.experiment_versions):
            stack.enter_context(patch.object(module, 'HERE', source))
        for version, package in catalog.PRACTICE_VERSIONS.items():
            stack.enter_context(patch.object(package.runtime, 'HERE', source / 'src/counselor' / version))
        for version in app.experiment_versions.MEMORY_EXPERIMENTS:
            stack.enter_context(patch.object(app.experiment_versions.client_runtime(version), 'HERE', source / 'src/client' / version))
        stack.enter_context(patch.object(app.experiment_versions.client_runtime('Q2a_prefill_test'), 'HERE', source / 'src/client/Q2a_prefill_test'))
        stack.enter_context(patch.object(app.experiment_versions.client_runtime('Q2a_trait_background'), 'HERE', source / 'src/client/Q2a_trait_background'))
        stack.enter_context(patch.object(app.experiment_versions.client_runtime('Q2a_variant_context'), 'HERE', source / 'src/client/Q2a_variant_context'))
        stack.enter_context(patch.object(app.experiment_versions.client_runtime('Q2a'), 'HERE', source / 'src/client/Q2a'))
        stack.enter_context(patch.object(app.experiment_versions.client_runtime('Q2'), 'HERE', source / 'src/client/Q2'))
        stack.enter_context(patch.object(app.runtime.q1, 'HERE', source / 'src/client/Q1'))
        stack.enter_context(patch.object(app.experiment_versions.client_runtime('Q1_s1'), 'HERE', source / 'src/client/Q1_s1'))
        stack.enter_context(patch.object(app.experiment_versions.client_runtime('Q1_s2'), 'HERE', source / 'src/client/Q1_s2'))
        stack.enter_context(patch.object(app.experiment_versions.client_runtime('Q1a'), 'HERE', source / 'src/client/Q1a'))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, model, temp


def close(server, model, temp):
    for instance in (server, model):
        instance.shutdown()
        instance.server_close()
    if hasattr(server, 'prompt_source_stack'):
        server.prompt_source_stack.close()
    temp.cleanup()


def post(server, path, body):
    request = Request(f'http://127.0.0.1:{server.server_port}' + path,
        data=json.dumps(body).encode(), headers={'X-Gemma-Request': '1'})
    return urlopen(request).read().decode()


class LabTests(unittest.TestCase):
    def setUp(self):
        self.server, self.model, self.temp = fixture()
        Model.requests.clear()

    def tearDown(self):
        close(self.server, self.model, self.temp)

    def test_group1_histories_and_withheld_answers(self):
        catalog = json.loads(urlopen(f'http://127.0.0.1:{self.server.server_port}/api/histories/group1').read())
        self.assertEqual([p['number'] for p in catalog['profiles']], ['001', '002', '005', '007', '008'])
        ids = set()
        for entry in catalog['profiles']:
            original = json.loads((ROOT / 'resources/profiles/group1' / ('gold_ccd_profile_' + entry['number'] + '.json')).read_text())
            self.assertEqual(len(entry['histories']), 3)
            for history in entry['histories']:
                scenario = history['scenario']
                ids.add(scenario['id'])
                self.assertEqual(len(history['turns']), 5)
                self.assertEqual(scenario['profile']['profile_id'], original['profile_id'])
                for key in ('demographic_context', 'presenting_problem', 'core_belief', 'intermediate_beliefs', 'coping_strategies'):
                    self.assertEqual(scenario['profile'][key], original[key])
                self.assertNotIn('childhood_data', scenario['profile'])
                self.assertNotIn('situations', scenario['profile'])
                target = history['turns'][-1]['counselor']
                payload = json.loads(post(self.server, '/api/preview', {'scenario': scenario, 'messages': [{'role': 'user', 'content': target}]}))['payload']
                prompt = payload['messages'][1]['content']
                self.assertTrue(prompt.endswith(target))
                self.assertNotIn(history['turns'][-1]['client'], prompt)
                self.assertIn(history['turns'][-2]['client'], prompt)
                self.assertNotIn('source_notes', prompt)
        self.assertEqual(len(ids), 15)
        self.assertEqual(Model.requests, [])

    def test_profile_exposure_and_optional_blocks_reach_model_input(self):
        catalog = json.load(urlopen(f'http://127.0.0.1:{self.server.server_port}/api/histories/group1'))
        entry = catalog['profiles'][0]
        scenario = copy.deepcopy(entry['histories'][0]['scenario'])
        scenario['profile'] = entry['source_profile']
        self.assertIn('childhood_data', scenario['profile'])
        original = copy.deepcopy(scenario)
        for version in ('basic', 'state_summary'):
            body = {'experiment_version': version, 'scenario': scenario,
                    'messages': [{'role': 'user', 'content': scenario['selected_counselor_utterance']}],
                    'enabled_tools': [], 'profile_context_mode': 'selective'}
            def preview():
                return json.loads(post(self.server, '/api/preview', body))['payload']
            selective = preview()['messages'][-1]['content']
            self.assertNotIn('childhood_data', selective)
            self.assertNotIn('situations', selective)
            body['profile_context_mode'] = 'full'
            full = preview()['messages'][-1]['content']
            self.assertIn(scenario['profile']['childhood_data'], full)
            self.assertIn('situations', full)
            body['input_blocks'] = {key: False for key in ('system', 'task', 'profile', 'dialogue')}
            payload = preview()
            self.assertEqual(len(payload['messages']), 1)
            text = payload['messages'][0]['content']
            for value in (scenario['task'], scenario['profile']['presenting_problem'], scenario['dialogue'][0]['content']):
                self.assertNotIn(value, text)
            self.assertIn('## states', text)
            self.assertIn('## taxonomy', text)
            self.assertTrue(text.endswith(scenario['selected_counselor_utterance']))
        self.assertEqual(scenario, original)
        self.assertEqual(Model.requests, [])

    def test_invalid_exposure_controls_rejected_before_generation(self):
        from urllib.error import HTTPError
        for option in ({'profile_context_mode': 'unknown'}, {'input_blocks': {'system': 'false'}},
                       {'input_blocks': {'unexpected': False}}):
            with self.assertRaises(HTTPError):
                post(self.server, '/api/preview', {'messages': [{'role': 'user', 'content': '생성'}], **option})
        self.assertEqual(Model.requests, [])


    def test_selective_retrieval_child_and_parent_boundary(self):
        import resources.profile_retrieval as profile_retrieval
        for name, word in [('retrieve_childhood', '성장'), ('retrieve_situation', '상황')]:
            Model.requests.clear()
            scenario = app.load_catalog(app.runtime.load_scenario({}), app.runtime.validate_scenario)['profiles'][1]['histories'][0]['scenario']
            scenario['dialogue_turns'][-1]['counselor'] = word + ' 선택 조회 테스트'
            body = {'scenario': scenario, 'enabled_tools': [name],
                    'messages': [{'role': 'user', 'content': scenario['dialogue_turns'][-1]['counselor']}]}
            events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
            self.assertEqual(events[-1]['type'], 'done')
            self.assertEqual(len(Model.requests), 3)
            main, child, resumed = Model.requests
            self.assertEqual(main['model'], child['model'])
            self.assertNotIn('childhood_data', main['messages'][1]['content'])
            self.assertNotIn('situations', main['messages'][1]['content'])
            self.assertNotIn('tools', child)
            database = json.loads(child['messages'][1]['content'])
            self.assertEqual(database['profile_id'], 'gold_002')
            self.assertEqual(len(child['messages']), 2)
            result = next(e for e in events if e['type'] == 'tool_result')
            self.assertFalse(result['is_error'])
            content = json.loads(result['model_content'])
            self.assertEqual(content['record'], database['records'][-1])
            self.assertNotIn('records', content)
            self.assertEqual(resumed['messages'][-1]['role'], 'tool')
            for e in events:
                if e['type'] == 'agent_event':
                    self.assertEqual(e['role_source'], 'developer')
            for invalid in ('{"record_id":"invented"}', '{"record_id":["a","b"]}', '{"record_id":null,"extra":1}'):
                with self.assertRaises(ValueError):
                    profile_retrieval.selected_result({'records': database['records']}, invalid, 'test')

    def test_enabled_pairs_generate_at_previous_counselor(self):
        catalog = app.load_catalog(app.runtime.load_scenario({}), app.runtime.validate_scenario)
        scenario = copy.deepcopy(catalog['profiles'][0]['histories'][0]['scenario'])
        turns = scenario['dialogue_turns']
        turns[-1]['enabled'] = False
        turns[1]['enabled'] = False
        # The server derives the input from selected source pairs, ignoring stale or tampered dialogue.
        scenario['dialogue'] = [{'role': 'client', 'content': 'THIS SHOULD NOT REACH THE MODEL'}]
        target = turns[3]['counselor']
        body = {'scenario': scenario, 'messages': [{'role': 'user', 'content': target}]}
        preview = json.loads(post(self.server, '/api/preview', body))['payload']
        prompt = preview['messages'][1]['content']
        self.assertTrue(prompt.endswith(target))
        self.assertIn(turns[2]['client'], prompt)
        for text in (turns[1]['counselor'], turns[1]['client'], turns[3]['client'], turns[4]['counselor'], turns[4]['client'], 'THIS SHOULD NOT REACH THE MODEL'):
            self.assertNotIn(text, prompt)
        events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(Model.requests[0]['messages'], preview['messages'])
        saved = json.loads(Path(events[0]['saved_path']).read_text())
        self.assertEqual(saved['input']['scenario']['dialogue_turns'], turns)
        followup = {**body, 'session_id': events[0]['session_id'], 'messages': events[-1]['history'] + [{'role': 'user', 'content': '이어서 묻는 질문'}]}
        next_payload = json.loads(post(self.server, '/api/preview', followup))['payload']
        self.assertEqual(next_payload['messages'][1]['content'], prompt)
        self.assertEqual(next_payload['messages'][-1]['content'], '이어서 묻는 질문')
        for turn in turns:
            turn['enabled'] = False
        with self.assertRaisesRegex(ValueError, '하나 이상'):
            app.runtime.validate_scenario(scenario)
        turns[0]['enabled'] = True
        turns[0]['client'] = ''
        normalized = app.runtime.validate_scenario(scenario)
        self.assertEqual(normalized['dialogue'], [])
        turns[2]['enabled'] = True
        with self.assertRaisesRegex(ValueError, '내담자 발화'):
            app.runtime.validate_scenario(scenario)

    def test_group1_records_do_not_leak_old_case(self):
        scenario = app.load_catalog(app.runtime.load_scenario({}), app.runtime.validate_scenario)['profiles'][0]['histories'][0]['scenario']
        config = {'_experience_case_id': scenario['id'], '_request_states': scenario['states']}
        results = [app.runtime.retrieve_experience(config, {'query': '교수님 피드백', 'reason': '확인'}),
                   app.runtime.inspect_state_basis(config, {'state': 'trust', 'reason': '확인'})]
        results.extend(app.runtime.inspect_record(config, name, {'reason': '확인'}) for name in ('inspect_current_event', 'get_current_constraints'))
        for result in results:
            self.assertEqual(result['status'], 'not_recorded')
            self.assertNotIn('교수님', json.dumps({k:v for k,v in result.items() if k!='query'}, ensure_ascii=False))

    def test_initial_context_direct_and_followup(self):
        scenario = app.runtime.load_scenario({})
        scenario['states']['trust'].update(level='높음', meaning=scenario['state_levels']['trust']['높음'])
        body = {'messages': [{'role': 'user', 'content': scenario['example_counselor_utterance']}], 'scenario': scenario}
        preview = json.loads(post(self.server, '/api/preview', body))['payload']
        self.assertEqual(preview['messages'][0]['content'], '주어진 툴을 적절히 활용하세요.')
        self.assertEqual([t['function']['name'] for t in preview['tools']], ['delegate_task', 'retrieve_experience', 'inspect_current_event', 'get_current_constraints', 'inspect_state_basis'])
        for key in ('dialogue', 'profile', 'states', 'taxonomy'):
            self.assertIn('## ' + key + '\n', preview['messages'][1]['content'])
        script = '\n\n'.join(
            ('상담사' if row['role'] == 'counselor' else '내담자') + ': ' + row['content']
            for row in scenario['dialogue'])
        self.assertIn('## dialogue\n\n' + script + '\n\n## profile', preview['messages'][1]['content'])
        self.assertNotIn('- role:', preview['messages'][1]['content'])
        self.assertNotIn('source_notes', preview['messages'][1]['content'])
        for note in scenario['source_notes'].values():
            self.assertNotIn(note, preview['messages'][1]['content'])
        self.assertIn('  - level: 높음\n', preview['messages'][1]['content'])
        self.assertNotIn('example_counselor_utterance', preview['messages'][1]['content'])
        self.assertIn(scenario['states']['trust']['meaning'], preview['messages'][1]['content'])
        for hidden in ('state_levels', 'counselor_presets', scenario['counselor_presets'][2]['content']):
            self.assertNotIn(hidden, preview['messages'][1]['content'])
        self.assertEqual(Model.requests, [])
        events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
        self.assertEqual(events[0]['payload'], preview)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['tool_calls_used'], 0)
        saved = json.loads(Path(events[0]['saved_path']).read_text())
        self.assertEqual(saved['input']['scenario'], scenario)
        self.assertEqual(saved['status'], 'completed')
        body['messages'] = events[-1]['history'] + [{'role': 'user', 'content': 'あなたのペースで大丈夫です。'}]
        followup = json.loads(post(self.server, '/api/preview', body))['payload']
        self.assertEqual(json.dumps(followup, ensure_ascii=False).count('[初期資料'), 0)
        self.assertEqual(json.dumps(followup, ensure_ascii=False).count('[초기 자료:'), 1)
        self.assertEqual(followup['messages'][1], preview['messages'][1])

    def test_selected_tools_preview_execution_and_child_inheritance(self):
        names = ['delegate_task', 'inspect_state_basis']
        body = {'messages': [{'role': 'user', 'content': '위임 테스트'}], 'enabled_tools': names}
        preview = json.loads(post(self.server, '/api/preview', body))['payload']
        self.assertEqual([t['function']['name'] for t in preview['tools']], names)
        events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(len(Model.requests), 3)
        for request in Model.requests:
            self.assertEqual(request['tools'], preview['tools'])
        saved = json.loads(Path(events[0]['saved_path']).read_text())
        self.assertEqual(saved['input']['enabled_tools'], names)

    def test_no_tools_and_unselected_tool_is_not_executed(self):
        body = {'messages': [{'role': 'user', 'content': '도구 없는 응답 테스트'}], 'enabled_tools': []}
        preview = json.loads(post(self.server, '/api/preview', body))['payload']
        for key in ('tools', 'tool_choice', 'parallel_tool_calls'):
            self.assertNotIn(key, preview)
        events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertNotIn('tools', Model.requests[0])
        Model.requests.clear()
        # The mock deliberately calls an unavailable tool; execution must reject it.
        body['messages'][0]['content'] = '위임 테스트'
        events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertFalse(any(e['type'] == 'agent_event' for e in events))
        result = next(e for e in events if e['type'] == 'tool_result')
        self.assertTrue(result['is_error'])
        self.assertIn('허용되지 않은 도구', result['model_content'])
        self.assertEqual(len(Model.requests), 2)
        self.assertTrue(all('tools' not in request for request in Model.requests))

    def test_tool_selection_validation(self):
        for names in (None, 'delegate_task', ['unknown'], ['delegate_task', 'delegate_task'], [42]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                app.runtime.selected_definitions({}, {'enabled_tools': names})
        self.assertEqual(len(app.runtime.selected_definitions({}, {})), 5)
        self.assertEqual(app.runtime.selected_definitions({'_enabled_tools': []}, {}), [])

    def test_delegation_isolated_context_and_logs(self):
        body = {'messages': [{'role': 'user', 'content': '위임 테스트'}]}
        events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(len(Model.requests), 3)
        child = Model.requests[1]
        self.assertEqual(child['messages'], [
            {'role': 'system', 'content': '느낌 검토자'},
            {'role': 'user', 'content': '상담자의 행동 제안에 압박을 느낄 수 있는지 검토하세요.'}])
        self.assertEqual(child['tools'], Model.requests[0]['tools'])
        self.assertTrue(any(e['type'] == 'agent_event' for e in events))
        self.assertEqual(events[-1]['history'][1]['content'], '느낌 판단을 맡겨보겠습니다.')
        args = json.loads(events[-1]['history'][1]['tool_calls'][0]['function']['arguments'])
        self.assertIn('reason', args)
        saved = json.loads(Path(events[0]['saved_path']).read_text())
        self.assertTrue(saved['subagent_events'])

    def test_fictional_case_and_profile_boundaries(self):
        scenario = app.runtime.load_scenario({})
        self.assertEqual(scenario['id'], 'lab_meeting_handover')
        self.assertEqual([row['role'] for row in scenario['dialogue']], ['counselor', 'client'] * 5)
        self.assertEqual(len(scenario['profile']['traits']), 5)
        self.assertIn('가상', scenario['source_notes']['case_origin'])
        self.assertIn('실습을 위해 새로 작성', scenario['source_notes']['dialogue'])
        context = app.runtime.initial_context(scenario)
        for old_detail in ('smd', 'SMD', '이보화', '사회불안', '29세', '거절 문자'):
            self.assertNotIn(old_detail, context)
        self.assertNotIn(scenario['example_counselor_utterance'], context)
        self.assertEqual([v['level'] for v in scenario['states'].values()], ['중간', '낮음', '낮음'])
        old = json.loads((ROOT / 'resources/cases/work_stress.json').read_text())
        self.assertEqual(app.runtime.validate_scenario(old), old)
        old_script = app.runtime.dialogue_script(old)
        self.assertTrue(old_script.startswith('상담사: 오늘은 어떤 이야기를 나누고 싶으세요?\n\n내담자: '))
        self.assertEqual(old_script.count('상담사: '), 5)
        self.assertEqual(old_script.count('내담자: '), 5)
        self.assertIn(old['dialogue'][-1]['client'], old_script)

    def test_retrieval_context_and_log(self):
        body = {'messages': [{'role': 'user', 'content': '경험 조회 테스트'}]}
        events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(len(Model.requests), 2)
        self.assertNotIn('비교 조건을 표로 정리', json.dumps(Model.requests[0], ensure_ascii=False))
        result = json.loads(Model.requests[1]['messages'][-1]['content'])
        self.assertEqual(result['matches'][0]['id'], 'exp_feedback')
        self.assertIn('비교 조건을 표로 정리', result['matches'][0]['event'])
        self.assertEqual(Model.requests[1]['messages'][-1]['tool_call_id'], 'call_retrieve')
        self.assertEqual(events[-1]['tool_calls_used'], 1)
        self.assertFalse(any(e['type'] == 'agent_event' for e in events))
        saved = json.loads(Path(events[0]['saved_path']).read_text())
        self.assertIn('exp_feedback', json.dumps(saved['history']))

    def test_retrieval_missing_invalid_and_case_isolation(self):
        retrieve = app.runtime.retrieve_experience
        result = retrieve({}, {'reason': '이전 경험 조회', 'query': '우주여행'})
        self.assertEqual(result['status'], 'no_match')
        self.assertEqual(result['matches'], [])
        with self.assertRaises(ValueError):
            retrieve({}, {'reason': '확인', 'query': ''})
        with self.assertRaises(ValueError):
            retrieve({}, {'reason': '확인', 'query': '교수', 'path': '/tmp/other'})
        with self.assertRaises(ValueError):
            retrieve({'_experience_case_id': 'work_stress'}, {'reason': '확인', 'query': '교수'})

    def test_editable_initial_input(self):
        scenario = app.runtime.load_scenario({})
        scenario['dialogue'] = [{'role': 'client', 'content': '수동으로 설정한 초기 발화\n이어지는 내용'}]
        scenario['profile']['background'] = '수동 프로필'
        scenario['profile']['details'] = {'memo': '첫 번째 줄\n두 번째 줄', 'items': [], 'metadata': {}}
        scenario['task'] = '수동 과제 지침'
        body = {'system_prompt': '수동 메인 지침', 'scenario': scenario,
                'messages': [{'role': 'user', 'content': '수동 입력 검증'}]}
        preview = json.loads(post(self.server, '/api/preview', body))['payload']
        self.assertEqual(preview['messages'][0]['content'], '수동 메인 지침')
        for phrase in ('수동 프로필', '수동 과제 지침', '수동으로 설정한 초기 발화'):
            self.assertIn(phrase, preview['messages'][1]['content'])
        self.assertIn('내담자: 수동으로 설정한 초기 발화\n이어지는 내용', preview['messages'][1]['content'])
        self.assertIn('- details:\n  - memo: 첫 번째 줄\n    두 번째 줄\n  - items: []\n  - metadata: {}', preview['messages'][1]['content'])
        self.assertNotIn('constraints_01', str(preview))
        events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
        self.assertEqual(Model.requests[0]['messages'], preview['messages'])
        saved = json.loads(Path(events[0]['saved_path']).read_text())
        self.assertEqual(saved['input']['scenario'], scenario)
        scenario['dialogue'] = []
        self.assertEqual(app.runtime.validate_scenario(scenario)['dialogue'], [])

    def test_current_records(self):
        for name, expected in [('inspect_current_event', 'current_event_01'), ('get_current_constraints', 'constraints_01')]:
            result = app.runtime.inspect_record({}, name, {'reason': '현재 사실 확인'})
            self.assertEqual(result['record']['id'], expected)
            Model.requests.clear()
            events = [json.loads(line) for line in post(self.server, '/api/chat', {'messages': [{'role': 'user', 'content': name}]}).splitlines()]
            self.assertEqual(events[-1]['tool_calls_used'], 1)
            self.assertEqual(len(Model.requests), 2)
            actual = json.loads(Model.requests[1]['messages'][-1]['content'])
            self.assertEqual(actual['record']['id'], expected)

            with self.assertRaises(ValueError):
                app.runtime.inspect_record({'_experience_case_id': 'different'}, name, {'reason': '확인'})
            with self.assertRaises(ValueError):
                app.runtime.inspect_record({}, name, {'reason': '확인', 'path': '/tmp/other'})

    def test_state_basis_request_snapshot_and_errors(self):
        scenario = app.runtime.load_scenario({})
        scenario['states']['trust']['level'] = '높음'
        scenario['states']['trust']['meaning'] = scenario['state_levels']['trust']['높음']
        body = {'scenario': scenario, 'messages': [{'role': 'user', 'content': '상태 근거 테스트'}]}
        preview = json.loads(post(self.server, '/api/preview', body))['payload']
        self.assertNotIn('설정 이유와 이전 변화 이력이 제공되지 않았습니다.', str(preview))
        events = [json.loads(line) for line in post(self.server, '/api/chat', body).splitlines()]
        self.assertEqual(events[-1]['tool_calls_used'], 1)
        result = json.loads(Model.requests[1]['messages'][-1]['content'])
        self.assertEqual(result['current_state']['level'], '높음')
        self.assertIsNone(result['record']['basis'])
        with self.assertRaises(ValueError):
            app.runtime.inspect_state_basis({'_experience_case_id': 'other'}, {'state': 'trust', 'reason': '확인'})
        with self.assertRaises(ValueError):
            app.runtime.inspect_state_basis({}, {'state': 'bad', 'reason': '확인'})
        with self.assertRaises(ValueError):
            app.runtime.inspect_state_basis({}, {'state': 'trust', 'reason': '확인', 'path': '/tmp/other'})

    def test_scenario_and_tools_api(self):
        with urlopen(f'http://127.0.0.1:{self.server.server_port}/api/profiles/group1') as response:
            library = json.load(response)
        self.assertEqual(library['group'], 'group1')
        self.assertEqual([entry['number'] for entry in library['profiles']], ['001', '002', '005', '007', '008'])
        for entry in library['profiles']:
            source = json.loads((ROOT / 'resources/profiles/group1' / entry['filename']).read_text())
            self.assertEqual(entry['profile'], source)
        self.assertEqual(Model.requests, [])
        tools = json.loads(post(self.server, '/api/tools', {}))
        self.assertTrue(tools['enabled'])
        self.assertIsNone(tools['max_depth'])
        self.assertEqual(len(tools['tools']), 7)
        scenario = app.runtime.load_scenario({})
        scenario['states']['trust']['level'] = 0.6
        with self.assertRaises(ValueError):
            app.runtime.validate_scenario(scenario)


if __name__ == '__main__':
    if '--serve' in sys.argv:
        if '--p1-slow' in sys.argv:
            Model.p1_delay = 1
        instances = fixture('state_summary' if '--state-summary' in sys.argv else 'basic',
                            prompt_editing='--prompt-editing' in sys.argv)
        print(f'http://127.0.0.1:{instances[0].server_port}', flush=True)
        try:
            threading.Event().wait()
        finally:
            close(*instances)
    else:
        unittest.main()
