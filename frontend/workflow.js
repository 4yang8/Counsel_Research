/* Observed model calls and tools; feedback edges appear only after results arrive. */
'use strict';
window.Workflow = (() => {
  const el=id=>document.getElementById(id);
  const text=(tag,value='',cls='')=>Object.assign(document.createElement(tag),{textContent:value,className:cls});
  const key=(agent,call)=>JSON.stringify([agent,call]);
  function readable(value){
    if(typeof value!=='string')return JSON.stringify(value ?? '');
    try{const obj=JSON.parse(value);return obj.content?.filter?.(x=>x.type==='text').map(x=>x.text).join('\n') || obj.structuredContent?.content || value;}catch{return value;}
  }
  function describe(s,child){
    const tool=s.phase==='result'?s.trace.find(t=>t.id===s.messages.at(-1)?.tool_call_id):s.trace.at(-1);
    let status='모델 호출',heading='작업 지시',preview=s.messages.findLast(m=>m.role==='user')?.content || '';
    if(s.phase==='model'){status=s.messages.some(m=>m.role==='tool')?'도구 결과를 반영해 응답 생성 중':'요청에 응답 생성 중';heading=s.output?'수신한 응답':'응답 대기';preview=s.output || preview;}
    else if(s.phase==='tool' || s.phase==='result'){status='도구 호출 생성 완료';heading=tool?.name || '도구';preview=s.output || s.reasoning || tool?.arguments || '';}
    else if(s.phase==='done'){status='답변 완료';heading='최종 응답';preview=s.output || '';}
    else if(['error','stopped'].includes(s.phase)){status='오류 / 중단';heading='오류 정보';preview=s.description || '';}
    return {status,heading,preview:String(preview),toolName:s.phase==='tool'?tool?.name || '':''};
  }
  function build(steps,index){
    const nodes=[],edges=[],latest=new Map(),tools=new Map(),models=new Map(),recordings=new Map();let row=0;
    const addEdge=(from,to,kind,step)=>{if(from && to)edges.push({from:from.id,to:to.id,kind,step,active:step===index});};
    steps.slice(0,index+1).forEach((s,i)=>{
      if(s.phase==='recording'){
        const agent=s.agentId || 'main';let record=recordings.get(agent);
        if(!record){
          record={id:'recording:'+agent,kind:'recording',agent,first:i,step:i,state:s,x:20,y:20+row++*140};
          recordings.set(agent,record);nodes.push(record);
          for(const list of models.values())if(list[0].state.agentMeta?.kind==='note_summarizer')addEdge(list.at(-1),record,'summary',i);
        }
        record.state=s;record.step=i;return;
      }
      const agent=s.agentId || 'main';let model=latest.get(agent);
      if(s.phase==='model' || !model){
        const previous=model;
        model={id:'model:'+key(agent,i),kind:s.phase==='submit'?'user':'model',first:i,step:i,state:s,agent,row:row++,tools:[]};
        model.x=20+(s.agentMeta?.depth || 0)*24;model.y=20+model.row*140;nodes.push(model);latest.set(agent,model);
        if(!models.has(agent))models.set(agent,[]);models.get(agent).push(model);
        addEdge(previous,model,'flow',i);
        const parentTool=tools.get(key(s.agentMeta?.parentId,s.agentMeta?.parentCallId));
        if(parentTool && models.get(agent).length===1)addEdge(parentTool,model,'delegate',i);
      }
      model.state=s;model.step=i;
      for(const trace of s.trace || []){
        const id=key(agent,trace.id);let tool=tools.get(id);
        if(!tool){
          const toolRow=model.tools.length?row++:model.row;
          tool={id:'tool:'+id,kind:'tool',first:i,step:i,state:s,tool:trace,agent,source:model,row:toolRow};
          tool.x=350+(s.agentMeta?.depth || 0)*24;tool.y=20+toolRow*140;model.tools.push(tool);tools.set(id,tool);nodes.push(tool);
          addEdge(model,tool,'call',i);
        }
        tool.tool=trace;
      }
      if(s.phase==='tool'){
        const trace=s.trace.at(-1),tool=trace && tools.get(key(agent,trace.id));
        if(tool){tool.state=s;tool.step=i;}
      }
      if(s.phase==='result'){
        const tool=tools.get(key(agent,s.messages.at(-1)?.tool_call_id));
        if(tool){tool.state=s;tool.step=i;tool.resultStep=i;}
      }
    });
    for(const [agent,list] of models){
      const meta=list[0].state.agentMeta,parentTool=tools.get(key(meta?.parentId,meta?.parentCallId));
      if(parentTool && ['completed','error'].includes(parentTool.tool.status))addEdge(list.at(-1),parentTool,'return',parentTool.step);
      if(meta?.kind==='summary' && !parentTool){const parent=models.get(meta.parentId)?.find(n=>n.first>list.at(-1).first);if(parent)addEdge(list.at(-1),parent,'summary',parent.first);}
      if(meta?.kind==='trait_router' && !parentTool){const parent=models.get(meta.parentId)?.find(n=>n.first>list.at(-1).first);if(parent)addEdge(list.at(-1),parent,'routing',parent.first);}
      if(meta?.kind==='note_summarizer' && !parentTool){
        const trigger=[...tools.values()].findLast(t=>t.agent===meta.parentId && t.tool.name==='commit_client_utterance' && t.first<list[0].first);
        addEdge(trigger,list[0],'automatic_summary',list[0].first);
      }
    }
    const waiting=new Map();
    for(const tool of tools.values())if(tool.resultStep!==undefined){
      let next=models.get(tool.agent)?.find(n=>n.first>tool.resultStep);
      if(!next)next=recordings.get(tool.agent);
      if(!next){
        next=waiting.get(tool.agent);
        if(!next){next={id:'context:'+tool.agent,kind:'context',agent:tool.agent,first:tool.resultStep,step:tool.resultStep,state:tool.state,x:tool.source.x,y:20+row++*140};waiting.set(tool.agent,next);nodes.push(next);}
        next.state=tool.state;next.step=tool.resultStep;
      }
      addEdge(tool,next,'feedback',tool.resultStep);
    }
    return {nodes,edges};
  }
  function svgNode(tag,attrs){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);return n;}
  const edgeLabels={call:'도구 호출',feedback:'결과 반환',flow:'다음 모델 호출',delegate:'서브에이전트 위임',return:'조사 결과 반환',summary:'요약 전달',routing:'자동 선정 특성 전달',automatic_summary:'발화 확정 후 자동 요약'};
  let paintedSteps=null,paintedIndex=-1,graph=null;
  function nodeInfo(n){
    if(n.kind==='recording')return {status:n.state.recording.discarded?'미저장':n.state.recording.pending?'검증·저장 대기':'저장 완료',preview:'Python 기록 · '+({raw:'선택 원문 복사',summary:'반환 요약 직접 저장',snapshot:'확정 필드 스냅샷'}[n.state.recording.mode] || '')};
    if(n.kind==='context')return {status:'결과 수신 · 다음 호출 대기',preview:'tool 메시지가 추가됐습니다. 아직 후속 모델 호출 전입니다.'};
    if(n.kind!=='tool')return describe(n.state);
    const t=n.tool;let args;try{args=JSON.parse(t.arguments);}catch{}
    let preview=t.result!=null?readable(t.result):args?.task || args?.path || args?.request || args?.reason || t.arguments || '';
    try{const saved=JSON.parse(preview);if(saved.saved)preview=`저장 완료 · 변경 ${saved.edits?.length || 0}건\n`+(saved.edits || []).map(e=>e.new_text || '기존 내용 삭제').join('\n');}catch{}
    if(t.name==='update_notes' && args?.target){const label={client:'내담자 노트',counselor:'상담사 생각 노트'}[args.target];if(label)preview=label+'\n'+preview;}
    return {status:{running:'실행 중',completed:'완료 · 결과 반환',error:'오류 결과 반환',unknown:'실행 상태 미확인'}[t.status] || '호출됨',preview:String(preview)};
  }
  function render(steps,index,select){
    const canvas=el('workflow-canvas'),scroll=el('workflow-scroll');
    if(paintedSteps!==steps || paintedIndex!==index){
      paintedSteps=steps;paintedIndex=index;graph=build(steps,index);
      const top=scroll.scrollTop,left=scroll.scrollLeft,{nodes,edges}=graph;
      const width=Math.max(300,...nodes.map(n=>n.x+250)),height=Math.max(160,...nodes.map(n=>n.y+120));
      canvas.replaceChildren();canvas.style.width=width+'px';canvas.style.height=height+'px';
      const svg=svgNode('svg',{width,height,'aria-hidden':'true'}),defs=svgNode('defs',{});
      for(const kind of Object.keys(edgeLabels)){
        const marker=svgNode('marker',{id:'wf-arrow-'+kind,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto'});
        marker.append(svgNode('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:['feedback','return'].includes(kind)?'#187b55':kind==='call'||kind==='delegate'?'#a86209':'#8795a7'}));defs.append(marker);
      }
      svg.append(defs);canvas.append(svg);const byId=new Map(nodes.map(n=>[n.id,n]));
      for(const e of edges){
        const a=byId.get(e.from),b=byId.get(e.to);let sx,sy,ex,ey,d,lx,ly;
        if((e.kind==='call' || e.kind==='feedback') && a.y===b.y){
          const forward=e.kind==='call';sx=a.x+(forward?220:0);ex=b.x+(forward?0:220);sy=a.y+(forward?30:80);ey=b.y+(forward?30:80);
          d=`M ${sx} ${sy} C ${(sx+ex)/2} ${sy}, ${(sx+ex)/2} ${ey}, ${ex} ${ey}`;lx=(sx+ex)/2;ly=(sy+ey)/2-7;
        }else{
          sx=a.x+110;sy=a.y+106;ex=b.x+110;ey=b.y;
          if(e.kind==='return'){sx=a.x+220;sy=a.y+75;ex=b.x+220;ey=b.y+75;const route=Math.max(sx,ex)+20;d=`M ${sx} ${sy} C ${route} ${sy}, ${route} ${ey}, ${ex} ${ey}`;lx=route-40;ly=(sy+ey)/2;}
          else{const mid=(sy+ey)/2;d=`M ${sx} ${sy} C ${sx} ${mid}, ${ex} ${mid}, ${ex} ${ey}`;lx=(sx+ex)/2+65;ly=mid;}
        }
        const path=svgNode('path',{d,class:'wf-edge '+e.kind+(e.active?' active':''),'marker-end':`url(#wf-arrow-${e.kind})`,'data-from':e.from,'data-to':e.to,'data-step':e.step});
        path.onclick=()=>select(e.step);svg.append(path);const label=svgNode('text',{x:lx,y:ly,class:'wf-edge-label','text-anchor':'middle'});label.textContent=edgeLabels[e.kind];svg.append(label);
      }
      nodes.forEach(n=>{
        const s=n.state,isTool=n.kind==='tool',selected=isTool?['tool','result'].includes(steps[index]?.phase)&&n.step===index:n.step===index&&!['tool','result'].includes(steps[index]?.phase);
          const color=isTool?(n.tool.status==='error'?'error':'tool'):s.problem?'error':['context','recording'].includes(n.kind)?'result':n.kind==='user'?'user':'model';
        const box=text('article','',`wf-node wf-${color}${selected?' selected':''}`);box.dataset.kind=n.kind;box.dataset.step=n.step;box.dataset.agent=n.agent;box.dataset.phase=s.phase;
        if(isTool)box.dataset.call=n.tool.id;box.style.left=n.x+'px';box.style.top=n.y+'px';
        const button=text('button','','wf-agent-button');button.type='button';button.setAttribute('aria-current',selected?'step':'false');
        button.append(text('strong',isTool?n.tool.name:n.kind==='recording'?'Python 기록 · 자동 저장':n.kind==='context'?'결과 → 누적 컨텍스트':(n.kind==='user'?'입력':`LLM ${s.iteration || 1}`)+' · '+s.agentName),text('span','','wf-status'),text('span','','wf-node-preview'),text('small',isTool?n.tool.id:`${n.first+1}단계 · ${s.agentMeta?.depth?`깊이 ${s.agentMeta.depth}`:'메인'}`));
        button.title=s.title;button.onclick=()=>select(isTool?n.step:n.first);box.append(button);canvas.append(box);n.element=box;
      });scroll.scrollTop=top;scroll.scrollLeft=left;
    }
    for(const n of graph?.nodes || []){const info=nodeInfo(n);n.element.querySelector('.wf-status').textContent=info.status;n.element.querySelector('.wf-node-preview').textContent=info.preview.slice(0,110)+(info.preview.length>110?'…':'');}
    const s=steps[index];if(!s)return;
    el('workflow-detail-title').textContent=`${index+1}단계 · ${s.title}`;
    el('workflow-explanation').textContent=s.phase==='tool'?'모델이 만든 도구 호출을 백엔드가 실행합니다. 도구 박스와 호출 화살표로 표시합니다.':s.phase==='result'?'도구 결과가 tool 메시지로 추가됐습니다. 초록 반환 화살표는 실제 도착한 결과입니다.':s.phase==='model'?'이 호출의 실제 누적 컨텍스트로 응답을 생성합니다. 이전 도구 호출과 결과는 앞선 박스에 남습니다.':s.description;
    const detail={agent_id:s.agentId,parent_agent_id:s.agentMeta?.parentId,phase:s.phase};
    if(s.phase==='tool'){const t=s.trace.at(-1);Object.assign(detail,{call_id:t?.id,name:t?.name,preamble:s.output || null,arguments:t?.arguments});}
    else if(s.phase==='result'){const m=s.messages.at(-1),t=s.trace.find(t=>t.id===m?.tool_call_id);Object.assign(detail,{name:t?.name,status:t?.status,message:m});}
    else if(s.phase==='model')Object.assign(detail,{iteration:s.iteration,input_messages:s.messages.length,output:s.output});
    else Object.assign(detail,{output:s.phase==='submit'?s.messages.at(-1)?.content:s.output});
    if(s.extras && Object.keys(s.extras).length)detail.saved_data=s.extras;el('workflow-detail').textContent=JSON.stringify(detail,null,2);
  }
  function reset(){paintedSteps=null;paintedIndex=-1;graph=null;el('workflow-canvas').replaceChildren();el('workflow-canvas').removeAttribute('style');el('workflow-detail').textContent='';el('workflow-detail-title').textContent='선택한 스텝 · 상세정보';el('workflow-explanation').textContent='질문을 보내면 실행 흐름이 표시됩니다.';el('workflow-scroll').scrollTop=0;el('workflow-scroll').scrollLeft=0;}
  return {render,reset,build,describe};
})();
