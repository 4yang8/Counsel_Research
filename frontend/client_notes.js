'use strict';
(() => {
  const panel=document.createElement('section');panel.id='client-notes-view';panel.className='client-notes-view';
  panel.innerHTML='<div class="note-view-heading"><h2>내담자 심리상태 노트</h2><button type="button" id="client-notes-refresh">새로고침</button></div><p id="client-notes-status" role="status"></p><div class="note-view-controls"><label>저장 시점<select id="client-notes-turn"><option value="latest">최신 파일</option></select></label><label>표시<select id="client-notes-mode"><option value="after">노트 원문</option><option value="diff">변경 내역과 근거</option><option value="before">갱신 전 원문</option></select></label></div><p id="client-notes-path" class="hint"></p><div id="client-notes-body"></div>';
  document.querySelector('.results-scroll').append(panel);window.registerLabView('client_notes',panel);
  const tab=document.createElement('button');tab.type='button';tab.id='client-notes-tab';tab.className='config-tab';tab.textContent='내담자 노트';tab.dataset.view='client_notes';tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panel.id);document.querySelector('.config-tabs').append(tab);
  let data=null,revision=0,contextKey='';
  const el=(tag,text,cls='')=>Object.assign(document.createElement(tag),{textContent:text,className:cls});
  function draw(){
    const target=$('client-notes-body');target.replaceChildren();if(!data?.available)return;
    const turn=$('client-notes-turn').value,record=turn==='latest'?data.records.at(-1):data.records.find(r=>r.turn_id===turn),mode=$('client-notes-mode').value;
    $('client-notes-path').textContent=data.path || '새 대화 초기 노트 · 아직 저장된 발화 없음';
    if(['Q3a','Q3b'].includes(data.version)){
      const plan=record?.local_plan;
      target.append(el('strong','로컬 작업 계획 · 해당 턴의 수행 기록 / 다음 턴에는 새로 작성'));
      if(record?.planning_mode==='direct'){target.append(el('p','별도 작업 계획 없이 반응을 판단했습니다.'));}
      else if(plan?.steps?.length){
        target.append(el('p',plan.goal));
        for(const step of plan.steps || []){
          const box=el('section','','turn-plan-step');box.dataset.status=step.status;
          box.append(el('strong',({pending:'□ 대기',in_progress:'▶ 진행 중',completed:'✓ 완료',skipped:'― 생략'}[step.status] || step.status)+' · '+step.task),el('p','완료 기준: '+step.completion_criterion,'hint'),el('p','결과: '+(step.result || '(내용 없음)')),el('p','근거: '+(step.sources || []).join(' / '),'hint'));target.append(box);
        }
        if(mode==='diff')for(const entry of record.local_plan_history || []){
          const detail=el('details');detail.append(el('summary','계획 변경 '+entry.revision+' · '+entry.arguments.reason),el('pre',JSON.stringify(entry,null,2)));target.append(detail);
        }
      }else target.append(el('p','(내용 없음)'));
    }
    if(['Q3','Q3a','Q3b'].includes(data.version)){
      const progress=mode==='before'?record?.trajectory_before:turn==='latest'?data.trajectory_progress:record?.trajectory_progress;
      const text=p=>p?Object.entries(p).map(([k,v])=>({position:'현재 위치',movement:'변화',current_experience:'현재 신뢰와 경계',grounds:'판단 근거',next_condition:'다음에 살필 조건'}[k] || k)+': '+({maintain:'유지·정체',advance:'진행',retreat:'후퇴'}[v] || v || '(내용 없음)')).join('\n'):'(내용 없음)';
      target.append(el('strong','사전 궤적 · '+(turn==='latest'?'다음 생성에 사용할 파일':'해당 턴 입력 원문')),el('pre',turn==='latest'?data.trajectory:record?.trajectory_source || '(내용 없음)'));
      target.append(el('strong','궤적 진행 · '+(mode==='before'?'갱신 전':'저장된 기록')),el('pre',text(progress)));
      if(mode==='diff' && record)target.append(el('strong','이번 잠정 계획'),el('pre',text(record.trajectory_plan)));
      target.append(el('strong','내담자 노트'));
    }
    if(mode==='after'){target.append(el('pre',turn==='latest'?data.content:record?.notes_after || data.content));return;}
    if(mode==='before'){target.append(el('pre',record?.notes_before || data.content));return;}
    if(!record){target.append(el('p','아직 저장된 변경 이력이 없습니다.','hint'));return;}
    target.append(el('p','내담자 턴 '+record.turn+' · '+record.utterance,'note-view-speech'));
    if(Array.isArray(record.note_operations)){
      for(const op of record.note_operations){
        const section=el('section','','note-view-change');
        const label=record.note_format==='free_items_v1'?'내담자 노트':op.label || data.state_labels?.[op.section] || ('현재 내면 · '+(data.inner_labels?.[op.section] || op.section));
        section.append(el('strong',label+' · '+({add:'추가',update:'수정',delete:'삭제'})[op.action]+' · '+op.item_id));
        if(op.before)section.append(el('div','− '+op.before,'note-deleted'));
        if(op.action!=='delete')section.append(el('div','+ '+op.content,'note-added'));
        section.append(el('p','변경 근거: '+op.reason));target.append(section);
      }
      const changed=new Set(record.note_operations.map(op=>op.section));
      for(const route of record.state_routing || [])if(!changed.has(route.state))target.append(el('p',(data.state_labels?.[route.state] || route.state)+' · 유지 · 검토 근거: '+route.reason));
      if(!record.note_operations.length)target.append(el('p','현재 노트 항목을 유지했습니다.','hint'));
      if(record.legacy_note_converted)target.append(el('p',record.note_format==='free_items_v1'?'기존 내용과 항목 ID를 보존하여 자유 글머리 형식으로 정리했습니다.':'기존 문단의 내용은 보존하고 항목 ID를 부여했습니다.','hint'));
      return;
    }
    const updated=new Map((record.state_updates || []).map(row=>[row.state,row]));
    for(const route of record.state_routing || []){
      const row=updated.get(route.state),section=el('section','','note-view-change');
      section.append(el('strong',(data.state_labels?.[route.state] || route.state)+' · '+(row?'변경':'유지')),el('p','검토 근거: '+route.reason));
      if(row)section.append(el('p','변경 근거: '+row.reason));target.append(section);
    }
    const innerUpdates=record.inner_updates || [];
    for(const row of innerUpdates){
      const section=el('section','','note-view-change');section.append(el('strong','현재 내면 · '+(data.inner_labels?.[row.section] || row.section)),el('p','변경 근거: '+row.reason));target.append(section);
    }
    if(!updated.size)target.append(el('p',innerUpdates.length?'10가지 심리상태는 유지하고 현재 내면을 갱신했습니다.':'현재 상태 설명을 유지했습니다.','hint'));
    for(const line of record.diff || [])target.append(el('div',line,line.startsWith('+')?'note-added':line.startsWith('-')?'note-deleted':'note-diff-context'));
  }
  async function refresh(){
    const rev=++revision,paired=document.body.classList.contains('paired-mode'),sid=paired?Paired.sessionId:sessionId;
    const key=[paired,sid,currentVersion].join(':');if(key!==contextKey){contextKey=key;data=null;$('client-notes-body').replaceChildren();$('client-notes-turn').replaceChildren(new Option('최신 파일','latest'));}
    const params=new URLSearchParams({version:currentVersion});if(sid)params.set('session_id',sid);if(paired)params.set('paired','1');if(!paired && restoredSessionKey)params.set('key',restoredSessionKey);
    $('client-notes-status').textContent='조회 중…';
    try{
      const result=await api('/api/client_notes?'+params);if(rev!==revision)return;data=result;panel.querySelector('h2').textContent=['Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(result.version)?'내담자 노트 · 자유 글머리':['Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(result.version)?'내담자 노트 · 현재 내면 / 심리상태':'내담자 심리상태 노트';
      const choice=$('client-notes-turn').value;const picker=$('client-notes-turn');picker.replaceChildren(new Option('최신 파일','latest'));
      for(const row of result.records || [])picker.add(new Option('내담자 턴 '+row.turn,row.turn_id));
      if([...picker.options].some(o=>o.value===choice))picker.value=choice;
      $('client-notes-status').textContent=result.available?'완료된 내담자 턴 '+result.records.length+'개 · 노트 갱신 완료 후 반영':result.message;
      $('client-notes-path').textContent='';draw();
    }catch(error){if(rev===revision){$('client-notes-status').textContent='조회 실패: '+error.message;$('client-notes-body').replaceChildren();}}
  }
  tab.onclick=()=>{window.showLabResult('client_notes');refresh();};$('client-notes-refresh').onclick=refresh;
  $('client-notes-turn').onchange=draw;$('client-notes-mode').onchange=draw;
  for(const name of ['lab-sessions-changed','lab-session-status'])document.addEventListener(name,()=>{if(!panel.hidden && !Paired.running && !busy)refresh();});
  $('experiment-version').addEventListener('change',()=>{if(!panel.hidden)refresh();});
})();

// A single current-file view for both actors. Independent of selected past calls.
(() => {
  const panel=document.createElement('section');panel.id='session-notes-view';panel.className='session-notes-view';
  panel.innerHTML='<div class="note-view-heading"><h2>현재 노트</h2><button type="button" id="session-notes-refresh">새로고침</button></div><p id="session-notes-status" role="status"></p><div id="session-notes-body"></div>';
  document.querySelector('.results-scroll').append(panel);window.registerLabView('session_notes',panel);
  const tab=document.createElement('button');tab.type='button';tab.id='session-notes-tab';tab.textContent='노트';tab.dataset.view='session_notes';tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panel.id);
  document.querySelector('.context-tabs').append(tab);
  const node=(tag,text,cls='')=>Object.assign(document.createElement(tag),{textContent:text,className:cls});
  let revision=0,stamp='',context='',timer;
  async function refresh(){
    if(panel.hidden)return;
    const rev=++revision,paired=document.body.classList.contains('paired-mode'),sid=paired?Paired.sessionId:sessionId;
    const key=JSON.stringify([paired,sid,currentVersion,restoredSessionKey]);
    if(key!==context){context=key;stamp='';$('session-notes-body').replaceChildren();}
    const params=new URLSearchParams({version:currentVersion});if(sid)params.set('session_id',sid);if(paired)params.set('paired','1');if(!paired && restoredSessionKey)params.set('key',restoredSessionKey);
    try{
      const result=await api('/api/session_notes?'+params);if(rev!==revision)return;
      $('session-notes-status').textContent=result.message;
      const next=JSON.stringify(result.notes);if(next===stamp)return;stamp=next;
      const target=$('session-notes-body'),scrolls=[...target.querySelectorAll('pre')].map(e=>e.scrollTop);target.replaceChildren();
      for(const [actor,title] of [['client','내담자가 작성한 노트'],['counselor','상담사가 작성한 노트']]){
        const group=node('section','','session-notes-group');group.dataset.actor=actor;group.append(node('h3',actor==='client' && result.notes.some(n=>['Q3','Q3a','Q3b'].includes(n.version))?'내담자 노트 · 궤적':title));
        const notes=result.notes.filter(note=>note.actor===actor);
        for(const note of notes){const box=node('section','','session-note-box');box.append(node('strong',note.version+' · '+note.label),node('pre',note.content || '(빈 노트)'));group.append(box);}
        if(!notes.length)group.append(node('p','현재 저장된 노트가 없습니다.','hint'));target.append(group);
      }
      [...target.querySelectorAll('pre')].forEach((e,i)=>{e.scrollTop=scrolls[i] || 0;});
    }catch(error){if(rev===revision){stamp='';$('session-notes-status').textContent='조회 실패: '+error.message;$('session-notes-body').replaceChildren();}}
  }
  tab.onclick=()=>{window.showLabResult('session_notes');refresh();};$('session-notes-refresh').onclick=refresh;
  for(const event of ['lab-sessions-changed','lab-session-status'])document.addEventListener(event,()=>{clearTimeout(timer);timer=setTimeout(refresh,100);});
  setInterval(()=>{if(!document.hidden)refresh();},2000);
})();
