'use strict';
(() => {
  const $=id=>document.getElementById(id),node=(tag,text)=>Object.assign(document.createElement(tag),{textContent:text});
  const labels={starting:'입력 준비 중',running:'평가 중',stopping:'중단 요청됨',completed:'완료',failed:'오류',cancelled:'중단',interrupted:'프로세스 종료'};
  let catalog=[],job=null,busy=false,serial=0,artifactKey='';
  const draft=formSettings('evaluation-settings-v1',$('settings'));let ready=false;
  function saveSettings(){if(ready)draft.save(Object.fromEntries(['clients','counselors','batches'].map(id=>[id,checked(id)])));}
  const checked=id=>[...$(id).querySelectorAll('input:checked')].map(e=>e.value);
  async function api(path,body){const r=await fetch('/api/evaluation/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Gemma-Request':'1'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||r.statusText);return d;}
  function error(e){$('error').hidden=!e;$('error').textContent=e?.message||'';}
  function choices(id,values){const selected=new Set(checked(id)),had=$(id).children.length;$(id).replaceChildren();for(const value of values){const l=node('label','');l.className='check';const c=document.createElement('input');c.type='checkbox';c.value=value;c.checked=had?selected.has(value):true;l.append(c,node('span',value));$(id).append(l);}}
  function targets(){const latest=$('selection').value==='latest',clients=checked('clients'),counselors=checked('counselors'),ids=checked('batches');
    const seen=new Set();return catalog.filter(r=>{if(!r.eligible)return false;if(!latest)return ids.includes(r.id);const key=r.client+'|'+r.counselor;if(!clients.includes(r.client)||!counselors.includes(r.counselor)||seen.has(key))return false;seen.add(key);return true;});}
  function estimate(){const latest=$('selection').value==='latest';$('version-selection').hidden=!latest;$('batch-selection').hidden=latest;
    const rows=targets(),max=Number($('max-profiles').value)||Infinity,count=rows.reduce((n,r)=>n+Math.min(max,r.profiles),0),missing=latest?checked('clients').length*checked('counselors').length-rows.length:0;
    $('target-summary').textContent=`대상 ${rows.length}개 배치${missing?' · 완료 배치가 없는 조합 '+missing+'개: 선택을 조정하거나 배치를 직접 선택하세요.':''}`;
    $('create').disabled=!rows.length||missing>0;
    $('estimate').textContent=`제공·미제공 대화 ${count*2}개 × 공개·내면 평가 2종 × 반복 ${$('repeats').value} = ${count*4*Number($('repeats').value)}회${$('prepare-only').checked?' (이번 실행은 준비만 하므로 LLM 호출 없음)':''}. 실패 재시도는 별도입니다.`;
    const table=document.createElement('table'),head=document.createElement('tr');for(const t of ['배치명','내담자 × 상담사','프로필 / 전체 턴 / 분기'])head.append(node('th',t));table.append(head);
    for(const row of rows){const tr=document.createElement('tr');for(const t of [row.id,row.client+' × '+row.counselor,`${row.profiles} / ${row.turns} / ${row.fork_turn}`])tr.append(node('td',t));table.append(tr);}$('targets').replaceChildren(table);}
  async function refresh(initial=false){const data=await api('options');catalog=data.batches;
    choices('clients',[...new Set(catalog.filter(r=>r.eligible).map(r=>r.client))]);choices('counselors',[...new Set(catalog.filter(r=>r.eligible).map(r=>r.counselor))]);
    const selected=new Set(checked('batches'));$('batches').replaceChildren();for(const row of catalog){const l=node('label','');l.className='check';const c=document.createElement('input');c.type='checkbox';c.value=row.id;c.disabled=!row.eligible;c.checked=row.eligible&&selected.has(row.id);
      l.append(c,node('span',`${row.id} · ${row.client} × ${row.counselor} · ${row.profiles}개 프로필${row.eligible?'':' · '+row.reason}`));$('batches').append(l);}
    if(initial){for(const [id,key] of [['workers','workers'],['repeats','repeats'],['retries','retries'],['timeout','timeout_seconds'],['model','model'],['effort','effort']])$(id).value=data.defaults[key];}if(initial){draft.restore();for(const id of ['clients','counselors','batches'])draft.checks(id,draft.data[id]);ready=true;}estimate();}
  async function list(){const data=await api('list');$('jobs').replaceChildren(new Option('평가 작업 선택',''),...data.jobs.map(j=>new Option(j.id+' · '+new Date(j.created_at).toLocaleString('ko-KR'),j.id)));if(job)$('jobs').value=job.id;}
  async function select(id){if(!id)return;const token=++serial,result=await api('job?id='+encodeURIComponent(id));if(token!==serial)return;job=result;localStorage.setItem('evaluation-job-id',id);$('jobs').value=id;draw();}
  function draw(){if(!job)return;const running=['starting','running','stopping'].includes(job.status),p=job.progress,prepared=job.prepare_only&&job.status==='completed';
    $('status').textContent=`${prepared?'입력 준비 완료 · LLM 평가 미실행':labels[job.status]||job.status} · ${job.config.model} / ${job.config.effort} · 병렬 ${job.config.workers} · 성공 ${p.completed} · 실패 ${p.failed}${job.error?' · '+job.error:''}`;
    $('progress').value=p.total?100*p.finished/p.total:0;$('progress-text').textContent=`${p.finished} / ${p.total}`;$('output').textContent=job.output_path;$('log').textContent=job.log;
    $('stop').disabled=!running||job.status==='stopping';$('resume').disabled=running||(!prepared&&job.status==='completed'&&p.finished===p.total&&p.failed===0);
    const key=job.id+'|'+job.status+'|'+job.artifacts.map(a=>a.name).join(',');if(key===artifactKey)return;artifactKey=key;
    $('artifacts').replaceChildren();$('figures').replaceChildren();$('eval-report').textContent='';
    for(const item of job.artifacts){if(item.name.endsWith('.png')){const fig=document.createElement('figure'),img=document.createElement('img');img.alt=item.name;img.src=item.url;fig.append(img,node('figcaption',item.name));$('figures').append(fig);}
      else{const a=node('a',item.name);a.href=item.url;a.download=item.name;$('artifacts').append(a);if(item.name==='report.md'){const expected=key;fetch(item.url).then(r=>r.text()).then(t=>{if(artifactKey===expected)$('eval-report').textContent=t;}).catch(error);}}}
  }
  async function action(fn){if(busy)return;busy=true;serial++;$('settings').disabled=true;error(null);try{await fn();}catch(e){error(e);}finally{busy=false;$('settings').disabled=false;}}
  $('settings').addEventListener('input',()=>{estimate();saveSettings();});$('settings').addEventListener('change',saveSettings);$('selection').onchange=estimate;
  for(const id of ['clients','counselors','batches'])for(const all of [true,false])$(id+(all?'-all':'-none')).onclick=()=>{$(id).querySelectorAll('input').forEach(c=>c.checked=all&&!c.disabled);estimate();saveSettings();};
  $('refresh').onclick=()=>action(()=>refresh());$('reload').onclick=()=>action(list);$('jobs').onchange=()=>action(()=>select($('jobs').value));
  $('create').onclick=()=>action(async()=>{saveSettings();const max=$('max-profiles').value;
    job=await api('create',{selection:$('selection').value,clients:checked('clients'),counselors:checked('counselors'),batches:checked('batches'),workers:Number($('workers').value),repeats:Number($('repeats').value),retries:Number($('retries').value),timeout_seconds:Number($('timeout').value),max_profiles:max?Number(max):null,prepare_only:$('prepare-only').checked});
    localStorage.setItem('evaluation-job-id',job.id);draw();await list();});
  $('stop').onclick=()=>action(async()=>{job=await api('stop',{id:job.id});draw();});
  $('resume').onclick=()=>action(async()=>{job=await api('resume',{id:job.id,workers:Number($('workers').value)});draw();});
  async function poll(){try{if(job&&!busy)await select(job.id);}catch(e){error(e);}finally{setTimeout(poll,1500);}}
  action(async()=>{await refresh(true);await list();const id=localStorage.getItem('evaluation-job-id');if(id&&[...$('jobs').options].some(o=>o.value===id))await select(id);}).then(poll);
})();
