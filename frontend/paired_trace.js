// Pure event reducer: each replay step owns its context and metadata snapshot.
(() => {
  const clone=value=>structuredClone(value);
  function create(actor,id,number,counselorVersion,clientVersion){
    return {actor,id,number,status:'running',calls:[],steps:[],agents:{},extras:{},utterance:'',counselorVersion,clientVersion};
  }
  function accept(attempt,event){
    const wrapped=event.type==='agent_event',item=wrapped?event.event:event;
    const agent=wrapped?event.agent_id:'main';
    const name=wrapped?event.agent_name:attempt.actor==='client'?'내담자 메인':attempt.counselorVersion==='basic'?'상담사 · 기본':'상담사 · '+attempt.counselorVersion;
    const meta={depth:event.depth || 0,parentId:event.parent_agent_id || null,parentCallId:event.parent_call_id || null,
      kind:event.agent_kind || null,role:event.role_instruction || null,roleSource:event.role_source || 'developer',
      version:attempt.actor==='client'?attempt.clientVersion:attempt.counselorVersion==='basic'?'basic_counselor':attempt.counselorVersion};
    let state=attempt.agents[agent];
    const snapshot=(phase,title)=>{
      state=state?clone(state):{messages:[],tools:[],trace:[],output:'',reasoning:'',payload:null,iteration:0,newFrom:Infinity,pending:null};
      Object.assign(state,{agentId:agent,agentName:name,agentMeta:meta,phase,title:name+' · '+title,
        description:'선택한 시점에 실제로 전달되거나 수신된 기록입니다.',terminal:false,problem:false,extras:clone(attempt.extras)});
      attempt.steps.push(state);attempt.agents[agent]=state;return state;
    };
    if(item.type==='state_summary')Object.assign(attempt.extras,{summary:item.summary,stateBefore:clone(item.state_before)});
    if(item.type==='state_record')attempt.extras.inner=clone(item.record);
    if(item.type==='counselor_record')attempt.extras.c4=clone(item.record);
    if(item.type==='client_record'){attempt.modelResponse=item.model_response ?? item.response;attempt.responseSource=item.response_source;}
    if(item.type==='version_event'){
      // Old Q2 journals remain untouched after the version was renamed Q1a.
      // Scope the alias to migrated sessions so a future Q2 is independent.
      const versionId=item.version==='Q2' && attempt.clientVersion==='Q1a'?'Q1a':item.version;
      const versions=attempt.extras.versions ||= {},version=versions[versionId] ||= {};
      const section=version[item.section] ||= {history:[]};
      section.current=clone(item.data);section.history.push(clone(item));
      // Note writes arrive between tool_start and tool_result. Associate the
      // actual saved diff with that call without altering an earlier snapshot.
      if(['Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b'].includes(versionId) && item.section==='state_notes' && item.action==='updated'){
        const tool=state?.trace.findLast(t=>['update_client_states','update_client_notes'].includes(t.name) && t.status==='completed');
        if(tool)tool.noteChanges=[clone(item.data)];
      }
      if(item.action==='updated' || item.action==='staged' && item.section==='inner_notes'){
        const tool=state?.trace.findLast(tool=>tool.status==='running' && ['update_notes','stage_client_update'].includes(tool.name));
        if(tool){
          const pending=attempt.pendingNoteChanges ||= {};
          (pending[JSON.stringify([agent,tool.id])] ||= []).push(clone(item.data));
        }
      }
    }
    if(item.type==='request'){
      snapshot('model',item.phase || '모델 호출 '+(item.iteration || 1));
      Object.assign(state,{messages:clone(item.payload.messages),tools:clone(item.payload.tools || []),payload:clone(item.payload),
        wireRequest:clone(item.wire_request || null),endpoint:item.endpoint || null,reasoningPrefill:item.reasoning_prefill || '',contextManifest:clone(item.context_manifest || null),output:'',reasoning:'',inspection:null,usage:null,toolDrafts:[],iteration:item.iteration || 1});
      attempt.calls.push({agent,name,phase:item.phase || name,payload:clone(item.payload),stepIndex:attempt.steps.length-1,reasoningPrefill:item.reasoning_prefill || '',reasoning:'',output:'',toolDrafts:[]});
    }else if(item.type==='chunk' && state){
      for(const choice of item.data.choices || [])if((choice.index ?? 0)===0){state.output+=choice.delta?.content || '';state.reasoning+=choice.delta?.reasoning_content || choice.delta?.reasoning || '';}
      const call=attempt.calls.findLast(row=>row.agent===agent);
      if(call){
        call.reasoning=state.reasoning;call.output=state.output;
        // Drafts are display-only: do not insert incomplete calls into raw context.
        call.toolDrafts ||= [];
        for(const choice of item.data.choices || [])if((choice.index ?? 0)===0){
          for(const fragment of choice.delta?.tool_calls || []){
            const index=fragment.index ?? 0;if(!Number.isInteger(index) || index<0 || index>=50)continue;
            let draft=call.toolDrafts.find(row=>row.index===index);
            if(!draft){draft={index,draftKey:attempt.calls.indexOf(call)+':'+index,id:'',name:'',arguments:'',status:'drafting'};call.toolDrafts.push(draft);}
            if(fragment.id)draft.id=fragment.id;
            draft.name+=fragment.function?.name || '';draft.arguments+=fragment.function?.arguments || '';
          }
        }
      }
      if(call)state.toolDrafts=clone(call.toolDrafts || []);
      if(item.data.usage)state.usage=clone(item.data.usage);
    }else if(item.type==='inspection' && state)state.inspection=clone(item);
    else if(item.type==='model_result' && state){state.output=item.response;if(item.reasoning!==undefined)state.reasoning=item.reasoning;const call=attempt.calls.findLast(row=>row.agent===agent);if(call){call.reasoning=state.reasoning;call.output=state.output;}}
    else if(item.type==='tool_start' && state){
      snapshot('tool','도구 호출 · '+item.name);
      const call={id:item.call_id,type:'function',function:{name:item.name,arguments:item.arguments}};
      const previous=state.messages.at(-1);
      if(item.assistant_message){
        if(!state.messages.some(message=>message.role==='assistant' && message.tool_calls?.some(tool=>tool.id===item.call_id)))state.messages.push(clone(item.assistant_message));
      }
      else if(previous?.role==='assistant' && previous.tool_calls)previous.tool_calls.push(call);
      else state.messages.push({role:'assistant',content:state.output || '',tool_calls:[call]});
      const currentCall=attempt.calls.findLast(row=>row.agent===agent);
      const draft=currentCall?.toolDrafts?.find(row=>row.id===item.call_id)
        || currentCall?.toolDrafts?.find(row=>row.status==='drafting' && row.name===item.name);
      if(draft)draft.status='running';
      state.trace.push({id:item.call_id,draftKey:draft?.draftKey,name:item.name,arguments:item.arguments,iteration:state.iteration,status:'running',result:null});
    }else if(item.type==='tool_result' && state){
      snapshot('result','도구 결과 · '+item.name);
      const tool=state.trace.find(row=>row.id===item.call_id);
      if(tool)Object.assign(tool,{status:item.is_error?'error':'completed',result:item.model_content,rawResult:clone(item.result ?? item.model_content)});
      const noteKey=JSON.stringify([agent,item.call_id]);
      if(tool && attempt.pendingNoteChanges?.[noteKey]){
        tool.noteChanges=clone(attempt.pendingNoteChanges[noteKey]);delete attempt.pendingNoteChanges[noteKey];
      }
      state.messages.push({role:'tool',tool_call_id:item.call_id,content:item.model_content});state.problem=!!item.is_error;
    }else if(['done','error','cancelled','disconnected'].includes(item.type)){
      snapshot(item.type==='done'?'done':'error',item.type==='done'?'발화 생성 완료':'중단 / 오류');
      state.terminal=true;state.problem=item.type!=='done';
      if(item.type==='done'){
        state.output=item.model_response ?? (!wrapped?attempt.modelResponse:null) ?? state.output ?? item.response;
        if(state.reasoningPrefill && item.history)state.messages=clone(item.history);
        else if(item.response_source!=='committed_utterance' && attempt.responseSource!=='committed_utterance')state.messages.push({role:'assistant',content:state.output || item.response || ''});
        state.description=(item.response_source==='committed_utterance' || attempt.responseSource==='committed_utterance')?'공개 발화 확정 및 상태 기록 도구가 완료된 시점입니다. 이후 추가 모델 생성은 없습니다.':(item.response_source==='finalized_draft' || !wrapped && attempt.responseSource==='finalized_draft')?'선택한 초안의 발화를 확정한 기록입니다. 확정 도구 이후의 추가 모델 생성은 없습니다.':'선택한 발화의 완료 시점 기록입니다. 이후 발화의 정보는 포함하지 않습니다.';
      }else state.description=item.error || '생성이 완료되지 않았습니다.';
      if(!wrapped){attempt.status=item.type==='done'?'done':'failed';attempt.utterance=item.response || '';attempt.error=item.error;attempt.usage=item.usage;}
    }
  }

  // Decode incomplete tool JSON for a provisional display only. Never evaluate
  // JSON as code, repair it into an executable request, or mark it as saved.
  function partialJSON(text){
    let pos=0;
    const skip=()=>{while(/\s/.test(text[pos] || '') && pos<text.length)pos++;};
    function read(depth=0){
      if(depth>32)throw Error('depth');skip();const start=text[pos];
      if(start===undefined)return null;
      if(start==='"'){
        pos++;let value='';
        while(pos<text.length){
          const char=text[pos++];if(char==='"')return {value,complete:true};
          if(char==='\\'){
            if(pos>=text.length)break;
            const escape=text[pos++],map={'"':'"','\\':'\\','/':'/','b':'\b','f':'\f','n':'\n','r':'\r','t':'\t'};
            if(escape==='u'){
              const hex=text.slice(pos,pos+4);if(hex.length<4)break;
              if(!/^[\da-f]{4}$/i.test(hex))throw Error('escape');
              value+=String.fromCharCode(parseInt(hex,16));pos+=4;
            }else if(Object.hasOwn(map,escape))value+=map[escape];else throw Error('escape');
          }else{if(char.charCodeAt(0)<32)throw Error('control');value+=char;}
        }
        // Do not render half of a Unicode surrogate pair while streaming.
        if(/[\uD800-\uDBFF]$/.test(value))value=value.slice(0,-1);
        return {value,complete:false};
      }
      if(start==='{' || start==='['){
        const object=start==='{',children=object?Object.create(null):[];pos++;skip();
        const close=object?'}':']';if(text[pos]===close){pos++;return {children,complete:true};}
        while(pos<text.length){
          let key;
          if(object){const name=read(depth+1);if(!name?.complete || typeof name.value!=='string')return {children,complete:false};key=name.value;skip();if(text[pos]!==':'){if(pos===text.length)break;throw Error('colon');}pos++;}
          const child=read(depth+1);if(!child)break;
          if(object)children[key]=child;else children.push(child);
          if(!child.complete)return {children,complete:false};skip();
          if(text[pos]===close){pos++;return {children,complete:true};}
          if(pos===text.length)break;if(text[pos++]!==',')throw Error('separator');skip();
        }
        return {children,complete:false};
      }
      const startPos=pos;while(pos<text.length && !/[\s,\]}]/.test(text[pos]))pos++;
      try{return {value:JSON.parse(text.slice(startPos,pos)),complete:true};}catch{return null;}
    }
    try{return read();}catch{return null;}
  }
  function notePreview(argumentsText,before){
    const parsed=partialJSON(argumentsText || ''),fields=parsed?.children;
    if(!fields || Array.isArray(fields))return {target:null,lines:[]};
    const lines=[],append=(kind,text)=>{if(text)for(const line of text.split('\n'))lines.push({kind,text:line});};
    if(Array.isArray(fields.edits?.children)){
      for(const edit of fields.edits.children){
        const values=edit.children || {},old=values.old_text,next=values.new_text;
        if(old?.complete && typeof old.value==='string')append('deleted',old.value);
        if(typeof next?.value==='string')append('added',next.value);
      }
    }else if(typeof fields.content?.value==='string'){
      const after=fields.content.value;
      if(typeof before!=='string')append('added',after);
      else{
        const old=before.split('\n'),next=after.split('\n');
        if(!fields.content.complete && !next.at(-1))next.pop();
        // An LCS keeps unchanged lines out of the preview. Bound the work for
        // unusually large files; the authoritative saved diff comes from Python.
        if(old.length*next.length>200000)append('added',after);
        else{
          const matrix=Array.from({length:old.length+1},()=>new Uint16Array(next.length+1));
          for(let i=old.length-1;i>=0;i--)for(let j=next.length-1;j>=0;j--)
            matrix[i][j]=old[i]===next[j]?matrix[i+1][j+1]+1:Math.max(matrix[i+1][j],matrix[i][j+1]);
          let i=0,j=0;
          while(i<old.length && j<next.length){
            if(!fields.content.complete && j===next.length-1 && !after.endsWith('\n')){
              if(!old[i].startsWith(next[j])){lines.push({kind:'deleted',text:old[i]},{kind:'added',text:next[j]});}
              i++;j++;break;
            }
            if(old[i]===next[j]){i++;j++;}
            else if(matrix[i+1][j]>=matrix[i][j+1])lines.push({kind:'deleted',text:old[i++]});
            else lines.push({kind:'added',text:next[j++]});
          }
          while(j<next.length)lines.push({kind:'added',text:next[j++]});
          // The model has not written the rest yet; it has not deleted it.
          if(fields.content.complete)while(i<old.length)lines.push({kind:'deleted',text:old[i++]});
        }
      }
    }
    return {target:fields.target?.complete?fields.target.value:null,lines,utterance:fields.utterance?.value || ''};
  }

  const api={create,accept,notePreview};
  if(typeof module!=='undefined' && module.exports)module.exports=api;
  else window.PairedTrace=api;
})();
