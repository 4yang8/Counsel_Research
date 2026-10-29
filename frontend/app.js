'use strict';
const $ = id => document.getElementById(id);
let history = [], records = [], config, lastRequest = null, preview = null, busy = false, controller, timer, previewVersion = 0;
let activeScenario = null, activeSystem = null, activeTools = null, enabledToolNames = null, editorScenario = null, historyCatalog = null;
let sessionId = null, activeRequestId = null, modelCalls = [], toolInfo = null;
let restoredSessionKey = null;
let versionCatalog = null, versionDrafts = {}, currentVersion = 'state_summary', innerRecords = [];
let versionReady = Promise.resolve();
let settingsLoading = true;
// Remember the versions currently in use, including a restored conversation.
const versionPreferences={
  get(role,versions,fallback){
    try{const saved=LabStorage.getItem('counseling-last-'+role+'-version');if(versions.some(v=>v.id===saved))return saved;}catch{}
    return fallback;
  },
  set(role,value){if(value)try{LabStorage.setItem('counseling-last-'+role+'-version',value);}catch{}},
  async initialize(){
    try{
      const legacy=LabStorage.getItem('counseling-version-preferences-format')!=='2';
      const defaults={client:'state_summary',counselor:'basic'};
      const before=Object.fromEntries(['client','counselor'].map(role=>[role,LabStorage.getItem('counseling-last-'+role+'-version')]));
      // Earlier reset code saved server defaults before recovering the last used versions.
      const missing=Object.keys(before).filter(role=>!before[role] || legacy && before[role]===defaults[role]);
      if(missing.length){
        const rows=(await api('/api/sessions')).sessions;
        const latest=rows.find(row=>row.kind==='paired');
        if(latest){
          const saved=await api('/api/paired/session?session_id='+encodeURIComponent(latest.session_id)+'&key='+encodeURIComponent(latest.key));
          for(const role of missing)if(LabStorage.getItem('counseling-last-'+role+'-version')===before[role])this.set(role,saved[role+'_version']);
        }
      }
      LabStorage.setItem('counseling-version-preferences-format','2');
    }catch{} // Preferences must not prevent starting a new conversation.
  }
};
const versionPreferencesReady=versionPreferences.initialize();
const stateSummaryMode = () => currentVersion === 'state_summary';
const q1Mode = () => ['Q1','Q1_s1','Q1_s2','Q1a','Q2','Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(currentVersion);
function clientBudgets(){
  return Object.fromEntries(['main','subagent'].map(scope=>{
    if(!$('client-'+scope+'-budget-enabled').checked)return [scope,null];
    const input=$('client-'+scope+'-budget'),value=Number(input.value);
    if(!input.value.trim() || !Number.isInteger(value) || value<1 || value>1000000)throw Error('내담자 추론 한도는 1~1,000,000 정수로 입력하세요.');
    return [scope,value];
  }));
}
function restoreClientBudgets(value={}){
  for(const scope of ['main','subagent']){
    $('client-'+scope+'-budget-enabled').checked=Number.isInteger(value?.[scope]);
    $('client-'+scope+'-budget').value=value?.[scope] ?? 512;
  }
}
function syncClientVersionUI(){
  const q1=q1Mode(),locked=busy || !!window.Paired?.hasSession || (!independent() && !!activeScenario);
  for(const id of ['block-task','block-state','block-taxonomy'])$(id).hidden=q1;
  $('q1-input-hint').hidden=!q1;$('client-reasoning-budgets').hidden=!q1;
  $('profile-context-mode').closest('label').hidden=q1;
  if(q1)for(const name of (versionCatalog?.versions.find(v=>v.id===currentVersion)?.required_tools || ['stage_client_update'])){const check=$('tool-enabled-'+name);if(check){check.checked=true;check.disabled=true;}}
  $('client-subagent-budget-enabled').parentElement.lastChild.textContent=currentVersion==='Q2'?'서브에이전트 · 특성/상태':currentVersion==='Q1a'?'서브에이전트 · 라우터/검토':'특성 라우터';
  $('q1-input-hint').textContent=currentVersion==='Q3b'?'현재 정보로 판단할 수 있으면 바로 반응을 기록하고, 별도로 해결할 판단이 있을 때만 작업 계획을 사용합니다. 다음 턴에 필요한 현재 마음을 중심으로 노트를 갱신합니다.':currentVersion==='Q3a'?'이번 발화의 작업 목표와 단계를 계획하고, 결과를 기록하며 재계획합니다. 작업 계획은 생성 과정과 노트 화면에서 확인할 수 있습니다. 다음 턴에는 새 계획을 만듭니다.':currentVersion==='Q3'?'사전 신뢰 궤적과 현재 마음을 연결해 반응을 계획합니다. 필요하면 과거 근거를 조회하고, 발화 후 내면 노트·진행 기록을 함께 저장합니다. 궤적은 아래에서 편집할 수 있습니다.':['Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(currentVersion)?'노트 선택·추가 조회·대화 의도 비교 실험. 발화 확정 후 전체 노트로 갱신합니다. 실제 입력은 Activation context에서 확인하세요.':currentVersion==='Q2a_prefill_test'?'특성·배경 라우터에만 영어 reasoning prefill을 넣습니다. 채팅 생성 과정에서 주입한 입력과 모델의 추론을 구분하고, 실제 템플릿 프롬프트에서 적용 위치를 확인할 수 있습니다.':currentVersion==='Q2a_trait_background'?'라우터가 관련 특성과 성장 경험·상황 사례를 함께 선택해 원문을 전달합니다. 배경이 있는 그룹1 프로필로 비교할 수 있습니다. 노트는 글머리 항목 단위로 추가·수정·삭제합니다.':currentVersion==='Q2a_variant_context'?'마지막 노트 갱신 호출에서 발화·진행 지침, 다른 도구 정의, 선택하지 않은 상태 설명을 제외합니다. 개인 정보·대화·노트·같은 턴 reasoning은 유지합니다. Activation context에서 세부 입력을 확인하세요.':['Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(currentVersion)?'메인이 같은 컨텍스트에서 내면 판단 → 발화 확정 → 상태 선택 → 상태 갱신을 이어갑니다. 특성 라우터만 별도 호출합니다. 모든 기록 성공 후 대화와 노트에 반영합니다.':currentVersion==='Q2'?'10개 자연어 심리상태를 참고해 반응합니다. 발화 완료 후 상태 라우터·갱신 담당이 다음 턴의 노트를 정리합니다. 초기 상태는 판단 정보 부족으로 시작합니다. 노트 화면에서 원문·변경 이력을 조회할 수 있습니다.':currentVersion==='Q1a'?'Q1a는 원본 Q1의 입력·특성 라우팅을 유지합니다. 초안 저장과 확정은 필수이며 검토는 선택적입니다. 발화·appraisal·내면 노트를 후보별로 함께 관리합니다.':'Q1은 기본 정보와 자동 선정 특성, 공개 대화, 최신 비공개 내면 노트를 사용합니다. 기존 작업 지침·고정 상태값·taxonomy는 입력하지 않습니다.';
  for(const scope of ['main','subagent']){
    const enabled=$('client-'+scope+'-budget-enabled');enabled.disabled=locked;
    $('client-'+scope+'-budget').disabled=locked || !enabled.checked;
  }
}
const fields = ['temperature','top_p','max_tokens','seed','system_prompt'];
const pretty = value => JSON.stringify(value, null, 2);
function notice(text='') { $('notice').textContent=text; $('notice').hidden=!text; }
function settings() { return {model:$('model-select').value,thinking_mode:$('thinking-mode').value,system_prompt:$('system_prompt').value,generation:{temperature:Number($('temperature').value),top_p:Number($('top_p').value),max_tokens:Number($('max_tokens').value),seed:$('seed').value===''?null:Number($('seed').value)}}; }
function selectModel(value) {
  if(![...$('model-select').options].some(option=>option.value===value))$('model-select').add(new Option(value,value));
  $('model-select').value=value;$('model').textContent=value;
}
function independent() { return $('trial-mode').value==='independent'; }
const inputBlocks=()=>Object.fromEntries([...document.querySelectorAll('[data-block]')].map(input=>[input.dataset.block,input.checked]));
const profileFixedFields=['profile_id','demographic_context','presenting_problem','core_belief','intermediate_beliefs','coping_strategies'];
function updateProfileExposure() {
  let profile;
  try{profile=JSON.parse($('edit-profile').value);}catch{return;}
  if(q1Mode()){
    const basic=['profile_id','name','age','gender','occupation','background','demographic_context','presenting_problem','recent_context','desired_help'];
    const excluded=['childhood_data','situations','created_at','meta','start_rapport'];
    $('profile-exposure-status').textContent=currentVersion+': 기본 정보는 메인·라우터에 제공, 특성은 라우터에만 전체 제공 → 매 턴 관련 원문만 메인에 전달. 기존 고정 상태·taxonomy는 사용하지 않습니다.';
    $('profile-exposure-fields').textContent='기본 정보: '+Object.keys(profile).filter(k=>basic.includes(k)).join(', ')+' · 라우팅 대상: '+Object.keys(profile).filter(k=>!basic.includes(k)&&!excluded.includes(k)).join(', ')+' · 이번 버전 미사용: '+Object.keys(profile).filter(k=>excluded.includes(k)).join(', ');
    if(['Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(currentVersion)){
      const backgrounds=['childhood_data','situations'].filter(k=>k in profile);
      $('profile-exposure-status').textContent=$('block-enabled-profile').checked?'기본 정보는 메인·라우터에 제공. 특성과 배경 후보는 라우터가 읽고, 선택된 원문과 관련성만 메인에 전달합니다.':'프로필 블록 비활성 · 기본 정보·특성·배경 후보 제외';
      $('profile-exposure-fields').textContent='기본 정보: '+Object.keys(profile).filter(k=>basic.includes(k)).join(', ')+' · 특성 후보: '+Object.keys(profile).filter(k=>!basic.includes(k)&&!excluded.includes(k)).join(', ')+' · 배경 후보: '+(backgrounds.join(', ') || '없음 (그룹1 상세 프로필에서 선택 가능)')+' · 미사용: '+Object.keys(profile).filter(k=>['created_at','meta','start_rapport'].includes(k)).join(', ');
    }
    return;
  }
  const group=String((activeScenario || editorScenario)?.id || '').match(/^(group1_|trust_)/);
  const enabled=$('block-enabled-profile').checked,mode=$('profile-context-mode').value;
  const keys=Object.keys(profile).filter(key=>!group || !['created_at','meta','start_rapport'].includes(key));
  const included=enabled?keys.filter(key=>!group || mode==='full' || profileFixedFields.includes(key)):[];
  const deferred=group?keys.filter(key=>['childhood_data','situations'].includes(key)):[];
  $('profile-exposure-status').textContent=!enabled?'프로필 블록 비활성 · 처음 입력에서 제외':group?(mode==='full'?'전체 제공 · 성장 경험과 상황 기록도 처음부터 입력':'선택 제공 · 성장 경험과 상황 기록은 처음 입력에서 제외'):'기존 예제 / 수동 프로필은 편집 내용을 직접 제공합니다. 범위 선택은 그룹1 프로필에 적용됩니다.';
  $('profile-exposure-fields').replaceChildren();
  for(const [title,list,cls] of [['처음 입력',included,''],['원본 DB · 해당 조회 도구를 켰을 때 사용 가능',deferred,'lookup']]){
    const heading=document.createElement('span');heading.className='exposure-label';heading.textContent=title+':';$('profile-exposure-fields').append(heading);
    for(const key of list){const chip=document.createElement('span');chip.className='profile-field-chip '+cls;chip.textContent=key;$('profile-exposure-fields').append(chip);}
    if(!list.length)$('profile-exposure-fields').append(document.createTextNode(' 없음'));
  }
}
function sourceTurns(scenario) {
  if(scenario.dialogue_options)return structuredClone(scenario.dialogue_options);
  if(scenario.dialogue_turns)return structuredClone(scenario.dialogue_turns);
  if(scenario.dialogue_format!=='messages')return scenario.dialogue.map(r=>({...r,enabled:true}));
  const turns=[];
  for(const row of scenario.dialogue){
    if(row.role==='counselor')turns.push({counselor:row.content,client:'',enabled:true});
    else if(turns.length && !turns.at(-1).client)turns.at(-1).client=row.content;
    else throw Error('초기 대화는 상담사·내담자 순서의 턴이어야 합니다.');
  }
  return turns;
}
function addDialogueTurn(turn={counselor:'',client:'',enabled:true},open=true) {
  const row=document.createElement('details');row.className='dialogue-turn';row.open=open;
  const heading=document.createElement('summary');
  const toggle=document.createElement('label');toggle.className='turn-toggle';
  const check=document.createElement('input');check.type='checkbox';check.checked=turn.enabled;
  const number=document.createElement('span');number.className='turn-number';toggle.append(check,number);
  toggle.onclick=event=>event.stopPropagation();
  const excerpt=document.createElement('span');excerpt.className='turn-excerpt';
  const badge=document.createElement('span');badge.className='turn-badge';heading.append(toggle,excerpt,badge);
  const fields=document.createElement('div');fields.className='turn-fields';
  for(const [role,name] of [['counselor','상담사 발화'],['client','내담자 발화']]){
    const label=document.createElement('label');label.className='turn-'+role+'-label';
    const caption=document.createElement('span');caption.textContent=name;
    const text=document.createElement('textarea');text.rows=2;text.dataset.role=role;text.value=turn[role];text.setAttribute('aria-label',name);
    text.oninput=()=>{if(role==='counselor')syncTarget();editorChanged();};
    label.append(caption,text);fields.append(label);
  }
  check.onchange=()=>{syncTarget();editorChanged();};
  row.append(heading,fields);$('dialogue-editor').append(row);
}
function readTurns(){return [...$('dialogue-editor').children].map(row=>({enabled:row.querySelector('input').checked,counselor:row.querySelector('[data-role=counselor]').value,client:row.querySelector('[data-role=client]').value}));}
function targetRow(){return [...$('dialogue-editor').children].filter(row=>row.querySelector('input').checked).at(-1);}
function syncTarget() {
  const rows=[...$('dialogue-editor').children],last=targetRow();
  rows.forEach((row,i)=>{
    const enabled=row.querySelector('input').checked,target=row===last;
    row.dataset.enabled=String(enabled);row.dataset.target=String(target);
    row.querySelector('.turn-number').textContent=(i+1)+'턴';
    row.querySelector('input').setAttribute('aria-label',(i+1)+'턴 활성화');
    row.querySelector('.turn-excerpt').textContent=row.querySelector('[data-role=counselor]').value || '상담사 발화를 입력하세요';
    row.querySelector('.turn-badge').textContent=target?'생성 대상':enabled?'히스토리':'비활성';
    row.querySelector('.turn-client-label span').textContent=target?'내담자 원문 · 비교용, 모델 입력 제외':'내담자 발화';
  });
  if(independent() || !activeScenario){$('message').value=last?.querySelector('[data-role=counselor]').value || '오늘 상담에서 가장 먼저 다뤄보고 싶은 일이 있나요?';$('counselor-preset').value='custom';}
}
function syncComposerTarget(){
  if(!independent() && activeScenario)return;
  const row=targetRow();if(!row)return;
  row.querySelector('[data-role=counselor]').value=$('message').value;syncTarget();
}
function setupHistoryChoices(){
  $('experiment-profile').replaceChildren(new Option('기존 연구 보고 예제','default'));
  for(const entry of historyCatalog.profiles){
    const profile=entry.histories[0].scenario.profile;
    $('experiment-profile').add(new Option(entry.label || entry.number+' · '+profile.core_belief.young_schema,entry.number));
  }
  $('experiment-profile').onchange=()=>{populateHistoryChoices();applyHistory();};
  $('history-select').onchange=applyHistory;
  populateHistoryChoices();
}
function populateHistoryChoices(){
  const entry=historyCatalog.profiles.find(p=>p.number===$('experiment-profile').value);
  $('history-select').replaceChildren();
  if(!entry)$('history-select').add(new Option('기존 기본 히스토리','default'));
  else for(const h of entry.histories)$('history-select').add(new Option(h.title,h.id));
}
function applyHistory(){
  const editedTask=$('edit-task').value,editedTaxonomy=$('edit-taxonomy').value;
  const previousStates=Object.fromEntries(Object.keys(editorScenario.states).map(key=>[key,{...editorScenario.states[key],level:$('state-'+key).value,meaning:$('meaning-'+key).value}]));
  clearConversation(false);
  const entry=historyCatalog.profiles.find(p=>p.number===$('experiment-profile').value);
  const chosen=entry?.histories.find(h=>h.id===$('history-select').value);
  editorScenario=structuredClone(chosen?.scenario || config.scenario);editorScenario.states=previousStates;
  if(chosen && entry.source_profile)editorScenario.profile=structuredClone(entry.source_profile);
  editorScenario.dialogue_options=sourceTurns(editorScenario).map(turn=>({...turn,enabled:false}));
  $('history-description').textContent=chosen?chosen.description+(entry.group==='trust_contrast'?'':' · 가상 대화 · 프로필 제공 범위는 프로필 블록에서 선택'):'기존 연구 보고 예제의 초기 5턴';
  $('lab-mode').textContent=chosen?(entry.label || '그룹1 '+entry.number)+' · '+chosen.title:'연구 보고 고민 · 가상 내담자 반응';
  setupConditions();$('edit-task').value=editedTask;$('edit-taxonomy').value=editedTaxonomy;syncConditions();
  $('lab-example').textContent=editorScenario.selected_counselor_utterance || readTurns().at(-1)?.counselor || '';updatePreview();
}
function setupEditor() {
  const scenario=activeScenario || editorScenario;
  for(const [id,key] of [['edit-task','task'],['edit-profile','profile'],['edit-taxonomy','taxonomy'],['edit-source','source_notes']]) {
    $(id).value=key==='task'?scenario[key]:pretty(scenario[key] || {});$(id).oninput=editorChanged;
  }
  $('dialogue-editor').replaceChildren();
  const turns=sourceTurns(scenario);
  turns.forEach(turn=>addDialogueTurn(turn));
  syncTarget();
}
function editorChanged() { try {syncConditions();notice();updatePreview();}catch(error){clearTimeout(timer);++previewVersion;notice('초기 입력 확인: '+error.message);} }
function selectedScenario() {
  const scenario=structuredClone(editorScenario);
  scenario.task=$('edit-task').value;
  if(!scenario.task.trim())throw Error('과제 지침을 입력하세요.');
  for(const [id,key] of [['edit-profile','profile'],['edit-taxonomy','taxonomy'],['edit-source','source_notes']]) {
    try{scenario[key]=JSON.parse($(id).value);}catch{throw Error(key+' JSON 형식을 확인하세요.');}
  }
  if(!scenario.profile || Array.isArray(scenario.profile) || typeof scenario.profile!=='object')throw Error('profile은 객체여야 합니다.');
  if(!Array.isArray(scenario.taxonomy) || !scenario.taxonomy.length)throw Error('taxonomy는 비어 있지 않은 배열이어야 합니다.');
  scenario.dialogue_format='messages';
  scenario.dialogue_turns=readTurns();
  const enabled=scenario.dialogue_turns.filter(r=>r.enabled);
  // Keep inactive examples editable without submitting them as public history.
  if(!enabled.length){scenario.dialogue_options=scenario.dialogue_turns;delete scenario.dialogue_turns;}
  else delete scenario.dialogue_options;
  if(enabled.some(r=>!r.counselor.trim()))throw Error('활성화된 턴의 상담사 발화를 입력하세요.');
  if(enabled.slice(0,-1).some(r=>!r.client.trim()))throw Error('마지막 턴 이전의 활성화된 내담자 발화를 입력하세요.');
  scenario.dialogue=enabled.slice(0,-1).flatMap(r=>[{role:'counselor',content:r.counselor},{role:'client',content:r.client}]);
  scenario.selected_counselor_utterance=enabled.at(-1)?.counselor || $('message').value;
  for(const [key,state] of Object.entries(scenario.states)) {
    state.level=$('state-'+key).value;
    state.meaning=$('meaning-'+key).value;
    if(!state.meaning.trim())throw Error('상태의 의미를 입력하세요.');
  }
  return scenario;
}
function body() {
  const selected=(!independent() && activeTools!==null)?activeTools:enabledToolNames;
  return {scenario:independent()?selectedScenario():(activeScenario || selectedScenario()),session_id:independent()?null:sessionId,...settings(),
    ...(!independent() && restoredSessionKey?{session_key:restoredSessionKey}:{}),
    ...(window.TurnConnection?{vllm_url:TurnConnection.value()}:{}),
    profile_context_mode:$('profile-context-mode').value,input_blocks:inputBlocks(),
    experiment_version:currentVersion,
    ...(q1Mode()?{client_reasoning_budgets:clientBudgets()}:{}),
    ...(stateSummaryMode()?{summary_system_prompt:$('summary_system_prompt').value}:{}),
    ...(selected!==null?{enabled_tools:[...selected]}:{}),
    system_prompt:(!independent() && activeSystem!==null)?activeSystem:$('system_prompt').value,
    messages:[...((independent() || stateSummaryMode() || q1Mode())?[]:history),{role:'user',content:$('message').value}]};
}
function syncConditions() {
  if(!editorScenario){$('experiment-version').disabled=true;return;}
  const frozen=window.Paired?.hasSession || (!independent() && !!activeScenario);
  for(const [key,state] of Object.entries(editorScenario.states)) {
    const select=$('state-'+key);
    select.disabled=busy || frozen;
    $('meaning-'+key).disabled=busy || frozen;
  }
  for(const el of document.querySelectorAll('#initial-editor textarea,#initial-editor select,#initial-editor button,#initial-editor input')){
    if(!el.closest('#block-model') || window.Paired?.hasSession)el.disabled=busy || frozen;
  }
  $('system_prompt').disabled=busy;
  $('summary_system_prompt').disabled=busy;
  $('profile-context-mode').disabled=busy || frozen;
  $('experiment-version').disabled=settingsLoading || busy || !!window.Paired?.hasSession;
  for(const id of ['temperature','top_p','max_tokens','seed'])$(id).disabled=busy || !!window.Paired?.hasSession;
  $('model-select').disabled=busy || frozen;$('thinking-mode').disabled=busy || frozen;
  updateProfileExposure();
  syncClientVersionUI();
  window.PromptEditors?.sync();
  try{$('scenario').textContent=pretty(frozen?activeScenario:selectedScenario());}catch(error){$('scenario').textContent='입력 확인 필요: '+error.message;}
  $('trial-hint').textContent=frozen?(stateSummaryMode()?'대화 이어가기: 생성된 내면상태를 다음 턴으로 이어갑니다. 초기 설정은 잠기며 현재 상태는 아래 기록에서 확인합니다.':'대화 이어가기: 첫 전송의 상태를 유지합니다. 상태를 바꾸려면 새 대화 또는 독립 실험을 선택하세요.'):'선택만으로는 생성하지 않습니다. 아래 발화를 확인·수정한 뒤 전송하세요. 독립 실험은 편집한 초기 대화에서 시작하며 이전 실험은 서버 기록에 보존됩니다.';
}
function setupConditions() {
  $('counselor-preset').replaceChildren(new Option('직접 입력','custom'));
  for(const preset of editorScenario.counselor_presets || []) $('counselor-preset').add(new Option(preset.label,preset.id));
  setupEditor();
  $('state-controls').replaceChildren();
  for(const [key,state] of Object.entries(editorScenario.states)) {
    const label=document.createElement('label');label.textContent=state.name;
    const select=document.createElement('select');select.id='state-'+key;
    for(const level of Object.keys(editorScenario.state_levels?.[key] || {[state.level]:state.meaning})) select.add(new Option(level,level));
    select.value=activeScenario?.states[key]?.level || state.level;
    const meaning=document.createElement('textarea');meaning.rows=3;meaning.id='meaning-'+key;meaning.value=activeScenario?.states[key]?.meaning || state.meaning;meaning.setAttribute('aria-label',state.name+' 의미');meaning.oninput=editorChanged;
    select.onchange=()=>{meaning.value=editorScenario.state_levels?.[key]?.[select.value] || state.meaning;editorChanged();};
    label.append(select,meaning);$('state-controls').append(label);
  }
  syncConditions();
}
async function api(path, data) {
  return LabHTTP.requestJSON(path, data);
}
function showRequest() {
  if(Journey.hasTurns || Journey.hasExternal){Journey.refresh();return;}
  const last=$('request-mode').value==='last';
  $('request').textContent=pretty(last ? lastRequest : preview) || '—';
  $('request-label').textContent=last?'최근 전송 요청 원본 · 이후 설정을 바꿔도 유지됩니다.':'다음 요청 미리보기 · 입력과 설정이 반영됩니다.';
}
function showContext(messages) {
  if(Journey.hasTurns || Journey.hasExternal){Journey.refresh();return;}
  $('context').replaceChildren();
  messages.forEach(m=>{const box=document.createElement('div');box.className='context-item';const title=document.createElement('b');title.textContent=m.role;const text=document.createElement('pre');text.textContent=m.tool_calls?pretty(m):m.content || '(입력 대기)';box.append(title,text);$('context').append(box);});
  $('context-count').textContent=`${messages.length}개 메시지`;
}
function localContext() {
  const messages=body().messages;
  const system=$('system_prompt').value;
  if(system) {
    if(config.system_role_mode==='prepend_user') messages[0]={...messages[0],content:system+'\n\n'+messages[0].content};
    else messages.unshift({role:'system',content:system});
  }
  return messages;
}
function updatePreview() {
  if(!config || busy) return;
  clearTimeout(timer); const version=++previewVersion;
  preview=null;
  try{body();showContext(localContext());}catch(error){notice(error.message);if(!Journey.hasTurns){$('request').textContent='입력 확인 필요: '+error.message;showContext([]);}return;} showRequest();
  timer=setTimeout(async()=>{try{const result=await api('/api/preview',body());if(version!==previewVersion || busy)return;preview=result.payload;Journey.showPreview(preview);showRequest();showContext(preview.messages);}catch(error){if(version===previewVersion && !busy && $('request-mode').value==='preview')$('request').textContent=error.message;}},180);
}
function setBusy(value) {
  busy=value; for(const id of [...fields,'reload','reset','send','message','check','tools-refresh','sample-request','trial-mode','counselor-preset']) $(id).disabled=value;
  $('stop').hidden=!value; if(config)syncConditions();
  document.dispatchEvent(new CustomEvent('lab-session-status',{detail:{busy:value,sessionId}}));
}
function bubble(role, content) {
  if(!history.length && !records.length) $('chat').replaceChildren();
  const el=document.createElement('div');el.className='bubble '+role;
  const turn=records.length;el.tabIndex=0;el.setAttribute('role','button');el.setAttribute('aria-label','대화 '+turn+' 실행 기록 선택');
  const select=()=>{Journey.selectTurn(turn);window.showLabResult('visual');if(records[turn-1]?.liveTrace)LabVersionViews.saved(records[turn-1].liveTrace);};el.onclick=select;el.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select();}};
  const title=document.createElement('strong');title.textContent=role;
  const text=document.createElement('div');text.textContent=content;el.append(title,text);
  let process=null;if(role==='assistant' && q1Mode()){process=document.createElement('div');process.className='paired-turn-process';el.insertBefore(process,text);}
  $('chat').append(el);return {el,text,process};
}
function mountQ1Process(record,answer){
  if(!answer.process)return;
  LabVersionViews.mountAttempt(answer.process,record.liveTrace,index=>{
    window.showLabResult('raw');
    Journey.inspectExternal({id:record.liveTrace.id,number:records.indexOf(record)+1,question:currentVersion+' 내담자 생성 과정',
      steps:record.liveTrace.steps,done:record.liveTrace.status!=='running',status:record.liveTrace.status,onSelect:()=>{}},index);
  });
}
async function reload() {
  settingsLoading=true;$('experiment-version').disabled=true;
  try { config=await api('/api/config');for(const id of fields) $(id).value=id==='system_prompt' ? config.system_prompt || '' : config.generation[id] ?? '';
    $('profile-context-mode').value=config.profile_context_mode || 'selective';
    for(const input of document.querySelectorAll('[data-block]')){input.checked=true;input.closest('.setting-block').dataset.enabled='true';}
    enabledToolNames=null;
    versionCatalog=await api('/api/versions');await versionPreferencesReady;versionDrafts={};currentVersion=versionPreferences.get('client',versionCatalog.versions,versionCatalog.default);
    $('experiment-version').replaceChildren(...versionCatalog.versions.map(v=>new Option(v.label,v.id)));
    $('experiment-version').value=currentVersion;
    const version=versionCatalog.versions.find(v=>v.id===currentVersion);
    if(version.default_reasoning_budgets)restoreClientBudgets(version.default_reasoning_budgets);
    $('system_prompt').value=version.system_prompt;$('summary_system_prompt').value=version.summary_system_prompt;
    $('summary-settings').hidden=!stateSummaryMode();$('state-memory').hidden=!stateSummaryMode();$('state-input-hint').hidden=!stateSummaryMode();
    $('lab-title').firstChild.nodeValue='상담사·내담자 실습 ';
    document.title='상담사·내담자 실습';
    $('lab-mode').textContent='연구 보고 고민 · 가상 내담자 반응';
    $('lab-description').textContent='초기 자료는 메인에게 직접 제공됩니다. 느낌·판단 이유·내담자 발화를 생성하며, 위임 여부와 역할은 모델이 선택합니다.';
    $('lab-example').textContent=config.scenario.example_counselor_utterance;
    historyCatalog=await api('/api/histories');editorScenario=structuredClone(config.scenario);
    editorScenario.dialogue_options=sourceTurns(editorScenario).map(turn=>({...turn,enabled:false}));
    setupHistoryChoices();$('history-description').textContent='기존 연구 보고 예제의 초기 5턴';setupConditions();
    $('lab-example').textContent=readTurns().at(-1)?.counselor || '';
    $('system_prompt').readOnly=false;
    selectModel(config.model);$('thinking-mode').value=config.chat_template_kwargs?.enable_thinking===false?'off':'on';$('endpoint').textContent=config.vllm_url;$('status').textContent='준비';notice();await refreshTools();settingsLoading=false;syncConditions();updatePreview();document.dispatchEvent(new Event('lab-settings-ready'));
  } catch(error) { notice(error.message);$('status').textContent='설정 오류'; }
}
$('composer').addEventListener('submit',async event=>{
  event.preventDefault();if(busy || !config)return;
  setBusy(true);
  try{await versionReady;await window.PromptEditors?.prepare(['client']);}catch(error){notice(error.message);return;}
  finally{setBusy(false);}
  syncComposerTarget();
  if(!$('message').value.trim()){notice('생성 대상 상담사 발화와 활성화된 턴을 확인하세요.');return;}
  try{body();}catch(error){notice(error.message);return;}
  notice();if(independent())clearConversation(false);activeScenario ||= selectedScenario();activeSystem ??= $('system_prompt').value;activeTools ??= enabledToolNames && [...enabledToolNames];clearTimeout(timer);++previewVersion;const request=body(),text=request.messages.at(-1).content;activeRequestId=crypto.randomUUID();request.request_id=activeRequestId;Journey.begin(request);
  // Create both bubbles before recording this attempt.
  if(!records.length) $('chat').replaceChildren();
  records.push({request:null,response:'',reasoning:'',events:[],status:'running',started_at:new Date().toISOString()});
  const record=records.at(-1);record.liveTrace=PairedTrace.create('client',activeRequestId,records.length,'basic',currentVersion);LabVersionViews.live(record.liveTrace);bubble('user',text);const answer=bubble('assistant','');
  setBusy(true);$('status').textContent='프롬프트 조회 · 생성 대기';$('live').textContent='—';$('reasoning').textContent='—';$('prompt').textContent=config.inspect_prompt===false?'config에서 프롬프트 조회를 껐습니다.':'조회 중…';$('tokens').textContent='—';$('metrics').textContent='이번 요청 토큰 사용량 대기 중';
  controller=new AbortController();let done=false,output='',reasoning='',completedHistory=null,currentCall=null;
  function handle(message) {
    PairedTrace.accept(record.liveTrace,message);LabVersionViews.refresh(record.liveTrace);
    mountQ1Process(record,answer);
    record.events.push(message);Journey.accept(message);
    if(message.type==='experiment_setup'){
      sessionId=message.session_id;record.saved_path=message.saved_path;
      $('save-status').textContent='자동 저장 중 · '+record.saved_path;
      $('status').textContent='내면상태·요약 준비 중';return;
    }
    if(message.type==='state_summary'){
      $('state-summary-text').textContent=message.summary || '첫 생성: 이전 내면 기록 없음';
      $('state-before').textContent=pretty(message.state_before);
      $('state-after').textContent='생성 중…';
      $('state-memory-status').textContent=message.previous_turn?`생성 ${message.source_turns?.[0]??message.previous_turn}~${message.previous_turn}턴의 내면 원문 → 통합 흐름 요약 → 이번 메인 입력`:'초기 상태로 첫 응답 생성';return;
    }
    if(message.type==='state_record'){
      innerRecords.push(message.record);$('state-records').textContent=pretty(innerRecords);
      $('state-after').textContent=pretty(message.record.state_after);
      $('state-memory-status').textContent=`생성 ${message.record.turn}턴 저장 완료 · ${message.saved_path}`;return;
    }
    if(message.type==='agent_event'){ $('status').textContent='서브에이전트 실행 중 · '+message.agent_name;return; }
    if(message.type==='request'){
      if(message.session_id)sessionId=message.session_id;
      if(message.saved_path)record.saved_path=message.saved_path;
      $('save-status').textContent='자동 저장 중 · '+record.saved_path;
      record.request=message.payload;lastRequest=message.payload;
      output='';reasoning='';answer.text.textContent='';$('live').textContent='—';$('reasoning').textContent='—';
      currentCall={payload:message.payload,iteration:message.iteration || 1,turn:records.length};modelCalls.push(currentCall);
      const option=document.createElement('option');option.value=String(modelCalls.length-1);option.textContent=`대화 ${records.length} · 모델 호출 ${currentCall.iteration}`;
      if(modelCalls.length===1)$('call-selector').replaceChildren();$('call-selector').append(option);$('call-selector').value=option.value;
      $('request-mode').value='last';showRequest();showContext(lastRequest.messages);
      $('prompt').textContent=config.inspect_prompt===false?'프롬프트 조회 꺼짐':'이번 호출 프롬프트 조회 중…';$('tokens').textContent='—';
    }
    if(message.type==='inspection'){
      if(currentCall)currentCall.inspection=message;record.inspection=message;$('prompt').textContent=message.error?`조회 불가 (채팅은 계속 진행): ${message.error}`:message.prompt ?? `텍스트 복원 불가: ${message.decode_error || '서버 미지원'}`;
      $('tokens').textContent=pretty(message);if(message.token_count!=null)$('metrics').textContent=`입력 ${message.token_count} tokens · 서버 context 한도 ${message.max_model_len ?? '미제공'}`;
    }
    if(message.type==='chunk'){
      $('status').textContent='응답 생성 중';const chunk=message.data;const choice=chunk.choices?.[0];
      output+=choice?.delta?.content || '';reasoning+=choice?.delta?.reasoning_content || choice?.delta?.reasoning || '';
      answer.text.textContent=currentVersion==='Q1a'?'초안 검토·선택 중…':stateSummaryMode()?'내면상태와 발화 생성 중…':output || (reasoning?'reasoning 수신 중…':'');$('live').textContent=output || '—';$('reasoning').textContent=reasoning || '—';
      if(choice?.finish_reason)record.finish_reason=choice.finish_reason;
      if(chunk.usage){record.usage=chunk.usage;$('metrics').textContent=`입력 ${chunk.usage.prompt_tokens} · 출력 ${chunk.usage.completion_tokens} · 합계 ${chunk.usage.total_tokens} tokens`;}
      $('chat').scrollTop=$('chat').scrollHeight;
    }
    if(message.type==='tool_start')$('status').textContent='도구 실행 중 · '+message.name;
    if(message.type==='error' || message.type==='cancelled')throw Error(message.error);
    if(message.type==='done'){done=true;completedHistory=message.history;if(stateSummaryMode() || currentVersion==='Q1a'){record.model_response=message.model_response;output=message.response;answer.text.textContent=output;}record.usage=message.usage;record.finish_reason=message.finish_reason;if(message.usage && Object.keys(message.usage).length)$('metrics').textContent=`이번 턴 전체 · 입력 ${message.usage.prompt_tokens ?? 0} · 출력 ${message.usage.completion_tokens ?? 0} · 합계 ${message.usage.total_tokens ?? 0} tokens`;else $('metrics').textContent='토큰 사용량: 서버 미제공'; $('save-status').textContent='서버 저장 완료 · '+record.saved_path;}
  }
  try {
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-Gemma-Request':'1'},body:JSON.stringify(request),signal:controller.signal});
    await LabHTTP.requireStream(response, '/api/chat');
    const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';
    while(true){const next=await reader.read();pending+=decoder.decode(next.value || new Uint8Array(),{stream:!next.done});let index;while((index=pending.indexOf('\n'))>=0){const line=pending.slice(0,index);pending=pending.slice(index+1);if(line.trim())handle(JSON.parse(line));}if(next.done)break;}
    if(pending.trim())handle(JSON.parse(pending));if(!done)throw Error('서버 연결이 완료 전에 끊겼습니다.');
    if(!output)throw Error('텍스트 응답이 비어 있습니다. reasoning 또는 max_tokens 설정을 확인하세요.');
    history=completedHistory || [...history,{role:'user',content:text},{role:'assistant',content:output}];if(!independent()){$('message').value='';$('counselor-preset').value='custom';}record.status='completed';$('status').textContent=record.finish_reason==='length'?'완료 · 출력 토큰 한도 도달':'완료';
  } catch(error) {
    Journey.accept({type:error.name==='AbortError'?'cancelled':'client_error',error:error.message});
    for(const box of document.querySelectorAll('.tool-step[data-pending="true"]')){box.dataset.pending='false';box.classList.add('error');box.querySelector('summary').textContent+=' · 중단/실패';box.querySelector('.tool-result').textContent='결과 확인 전 중단되었습니다. 실행 기록에서 진행 상황을 확인하세요.';}
    controller.abort();record.status=error.name==='AbortError'?'stopped':'failed';$('save-status').textContent=record.saved_path?'부분 응답·오류 기록: '+record.saved_path:'서버 저장 확인 안 됨';record.error=error.name==='AbortError'?'수신을 중단했습니다. 서버 생성 종료는 vLLM 연결 처리에 따라 지연될 수 있습니다.':error.message;
    answer.el.classList.add('failed');const note=document.createElement('div');note.className='error';note.textContent=record.error+' (미완료 대화는 컨텍스트에서 제외)';answer.el.append(note);notice(record.error);$('status').textContent='미완료 · 재전송 가능';
  } finally {record.response=output;record.reasoning=reasoning;record.finished_at=new Date().toISOString();activeRequestId=null;setBusy(false);updatePreview();Journey.refresh();$('message').focus({preventScroll:true});document.dispatchEvent(new Event('lab-sessions-changed'));}
});
$('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}});
for(const id of fields) $(id).addEventListener('input',updatePreview);
$('message').addEventListener('input',()=>{syncComposerTarget();editorChanged();});
$('request-mode').onchange=showRequest;
$('stop').onclick=async()=>{if(activeRequestId){try{await api('/api/cancel',{request_id:activeRequestId});}catch(error){notice(error.message);}}controller?.abort();};
$('reload').onclick=()=>{clearConversation();reload();};
$('check').onclick=async()=>{try{const result=await api('/api/models');const models=result.data.map(m=>m.id);notice(`연결 성공 · 모델: ${models.join(', ')}${models.includes($('model-select').value)?'':'\n선택한 모델명이 서버 목록에 없습니다.'}`);}catch(error){notice(error.message);}};
function clearConversation(clearInput=true) {
  restoredSessionKey=null;
  innerRecords=[];$('state-records').textContent='[]';for(const id of ['state-before','state-after','state-summary-text'])$(id).textContent='—';$('state-memory-status').textContent='첫 생성 전에는 내면 기록이 없습니다.';
  Journey.reset();activeScenario=null;activeSystem=null;activeTools=null;sessionId=null;modelCalls=[];$('call-selector').replaceChildren();$('tool-trace').replaceChildren();$('save-status').textContent='전송 즉시 서버에 자동 저장됩니다.';history=[];records=[];lastRequest=null;$('chat').replaceChildren();for(const id of ['live','reasoning','prompt','tokens'])$(id).textContent='—';$('metrics').textContent='토큰 사용량: 아직 응답 없음';
  if(clearInput)syncTarget();
  $('request-mode').value='preview';$('status').textContent='새 대화';notice();syncConditions();
}
$('reset').onclick=()=>{clearConversation();updatePreview();};
$('trial-mode').onchange=()=>{clearConversation(false);syncTarget();updatePreview();};
$('counselor-preset').onchange=()=>{
  const preset=editorScenario.counselor_presets?.find(p=>p.id===$('counselor-preset').value);
  if(preset){$('message').value=preset.content;syncComposerTarget();$('counselor-preset').value=preset.id;}
  editorChanged();
};
$('message').addEventListener('input',()=>{$('counselor-preset').value='custom';});

$('export').onclick=()=>{const url=URL.createObjectURL(new Blob([pretty({exported_at:new Date().toISOString(),experiment_version:currentVersion,history,records,inner_records:innerRecords})],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=currentVersion+'-'+Date.now()+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
const toolLabels={delegate_task:'서브에이전트 위임',retrieve_experience:'과거 경험 조회',inspect_current_event:'현재 사건 상세 조회',get_current_constraints:'현재 실행 여건 조회',inspect_state_basis:'상태 설정 근거 조회',retrieve_childhood:'성장 경험 담당 조회',retrieve_situation:'상황 기록 담당 조회',retrieve_inner_state:'이전 내면상태·근거 원문 조회'};
function updateToolSelection(){
  const selected=toolInfo.tools.filter(tool=>enabledToolNames.includes(tool.function.name));
  Journey.setTools({...toolInfo,enabled:selected.length>0,tools:selected});
  $('tool-status').textContent=currentVersion==='Q1a'?`${selected.length}/${toolInfo.tools.length}개 도구 · 검토 호출은 모델 선택 · 최대 ${toolInfo.max_calls}회`:`${selected.length}/${toolInfo.tools.length}개 도구 선택 · 조회 담당은 자료 선택만 수행 · 에이전트당 ${toolInfo.max_calls}회`;
  $('tool-catalog').textContent=pretty(selected);
}
async function refreshTools(){
  try{
    const version=currentVersion,result=await api('/api/tools',{experiment_version:version});
    if(currentVersion!==version)return;
    toolInfo=result;
    const names=toolInfo.tools.map(tool=>tool.function.name);
    enabledToolNames=enabledToolNames===null?(stateSummaryMode()?names.filter(n=>['retrieve_inner_state','retrieve_childhood','retrieve_situation'].includes(n)):names):enabledToolNames.filter(name=>names.includes(name));
    $('tool-options').replaceChildren();
    for(const tool of toolInfo.tools){
      const name=tool.function.name,label=document.createElement('label');label.className='tool-option';
      const check=document.createElement('input');check.type='checkbox';check.id='tool-enabled-'+name;check.checked=enabledToolNames.includes(name);
      const caption=document.createElement('span'),title=document.createElement('strong'),description=document.createElement('small');
      title.textContent=name;description.textContent=toolLabels[name] || name;caption.append(title,description);
      check.onchange=()=>{enabledToolNames=[...document.querySelectorAll('#tool-options input:checked')].map(input=>input.value);updateToolSelection();editorChanged();};
      if(q1Mode()){
        const required=versionCatalog?.versions.find(v=>v.id===currentVersion)?.required_tools || names;
        if(required.includes(name)){check.checked=true;check.disabled=true;if(!enabledToolNames.includes(name))enabledToolNames.push(name);}
        description.textContent=['Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(currentVersion)?({stage_client_update:'내면 판단·반응 계획',commit_client_utterance:'공개할 발화 고정',select_client_states:'검토할 상태 선정',update_client_states:'노트 항목 갱신·턴 완료',update_client_notes:'내담자 노트 항목 갱신',update_turn_plan:'이번 턴 작업 계획·수행 결과·재계획',update_trajectory_progress:'실제 궤적 진행·근거 기록',retrieve_client_memory:'현재 노트·변경 이력·과거 원문 조회 · 필요할 때'})[name]:currentVersion==='Q2'?'이번 appraisal·반응 방향 기록 · 상태 노트는 발화 후 갱신':currentVersion==='Q1a'?({stage_client_update:'발화·appraisal·내면 노트 후보 저장 · 필수',review_client_draft:'검토 서브에이전트 · 선택적 호출',finalize_client_draft:'후보 선택 후 종료 · 필수'})[name]:'이번 appraisal와 내면 노트 변경안 기록 · 필수';
      }
      check.value=name;label.append(check,caption);$('tool-options').append(label);
    }
    updateToolSelection();syncConditions();updatePreview();
  }catch(error){$('tool-status').textContent='연결 실패: '+error.message;$('tool-catalog').textContent='서버의 도구 설정을 확인하세요.';}
}
function showToolEvent(event,turn){
  const key=`tool-${turn}-${event.call_id}`;let box=document.getElementById(key);
  if(!box){box=document.createElement('details');box.id=key;box.className='tool-step';box.open=true;box.dataset.pending='true';const title=document.createElement('summary');const args=document.createElement('pre');args.className='tool-args';const result=document.createElement('pre');result.className='tool-result';box.append(title,args,result);$('tool-trace').append(box);}
  box.querySelector('summary').textContent=`대화 ${turn} · ${event.name} · ${event.type==='tool_start'?'실행 중':event.is_error?'오류':'완료'}`;
  if(event.type==='tool_start'){box.querySelector('.tool-args').textContent=event.arguments;$('status').textContent='도구 실행 중 · '+event.name;}
  else {box.dataset.pending='false';box.querySelector('.tool-result').textContent=event.model_content;box.classList.toggle('error',event.is_error);}
}
$('tools-refresh').onclick=refreshTools;
$('call-selector').onchange=()=>{const item=modelCalls[Number($('call-selector').value)];if(!item)return;Journey.selectCall(item.turn,item.iteration);lastRequest=item.payload;$('request-mode').value='last';showRequest();$('prompt').textContent=item.inspection?.prompt || item.inspection?.error || '조회 결과 없음';$('tokens').textContent=pretty(item.inspection) || '—';};
$('sample-request').onclick=()=>{$('message').value=$('lab-example').textContent;syncComposerTarget();editorChanged();$('message').focus({preventScroll:true});};
$('add-turn').onclick=()=>{addDialogueTurn(undefined,true);syncTarget();editorChanged();};
$('summary_system_prompt').oninput=updatePreview;
for(const scope of ['main','subagent']){
  $('client-'+scope+'-budget-enabled').onchange=()=>{syncClientVersionUI();updatePreview();};
  $('client-'+scope+'-budget').oninput=updatePreview;
}
document.addEventListener('lab-prompt-source',event=>{
  const value=event.detail;if(value.role!=='client' || !['system','summary'].includes(value.kind))return;
  const version=versionCatalog?.versions.find(row=>row.id===value.version);
  if(version)version[value.kind==='summary'?'summary_system_prompt':'system_prompt']=value.content;
});
document.addEventListener('lab-prompt-applied',event=>{
  const value=event.detail;if(value.role!=='client' || !['system','summary'].includes(value.kind))return;
  const version=versionCatalog?.versions.find(row=>row.id===value.version);
  if(version)version[value.kind==='summary'?'summary_system_prompt':'system_prompt']=value.content;
  if(versionDrafts[value.version])delete versionDrafts[value.version][value.kind==='summary'?'summary':'system'];
  if(value.version==='basic' && value.kind==='system')config.system_prompt=value.content;
});
$('profile-context-mode').onchange=editorChanged;
$('model-select').onchange=()=>{selectModel($('model-select').value);updatePreview();};
$('thinking-mode').onchange=updatePreview;
for(const input of document.querySelectorAll('[data-block]'))input.addEventListener('change',editorChanged);
$('experiment-version').onchange=async()=>{
  if(busy || settingsLoading)return;
  // Prompt drafts belong to PromptEditors; these values may be frozen session
  // settings rather than user edits.
  versionDrafts[currentVersion]={tools:enabledToolNames && [...enabledToolNames]};
  currentVersion=$('experiment-version').value;
  versionPreferences.set('client',currentVersion);
  clearConversation(false);
  const version=versionCatalog.versions.find(v=>v.id===currentVersion),draft=versionDrafts[currentVersion];
  if(!draft && version.default_reasoning_budgets)restoreClientBudgets(version.default_reasoning_budgets);
  $('system_prompt').value=version.system_prompt;
  $('summary_system_prompt').value=version.summary_system_prompt;
  enabledToolNames=draft?.tools ?? null;
  $('summary-settings').hidden=!stateSummaryMode();$('state-memory').hidden=!stateSummaryMode();$('state-input-hint').hidden=!stateSummaryMode();
  $('lab-description').textContent=version.description;
  await (versionReady=refreshTools());syncTarget();updatePreview();
};
reload();
