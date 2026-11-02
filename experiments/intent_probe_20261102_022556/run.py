"""Standalone, controlled component probe. Not an end-to-end Q3p6 run."""
import hashlib
import json
from pathlib import Path
import statistics
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'http://192.168.0.91:8002/v1/chat/completions'
MODEL = 'Qwen/Qwen3.8-Flash-Next-FP8'
SYSTEM = '''당신은 제공된 프로필의 가상 내담자입니다. 공개 대화에 이어 상담사에게 말할 발화 하나를 작성하세요.
프로필, 공개 대화, 제공된 노트를 참고하세요. 발화 당시 기록이 추가로 제공되면 그것도 참고할 수 있습니다.
상담에 협조하거나 거절하는 특정 결과를 목표로 삼지 마세요. 현재 상황에 따라 망설임·선택·변화가 모두 가능합니다. 제공되지 않은 구체적 경험을 사실로 만들지 마세요.
별도의 분석이나 계획은 출력하지 말고 상담사에게 공개할 실제 발화만 JSON의 utterance에 출력하세요.'''
SCHEMA = {'type':'object','properties':{'utterance':{'type':'string'}},'required':['utterance'],'additionalProperties':False}

def save(name, obj):
    p = ROOT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    tmp.replace(p)

def pair(c, u):
    return [{'role':'counselor','content':c},{'role':'client','content':u}]

cases = [
 {'id':'case_01','name':'같은 동의: 실행 의향 / 거절 부담',
  'profile':{'age':25,'occupation':'대학원생','presenting_problem':'연구 미팅 이후 그날의 대화를 반복해서 생각해 잠들기 늦어지는 일이 있다.'},
  'public_dialogue':pair('오늘 어떤 이야기를 해보고 싶으세요?','연구실 미팅에서 한 말이 밤에도 자꾸 떠올라요.')
      +pair('이번 주에 잠들기 전에 떠오른 생각을 한 줄씩 적어보는 건 어떨까요?','네, 이번 주에 해볼게요.')
      +pair('평소 하루 일정은 어떻게 되나요?','낮에는 연구실에 있고 저녁에는 집으로 와요.')
      +pair('하루 중 가장 피곤하게 느껴지는 때는 언제인가요?','집에 돌아와 씻고 나면 피곤한 게 느껴져요.'),
  'target_turn':2,'target_quote':'네, 이번 주에 해볼게요.',
  'worlds':[
   {'id':'w1','private':'그때는 한 줄 기록이 반복되는 생각을 정리하는 데 도움이 될 것 같아 실제로 해보고 싶었다. 상담사에게 잘 보이려고 동의한 것은 아니었다. 아직 실행한 것은 아니지만 작은 시도라 해볼 마음이 있었다.',
    'meaning':'도움에 대한 기대와 실제 시도 의향 때문에 동의'},
   {'id':'w2','private':'그때는 기록을 해볼 마음이 없었다. 이미 머릿속에서 같은 생각을 반복하므로 또 기록하는 것은 부담스럽게 느꼈다. 하지만 처음 만난 상담사의 제안을 바로 거절하면 비협조적으로 보일까 걱정돼 동의하는 말만 했다.',
    'meaning':'실행 의향 없이 거절 부담 때문에 형식적으로 동의'}]},
 {'id':'case_02','name':'같은 보류: 타인 정보 보호 / 자신에 대한 평가 우려',
  'profile':{'age':29,'occupation':'회사원','presenting_problem':'팀 프로젝트에서 동료와 갈등을 겪은 뒤 마음이 복잡해 상담을 시작했다.'},
  'public_dialogue':pair('어떤 일로 상담을 시작하게 되셨나요?','팀 프로젝트에서 동료와 부딪친 일이 마음에 남아요.')
      +pair('그날 동료와 어떤 일이 있었는지 조금 이야기해주실 수 있나요?','아직 그건 말할 준비가 안 됐어요.')
      +pair('지금은 자세히 이야기하지 않으셔도 됩니다. 요즘 퇴근 뒤에는 어떻게 지내시나요?','집에 와서 저녁을 먹고 쉬어요.')
      +pair('잠은 대체로 어떠세요?','잠드는 시간이 전보다 조금 늦어졌어요.'),
  'target_turn':2,'target_quote':'아직 그건 말할 준비가 안 됐어요.',
  'worlds':[
   {'id':'w1','private':'그때는 사건을 자세히 말하면 동료가 개인적으로 알려준 사적인 사정까지 드러날까 봐 보류했다. 그 동료의 허락 없이 그 정보를 말하고 싶지 않았다. 상담사가 나를 비난할까 봐 숨긴 것은 아니었다.',
    'meaning':'동료의 사적인 정보를 보호하려고 보류'},
   {'id':'w2','private':'그때는 갈등 과정에서 나도 날카로운 말을 한 일이 떠올랐다. 그걸 말하면 상담사가 나를 잘못한 사람으로 판단할까 봐 걱정돼 구체적인 이야기를 보류했다. 동료의 사적인 정보를 보호하려던 것은 아니었다.',
    'meaning':'자신의 행동에 대한 상담사의 판단이 두려워 보류'}]}
]
for case in cases:
    case['question'] = f'앞서 “{case["target_quote"]}”라고 말씀하셨는데, 그때 어떤 생각으로 그렇게 말씀하셨나요?'
    case['public_notes'] = '\n'.join('- '+m['content'] for m in case['public_dialogue'] if m['role']=='client')
    case['unchanged_context'] = '위 대화 사이에 추가 행동이나 외부 사건이 일어났다는 설정은 없다.'
save('cases.json', cases)
plan = []
for case in cases:
    for repeat in (1,2):
        seed = 41000 + 100 * int(case['id'][-2:]) + repeat
        items = [('C_public',None)]
        for w in case['worlds']:
            items += [(cond,w) for cond in (('A_notes','B_record') if repeat==1 else ('B_record','A_notes'))]
        for cond, world in items:
            source = {'profile':case['profile'],'public_dialogue':case['public_dialogue']+[{'role':'counselor','content':case['question']}],
                      'notes':case['public_notes'],'context':case['unchanged_context']}
            if world:
                source['notes'] += '\n- '+world['private']
            if cond=='B_record':
                source['utterance_time_records']=[{'turn':case['target_turn'],'utterance':case['target_quote'],'private_intent':world['private']}]
            uid=f'{case["id"]}_{world["id"] if world else "shared"}_{cond}_r{repeat}'
            request={'model':MODEL,'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(source,ensure_ascii=False)}],
                     'temperature':0.7,'top_p':0.95,'max_tokens':1024,'seed':seed,'stream':False,
                     'chat_template_kwargs':{'enable_thinking':False},'structured_outputs':{'json':SCHEMA}}
            plan.append({'id':uid,'case':case['id'],'world':world['id'] if world else None,'condition':cond,'repeat':repeat,'request':request})
assert len(plan)==20
manifest={'tier':'component_probe','not_full_q3p6':True,'endpoint':ENDPOINT,'model':MODEL,'total_calls':len(plan),
 'hypothesis':'公開情報から過去の非公開理由を特定できない条件で、理由を含むノートが応答の整合性に与える影響を探索する。',
 'ground_truth':'Researcher-authored hypothetical intentions, not model-generated history or human ground truth.',
 'conditions':{'C_public':'Public dialogue and public note only; one shared response for both hidden worlds.',
               'A_notes':'Same prompt/generation + historical private reason in note.',
               'B_record':'A_notes + same private reason repeated in utterance-linked record; not an information-matched linkage ablation.'},
 'comparison_limits':['Diagnostic fixture, not a natural dialogue occurrence rate.','C has insufficient information to uniquely identify the hidden world; uncertainty is not counted as a wrong memory.','B versus A also changes redundancy and input length.','Two repeats per situation; no statistical significance claim.','No downstream counseling progress measurement.'],
 'evaluation_predeclared':{'consistent':'Expresses reason compatible with authored intent; withholding or uncertainty separately marked.','contradiction':'Confidently asserts a motive ruled out by the authored intent.','underdetermined':'No distinguishing motive or explicit uncertainty; not a contradiction.'},
 'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
 'plan':[{'id':x['id'],'case':x['case'],'world':x['world'],'condition':x['condition'],'repeat':x['repeat'],'request_sha256':hashlib.sha256(json.dumps(x['request'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()} for x in plan]}
save('manifest.json',manifest)
started=time.time(); durations=[]; results=[]
for i,item in enumerate(plan):
    name=item['id']; folder=ROOT/name
    if folder.exists(): raise RuntimeError('Do not overwrite prior run: '+name)
    save(name+'/request.json',item['request'])
    status={'state':'running','completed':i,'total':len(plan),'percent':round(100*i/len(plan)), 'active':name,'elapsed_seconds':round(time.time()-started,1)}
    save('progress.json',status); print(json.dumps(status),flush=True)
    before=time.time()
    try:
        req=urllib.request.Request(ENDPOINT,data=json.dumps(item['request']).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=180) as response:
            raw=response.read().decode()
        (folder/'response.raw.json').write_text(raw)
        response=json.loads(raw); choice=response['choices'][0]
        if choice['finish_reason']!='stop':raise RuntimeError('Incomplete response: '+str(choice['finish_reason']))
        value=json.loads(choice['message']['content'])
        if not isinstance(value.get('utterance'),str) or not value['utterance'].strip():raise RuntimeError('Missing utterance')
        seconds=time.time()-before;durations.append(seconds)
        result={k:item[k] for k in ('id','case','world','condition','repeat')}
        result.update(utterance=value['utterance'],seconds=round(seconds,2),usage=response.get('usage'),finish_reason=choice['finish_reason'])
        save(name+'/result.json',result);results.append(result);save('results.json',results)
    except Exception as error:
        extra=error.read().decode() if isinstance(error,urllib.error.HTTPError) else ''
        save(name+'/error.json',{'error':str(error),'body':extra})
        save('progress.json',{**status,'state':'error','error':str(error)})
        raise
    status={'state':'completed' if i+1==len(plan) else 'running','completed':i+1,'total':len(plan),'percent':round(100*(i+1)/len(plan)),
            'elapsed_seconds':round(time.time()-started,1),'estimated_remaining_seconds':round(statistics.median(durations)*(len(plan)-i-1),1)}
    save('progress.json',status);print(json.dumps(status),flush=True)
