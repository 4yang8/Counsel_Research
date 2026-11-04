'use strict';
(() => {
  const versions=['Q3public','Q3oracle','Q3full','Q3select','Q3ragtool'];
  const active=()=>versions.includes(currentVersion);
  const drafts={},frozen={};let locked=false,latest=null,requestSerial=0,lastSession='',pickerStamp='';
  const fields={embedding_backend:'memory-backend',embedding_path:'memory-path',embedding_device:'memory-device',
    embedding_url:'memory-url',embedding_model:'memory-model',embedding_timeout:'memory-timeout',input_char_limit:'memory-input-limit'};
  const box=document.createElement('section');box.id='memory-access-settings';box.className='setting-block';box.hidden=true;
  box.innerHTML=`<h3>과거 비공개 기억 실험</h3><p id="memory-condition-description" class="hint"></p>
    <p class="hint">모든 조건은 이번 턴의 내면을 먼저 기록하고 공개 발화를 생성합니다. 과거 내면의 입력 방식만 다릅니다. 프로필·공개 대화는 공통이며 누적 내면 노트와 궤적 요약은 자동 주입하지 않습니다.</p>
    <label>공통 대화·내면 기록 가져오기<input id="memory-import" type="file" accept=".json,application/json"></label>
    <label>공통 기록 JSON · 비워두면 위 초기 대화에서 시작<textarea id="memory-seed" rows="5" spellcheck="false" placeholder='{"public_dialogue": [...], "records": [...]}'></textarea></label>
    <p id="memory-seed-status" class="hint"></p>
    <p class="hint">가져온 공개 대화가 초기 히스토리를 대체합니다. 프로필과 다음 상담사 질문은 실험 설정에서 동일하게 맞추세요. 턴은 상담사·내담자 한 쌍 기준입니다. JSON 안의 내면은 public 조건의 모델 입력에 전달되지 않습니다.</p>
    <div id="memory-oracle-controls" hidden><h4>Oracle · 제공할 과거 턴 직접 선택</h4>
      <div id="memory-oracle-picker"></div>
      <label>기본 제공 턴 번호 · 쉼표 구분<input id="memory-oracle-turns" placeholder="예: 2, 5"></label>
      <label>생성 턴별 선택 계획 · 선택 사항<textarea id="memory-oracle-plan" rows="3" spellcheck="false" placeholder='{"6": [2, 5], "7": [5]}'>{}</textarea></label>
      <p class="hint">계획의 키는 생성할 내담자 턴, 값은 제공할 과거 턴입니다. 계획이 없으면 기본 선택을 사용합니다. 선택하지 않으면 과거 내면 0개가 제공됩니다. 현재·미래·없는 기록은 사용할 수 없습니다.</p>
    </div>
    <div id="memory-rag-controls" hidden><h4>RAG 검색 도구</h4>
      <p>턴 단위 청크 · Top-3 · 코사인 순위 · 임계값 없음 · 생성당 최대 1회</p>
      <label>임베딩 실행<select id="memory-backend"><option value="local">로컬 SentenceTransformers</option><option value="openai">vLLM / OpenAI 호환 임베딩 서버</option></select></label>
      <div id="memory-local-controls"><label>로컬 모델 경로<input id="memory-path"></label><label>장치<input id="memory-device" value="auto" placeholder="auto / cpu / cuda:0"></label></div>
      <div id="memory-server-controls" hidden><label>임베딩 서버 주소<input id="memory-url" placeholder="http://호스트:포트/v1"></label><label>서버에 등록한 모델 이름<input id="memory-model"></label><label>임베딩 요청 제한 시간 · 초<input id="memory-timeout" type="number" min="1" max="600" value="120"></label></div>
      <p class="hint">발화 생성 모델 주소와 별개입니다. 로컬 모델은 첫 검색 때 로딩합니다. 검색 실패 시 정보 없는 조건으로 대체하지 않고 이번 턴을 중단합니다.</p>
    </div>
    <p id="memory-select-hint" class="hint" hidden>기억 선별 담당이 전체 과거 기록을 읽고 최대 3개 턴 번호를 선택합니다. 선택한 원문을 그대로 제공하며 추가 해석으로 재작성하지 않습니다. 선별 지침은 아래 서브에이전트 편집기에서 수정할 수 있습니다.</p>
    <label>호출 입력 크기 한도 · 문자<input id="memory-input-limit" type="number" min="1000" max="2000000" value="240000"></label>
    <p class="hint">한도를 넘으면 자동 절삭하지 않고 오류를 표시합니다. 실제 모델의 토큰 한도와는 별도입니다.</p>`;
  $('client-reasoning-budgets').before(box);
  const panel=document.createElement('section');panel.id='memory-access-view';panel.className='client-notes-view';
  panel.innerHTML='<div class="note-view-heading"><h2>기억 제공 · 검색 기록</h2><button id="memory-refresh" type="button">저장 기록 새로고침</button><button id="memory-export" type="button" disabled>공통 기록 JSON 내보내기</button></div><p id="memory-live-status" role="status">기억 실험 버전을 선택하세요.</p><p id="memory-live-summary"></p><details><summary>현재 실행 상세</summary><pre id="memory-live-detail"></pre></details><div id="memory-turn-results"></div>';
  document.querySelector('.results-scroll').append(panel);window.registerLabView('memory_access',panel);
  const tab=document.createElement('button');tab.type='button';tab.id='memory-access-tab';tab.className='config-tab';tab.textContent='기억 검색';tab.dataset.view='memory_access';tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panel.id);document.querySelector('.config-tabs').append(tab);
  const parse=(text,label,fallback)=>{try{return text.trim()?JSON.parse(text):fallback;}catch{throw Error(label+' JSON 형식을 확인하세요.');}};
  function turnIds(){
    const text=$('memory-oracle-turns').value.trim();if(!text)return [];
    const result=text.split(',').map(s=>Number(s.trim()));
    if(result.some(n=>!Number.isInteger(n)||n<1)||new Set(result).size!==result.length)throw Error('Oracle 턴 번호는 중복 없는 양의 정수로 입력하세요.');
    return result;
  }
  function read(){
    const spec=versionCatalog?.versions.find(v=>v.id===currentVersion);
    const options={...structuredClone(spec?.memory_defaults || {}),...Object.fromEntries(Object.entries(fields).map(([key,id])=>[key,$(id).value]))};
    options.embedding_timeout=Number(options.embedding_timeout);options.input_char_limit=Number(options.input_char_limit);
    options.top_k=3;options.seed=parse($('memory-seed').value,'공통 기록',null);
    options.oracle_turns=turnIds();options.oracle_plan=parse($('memory-oracle-plan').value,'Oracle 계획',{});
    return options;
  }
  function fill(options){
    for(const [key,id] of Object.entries(fields))$(id).value=options[key]??'';
    $('memory-seed').value=options.seed?JSON.stringify(options.seed,null,2):'';
    $('memory-oracle-turns').value=(options.oracle_turns || []).join(', ');
    $('memory-oracle-plan').value=JSON.stringify(options.oracle_plan || {},null,2);
    picker();
  }
  function picker(){
    const target=$('memory-oracle-picker');target.replaceChildren();
    try{
      const seed=parse($('memory-seed').value,'공통 기록',null);const rows=seed?.records || [], chosen=turnIds();
      $('memory-seed-status').textContent=seed?'공통 대화 '+(seed.public_dialogue?.length || 0)+'개 발화 / 당시 내면 기록 '+rows.length+'개':'초기 대화의 내면은 추론해서 채우지 않습니다. 새로 생성된 턴부터 기록이 쌓입니다.';
      for(const row of rows){
        const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=chosen.includes(row.turn);check.disabled=locked;
        check.onchange=()=>{const ids=new Set(turnIds());if(check.checked)ids.add(row.turn);else ids.delete(row.turn);$('memory-oracle-turns').value=[...ids].sort((a,b)=>a-b).join(', ');changed();};
        label.append(check,document.createTextNode(' '+row.turn+'턴 · '+String(row.utterance).slice(0,80)));target.append(label);
      }
    }catch(error){$('memory-seed-status').textContent=error.message;}
  }
  function backend(){
    const local=$('memory-backend').value==='local';$('memory-local-controls').hidden=!local;$('memory-server-controls').hidden=local;
  }
  function changed(){
    if(!active()||locked)return;
    try{drafts[currentVersion]=read();}catch{}backend();updatePreview();
  }
  box.addEventListener('input',changed);box.addEventListener('change',changed);
  $('memory-seed').addEventListener('change',picker);$('memory-oracle-turns').addEventListener('change',picker);
  $('memory-import').onchange=async()=>{
    try{const file=$('memory-import').files[0];if(!file)return;if(file.size>2000000)throw Error('공통 기록 파일은 2 MB 이하여야 합니다.');const value=JSON.parse(await file.text());$('memory-seed').value=JSON.stringify(value.snapshot || value,null,2);picker();changed();document.dispatchEvent(new Event('change',{bubbles:true}));}
    catch(error){notice(error.message);}
  };
  function sync(isLocked){
    locked=isLocked;box.hidden=!active();tab.hidden=!active();if(!active())return;
    if(box.dataset.version!==currentVersion){box.dataset.version=currentVersion;fill(drafts[currentVersion] || versionCatalog?.versions.find(v=>v.id===currentVersion)?.memory_defaults || {});}
    $('memory-condition-description').textContent=versionCatalog?.versions.find(v=>v.id===currentVersion)?.description || '';
    $('memory-oracle-controls').hidden=currentVersion!=='Q3oracle';$('memory-rag-controls').hidden=currentVersion!=='Q3ragtool';$('memory-select-hint').hidden=currentVersion!=='Q3select';
    for(const el of box.querySelectorAll('input,textarea,select,button'))el.disabled=locked;
    const stamp=JSON.stringify([$('memory-seed').value,$('memory-oracle-turns').value,locked]);
    if(stamp!==pickerStamp){pickerStamp=stamp;picker();}
    backend();
  }
  function request(){
    if(!active())return {};
    return {memory_options:read(),...(frozen[currentVersion]?{memory_selector_prompt:frozen[currentVersion]}:{})};
  }
  function restore(settings){
    if(!active())return;drafts[currentVersion]=structuredClone(settings.memory_options || {});frozen[currentVersion]=settings.memory_selector_prompt;
    box.dataset.version=currentVersion;fill(drafts[currentVersion]);sync(true);
  }
  function contextQuery(){
    const paired=window.Paired?.hasSession,sid=paired?Paired.sessionId:sessionId;
    if(!sid)return null;
    const q=new URLSearchParams({session_id:sid,version:currentVersion});if(paired)q.set('paired','1');else if(restoredSessionKey)q.set('key',restoredSessionKey);
    return q;
  }
  async function refresh(){
    if(!active())return;const q=contextQuery();if(!q){latest=null;$('memory-export').disabled=true;$('memory-turn-results').replaceChildren();return;}
    const serial=++requestSerial;
    const result=await api('/api/memory_access/state?'+q);
    if(serial!==requestSerial||!active()||String(q)!==String(contextQuery()))return;
    latest=result;$('memory-export').disabled=!result.snapshot.public_dialogue.length;
    const target=$('memory-turn-results');target.replaceChildren();
    for(const row of result.state.records){
      const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');
      summary.textContent=row.turn+'턴 · '+(row.condition?row.condition+' · 주입 턴 '+(row.metrics?.injected_turns?.join(', ') || '없음')+' · 검색 '+(row.metrics?.search_calls ?? 0)+'회':'가져온 공통 기록');
      pre.textContent=JSON.stringify({발화:row.utterance,이번_내면:row.inner,실제_주입:row.memory_input,검색:row.retrievals,선별:row.selection,측정:row.metrics},null,2);details.append(summary,pre);target.append(details);
    }
  }
  function event(e){
    if(e.type!=='version_event'||e.section!=='memory_access'||!active()||e.version!==currentVersion)return;
    const names={started:'기억 조건 준비',selected:'관련 턴 선별 완료',injected:'초기 기억 주입',searching:'RAG 검색 중',retrieved:'RAG 결과 주입',completed:'기록·발화 저장 완료',discarded:'오류·중단 · 이번 턴 미저장'};
    $('memory-live-status').textContent=e.version+' · '+(names[e.action]||e.action);
    const d=e.data || {};
    $('memory-live-summary').textContent=d.query?'검색어: '+d.query:d.llm_usage?'검색 '+d.search_calls+'회 · 주입 턴 '+(d.injected_turns.join(', ') || '없음')+' · LLM '+(d.llm_usage.total_tokens ?? '미집계')+' 토큰 · '+(d.latency_ms/1000).toFixed(2)+'초':d.selected_turns?'주입 턴: '+(d.selected_turns.join(', ') || '없음'):d.available_turns?'검색 가능한 과거 기록 '+d.available_turns.length+'개':'';
    $('memory-live-detail').textContent=JSON.stringify(e.data,null,2);
  }
  tab.onclick=()=>{window.showLabResult('memory_access');refresh().catch(error=>notice(error.message));};
  $('memory-refresh').onclick=()=>refresh().catch(error=>notice(error.message));
  $('memory-export').onclick=()=>{
    if(!latest)return;const url=URL.createObjectURL(new Blob([JSON.stringify(latest.snapshot,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download=currentVersion+'-common-memory.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  document.addEventListener('lab-sessions-changed',()=>refresh().catch(()=>{}));
  document.addEventListener('lab-session-status',()=>{const key=String(contextQuery());if(key!==lastSession){lastSession=key;latest=null;$('memory-export').disabled=true;$('memory-turn-results').replaceChildren();$('memory-live-detail').textContent='';$('memory-live-summary').textContent='';refresh().catch(()=>{});}});
  $('experiment-version').addEventListener('change',()=>{latest=null;frozen[currentVersion]=undefined;$('memory-export').disabled=true;$('memory-turn-results').replaceChildren();$('memory-live-status').textContent='새 실험 대기';$('memory-live-detail').textContent='';$('memory-live-summary').textContent='';});
  function capture(){if(active() && box.dataset.version===currentVersion)try{drafts[currentVersion]=read();}catch{}}
  window.MemoryAccess={active,sync,request,restore,event,refresh,capture};
})();
