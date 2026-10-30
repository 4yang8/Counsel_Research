'use strict';
(() => {
  const pairs=[['client','내담자','experiment-version'],['counselor','상담사','counselor-version'],['profile','프로필','experiment-profile']];
  const heading=document.querySelector('.dashboard-heading'),box=document.createElement('div');
  const paneShell=document.querySelector('.lab-shell');
  paneShell.inert=true;document.body.classList.add('pane-restoring');
  box.className='quick-experiment-settings';box.setAttribute('role','group');box.setAttribute('aria-label','실험 빠른 설정');
  const mirrors=[];
  for(const [key,label,id] of pairs){
    const wrapper=document.createElement('label'),select=document.createElement('select');
    select.id='quick-'+key;select.setAttribute('aria-label','빠른 설정 · '+label);select.disabled=true;
    wrapper.append(document.createTextNode(label),select);box.append(wrapper);const original=$(id);
    mirrors.push({select,original,signature:''});
    select.onchange=()=>{
      if(original.disabled){sync();return;}
      original.value=select.value;original.dispatchEvent(new Event('change',{bubbles:true}));sync();
    };
    new MutationObserver(sync).observe(original,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
  }
  heading.append(box);
  new ResizeObserver(()=>document.querySelector('#settings-dashboard').style.setProperty('--settings-heading-height',Math.max(0,heading.offsetHeight-10)+'px')).observe(heading);
  let ready=false,disposed=false,saving=null,lastStatus='',reservation=null,reservationBlocks=[];
  function sync(){
    for(const mirror of mirrors){
      const {select,original}=mirror;
      const signature=JSON.stringify([...original.options].map(o=>[o.value,o.text,o.disabled]));
      if(signature!==mirror.signature){select.replaceChildren(...[...original.options].map(o=>Object.assign(new Option(o.text,o.value),{disabled:o.disabled})));mirror.signature=signature;}
      select.value=original.value;select.disabled=!ready || original.disabled;
      select.title=original.disabled?'현재 세션의 설정입니다. 변경하려면 새 대화를 시작하세요.':original.selectedOptions[0]?.text || '';
    }
    announce();
  }
  function status(){
    const client=$('experiment-version').value,counselor=$('counselor-version').value;
    const profile=$('experiment-profile').selectedOptions[0]?.text || '프로필';
    return {busy:!ready || busy || !!window.Paired?.running,sessionId:window.Paired?.sessionId,
      reserved:!!reservation,progress:window.Paired?.progress(),clientVersion:client,counselorVersion:counselor,profileId:$('experiment-profile').value,
      label:client+' / '+counselor+' · '+profile,title:'내담자 '+client+' · 상담사 '+counselor+' · '+profile,ready};
  }
  function announce(){
    if(disposed)return;
    const current=JSON.stringify(status());if(current===lastStatus)return;lastStatus=current;
    if(parent!==window)parent.postMessage({type:'lab-pane-status'},location.origin);
  }
  // File-backed subagent/template editors still edit shared source files explicitly.
  // Per-session main prompts, initial dialogue and generation settings are pane drafts.
  function draftField(el){return el.id && !el.id.startsWith('quick-') && !el.id.includes('subagent-prompt') && !el.id.endsWith('-user-template') && el.id!=='client-trajectory' && el.id!=='client-note-skill' && !el.id.endsWith('subagent-select') && !['file','button','submit'].includes(el.type);}
  function snapshot(){
    let scenario;
    try{scenario=selectedScenario();}catch{scenario=structuredClone(editorScenario);}
    const values={};
    for(const el of document.querySelectorAll('#settings-dashboard input[id],#settings-dashboard select[id],#settings-dashboard textarea[id]')){
      if(draftField(el))values[el.id]={value:el.value,...(el.type==='checkbox'?{checked:el.checked}:{})};
    }
    for(const id of ['manual-client','manual-counselor','paired-mode','paired-limit','paired-manual-text','message']){
      const el=$(id);if(el)values[id]={value:el.value,...(el.type==='checkbox'?{checked:el.checked}:{})};
    }
    let endpoint;try{endpoint=TurnConnection.value();}catch{}
    return {clientVersion:$('experiment-version').value,counselorVersion:$('counselor-version').value,
      profileId:$('experiment-profile').value,historyId:$('history-select').value,scenario,turns:readTurns(),values,
      tools:[...document.querySelectorAll('#tool-options input:checked')].map(el=>el.value),endpoint};
  }
  function save(){
    if(!ready || disposed)return;
    try{
      // Existing conversations restore their immutable settings from the server.
      const value=window.Paired?.hasSession?{
        sessionId:Paired.sessionId,composer:$('paired-manual-text').value,endpoint:TurnConnection.value(),
        controls:{automatic:$('paired-mode').checked,limit:$('paired-limit').value}
      }:snapshot();
      LabStorage.setItem('draft',JSON.stringify(value));
    }catch{if(parent!==window)parent.postMessage({type:'lab-pane-storage-error'},location.origin);}
  }
  function changed(event){
    // A select emits input before change. Do not replace the user's quick choice
    // with the old detailed value before its change handler can apply it.
    if(event?.type==='input' && event.target.id?.startsWith('quick-'))return;
    sync();clearTimeout(saving);saving=setTimeout(save,250);
  }
  async function restore(draft){
    if(!draft)return;
    if(Paired.hasSession){
      if(draft.sessionId===Paired.sessionId){
        $('paired-manual-text').value=draft.composer || '';if(draft.endpoint)TurnConnection.restore(draft.endpoint);
        if(draft.controls){
          $('paired-mode').checked=!!draft.controls.automatic;
          $('paired-limit').value=draft.controls.limit;
          $('paired-mode').dispatchEvent(new Event('change',{bubbles:true}));
        }
      }
      return;
    }
    if(draft.sessionId)return;
    for(const [id,value] of [['experiment-profile',draft.profileId],['history-select',draft.historyId],['experiment-version',draft.clientVersion],['counselor-version',draft.counselorVersion]]){
      const el=$(id);if(value && [...el.options].some(o=>o.value===value)){el.value=value;el.dispatchEvent(new Event('change',{bubbles:true}));}
    }
    await versionReady;await PromptEditors.sync();
    if(draft.scenario){editorScenario=structuredClone(draft.scenario);activeScenario=null;setupConditions();}
    if(draft.turns){$('dialogue-editor').replaceChildren();draft.turns.forEach(turn=>addDialogueTurn(turn));syncTarget();}
    // Restore values after version/profile handlers have populated their defaults.
    if(draft.values?.['model-select']?.value)selectModel(draft.values['model-select'].value);
    for(const [id,value] of Object.entries(draft.values || {})){
      const el=$(id);if(!el || !draftField(el) || ['experiment-version','counselor-version','experiment-profile','history-select'].includes(id))continue;
      if(el.tagName!=='SELECT' || [...el.options].some(o=>o.value===value.value))el.value=value.value;
      if(el.type==='checkbox')el.checked=value.checked;
    }
    if(Array.isArray(draft.tools)){enabledToolNames=draft.tools;await refreshTools();}
    if(draft.endpoint)TurnConnection.restore(draft.endpoint);
    selectModel($('model-select').value);
    for(const id of ['manual-client','manual-counselor','paired-mode','counselor-main-budget-enabled','counselor-subagent-budget-enabled'])$(id).dispatchEvent(new Event('change',{bubbles:true}));
    for(const input of document.querySelectorAll('[data-block]'))input.closest('.setting-block').dataset.enabled=String(input.checked);
    for(const id of ['system_prompt','summary_system_prompt','counselor-system-prompt','paired-manual-text'])$(id).dispatchEvent(new Event('input',{bubbles:true}));
    syncConditions();updatePreview();
  }
  // Only shared experimental conditions cross panes. Version-specific prompts,
  // tool choices, memory and ongoing public dialogue remain owned by each pane.
  const commonFields=['model-select','thinking-mode','temperature','top_p','max_tokens','seed',
    'profile-context-mode','message',...['system','task','profile','dialogue'].map(k=>'block-enabled-'+k),
    ...['client','counselor'].flatMap(role=>['main','subagent'].flatMap(scope=>[role+'-'+scope+'-budget',role+'-'+scope+'-budget-enabled']))];
  function common(){
    return {scenario:selectedScenario(),turns:readTurns(),profileId:$('experiment-profile').value,historyId:$('history-select').value,
      counselorVersion:$('counselor-version').value,counselorPrompt:$('counselor-system-prompt').value,
      endpoint:TurnConnection.value(),values:Object.fromEntries(commonFields.filter(id=>$(id)).map(id=>[id,{value:$(id).value,...($(id).type==='checkbox'?{checked:$(id).checked}:{})}]))};
  }
  function checkAccess(owner){
    if(!ready || disposed || busy || Paired.running)throw Error('준비 또는 생성 중인 탭입니다.');
    if(reservation && reservation!==owner)throw Error('다른 일괄 작업이 제어 중인 탭입니다.');
  }
  function commonRequest(value){
    const request={experiment_version:currentVersion,system_prompt:$('system_prompt').value,summary_system_prompt:$('summary_system_prompt').value,
      ...(enabledToolNames!==null?{enabled_tools:[...enabledToolNames]}:{})},v=value.values;
    request.session_id=null;delete request.session_key;
    request.scenario=structuredClone(value.scenario);
    request.messages=[{role:'user',content:v.message.value}];
    request.model=v['model-select'].value;request.thinking_mode=v['thinking-mode'].value;
    request.generation={temperature:Number(v.temperature.value),top_p:Number(v.top_p.value),max_tokens:Number(v.max_tokens.value),seed:v.seed.value===''?null:Number(v.seed.value)};
    request.profile_context_mode=v['profile-context-mode'].value;
    request.input_blocks=Object.fromEntries(['system','task','profile','dialogue'].map(k=>[k,v['block-enabled-'+k]?.checked ?? true]));
    request.client_reasoning_budgets=Object.fromEntries(['main','subagent'].map(scope=>[scope,v['client-'+scope+'-budget-enabled']?.checked?Number(v['client-'+scope+'-budget'].value):null]));
    request.vllm_url=value.endpoint;
    return request;
  }
  async function validateCommon(value,{fresh=false,owner}={}){
    checkAccess(owner);
    if(Paired.hasSession && !fresh)throw Error('기존 대화에는 초기 조건을 적용할 수 없습니다. 새 대화 실행을 사용하세요.');
    if(![...$('counselor-version').options].some(o=>o.value===value.counselorVersion))throw Error('지원하지 않는 상담사 버전입니다.');
    if(value.counselorVersion!=='C4'&&!value.counselorPrompt.trim())throw Error('상담사 지침을 입력하세요.');
    for(const role of ['client','counselor'])for(const scope of ['main','subagent']){
      const v=value.values;
      if(v[role+'-'+scope+'-budget-enabled']?.checked){const n=Number(v[role+'-'+scope+'-budget'].value);if(!Number.isInteger(n)||n<1||n>1000000)throw Error('추론 토큰 한도는 1~1,000,000 정수로 입력하세요.');}
    }
    await api('/api/preview',commonRequest(value));
  }
  async function applyCommon(value,{fresh=false,owner}={}){
    checkAccess(owner);
    if(Paired.hasSession && !fresh)throw Error('기존 대화에는 초기 조건을 적용할 수 없습니다.');
    const own={system_prompt:{value:$('system_prompt').value},summary_system_prompt:{value:$('summary_system_prompt').value}};
    if(fresh)Paired.reset(false);
    const values={...structuredClone(value.values),...own,'counselor-system-prompt':{value:value.counselorPrompt},
      'manual-client':{value:'on',checked:false},'manual-counselor':{value:'on',checked:false},'paired-mode':{value:'on',checked:true},'paired-manual-text':{value:''}};
    await restore({...structuredClone(value),values});
    document.body.classList.add('paired-mode');
    save();sync();
  }
  function reserve(owner){
    checkAccess(owner);if(reservation===owner)return;reservation=owner;
    reservationBlocks=[...document.querySelectorAll('#settings-dashboard,.paired-composer,.session-sidebar,.composer-dock,#reload,#reset,#send,#message,#trial-mode,#tools-refresh,#sample-request,#stop,#check')].map(el=>[el,el.inert]);
    for(const [el] of reservationBlocks)el.inert=true;
    announce();
  }
  function release(owner){if(reservation===owner){reservation=null;for(const [el,inert] of reservationBlocks)el.inert=inert;reservationBlocks=[];sync();save();}}
  window.LabPane={get ready(){return ready;},status,snapshot,save,common,validateCommon,applyCommon,reserve,release,
    setControlledEndpoint(endpoint,owner){checkAccess(owner);TurnConnection.restore(endpoint);save();},
    async startControlled(owner,count,metadata){if(reservation!==owner)throw Error('일괄 실행 제어권이 없습니다.');return Paired.startControlled(count,metadata);},
    pause:()=>Paired.pause(),stop:()=>Paired.stop(),
    dispose(){disposed=true;clearTimeout(saving);}};
  for(const name of ['input','change','lab-settings-ready','lab-session-status','lab-sessions-changed'])document.addEventListener(name,changed);
  window.addEventListener('beforeunload',save);
  window.addEventListener('pagehide',save);
  (async()=>{
    await Paired.ready;await PromptEditors.sync();
    let draft;try{draft=JSON.parse(LabStorage.getItem('draft'));}catch{}await restore(draft);
  })().catch(error=>notice('실험 탭 설정 복원 실패: '+error.message)).finally(()=>{
    ready=true;paneShell.inert=false;document.body.classList.remove('pane-restoring');sync();save();
  });
})();
