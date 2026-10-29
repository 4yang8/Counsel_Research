/* Actual request membership, not inferred attention or accumulated post-call history. */
(() => {
  const json=value=>JSON.stringify(value,null,2);
  const text=value=>typeof value==='string'?value:value==null?'':json(value);
  const size=value=>Array.from(text(value)).length;
  const labels={system:'System prompt',developer:'Developer 지침',user:'User prompt',assistant:'Assistant 기록',tool:'Tool 결과',tools:'사용 가능한 도구'};
  const safeRole=role=>Object.hasOwn(labels,role)?role:'other';
  function blocks(payload){
    const messages=payload?.messages || [],names=new Map();
    for(const message of messages)for(const call of message.tool_calls || [])names.set(call.id,call.function?.name || 'tool');
    const rows=messages.map((message,index)=>{
      const names=(message.tool_calls || []).map(call=>call.function?.name).filter(Boolean);
      const parts=[];
      if(message.content)parts.push(text(message.content));
      if(message.reasoning || message.reasoning_content)parts.push('Reasoning: '+(message.reasoning || message.reasoning_content));
      if(names.length)parts.push('도구 호출: '+names.join(', '));
      return {id:'message-'+index,role:safeRole(message.role),label:labels[message.role] || message.role || '메시지',
        index:index+1,value:message,chars:size(message),summary:parts.join('\n'),
        tags:[...(message.reasoning || message.reasoning_content?['reasoning 포함']:[]),...names],
        sections:typeof message.content==='string'?[...message.content.matchAll(/^#{1,3}\s+(.+)$/gm)].map(m=>m[1]).slice(0,8):[]};
    });
    for(const row of rows)if(row.role==='tool')row.tags=[names.get(row.value.tool_call_id) || row.value.name || '도구 결과'];
    if(payload?.tools?.length)rows.push({id:'tools',role:'tools',label:'도구 정의',value:payload.tools,
      chars:size(payload.tools),summary:payload.tools.map(tool=>tool.function?.name || tool.name || tool.type).join(', '),tags:[payload.tools.length+'개'],sections:[]});
    return rows;
  }
  function allBlocks(state,steps){
    const groups=new Map(),selectedAgent=state.agentId || 'main';
    const add=(snapshot,payload)=>{
      const agent=snapshot.agentId || 'main';
      if(!groups.has(agent))groups.set(agent,new Map());
      const group=groups.get(agent);
      for(const row of blocks(payload)){
        const slot=row.role==='tools'?'tools':row.index-1;
        const identity=JSON.stringify([slot,row.value]);
        if(group.has(identity))continue;
        const actual=row.role==='tools'?state.payload.tools:state.payload.messages[slot];
        group.set(identity,{...row,id:agent+':'+identity,agent,agentName:snapshot.agentName || '메인',
          active:agent===selectedAgent && json(actual)===json(row.value)});
      }
    };
    for(const snapshot of steps){
      if(snapshot.payload?.messages)add(snapshot,snapshot.payload);
      // Completed outputs/results are observable blocks even before any later input uses them.
      if(snapshot.messages?.length)add(snapshot,{messages:snapshot.messages});
    }
    add(state,state.payload);
    return [...groups.values()].flatMap(group=>[...group.values()].sort((a,b)=>(a.index ?? Infinity)-(b.index ?? Infinity)));
  }
  function componentBlocks(state,steps){
    const selectedAgent=state.agentId || 'main',selectedIndex=steps.indexOf(state),groups=new Map();
    const selectedGroups=new Map((state.contextManifest?.groups || []).map(group=>[group.id,group]));
    for(const [stepIndex,snapshot] of steps.entries()){
      const agent=snapshot.agentId || 'main';
      for(const group of snapshot.contextManifest?.groups || []){
        const groupKey=agent+':'+group.id;
        if(!groups.has(groupKey))groups.set(groupKey,{...group,agent,agentName:snapshot.agentName || '메인',parts:new Map()});
        const target=groups.get(groupKey);
        for(const part of group.parts){
          // Values can change between calls without losing their stable source ID.
          const identity=part.id+':'+json(part.value);
          if(target.parts.has(identity))continue;
          const selected=agent===selectedAgent?selectedGroups.get(group.id)?.parts.find(p=>p.id===part.id && json(p.value)===json(part.value)):null;
          const active=!!selected?.included;
          const reason=agent!==selectedAgent?'다른 에이전트의 독립 컨텍스트':selected?(selected.reason || (active?'실제 입력':'이번 호출에서 제외')):stepIndex>selectedIndex?'선택한 시점에는 아직 입력되지 않음':'이번 요청에 포함되지 않은 원문';
          target.parts.set(identity,{id:groupKey+':'+identity,componentId:part.id,groupId:groupKey,groupLabel:group.label,
            index:group.message_index==null?null:group.message_index+1,role:safeRole(group.role),label:part.label,value:part.value,
            agent,agentName:target.agentName,active,reason,chars:size(part.value),summary:text(part.value),tags:[],sections:[],
            location:selected?.request_range?'content 문자 '+selected.request_range.join('–'):part.field==='tools'?'tools['+(selected?.request_index ?? part.source_index)+']':part.field,
            sourceRange:part.source_range,requestRange:selected?.request_range});
        }
      }
    }
    // Outputs not used by a subsequent request yet still belong to the observed inventory.
    const fallback=allBlocks(state,steps);
    for(const row of fallback){
      const key=row.agent+':'+(row.role==='tools'?'tools':'message:'+(row.index-1));
      if(groups.has(key))continue;
      groups.set(key,{agent:row.agent,parts:new Map([[row.id,{...row,groupId:key,groupLabel:row.label,
        reason:row.agent!==selectedAgent?'다른 에이전트의 독립 컨텍스트':row.active?'실제 입력':'선택한 호출에 아직 입력되지 않은 기록'}]])});
    }
    return [...groups.values()].sort((a,b)=>Number(b.agent===selectedAgent)-Number(a.agent===selectedAgent)).flatMap(group=>[...group.parts.values()]);
  }
  function project(state,steps=[]){
    if(!state?.payload?.messages)return {available:false,rows:[]};
    const stateIndex=steps.indexOf(state);
    let requestIndex=-1,model=state;
    for(let i=stateIndex;i>=0;i--)if(steps[i].agentId===state.agentId && steps[i].phase==='model'){
      requestIndex=i;model=steps[i];break;
    }
    const payload=state.payload,rows=blocks(payload),input=payload.messages;
    // Only post-request assistant output belongs to this call; tool results do
    // not become input until a subsequent request explicitly includes them.
    const added=(state.messages || []).slice(input.length);
    const assistants=added.filter(message=>message.role==='assistant');
    const calls=assistants.flatMap(message=>message.tool_calls || []);
    const drafts=calls.length?[]:(state.toolDrafts || model.toolDrafts || []).filter(draft=>draft.status!=='completed');
    const results=added.filter(message=>message.role==='tool');
    const priorIndex=steps.slice(0,Math.max(0,requestIndex)).findLastIndex(s=>s.agentId===state.agentId && s.phase==='model');
    const previous=priorIndex>=0?steps[priorIndex].payload:null;
    if(previous){
      for(const row of rows){
        const value=row.id==='tools'?previous.tools:previous.messages?.[row.index-1];
        row.changed=json(row.value)!==json(value);
      }
    }
    // A terminal event can contain public speech committed by an earlier tool.
    // Use the actual model-step output, never relabel that speech as this call.
    return {available:true,rows,allRows:state.contextManifest?componentBlocks(state,steps):allBlocks(state,steps),segmented:!!state.contextManifest,payload,requestIndex,agent:state.agentName || '메인',agentId:state.agentId || 'main',
      depth:state.agentMeta?.depth || 0,parent:state.agentMeta?.parentId,iteration:state.iteration || 1,
      phase:state.phase,problem:state.problem,description:state.description,
      reasoning:state.reasoning || model.reasoning || '',output:model.output || assistants.filter(m=>!m.tool_calls?.length).map(m=>text(m.content)).join('\n\n'),calls,drafts,results,
      compared:!!previous,preview:!state.phase,model:payload.model || '',
      constraints:Object.fromEntries(['tool_choice','response_format','structured_outputs','chat_template_kwargs'].filter(key=>key in payload).map(key=>[key,payload[key]]))};
  }
  function mergeDrafts(drafts,fragments){
    for(const fragment of fragments || []){
      const index=fragment.index ?? 0;if(!Number.isInteger(index) || index<0 || index>=50)continue;
      let draft=drafts.find(row=>row.index===index);
      if(!draft){draft={index,id:'',name:'',arguments:'',status:'drafting'};drafts.push(draft);}
      if(fragment.id)draft.id=fragment.id;
      draft.name+=fragment.function?.name || '';draft.arguments+=fragment.function?.arguments || '';
    }
    return drafts;
  }
  let key='',selected=null,current=null,outputKey='';
  const el=id=>document.getElementById(id);
  const node=(tag,cls,value)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(value!==undefined)e.textContent=value;return e;};
  function detail(row){
    selected=row.id;
    for(const button of el('activation-blocks').querySelectorAll('button'))button.setAttribute('aria-pressed',String(button.dataset.block===selected));
    el('activation-detail-title').textContent=row.agentName+' · '+(row.active?'입력됨':'입력 안 됨')+' · '+(row.index?'messages['+(row.index-1)+'] · ':'tools · ')+row.label+(row.reason?' · '+row.reason:'')+(row.location?' · '+row.location:'');
    el('activation-detail').textContent=row.componentId?text(row.value):json(row.value);el('activation-detail').scrollTop=0;
    el('activation-detail-box').hidden=false;
  }
  function reset(message='발화를 생성하거나 실행 스텝을 선택하면 실제 입력 구성을 표시합니다.'){
    key='';selected=null;current=null;outputKey='';
    if(typeof document==='undefined' || !el('activation-context'))return;
    el('activation-title').textContent='Activation context';el('activation-status').textContent=message;
    for(const id of ['activation-blocks','activation-output','activation-results'])el(id).replaceChildren();
    for(const id of ['activation-detail-box','activation-output-box','activation-results-box','activation-options'])el(id).hidden=true;
    el('activation-summary').textContent='';
  }
  function render(state,steps=[],scope=''){
    const data=project(state,steps);current=data;
    if(!data.available){reset(state?.description || '이 단계에는 실제 모델 호출 입력이 없습니다.');return;}
    const nextKey=scope+':'+data.agentId+':'+data.requestIndex+(data.preview?':'+json(data.payload):'')+':'+json(data.allRows.map(row=>[row.id,row.active]));
    el('activation-title').textContent=data.agent+' · 모델 호출 '+data.iteration;
    const states={model:'모델 출력',tool:'도구 실행',result:'도구 결과 도착',done:'턴 / 에이전트 완료',error:'오류·중단',stopped:'중단'};
    el('activation-status').textContent=(data.preview?'미리보기 · 아직 전송 전':(states[data.phase] || '선택한 단계'))+
      (data.depth?' · 별도 서브에이전트 입력 · 부모 '+(data.parent || '—'):' · 메인 입력');
    el('activation-summary').textContent=`전체 ${data.allRows.length}개 블록 · 이번 입력 ${data.allRows.filter(row=>row.active).length}개 · 회색: 이번 호출에 입력되지 않음`;
    if(key!==nextKey){
      key=nextKey;outputKey='';el('activation-detail-box').hidden=true;
      const rendered=data.allRows.map(row=>{
        const button=node('button','ac-block ac-'+row.role+(!row.active?' ac-inactive':''));
        button.type='button';button.dataset.block=row.id;button.dataset.role=row.role;button.dataset.active=String(row.active);button.dataset.agent=row.agent;if(row.componentId)button.dataset.component=row.componentId;button.setAttribute('aria-pressed','false');
        button.append(node('strong','',(!data.segmented && row.index?row.index+'. ':'')+row.label),node('small','',row.agentName+' · '+(row.active?'입력됨':'입력 안 됨')+' · '+row.chars.toLocaleString()+'자'));
        if(row.reason)button.append(node('span','ac-reason',row.reason));
        if(row.tags.length)button.append(node('span','ac-tags',row.tags.join(' · ')));
        if(row.sections.length)button.append(node('span','ac-section-names',row.sections.join(' / ')));
        button.append(node('span','ac-preview',row.summary.slice(0,140) || '(빈 본문)'));
        button.onclick=()=>detail(row);return {row,button};
      });
      if(data.segmented){
        const groups=new Map();
        for(const {row,button} of rendered){
          if(!groups.has(row.groupId)){
            const group=node('section','ac-message-group');group.dataset.group=row.groupId;
            group.append(node('strong','ac-group-title',row.agentName+' · '+(row.index?'messages['+(row.index-1)+'] · ':'')+row.groupLabel));groups.set(row.groupId,group);
          }
          groups.get(row.groupId).append(button);
        }
        el('activation-blocks').replaceChildren(...groups.values());
      }else el('activation-blocks').replaceChildren(...rendered.map(item=>item.button));
      const retained=data.allRows.find(row=>row.id===selected);if(retained)detail(retained);else selected=null;
      const hasOptions=Object.keys(data.constraints).length>0;el('activation-options').hidden=!hasOptions;
      el('activation-options-json').textContent=json(data.constraints);
    }
    el('activation-output-box').hidden=data.preview;
    const stamp=json([data.reasoning,data.output,data.calls,data.drafts,data.results,data.phase]);
    if(outputKey===stamp)return;outputKey=stamp;
    // Keep the existing scroll/focus position when streaming text grows.
    const output=el('activation-output'),positions=new Map([...output.querySelectorAll('pre')].map(n=>[n.dataset.part,n.scrollTop]));
    const section=(label,value,part,cls='')=>{
      const box=node('div','ac-output-part '+cls);box.append(node('strong','',label));
      const pre=node('pre','',value);pre.dataset.part=part;box.append(pre);return box;
    };
    const pieces=[];
    if(data.reasoning)pieces.push(section('Reasoning · 이번 호출의 출력',data.reasoning,'reasoning','ac-reasoning'));
    if(data.output)pieces.push(section('본문 · 이번 호출의 출력',data.output,'content'));
    for(const [i,call] of data.calls.entries())pieces.push(section('도구 호출 → '+call.function?.name,call.function?.arguments || '', 'call-'+i,'ac-assistant'));
    for(const [i,draft] of data.drafts.entries())pieces.push(section('도구 호출 작성 중 → '+(draft.name || '이름 수신 중'),draft.arguments || '인자 수신 대기', 'draft-'+i,'ac-assistant'));
    if(!pieces.length)pieces.push(node('p','hint',data.problem?'이 호출에서 반환된 출력이 없습니다.':'입력 전송됨 · 출력 수신 대기'));
    output.replaceChildren(...pieces);for(const pre of output.querySelectorAll('pre'))pre.scrollTop=positions.get(pre.dataset.part) || 0;
    el('activation-results-box').hidden=!data.results.length;
    el('activation-results').replaceChildren(...data.results.map(message=>{
      const name=data.calls.find(call=>call.id===message.tool_call_id)?.function?.name || message.name || '도구';
      return section(name+' 실행 결과',text(message.content),message.tool_call_id,'ac-tool');
    }));
  }
  const api={blocks,allBlocks,componentBlocks,project,mergeDrafts,render,reset};
  if(typeof module!=='undefined' && module.exports)module.exports=api;else window.ActivationContext=api;
})();
