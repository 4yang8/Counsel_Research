"""Independent therapist state, returned as the paired session's atomic checkpoint."""
import copy
import json
from pathlib import Path
from client.paper_baselines import common, calmit
from runtime.cancellation import Cancelled

HERE = Path(__file__).resolve().parent


def specification():
    return {'id':'Q3calmit_counsel','label':'Q3calmit_counsel · CALM-IT 상담사','view':'none',
            'paper_baseline':True,
            'description':'공개 대화 추정→정체 감지→MI 전략/전환→후보 3개→대체 선택기. 원본 ranker 미포함.',
            'system_prompt':(HERE/'system.md').read_text(),
            'subagent_prompts':[]}


def run(config, session, backend, build, emit, cancelled, directory=None):
    settings = session['settings']
    # Only public conversation is supplied; never forward session.settings as LLM input.
    messages = [{'role':'user' if row['role']=='counselor' else 'assistant','content':row['content']}
                for row in session['public_dialogue']]
    frozen = settings.get('paper_counselor')
    if not frozen:
        raise ValueError('CALM-IT 상담사 설정이 없습니다. 새 세션을 시작하세요.')
    options, prompts = frozen['options'], frozen['prompts']
    request = {'experiment_version':'Q3calmit_counsel','generation':settings.get('generation',{}),
               'system_prompt':settings.get('counselor_system_prompt','')}
    call = common.StageCalls(config,request,build,backend.upstream,backend.stream_events,
                            backend.inspect_prompt,emit,cancelled)
    checkpoint = copy.deepcopy(session.get('counselor_checkpoint') or {})
    if checkpoint.get('terminated'):
        raise ValueError('종료된 CALM-IT 상담사 세션입니다.')
    utterance,state = calmit.counselor(call,prompts,options,messages,checkpoint)
    if cancelled():
        raise Cancelled('상담사 상태 확정 전에 중단되었습니다.')
    state['provenance'] = frozen['provenance']
    state['last_stages'] = call.trace
    emit('version_event',version='Q3calmit_counsel',section='paper_state',action='staged',
         data={'state':state,'provenance':frozen['provenance']})
    return utterance,state,call.usage


def freeze(config, request):
    body = {'experiment_version':'Q3calmit','paper_options':{
        k:v for k,v in request.get('paper_options',{}).items() if k!='profile'}}
    common.configure(config,body)
    body['paper_provenance']['version'] = 'Q3calmit_counsel'
    body['paper_provenance']['profile_source'] = '공개 대화만으로 추정'
    request['paper_counselor'] = {'options':body['paper_options'],'prompts':body['paper_prompts'],
                                'provenance':body['paper_provenance']}


def view_state(version_directory):
    path = Path(version_directory)/'session.json'
    state = json.loads(path.read_text()).get('counselor_checkpoint') if path.exists() else None
    return {'notes':{'current':{'content':json.dumps(state or {},ensure_ascii=False,indent=2),'path':str(path)}}}
