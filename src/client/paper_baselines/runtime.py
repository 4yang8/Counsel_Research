"""Registry adapters for three independent paper pipelines."""
import copy
from pathlib import Path
from . import common, cars, patientact, calmit
from runtime import reasoning_budget

DESCRIPTIONS = {
    'Q3cars':'CARS · CCD 반응 생성. 프로젝트 프로필은 session CCD로 변환합니다. 원본 감정 예문·사후 절단은 미포함인 근사 재현입니다.',
    'Q3patientact':'PatientAct · 공식 PatientHub 프로필 40개와 태그 검색→신뢰도 제한→반응→행동→발화→신뢰 갱신. 원논문 모델·점수 재현은 별도입니다.',
    'Q3calmit':'CALM-IT · 상태 갱신→후보 3개→대체 선택기. 원본 likelihood ranker가 없는 근사 재현입니다. Q3calmit_counsel과 함께 선택하세요.'}


class PaperRuntime:
    TOOLS = []
    TOOL = None
    LOCK = common.LOCK

    def __init__(self, version):
        self.version = version
        self.HERE = Path(__file__).resolve().parent.parent/version

    def specification(self):
        return {'id':self.version,'label':self.version+' · 논문 비교','description':DESCRIPTIONS[self.version],
                'view':'none','paper_baseline':True,'system_prompt':(self.HERE/'system.md').read_text(),
                'summary_system_prompt':'','tools':[],'required_tools':[],
                'subagent_prompts':[{'kind':key,'label':key} for key in common.bundle(self.version)],
                'paper_profiles': [{'index':i,'label':str(i)+' · '+row['profile']['demographics']['name']+
                    ' · '+str(row.get('disease_key',''))} for i,row in enumerate(patientact.characters())]
                    if self.version=='Q3patientact' else []}

    def load(self, config, session):
        saved = common.load(config,self.version,session)
        return {'content':common.encoded(saved['state']), 'records':saved['records'],
                'path':str(common.directory(config,self.version,session)) if session else None}

    def build(self, config, body, runtime, base_build, preview=False):
        ctx = common.context(config,body,runtime,preview)
        options = body['paper_options']
        if self.version=='Q3patientact':
            patientact.character({'profile':body['paper_native_profile'],'profile_index':options['profile_index']})
            common.render(body['paper_prompts'],'patientact_sys_prompt',profile=body['paper_native_profile']['profile'])
        elif 'profile' in options:
            (cars if self.version=='Q3cars' else calmit).validate_profile(options['profile'])
        # A setup preview is not falsely presented as the final adaptive request.
        request = {'system_prompt':body.get('system_prompt',self.specification()['system_prompt']),
                   'messages':[{'role':'user','content':common.encoded({
                       'preview':'다단계 실행의 초기 설정입니다. 실제 단계별 요청은 생성 과정 로그에서 확인하세요.',
                       'provenance':body['paper_provenance'],'options':options,
                       'source_profile':ctx['scenario']['profile'] if self.version!='Q3patientact' else
                           body['paper_native_profile'],
                       'public_messages':ctx['messages'],'prior_state':ctx['saved']['state']})}],
                   'generation':body.get('generation',config.get('generation',{}))}
        return base_build(config,request,[],preview)

    def execute(self, config, payload, client, upstream, frames, inspect, emit, cancelled, original, base_build, mode, runtime):
        body = config['_request_body']
        ctx = common.context(config,body,runtime)
        calls = common.StageCalls(config,body,reasoning_budget.wrap_builder(base_build,body.get('client_reasoning_budgets')),
                                  upstream,frames,inspect,emit,cancelled)
        options, prompts = body['paper_options'], body['paper_prompts']
        state = copy.deepcopy(ctx['saved']['state'])
        emit('version_event',version=self.version,section='paper_provenance',action='snapshot',
             data=body['paper_provenance'])
        if self.version=='Q3patientact':
            native = copy.deepcopy(body['paper_native_profile'])
            utterance,state = patientact.generate(calls,prompts,options,native,ctx['messages'],state)
        else:
            native = state.get('native_profile') or options.get('profile')
            if native is None:
                native = cars.prepare(calls,prompts,ctx['scenario']['profile'],ctx['messages']) if self.version=='Q3cars' else calmit.profile(ctx['scenario']['profile'])
            module = cars if self.version=='Q3cars' else calmit
            native = module.validate_profile(native)
            utterance,state = module.generate(calls,prompts,options,native,ctx['messages'],state)
            state['native_profile'] = native
        common.commit(config,body,ctx,state,utterance,calls)


RUNTIMES = {v:PaperRuntime(v) for v in common.VERSIONS}
