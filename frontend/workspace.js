'use strict';
(() => {
  const $=id=>document.getElementById(id),key='counseling-workspace-v1',panes=new Map();
  let state;
  function storageError(){const el=$('workspace-error');el.hidden=false;el.textContent='브라우저 저장 공간에 탭 설정을 저장하지 못했습니다. 현재 화면에서는 계속 사용할 수 있습니다.';}
  function save(){try{localStorage.setItem(key,JSON.stringify(state));}catch{storageError();}}
  function prefix(id){return 'counseling-pane:'+id+':';}
  function seed(id,snapshot,legacy=false){
    try{
      for(const role of ['client','counselor']){
        const value=snapshot?.[role+'Version'] || localStorage.getItem('counseling-last-'+role+'-version');
        if(value)localStorage.setItem(prefix(id)+'counseling-last-'+role+'-version',value);
      }
      if(snapshot)localStorage.setItem(prefix(id)+'draft',JSON.stringify(snapshot));
      if(legacy){const session=localStorage.getItem('counseling-paired-session');if(session)localStorage.setItem(prefix(id)+'counseling-paired-session',session);}
    }catch{storageError();}
  }
  try{state=JSON.parse(localStorage.getItem(key));}catch{}
  if(!state || !Array.isArray(state.tabs) || !state.tabs.length || state.tabs.some(t=>!t || !/^[a-zA-Z0-9_-]{1,80}$/.test(t.id)) || new Set(state.tabs.map(t=>t.id)).size!==state.tabs.length){
    const id=crypto.randomUUID();state={tabs:[{id,label:'실험 1'}],active:id,expanded:false,next:2};seed(id,null,true);
  }
  if(!state.tabs.some(t=>t.id===state.active))state.active=state.tabs[0].id;
  function status(id){return panes.get(id)?.frame.contentWindow?.LabPane?.status();}
  function render(){
    let running=0;
    for(const tab of state.tabs){
      const pane=panes.get(tab.id);if(!pane)continue;
      const info=status(tab.id),active=tab.id===state.active;
      if(info?.label)tab.label=info.label;
      if(info?.busy)running++;
      pane.tab.classList.toggle('selected',active);pane.tab.dataset.busy=String(!!info?.busy);
      pane.button.textContent=tab.label;pane.button.title=info?.title || tab.label;
      pane.button.setAttribute('aria-selected',String(active));pane.button.tabIndex=active?0:-1;
      pane.close.disabled=!!info?.busy || !!info?.reserved || state.tabs.length===1;
      pane.close.title=info?.reserved?'대조실험 제어 중에는 탭을 닫을 수 없습니다.':info?.busy?'생성 또는 불러오기 중에는 탭을 닫을 수 없습니다.':'탭 닫기 · 저장된 대화는 채팅 내역에 남습니다.';
      pane.frame.hidden=!active;
    }
    $('active-experiment-label').textContent=state.tabs.find(t=>t.id===state.active)?.label || '';
    $('workspace-running').textContent=running?running+'개 실행 중':'';
    $('experiment-tabs-row').hidden=!state.expanded;
    $('experiment-tabs-toggle').textContent=(state.expanded?'▾':'▸')+' 실험 탭 '+state.tabs.length;
    $('experiment-tabs-toggle').setAttribute('aria-expanded',String(!!state.expanded));
  }
  function activate(id,focus=false){
    panes.get(state.active)?.frame.contentWindow?.LabPane?.save();
    state.active=id;render();save();$('add-experiment').disabled=!panes.get(id)?.frame.contentWindow?.LabPane?.ready;
    if(focus)panes.get(id).button.focus();
    panes.get(id)?.frame.contentWindow?.dispatchEvent(new Event('resize'));
  }
  function mount(tab){
    const holder=document.createElement('div');holder.className='experiment-tab';
    const button=document.createElement('button');button.setAttribute('role','tab');button.id='tab-'+tab.id;button.setAttribute('aria-controls','pane-'+tab.id);button.onclick=()=>activate(tab.id);
    const close=document.createElement('button');close.className='tab-close';close.textContent='×';close.setAttribute('aria-label','실험 탭 닫기');close.onclick=()=>remove(tab.id);
    holder.append(button,close);$('experiment-tabs').append(holder);
    const frame=document.createElement('iframe');frame.id='pane-'+tab.id;frame.title=tab.label;frame.setAttribute('role','tabpanel');frame.setAttribute('aria-labelledby',button.id);frame.src='/lab?pane='+encodeURIComponent(tab.id);
    panes.set(tab.id,{tab:holder,button,close,frame});$('experiment-panes').append(frame);
    button.onkeydown=event=>{
      const index=state.tabs.findIndex(t=>t.id===tab.id);let next;
      if(event.key==='ArrowRight')next=(index+1)%state.tabs.length;
      if(event.key==='ArrowLeft')next=(index-1+state.tabs.length)%state.tabs.length;
      if(event.key==='Home')next=0;if(event.key==='End')next=state.tabs.length-1;
      if(next!==undefined){event.preventDefault();activate(state.tabs[next].id,true);}
    };
  }
  function remove(id){
    if(state.tabs.length===1 || status(id)?.busy || status(id)?.reserved)return;
    const index=state.tabs.findIndex(t=>t.id===id),pane=panes.get(id);
    state.tabs.splice(index,1);
    if(state.active===id)state.active=state.tabs[Math.min(index,state.tabs.length-1)].id;
    // Closing a view does not delete its server-side conversation.
    pane.frame.contentWindow?.LabPane?.dispose();pane.frame.remove();pane.tab.remove();panes.delete(id);
    try{for(const k of Object.keys(localStorage))if(k.startsWith(prefix(id)))localStorage.removeItem(k);}catch{}
    render();save();panes.get(state.active).button.focus();
  }
  $('add-experiment').onclick=()=>{
    const source=panes.get(state.active)?.frame.contentWindow?.LabPane;
    if(!source?.ready)return;
    source.save();const id=crypto.randomUUID();seed(id,source.snapshot());
    const tab={id,label:'실험 '+(state.next || state.tabs.length+1)};state.next=(state.next || state.tabs.length+1)+1;
    state.tabs.push(tab);mount(tab);state.expanded=true;activate(id);
  };
  $('experiment-tabs-toggle').onclick=()=>{state.expanded=!state.expanded;render();save();};
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin || !['lab-pane-status','lab-pane-storage-error'].includes(event.data?.type))return;
    if(![...panes.values()].some(p=>p.frame.contentWindow===event.source))return;
    if(event.data.type==='lab-pane-storage-error'){storageError();return;}
    render();save();$('add-experiment').disabled=!panes.get(state.active)?.frame.contentWindow?.LabPane?.ready;
  });
  window.addEventListener('beforeunload',event=>{
    for(const pane of panes.values())pane.frame.contentWindow?.LabPane?.save();
    if(state.tabs.some(t=>status(t.id)?.busy)){event.preventDefault();event.returnValue='';}
  });
  window.ExperimentWorkspace={tabs:()=>state.tabs.map(tab=>({...tab,status:status(tab.id),api:panes.get(tab.id)?.frame.contentWindow?.LabPane})),activeId:()=>state.active,activate,refresh:render};
  for(const tab of state.tabs)mount(tab);render();save();$('add-experiment').disabled=true;
})();
