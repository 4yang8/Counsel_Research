/* Manual, immediate selection of recorded execution snapshots; no playback timers. */
'use strict';
window.Journey = (() => {
  const el = id => document.getElementById(id);
  const clone = value => JSON.parse(JSON.stringify(value));
  const format = value => JSON.stringify(value, null, 2);
  const turns = [];
  let currentAgent='main', currentAgentName='메인', currentMeta={depth:0};
  let selectedTurn = -1, selectedStep = -1, railPaint = '';
  let lastPaint = '', paintFrame = null, defaults = {tools:[]};
  let externalTurn=null, savedSelection=null, activationFollow=true;
  const displayedTurn=()=>externalTurn || turns[selectedTurn];
  const roleLabels = {user:'사용자 질문', assistant:'모델 답변', tool:'도구 실행 결과'};
  const descriptions = {
    submit:'백엔드에 질문을 보냈습니다. 실제 모델 요청이 확정되기를 기다립니다.',
    model:'이 단계의 입력으로 모델 응답을 생성합니다. 도구 선택은 호출 결과가 도착해야 알 수 있습니다.',
    tool:'모델이 생성한 함수 이름과 인자로 백엔드가 서브에이전트를 실행합니다.',
    result:'도구 결과가 tool 메시지로 누적 기록에 추가됐습니다. 다음 LLM 호출 때 이 기록을 전송합니다.',
    done:'최종 답변까지 완료됐습니다. 답변도 다음 질문의 대화 기록에 포함됩니다.',
    error:'이번 턴이 완료되지 않았습니다. 진행된 호출은 실행 기록에 남습니다.',
    stopped:'수신 또는 실행이 중단됐습니다. 진행된 호출은 실행 기록에 남습니다.'
  };
  function completedDescription(state){
    if(state.agentMeta?.kind==='summary')return '완료된 통합 내면 요약입니다. 이번 상담 턴의 메인 입력에 참고 정보로 포함됩니다.';
    if(state.agentId!=='main')return '조회 또는 위임 담당의 완료 출력입니다. 선택한 원문이나 결과를 부모의 도구 결과로 전달합니다.';
    return '이번 턴의 생성 원문과 도구 실행 기록입니다. 다음 상담 턴에는 생성된 실제 발화를 공개 히스토리에 합치고 현재 상태·통합 내면 요약으로 새 프롬프트를 구성합니다. 이 메시지 배열 전체를 다음 상담 턴에 보내지 않습니다.';
  }
  function active(){return turns.at(-1);}
  function snapshot(turn, phase, title){
    const previous = turn.agentStates[currentAgent];
    const state = previous ? clone(previous) : {messages:[],tools:currentAgent==='main'?(defaults.tools || []):[],trace:[],output:'',reasoning:'',payload:null,inspection:null,usage:null,iteration:0,newFrom:0,pending:null};
    state.agentMeta=clone(currentMeta);state.agentId=currentAgent;state.agentName=currentAgentName;state.phase=phase;state.title=`${currentAgentName} · ${title}`;state.description=descriptions[phase];state.terminal=false;state.problem=false;
    state.extras=currentAgent==='main'?clone(turn.extras || {}):{};
    turn.steps.push(state);turn.agentStates[currentAgent]=state;
    return state;
  }
  function begin(body){
    clearExternal();activationFollow=true;
    const turn={number:turns.length+1,question:body.messages.at(-1).content,steps:[],base:clone(body.messages.slice(0,-1)),lastInput:null,agentStates:{},agentInputs:{},extras:{},done:false};
    currentAgent='main';currentAgentName='메인';currentMeta={depth:0,version:body.experiment_version || 'basic'};turns.push(turn);const state=snapshot(turn,'submit','질문 전송');state.messages=clone(body.messages);state.newFrom=turn.base.length;
    if(currentMeta.version==='state_summary')state.description='이번 상담사 발화를 서버에 전달했습니다. 서버가 저장된 공개 대화·현재 상태·통합 내면 요약을 합쳐 새 모델 입력을 구성합니다. 아래 내용은 실제 모델 요청이 확정되기 전의 전달 데이터입니다.';
    selectedTurn=turns.length-1;selectedStep=0;
    queuePaint();
  }
  function accept(event){
    const turn=active();if(!turn)return;
    if(event.type==='state_summary')Object.assign(turn.extras,{summary:event.summary,stateBefore:clone(event.state_before)});
    if(event.type==='state_record')turn.extras.inner=clone(event.record);
    if(event.type==='agent_event')event={...event.event,experiment_version:event.experiment_version,agent_id:event.agent_id,agent_name:event.agent_name,parent_call_id:event.parent_call_id,parent_agent_id:event.parent_agent_id,depth:event.depth,role_instruction:event.role_instruction,role_source:event.role_source,agent_kind:event.agent_kind};
    currentMeta={depth:event.depth || 0,parentId:event.parent_agent_id || null,parentCallId:event.parent_call_id || null,role:event.role_instruction || null,roleSource:event.role_source || 'developer',kind:event.agent_kind || null,version:event.experiment_version || null};
    currentAgent=event.agent_id || 'main';currentAgentName=event.agent_name || '메인';
    let state=turn.agentStates[currentAgent];
    if(!state && event.type!=='request')return;

    if(event.type==='request'){
      state=snapshot(turn,'model',`모델 호출 ${event.iteration || 1}`);
      const dialogue=event.payload.messages.filter(m=>m.role!=='system');
      const before=turn.agentInputs[currentAgent] || (currentAgent==='main'?turn.base:[]);
      let common=0;while(common<Math.min(before.length,dialogue.length) && format(before[common])===format(dialogue[common]))common++;
      state.messages=clone(event.payload.messages);state.newFrom=common;
      state.payload=clone(event.payload);state.wireRequest=clone(event.wire_request || null);state.endpoint=event.endpoint || null;state.reasoningPrefill=event.reasoning_prefill || '';state.contextManifest=clone(event.context_manifest || null);state.tools=clone(event.payload.tools || []);state.iteration=event.iteration || 1;
      state.pending=null;state.inspection=null;state.output='';state.reasoning='';state.usage=null;state.toolDrafts=[];
      turn.agentInputs[currentAgent]=clone(dialogue);
    }else if(event.type==='inspection'){
      state.inspection=clone(event);
    }else if(event.type==='chunk'){
      const choice=event.data.choices?.find(c=>(c.index ?? 0)===0);
      state.output+=choice?.delta?.content || '';
      state.reasoning+=choice?.delta?.reasoning_content || choice?.delta?.reasoning || '';
      window.ActivationContext.mergeDrafts(state.toolDrafts ||= [],choice?.delta?.tool_calls);
      if(event.data.usage)state.usage=clone(event.data.usage);
    }else if(event.type==='tool_start'){
      state=snapshot(turn,'tool',`도구 실행 · ${event.name}`);
      const call={id:event.call_id,type:'function',function:{name:event.name,arguments:event.arguments}};
      let message=state.messages.at(-1);
      state.newFrom=state.messages.filter(m=>m.role!=='system').length;
      if(event.assistant_message){
        if(!state.messages.some(message=>message.role==='assistant' && message.tool_calls?.some(tool=>tool.id===event.call_id)))state.messages.push(clone(event.assistant_message));
      }
      else if(message?.role==='assistant' && message.tool_calls && state.trace.some(t=>t.iteration===event.iteration))message.tool_calls.push(call);
      else state.messages.push({role:'assistant',content:state.output || '',tool_calls:[call]});
      state.trace.push({id:event.call_id,name:event.name,arguments:event.arguments,iteration:event.iteration,status:'running',result:null});
    }else if(event.type==='tool_result'){
      state=snapshot(turn,'result',`결과 도착 · ${event.name}`);
      const tool=state.trace.find(t=>t.id===event.call_id);
      if(tool){tool.status=event.is_error?'error':'completed';tool.result=event.model_content;tool.rawResult=clone(event.result ?? event.model_content);tool.truncated=event.truncated;}
      state.newFrom=state.messages.filter(m=>m.role!=='system').length;
      state.messages.push({role:'tool',tool_call_id:event.call_id,content:event.model_content});state.pending=null;state.problem=Boolean(event.is_error);if(state.problem)state.title+=' · 오류';
    }else if(['done','error','cancelled','disconnected','client_error'].includes(event.type)){
      if(turn.done)return;
      const phase=event.type==='done'?'done':['cancelled','disconnected'].includes(event.type)?'stopped':'error';
      state=snapshot(turn,phase,phase==='done'?'답변 완료':phase==='stopped'?'중단됨':'오류 발생');
      state.terminal=true;state.problem=phase!=='done';if(currentAgent==='main')turn.done=true;
      if(phase==='done'){
        const assistant={role:'assistant',content:event.model_response ?? event.response ?? state.output};
        state.newFrom=state.messages.filter(m=>m.role!=='system').length;
        if(state.reasoningPrefill && event.history)state.messages=clone(event.history);else state.messages.push(assistant);state.output=assistant.content;state.pending=null;
        state.description+=' 아래 메시지는 다음 요청에 보낼 기록이며, 완료 후 별도 모델 호출을 실행한 것은 아닙니다.';
        if(event.experiment_version==='state_summary')state.description=completedDescription(state);
        state.usage=event.usage || state.usage;state.totalUsage=true;
      }else{
        state.description=event.error || descriptions[phase];
        for(const tool of state.trace)if(tool.status==='running')tool.status='unknown';
      }
    }
    if(event.type==='request' && currentAgent==='main' && turns[selectedTurn]===turn && selectedStep===0 && turn.steps[0].phase==='submit')selectedStep=1;
    if(activationFollow && document.querySelector('.lab-shell')?.dataset.view==='activation' && turns[selectedTurn]===turn)selectedStep=turn.steps.length-1;
    queuePaint();
  }
  function queuePaint(){if(paintFrame===null)paintFrame=requestAnimationFrame(()=>{paintFrame=null;render();});}
  function node(tag,className,text){const n=document.createElement(tag);if(className)n.className=className;if(text!==undefined)n.textContent=text;return n;}
  function showMessages(state){
    const messages=state.messages.filter(m=>m.role!=='system');
    const previous=[...el('learning-messages').children].map(card=>{
      const content=card.querySelector(':scope > .message-content');
      return content?{text:content.textContent,top:content.scrollTop,left:content.scrollLeft,focused:document.activeElement===content}:null;
    });
    el('learning-messages').replaceChildren();
    messages.forEach((message,index)=>{
      const isNew=index>=state.newFrom;
      const card=node('article',`message-card ${message.role}${isNew?' newly-added':''}`);
      const label=message.tool_calls?'모델 → 도구 호출':roleLabels[message.role] || message.role;
      const heading=node('div','message-heading');heading.append(node('strong','',label));
      if(isNew)heading.append(node('span','new-label','이번 단계에 추가'));
      card.append(heading);
      if(message.tool_calls){
        if(message.content)card.append(node('p','',message.content));
        for(const call of message.tool_calls){card.append(node('b','',call.function.name));card.append(node('pre','',call.function.arguments));}
      }else if(message.role==='tool'){
        let text=message.content;
        try{const parsed=JSON.parse(text);text=parsed.content?.filter(c=>c.type==='text').map(c=>c.text).join('\n') || parsed.text || text;}catch{}
        card.append(node('pre','',text));
        const raw=node('details');raw.append(node('summary','','모델에 전달한 결과 원본'),node('pre','',message.content));card.append(raw);
      }else{
        const content=node('p','message-content',message.content || '(입력 대기)');
        content.tabIndex=0;content.setAttribute('role','region');content.setAttribute('aria-label',label+' 내용');card.append(content);
      }
      el('learning-messages').append(card);
      const content=card.querySelector(':scope > .message-content'),saved=previous[index];
      if(content && saved?.text===content.textContent){content.scrollTop=saved.top;content.scrollLeft=saved.left;if(saved.focused)content.focus({preventScroll:true});}
    });
    el('learning-count').textContent=`${messages.length}개`;
    el('context-count').textContent=`메시지 ${state.messages.length}개`;
    el('context-explanation').textContent=state.phase==='done'?(state.agentMeta?.version==='state_summary'?completedDescription(state):'최종 답변이 추가된 다음 질문용 기록입니다.'):state.payload?'초록색은 이 단계에서 새로 추가되거나 모델 입력에 새로 포함된 메시지입니다.':'아직 전송 요청이 확정되지 않은 입력입니다.';
    const systems=state.messages.filter(m=>m.role==='system');
    el('fixed-summary').textContent=`시스템 지침 ${systems.length}개 · 도구 설명 ${state.tools.length}개`;
    el('fixed-context').textContent=format({system:systems.map(m=>m.content),tools:state.tools});
    el('pending-result').replaceChildren();el('pending-result').hidden=!state.pending;
    if(state.pending){el('pending-result').append(node('strong','','도착한 결과 · 다음 모델 입력에 추가 예정'),node('p','',state.pending.name),node('pre','',state.pending.content));}
  }
  function showTrace(state){
    el('tool-trace').replaceChildren();
    if(!state.trace.length)el('tool-trace').append(node('p','hint','이 단계까지 실행한 도구가 없습니다.'));
    for(const tool of state.trace){
      const box=node('details',`tool-step ${tool.status}`);box.open=true;
      const label={running:'실행 중',completed:'✓ 완료',error:'도구 오류',unknown:'결과 확인 전 중단'}[tool.status];
      box.append(node('summary','',`${tool.name} · ${label}`),node('small','','실행 인자'),node('pre','',tool.arguments));
      if(tool.result!==null)box.append(node('small','',tool.truncated?'실행 결과 · 길이 제한으로 일부 전달':'모델에 전달한 실행 결과'),node('pre','',tool.result));
      el('tool-trace').append(box);
    }
  }
  // A single JSON document: spans add only role color, never rewrite values.
  function showRaw(state){
    const code=el('raw-json'), notes=el('raw-annotations');
    const scroll=code.closest('.raw-scroll');const top=scroll.scrollTop;
    code.replaceChildren();notes.replaceChildren();
    const add=(text,role,heading,description)=>{
      const span=node('span',role ? `raw-role ${role}` : '',text);code.append(span);
      if(heading){
        const note=node('div',`raw-note ${role}`);
        note.append(node('strong','',heading),node('p','',description));
        notes.append(note);note._source=span;
      }
    };
    const indent=value=>format(value).split('\n').map(line=>'    '+line).join('\n');
    const calls=new Map();
    for(const m of state.messages)for(const c of m.tool_calls || [])calls.set(c.id,c);
    add('{\n  "messages": [\n');
    state.messages.forEach((message,index)=>{
      let title=`messages[${index}] · ${message.role}`, description='';
      if(message.role==='system')description='매 모델 호출에 포함되는 시스템 지침입니다.';
      else if(message.role==='user')description=state.agentMeta?.version==='basic_counselor'?'기본 상담사에게 전달한 공개 대화입니다. 단일 시스템 지침으로 다음 상담사 발화를 생성합니다. 내담자의 비공개 프로필·상태·요약은 포함하지 않습니다.':state.agentMeta?.version==='C4'?'C4의 선택한 단계에 전달된 공개 대화와 상담사 작업 기록입니다. 내담자의 비공개 프로필·상태·요약은 포함하지 않습니다.':state.agentMeta?.kind==='counseling_guide'?'메인 상담사가 작성한 상황·질문과 네 상세 가이드 전체를 받는 별도 입력입니다. 메인의 전체 대화·노트는 자동 공유되지 않습니다.':state.agentMeta?.kind==='summary'?'누적 내면 기록에서 상담사 발화 → 받아들인 의미 → 상태 설정 이유와 반응의 흐름을 통합하는 요약 담당 입력입니다. 전체 공개 대화는 넣지 않습니다. Python이 실행합니다.':state.agentId && state.agentId!=='main'?'조회 질문·자료 또는 위임 작업 지시가 이 에이전트의 user 메시지로 들어갑니다.':state.agentMeta?.version==='state_summary'?(state.phase==='submit'?'서버에 전달할 이번 상담사 발화입니다. 아직 실제 모델 입력이 아닙니다.':'매 상담 턴 새로 구성한 입력입니다. 과제 지침·갱신된 공개 대화·프로필·현재 상태·통합 내면 요약·이번 상담사 발화를 포함합니다.'):'사용자가 메인 모델에게 보낸 메시지입니다.';
      else if(message.tool_calls){
        description=message.tool_calls.map(call=>{
          let args;try{args=JSON.parse(call.function.arguments);}catch{}
          const target=args?.path || args?.paths?.join(', ') || args?.pattern;
          return `${call.function.name} 도구를 호출하도록 모델이 생성한 요청입니다.${target ? ` 대상: ${target}.` : ''}`;
        }).join(' ')+ ' arguments는 JSON 문자열이며, id로 결과와 연결됩니다. 이 기록 자체는 실행 결과가 아닙니다.';
      }else if(message.role==='tool'){
        const call=calls.get(message.tool_call_id),name=call?.function.name || '도구';
        const actions={retrieve_inner_state:'상담 장면과 반응 이유를 질문으로 찾아 저장된 내면 원문 한 개를 반환한',delegate_task:'서브에이전트에게 위임해 최종 보고를 받은',retrieve_childhood:'성장 경험 담당이 관련 원문 한 개를 선택해 반환한',retrieve_situation:'상황 기록 담당이 사건 한 개를 선택해 반환한',read_text_file:'파일 내용을 읽어 반환한',read_multiple_files:'여러 파일의 내용을 읽어 반환한',list_directory:'폴더의 파일·하위 폴더 목록을 조회한',search_files:'파일을 검색한',get_file_info:'파일 정보를 조회한',write_file:'파일 쓰기를 실행한',edit_file:'파일 편집을 실행한',create_directory:'폴더 생성을 실행한',list_allowed_directories:'접근 가능한 폴더를 조회한'};
        const trace=state.trace?.find(t=>t.id===message.tool_call_id);
        description=`${name}: ${actions[name] || '도구를 실행한'} 결과입니다. tool_call_id로 위 호출과 연결됩니다. content 문자열이 모델에 전달됩니다.`;
        if(trace?.status==='error')description+=' 도구가 오류를 반환했습니다.';
        if(trace?.truncated)description+=' 길이 제한이 적용된 결과입니다.';
        description+=state.payload?.messages.some(m=>m.role==='tool' && m.tool_call_id===message.tool_call_id)?' 이 결과는 표시 중인 모델 요청에 포함됐습니다.':' 기록에 추가됐으며 다음 모델 호출에 포함될 데이터입니다.';
      }else if(message.role==='assistant')description=state.agentMeta?.version==='state_summary'?completedDescription(state):'모델이 생성한 텍스트 답변입니다. 이후 대화의 입력 기록에 포함됩니다.';
      if(message.role==='user' && state.agentMeta?.kind==='trait_router')description='매 턴 Python이 자동 실행하는 특성 라우터의 입력입니다. 기본 정보·공개 대화·이번 상담사 발화·최신 내면 노트·전체 특성을 읽습니다. 선정한 특성의 원문과 관련성 근거만 메인에게 전달합니다.';
      if(message.role==='user' && state.agentMeta?.version==='Q1' && state.agentMeta?.kind!=='trait_router')description='Q1 내담자의 이번 입력입니다. 기본 정보·선정된 특성·공개 대화·최신 내면 노트만 제공하며 전체 특성과 이전 appraisal 로그·이전 턴 reasoning은 포함하지 않습니다.';
      if(message.role==='tool' && calls.get(message.tool_call_id)?.function.name==='stage_client_update')description='비공개 appraisal와 내면 노트 변경안을 임시 접수한 결과입니다. 호출 성공만으로 영구 저장되지는 않으며, 공개 발화 정상 완료 후 확정하고 오류·중단 시 폐기합니다.';
      if(message.role==='tool' && state.tools.some(t=>t.function?.name==='commit_client_utterance')){
        const name=calls.get(message.tool_call_id)?.function.name;
        const descriptions={stage_client_update:'이번 내면 판단과 반응 방향을 접수했습니다. 상태 노트 변경은 아직 하지 않았습니다.',commit_client_utterance:'공개할 발화 내용을 고정했습니다. 뒤의 상태 기록까지 성공해야 대화와 노트에 저장됩니다.',select_client_states:'메인이 선택한 상태 검토 목록입니다. 선택됐어도 반드시 변경해야 하는 것은 아닙니다.',update_client_states:'메인의 상태 변경안을 검증·접수한 결과입니다. 성공하면 별도 모델 호출 없이 턴 저장을 진행합니다. 실제 저장 결과는 완료 기록과 노트에서 확인합니다.'};
        if(descriptions[name])description=descriptions[name]+(state.trace?.find(t=>t.id===message.tool_call_id)?.status==='error'?' 이 호출은 오류로 거부되었습니다.':'');
      }
      add(indent(message)+(index<state.messages.length-1?',':'')+'\n',message.role,title,description);
    });
    add('  ],\n  "tools": [');
    if(state.tools.length)add('\n');
    state.tools.forEach((tool,index)=>add(indent(tool)+(index<state.tools.length-1?',':'')+'\n','definitions',`tools[${index}] · ${tool.function?.name || '도구 정의'}`,'모델에게 제공하는 도구 설명과 인자 형식입니다. description과 parameters도 모델 입력에 포함됩니다. 이 정의가 있다는 사실은 실행했다는 의미가 아닙니다.'));
    add((state.tools.length?'  ':'')+']\n}');
    el('raw-state').textContent=state.phase==='model'?`모델 호출 ${state.iteration}에 실제 전송한 messages와 tools입니다. 생성 옵션 등 나머지 요청 필드는 시각화 탭의 요청 JSON에서 확인할 수 있습니다.`:state.phase==='submit' || !state.phase?'서버에 전달한 준비 데이터입니다. 실제 모델 입력은 모델 호출 단계에서 확인하세요.':state.agentMeta?.version==='state_summary' && state.phase==='done'?completedDescription(state):'선택한 단계까지 누적된 기록입니다. 이번 턴의 다음 모델 호출 때 도구 호출과 결과를 함께 입력합니다.';
    if(state.phase==='done' && state.tools.some(t=>t.function?.name==='commit_client_utterance'))el('raw-state').textContent='발화 확정과 상태 기록까지 완료된 메인 컨텍스트입니다. 마지막 도구 결과 뒤에는 추가 모델 호출이 없습니다.';
    alignRawNotes();scroll.scrollTop=top;
  }
  function alignRawNotes(){
    if(el('raw-context').hidden)return;
    const base=el('raw-json').getBoundingClientRect().top;
    let end=0;
    for(const note of el('raw-annotations').children){
      const sourceTop=note._source.getBoundingClientRect().top-base;
      const top=Math.max(sourceTop,end);
      note.style.top=`${top}px`;end=top+note.offsetHeight+12;
    }
    el('raw-annotations').style.minHeight=`${end}px`;
  }
  function setView(view){
    if(typeof view==='boolean')view=view?'raw':'visual';
    window.showLabResult?.(view);
    for(const name of ['visual','raw','activation','workflow']){
      const selected=name===view;
      el(`${name}-context`).hidden=!selected;
      el(`${name}-tab`).setAttribute('aria-selected',String(selected));
      el(`${name}-tab`).tabIndex=selected?0:-1;
    }
    render();alignRawNotes();
  }
  function render(){
    const turn=displayedTurn(),timeline=turn?.steps[selectedStep];if(!timeline)return;
    const picker=el('agent-picker'), wanted=picker.value;
    const available=new Map(turn.steps.slice(0,selectedStep+1).map(s=>[s.agentId,`${'　'.repeat(s.agentMeta?.depth || 0)}${s.agentMeta?.depth ? '↳ ' : ''}${s.agentName}`]));
    picker.replaceChildren(node('option','','선택한 스텝의 에이전트'));picker.firstChild.value='auto';
    for(const [id,name] of available){const option=node('option','',name);option.value=id;picker.append(option);}
    picker.value=available.has(wanted)?wanted:'auto';
    const state=picker.value==='auto'?timeline:turn.steps.slice(0,selectedStep+1).findLast(s=>s.agentId===picker.value);
    el('agent-context-label').textContent=`표시 중: ${state.agentName} · 깊이 ${state.agentMeta?.depth || 0} · 부모 ${state.agentMeta?.parentId || '없음'} · 현재 스텝까지의 기록`;
    el('agent-origin').textContent=format({agent_id:state.agentId,parent_agent_id:state.agentMeta?.parentId,parent_call_id:state.agentMeta?.parentCallId,depth:state.agentMeta?.depth || 0,role_source:state.agentMeta?.roleSource==='model'?'부모 모델이 생성':'개발자가 정의',role_instruction:state.agentMeta?.role || state.messages.find(m=>m.role==='system')?.content});

    window.Workflow.render(turn.steps,selectedStep,index=>{el('agent-picker').value='auto';select(selectedTurn,index);});
    const railKey=`${selectedTurn}:${selectedStep}:${turn.steps.length}`;
    if(railPaint!==railKey){
      railPaint=railKey;el('stage-rail').replaceChildren();
      turn.steps.forEach((step,index)=>{
        const button=node('button',`stage ${index===selectedStep?'selected':index<selectedStep?'passed':'future'}${step.problem?' problem':''}`);
        button.dataset.depth=String(step.agentMeta?.depth || 0);
        button.append(node('span','stage-number',step.problem?'!':index<selectedStep?'✓':String(index+1)),node('span','',step.title));
        button.setAttribute('aria-current',index===selectedStep?'step':'false');button.onclick=()=>select(selectedTurn,index);
        el('stage-rail').append(button);
      });
      // Reveal the selected tab inside the sidebar only.
      // scrollIntoView also scrolls the page and pulls readers away from panels.
      const rail=el('stage-rail'), selected=rail.querySelector('.selected');
      if(selected){
        const bounds=rail.getBoundingClientRect(), item=selected.getBoundingClientRect();
        if(item.top<bounds.top)rail.scrollTop-=bounds.top-item.top;
        else if(item.bottom>bounds.bottom)rail.scrollTop+=item.bottom-bounds.bottom;
      }
    }
    const key=`${selectedTurn}:${selectedStep}:${state.agentId}`;
    if(lastPaint!==key){lastPaint=key;showMessages(state);showTrace(state);showRaw(state);}
    showExecutionData(state);
    window.ActivationContext.render(state,turn.steps,turn.id || 'standalone-'+selectedTurn);
    el('live').textContent=state.output || (state.phase==='tool'?'도구 실행 중입니다.':state.phase==='result'?'도구 결과를 받아 다음 모델 호출을 준비합니다.':'이 단계의 텍스트 출력 없음');
    el('reasoning').textContent=state.reasoning || '이 단계에서 반환된 reasoning 없음';
    if(state.usage && Object.keys(state.usage).length)el('metrics').textContent=`${state.totalUsage?'이번 턴 전체':'이 모델 호출'} · 입력 ${state.usage.prompt_tokens ?? '—'} · 출력 ${state.usage.completion_tokens ?? '—'} · 합계 ${state.usage.total_tokens ?? '—'} tokens`;
    else el('metrics').textContent='이 단계의 토큰 사용량 미제공';
    el('request').textContent=state.payload?format(state.wireRequest?{endpoint:state.endpoint,body:state.wireRequest}:state.payload):'실제 모델 요청이 아직 도착하지 않았습니다.';
    el('request-label').textContent=`선택한 단계의 실제 요청 · 대화 ${turn.number} / 모델 호출 ${state.iteration || '대기'}`;
    el('prompt').textContent=state.inspection?.prompt || state.inspection?.error || '이 단계까지 프롬프트 조회 결과 없음';
    el('tokens').textContent=state.inspection?format(state.inspection):'—';
  }
  function showExecutionData(state){
    const data={...(state.extras || {}),...(state.problem?{error:state.description}:{})},hasData=Object.keys(data).length>0;
    el('execution-data').hidden=!hasData;el('execution-data-json').textContent=hasData?format(data):'';
  }
  function select(turn,index){
    activationFollow=false;
    if(!externalTurn)selectedTurn=turn;
    const target=displayedTurn();if(!target?.steps.length)return;
    selectedStep=Math.max(0,Math.min(index,target.steps.length-1));lastPaint='';render();externalTurn?.onSelect?.(selectedStep);
  }
  function selectTurn(number){
    clearExternal();const turn=turns[number-1];if(turn)select(number-1,turn.steps.length-1);
  }
  function reset(){
    externalTurn=null;savedSelection=null;activationFollow=true;window.Workflow.reset();window.ActivationContext.reset();turns.length=0;selectedTurn=-1;selectedStep=-1;lastPaint='';railPaint='';
    if(paintFrame!==null)cancelAnimationFrame(paintFrame);paintFrame=null;
    for(const id of ['stage-rail','learning-messages','tool-trace','pending-result','raw-json','raw-annotations'])el(id).replaceChildren();
    el('agent-origin').textContent='메인';el('agent-picker').replaceChildren(node('option','','선택한 스텝의 에이전트'));el('agent-picker').firstChild.value='auto';el('agent-context-label').textContent='발화를 선택하면 해당 실행의 에이전트를 표시합니다.';el('raw-state').textContent='발화를 선택하면 표시됩니다.';el('pending-result').hidden=true;showExecutionData({});
  }
  function showPreview(payload){if(externalTurn || turns.length)return;const state={messages:payload.messages,tools:payload.tools || [],newFrom:Infinity,pending:null,payload};showMessages(state);showRaw(state);showExecutionData(state);window.ActivationContext.render(state,[],'preview');}
  function inspectExternal(turn,index){
    if(!externalTurn)savedSelection={selectedTurn,selectedStep};
    const changed=externalTurn?.id!==turn.id || selectedStep!==index;
    externalTurn={...turn,external:true};selectedStep=Math.max(0,Math.min(index,turn.steps.length-1));
    lastPaint='';railPaint='';if(changed)el('agent-picker').value='auto';render();
    if(!turn.steps.length){
      const state={messages:[],tools:[],trace:[],output:'',newFrom:Infinity,pending:null,phase:'submit'};
      showMessages(state);showTrace(state);showRaw(state);showExecutionData(state);window.Workflow.reset();window.ActivationContext.reset(turn.question+' · 모델 호출 기록 없음');el('stage-rail').replaceChildren();
      el('agent-picker').replaceChildren(node('option','','선택한 스텝의 에이전트'));el('agent-picker').firstChild.value='auto';
      el('agent-context-label').textContent=turn.question;el('agent-origin').textContent='모델 호출 기록 없음';
      el('request').textContent=turn.question+' · 실제 모델 요청이 없습니다.';el('raw-state').textContent=turn.question+' · 실제 모델 요청이 없습니다.';
      for(const id of ['live','reasoning','prompt','tokens'])el(id).textContent='—';el('metrics').textContent='모델 호출 기록 없음';
    }
  }
  function clearExternal(){
    if(!externalTurn)return;
    externalTurn=null;if(savedSelection){selectedTurn=savedSelection.selectedTurn;selectedStep=savedSelection.selectedStep;}savedSelection=null;
    lastPaint='';railPaint='';render();
  }
  function init(){
    el('agent-picker').onchange=()=>{lastPaint='';render();};
    const views=['visual','raw','activation','workflow'];
    views.forEach((view,index)=>{
      el(`${view}-tab`).onclick=()=>setView(view);
      el(`${view}-tab`).onkeydown=event=>{
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
        event.preventDefault();
        const next=event.key==='Home'?0:event.key==='End'?views.length-1:(index+(event.key==='ArrowRight'?1:views.length-1))%views.length;
        setView(views[next]);el(`${views[next]}-tab`).focus({preventScroll:true});
      };
    });
    el('workflow-locate').onclick=()=>{const n=el('workflow-canvas').querySelector('.selected');if(n){const sc=el('workflow-scroll');sc.scrollTop=Math.max(0,n.offsetTop-sc.clientHeight/3);sc.scrollLeft=Math.max(0,n.offsetLeft-30);}};
    el('workflow-raw').onclick=()=>{el('agent-picker').value='auto';lastPaint='';render();setView('raw');};
    new ResizeObserver(alignRawNotes).observe(el('raw-json'));
    document.fonts.ready.then(alignRawNotes);


  }
  document.addEventListener('DOMContentLoaded',init);
  return {begin,accept,reset,showPreview,inspectExternal,clearExternal,selectTurn,setTools:info=>{defaults=info;},refresh:queuePaint,
    get hasExternal(){return !!externalTurn;},
    get hasTurns(){return turns.length>0;},selectCall:(turn,iteration)=>{const t=turns[turn-1];if(t){const i=t.steps.findIndex(s=>s.phase==='model' && s.iteration===iteration);if(i>=0)select(turn-1,i);}}};
})();
