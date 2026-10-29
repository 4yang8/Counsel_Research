'use strict';
// Public speech on the right; one selected, time-bounded execution on the left.
(() => {
  const $=id=>document.getElementById(id),node=(tag,cls='',text='')=>Object.assign(document.createElement(tag),{className:cls,textContent:text});
  const shell=document.querySelector('.lab-shell'),left=document.querySelector('.results-scroll'),right=document.querySelector('.conversation-column');
  const pretty=value=>JSON.stringify(value,null,2),names={client:'내담자',counselor:'상담사'};
  let counselorSpecs=[],counselorDrafts={},previousCounselor='basic',counselorReady=Promise.resolve();
  let session=null,running=false,auto=false,pause=false,halt=false,remaining=0,requestId=null,abort=null,paint=null,restoring=false,sending=false;
  let followTranscript=true,activationFollow=true;
  const transcriptEntries=new Map();
  const attempts=[],selection={attempt:null,index:null,activationIndex:null,initial:null,manual:null};
  const toolbar=node('section','paired-controls');toolbar.setAttribute('aria-label','양방향 대화 실행');
  toolbar.innerHTML='<div class="paired-actions"><button id="paired-next">다음 발화 생성</button><button id="paired-auto" hidden>자동 시작</button><button id="paired-pause" disabled hidden>일시정지</button><button id="paired-stop" disabled hidden>현재 생성 중단</button></div><p id="paired-status" role="status">설정 후 내담자부터 생성합니다.</p>';
  const options=node('div','paired-composer-options');options.innerHTML='<span class="manual-options" role="group" aria-label="직접 입력할 역할"><span>직접 입력</span><label><input id="manual-client" type="checkbox">내담자</label><label><input id="manual-counselor" type="checkbox">상담사</label></span><label class="model-switch" title="켜면 직접 입력 차례 또는 설정한 발화 수까지 자동 생성합니다."><input id="paired-mode" type="checkbox" role="switch" aria-label="모델 자동 실행"><span class="switch-track" aria-hidden="true"></span>자동 실행</label><label id="paired-limit-setting" title="한 번에 자동 생성할 최대 발화 수">최대<input id="paired-limit" type="number" value="6" min="1" max="100" aria-label="자동 생성 발화 수"></label>';
  toolbar.prepend(options);
  const endpointControls=node('div','turn-endpoint');
  endpointControls.innerHTML='<label>vLLM <select id="turn-endpoint-mode" aria-label="이번 턴 vLLM 주소"><option value="local">127.0.0.1</option><option value="lan">192.168.0.n</option></select></label><label id="turn-endpoint-octet-label" hidden>n <input id="turn-endpoint-octet" type="number" min="0" max="255" step="1" placeholder="n" aria-label="192.168.0.n의 n"></label><label>포트 <input id="turn-endpoint-port" type="number" min="1" max="65535" step="1" value="18001" aria-label="vLLM 포트"></label><small id="turn-endpoint-preview" aria-live="polite"></small>';
  toolbar.append(endpointControls);
  const endpointField=id=>endpointControls.querySelector('#'+id);
  const endpointPorts={local:'18001',lan:'8001'};let endpointMode='local',endpointFallback=null,endpointTouched=false;
  function endpointValue(){
    if(endpointFallback)return endpointFallback;
    const port=Number(endpointField('turn-endpoint-port').value),octet=Number(endpointField('turn-endpoint-octet').value);
    if(!endpointField('turn-endpoint-port').value.trim() || !Number.isInteger(port) || port<1 || port>65535)throw Error('vLLM 포트는 1~65535 정수로 입력하세요.');
    if(endpointMode==='lan' && (!endpointField('turn-endpoint-octet').value.trim() || !Number.isInteger(octet) || octet<0 || octet>255))throw Error('192.168.0.n의 n은 0~255 정수로 입력하세요.');
    return 'http://'+(endpointMode==='local'?'127.0.0.1':'192.168.0.'+octet)+':'+port+'/v1';
  }
  function endpointPreview(){
    endpointField('turn-endpoint-octet-label').hidden=endpointMode!=='lan';
    endpointField('turn-endpoint-port').disabled=running || restoring || sending || !!endpointFallback;
    try{const url=endpointValue();endpointField('turn-endpoint-preview').textContent=url+' · 다음 생성부터';$('endpoint').textContent=url;}catch(error){endpointField('turn-endpoint-preview').textContent=error.message;}
  }
  function restoreEndpoint(value){
    if(!value)return;
    endpointFallback=null;
    try{
      const url=new URL(value.includes('://')?value:'http://'+value),match=/^192\.168\.0\.(\d{1,3})$/.exec(url.hostname);
      if(url.protocol!=='http:' || !['','/','/v1','/v1/'].includes(url.pathname) || (!match && url.hostname!=='127.0.0.1')){
        endpointFallback=value;
        let option=endpointField('turn-endpoint-mode').querySelector('[value="saved"]');
        if(!option){option=new Option('저장된 주소','saved');endpointField('turn-endpoint-mode').append(option);}
        endpointMode='saved';
      }else{
        endpointMode=match?'lan':'local';if(match)endpointField('turn-endpoint-octet').value=match[1];
        endpointPorts[endpointMode]=url.port || '80';endpointField('turn-endpoint-port').value=endpointPorts[endpointMode];
      }
      endpointField('turn-endpoint-mode').value=endpointMode;endpointPreview();
    }catch{endpointFallback=value;endpointPreview();}
  }
  endpointField('turn-endpoint-mode').onchange=()=>{
    endpointTouched=true;if(endpointMode!=='saved')endpointPorts[endpointMode]=endpointField('turn-endpoint-port').value;
    endpointMode=endpointField('turn-endpoint-mode').value;
    if(endpointMode!=='saved'){endpointFallback=null;endpointField('turn-endpoint-mode').querySelector('[value="saved"]')?.remove();endpointField('turn-endpoint-port').value=endpointPorts[endpointMode];}
    endpointPreview();
  };
  for(const id of ['turn-endpoint-octet','turn-endpoint-port'])endpointField(id).oninput=()=>{endpointTouched=true;endpointPreview();};
  window.TurnConnection={value:endpointValue,restore:restoreEndpoint};

  const chosenModes=()=>({client:$('manual-client').checked?'manual':'model',counselor:$('manual-counselor').checked?'manual':'model'});
  const modes=()=>session?.speaker_modes || chosenModes();
  const nextActor=()=>session?.next_actor || 'client';
  const manualTurn=()=>modes()[nextActor()]==='manual';
  function setModes(value){for(const role of ['client','counselor'])$('manual-'+role).checked=value?.[role]==='manual';}
  const retryInput=()=>session?.retry_manual && modes()[session.retry_manual.actor]==='manual'?session.retry_manual:null;
  const inputActor=()=>retryInput()?.actor || nextActor();
  function resizeInput(){const input=$('paired-manual-text');input.style.height='auto';input.style.height=Math.min(132,Math.max(32,input.scrollHeight))+'px';}
  const counselorBlock=node('section','setting-block');counselorBlock.id='block-counselor';
  counselorBlock.innerHTML='<div class="block-heading"><h3>상담사 설정</h3><span id="counselor-block-badge" class="block-badge">단일 프롬프트</span></div><label>상담사 버전<select id="counselor-version"><option value="basic">기본 상담사 · 단일 프롬프트</option><option value="C4">C4 · 관찰 / 선택 / 발화</option></select></label><label id="basic-counselor-settings">기본 상담사 System prompt<textarea id="counselor-system-prompt" rows="7"></textarea></label><p id="counselor-mode-description" class="hint"></p><details id="c4-settings" hidden><summary>C4 원본 설정 / 프롬프트 소스</summary><p id="c4-source" class="hint">C4 설정 읽는 중…</p><pre id="c4-source-json"></pre></details><p class="hint">공개 발화만 공유합니다. 연결과 기본 생성 옵션은 공통 설정을 사용하며, C4는 원본의 단계별 옵션·검증을 사용합니다.</p>';
  $('settings-counselor-panel').append(counselorBlock);
  const budgetSettings=node('fieldset','counselor-budget-settings');budgetSettings.id='counselor-reasoning-budgets';
  budgetSettings.innerHTML='<legend>Reasoning token budget · 추론 토큰 한도</legend><div class="counselor-budget-options"><div class="counselor-budget-option"><label><input id="counselor-main-budget-enabled" type="checkbox">메인</label><input id="counselor-main-budget" type="number" min="1" max="1000000" step="1" value="512" disabled aria-label="상담사 메인 추론 토큰 한도"></div><div id="counselor-subagent-budget-setting" class="counselor-budget-option" hidden><label><input id="counselor-subagent-budget-enabled" type="checkbox">서브에이전트</label><input id="counselor-subagent-budget" type="number" min="1" max="1000000" step="1" value="512" disabled aria-label="상담사 서브에이전트 추론 토큰 한도"></div></div><p class="hint">체크 해제: 한도 미지정 · 상담사 모델 호출마다 적용 · 전체 출력 한도(Max tokens)는 공통 설정을 따릅니다. 새 대화 시작 시 저장됩니다.</p>';
  $('counselor-version').closest('label').after(budgetSettings);
  const promptActions=node('div','counselor-prompt-actions'),restorePrompt=node('button','','기본 지침으로 복원'),promptSource=node('small');
  promptActions.id='counselor-prompt-actions';restorePrompt.id='counselor-prompt-default';restorePrompt.type='button';promptSource.id='counselor-prompt-source';
  promptActions.append(restorePrompt,promptSource);$('basic-counselor-settings').after(promptActions);
  const subagentBlock=node('section','setting-block');subagentBlock.id='block-counselor-subagent';subagentBlock.hidden=true;
  subagentBlock.innerHTML='<div class="block-heading"><h3>서브에이전트 프롬프트</h3></div><label id="counselor-subagent-picker">서브에이전트<select id="counselor-subagent-select"></select></label><label id="counselor-subagent-prompt-label">지침<textarea id="counselor-subagent-prompt" rows="12"></textarea></label><p class="hint">원본 파일을 편집합니다. 저장하면 기존 대화를 포함해 다음 서브에이전트 호출부터 적용됩니다.</p>';
  $('settings-counselor-panel').append(subagentBlock);
  const phase=node('span','settings-phase','생성 전 설정');phase.id='settings-phase';document.querySelector('.dashboard-heading h2').append(phase);
  const chat=node('section','paired-chat');chat.id='paired-chat';chat.setAttribute('aria-label','상담사와 내담자의 공개 대화');
  chat.innerHTML='<div class="paired-chat-heading"><h2>공개 대화</h2><span id="paired-dialogue-count">초기 대화</span></div><p class="paired-chat-hint">발화를 클릭하면 왼쪽에서 그 발화의 생성 과정을 확인합니다.</p><div id="paired-transcript" aria-label="공개 발화 목록"></div><p id="paired-chat-status" role="status"></p>';
  right.prepend(chat);right.setAttribute('aria-label','상담사와 내담자의 공개 대화');
  const chatScroll=$('paired-transcript');
  chatScroll.addEventListener('scroll',()=>{if(chatScroll.clientHeight)followTranscript=chatScroll.scrollHeight-chatScroll.clientHeight-chatScroll.scrollTop<=40;},{passive:true});
  new ResizeObserver(()=>{if(followTranscript && chatScroll.clientHeight)chatScroll.scrollTop=chatScroll.scrollHeight;}).observe(chatScroll);
  const manual=node('form','paired-manual');manual.id='paired-manual';manual.innerHTML='<input id="paired-manual-role" type="hidden"><textarea id="paired-manual-text" rows="1" placeholder="발화를 직접 입력하세요." aria-label="현재 대화에 직접 입력할 발화"></textarea><button id="paired-manual-send" type="submit">전송</button>';
  const composer=node('section','paired-composer');composer.setAttribute('aria-label','발화 방식과 대화 실행 / 직접 입력');composer.append(toolbar,manual);chat.append(composer);
  LabVersionViews.install();
  const nav=node('button','config-tab','대화 / 생성 과정');nav.id='paired-tab';nav.type='button';nav.setAttribute('role','tab');document.querySelector('.config-tabs').prepend(nav);
  const counselorSelect=$('counselor-version');let source='paired';
  const mode=()=>session?.counselor_version || counselorSelect.value;
  const selected=()=>attempts.find(row=>row.id===selection.attempt);
  function syncCounselorBudgets(){
    const locked=running || restoring || sending || !!session;
    $('counselor-subagent-budget-setting').hidden=!counselorSpecs.find(row=>row.id===mode())?.subagent_prompts?.length;
    for(const scope of ['main','subagent']){
      const enabled=$('counselor-'+scope+'-budget-enabled');enabled.disabled=locked;
      $('counselor-'+scope+'-budget').disabled=locked || !enabled.checked;
    }
  }
  function readCounselorBudgets(){
    return Object.fromEntries(['main','subagent'].map(scope=>{
      const input=$('counselor-'+scope+'-budget');
      if(!$('counselor-'+scope+'-budget-enabled').checked || scope==='subagent' && $('counselor-subagent-budget-setting').hidden)return [scope,null];
      const value=Number(input.value);
      if(!input.value.trim() || !Number.isInteger(value) || value<1 || value>1000000)throw Error((scope==='main'?'메인':'서브에이전트')+' 추론 한도는 1~1,000,000 정수로 입력하세요.');
      return [scope,value];
    }));
  }
  function restoreCounselorBudgets(value={}){
    for(const scope of ['main','subagent']){
      const budget=value?.[scope],enabled=Number.isInteger(budget) && budget>=1 && budget<=1000000;
      $('counselor-'+scope+'-budget-enabled').checked=enabled;
      $('counselor-'+scope+'-budget').value=enabled?budget:512;
    }
    syncCounselorBudgets();
  }
  function rememberCounselorDraft(version=previousCounselor){
    if(session)return;
    const value=$('counselor-system-prompt').value,base=counselorSpecs.find(row=>row.id===version)?.system_prompt ?? '';
    if(value===base)delete counselorDrafts[version];else counselorDrafts[version]=value;
  }
  function applyCounselorPrompt(){
    previousCounselor=counselorSelect.value;
    $('counselor-system-prompt').value=counselorDrafts[previousCounselor] ?? counselorSpecs.find(row=>row.id===previousCounselor)?.system_prompt ?? '';
  }
  function refreshCounselorCatalog(initial=false){
    return api('/api/paired/counselor').then(async data=>{
      await versionPreferencesReady;
      const chosen=session?.counselor_version || (initial?versionPreferences.get('counselor',data.versions,data.default):counselorSelect.value);
      counselorSpecs=data.versions;counselorSelect.replaceChildren(...data.versions.map(row=>new Option(row.label,row.id)));
      counselorSelect.value=data.versions.some(row=>row.id===chosen)?chosen:data.default;
      LabVersionViews.setCatalog(data.versions);LabVersionViews.select(counselorSelect.value);
      if(!session)applyCounselorPrompt();
      $('c4-source').textContent='C4 · 관찰 / 행동 선택 / 발화 · '+data.language+' 프롬프트';$('c4-source-json').textContent=pretty(data);controls();
    });
  }
  function updateSummary(){
    const sourceModes=modes();
    toolbar.title=`${session?.connection?.model || $('model-select').value} · 내담자 ${sourceModes.client==='manual'?'직접 입력':session?.client_version || $('experiment-version').value} · 상담사 ${sourceModes.counselor==='manual'?'직접 입력':mode()==='basic'?'기본':mode()}`;
    phase.textContent=session || attempts.length?'생성 후 설정 확인':'생성 전 설정';
  }
  function updateCounselor(){
    const spec=counselorSpecs.find(row=>row.id===mode()),editable=spec?.system_prompt!==undefined || mode()==='basic';
    $('basic-counselor-settings').hidden=!editable;$('c4-settings').hidden=mode()!=='C4';
    $('basic-counselor-settings').firstChild.nodeValue='상담 지침';
    $('counselor-block-badge').textContent=editable?'실습 지침':'연구 코드 연결';
    $('counselor-mode-description').textContent=spec?.description || '상담사 버전 설정을 읽는 중입니다.';
    $('counselor-system-prompt').disabled=running || restoring || sending;
    promptActions.hidden=!editable;restorePrompt.disabled=running || restoring || sending;
    const matches=$('counselor-system-prompt').value===(spec?.system_prompt ?? '');
    promptSource.textContent=session?(matches?'저장된 세션 지침':'저장된 세션 지침 · 현재 기본과 다름'):(matches?'현재 기본 지침':'직접 편집한 지침');
    syncCounselorBudgets();updateSummary();
  }
  function controls(){
    const automatic=$('paired-mode').checked,human=manualTurn(),retry=retryInput(),locked=running || restoring || sending;
    $('paired-next').hidden=automatic || human;$('paired-auto').hidden=!automatic || human;
    $('paired-next').disabled=locked || !config || !editorScenario;$('paired-auto').disabled=$('paired-next').disabled;
    $('paired-pause').disabled=!running || !auto;$('paired-stop').disabled=!running;
    $('paired-pause').hidden=!running || !auto;$('paired-stop').hidden=!running;
    for(const id of ['paired-mode','manual-client','manual-counselor','turn-endpoint-mode','turn-endpoint-octet','turn-endpoint-port'])$(id).disabled=locked;
    $('turn-endpoint-port').disabled=locked || !!endpointFallback;
    $('paired-mode').disabled=locked || Object.values(modes()).every(value=>value==='manual');
    $('paired-limit-setting').hidden=!automatic || Object.values(modes()).includes('manual');
    $('paired-limit').disabled=locked || !automatic;counselorSelect.disabled=locked || !!session;
    for(const id of ['paired-manual-send','paired-manual-text'])$(id).disabled=locked || !config;
    $('paired-manual-role').value=inputActor();manual.hidden=!human && !retry;
    $('paired-manual-text').placeholder=retry?'상대 응답 실패 · 마지막 '+names[inputActor()]+' 발화를 수정해 재전송할 수 있습니다.':names[inputActor()]+' 발화 입력 · Shift+Enter 줄바꿈';
    $('paired-manual-send').textContent=retry?'재전송':'전송';
    $('paired-manual-send').title=retry?'마지막 직접 입력 발화를 교체하고 상대 응답을 다시 생성합니다.':modes()[inputActor()==='client'?'counselor':'client']==='model'?'발화를 저장하고 상대 응답을 생성합니다.':'발화를 저장합니다.';
    $('paired-next').textContent=(retry?'재시도 · ':'생성 · ')+names[nextActor()];
    $('paired-pause').textContent=pause?'현재 발화 완료 후 정지':'일시정지';nav.disabled=false;
    $('settings-lock-hint').hidden=!session;$('settings-lock-hint').textContent='이 세션의 메인 상담 지침과 추론 한도는 시작 당시 값을 사용합니다. 변경하려면 새 대화를 시작하세요. 서브에이전트 지침은 해당 편집란의 적용 시점을 확인하세요.';
    updateCounselor();
    document.dispatchEvent(new CustomEvent('lab-session-status',{detail:{busy:locked,sessionId:session?.session_id || null}}));
  }
  function markSelection(){
    for(const region of $('paired-transcript').querySelectorAll('[data-context-step]')){
      const active=shell.dataset.view==='activation' && region.closest('.paired-speech')?.dataset.entry===selection.attempt && Number(region.dataset.contextStep)===selection.activationIndex;
      region.classList.toggle('context-region-selected',active);
      if(region.classList.contains('turn-context-select'))region.setAttribute('aria-pressed',String(active));
    }
    for(const button of $('paired-transcript').querySelectorAll('.paired-speech-button,.paired-utterance-button'))button.setAttribute('aria-pressed',String(button.dataset.attempt?button.dataset.attempt===(selection.attempt || selection.manual?.turn_id):selection.initial!=null && Number(button.dataset.row)===selection.initial));
  }
  function inspect(view=shell.dataset.view){
    if(source==='standalone'){Journey.clearExternal();return false;}
    const attempt=selected();
    const index=attempt?Math.max(0,Math.min((view==='activation'?selection.activationIndex:selection.index) ?? 0,attempt.steps.length-1)):0;
    Journey.inspectExternal({id:attempt?.id || selection.manual?.turn_id || 'initial-'+selection.initial,number:attempt?.number || 0,
      question:attempt?`${names[attempt.actor]} · 생성 ${attempt.number}`:selection.manual?names[selection.manual.actor]+' · 직접 입력한 공개 발화':selection.initial!=null?'초기 대화 · 모델 호출 기록은 없습니다.':'오른쪽 발화를 선택하면 실행 과정이 나타납니다.',
      steps:attempt?.steps || [],done:attempt?.status!=='running',status:attempt?.status || 'empty',
      onSelect:i=>{if(shell.dataset.view==='activation')selection.activationIndex=i;else selection.index=i;activationFollow=false;markSelection();}},index);
    markSelection();return true;
  }
  const originalView=window.showLabResult;
  window.showLabResult=view=>{
    view=view==='paired'?'visual':view;
    if(['visual','raw','activation','workflow'].includes(view)){
      if(source==='paired'){document.body.classList.add('paired-mode');LabVersionViews.install();}else document.body.classList.remove('paired-mode');
      inspect(view);
    }
    originalView(view);markSelection();nav.setAttribute('aria-selected',String(['visual','raw','activation','workflow'].includes(view) && source==='paired'));
  };
  function chooseAttempt(attempt,{keepView=true}={}){
    activationFollow=attempt?.status==='running';
    selection.attempt=attempt?.id || null;selection.index=attempt?Math.max(0,attempt.steps.length-1):null;selection.activationIndex=selection.index;selection.initial=null;selection.manual=null;source='paired';
    const view=keepView && ['visual','raw','activation','workflow','session_notes'].includes(shell.dataset.view)?shell.dataset.view:'visual';window.showLabResult(view);
    LabVersionViews.saved(attempt);
  }
  function chooseRegion(attempt,index){
    // A chat region changes the detailed request only in Activation context.
    chooseAttempt(attempt);
    selection.activationIndex=index;activationFollow=false;
    $('agent-picker').value='auto';inspect();
  }
  function utteranceStep(attempt){
    const main=attempt.calls.filter(call=>call.agent==='main');
    // Committed speech can precede further state-update calls.
    for(const call of main)for(const state of attempt.steps){
      if(state.agentId!=='main' || state.iteration!==attempt.steps[call.stepIndex]?.iteration)continue;
      for(const tool of state.trace || [])if(['commit_client_utterance','stage_client_update'].includes(tool.name)){
        try{if(JSON.parse(tool.arguments).utterance===attempt.utterance && attempt.utterance)return call.stepIndex;}catch{}
      }
    }
    return main.findLast(call=>call.output && (!attempt.utterance || call.output===attempt.utterance))?.stepIndex ?? main.at(-1)?.stepIndex ?? 0;
  }
  nav.onclick=()=>{source='paired';document.body.classList.add('paired-mode');if(!attempts.length){window.showLabResult('visual');transcript();return;}if(!selected())chooseAttempt(attempts.at(-1));else window.showLabResult('visual');};
  $('settings-tab').addEventListener('click',()=>{nav.setAttribute('aria-selected','false');updateSummary();});
  $('config-tab-profile-library').addEventListener('click',()=>nav.setAttribute('aria-selected','false'));
  $('chat-tab').addEventListener('click',()=>{source='standalone';Journey.clearExternal();document.body.classList.remove('paired-mode');nav.setAttribute('aria-selected','false');updatePreview();});
  function transcript(){
    const target=$('paired-transcript'),top=target.scrollTop;
    let rows=session?.public_dialogue || [];
    if(!session && config){try{rows=[...selectedScenario().dialogue,{role:'counselor',content:$('message').value}];}catch{}}
    const initialCount=session?rows.length-session.steps.length:rows.length,records=[];
    rows.forEach((row,index)=>{
      const step=index>=initialCount?session.steps[index-initialCount]:null;
      if(step)for(const attempt of attempts.filter(a=>a.number===step.number && a.id!==step.turn_id))records.push({key:attempt.id,attempt,row:{role:attempt.actor,content:''}});
      records.push({key:step?.turn_id || 'initial-'+index,row,index,step,attempt:step && attempts.find(a=>a.id===step.turn_id)});
    });
    for(const attempt of attempts.filter(a=>a.number>=(session?.steps.length || 0)+1))records.push({key:attempt.id,attempt,row:{role:attempt.actor,content:''}});
    const wanted=[];
    for(const record of records){
      let entry=transcriptEntries.get(record.key);
      if(!entry){
        const box=node('article','paired-speech '+record.row.role),button=node('button','paired-speech-button'),heading=node('strong'),process=node('div','paired-turn-process'),speech=node('button','paired-utterance-button'),body=node('span','speech-text'),status=node('p','turn-response-status');
        button.type='button';speech.type='button';button.append(heading);speech.append(body);box.append(button,process,status,speech);
        entry={box,button,heading,process,speech,body,status};button.onclick=speech.onclick=()=>entry.onSelect();transcriptEntries.set(record.key,entry);
      }
      const {attempt,row,step,index}=record;
      entry.box.dataset.entry=record.key;
      for(const button of [entry.button,entry.speech]){
        if(attempt || step){button.dataset.attempt=attempt?.id || step.turn_id;delete button.dataset.row;}else{button.dataset.row=index;delete button.dataset.attempt;}
      }
      entry.heading.textContent=names[row.role]+(attempt?' · '+(attempt.status==='done'?'생성 ':'생성 시도 ')+attempt.number:step?' · 직접 입력 '+step.number:' · 초기 대화');
      entry.onSelect=()=>{if(attempt)chooseAttempt(attempt);else{selection.attempt=null;selection.initial=index;selection.manual=step?.source==='manual'?step:null;selection.index=null;source='paired';window.showLabResult('visual');}};
      entry.process.hidden=!attempt;
      if(attempt){
        LabVersionViews.mountAttempt(entry.process,attempt,i=>chooseRegion(attempt,i));
        entry.speech.dataset.contextStep=utteranceStep(attempt);
        entry.speech.classList.add('turn-selectable-utterance');
        entry.speech.onclick=()=>chooseRegion(attempt,utteranceStep(attempt));
      }else{delete entry.speech.dataset.contextStep;entry.speech.classList.remove('turn-selectable-utterance');entry.speech.onclick=()=>entry.onSelect();}
      const done=!attempt || attempt.status==='done';
      const live=attempt?.status==='running' && (attempt.actor==='client' && ['Q1','Q1_s1','Q1_s2'].includes(attempt.clientVersion) || attempt.actor==='counselor' && ['basic','P1','P2','P3','P3a','P3a_s1'].includes(attempt.counselorVersion))?attempt.calls.findLast(call=>call.agent==='main')?.output || '':'';
      const value=done?row.content || attempt?.utterance || '':live;
      entry.speech.hidden=!value;if(entry.body.textContent!==value)entry.body.textContent=value;
      entry.speech.classList.toggle('streaming',!!live && !done);
      entry.status.hidden=done;
      entry.status.textContent=done?'':attempt.status==='running'?names[attempt.actor]+' 응답 생성 중…':'응답 미완료 · '+(attempt.error || '중단된 실행입니다.');
      entry.box.classList.toggle('execution-attempt',!!attempt && !done);wanted.push(entry.box);
    }
    // Keep existing nodes attached: streaming must not reset details, focus or scroll.
    wanted.forEach((element,i)=>{if(target.children[i]!==element)target.insertBefore(element,target.children[i] || null);});
    const keep=new Set(wanted);for(const element of [...target.children])if(!keep.has(element))element.remove();
    const pending=attempts.findLast(row=>row.status==='running');
    $('paired-chat-status').textContent=pending?names[pending.actor]+' 발화 생성 중…':session?'다음 차례: '+names[session.next_actor]:'';
    $('paired-dialogue-count').textContent=session?session.public_dialogue.length+'개 발화 / 새로 생성 '+session.steps.filter(row=>row.source!=='manual').length+'개 · 직접 입력 '+session.steps.filter(row=>row.source==='manual').length+'개':'초기 대화';
    markSelection();target.scrollTop=followTranscript?target.scrollHeight:top;
  }
  function newAttempt(actor,id,number){
    const attempt=PairedTrace.create(actor,id,number,mode(),session?.client_version || currentVersion);attempts.push(attempt);
    if(!selection.attempt && selection.initial==null && !selection.manual || !restoring && activationFollow && shell.dataset.view==='activation'){selection.attempt=id;selection.index=0;selection.activationIndex=0;selection.initial=null;selection.manual=null;}
    return attempt;
  }
  function accept(attempt,event){
    PairedTrace.accept(attempt,event);
    if(activationFollow && shell.dataset.view==='activation' && selection.attempt===attempt.id)selection.activationIndex=Math.max(0,attempt.steps.length-1);
    if(attempt.extras.versions?.[attempt.counselorVersion])LabVersionViews.setCurrent(attempt.counselorVersion,attempt.extras.versions[attempt.counselorVersion]);
    LabVersionViews.refresh(attempt);
    if(event.type==='done')session=event.session;
    if(!paint && !restoring)paint=requestAnimationFrame(()=>{paint=null;transcript();if(['visual','raw','activation','workflow'].includes(shell.dataset.view) && source==='paired')inspect();});
  }
  function freeze(){if(config)syncConditions();controls();}
  async function ensureSession(){
    await versionReady;await counselorReady;if(session)return;
    await window.PromptEditors?.prepare();
    const request=body();request.session_id=null;request.counselor_version=counselorSelect.value;
    request.counselor_reasoning_budgets=readCounselorBudgets();
    if(counselorSpecs.find(row=>row.id===request.counselor_version)?.system_prompt!==undefined)request.counselor_system_prompt=$('counselor-system-prompt').value;
    request.speaker_modes=chosenModes();
    session=await api('/api/paired/create',request);LabStorage.setItem('counseling-paired-session',session.session_id);transcript();freeze();document.dispatchEvent(new Event('lab-sessions-changed'));
  }
  async function one(){
    await ensureSession();if(halt)throw Error('생성 시작 전에 중단했습니다.');requestId=crypto.randomUUID();abort=new AbortController();const actor=session.next_actor;
    let attempt=null,completed=false,failure=null;
    $('paired-status').textContent=names[actor]+' 생성 중'+(auto?' · 남은 발화 '+remaining:'');$('status').textContent=names[actor]+' 생성 중';
    const response=await fetch('/api/paired/step',{method:'POST',headers:{'Content-Type':'application/json','X-Gemma-Request':'1'},body:JSON.stringify({session_id:session.session_id,expected_step:session.steps.length,request_id:requestId,vllm_url:endpointValue()}),signal:abort.signal});
    await LabHTTP.requireStream(response,'/api/paired/step');
    const handle=message=>{const event=message.event;if(!attempt){attempt=newAttempt(actor,event.turn_id || 'live-'+Date.now(),session.steps.length+1);LabVersionViews.live(attempt);transcript();}
      accept(attempt,event);if(event.type==='done')completed=true;if(['error','cancelled'].includes(event.type))failure=event.error;};
    const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';
    while(true){const part=await reader.read();pending+=decoder.decode(part.value || new Uint8Array(),{stream:!part.done});let i;while((i=pending.indexOf('\n'))>=0){const line=pending.slice(0,i);pending=pending.slice(i+1);if(line.trim())handle(JSON.parse(line));}if(part.done)break;}
    if(pending.trim())handle(JSON.parse(pending));if(!completed)throw Error(failure || '발화가 완료되기 전에 연결이 종료되었습니다.');
  }
  async function run(automatic){
    if(running || restoring || !config || !editorScenario)return;running=true;auto=automatic;pause=false;halt=false;remaining=automatic?Number($('paired-limit').value):1;
    followTranscript=true;if(shell.dataset.view==='activation')activationFollow=true;
    if(!Number.isInteger(remaining) || remaining<1 || remaining>100){running=false;notice('자동 생성 발화 수는 1~100 정수로 입력하세요.');controls();return;}
    try{endpointValue();}catch(error){running=false;notice(error.message);controls();return;}
    setBusy(true);controls();notice();
    if(['settings','library'].includes(shell.dataset.view))window.showLabResult('visual');
    try{while(remaining>0 && !pause && !manualTurn()){await one();remaining--;if(remaining>0 && !pause && !manualTurn())await new Promise(resolve=>setTimeout(resolve,300));}
      $('paired-status').textContent=(manualTurn()?'직접 입력 대기 · ':pause?'일시정지 · ':'완료 · ')+'다음 차례: '+names[nextActor()];$('status').textContent=manualTurn()?'직접 입력 대기':pause?'일시정지':'완료';
    }catch(error){pause=true;$('status').textContent='미완료';$('paired-status').textContent='중단 / 오류 · 같은 발화부터 다시 시도 가능';notice(error.message);
      if(session)try{session=await api('/api/paired/session?session_id='+encodeURIComponent(session.session_id));}catch{}
      if(retryInput()){$('paired-manual-text').value=retryInput().content;}
      const attempt=attempts.at(-1);if(attempt?.status==='running')accept(attempt,{type:'error',error:error.message});
    }finally{running=false;auto=false;requestId=null;abort=null;setBusy(false);freeze();resizeInput();transcript();if(['visual','raw','activation','workflow'].includes(shell.dataset.view) && source==='paired')inspect();document.dispatchEvent(new Event('lab-sessions-changed'));}
  }
  function reset(resetVersion=true){
    if(running || sending || (resetVersion!==false && (restoring || settingsLoading || !counselorSpecs.length)))return;const hadSession=!!session;if(!hadSession)rememberCounselorDraft();session=null;followTranscript=true;activationFollow=true;attempts.length=0;transcriptEntries.clear();selection.attempt=null;selection.index=null;selection.activationIndex=null;selection.initial=null;selection.manual=null;
    if(paint){cancelAnimationFrame(paint);paint=null;}LabStorage.removeItem('counseling-paired-session');
    if(config)clearConversation(false);else Journey.clearExternal();
    if(resetVersion!==false){
      versionPreferences.set('counselor',counselorSelect.value);
    }
    applyCounselorPrompt();
    LabVersionViews.reset(counselorSelect.value);
    $('paired-manual-text').value='';resizeInput();
    $('paired-status').textContent='설정 후 내담자부터 생성합니다.';transcript();freeze();
    if(resetVersion!==false && versionCatalog){
      const preferred=$('experiment-version').value || currentVersion;
      versionPreferences.set('client',preferred);
      if(hadSession || currentVersion!==preferred){$('experiment-version').value=preferred;$('experiment-version').dispatchEvent(new Event('change'));}
    }
    if(resetVersion!==false){
      if(hadSession || source==='standalone')selectModel(config.model);
      source='paired';document.body.classList.add('paired-mode');LabVersionViews.install();$('settings-tab').click();window.selectSettingsRole('client');
      counselorReady=refreshCounselorCatalog().then(()=>window.PromptEditors?.sync(true));counselorReady.catch(error=>notice('상담사 기본 지침을 불러오지 못했습니다: '+error.message));
    }updateSummary();
  }
  async function load(id,key=''){
    if(running || restoring || sending || busy)return;
    await counselorReady;restoring=true;controls();
    let saved;
    try{saved=await api('/api/paired/session?session_id='+encodeURIComponent(id)+(key?'&key='+encodeURIComponent(key):''));}catch(error){restoring=false;controls();throw error;}
    reset(false);restoring=true;session=saved;controls();source='paired';document.body.classList.add('paired-mode');
    try{
      currentVersion=session.client_version;$('experiment-version').value=currentVersion;
      versionPreferences.set('client',currentVersion);versionPreferences.set('counselor',session.counselor_version);
      $('summary-settings').hidden=!stateSummaryMode();$('state-memory').hidden=!stateSummaryMode();$('state-input-hint').hidden=!stateSummaryMode();counselorSelect.value=session.counselor_version || 'C4';
      if(session.settings.counselor_system_prompt)$('counselor-system-prompt').value=session.settings.counselor_system_prompt;
      previousCounselor=mode();LabVersionViews.select(mode());
      setModes(session.speaker_modes);selectModel(session.connection.model);restoreEndpoint(session.connection.vllm_url);
      const profileEntry=historyCatalog.profiles.find(p=>p.histories.some(h=>h.scenario.id===session.settings.scenario.id));$('experiment-profile').value=profileEntry?.number || 'default';populateHistoryChoices();if(profileEntry)$('history-select').value=profileEntry.histories.find(h=>h.scenario.id===session.settings.scenario.id).id;
      editorScenario=structuredClone(session.settings.scenario);activeScenario=null;setupConditions();$('history-description').textContent='저장된 세션의 초기 프로필·히스토리 설정';
      $('system_prompt').value=session.settings.system_prompt;$('summary_system_prompt').value=session.settings.summary_system_prompt || '';$('profile-context-mode').value=session.settings.profile_context_mode || 'selective';
      for(const input of document.querySelectorAll('[data-block]')){input.checked=session.settings.input_blocks?.[input.dataset.block] ?? true;input.closest('.setting-block').dataset.enabled=String(input.checked);}
      for(const key of ['temperature','top_p','max_tokens','seed'])$(key).value=session.settings.generation[key] ?? '';
      restoreCounselorBudgets(session.settings.counselor_reasoning_budgets);
      restoreClientBudgets(session.settings.client_reasoning_budgets);
      $('thinking-mode').value=session.settings.thinking_mode || (session.connection.chat_template_kwargs?.enable_thinking===true?'on':session.connection.chat_template_kwargs?.enable_thinking===false?'off':'default');
      enabledToolNames=session.settings.enabled_tools || null;await refreshTools();
      for(const row of saved.attempts){const attempt=newAttempt(row.actor,row.turn_id,row.step),result=await api('/api/paired/events?session_id='+encodeURIComponent(id)+'&attempt='+encodeURIComponent(row.turn_id));for(const event of result.events)accept(attempt,event);if(attempt.status==='running')accept(attempt,{type:'error',error:'완료 전 연결이 종료된 실행 기록입니다.'});}
      session=saved;LabStorage.setItem('counseling-paired-session',id);$('paired-status').textContent='불러옴 · 다음 차례: '+names[session.next_actor];
      $('paired-manual-text').value=retryInput()?.content || '';followTranscript=true;transcript();
      const versionState=await api('/api/paired/version_state?session_id='+encodeURIComponent(id));LabVersionViews.setCurrent(versionState.version,versionState.data);
      if(attempts.length)chooseAttempt(attempts.at(-1));else window.showLabResult('visual');
    }finally{restoring=false;freeze();resizeInput();}
  }
  window.Paired={get hasSession(){return !!session;},get sessionId(){return session?.session_id || null;},get running(){return running || restoring || sending;},reset,load,
    get counselorSpec(){return counselorSpecs.find(row=>row.id===mode());},
    showStandalone(){source='standalone';document.body.classList.remove('paired-mode');LabVersionViews.standalone();window.showLabResult('visual');}
  };
  manual.onsubmit=async event=>{event.preventDefault();if(running || sending || restoring)return;const content=$('paired-manual-text').value.trim();if(!content)return;
    sending=true;controls();notice();try{endpointValue();await ensureSession();const retry=retryInput();const result=await api('/api/paired/manual',{session_id:session.session_id,expected_step:session.steps.length,actor:inputActor(),content,...(retry?{replace_turn_id:retry.turn_id,replace_content:retry.content}:{})});session=result.session;$('paired-manual-text').value='';resizeInput();followTranscript=true;transcript();sending=false;freeze();document.dispatchEvent(new Event('lab-sessions-changed'));if(!manualTurn())await run($('paired-mode').checked);else{$('paired-status').textContent='직접 입력 대기 · '+names[nextActor()];if(['settings','library'].includes(shell.dataset.view))window.showLabResult('visual');}}catch(error){notice(error.message);}finally{sending=false;controls();}};
  $('paired-manual-text').oninput=resizeInput;
  $('paired-manual-text').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();manual.requestSubmit();}};
  $('paired-next').onclick=()=>run(false);$('paired-auto').onclick=()=>run(true);$('paired-mode').onchange=controls;$('paired-limit').oninput=updateSummary;
  const changeModes=async()=>{
    if(running || restoring || sending)return;
    const chosen=chosenModes();
    if(session){sending=true;controls();try{session=await api('/api/paired/controls',{session_id:session.session_id,expected_step:session.steps.length,speaker_modes:chosen});}catch(error){notice(error.message);setModes(session.speaker_modes);}finally{sending=false;controls();}}
    else controls();
    if(manualTurn()){$('paired-status').textContent='직접 입력 대기 · '+names[nextActor()];window.showLabResult('visual');$('paired-manual-text').focus({preventScroll:true});}
  };
  $('manual-client').onchange=changeModes;$('manual-counselor').onchange=changeModes;
  $('experiment-version').addEventListener('change',updateSummary);document.addEventListener('lab-settings-ready',()=>{if(!endpointTouched)restoreEndpoint(config.vllm_url);controls();transcript();});
  $('paired-pause').onclick=()=>{pause=true;controls();};
  $('paired-stop').onclick=async()=>{pause=true;halt=true;if(requestId)try{const result=await api('/api/cancel',{request_id:requestId});if(!result.cancelled)abort?.abort();}catch(error){abort?.abort();notice(error.message);}controls();};
  $('reset').addEventListener('click',()=>{if(document.body.classList.contains('paired-mode'))reset();});$('reload').addEventListener('click',reset);
  counselorReady=refreshCounselorCatalog(true);counselorReady.catch(error=>notice(error.message));
  $('counselor-system-prompt').oninput=()=>{rememberCounselorDraft(mode());updateCounselor();};
  for(const scope of ['main','subagent'])$('counselor-'+scope+'-budget-enabled').onchange=syncCounselorBudgets;
  document.addEventListener('lab-prompt-applied',event=>{
    const value=event.detail;if(value.role!=='counselor' || value.kind!=='system')return;
    const spec=counselorSpecs.find(row=>row.id===value.version);if(spec)spec.system_prompt=value.content;
    delete counselorDrafts[value.version];updateCounselor();
  });
  document.addEventListener('lab-prompt-source',event=>{
    const value=event.detail;if(value.role!=='counselor' || value.kind!=='system')return;
    const spec=counselorSpecs.find(row=>row.id===value.version);if(spec)spec.system_prompt=value.content;
  });
  restorePrompt.onclick=()=>{if(session || running || sending || restoring)return;delete counselorDrafts[mode()];applyCounselorPrompt();updateCounselor();counselorReady=refreshCounselorCatalog();counselorReady.catch(error=>notice(error.message));};
  document.addEventListener('input',()=>{if(!session && config)transcript();});counselorSelect.onchange=()=>{versionPreferences.set('counselor',counselorSelect.value);rememberCounselorDraft();applyCounselorPrompt();LabVersionViews.select(counselorSelect.value);updateCounselor();};updateCounselor();controls();
  $('settings-tab').click();document.body.classList.remove('ui-booting');$('ui-loading')?.remove();
  window.Paired.ready=(async()=>{
    while(!config || !editorScenario || settingsLoading)await new Promise(resolve=>setTimeout(resolve,100));
    await counselorReady;controls();transcript();
    const id=LabStorage.getItem('counseling-paired-session');
    if(id)try{await load(id);}catch(error){notice('이전 세션 자동 불러오기 실패: '+error.message);}
  })();
})();
