'use strict';
(() => {
  const $=id=>document.getElementById(id), node=(tag,text)=>Object.assign(document.createElement(tag),{textContent:text});
  const matrix=document.body.dataset.matrix==='true',apiRoot=matrix?'/api/batch_matrix':'/api/profile_batch',storageKey=matrix?'profile-matrix-id':'profile-batch-id';
  const embedded=new URLSearchParams(location.search).has('embedded');
  const draft=formSettings(matrix?'production-settings-v1':'batch-settings-v1',$('settings'),{enabled:!embedded});
  let ready=false;
  const currentClient=v=>/^cm(?:_|$)/.test(v)?'Q3'+v:v;
  function saveSettings(){if(!ready)return;
    draft.save({profileSelection:scanned?{directory:scanned,files:chosen()}:draft.data.profileSelection,
      ...(matrix?{clients:checked('matrix-clients'),counselors:checked('matrix-counselors'),conditions:Object.fromEntries([...$('matrix-counselors').querySelectorAll('select')].map(e=>[e.dataset.version,e.value]))}:{})});
  }
  const checked=id=>[...$(id).querySelectorAll('input:checked')].map(x=>x.value);
  const labels={ready:'준비',queued:'대기',running:'진행 중',stopping:'중단 요청됨',completed:'완료',failed:'오류',cancelled:'중단',interrupted:'서버 중단'};
  let options,profiles=[],scanned='',job=null,busy=false,selectionSerial=0;
  async function api(path,body){
    const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Gemma-Request':'1'},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw Error(data.error||response.statusText);return data;
  }
  function error(e){$('error').hidden=!e;$('error').textContent=e?.message||'';}
  const chosen=()=>[...$('profiles').querySelectorAll('input:checked')].map(e=>e.value);
  const versions=()=>[$('memory').checked?'Q3inner_memory':null,$('no-memory').checked?'inner_memory_no':null].filter(Boolean);
  const compare=()=>matrix||$('mode').value.startsWith('compare:');
  function endpoint(){
    if($('endpoint-mode').value==='current')return $('endpoint').value;
    const text=$('endpoint-octet').value.trim(),n=Number(text);
    if(!text||!Number.isInteger(n)||n<0||n>255)throw Error('192.168.0.n의 n은 0~255 정수로 입력하세요.');
    return `http://192.168.0.${n}:8002/v1`;}
  function endpointDraw(){const lan=$('endpoint-mode').value==='lan';$('endpoint').hidden=lan;$('endpoint-octet').hidden=!lan;
    let text='';if(lan){try{text=endpoint();}catch(e){text=e.message;}}$('endpoint-preview').textContent=text;}
  const branchLabel=r=>r.branch?' · '+(options?.compare?.branches?.[r.branch]||r.branch):'';
  function estimate(){
    if(matrix){const p=chosen().length,c=checked('matrix-clients').length,s=checked('matrix-counselors').length,t=Number($('turns').value),n=Number($('fork-turn').value);
      $('estimate').textContent=`내담자 ${c}종 × 상담사 ${s}종 = ${c*s}개 배치 · 프로필 ${p}개 · 총 ${c*s*p*3}개 세션 / ${c*s*p*((n-1)+2*(t-n+1))}개 내담자 턴. 전체 조합에서 최대 ${$('concurrency').value}개 프로필 흐름을 동시에 실행합니다.`;return;}
    if(compare()){const p=chosen().length,t=Number($('turns').value),n=Number($('fork-turn').value);
      $('estimate').textContent=`${p}개 프로필 × (공통 구간 1 + 갈래 2) = ${p*3}개 세션 / 프로필당 공통 ${n-1}턴 + 갈래마다 ${t-n+1}턴 / 총 ${p*((n-1)+2*(t-n+1))}개 내담자 턴`;return;}
    const count=chosen().length*versions().length;$('estimate').textContent=`${chosen().length}개 프로필 × ${versions().length}개 조건 = ${count}개 대화 / 총 ${count*Number($('turns').value)}개 내담자 턴`;}
  function drawProfiles(){
    $('profiles').replaceChildren();
    for(const p of profiles){const l=node('label','');l.className='check';const c=document.createElement('input');c.type='checkbox';c.value=p.filename;c.disabled=!!p.error;c.checked=!p.error;
      l.append(c,node('span',p.error?`${p.filename} · ${p.error}`:`${p.profile_id} · ${p.belief}`));if(p.error)l.classList.add('bad');$('profiles').append(l);}
    $('profile-summary').textContent=`${profiles.length}개 파일 · 사용 가능 ${profiles.filter(p=>!p.error).length}개`;estimate();
  }
  async function scan(){const d=await api('/api/profile_batch/profiles?directory='+encodeURIComponent($('directory').value));profiles=d.profiles;scanned=d.directory;$('directory').value=scanned;drawProfiles();const previous=draft.data.profileSelection;if(previous?.directory===scanned)draft.checks('profiles',previous.files);estimate();saveSettings();}
  async function list(){const data=await api(apiRoot+'/list');$('jobs').replaceChildren(new Option('작업 선택',''),...data.jobs.map(j=>new Option(`${j.id} · ${labels[j.status]||j.status} · ${new Date(j.created_at).toLocaleString('ko-KR')}`,j.id)));if(job)$('jobs').value=job.id;}
  async function select(id){if(!id)return;const serial=++selectionSerial;const result=await api(apiRoot+'/job?id='+encodeURIComponent(id));if(serial!==selectionSerial)return;job=result;localStorage.setItem(storageKey,job.id);$('jobs').value=job.id;draw();}
  function drawMatrix(){
    const running=['running','stopping'].includes(job.status);
    $('status').textContent=`${labels[job.status]||job.status} · ${job.children.length}개 조합 · 전체 동시생성 ${job.concurrency} · 성공 세션 ${job.succeeded}/${job.total} · 오류 ${job.failed}${job.error?' · '+job.error:''}`;
    for(const [prefix,done,total,percent] of [['turn',job.completed_turns,job.total_turns,job.turn_percent],['job',job.finished,job.total,job.finished_percent]]){
      $(prefix+'-progress').value=percent;$(prefix+'-progress-text').textContent=`${done} / ${total} (${percent}%)`;}
    $('stop').disabled=!running||job.status==='stopping';$('resume').disabled=running||job.succeeded===job.total||!!job.error;
    $('output').textContent='작업 묶음: '+job.output_path;
    const focused=document.activeElement?.dataset?.batch;$('rows').replaceChildren();
    for(const child of job.children){const tr=document.createElement('tr');
      for(const text of [child.id,`${child.client} × ${child.counselor}`,`${child.completed_turns}/${child.total_turns}`,labels[child.status]||child.status,`성공 ${child.succeeded}/${child.total} · 오류 ${child.failed}`])tr.append(node('td',text));
      const td=document.createElement('td'),button=node('button','배치 상세');button.dataset.batch=child.id;button.onclick=()=>{
        $('detail-title').textContent=child.id+' · '+child.client+' × '+child.counselor;
        const frame=document.createElement('iframe');frame.title='배치 상세';frame.src='/batch?embedded=1&job='+encodeURIComponent(child.id);frame.className='batch-detail-frame';
        $('detail-body').replaceChildren(frame);$('detail').showModal();};td.append(button);tr.append(td);$('rows').append(tr);if(focused===child.id)button.focus({preventScroll:true});}
  }
  function draw(){
    if(!job)return;const option=[...$('jobs').options].find(o=>o.value===job.id);if(option)option.textContent=`${job.id} · ${labels[job.status]||job.status} · ${new Date(job.created_at).toLocaleString('ko-KR')}`;
    if(matrix){drawMatrix();return;}const running=['running','stopping'].includes(job.status);
    $('status').textContent=`${labels[job.status]||job.status} · 대화당 ${job.options.turns}턴${job.mode==='compare'?` · ${job.options.fork_turn}턴부터 분기`:''} / 병렬 ${job.options.concurrency}개 · 성공 ${job.succeeded}/${job.total}개 · 오류 ${job.failed}개${job.error?' · '+job.error:''}`;
    $('turn-progress').value=job.turn_percent;$('turn-progress-text').textContent=`${job.completed_turns} / ${job.total_turns} (${job.turn_percent}%)`;
    $('job-progress').value=job.finished_percent;$('job-progress-text').textContent=`${job.finished} / ${job.total} (${job.finished_percent}%)`;
    $('stop').disabled=!running||job.status==='stopping';$('resume').disabled=running||job.succeeded===job.total;
    $('output').textContent='작업 저장 위치: '+job.output_path;
    const focused=document.activeElement?.dataset?.session;
    $('rows').replaceChildren();
    for(const r of job.rows){const tr=document.createElement('tr');
      for(const text of [r.profile_id,r.version+branchLabel(r),`${r.completed_turns}/${r.target_turns??job.options.turns}`,labels[r.status]||r.status,r.phase+(r.retry_count?` · 재시도 ${r.retry_count}회`:'')+(r.error?' · '+r.error:'')])tr.append(node('td',text));
      if(r.status==='failed')tr.classList.add('bad');const td=document.createElement('td'),button=node('button','대화·내면');button.disabled=!r.session_id;button.dataset.session=r.session_id||'';button.onclick=()=>detail(r).catch(error);td.append(button);tr.append(td);$('rows').append(tr);
      if(focused&&focused===r.session_id)button.focus({preventScroll:true});}
  }
  async function detail(row){
    const data=await api(`/api/profile_batch/detail?id=${job.id}&session_id=${encodeURIComponent(row.session_id)}`);
    $('detail-title').textContent=`${row.profile_id} · ${row.version}${branchLabel(row)} · ${row.session_id}`;$('detail-body').replaceChildren();
    const session=data.session,first=session.initial_public_count??1,fork=session.compare&&session.compare.branch!=='common'?first+session.compare.common_steps:-1;
    session.public_dialogue.forEach((m,i)=>{
      if(i===fork){const d=node('p',`여기부터 분기 · ${options?.compare?.branches?.[session.compare.branch]||session.compare.branch} (공통 세션 ${session.compare.parent_session})`);d.className='divider';$('detail-body').append(d);}
      const p=node('p',(m.role==='client'?'내담자: ':'상담사: ')+m.content);p.className='speech';$('detail-body').append(p);
      const mark=session.steps[i-first]?.revisit;
      if(mark?.is_revisit){const q=node('p',mark.valid?`되짚기 표시 · 내담자 ${mark.client_turn}턴 · "${mark.quote}"${mark.quote_found?'':' · 인용이 원문과 다름'}`:'되짚기 표시 · 가리킨 턴이 올바르지 않음');q.className='mark';$('detail-body').append(q);}
      const note=session.steps[i-first]?.rupture;
      if(note){const parts=[];
        if(note.explore.is_explore)parts.push(note.explore.valid?`균열 탐색 · 내담자 ${note.explore.client_turn}턴 (간격 ${note.explore.gap}${note.explore.noted?'':' · 메모에 없던 턴'})`:'균열 탐색 · 가리킨 턴이 올바르지 않음');
        if(note.check?.is_check)parts.push(note.check.valid?`개입 확인 · 상담사 ${note.check.counselor_turn}턴 발화 (간격 ${note.check.gap})`:'개입 확인 · 가리킨 턴이 올바르지 않음');
        if(note.marker.found)parts.push(note.marker.valid?`물러남 표지 메모 · 내담자 ${note.marker.client_turn}턴 · ${note.marker.kind} · "${note.marker.quote}"${note.marker.quote_found?'':' · 인용이 원문과 다름'}`:'물러남 표지 메모 · 기록이 올바르지 않음');
        if(note.observer)parts.push(`관찰 담당 권고 · ${note.observer.recommend}${note.observer.target_turn?' '+note.observer.target_turn+'턴':''}${note.observer.followed?'':' · 따르지 않음'}`);
        const q=node('p',[`상담사 기록 · ${note.move}`,...parts].join(' / '));q.className='mark';$('detail-body').append(q);}
    });
    for(const r of data.records){const d=document.createElement('details');d.append(node('summary',`${r.turn}턴 · 내면·선택 특성·실제 주입 기록`),node('pre',JSON.stringify(r,null,2)));$('detail-body').append(d);}
    $('detail').showModal();
  }
  async function action(fn){if(busy)return;busy=true;selectionSerial++;error(null);$('settings').disabled=true;try{await fn();}catch(e){error(e);}finally{busy=false;$('settings').disabled=false;}}
  $('scan').onclick=()=>action(scan);
  for(const [id,n] of [['select-all',Infinity],['select-20',20],['select-none',0]])$(id).onclick=()=>{let count=0;for(const c of $('profiles').querySelectorAll('input'))c.checked=!c.disabled&&count++<n;estimate();saveSettings();};
  $('settings').addEventListener('input',()=>{estimate();saveSettings();});
  $('settings').addEventListener('change',saveSettings);
  $('mode').onchange=()=>{$('version-setting').hidden=compare();$('fork-setting').hidden=!compare();
    if(compare()){$('counselor').value=options.compare.counselor;$('counselor').onchange();}estimate();};
  $('counselor').onchange=()=>{const c=options.counselors.find(c=>c.id===$('counselor').value);$('counselor-prompt').value=c.system_prompt;const conditions=c.intervention_conditions||[];$('intervention-setting').hidden=!conditions.length;$('intervention').replaceChildren(...conditions.map(x=>new Option(x.label,x.id)));if(conditions.length)$('intervention').value=c.default_intervention_condition;};
  $('create').onclick=()=>action(async()=>{
    saveSettings();
    if($('directory').value!==scanned)throw Error('변경한 프로필 폴더를 먼저 확인하세요.');
    const seed=$('seed').value.trim();
    if(seed&&(!Number.isInteger(Number(seed))||Number(seed)<0))throw Error('Seed는 0 이상의 정수입니다.');
    const body={profile_directory:scanned,filenames:chosen(),mode:compare()?'compare':'versions',...(compare()?{fork_turn:Number($('fork-turn').value),compare_version:$('mode').value.slice(8)}:{versions:versions()}),turns:Number($('turns').value),
      concurrency:Number($('concurrency').value),retries:Number($('retries').value),opening:$('opening').value,
      counselor_version:$('counselor').value,counselor_system_prompt:$('counselor-prompt').value,
      model:$('model').value,vllm_url:endpoint(),thinking_mode:$('thinking').value,
      input_char_limit:Number($('input-limit').value),...(!$('intervention-setting').hidden?{counselor_intervention_condition:$('intervention').value}:{}),
      generation:{...options.generation,temperature:Number($('temperature').value),max_tokens:Number($('max-tokens').value),seed:seed?Number(seed):null}};
    if(matrix){body.clients=checked('matrix-clients');body.counselors=checked('matrix-counselors');body.counselor_conditions=Object.fromEntries([...$('matrix-counselors').querySelectorAll('select')].map(e=>[e.dataset.version,e.value]));delete body.counselor_system_prompt;}
    selectionSerial++;job=await api(apiRoot+'/create',body);localStorage.setItem(storageKey,job.id);draw();await list();
    job=await api(apiRoot+'/start',{id:job.id});draw();
  });
  $('resume').onclick=()=>action(async()=>{job=await api(apiRoot+'/start',{id:job.id});draw();});
  $('stop').onclick=()=>action(async()=>{job=await api(apiRoot+'/stop',{id:job.id});draw();});
  $('reload').onclick=()=>action(list);$('jobs').onchange=()=>action(()=>select($('jobs').value));$('close-detail').onclick=()=>$('detail').close();
  async function poll(){try{if(job&&!busy)await select(job.id);}catch(e){error(e);}finally{setTimeout(poll,1500);}}
  action(async()=>{
    options=await api('/api/profile_batch/options');$('directory').value=options.profile_directory;$('opening').value=options.opening;
    if(matrix){for(const [id,items] of [['matrix-clients',options.compare.versions],['matrix-counselors',options.counselors]]){
      for(const spec of items){const label=node('label','');label.className='check';const box=document.createElement('input');box.type='checkbox';box.value=spec.id;
        box.checked=id==='matrix-clients'?spec.id==='Q3cm':spec.id===options.compare.counselor;label.append(box,node('span',spec.label));$(id).append(label);
        if(spec.intervention_conditions){const select=document.createElement('select');select.dataset.version=spec.id;select.setAttribute('aria-label',spec.id+' 개입 조건');select.replaceChildren(...spec.intervention_conditions.map(x=>new Option(x.label,x.id)));select.value=spec.default_intervention_condition;$(id).append(select);}}
      for(const all of [true,false]){$(id+(all?'-all':'-none')).onclick=()=>{$(id).querySelectorAll('input').forEach(c=>c.checked=all);estimate();saveSettings();};}}
    }
    const previousMode=$('mode').value;
    $('mode').replaceChildren(new Option('독립 대화 · 내면 제공/미제공','versions'),
      ...(options.compare.versions||[options.compare.version]).map(v=>new Option('공통 생성 후 분기 · '+v.label,'compare:'+v.id)));
    if([...$('mode').options].some(o=>o.value===previousMode))$('mode').value=previousMode;
    $('endpoint').value=options.vllm_url;$('model').replaceChildren(...options.models.map(m=>new Option(m,m)));$('model').value=options.model;
    $('temperature').value=options.generation.temperature;$('max-tokens').value=options.generation.max_tokens;
    $('counselor').replaceChildren(...options.counselors.map(c=>new Option(c.label,c.id)));$('counselor').value=options.compare.counselor;$('counselor').onchange();
    $('fork-turn').value=options.compare.fork_turn;$('endpoint-mode').onchange=endpointDraw;$('endpoint-octet').oninput=endpointDraw;endpointDraw();
    if(draft.data.fields?.mode?.startsWith('compare:cm'))draft.data.fields.mode='compare:'+currentClient(draft.data.fields.mode.slice(8));
    draft.restore(['mode','counselor']);$('counselor').onchange();draft.restore();
    $('version-setting').hidden=compare();$('fork-setting').hidden=!compare();endpointDraw();
    if(matrix){draft.checks('matrix-clients',Array.isArray(draft.data.clients)?draft.data.clients.map(currentClient):undefined);draft.checks('matrix-counselors',draft.data.counselors);
      for(const e of $('matrix-counselors').querySelectorAll('select')){const value=draft.data.conditions?.[e.dataset.version];if([...e.options].some(o=>o.value===value))e.value=value;}}
    try{await scan();}catch(e){error(e);}ready=true;estimate();
    await list();const saved=new URLSearchParams(location.search).get('job')||localStorage.getItem(storageKey);if(saved&&[...$('jobs').options].some(o=>o.value===saved))await select(saved);
    if(new URLSearchParams(location.search).has('embedded')){document.querySelector('body>header').hidden=true;document.querySelector('main>section').hidden=true;}
  }).then(poll);
})();
