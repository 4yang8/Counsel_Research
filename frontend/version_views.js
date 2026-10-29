'use strict';
// Shared space; each counselor version supplies metadata and an independent renderer.
window.LabVersionViews=(()=>{
  const renderers=new Map(),node=(tag,text='',cls='')=>Object.assign(document.createElement(tag),{textContent:text,className:cls});
  const region=node('section','','version-monitor');region.id='version-monitor';region.setAttribute('aria-label','버전별 실험 기록과 Reasoning');
  const versionBox=node('details','','version-specific');versionBox.open=true;versionBox.id='version-specific';
  const title=node('summary','상담사 버전별 기록');title.id='version-specific-title';const content=node('div','','version-specific-content');content.id='version-specific-content';versionBox.append(title,content);
  const reasoning=node('details','','reasoning-monitor');reasoning.id='reasoning-monitor';reasoning.open=true;
  const reasonTitle=node('summary','Reasoning · 스트리밍 원문');reasonTitle.id='reasoning-monitor-title';const calls=node('div','','reasoning-calls');calls.id='reasoning-calls';
  reasoning.append(reasonTitle,calls);region.append(versionBox,reasoning);
  let specs=[],version='basic',attempt=null,mode='ready',noteKey='',reasonKey='',currentData={};
  function install(){region.remove();}
  function standalone(){if(['Q1','Q1_s1','Q1_s2','Q1a','Q2','Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(currentVersion)){region.remove();return;}document.querySelector('.conversation-column .composer-dock').before(region);version='basic';mode='ready';attempt=null;draw();}
  function metadata(){return specs.find(row=>row.id===version) || {id:version,view:'generic'};}
  function draw(){
    const spec=metadata(),versions=attempt?.extras?.versions || {},data=versions[version] || currentData[version] || {};
    versionBox.hidden=spec.view==='none';title.textContent=version+' · '+(mode==='live'?'이번 생성 기록':mode==='saved'?'선택한 발화 기록':'실험 기록');
    const key=JSON.stringify([version,mode,data,attempt?.extras?.c4]);
    if(key!==noteKey){noteKey=key;content.replaceChildren();const renderer=renderers.get(spec.view);if(renderer)renderer(content,data,attempt,spec);else content.append(node('pre',JSON.stringify(data,null,2)));}
    const key2=JSON.stringify([attempt?.id,mode,attempt?.calls?.map(row=>[row.agent,row.phase,row.reasoning])]);
    if(key2!==reasonKey){
      reasonKey=key2;const positions=[...calls.querySelectorAll('pre')].map(pre=>pre.scrollTop);calls.replaceChildren();
      reasonTitle.textContent='Reasoning · '+(mode==='live'?(attempt?.status==='done'?'이번 생성 원문':'현재 생성 스트리밍'):mode==='saved'?'선택한 발화 원문':'수신 대기');
      for(const [index,call] of (attempt?.calls || []).entries()){
        const entry=node('section','','reasoning-call');entry.append(node('strong',(index+1)+' · '+(call.name || call.agent)+' · '+call.phase));
        const pre=node('pre',call.reasoning || '이 호출에서 분리된 Reasoning이 아직 수신되지 않았습니다.');pre.dataset.call=String(index);pre.tabIndex=0;entry.append(pre);calls.append(entry);pre.scrollTop=positions[index] || 0;
      }
      if(!calls.children.length)calls.append(node('p','서버가 반환하는 reasoning / reasoning_content를 호출별로 빠짐없이 표시합니다.','hint'));
    }
  }
  const register=(view,render)=>renderers.set(view,render);
  function renderNoteOperations(target,operations){
    for(const op of operations){
      const box=node('section','','note-item-operation');
      box.append(node('strong',({add:'추가',update:'수정',delete:'삭제'})[op.action]+' · '+(op.label || op.section)+' · '+op.item_id));
      if(op.before)box.append(node('div','− '+op.before,'note-deleted'));
      if(op.action!=='delete')box.append(node('div','+ '+op.content,'note-added'));
      box.append(node('small','변경 근거: '+op.reason));target.append(box);
    }
  }
  register('notes',(target,data)=>{
    const notes=data.notes,current=notes?.current;
    target.append(node('small',current?.path || '새 세션의 notes.md는 빈 파일로 시작합니다.'));
    const pre=node('pre',current?.content || '(빈 노트)','p1-note-content');target.append(pre);
    if(data.hideChanges)return;
    const changes=(notes?.history || []).filter(event=>event.action==='updated');
    const diff=node('div','','note-changes p1-note-changes');
    if(!changes.length)diff.append(node('p','이 발화에서 저장된 노트 변경이 없습니다.','hint'));
    for(const [index,event] of changes.entries()){
      diff.append(node('strong','변경 '+(index+1)));
      if(Array.isArray(event.data.operations)){renderNoteOperations(diff,event.data.operations);continue;}
      for(const line of event.data.diff || [])diff.append(node('div',line,line.startsWith('+')?'note-added':line.startsWith('-')?'note-deleted':'note-diff-context'));
    }
    target.append(diff);
  });
  register('dual_notes',(target,data)=>{
    for(const [key,label] of [['client_notes','내담자 노트'],['counselor_notes','상담사 생각 노트']]){
      const section=node('section','','p2-note');section.dataset.note=key;
      section.append(node('strong',label));
      renderers.get('notes')(section,{notes:data[key],hideChanges:data.hideChanges});target.append(section);
    }
  });
  register('client_inner',(target,data)=>{
    const traits=data.traits?.current?.selected || [];
    target.append(node('strong','이번 관련 특성 · 자동 라우팅'));
    target.append(node('pre',traits.length?JSON.stringify(traits,null,2):'관련 특성 없음 ([])'));
    const appraisal=data.appraisal?.current;
    if(appraisal){target.append(node('strong','이번 appraisal'));target.append(node('pre',JSON.stringify(appraisal,null,2)));}
    target.append(node('strong','현재 비공개 내면 노트'));
    renderers.get('notes')(target,{notes:data.inner_notes,hideChanges:data.hideChanges});
    if(data.inner_notes?.current?.pending)target.append(node('p','변경안 · 공개 발화 성공 후 확정','hint'));
    if(data.inner_notes?.current?.discarded)target.append(node('p','이번 변경안 폐기 · 기존 노트 유지','hint'));
  });
  function drawTurnPlan(target,value){
    if(value?.planning_mode==='direct'){target.append(node('p','별도 작업 계획 없이 반응을 판단했습니다.','hint'));return;}
    const plan=value?.plan;if(!plan?.steps?.length)return;
    const box=node('section','','turn-plan-view');box.dataset.revision=plan.revision;
    box.append(node('strong','로컬 작업 계획 · '+(value.discarded?'미완료 · 미저장':value.pending?'수행 중':'턴 기록')),node('p',plan.goal));
    const list=node('ol');
    for(const step of plan.steps){
      const row=node('li','','turn-plan-step');row.dataset.status=step.status;
      row.append(node('strong',({pending:'□ 대기',in_progress:'▶ 진행 중',completed:'✓ 완료',skipped:'― 생략'}[step.status] || step.status)+' · '+step.id+' · '+step.task),node('p','완료 기준: '+step.completion_criterion,'hint'));
      if(step.result)row.append(node('p','결과: '+step.result));
      if(step.sources?.length)row.append(node('p','근거: '+step.sources.join(' / '),'hint'));
      list.append(row);
    }
    box.append(list,node('p','갱신 이유: '+(plan.last_change_reason || ''),'hint'));target.append(box);
  }
  register('client_states',(target,data)=>{
    drawTurnPlan(target,data.turn_plan?.current);
    if(data.traits?.current?.background_routing){
      target.append(node('strong','이번 관련 특성 · 연결된 배경 원문 · 선정 이유'));
      const selected=data.traits.current.selected || [];
      target.append(node('pre',selected.length?JSON.stringify(selected,null,2):'관련 특성·배경 없음 ([])'));
      if(!data.traits.current.background_candidate_count)target.append(node('p','현재 프로필에는 성장 경험·상황 사례 후보가 없습니다.','hint'));
    }
    target.append(node('strong','이번 appraisal · 반응 방향'),node('pre',JSON.stringify({appraisal:data.appraisal?.current,response_plan:data.response_plan?.current},null,2)));
    for(const [key,label] of [['note_selection','발화 생성에 선택한 노트 항목'],['turn_goal','이번 대화 의도'],['trajectory_plan','이번 궤적 계획'],['trajectory_progress','궤적 진행 기록'],['evidence_decision','근거 확인 종료 판단']]){
      const value=data[key]?.current;
      if(value)target.append(node('strong',label),node('pre',JSON.stringify(key==='note_selection'?{selected_ids:value.selected_ids,scope:value.scope}:value,null,2)));
    }
    const lookups=data.memory_retrieval?.history || [];
    if(lookups.length){
      target.append(node('strong','추가 조회 · 요청 이유와 반환 출처'));
      for(const row of lookups){const box=node('details');box.open=true;box.append(node('summary','조회 '+row.data.index+' · '+row.data.arguments.source),node('pre',JSON.stringify(row.data,null,2)));target.append(box);}
    }
    if(data.state_notes?.current?.note_format!=='free_items_v1')target.append(node('strong','발화 후 상태 검토 대상'),node('pre',JSON.stringify(data.state_routing?.current?.selected || [],null,2)));
    target.append(node('strong',data.state_notes?.current?.note_format==='free_items_v1'?'내담자 노트 · 자유 글머리':data.state_notes?.current?.inner?'내담자 노트 · 현재 내면 / 심리상태':'현재 심리상태 노트'));
    renderers.get('notes')(target,{notes:data.state_notes,hideChanges:data.hideChanges});
    if(data.state_notes?.current?.discarded)target.append(node('p','상태 갱신 미완료 · 기존 노트 유지','hint'));
  });
  register('client_drafts',(target,data)=>{
    const drafts=(data.drafts?.history || []).filter(row=>row.action==='staged').map(row=>row.data);
    const reviews=(data.reviews?.history || []).filter(row=>row.action==='completed').map(row=>row.data);
    const selection=data.selection?.current;
    target.append(node('strong','이번 턴 후보 · 검토 · 최종 선택'));
    if(!drafts.length)target.append(node('p','아직 저장된 초안이 없습니다.','hint'));
    for(const draft of drafts){
      const box=node('details','','client-draft-record');box.open=true;
      const selected=selection?.draft_id===draft.draft_id && !selection.discarded;
      box.append(node('summary',draft.draft_id+(draft.revises?' · '+draft.revises+' 수정':'')+(selected?(selection.pending?' · 선택됨 / 확정 대기':' · 최종 확정'):'')),node('pre',draft.utterance));
      for(const review of reviews.filter(row=>row.draft_id===draft.draft_id))box.append(node('pre',JSON.stringify(review,null,2),'turn-review-record'));
      target.append(box);
    }
    if(selection?.reason)target.append(node('strong','최종 선택 이유'),node('p',selection.reason));
    if(selection?.discarded)target.append(node('p','이번 생성은 확정되지 않았습니다. 초안·검토는 실행 기록에만 남습니다.','hint'));
    renderers.get('client_inner')(target,data);
  });
  register('c4',(target,data,attempt)=>{
    const record=attempt?.extras?.c4?.e2e_counselor;
    if(!record){target.append(node('p','C4가 생성한 정보·작업 노트는 생성 완료 후 표시합니다.','hint'));return;}
    const details=node('details');details.append(node('summary','정보·과제·상담 작업 기록 원문'),node('pre',JSON.stringify(record.ledger_after_reduction,null,2)));target.append(details);
  });
  const mounts=new WeakMap();
  function mountAttempt(target,value,inspect){
    let ui=mounts.get(target);
    if(!ui){
      const details=node('details','','turn-process');details.open=true;
      const summary=node('summary','생성 과정'),list=node('div','','turn-process-list'),extra=node('details','','turn-version-record');
      const extraTitle=node('summary','버전 기록'),extraBody=node('div','','turn-version-content');extra.append(extraTitle,extraBody);details.append(summary,list,extra);target.append(details);
      ui={details,summary,list,extra,extraTitle,extraBody,entries:new Map(),versionKey:''};mounts.set(target,ui);
    }
    ui.inspect=inspect;
    const allTools=new Map(),items=[];
    value.calls.forEach((call,i)=>{
      items.push({key:'call:'+i,kind:'call',call,index:i,step:call.stepIndex});
      for(const draft of call.toolDrafts || [])if(draft.status==='drafting' && draft.name){
        const active=value.status==='running' && value.calls.findLast(row=>row.agent===call.agent)===call;
        items.push({key:'tool:'+JSON.stringify([call.agent,draft.draftKey]),kind:'tool',
          tool:{...draft,status:active?'drafting':'interrupted'},step:call.stepIndex+.25+draft.index*.001,contextStep:call.stepIndex,agent:call.agent});
      }
    });
    value.steps.forEach((state,i)=>{for(const tool of state.trace || []){
      const id=JSON.stringify([state.agentId,tool.draftKey || tool.id]);
      if(!allTools.has(id)){const item={key:'tool:'+id,kind:'tool',tool,step:i,agent:state.agentId};allTools.set(id,item);items.push(item);}
      else allTools.get(id).tool=tool;
      if(state.phase==='result' && state.messages.at(-1)?.tool_call_id===tool.id)allTools.get(id).contextStep=i;
    }});
    items.sort((a,b)=>a.step-b.step);
    ui.summary.textContent=`생성 과정 · ${value.status==='done'?'완료':value.status==='running'?'진행 중':'오류 / 중단'}`;
    for(const item of items){
      let entry=ui.entries.get(item.key);
      if(!entry){
        const box=node('section','',item.kind==='call'?'turn-model-call':'turn-tool-call');
        const heading=node('div','','turn-event-heading'),label=node('strong'),select=node('button','선택','turn-context-select');
        select.type='button';select.onclick=()=>ui.inspect(entry.step);
        heading.append(label,select);
        box.append(heading);entry={box,label,select};
        box.onclick=event=>{
          if(event.target.closest('button,summary,a,input,textarea,select') || window.getSelection()?.toString())return;
          ui.inspect(entry.step);
        };
        if(item.kind==='call'){
          const pre=node('pre','','turn-reasoning-text'),outputLabel=node('small','상담 진행 제안'),output=node('pre','','turn-agent-output');const prefillLabel=node('small','Reasoning prefill · Python에서 넣은 입력'),prefill=node('pre','','turn-agent-output');prefillLabel.hidden=prefill.hidden=true;box.append(prefillLabel,prefill,pre,outputLabel,output);Object.assign(entry,{pre,outputLabel,output,prefillLabel,prefill});
        }else{
          const draftText=node('pre','','turn-draft-text');draftText.hidden=true;
          const status=node('span','','turn-tool-status'),previewLabel=node('small','','turn-tool-preview-label'),diff=node('div','','turn-tool-changes p1-note-changes');heading.append(status);
          status.setAttribute('role','status');previewLabel.hidden=true;
          const details=node('details','','turn-tool-details'),argsPre=node('pre'),result=node('div'),resultPre=node('pre'),button=node('button','컨텍스트에서 보기','turn-inspect');
          button.type='button';button.onclick=()=>ui.inspect(entry.step);
          result.append(node('small','도구 반환 원문'),resultPre);
          details.append(node('summary','호출 상세'),button,node('small','호출 인자'),argsPre,result);
          box.append(draftText,previewLabel,diff,details);Object.assign(entry,{status,draftText,previewLabel,diff,argsPre,result,resultPre,diffKey:'',previewAt:0});
        }
        ui.entries.set(item.key,entry);ui.list.append(box);
      }
      entry.step=item.contextStep ?? Math.floor(item.step);
      entry.box.dataset.contextStep=entry.select.dataset.contextStep=entry.step;
      entry.box.dataset.regionKey=item.key;
      entry.select.setAttribute('aria-label',(item.kind==='call'?`모델 호출 ${item.index+1}`:item.tool.name+' 호출 및 결과')+' 선택');
      if(item.kind==='call'){
        entry.box.classList.toggle('turn-subagent-call',item.call.agent!=='main');
        entry.label.textContent=item.call.agent==='main'?'reasoning:':`reasoning · ${item.call.name || item.call.agent}:`;
        entry.label.title=item.call.phase || '';
        entry.prefillLabel.hidden=entry.prefill.hidden=!item.call.reasoningPrefill;entry.prefill.textContent=item.call.reasoningPrefill || '';
        const waiting=value.status==='running' && item.index===value.calls.length-1 && !items.some(other=>other.kind==='tool' && other.step>item.step && other.agent===item.call.agent);
        const reason=item.call.reasoning || (waiting?'생각 중…':'');entry.box.hidden=false;entry.pre.hidden=!reason;
        if(entry.pre.textContent!==reason){
          const top=entry.pre.scrollTop,follow=entry.pre.scrollHeight-entry.pre.clientHeight-top<24;
          entry.pre.textContent=reason;entry.pre.scrollTop=follow?entry.pre.scrollHeight:top;
        }
        entry.pre.tabIndex=0;entry.pre.setAttribute('aria-label',entry.label.textContent+' 원문');
        const guidance=(value.actor==='client' && ['Q1','Q1_s1','Q1_s2','Q1a','Q2','Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(value.clientVersion) || value.actor==='counselor' && ['P3a','P3a_s1'].includes(value.counselorVersion)) && item.call.agent!=='main'?item.call.output || '':'';
        entry.outputLabel.textContent=value.actor==='client'?(({draft_reviewer:'초안 검토 결과',state_router:'상태 검토 대상',state_updater:'상태 설명 변경안'})[value.steps[item.call.stepIndex]?.agentMeta.kind] || '특성 선정 결과'):'상담 진행 제안';
        entry.outputLabel.hidden=entry.output.hidden=!guidance;
        if(entry.output.textContent!==guidance)entry.output.textContent=guidance;
        if(guidance){entry.box.hidden=false;if(!reason)entry.label.textContent=(item.call.name || item.call.agent)+' 응답';}
      }else{
        const t=item.tool;let suffix='';
        const provisional=t.name==='update_notes' && !t.noteChanges?.length && ['drafting','running','interrupted','error'].includes(t.status);
        if(t.name==='update_notes'){try{const args=JSON.parse(t.arguments);suffix=({client:' · 내담자 노트',counselor:' · 상담사 생각 노트'})[args.target] || '';}catch{}}
        if(['read_counseling_guide','consult_counseling_guide'].includes(t.name)){try{const args=JSON.parse(t.arguments);suffix=({routing:' · 기법 선택',common:' · Common',mi:' · MI',cbt:' · CBT'})[args.guide] || '';}catch{}}
        if(provisional && !suffix){const target=PairedTrace.notePreview(t.arguments).target;suffix=({client:' · 내담자 노트',counselor:' · 상담사 생각 노트'})[target] || '';}
        entry.label.textContent=t.name+suffix+' 호출';
        const state={drafting:t.name==='update_notes'?'수정안 작성 중…':'호출 인자 작성 중…',running:t.name==='update_notes'?'적용 중…':'실행 중…',completed:t.name==='update_notes'?'✓ 저장 완료':'✓ 완료',error:t.name==='update_notes'?'저장 실패':'실행 오류',interrupted:'작성 중단 · 미적용',unknown:'중단 · 상태 미확인'}[t.status] || '호출됨';
        entry.status.textContent=state;entry.box.dataset.status=t.status;entry.argsPre.textContent=t.arguments || '';entry.result.hidden=t.result==null;entry.resultPre.textContent=t.result || '';
        if(t.name==='stage_client_update' && t.status==='completed'){
          if(value.clientVersion==='Q1a'){
            const id=t.rawResult?.structuredContent?.draft_id,chosen=value.extras.versions?.Q1a?.selection?.current;
            entry.status.textContent=value.status==='failed'?'초안 기록 · 미확정':id===chosen?.draft_id && !chosen?.pending?'✓ 최종 선택된 초안':value.status==='done'?'초안 기록 · 미선택':'초안 저장 · 확정 대기';
          }else entry.status.textContent=value.status==='done'?'✓ 발화 성공 · 기록 확정':value.status==='failed'?'변경안 폐기':'변경안 접수 · 확정 대기';
        }
        if(value.clientVersion==='Q1a' && t.name==='finalize_client_draft' && t.status==='completed')entry.status.textContent=value.status==='done'?'✓ 발화·내면 기록 확정':value.status==='failed'?'확정 미완료':'선택 완료 · 기록 확정 중';
        if(['Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(value.clientVersion) && ['commit_client_utterance','update_client_states','update_client_notes','update_trajectory_progress'].includes(t.name) && t.status==='completed')entry.status.textContent=value.status==='failed'?'턴 미완료 · 미저장':value.status==='done'?'✓ 턴 저장 완료':t.name==='commit_client_utterance'?'발화 고정 · 노트 기록 대기':'노트 변경안 접수 · 저장 중';
        const draft=['Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(value.clientVersion) && t.name==='commit_client_utterance'?PairedTrace.notePreview(t.arguments).utterance || '':value.clientVersion==='Q1a' && t.name==='stage_client_update'?PairedTrace.notePreview(t.arguments).utterance || '':'';
        entry.draftText.hidden=!draft;if(entry.draftText.textContent!==draft)entry.draftText.textContent=draft;
        const changes=t.noteChanges || [];
        entry.previewLabel.hidden=!provisional;
        entry.previewLabel.textContent=t.status==='error'?'저장 실패 · 아래는 적용되지 않은 수정안':t.status==='interrupted'?'작성 중단 · 아래는 적용되지 않은 수정안':'수정안 미리보기 · 아직 저장되지 않았습니다';
        entry.diff.dataset.preview=String(provisional);
        // Batch a growing full-note comparison so the preview does not flicker
        // with every token. Executed results always replace it immediately.
        const now=performance.now(),key=JSON.stringify([t.status,t.arguments,changes]);
        if(key!==entry.diffKey && (!provisional || t.status!=='drafting' || now-entry.previewAt>=140)){
          entry.diffKey=key;entry.previewAt=now;
          const top=entry.diff.scrollTop,follow=entry.diff.scrollHeight-entry.diff.clientHeight-top<24;
          entry.diff.replaceChildren();
          if(changes.length){
            for(const change of changes){
              if(Array.isArray(change.operations)){renderNoteOperations(entry.diff,change.operations);continue;}
              for(const line of (change.diff || []).slice(2)){
                if(line.startsWith('+') || line.startsWith('-'))entry.diff.append(node('div',line,line.startsWith('+')?'note-added':'note-deleted'));
              }
            }
            if(!entry.diff.children.length)entry.diff.append(node('small','내용 변경 없음'));
          }else if(provisional){
            const context=value.steps[item.contextStep ?? Math.floor(item.step)],version=value.actor==='client'?value.clientVersion:value.counselorVersion;
            const target=PairedTrace.notePreview(t.arguments).target;
            const records=context?.extras?.versions?.[version] || {};
            const before=(records[target+'_notes'] || records.notes)?.current?.content;
            for(const line of PairedTrace.notePreview(t.arguments,before).lines)
              entry.diff.append(node('div',(line.kind==='added'?'+':'-')+line.text,line.kind==='added'?'note-added':'note-deleted'));
          }
          entry.diff.scrollTop=follow?entry.diff.scrollHeight:top;
        }
        entry.diff.hidden=!entry.diff.children.length;
      }
    }
    const wanted=items.map(item=>ui.entries.get(item.key).box);
    wanted.forEach((box,i)=>{if(ui.list.children[i]!==box)ui.list.insertBefore(box,ui.list.children[i] || null);});
    const keep=new Set(wanted);for(const box of [...ui.list.children])if(!keep.has(box))box.remove();
    const selectedVersion=value.actor==='client'?value.clientVersion:value.counselorVersion;
    const spec=value.actor==='client'?versionCatalog?.versions.find(s=>s.id===selectedVersion):specs.find(s=>s.id===selectedVersion),data=value.extras.versions?.[selectedVersion] || {};
    ui.extra.hidden=(value.actor==='client' && !['Q1','Q1_s1','Q1_s2','Q1a','Q2','Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(selectedVersion)) || !spec || spec.view==='none';
    ui.extraTitle.textContent=['Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(selectedVersion)?'appraisal · 반응 방향 · 내담자 노트':['Q2'].includes(selectedVersion)?'appraisal · 반응 방향 · 심리상태 노트':selectedVersion==='Q1a'?'후보 · 검토 · 선택 · 내면 기록':['Q1','Q1_s1','Q1_s2'].includes(selectedVersion)?'관련 특성 · appraisal · 내면 노트':['notes','dual_notes'].includes(spec?.view)?'전체 노트 보기':selectedVersion+' · 저장 기록';
    const k=JSON.stringify([data,value.extras.c4]);
    if(k!==ui.versionKey){ui.versionKey=k;ui.extraBody.replaceChildren();const renderer=renderers.get(spec?.view);if(renderer)renderer(ui.extraBody,{...data,hideChanges:true},value,spec);else ui.extraBody.append(node('pre',JSON.stringify(data,null,2)));}
    return ui;
  }
  return {install,standalone,register,mountAttempt,setCatalog(value){specs=value;draw();},select(value){version=value;attempt=null;mode='ready';draw();},
    setCurrent(value,data){currentData[value]=data;draw();},reset(value){currentData={};version=value;attempt=null;mode='ready';draw();},
    live(value){attempt=value;version=value?.counselorVersion || version;mode='live';draw();},saved(value){attempt=value;version=value?.counselorVersion || version;mode='saved';draw();},refresh(value){if(attempt?.id===value.id){attempt=value;draw();}}};
})();
