'use strict';
(() => {
  const pairs=[['client','내담자','experiment-version'],['counselor','상담사','counselor-version'],['profile','프로필','experiment-profile']];
  const heading=document.querySelector('.dashboard-heading'),box=document.createElement('div');
  const paneShell=document.querySelector('.lab-shell');
  if(LabStorage.prefix){paneShell.inert=true;document.body.classList.add('pane-restoring');}
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
  let ready=false,disposed=false,saving=null,lastStatus='';
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
      label:client+' / '+counselor+' · '+profile,title:'내담자 '+client+' · 상담사 '+counselor+' · '+profile,ready};
  }
  function announce(){
    if(disposed)return;
    const current=JSON.stringify(status());if(current===lastStatus)return;lastStatus=current;
    if(parent!==window)parent.postMessage({type:'lab-pane-status'},location.origin);
  }
  // File-backed subagent/template editors still edit shared source files explicitly.
  // Per-session main prompts, initial dialogue and generation settings are pane drafts.
  function draftField(el){return el.id && !el.id.startsWith('quick-') && !el.id.includes('subagent-prompt') && !el.id.endsWith('-user-template') && el.id!=='client-trajectory' && !el.id.endsWith('subagent-select') && !['file','button','submit'].includes(el.type);}
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
    if(!ready || disposed || !LabStorage.prefix || window.Paired?.running)return;
    try{
      // Existing conversations restore their immutable settings from the server.
      const value=window.Paired?.hasSession?{sessionId:Paired.sessionId,composer:$('paired-manual-text').value,endpoint:TurnConnection.value()}:snapshot();
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
      if(draft.sessionId===Paired.sessionId){$('paired-manual-text').value=draft.composer || '';if(draft.endpoint)TurnConnection.restore(draft.endpoint);}
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
    for(const [id,value] of Object.entries(draft.values || {})){
      const el=$(id);if(!el || !draftField(el) || ['experiment-version','counselor-version','experiment-profile','history-select'].includes(id))continue;
      if(el.tagName!=='SELECT' || [...el.options].some(o=>o.value===value.value))el.value=value.value;
      if(el.type==='checkbox')el.checked=value.checked;
    }
    if(Array.isArray(draft.tools)){enabledToolNames=draft.tools;await refreshTools();}
    if(draft.endpoint)TurnConnection.restore(draft.endpoint);
    selectModel($('model-select').value);
    for(const id of ['manual-client','manual-counselor','paired-mode'])$(id).dispatchEvent(new Event('change',{bubbles:true}));
    for(const input of document.querySelectorAll('[data-block]'))input.closest('.setting-block').dataset.enabled=String(input.checked);
    for(const id of ['system_prompt','summary_system_prompt','counselor-system-prompt','paired-manual-text'])$(id).dispatchEvent(new Event('input',{bubbles:true}));
    syncConditions();updatePreview();
  }
  window.LabPane={get ready(){return ready;},status,snapshot,save,dispose(){disposed=true;clearTimeout(saving);}};
  for(const name of ['input','change','lab-settings-ready','lab-session-status','lab-sessions-changed'])document.addEventListener(name,changed);
  window.addEventListener('pagehide',save);
  (async()=>{
    await Paired.ready;await PromptEditors.sync();
    if(LabStorage.prefix){let draft;try{draft=JSON.parse(LabStorage.getItem('draft'));}catch{}await restore(draft);}
  })().catch(error=>notice('실험 탭 설정 복원 실패: '+error.message)).finally(()=>{
    ready=true;paneShell.inert=false;document.body.classList.remove('pane-restoring');sync();save();
  });
})();
