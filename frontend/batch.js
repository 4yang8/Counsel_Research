'use strict';
(() => {
  const $=id=>document.getElementById(id), node=(tag,text)=>Object.assign(document.createElement(tag),{textContent:text});
  const labels={ready:'준비',queued:'대기',running:'진행 중',stopping:'중단 요청됨',completed:'완료',failed:'오류',cancelled:'중단',interrupted:'서버 중단'};
  let options,profiles=[],scanned='',job=null,busy=false,selectionSerial=0;
  async function api(path,body){
    const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Gemma-Request':'1'},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw Error(data.error||response.statusText);return data;
  }
  function error(e){$('error').hidden=!e;$('error').textContent=e?.message||'';}
  const chosen=()=>[...$('profiles').querySelectorAll('input:checked')].map(e=>e.value);
  const versions=()=>[$('memory').checked?'Q3inner_memory':null,$('no-memory').checked?'inner_memory_no':null].filter(Boolean);
  function estimate(){const count=chosen().length*versions().length;$('estimate').textContent=`${chosen().length}개 프로필 × ${versions().length}개 조건 = ${count}개 대화 / 총 ${count*Number($('turns').value)}개 내담자 턴`;}
  function drawProfiles(){
    $('profiles').replaceChildren();
    for(const p of profiles){const l=node('label','');l.className='check';const c=document.createElement('input');c.type='checkbox';c.value=p.filename;c.disabled=!!p.error;c.checked=!p.error;
      l.append(c,node('span',p.error?`${p.filename} · ${p.error}`:`${p.profile_id} · ${p.belief}`));if(p.error)l.classList.add('bad');$('profiles').append(l);}
    $('profile-summary').textContent=`${profiles.length}개 파일 · 사용 가능 ${profiles.filter(p=>!p.error).length}개`;estimate();
  }
  async function scan(){const d=await api('/api/profile_batch/profiles?directory='+encodeURIComponent($('directory').value));profiles=d.profiles;scanned=d.directory;$('directory').value=scanned;drawProfiles();}
  async function list(){const data=await api('/api/profile_batch/list');$('jobs').replaceChildren(new Option('작업 선택',''),...data.jobs.map(j=>new Option(`${j.created_at.slice(0,19)} · ${labels[j.status]||j.status} · ${j.id}`,j.id)));if(job)$('jobs').value=job.id;}
  async function select(id){if(!id)return;const serial=++selectionSerial;const result=await api('/api/profile_batch/job?id='+encodeURIComponent(id));if(serial!==selectionSerial)return;job=result;localStorage.setItem('profile-batch-id',job.id);$('jobs').value=job.id;draw();}
  function draw(){
    if(!job)return;const running=['running','stopping'].includes(job.status);
    $('status').textContent=`${labels[job.status]||job.status} · 대화당 ${job.options.turns}턴 / 병렬 ${job.options.concurrency}개 · 성공 ${job.succeeded}/${job.total}개 · 오류 ${job.failed}개${job.error?' · '+job.error:''}`;
    $('turn-progress').value=job.turn_percent;$('turn-progress-text').textContent=`${job.completed_turns} / ${job.total_turns} (${job.turn_percent}%)`;
    $('job-progress').value=job.finished_percent;$('job-progress-text').textContent=`${job.finished} / ${job.total} (${job.finished_percent}%)`;
    $('stop').disabled=!running||job.status==='stopping';$('resume').disabled=running||job.succeeded===job.total;
    $('output').textContent='작업 저장 위치: '+job.output_path;
    const focused=document.activeElement?.dataset?.session;
    $('rows').replaceChildren();
    for(const r of job.rows){const tr=document.createElement('tr');
      for(const text of [r.profile_id,r.version,`${r.completed_turns}/${job.options.turns}`,labels[r.status]||r.status,r.phase+(r.retry_count?` · 재시도 ${r.retry_count}회`:'')+(r.error?' · '+r.error:'')])tr.append(node('td',text));
      if(r.status==='failed')tr.classList.add('bad');const td=document.createElement('td'),button=node('button','대화·내면');button.disabled=!r.session_id;button.dataset.session=r.session_id||'';button.onclick=()=>detail(r).catch(error);td.append(button);tr.append(td);$('rows').append(tr);
      if(focused&&focused===r.session_id)button.focus({preventScroll:true});}
  }
  async function detail(row){
    const data=await api(`/api/profile_batch/detail?id=${job.id}&session_id=${encodeURIComponent(row.session_id)}`);
    $('detail-title').textContent=`${row.profile_id} · ${row.version} · ${row.session_id}`;$('detail-body').replaceChildren();
    for(const m of data.session.public_dialogue){const p=node('p',(m.role==='client'?'내담자: ':'상담사: ')+m.content);p.className='speech';$('detail-body').append(p);}
    for(const r of data.records){const d=document.createElement('details');d.append(node('summary',`${r.turn}턴 · 내면·선택 특성·실제 주입 기록`),node('pre',JSON.stringify(r,null,2)));$('detail-body').append(d);}
    $('detail').showModal();
  }
  async function action(fn){if(busy)return;busy=true;selectionSerial++;error(null);$('settings').disabled=true;try{await fn();}catch(e){error(e);}finally{busy=false;$('settings').disabled=false;}}
  $('scan').onclick=()=>action(scan);
  for(const [id,n] of [['select-all',Infinity],['select-20',20],['select-none',0]])$(id).onclick=()=>{let count=0;for(const c of $('profiles').querySelectorAll('input'))c.checked=!c.disabled&&count++<n;estimate();};
  $('settings').addEventListener('input',estimate);
  $('counselor').onchange=()=>{const c=options.counselors.find(c=>c.id===$('counselor').value);$('counselor-prompt').value=c.system_prompt;const conditions=c.intervention_conditions||[];$('intervention-setting').hidden=!conditions.length;$('intervention').replaceChildren(...conditions.map(x=>new Option(x.label,x.id)));if(conditions.length)$('intervention').value=c.default_intervention_condition;};
  $('create').onclick=()=>action(async()=>{
    if($('directory').value!==scanned)throw Error('변경한 프로필 폴더를 먼저 확인하세요.');
    const seed=$('seed').value.trim();
    if(seed&&(!Number.isInteger(Number(seed))||Number(seed)<0))throw Error('Seed는 0 이상의 정수입니다.');
    const body={profile_directory:scanned,filenames:chosen(),versions:versions(),turns:Number($('turns').value),
      concurrency:Number($('concurrency').value),retries:Number($('retries').value),opening:$('opening').value,
      counselor_version:$('counselor').value,counselor_system_prompt:$('counselor-prompt').value,
      model:$('model').value,vllm_url:$('endpoint').value,thinking_mode:$('thinking').value,
      input_char_limit:Number($('input-limit').value),...(!$('intervention-setting').hidden?{counselor_intervention_condition:$('intervention').value}:{}),
      generation:{...options.generation,temperature:Number($('temperature').value),max_tokens:Number($('max-tokens').value),seed:seed?Number(seed):null}};
    selectionSerial++;job=await api('/api/profile_batch/create',body);localStorage.setItem('profile-batch-id',job.id);draw();await list();
    job=await api('/api/profile_batch/start',{id:job.id});draw();
  });
  $('resume').onclick=()=>action(async()=>{job=await api('/api/profile_batch/start',{id:job.id});draw();});
  $('stop').onclick=()=>action(async()=>{job=await api('/api/profile_batch/stop',{id:job.id});draw();});
  $('reload').onclick=()=>action(list);$('jobs').onchange=()=>action(()=>select($('jobs').value));$('close-detail').onclick=()=>$('detail').close();
  async function poll(){try{if(job&&!busy)await select(job.id);}catch(e){error(e);}finally{setTimeout(poll,1500);}}
  action(async()=>{
    options=await api('/api/profile_batch/options');$('directory').value=options.profile_directory;$('opening').value=options.opening;
    $('endpoint').value=options.vllm_url;$('model').replaceChildren(...options.models.map(m=>new Option(m,m)));$('model').value=options.model;
    $('temperature').value=options.generation.temperature;$('max-tokens').value=options.generation.max_tokens;
    $('counselor').replaceChildren(...options.counselors.map(c=>new Option(c.label,c.id)));$('counselor').value='basic';$('counselor').onchange();
    await scan();await list();const saved=localStorage.getItem('profile-batch-id');if(saved&&[...$('jobs').options].some(o=>o.value===saved))await select(saved);
  }).then(poll);
})();
