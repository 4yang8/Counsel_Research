'use strict';
(() => {
  const $=id=>document.getElementById(id),editors=[];
  const selected=role=>role==='counselor'?$('counselor-version').value:currentVersion;
  const locked=()=>busy || !!window.Paired?.running;
  const subagents=()=>window.Paired?.counselorSpec?.subagent_prompts || [];
  const clientSubagents=()=>versionCatalog?.versions.find(row=>row.id===currentVersion)?.subagent_prompts || [];
  let clientOptionsKey='';
  let subagentOptionsKey='';
  function labelSubagent(){
    const label=subagents().find(item=>item.kind===$('counselor-subagent-select').value)?.label || '서브에이전트';
    $('counselor-subagent-prompt-label').firstChild.nodeValue=label+' 지침';
    $('counselor-subagent-prompt').setAttribute('aria-label',label+' 프롬프트');
  }
  function syncSubagents(){
    const clientDefinitions=clientSubagents(),clientIdentity=JSON.stringify([currentVersion,clientDefinitions]);
    $('block-client-subagent').hidden=!clientDefinitions.length;
    $('client-subagent-picker').hidden=clientDefinitions.length<2;
    if(clientIdentity!==clientOptionsKey){clientOptionsKey=clientIdentity;$('client-subagent-select').replaceChildren(...clientDefinitions.map(item=>new Option(item.label,item.kind)));}
    const clientLabel=clientDefinitions.find(item=>item.kind===$('client-subagent-select').value)?.label || '서브에이전트';
    $('client-subagent-prompt-label').firstChild.nodeValue=clientLabel+' 지침';
    $('client-subagent-prompt').setAttribute('aria-label',clientLabel+' 프롬프트');
    const definitions=subagents(),version=selected('counselor'),identity=JSON.stringify([version,definitions]);
    $('block-counselor-subagent').hidden=!definitions.length;
    $('counselor-subagent-picker').hidden=definitions.length<2;
    if(identity!==subagentOptionsKey){
      subagentOptionsKey=identity;
      $('counselor-subagent-select').replaceChildren(...definitions.map(item=>new Option(item.label,item.kind)));
      labelSubagent();
    }
  }
  function target(editor){
    const version=selected(editor.role);
    if(editor.skill)return version==='Q3sk'?{role:'client',version,kind:'skill_notes'}:null;
    if(editor.kind==='trajectory')return ['Q3','Q3a','Q3b','Q3sk','Q3n','Q3sb','Q3x'].includes(version)?{role:'client',version,kind:'trajectory'}:null;
    if(editor.subagent){
      const kind=$(editor.role+'-subagent-select').value;
      return (editor.role==='client'?clientSubagents():subagents()).some(item=>item.kind===kind)?{role:editor.role,version,kind:editor.userTemplate?kind+'_user':kind}:null;
    }
    if(editor.role==='counselor' && version==='C4' || ['summary','summary_user'].includes(editor.kind) && version!=='state_summary')return null;
    return {role:editor.role,version,kind:editor.kind};
  }
  const key=value=>value?`${value.role}/${value.version}/${value.kind}`:'';
  const frozen=()=>!!window.Paired?.hasSession || (!independent() && !!activeScenario);
  async function read(value){return api('/api/prompts/source?'+new URLSearchParams(value));}
  function paint(editor){
    const changed=editor.meta && editor.input.value!==editor.meta.content,blocked=locked() || editor.pending || editor.loading;
    editor.actions.hidden=!editor.target;
    if(editor.wrapper)editor.wrapper.hidden=!editor.target;
    editor.input.disabled=blocked;
    editor.save.disabled=blocked || !changed || !editor.meta;
    editor.undo.disabled=blocked || !changed || !editor.meta;
    if(editor.subagent)$(editor.role+'-subagent-select').disabled=blocked;
    if(editor.error){editor.status.textContent=editor.error;editor.status.dataset.error='true';return;}
    editor.status.dataset.error='false';
    editor.status.textContent=editor.meta?
      `${editor.meta.path}${editor.meta.field?' · '+editor.meta.field:''} · ${changed?(editor.touched?'미저장 변경':!editor.subagent && !editor.userTemplate && frozen()?'저장된 세션의 지침 · 현재 파일과 다름':'표시 내용이 원본과 다름'):editor.saved?'파일 저장 완료':'파일과 일치'} · ${editor.skill?'저장은 다음 내담자 생성부터 적용 · 실제 사용한 원문은 턴 기록에 보존':editor.kind==='trajectory'?'저장은 다음 내담자 생성부터 적용':editor.userTemplate?'저장은 다음 생성부터 적용 · 변수는 호출 시 최신 데이터로 대체':editor.subagent?'저장은 다음 서브에이전트 호출부터 적용':'저장은 새 대화부터 적용'}`:
      '원본 파일을 불러오는 중…';
  }
  async function load(editor,value){
    const identity=key(value),serial=++editor.loadSerial;
    editor.target=value;editor.key=identity;editor.meta=null;editor.error='';editor.saved=false;editor.touched=false;editor.loading=!!value;
    if(editor.subagent || editor.userTemplate)editor.input.value=editor.drafts.get(identity) ?? '';
    paint(editor);
    if(!value)return;
    try{const result=await read(value);if(editor.key===identity && editor.loadSerial===serial){
      editor.meta=result;editor.loading=false;
      if(editor.variables){
        editor.variables.replaceChildren(...Object.entries(result.variables || {}).map(([name,description])=>{
          const row=document.createElement('div'),code=document.createElement('code');code.textContent='{{'+name+'}}';
          row.append(code,document.createTextNode(' — '+description));return row;
        }));
      }
      // Only real input events create drafts. Restoring a session must never
      // turn its frozen prompt into the default for a future session.
      if(editor.subagent || editor.userTemplate || !frozen())editor.input.value=editor.drafts.get(identity) ?? result.content;
      editor.touched=editor.drafts.has(identity) && editor.input.value===editor.drafts.get(identity);
      document.dispatchEvent(new CustomEvent('lab-prompt-source',{detail:result}));
      paint(editor);
    }}
    catch(error){if(editor.key===identity && editor.loadSerial===serial){editor.loading=false;editor.error=error.message;paint(editor);}}
  }
  function sync(refresh=false){
    syncSubagents();
    return Promise.all(editors.map(editor=>{
      const value=target(editor);
      if(refresh || key(value)!==editor.key)editor.ready=load(editor,value);
      else paint(editor);
      return editor.ready;
    }));
  }
  async function prepare(roles=['client','counselor']){
    if(frozen())return;
    await sync(true);
    const failed=editors.find(editor=>!editor.subagent && editor.target && roles.includes(editor.role) && (!editor.meta || editor.error));
    if(failed)throw Error('최신 프롬프트를 불러오지 못했습니다: '+(failed.error || failed.input.id));
    updatePreview();
  }
  async function perform(editor,save){
    if(!editor.meta || locked() || editor.pending)return;
    const value=editor.target,identity=editor.key,content=editor.input.value;editor.pending=true;editor.error='';paint(editor);
    try{
      const result=save?await api('/api/prompts/save',{...value,revision:editor.meta.revision,content}):await read(value);
      if(editor.key!==identity)return;
      editor.meta=result;editor.input.value=result.content;
      editor.drafts?.delete(identity);
      editor.input.dispatchEvent(new Event('input',{bubbles:true}));
      editor.touched=false;editor.saved=save;
      document.dispatchEvent(new CustomEvent('lab-prompt-applied',{detail:result}));
      if(editor.userTemplate && save)updatePreview();
    }catch(error){if(editor.key===identity)editor.error=error.message;}
    finally{editor.pending=false;paint(editor);}
  }
  function install(role,kind,inputId,actionsId,undoId,options={}){
    const input=$(inputId),actions=$(actionsId) || document.createElement('div');
    actions.id=actionsId;actions.classList.add('prompt-file-actions');
    if(!actions.isConnected)input.closest('label').after(actions);
    const undo=$(undoId) || document.createElement('button'),save=document.createElement('button'),status=document.createElement('small');
    undo.id=undoId;undo.type=save.type='button';undo.textContent='실행취소';save.textContent='저장';
    save.id=inputId+'-save';status.id=inputId+'-file-status';status.className='prompt-file-status';status.setAttribute('role','status');
    if(!undo.isConnected)actions.append(undo);actions.insertBefore(save,undo.nextSibling);actions.append(status);
    const editor={role,kind,input,actions,undo,save,status,pending:false,loading:false,loadSerial:0,key:null,meta:null,drafts:new Map(),...options};editors.push(editor);
    input.addEventListener('input',()=>{
      if(editor.key && editor.meta){
        if(input.value===editor.meta.content)editor.drafts.delete(editor.key);
        else editor.drafts.set(editor.key,input.value);
      }
      editor.error='';editor.saved=false;editor.touched=true;paint(editor);
    });
    undo.onclick=()=>perform(editor,false);save.onclick=()=>perform(editor,true);
  }
  install('counselor','system','counselor-system-prompt','counselor-prompt-actions','counselor-prompt-default');
  install('client','system','system_prompt','client-prompt-actions','client-prompt-undo');
  install('client','summary','summary_system_prompt','summary-prompt-actions','summary-prompt-undo');
  install('counselor',null,'counselor-subagent-prompt','counselor-subagent-prompt-actions','counselor-subagent-prompt-undo',{subagent:true,drafts:new Map()});
  install('client',null,'client-subagent-prompt','client-subagent-prompt-actions','client-subagent-prompt-undo',{subagent:true,drafts:new Map()});
  // Keep each template directly below its matching system editor, including
  // version-specific subagents. Templates are live files, not session snapshots.
  function installTemplate(role,kind,systemId,subagent=false){
    const system=editors.find(editor=>editor.input.id===systemId);
    const wrapper=document.createElement('section'),label=document.createElement('label'),input=document.createElement('textarea');
    wrapper.className='user-template-editor';wrapper.hidden=true;
    input.id=systemId+'-user-template';input.rows=10;input.spellcheck=false;
    input.setAttribute('aria-label','User prompt 템플릿');
    label.append(document.createTextNode('User prompt · 입력 템플릿'),input);
    const hint=document.createElement('p');hint.className='hint';
    hint.textContent='{{변수명}}은 호출 시 실제 데이터로 대체됩니다. 아래 저장 버튼으로 원본 파일을 수정합니다. 실제 완성된 입력은 Raw 컨텍스트에서 확인하세요.';
    const help=document.createElement('details'),summary=document.createElement('summary'),variables=document.createElement('div');
    summary.textContent='사용 가능한 변수';variables.className='template-variables';help.append(summary,variables);
    wrapper.append(label,hint,help);system.actions.after(wrapper);
    install(role,kind,input.id,input.id+'-actions',input.id+'-undo',{subagent,userTemplate:true,wrapper,variables});
  }
  installTemplate('client','user','system_prompt');
  const trajectoryWrapper=document.createElement('section'),trajectoryLabel=document.createElement('label'),trajectoryInput=document.createElement('textarea');
  trajectoryWrapper.id='q3-trajectory-editor';trajectoryWrapper.className='user-template-editor';trajectoryWrapper.hidden=true;
  trajectoryInput.id='client-trajectory';trajectoryInput.rows=12;trajectoryInput.spellcheck=false;
  trajectoryLabel.append(document.createTextNode('사전 변화 궤적'),trajectoryInput);
  const trajectoryHint=document.createElement('p');trajectoryHint.className='hint';trajectoryHint.textContent='신뢰 형성의 방향과 단계별 의미입니다. 저장하면 다음 내담자 생성부터 적용됩니다. 모델은 이 파일을 자동 수정하지 않습니다. 실제 사용한 원문은 각 턴 기록에 남습니다.';
  trajectoryWrapper.append(trajectoryLabel,trajectoryHint);document.querySelector('#system_prompt-user-template').closest('section').after(trajectoryWrapper);
  install('client','trajectory',trajectoryInput.id,'client-trajectory-actions','client-trajectory-undo',{userTemplate:true,wrapper:trajectoryWrapper});
  const skillWrapper=document.createElement('section'),skillLabel=document.createElement('label'),skillInput=document.createElement('textarea');
  skillWrapper.id='q3sk-skill-editor';skillWrapper.className='user-template-editor';skillWrapper.hidden=true;
  skillInput.id='client-note-skill';skillInput.rows=12;skillInput.spellcheck=false;
  skillInput.setAttribute('aria-label','내담자 노트 작성 스킬');
  skillLabel.append(document.createTextNode('내담자 노트 작성 스킬 · SKILL.md'),skillInput);
  const skillHint=document.createElement('p');skillHint.className='hint';skillHint.textContent='반응 생성 중에는 이름·설명만 제공하고, 발화 확정 후 상세 지침을 읽습니다. 한 턴 안에서는 같은 원문을 사용합니다.';
  skillWrapper.append(skillLabel,skillHint);trajectoryWrapper.after(skillWrapper);
  install('client','skill_notes',skillInput.id,'client-note-skill-actions','client-note-skill-undo',{skill:true,userTemplate:true,wrapper:skillWrapper});
  installTemplate('counselor','user','counselor-system-prompt');
  installTemplate('client','summary_user','summary_system_prompt');
  installTemplate('client',null,'client-subagent-prompt',true);
  installTemplate('counselor',null,'counselor-subagent-prompt',true);
  $('client-subagent-select').addEventListener('change',()=>sync());
  $('counselor-subagent-select').addEventListener('change',()=>{labelSubagent();sync();});
  let sessionKey=window.Paired?.sessionId;
  document.addEventListener('lab-session-status',()=>{const next=window.Paired?.sessionId;sync(next!==sessionKey);sessionKey=next;});
  document.addEventListener('lab-settings-ready',()=>sync(true));
  $('counselor-version').addEventListener('change',()=>sync());$('experiment-version').addEventListener('change',()=>sync());
  window.PromptEditors={sync,prepare};sync();
})();
