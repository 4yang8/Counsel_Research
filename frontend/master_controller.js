'use strict';
// The workspace schedules existing pane runtimes; it never runs a second generator.
(() => {
  const $=id=>document.getElementById(id),workspace=window.ExperimentWorkspace,key='counseling-master-v1';
  const clone=value=>structuredClone(value),labels={waiting:'대기',preparing:'조건 적용 중',running:'실행 중',paused:'일시정지',completed:'완료',error:'오류',stopped:'중단',interrupted:'새로고침으로 중단',applied:'조건 적용 완료'};
  let saved={};try{saved=JSON.parse(localStorage.getItem(key)) || {};}catch{}
  let common=saved.common || null,selected=saved.selected || {},batch=saved.batch || null,phase='idle',owner=null,operation=false;
  const rows=new Map();let rowKey='';
  if(batch && ['preparing','running','paused','stopping'].includes(batch.phase)){
    batch.phase='interrupted';for(const row of batch.rows)if(['waiting','preparing','running','paused'].includes(row.status))row.status='interrupted';
  }
  $('master-body').innerHTML=`
    <div class="master-heading"><strong>버전은 탭별로 유지 · 나머지 조건은 공통 적용</strong><button id="master-load">현재 탭 조건 불러오기</button></div>
    <p id="master-source" class="master-hint"></p>
    <fieldset id="master-settings"><legend>공통 모델 · 실행 설정</legend><div class="master-fields">
      <label class="master-model">모델<select id="master-model"></select></label>
      <label class="master-endpoint">모델 주소<input id="master-endpoint" type="text" placeholder="http://127.0.0.1:18001/v1" spellcheck="false"></label>
      <label>생성 발화 수<input id="master-count" type="number" min="1" max="100" step="1" value="20"></label>
      <label>실행 순서<select id="master-schedule"><option value="sequential">순차 실행</option><option value="parallel">선택 탭 동시 실행</option></select></label>
    </div><details class="master-generation"><summary>생성 옵션</summary><div class="master-fields">
      <label>Temperature<input id="master-temperature" type="number" min="0" max="2" step="0.05"></label>
      <label>Top P<input id="master-top-p" type="number" min="0.000001" max="1" step="0.05"></label>
      <label>Max tokens<input id="master-max-tokens" type="number" min="1" max="1000000" step="1"></label>
      <label>Seed<input id="master-seed" type="number" step="1" placeholder="미지정"></label>
      <label>Reasoning<select id="master-thinking"><option value="on">켜기</option><option value="off">끄기</option><option value="default">서버 기본</option></select></label>
    </div></details></fieldset>
    <div class="master-target-heading"><strong>대상 탭</strong><button id="master-select-all">전체 선택</button><button id="master-select-none">선택 해제</button></div>
    <div id="master-targets" role="group" aria-label="대조실험 대상 탭"></div>
    <p class="master-hint">발화 수는 상담사·내담자 합계입니다. 새 대화 실행은 이전 기록을 채팅 내역에 남기고 시작합니다. 이어서 실행은 기존 조건을 유지하고 발화 수와 모델 주소만 적용합니다.</p>
    <div class="master-actions"><button id="master-apply">설정 일괄 적용</button><button id="master-start" class="master-primary">공통 조건으로 새 대화 실행</button><button id="master-continue">이어서 실행</button><button id="master-pause" hidden>전체 일시정지</button><button id="master-resume" hidden>계속 실행</button><button id="master-stop" hidden>전체 중단</button></div>
    <p id="master-status" role="status" aria-live="polite"></p>`;
  const fields={model:'master-model',endpoint:'master-endpoint',count:'master-count',schedule:'master-schedule',temperature:'master-temperature',top_p:'master-top-p',max_tokens:'master-max-tokens',seed:'master-seed',thinking:'master-thinking'};
  const tabs=()=>workspace.tabs(),active=()=>tabs().find(t=>t.id===workspace.activeId());
  const locked=()=>operation || ['running','paused','stopping','preparing'].includes(phase);
  const setStatus=text=>{$('master-status').textContent=text;};
  function store(){
    try{localStorage.setItem(key,JSON.stringify({common,selected,batch,open:$('master-controller').open,fields:Object.fromEntries(Object.entries(fields).map(([k,id])=>[k,$(id).value]))}));}catch{setStatus('브라우저에 컨트롤러 설정을 저장하지 못했습니다.');}
  }
  function normalizeEndpoint(text){
    const url=new URL(text.includes('://')?text:'http://'+text);
    if(!['http:','https:'].includes(url.protocol)||!url.hostname||url.username||url.password||url.search||url.hash||!['','/','/v1','/v1/'].includes(url.pathname)||url.port==='0'||/\s/.test(text))throw Error('모델 주소는 http(s)://호스트:포트 또는 /v1 형식으로 입력하세요.');
    return url.origin+'/v1';
  }
  function modelOptions(){
    const frame=document.getElementById('pane-'+workspace.activeId());
    const source=frame?.contentDocument?.querySelector('#model-select');if(!source)return;
    const chosen=$('master-model').value || saved.fields?.model || common?.values['model-select'].value;
    const choices=[...source.options].map(o=>[o.value,o.text]);
    if(JSON.stringify(choices)!==$('master-model').dataset.choices){
      $('master-model').replaceChildren(...choices.map(([value,label])=>new Option(label,value)));
      $('master-model').dataset.choices=JSON.stringify(choices);
      if(choices.some(([value])=>value===chosen))$('master-model').value=chosen;
    }
  }
  function writeCommon(value){
    common=clone(value);modelOptions();
    for(const [field,input] of Object.entries({model:'model-select',thinking:'thinking-mode',temperature:'temperature',top_p:'top_p',max_tokens:'max_tokens',seed:'seed'}))$(fields[field]).value=value.values[input].value;
    $('master-endpoint').value=value.endpoint;
  }
  function readCommon(){
    if(!common)throw Error('준비된 탭에서 공통 조건을 불러오세요.');
    const value=clone(common);value.endpoint=normalizeEndpoint($('master-endpoint').value.trim());
    for(const [field,input] of Object.entries({model:'model-select',thinking:'thinking-mode',temperature:'temperature',top_p:'top_p',max_tokens:'max_tokens',seed:'seed'}))value.values[input].value=$(fields[field]).value;
    const v=value.values;
    for(const [name,low,high] of [['temperature',0,2],['top_p',0.000001,1],['max_tokens',1,1000000]]){
      const n=Number(v[name].value);if(!v[name].value.trim()||!Number.isFinite(n)||n<low||n>high||(name==='max_tokens'&&!Number.isInteger(n)))throw Error(name+' 범위를 확인하세요.');
    }
    if(v.seed.value!==''&&!Number.isInteger(Number(v.seed.value)))throw Error('Seed는 정수 또는 빈 값이어야 합니다.');
    if(!v['model-select'].value)throw Error('모델을 선택하세요.');
    return value;
  }
  function count(){const n=Number($('master-count').value);if(!Number.isInteger(n)||n<1||n>100)throw Error('생성 발화 수는 1~100 정수로 입력하세요.');return n;}
  function targets(){const list=tabs().filter(t=>selected[t.id]!==false);if(!list.length)throw Error('대상 탭을 선택하세요.');return list;}
  const stable=value=>JSON.stringify(value,(_,v)=>v && !Array.isArray(v) && typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
  function differences(current,wanted){
    const groups=value=>({모델:[value.values['model-select'],value.values['thinking-mode']],주소:value.endpoint,
      '생성 옵션':['temperature','top_p','max_tokens','seed'].map(k=>value.values[k]),
      '프로필·초기 자료':value.scenario,'초기 발화':value.values.message,상담사:[value.counselorVersion,value.counselorPrompt],
      '입력·추론 설정':Object.fromEntries(Object.entries(value.values).filter(([k])=>k.includes('budget')||k.includes('block-enabled')||k==='profile-context-mode'))});
    const a=groups(current),b=groups(wanted);return Object.keys(a).filter(k=>stable(a[k])!==stable(b[k]));
  }
  function update(){
    const list=tabs();modelOptions();
    if(!common && active()?.status?.ready){try{writeCommon(active().api.common());store();}catch{}}
    const signature=list.map(t=>t.id).join(',');
    if(signature!==rowKey){
      rowKey=signature;$('master-targets').replaceChildren();rows.clear();
      for(const tab of list){
        const row=document.createElement('div');row.className='master-target';row.dataset.tab=tab.id;
        const check=document.createElement('input');check.type='checkbox';check.checked=selected[tab.id]!==false;check.setAttribute('aria-label','대상 탭 선택');check.onchange=()=>{selected[tab.id]=check.checked;store();update();};
        const name=document.createElement('button');name.className='master-target-name';name.onclick=()=>workspace.activate(tab.id);
        const detail=document.createElement('span');detail.className='master-target-detail';
        const progress=document.createElement('span');progress.className='master-target-progress';
        row.append(check,name,detail,progress);$('master-targets').append(row);rows.set(tab.id,{row,check,name,detail,progress});
      }
    }
    let wanted;try{wanted=readCommon();}catch{}
    for(const tab of list){
      const ui=rows.get(tab.id),entry=batch?.rows.find(r=>r.id===tab.id);
      ui.name.textContent=tab.status?.label || tab.label;ui.check.disabled=locked();
      let delta=[];try{if(wanted&&tab.status?.ready)delta=differences(tab.api.common(),wanted);}catch{}
      ui.detail.textContent=!tab.status?.ready?'탭 준비 중':tab.status?.busy?'생성 중':delta.length?'조건 차이: '+delta.join(' · '):'공통 조건 일치';
      if(entry && entry.status==='running')entry.generated=Math.max(0,(tab.status?.progress?.steps || 0)-entry.baseline);
      ui.progress.textContent=entry?(labels[entry.status] || entry.status)+(entry.goal?' · '+entry.generated+'/'+entry.goal:'')+(entry.error?' · '+entry.error:''):tab.status?.sessionId?'기존 대화 · '+tab.status.progress.steps+'개 발화':'새 대화';
      ui.row.dataset.status=entry?.status || '';
    }
    const busy=locked();$('master-settings').disabled=busy;$('master-load').disabled=busy||!active()?.status?.ready;
    for(const id of ['master-apply','master-start','master-continue','master-select-all','master-select-none'])$(id).disabled=busy||!common;
    $('master-pause').hidden=phase!=='running';$('master-resume').hidden=phase!=='paused';$('master-stop').hidden=!['running','paused','preparing','stopping'].includes(phase);$('master-stop').disabled=phase==='stopping';
    const done=batch?.rows.filter(r=>r.status==='completed').length || 0;
    $('master-summary').textContent=phase==='running'?'실행 중':phase==='paused'?'일시정지':batch?done+'/'+batch.rows.length+'개 완료':'';
    if(common)$('master-source').textContent='공통 원본: '+(common.scenario.profile?.profile_id || common.profileId || '수동 프로필')+' · 상담사 '+common.counselorVersion+' · 초기 상담사 발화: '+common.values.message.value.slice(0,80)+' · 입력 범위와 추론 한도도 함께 적용';
    workspace.refresh();
  }
  function release(){if(owner)for(const tab of tabs())tab.api?.release(owner);workspace.refresh();}
  async function prepare(list,value,fresh){
    // Validate every target before replacing any view or touching existing sessions.
    await Promise.all(list.map(t=>t.api.validateCommon(value,{fresh,owner})));
    for(const tab of list){if(phase==='stopping')throw Error('실행 준비 중 중단했습니다.');await tab.api.applyCommon(value,{fresh,owner});}
  }
  function checkTargets(list){for(const t of list)if(!t.status?.ready||t.status.busy||t.status.reserved)throw Error('준비·실행 중인 탭이 선택되어 있습니다. 완료 후 실행하세요.');}
  async function launch(fresh){
    if(locked())return;
    let list,value,n;try{list=targets();checkTargets(list);value=fresh?readCommon():{...clone(common),endpoint:normalizeEndpoint($('master-endpoint').value.trim())};n=count();
      if(!fresh && list.some(t=>!t.status.sessionId||t.status.progress.manual))throw Error('이어서 실행은 양쪽 역할이 모델인 기존 대화에서 사용할 수 있습니다.');
    }catch(error){setStatus(error.message);return;}
    owner=crypto.randomUUID();phase='preparing';operation=true;
    batch={id:owner,phase,mode:fresh?'fresh':'continue',schedule:$('master-schedule').value,created_at:new Date().toISOString(),common:clone(value),rows:list.map(t=>({id:t.id,label:t.status.label,status:'waiting',generated:0,goal:n,baseline:t.status.progress.steps,sessionId:t.status.sessionId,error:''}))};
    try{
      for(const t of list)t.api.reserve(owner);
      setStatus('선택한 '+list.length+'개 탭의 조건을 확인합니다.');update();store();
      if(fresh)await prepare(list,value,true);
      else for(const t of list){t.api.setControlledEndpoint(value.endpoint,owner);}
      if(phase==='stopping')throw Error('실행 준비 중 중단했습니다.');
      for(const row of batch.rows){const api=list.find(t=>t.id===row.id).api;row.baseline=api.status().progress.steps;row.conditions=api.common();}
      phase='running';batch.phase=phase;operation=false;store();update();await drain();
    }catch(error){
      const stopped=phase==='stopping';for(const row of batch.rows)if(['waiting','preparing'].includes(row.status)){row.status=stopped?'stopped':'error';row.error=stopped?'':error.message;}
      phase='idle';batch.phase=stopped?'stopped':'error';operation=false;release();setStatus(error.message);store();update();
    }
  }
  async function drain(){
    const thisBatch=batch;
    async function worker(){
      while(phase==='running' && batch===thisBatch){
        const row=batch.rows.find(r=>r.status==='waiting');if(!row)return;
        const tab=tabs().find(t=>t.id===row.id);row.status='running';update();store();
        try{
          const result=await tab.api.startControlled(owner,row.goal-row.generated,{batch_id:batch.id,schedule:batch.schedule,requested_utterances:row.goal});
          const info=tab.api.status();row.generated=Math.max(0,info.progress.steps-row.baseline);row.sessionId=info.sessionId;
          row.status=row.generated>=row.goal?'completed':phase==='stopping'||result.status==='stopped'?'stopped':result.status==='paused'?'paused':'error';
          row.error=row.status==='error'?(result.error || '설정한 발화 수에 도달하지 못했습니다.'):'';
        }catch(error){row.status=phase==='stopping'?'stopped':'error';row.error=error.message;}
        if(['completed','error','stopped'].includes(row.status))tab?.api.release(owner);
        store();update();
      }
    }
    await Promise.all(Array.from({length:batch.schedule==='parallel'?batch.rows.length:1},()=>worker()));
    if(phase==='paused')setStatus('진행 중이던 발화를 마친 뒤 일시정지했습니다. 계속 실행하면 남은 발화만 생성합니다.');
    else{
      const stopped=phase==='stopping';for(const row of batch.rows)if(row.status==='waiting')row.status='stopped';
      phase='idle';batch.phase=stopped?'stopped':'finished';release();
      const done=batch.rows.filter(r=>r.status==='completed').length,failed=batch.rows.filter(r=>r.status==='error').length;
      setStatus((stopped?'중단':'실행 종료')+' · 완료 '+done+' / '+batch.rows.length+'개'+(failed?' · 오류 '+failed+'개':'')+' · 기록은 각 탭과 채팅 내역에 저장됩니다.');
    }
    store();update();
  }
  $('master-load').onclick=()=>{try{writeCommon(active().api.common());setStatus('현재 탭의 공통 조건을 불러왔습니다.');store();update();}catch(error){setStatus(error.message);}};
  $('master-select-all').onclick=()=>{for(const t of tabs())selected[t.id]=true;for(const [id,ui] of rows)ui.check.checked=true;store();update();};
  $('master-select-none').onclick=()=>{for(const t of tabs())selected[t.id]=false;for(const ui of rows.values())ui.check.checked=false;store();update();};
  $('master-apply').onclick=async()=>{
    if(locked())return;let list;try{list=targets();checkTargets(list);const value=readCommon();owner=crypto.randomUUID();operation=true;for(const t of list)t.api.reserve(owner);update();await prepare(list,value,false);setStatus('선택한 '+list.length+'개 탭에 공통 조건을 적용했습니다.');}
    catch(error){setStatus(error.message);}finally{operation=false;release();store();update();}
  };
  $('master-start').onclick=()=>launch(true);$('master-continue').onclick=()=>launch(false);
  $('master-pause').onclick=()=>{if(phase!=='running')return;phase='paused';batch.phase=phase;for(const row of batch.rows)if(row.status==='running')tabs().find(t=>t.id===row.id)?.api.pause();setStatus('현재 발화가 완료되면 일시정지합니다.');store();update();};
  $('master-resume').onclick=async()=>{
    if(phase!=='paused')return;
    if(batch.rows.some(r=>r.status==='running')){setStatus('현재 발화가 완료될 때까지 기다려 주세요.');return;}
    for(const row of batch.rows)if(row.status==='paused')row.status='waiting';phase='running';batch.phase=phase;update();store();await drain();
  };
  $('master-stop').onclick=async()=>{
    if(!['running','paused','preparing'].includes(phase))return;
    const paused=phase==='paused'&&!batch.rows.some(r=>r.status==='running');phase='stopping';batch.phase=phase;
    for(const row of batch.rows)if(['waiting','paused'].includes(row.status))row.status='stopped';
    setStatus('실행 중인 생성을 중단하고 대기 탭 실행을 취소합니다.');update();store();
    await Promise.all(batch.rows.filter(r=>r.status==='running').map(r=>tabs().find(t=>t.id===r.id)?.api.stop()));
    if(paused){phase='idle';batch.phase='stopped';release();setStatus('전체 실행을 중단했습니다. 완료된 기록은 유지합니다.');update();store();}
  };
  for(const id of Object.values(fields))$(id).addEventListener('input',()=>{store();update();});
  $('master-controller').open=saved.open ?? false;$('master-controller').ontoggle=store;
  modelOptions();
  if(common)writeCommon(common);
  for(const [field,value] of Object.entries(saved.fields || {}))if(fields[field])$(fields[field]).value=value;
  if(batch){setStatus(batch.phase==='interrupted'?'이전 실행이 새로고침으로 중단되었습니다. 저장된 대화를 확인한 뒤 이어서 실행할 수 있습니다.':'이전 실행 결과를 표시합니다.');}
  window.addEventListener('beforeunload',event=>{store();if(locked()){event.preventDefault();event.returnValue='';}});
  setInterval(()=>{update();if(locked())store();},500);update();
})();
