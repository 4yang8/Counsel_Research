'use strict';
(() => {
  const $=id=>document.getElementById(id), node=(tag,text)=>{const el=document.createElement(tag);el.textContent=text;return el;};
  let config, job=null, profiles=[], defaultScenario, researchProfile, polling=false;
  const opening='오늘 상담에서 가장 먼저 다뤄보고 싶은 일이 있나요?';
  const endpointPorts={local:'18001',lan:'8001'};
  let endpointMode='local';
  function endpointValue(){
    if(endpointMode==='custom')return $('endpoint').value.trim();
    const port=Number($('endpoint-port').value),octet=Number($('endpoint-octet').value);
    if(!$('endpoint-port').value.trim()||!Number.isInteger(port)||port<1||port>65535)throw Error('포트는 1~65535 정수로 입력하세요.');
    if(endpointMode==='lan'&&(!$('endpoint-octet').value.trim()||!Number.isInteger(octet)||octet<0||octet>255))throw Error('내부망 주소 마지막 번호는 0~255 정수로 입력하세요.');
    return 'http://'+(endpointMode==='local'?'127.0.0.1':'192.168.0.'+octet)+':'+port+'/v1';
  }
  function endpointPreview(){
    const custom=endpointMode==='custom',lan=endpointMode==='lan';
    $('endpoint-octet-label').hidden=!lan;$('endpoint-octet').disabled=!lan;$('endpoint-octet').required=lan;
    $('endpoint-port-label').hidden=custom;$('endpoint-port').disabled=custom;
    $('endpoint-custom-label').hidden=!custom;$('endpoint').disabled=!custom;$('endpoint').required=custom;
    try{$('endpoint-preview').textContent='새 실험 모델 주소: '+endpointValue();}catch(e){$('endpoint-preview').textContent=e.message;}
  }
  function restoreEndpoint(value){
    $('endpoint').value=value;endpointMode='custom';
    try{const url=new URL(value),lan=url.hostname.match(/^192\.168\.0\.(\d{1,3})$/);
      if(url.protocol==='http:'&&!url.username&&!url.password&&!url.search&&!url.hash&&/^\/v1\/?$/.test(url.pathname)&&(url.hostname==='127.0.0.1'||lan)){
        endpointMode=lan?'lan':'local';if(lan)$('endpoint-octet').value=lan[1];
        endpointPorts[endpointMode]=url.port||'80';$('endpoint-port').value=endpointPorts[endpointMode];
      }
    }catch{}
    $('endpoint-mode').value=endpointMode;endpointPreview();
  }
  $('endpoint-mode').onchange=()=>{
    if(endpointMode!=='custom')endpointPorts[endpointMode]=$('endpoint-port').value;
    endpointMode=$('endpoint-mode').value;
    if(endpointMode!=='custom')$('endpoint-port').value=endpointPorts[endpointMode];
    endpointPreview();
  };
  for(const id of ['endpoint-octet','endpoint-port','endpoint'])$(id).oninput=endpointPreview;
  const labels={ready:'준비',running:'실행 중',paused:'일시정지',stopped:'중단',error:'오류',completed:'완료',interrupted:'서버 재시작 후 재개 대기',skipped:'미수행',preparing:'개입 준비',waiting:'대기'};
  async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Gemma-Request':'1'},body:JSON.stringify(body)}:{});const x=await r.json();if(!r.ok)throw Error(x.error||'요청 실패');return x;}
  function error(e){$('error').textContent=e?.message||'';}
  let contextualControls=null;
  function probeMode(){
    const diagnostic=$('probe-mode').value==='past_intent';
    if(diagnostic){
      contextualControls={mode:$('intervention-mode').value,policy:$('branch-policy').value};
      $('intervention-mode').value='single';$('branch-policy').value='all';
      if(Number($('start').value)<2)$('start').value=2;
      if(Number($('end').value)<Number($('start').value))$('end').value=$('start').value;
    }else if(contextualControls){$('intervention-mode').value=contextualControls.mode;$('branch-policy').value=contextualControls.policy;contextualControls=null;}
    $('intervention-mode').disabled=diagnostic;$('branch-policy').disabled=diagnostic;
    $('intent-probe-settings').hidden=!diagnostic;$('start').min=diagnostic?'2':'1';estimate();
  }
  $('probe-mode').onchange=probeMode;
  function estimate(){const n=Math.max(0,Number($('end').value)-Number($('start').value)+1),ab=n*($('intervention-mode').value==='dual'?4:2)*Number($('length').value)*Number($('repeats').value),c=$('include-c').value==='yes';
    $('trajectory-repeats').disabled=!c;
    const paths=c?(2*Number($('trajectory-repeats').value)-1)*Number($('end').value):0;
    const probes=c&&$('probe-mode').value==='past_intent'?(2*Number($('trajectory-repeats').value)-1)*n*Number($('repeats').value)*Number($('length').value):0;
    $('estimate').textContent=`A/B 분기 최대 ${ab}개 + 공통 ${$('end').value}개 + 독립 A/C 추가 ${paths}개 + 경로별 재질문 추가 ${probes}개 내담자 응답 · ${$('probe-mode').value==='past_intent'?'모든 지정 시점에서 고정 재질문':'자연스러운 시점만 분기하면 실제 건수는 줄어듭니다.'} 상담사·라우터·노트 호출은 별도`;
  }

  async function list(){const value=await api('/api/q3p6/list');$('jobs').replaceChildren(new Option('선택하세요',''),...value.jobs.map(j=>new Option(`${j.id} · ${labels[j.status]||j.status} · 공통 ${j.common_turn}턴`,j.id)));if(job)$('jobs').value=job.id;}
  async function select(id){job=await api('/api/q3p6/job?id='+encodeURIComponent(id));localStorage.setItem('q3p6.job',job.id);$('jobs').value=job.id;draw();}
  async function detail(sid){try{const data=await api('/api/q3p6/detail?id='+job.id+'&session_id='+encodeURIComponent(sid));const box=$('detail-body');box.replaceChildren();for(const m of data.session.public_dialogue){const p=node('p',(m.role==='client'?'내담자: ':'상담사: ')+m.content);p.className='speech';box.append(p);}for(const r of data.records){const d=document.createElement('details');d.append(node('summary',`내담자 ${r.turn}턴 · ${r.condition==='public_only'?'공개 노트·실제 입력':'노트·당시 내면·실제 모델 입력'}`),node('pre',JSON.stringify(r,null,2)));box.append(d);}$('detail').showModal();}catch(e){error(e);}}
  function freshScenario(profile){
    const s={};for(const k of ['task','states','taxonomy'])s[k]=structuredClone(defaultScenario[k]);
    s.profile=structuredClone(profile);s.id='q3p6_fresh_'+(profile.profile_id||'custom');
    s.dialogue_format='messages';s.dialogue=[];s.source_notes={case_origin:'Q3p6 새 상담 · 초기 대화 없음'};return s;
  }
  function caseSummary(){try{const s=JSON.parse($('scenario').value);const fresh=$('case-mode').value==='fresh';
    $('case-summary').textContent=`${s.profile.profile_id||'직접 입력한 프로필'} · ${fresh?'초기 대화 없이 새로 시작합니다.':'직접 작성한 초기 대화와 다음 질문의 맥락을 확인하세요.'}\n${s.profile.presenting_problem||''}`;
    $('opening').placeholder=fresh?opening:'초기 대화에 이어서 할 상담사 질문을 입력하세요.';
  }catch{$('case-summary').textContent='초기 자료 JSON을 확인하세요.';}}
  function drawWorkflow(){
    const live=job.live||{}, cp=job.checkpoints.at(-1);
    let active=-1;if(job.running){active=live.stage==='proposal'?1:live.stage==='branch'?(live.condition==='intent_linked'?3:2):live.stage==='advance'?4:0;}
    const names=['공통 대화 생성','대상·후속 질문 준비','A · 현재 노트','B · 당시 기록 추가','공통 한 턴 추가'];if(job.options.include_c)names.push('독립 A/C 경로');if(job.running&&live.stage==='trajectory')active=5;if(job.revision>=5&&job.options.include_c&&job.options.probe_mode==='past_intent')names.push('독립 A/C 재질문');if(job.running&&live.stage==='trajectory_probe')active=6;
    $('workflow').replaceChildren(...names.map((name,i)=>{const li=node('li',name);li.className=i===active?'active':(i<active?'passed':'');if(i===active)li.setAttribute('aria-current','step');return li;}));
    $('workflow-summary').textContent=job.running&&live.stage==='trajectory_probe'?`독립 ${live.condition} 재질문 · 경로 반복 ${live.path_repeat} · ${live.turn}턴 · ${live.repeat?'응답 반복 '+live.repeat:'대상 선택'}`:job.running&&live.stage==='trajectory'?`독립 ${live.condition} 경로 · 반복 ${live.repeat} · ${live.turn}턴`:job.running?`공통 ${live.turn||job.common_turn}턴 · ${live.stage==='branch'?`${live.intervention} · 반복 ${live.repeat} · ${live.condition==='intent_linked'?'B':'A'}`:live.stage==='proposal'?'분기 질문 준비':'공통 경로 진행'}`:`${labels[job.status]||job.status} · ${job.checkpoints.filter(c=>['completed','skipped'].includes(c.status)).length}개 시점 처리`;
    $('branch-progress').max=Math.max(1,job.planned_branch_responses);$('branch-progress').value=job.completed_branch_responses||0;
    $('progress-caption').textContent=`분기 내담자 응답 ${job.completed_branch_responses||0} / ${job.planned_branch_responses}개 저장 · 개입·반복마다 A → B를 수행한 뒤 공통 경로로 돌아갑니다.`;
    drawTrajectories();drawTrajectoryProbes();
    const states={waiting:'응답 대기',generating:'공개 발화 생성 중',pending:'발화 작성됨 · 내면·노트 저장 중',committed:'저장 완료',discarded:'미확정 · 폐기됨'};
    $('live-phase').textContent=job.running?`${live.actor==='client'?'내담자':'상담사'} · ${live.phase||'준비 중'} · ${states[live.speech_status]||''}`:job.error||'현재 진행 중인 생성이 없습니다.';
    $('live-speech').textContent=job.running?(live.speech||'공개 발화가 준비되면 여기에 표시합니다. 내담자의 비공개 추론이나 도구 원문은 표시하지 않습니다.'):'완료된 발화는 아래 결과와 공통 대화에서 확인할 수 있습니다.';
    const sig=JSON.stringify(job.common_dialogue||[]);if($('common-transcript').dataset.signature!==sig){$('common-transcript').dataset.signature=sig;$('common-transcript').replaceChildren(...(job.common_dialogue||[]).map(m=>node('p',`${m.role==='client'?'내담자':'상담사'}: ${m.content}`)));}
    $('checkpoint-track').replaceChildren(...Array.from({length:job.options.end_turn-job.options.start_turn+1},(_,i)=>{
      const n=job.options.start_turn+i,c=job.checkpoints.find(c=>c.turn===n),b=node('button',`${n}턴 · ${labels[c?.status]||'대기'}${c?' · '+c.branches.filter(r=>r.status==='completed').length+'/'+c.branches.length:''}`);
      b.disabled=!c;b.onclick=()=>document.getElementById('cp-'+n)?.scrollIntoView({behavior:'smooth'});return b;
    }));
    $('research-summary').textContent=`실행 결과와 연구 적합성은 별도입니다. 검토 후 후보로 표시한 시점 ${job.reviewed_candidates||0}개. 공개 이력 기반 관련성 표시는 LLM의 참고 판단이며, 기억 효과의 증거가 아닙니다.`;
  }
  function drawTrajectories(){
    $('trajectory-summary').textContent=job.options.include_c?`내담자 응답 ${job.completed_trajectory_responses||0} / ${job.planned_trajectory_responses||0}개 · C는 명시적 내면 생성 없이 공개 대화 인용으로 노트를 갱신합니다.`:'이 실험은 독립 A/C 경로를 생성하지 않습니다.';
    const sig=JSON.stringify([job.trajectories,job.running,job.status]);if($('trajectories').dataset.signature===sig)return;
    $('trajectories').dataset.signature=sig;$('trajectories').replaceChildren();
    if(!job.trajectories?.length)return;
    const table=document.createElement('table'),head=document.createElement('tr');for(const t of ['반복','A · 내면을 노트로 유지','C · 공개 정보 노트'])head.append(node('th',t));table.append(head);
    for(let r=1;r<=job.options.trajectory_repeats;r++){const tr=document.createElement('tr');tr.append(node('td',String(r)));
      for(const condition of ['A','C']){const row=job.trajectories.find(x=>x.repeat===r&&x.condition===condition),td=document.createElement('td'),status=row?.status==='running'&&!job.running?job.status:row?.status;
        td.append(node('p',`${labels[status]||'대기'} · ${row?.turns||0}/${job.options.end_turn}턴`));
        if(row?.session_id){const b=node('button','대화·입력·노트 보기');b.onclick=()=>detail(row.session_id);td.append(b);}tr.append(td);
      }table.append(tr);
    }$('trajectories').append(table);
  }
  function drawTrajectoryProbes(){
    const enabled=job.revision>=5&&job.options.include_c&&job.options.probe_mode==='past_intent';
    $('ac-probe-summary').textContent=enabled?`추가 재질문 응답 ${job.completed_trajectory_probe_responses||0} / ${job.planned_trajectory_probe_responses||0}개 저장 · A 경로 반복 1의 응답은 A/B 결과와 공유합니다.`:'이 실험에는 독립 A/C 재질문이 없습니다. 새 실험에서 당시 의도 확인 모드와 C 대조 실험을 함께 선택하세요.';
    const signature=JSON.stringify([job.id,job.checkpoints,job.trajectories,job.status]);
    if($('ac-probes').dataset.signature===signature)return;
    $('ac-probes').dataset.signature=signature;$('ac-probes').replaceChildren();if(!enabled)return;
    for(let repeat=1;repeat<=job.options.trajectory_repeats;repeat++){
      const group=document.createElement('div');group.append(node('h3',`경로 반복 ${repeat}`));
      const table=document.createElement('table'),head=document.createElement('tr');
      for(const title of ['분기 시점','A · 내면을 노트로 유지','C · 공개 정보 노트'])head.append(node('th',title));table.append(head);
      for(let n=job.options.start_turn;n<=job.options.end_turn;n++){
        const tr=document.createElement('tr');tr.append(node('td',`${n}턴 뒤`));
        for(const condition of ['A','C']){
          const path=job.trajectories.find(r=>r.condition===condition&&r.repeat===repeat);
          const checkpoints=condition==='A'&&repeat===1?job.checkpoints:(path?.probes||[]);
          const cp=checkpoints.find(c=>c.turn===n),p=cp?.proposal?.proposal,td=document.createElement('td');
          td.dataset.condition=condition;
          if(p){td.append(node('p',`대상 ${p.target_turn}턴: ${p.target_quote}`),node('p',`질문: ${p.I1}`));
            const branches=cp.branches.filter(b=>condition==='C'?b.condition==='public_only':b.condition==='summary_only');
            if(!branches.length)td.append(node('p','응답 준비 중'));
            for(const b of branches){td.append(node('p',`응답 반복 ${b.repeat} · ${labels[b.status]||b.status}`));
              for(const [i,response]of(b.responses||[]).entries())td.append(node('p',`${i+1}. ${response}`));
              if(b.session_id){const button=node('button','대화·입력·노트 보기');button.onclick=()=>detail(b.session_id);td.append(button);}
            }
          }else td.append(node('p',cp?'대상 선택 중':'대기'));
          tr.append(td);
        }table.append(tr);
      }group.append(table);$('ac-probes').append(group);
    }
  }
  async function download(blind){try{const data=await api('/api/q3p6/export?id='+job.id+'&blind='+(blind?1:0)),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=job.id+(blind?'_public_blind':'_research')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){error(e);}}
  function reviewControls(cp, section){
    const box=document.createElement('details');box.dataset.key='review-'+cp.turn;box.append(node('summary','사례 검토 · 실행 완료와 별도'));
    const status=document.createElement('select');status.setAttribute('aria-label','사례 검토 상태');
    for(const [v,t] of [['unreviewed','미검토'],['candidate','분석 후보'],['unsuitable','현재 주장 검토에 부적합']])status.append(new Option(t,v));status.value=cp.review?.status||'unreviewed';
    const note=document.createElement('textarea');note.value=cp.review?.note||'';note.placeholder='당시 생각과 후속 반응의 관계, 대안 설명, 적합성 판단 근거';note.setAttribute('aria-label','사례 검토 메모');
    const save=node('button','검토 저장');save.disabled=job.running;save.onclick=async()=>{try{job=await api('/api/q3p6/review',{id:job.id,turn:cp.turn,status:status.value,note:note.value});draw();}catch(e){error(e);}};
    box.append(node('p','당시 내면이 공개 발화에 이미 드러났는지, 후속 질문이 그 맥락과 관련되는지, 현재 변화로 설명 가능한지 함께 검토하세요. 실행 중에는 일시정지 후 저장할 수 있습니다.'),status,note,save);section.append(box);
  }
  function draw(){
    $('start-run').disabled=!job||job.running||job.status==='completed';$('pause').disabled=!job?.running;$('stop').disabled=!job?.running;$('common').disabled=!job;
    $('export-blind').disabled=!job||job.running;$('export-raw').disabled=!job||job.running;
    if(!job)return;
    $('status').textContent=`${job.id} · ${labels[job.status]||job.status} · 공통 ${job.common_turn}턴 / 마지막 분기 ${job.options.end_turn}턴 · ${job.running ? job.activity || '준비 중' : ''}`;
    $('job-settings').textContent=`저장된 실행 설정: ${job.options.probe_mode==='past_intent'?'당시 의도 확인 · 고정 재질문':'맥락에 맞는 후속 질문'} · ${job.options.start_turn}~${job.options.end_turn}턴 분기 · 개입 ${job.options.intervention_mode==='single'?1:2}개 · 분기 후 ${job.options.branch_turns}턴 · 조건별 ${job.options.repeats}회 · 최대 ${job.planned_branch_responses}개 분기 응답`;
    $('error').textContent=job.error||'';
    drawWorkflow();
    // Rebuild only when persisted result content changes, preserving open details during polling.
    const signature=JSON.stringify([job.checkpoints,job.running]);if($('results').dataset.signature===signature)return;
    const opened=new Set([...$('results').querySelectorAll('details[open]')].map(d=>d.dataset.key));
    $('results').dataset.signature=signature;$('results').replaceChildren();
    for(const cp of job.checkpoints){const section=node('div','');section.className='checkpoint';section.id='cp-'+cp.turn;section.append(node('h3',`공통 ${cp.turn}턴 뒤 · ${labels[cp.status]||cp.status}`));
      const p=cp.proposal?.proposal;if(!p){section.append(node('p','개입 준비 중'));$('results').append(section);continue;}
      section.append(node('p',`대상 선택 근거: ${p.reason}`));if(cp.skip_reason)section.append(node('p',cp.skip_reason));
      if(job.revision>=2){section.append(node('p',`공개 반응: ${p.response_kind} · 관련성 참고: ${p.relevance==='potential'?'관찰 여지 있음':'약함'} · 대상 이후 ${cp.target_lag??'?'}턴 경과`));
        const bundle=document.createElement('details');bundle.dataset.key='target-'+cp.turn;
        bundle.append(node('summary',`대상 ${p.target_turn}턴 발화와 당시 내면 확인`),node('p',p.target_quote),node('pre',JSON.stringify(cp.target_evidence,null,2)));section.append(bundle);reviewControls(cp,section);
      }
      const evidence=document.createElement('details');evidence.dataset.key='proposal-'+cp.turn;evidence.append(node('summary',`대상 ${p.target_turn||'없음'}턴 · 개입 준비 입력과 판단 근거`),node('pre',JSON.stringify(cp.proposal,null,2)));section.append(evidence);
      if(cp.status!=='skipped'&&(job.revision>=2||p.eligible==='yes'))for(const intervention of (job.options.intervention_mode==='single'?['I1']:['I1','I2'])){const speech=node('p',`${intervention}: ${p[intervention]}`);speech.className='intervention';section.append(speech);
        const table=document.createElement('table'),head=document.createElement('tr');for(const x of ['반복','A · 현재 노트','B · 당시 기록 추가'])head.append(node('th',x));table.append(head);
        for(let rep=1;rep<=job.options.repeats;rep++){const tr=document.createElement('tr');tr.append(node('td',String(rep)));
          for(const mode of ['summary_only','intent_linked']){const row=cp.branches.find(r=>r.intervention===intervention&&r.repeat===rep&&r.condition===mode);const td=document.createElement('td');td.append(node('p',row?.responses?.join('\n\n')||'아직 생성된 반응이 없습니다.'));const rowStatus=row?.status==='running'&&!job.running?job.status:row?.status;const st=node('p',labels[rowStatus]||'대기');st.className='status';td.append(st);if(row?.session_id){const b=node('button','입력·내면·대화 보기');b.onclick=()=>detail(row.session_id);td.append(b);}tr.append(td);}table.append(tr);}section.append(table);
      }$('results').append(section);
    }
    for(const d of $('results').querySelectorAll('details'))if(opened.has(d.dataset.key))d.open=true;
  }
  $('create-form').onsubmit=async e=>{e.preventDefault();error();$('create').disabled=true;try{const generation={...config.generation,temperature:Number($('temperature').value),max_tokens:Number($('tokens').value)};if($('seed').value==='')delete generation.seed;else generation.seed=Number($('seed').value);
    job=await api('/api/q3p6/create',{case_mode:$('case-mode').value,options:{probe_mode:$('probe-mode').value,intent_probe_template:$('intent-probe-template').value,include_c:$('include-c').value==='yes',trajectory_repeats:Number($('trajectory-repeats').value),branch_policy:$('branch-policy').value,intervention_mode:$('intervention-mode').value,start_turn:Number($('start').value),end_turn:Number($('end').value),branch_turns:Number($('length').value),repeats:Number($('repeats').value),input_char_limit:Number($('input-limit').value)},request:{scenario:JSON.parse($('scenario').value),model:$('model').value,thinking_mode:$('thinking').value,vllm_url:endpointValue(),generation,counselor_system_prompt:$('counselor-prompt').value,messages:[{role:'user',content:$('opening').value}]}});await list();await select(job.id);
  }catch(e){error(e);}finally{$('create').disabled=false;}};
  for(const [id,action] of [['start-run','start'],['pause','pause'],['stop','stop']])$(id).onclick=async()=>{try{error();$(id).disabled=true;job=await api('/api/q3p6/'+action,{id:job.id});draw();if(action==='pause')$('status').textContent+=' · 현재 작업 완료 후 멈춥니다.';}catch(e){error(e);draw();}};
  $('jobs').onchange=()=>{if($('jobs').value)select($('jobs').value).catch(error);};$('refresh').onclick=()=>list().catch(error);$('common').onclick=()=>detail(job.common_session_id);$('close-detail').onclick=()=>$('detail').close();
  for(const id of ['start','end','length','repeats','intervention-mode','include-c','trajectory-repeats','branch-policy'])$(id).oninput=estimate;
  $('profile').onchange=()=>{const n=$('profile').value;const profile=n===''?defaultScenario.profile:profiles[Number(n)].profile;$('case-mode').value='fresh';$('scenario').value=JSON.stringify(freshScenario(profile),null,2);$('opening').value=opening;caseSummary();};
  $('case-mode').onchange=()=>{if($('case-mode').value==='fresh'){try{const s=JSON.parse($('scenario').value);$('scenario').value=JSON.stringify(freshScenario(s.profile),null,2);$('opening').value=opening;}catch(e){error(e);}}else{$('opening').value='';}caseSummary();};
  $('scenario').oninput=caseSummary;
  $('research-case').onclick=()=>{$('profile').value='';$('case-mode').value='fresh';$('scenario').value=JSON.stringify(freshScenario(researchProfile),null,2);$('opening').value=opening;caseSummary();};
  $('export-blind').onclick=()=>download(true);$('export-raw').onclick=()=>download(false);
  async function init(){config=await api('/api/config');const versions=await api('/api/versions');const cs=await api('/api/paired/counselor');
    defaultScenario=config.scenario;$('scenario').value=JSON.stringify(freshScenario(defaultScenario.profile),null,2);restoreEndpoint(config.vllm_url||'http://127.0.0.1:18001/v1');
    const opts=await api('/api/q3p6/options');researchProfile=opts.research_profile;$('intent-probe-template').value=opts.intent_probe_template;$('intent-target-prompt').textContent=opts.intent_target_prompt;$('intervention-prompt').textContent=opts.intervention_prompt;const models=opts.models;for(const m of models)$('model').append(new Option(typeof m==='string'?m:m.label,typeof m==='string'?m:m.id));if(![...$('model').options].some(o=>o.value===config.model))$('model').append(new Option(config.model,config.model));$('model').value=config.model;
    $('temperature').value=config.generation.temperature;$('tokens').value=config.generation.max_tokens;
    $('counselor-prompt').value=cs.versions.find(v=>v.id==='Q3p6_counsel').system_prompt;
    const ps=await api('/api/profiles');profiles=ps.profiles;profiles.forEach((p,i)=>$('profile').append(new Option(p.profile.profile_id||p.filename||String(i),String(i))));
    caseSummary();estimate();await list();const previous=localStorage.getItem('q3p6.job');if(previous)try{await select(previous);}catch{localStorage.removeItem('q3p6.job');}
    if(!versions.versions.some(v=>v.id==='Q3p6'))throw Error('서버를 재시작하여 Q3p6를 등록하세요.');
  }
  init().catch(error);
  setInterval(async()=>{if(!job||polling||document.hidden)return;polling=true;try{await select(job.id);}catch(e){error(e);}finally{polling=false;}},1500);
})();
