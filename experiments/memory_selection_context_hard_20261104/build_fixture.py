"""Harder manually authored query contexts; no model generation of records or labels."""
import copy,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SOURCE=ROOT.parent/'memory_selection_long_20261104/records_long.json'
f=json.loads(SOURCE.read_text())
# Remove easy unique event tags/names throughout every visible/hidden input.
replacements=[('파란 카드','메모'),('초록색 말풍선','답장'),('초록 메시지','답장'),('수연','동료'),('지수','친구')]
def clean(x):
 if isinstance(x,str):
  for a,b in replacements:x=x.replace(a,b)
  return x
 if isinstance(x,list):return [clean(v) for v in x]
 if isinstance(x,dict):return {k:clean(v) for k,v in x.items()}
 return x
f=clean(f)
# Remove session labels and event headlines: the body still contains contemporaneous evidence.
for r in f['records']:
 r['inner'].pop('scene',None)
# Replace two separate-person deferral events with repeated deferrals involving the same friend.
# Preserve the distinction: social fear vs actual schedule consideration.
r=f['records'][9]
def rep_tree(x,pairs):
 if isinstance(x,str):
  for a,b in pairs:x=x.replace(a,b)
  return x
 if isinstance(x,dict):return {k:rep_tree(v,pairs) for k,v in x.items()}
 return x
r=rep_tree(r,[('팀장','친구'),('주말 근무','주말 모임 준비 도움'),('업무 평가','친구 관계'),('위계의 압박','관계를 유지해야 한다는 압박'),('평가에 불이익','관계에 불이익')])
r['counselor_utterance']='친구가 주말 모임 준비를 도와달라고 했을 때는요?'
r['inner']['appraisal']['relevant_evidence']='즉시 거절하면 친구가 실망하거나 관계가 서먹해질까 봐 시간을 벌었다.'
r['inner']['response_plan']['reason']=r['inner']['appraisal']['relevant_evidence']
f['records'][9]=r
r=f['records'][24]
r=rep_tree(r,[('팀장','친구'),('주말 근무','주말 모임 준비 도움'),('근무를','준비를'),('실제 평가','친구 관계'),('가족 일정','다른 약속'),('업무','모임')])
r['counselor_utterance']='준비를 돕는 일은 실제로 하셨나요?'
f['records'][24]=r
r=f['records'][28]
r=rep_tree(r,[('어머니','친구'),('동창 모임','독서 모임'),('오랜 사람들','다른 참가자들'),('위계 앞에서','부탁을 받았을 때')])
r['counselor_utterance']='그 친구가 나중에 독서 모임에 다시 오라고 했을 때는요?'
r['inner']['appraisal']['interpretation']='친구가 나중에 다시 권한 독서 모임에는 관심이 있지만 이번 일정은 확인해야 한다. 전에 준비를 도와달라는 부탁을 받았을 때와 공개적으로 쓴 말은 같다. 지금은 어떤 대답이 관계에 안전한지보다 실제 가능한 날짜를 확인하려는 마음이 중심이다.'
f['records'][28]=r
# Rebuild public history after edits, with no record IDs exposed in dialogue.
f['public_dialogue']=[]
for r in f['records']:
 f['public_dialogue'] += [{'role':'counselor','content':r['counselor_utterance']},{'role':'client','content':r['utterance']}]
(ROOT/'records.json').write_text(json.dumps(f,ensure_ascii=False,indent=2))
lines=['# 장문 인공 기록 30턴 — 문맥 기반 고난도 선별','','직접 작성한 이전 장문 자료를 바탕으로 쉬운 고유 표식·회기 표제·scene 필드를 제거했다. 일부 유보 장면은 같은 친구와 반복해서 대화한 사건으로 바꾸었다. 발화 당시 원본과 후일 회고를 구별해야 한다. 모델로 상담 대화나 기록을 생성한 자료가 아니다.','']
for r in f['records']:
 lines += [f"## 기록 {r['turn']}",'',f"상담사: {r['counselor_utterance']}",'',f"내담자: {r['utterance']}",'']
 for section,fields in r['inner'].items():
  lines += [f'### {section}','']
  for name,value in fields.items():lines += [f'**{name}**: {value}','']
(ROOT/'RECORDS.md').write_text('\n'.join(lines))
# Recent public clarification resolves references without revealing hidden motives or record IDs.
# The final question is deliberately underspecified when detached from this context.
data=[
('context_initial',1,
 [('counselor','친구의 초대에 답한 이야기나, 제가 부탁을 받았다고 가정하고 대사를 연습해보자고 했던 장면은 지금 제외할게요. 여기서 제가 먼저 연락을 권했던 대답을 돌아보고 싶어요.'),('client','네. 제가 시간을 직접 골라보겠다고 말하기 전의 대답을 말씀하시는 거죠?')],
 '맞아요. 그때 짧게 하겠다고 답한 순간에는 어떤 마음이었는지 확인해볼까요?', [3],
 '현재 공개 문맥이 실제 상담사의 최초 연락 권유로 범위를 한정한다. 친구 초대2, 역할극5, 현재 자발적 의향14, 회고27은 대상 발화 원본이 아니다.'),
('context_choice',1,
 [('counselor','결과를 말한 순간과 결정을 내린 순간을 나눠볼게요. 메시지를 이미 보냈다고 하거나 답장을 읽었다고 말한 장면은 지금 묻는 게 아니에요.'),('client','안 해도 된다고 하셔서, 제가 방법을 정해보겠다고 했던 대답인가요?')],
 '네. 그 말을 꺼낸 바로 그때의 마음을 확인하고 싶어요.', [14],
 '현재 문맥의 안 해도 된다는 조건과 직접 방법을 정한 대답이 원본14를 특정한다. 실행 보고17이나 답장21, 과거 회고27로 대체하지 않는다.'),
('context_unknown',1,
 [('counselor','같은 경험을 제가 나중에도 다시 여쭤봤고, 기록에 적었는지도 이야기했죠. 이번에는 나중에 한 설명은 빼고, 제가 처음 이유를 물었던 대답만 돌아볼게요.'),('client','갑자기 내렸던 일에 대해 저도 모르겠다고 했던 거요?')],
 '맞아요. 그때는 이유를 알고도 보류한 건지, 정말 설명이 안 됐던 건지 확인해볼까요?', [9],
 '처음 갑자기 내린 이유를 물었을 때의 원본9. 반복 질문16의 모르겠다는 말은 후속 기록이다. 정보 부재 자체를 기록한 원본을 선택한다.'),
('context_limited_share',1,
 [('counselor','혼자 쓰겠다고만 했던 장면이나 전체를 보내기는 싫다고 답했던 장면과 나눠서 볼게요. 실제로 여기서 일부를 읽는 방식을 제안하셨던 순간이요.'),('client','제가 읽을 부분을 골라서 말하겠다고 했던 때요?')],
 '네. 그때 정확히 어디까지 괜찮다고 생각했던 건지 확인하고 싶어요.', [22],
 '직접 한 문장을 고르겠다는 원본22가 대상이다. 작성과 공개를 구분한11, 개인앱 기록18, 전체 공유 거절28은 다른 시점이다.'),
('context_permission_pair',2,
 [('counselor','그 사건을 보류한 대답 두 개를 비교하려고 해요. 관련된 사람에게 물어보기 전의 대답과 그 사람의 답을 읽고도 자세히 말하기 어렵다고 했던 대답이에요.'),('client','멈추는 방법을 정하자고 하기 전까지의 두 장면이네요.')],
 '네. 두 번 모두 아직 어렵다고 했지만 그 말을 한 이유도 같았는지 확인해볼까요?', [6,19],
 '동료 허락 전6과 허락 후19. 허락을 요청할 계획15, 중단 신호를 수락한23은 두 대상 대답이 아니다.'),
('context_distance_pair',2,
 [('counselor','제가 자리를 옮겨도 되는지 물었을 때의 대답과, 나중에 그때 실제로 어땠는지 다시 물었을 때의 대답을 나눠보려고 해요.'),('client','그때 괜찮다고 해놓고 나중에는 조금 불편했다고 했었죠.')],
 '맞아요. 처음 그렇게 답한 마음과 나중에 정정해서 말한 마음을 각각 확인해볼까요?', [12,24],
 '거리 허락 당시12와 그 순간을 나중에 정정한24. 현재 다른 사건의 의례적 동의나 그 사건에 대한 하나의 회고만으로 대체하지 않는다.'),
('context_same_friend',2,
 [('counselor','같은 친구에게 생각해보겠다고 했던 대답을 비교할게요. 한 번은 준비 일을 도와달라는 부탁이었고, 다른 한 번은 나중에 독서 모임에 다시 오라는 권유였어요.'),('client','두 번 다 바로 네라고 하지는 않았어요.')],
 '그 두 번의 유보에는 각각 어떤 마음이 있었는지 확인해볼까요?', [10,29],
 '친구의 도움 부탁 당시10과 다시 초대받은 모임의 실제 일정 검토29가 원본. 최종 거절25나 최초 독서 모임 초대2는 제외한다.'),
('context_same_words',2,
 [('counselor','같이 해보겠다고 한 대답 중, 제가 이 자리에서 먼저 연락을 권했을 때와 나중에 그 친구와 산책 약속을 이야기했을 때만 비교할게요.'),('client','표현은 똑같이 했던 것 같아요.')],
 '네. 그 두 대답이 당시 같은 의미였는지 각각의 마음을 확인해볼까요?', [3,26],
 '첫 연락 과제3과 답장 이후 새 산책 의향26을 함께 찾아야 한다. 최초 모임 초대2나 방법 선택14, 회고27은 두 대상 발화가 아니다.'),
('context_scope_chain',3,
 [('counselor','읽어주는 범위가 바뀐 세 대답을 순서대로 보려고 해요. 쓰기는 하지만 보여주기는 아직이라고 했던 대답, 직접 일부를 골라 읽겠다고 했던 대답, 그 뒤 전체를 보내는 건 싫다고 했던 대답이에요.'),('client','앱에 적었다고 말한 순간은 빼고, 보여주는 범위를 제가 말한 때들이군요.')],
 '맞아요. 그 세 번에 각각 무엇을 허용하고 무엇을 남겨두려 했는지 확인해볼까요?', [11,22,28],
 '작성/공개 분리11, 선택한 문장 공개22, 전체 공개 거절28. 앱 보관18과 포털 업로드13은 별도 사건이다.'),
('context_contact_chain',3,
 [('counselor','연락 이야기는 처음 여기서 해보겠다고 대답한 순간, 나중에 제가 안 해도 된다고 하자 직접 방법을 정하겠다고 한 순간, 실제로 보냈지만 답은 아직 없다고 보고한 순간을 나눠볼게요.'),('client','상대에게 답장이 온 뒤의 대답이나 나중에 과거를 돌아본 설명은 아니고요.')],
 '네. 그 세 순간에 각각 어떤 마음으로 말했는지 확인해볼까요?', [3,14,17],
 '최초 동의3, 자율적 방법 선택14, 실행 직후 보고17. 답장 뒤 안도21와 회고27은 대상 밖이다.'),
('context_disclosure_chain',3,
 [('counselor','그 일을 자세히 말하기 어렵다고 했던 대답은 주변 사람에게 물어보기 전과 답을 받은 뒤에 있었죠. 그 다음에는 중단하는 방법을 정하고 조금 말해보겠다고 하셨고요.'),('client','먼저 물어볼 수 있다고만 했던 대답은 중간 계획이었고요.')],
 '네. 계획만 말한 때는 빼고, 말씀드린 세 번의 대답에 담긴 마음을 각각 확인해볼까요?', [6,19,23],
 '동료 보호6, 허락 뒤 감정 조절 우려19, 중단 조건 이후 의향23. 허락 문의 계획15나 다른 가족 이야기8을 골라서는 안 된다.'),
('context_agreement_roles',3,
 [('counselor','똑같이 해보겠다고 한 세 장면만 볼게요. 상담 전에 친구가 책 모임에 초대한 일, 여기서 제가 먼저 연락하자고 권한 일, 그리고 부탁을 받은 상황을 가정해서 대사를 연습한 일이에요.'),('client','나중에 산책해보겠다고 한 약속은 여기에 넣지 않는 거죠?')],
 '네. 그 세 번은 각각 어떤 의도로 한 말이었는지 확인해볼까요?', [2,3,5],
 '실제 초대의 진심2, 상담사 권유에 형식적 동의3, 가상 역할극5. 새로운 산책26이나 후일 회고27은 대상 아님.')
]
questions=[];gold=[]
for id,n,ctx,q,ids,reason in data:
 assert n==len(ids) and len(set(ids))==n
 questions.append({'id':id,'target_count':n,'recent_context':[{'role':role,'content':content} for role,content in ctx],'question':q})
 gold.append({'id':id,'core':ids,'rationale':reason})
(ROOT/'questions.json').write_text(json.dumps(questions,ensure_ascii=False,indent=2))
(ROOT/'gold.json').write_text(json.dumps(gold,ensure_ascii=False,indent=2))
(ROOT/'QUESTIONS.md').write_text('# 공개 질문과 직전 문맥\n\n정답 수 N만 양쪽 방법에 제공한다. 기록 ID는 제공하지 않는다. 마지막 질문만 보면 모호하지만 앞선 공개 문맥을 함께 읽으면 대상 발화가 구별된다.\n\n'+'\n\n'.join(f"## {q['id']} — N={q['target_count']}\n\n"+'\n\n'.join(x['role']+': '+x['content'] for x in q['recent_context'])+'\n\n상담사: '+q['question'] for q in questions))
(ROOT/'GOLD.md').write_text('# 실행 전 고정한 원본 ID 정답 — 모델 입력에서 제외\n\n이 실험은 대상 발화 당시 원본을 찾는 source selection 과제다. 후일 회고만으로 답을 추론할 수 있어도 지정된 당시 원본 대신 선택하면 오답이다. 원본이 아닌 보조 기록을 추가로 선택할 여유 없이 N개만 반환한다. N=0 질문은 제외한다. 같은 분석자가 자료와 정답을 작성했으며 독립 정답 검증을 받지 않았다.\n\n'+'\n\n'.join(f"## {g['id']}\n\n정답: {g['core']}\n\n근거: {g['rationale']}" for g in gold))
stats={'count':30,'mean_inner_chars':sum(len(json.dumps(r['inner'],ensure_ascii=False)) for r in f['records'])/30,'min_inner_chars':min(len(json.dumps(r['inner'],ensure_ascii=False)) for r in f['records']),'max_inner_chars':max(len(json.dumps(r['inner'],ensure_ascii=False)) for r in f['records']),'questions':12,'n_distribution':{str(n):sum(q['target_count']==n for q in questions) for n in [1,2,3]}}
(ROOT/'fixture_stats.json').write_text(json.dumps(stats,indent=2));print(json.dumps(stats))
