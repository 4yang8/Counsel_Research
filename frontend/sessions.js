'use strict';
(() => {
  const panel=document.createElement('section');panel.className='session-sidebar';panel.setAttribute('aria-label','저장된 대화 세션');
  panel.innerHTML='<div class="session-heading"><strong>채팅 내역</strong><button type="button" id="sessions-refresh" aria-label="채팅 내역 새로고침">↻</button></div><button type="button" id="sessions-new">＋ 새 대화</button><input id="sessions-search" type="search" placeholder="대화 검색" aria-label="저장된 대화 검색"><p id="sessions-status" role="status"></p><div id="sessions-list" aria-label="대화 세션 목록"></div>';
  document.querySelector('.config-sidebar').append(panel);
  let rows=[],locked=false,loading=false,selected=null,revision=0;
  function render(){
    const list=$('sessions-list'),scroll=list.scrollTop;list.replaceChildren();
    const query=$('sessions-search').value.trim().toLowerCase();
    for(const row of rows.filter(row=>[row.title,row.session_id,row.version,row.model].join(' ').toLowerCase().includes(query))){
      const button=document.createElement('button');button.type='button';button.className='session-item';button.dataset.key=row.key;
      button.disabled=locked || loading;button.setAttribute('aria-current',String(row.key===selected));
      const title=document.createElement('span');title.textContent=row.title || '새 대화';
      const meta=document.createElement('small');meta.textContent=row.version+' · '+(row.kind==='paired'?'대화':'단독 실험')+' · '+row.count+'개';
      const date=document.createElement('small');date.className='session-date';date.textContent=new Date(row.modified*1000).toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
      button.title=row.session_id+'\n'+row.model;button.append(title,meta,date);
      button.onclick=async()=>{
        if(locked || loading)return;loading=true;render();$('sessions-status').textContent='불러오는 중…';
        try{
          if(row.kind==='paired')await Paired.load(row.session_id,row.key);
          else await loadStandalone(row);
          selected=row.key;render();$('sessions-status').textContent='';
        }catch(error){notice(error.message);$('sessions-status').textContent='불러오기 실패';}
        finally{loading=false;render();}
      };
      list.append(button);
    }
    if(!list.children.length){const empty=document.createElement('p');empty.className='hint';empty.textContent=query?'검색 결과가 없습니다.':'저장된 대화가 없습니다.';list.append(empty);}
    list.scrollTop=scroll;$('sessions-new').disabled=locked || loading || settingsLoading || Paired.running;
  }
  async function refresh(){
    const version=++revision;
    try{const result=await api('/api/sessions');if(version!==revision)return;rows=result.sessions;
      const active=document.body.classList.contains('paired-mode')?Paired.sessionId:sessionId;
      selected=rows.find(row=>row.session_id===active && (document.body.classList.contains('paired-mode')?row.kind==='paired':row.kind===currentVersion))?.key || null;
      render();$('sessions-status').textContent='';
    }catch(error){$('sessions-status').textContent='목록 조회 실패: '+error.message;}
  }
  async function loadStandalone(row){
    await versionReady;
    const saved=await api('/api/sessions/standalone?key='+encodeURIComponent(row.key));
    if(!saved.turns.length)throw Error('읽을 대화 기록이 없습니다.');
    Paired.reset(false);clearConversation(false);clearTimeout(timer);++previewVersion;
    const completed=saved.turns.filter(turn=>turn.record.status==='completed');
    const last=(completed.at(-1) || saved.turns.at(-1)).record,input=last.input;
    currentVersion=saved.version;$('experiment-version').value=currentVersion;versionPreferences.set('client',currentVersion);
    const profileEntry=historyCatalog.profiles.find(p=>p.histories.some(h=>h.scenario.id===input.scenario?.id));$('experiment-profile').value=profileEntry?.number || 'default';populateHistoryChoices();if(profileEntry)$('history-select').value=profileEntry.histories.find(h=>h.scenario.id===input.scenario?.id).id;
    $('summary-settings').hidden=!stateSummaryMode();$('state-memory').hidden=!stateSummaryMode();$('state-input-hint').hidden=!stateSummaryMode();
    editorScenario=structuredClone(input.scenario || config.scenario);setupConditions();
    activeScenario=structuredClone(editorScenario);activeSystem=input.system_prompt || '';activeTools=input.enabled_tools || null;
    $('system_prompt').value=activeSystem;$('summary_system_prompt').value=input.summary_system_prompt || versionCatalog.versions.find(v=>v.id===currentVersion).summary_system_prompt;
    $('profile-context-mode').value=input.profile_context_mode || 'selective';
    for(const item of document.querySelectorAll('[data-block]')){item.checked=input.input_blocks?.[item.dataset.block] ?? true;item.closest('.setting-block').dataset.enabled=String(item.checked);}
    for(const key of ['temperature','top_p','max_tokens','seed'])$(key).value=input.generation?.[key] ?? config.generation[key] ?? '';
    restoreClientBudgets(input.client_reasoning_budgets);
    selectModel(input.model || last.request?.model || config.model);$('thinking-mode').value=input.thinking_mode || (last.request?.chat_template_kwargs?.enable_thinking===true?'on':last.request?.chat_template_kwargs?.enable_thinking===false?'off':'default');
    $('trial-mode').value='continue';sessionId=saved.session_id;restoredSessionKey=row.key;
    enabledToolNames=activeTools;await refreshTools();clearTimeout(timer);++previewVersion;
    history=last.status==='completed'?(last.history || []):[];
    $('call-selector').replaceChildren();
    for(const turn of saved.turns){
      const stored=turn.record;records.push({...stored,events:turn.events,liveTrace:PairedTrace.create('client',stored.turn_id,records.length+1,'basic',currentVersion)});Journey.begin(stored.input);
      bubble('user',stored.input.messages.at(-1).content);const response=bubble('assistant',stored.response || '');
      if(stored.status!=='completed')response.el.classList.add('failed');
      for(const event of turn.events){Journey.accept(event);PairedTrace.accept(records.at(-1).liveTrace,event);
        if(event.type==='state_record')innerRecords.push(event.record);
        if(event.type==='request'){lastRequest=event.payload;modelCalls.push({turn:records.length,iteration:event.iteration || 1,payload:event.payload});$('call-selector').add(new Option('대화 '+records.length+' · 모델 호출 '+(event.iteration || 1),String(modelCalls.length-1)));}
        if(event.type==='inspection' && modelCalls.length)modelCalls.at(-1).inspection=event;
      }
      mountQ1Process(records.at(-1),response);
    }
    $('state-records').textContent=pretty(innerRecords);
    if(innerRecords.length){$('state-after').textContent=pretty(innerRecords.at(-1).state_after);$('state-memory-status').textContent='불러온 내면 기록 '+innerRecords.length+'개';}
    $('message').value='';$('status').textContent='저장된 단독 실험 불러옴';$('save-status').textContent='이어가기는 이 세션에 저장됩니다.';
    LabStorage.removeItem('counseling-paired-session');Paired.showStandalone();if(records.at(-1)?.liveTrace)LabVersionViews.saved(records.at(-1).liveTrace);syncConditions();Journey.refresh();
  }
  $('sessions-refresh').onclick=refresh;$('sessions-search').oninput=render;
  $('sessions-new').onclick=()=>{Paired.reset();selected=null;render();};
  document.addEventListener('lab-session-status',event=>{
    locked=event.detail.busy || busy || Paired.running;
    const sid=document.body.classList.contains('paired-mode')?Paired.sessionId:sessionId;
    selected=rows.find(row=>row.session_id===sid)?.key || null;render();
  });
  document.addEventListener('lab-sessions-changed',refresh);document.addEventListener('lab-settings-ready',refresh);
  document.addEventListener('lab-storage-changed',refresh);refresh();
})();
