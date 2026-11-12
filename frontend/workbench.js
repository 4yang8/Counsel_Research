'use strict';
(() => {
  const tabs=[...document.querySelectorAll('#work-tabs button')];
  function select(view,focus=false){
    if(!tabs.some(t=>t.dataset.view===view))view='lab';
    for(const tab of tabs){const active=tab.dataset.view===view,panel=document.getElementById('work-'+tab.dataset.view);
      tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;panel.hidden=!active;
      if(active){const frame=panel.querySelector('iframe[data-src]');if(frame&&!frame.getAttribute('src'))frame.src=frame.dataset.src;if(focus)tab.focus();}}
    try{localStorage.setItem('counseling-work-view',view);}catch{}
  }
  tabs.forEach((tab,i)=>{tab.onclick=()=>select(tab.dataset.view);tab.onkeydown=e=>{
    const index=e.key==='ArrowRight'?(i+1)%tabs.length:e.key==='ArrowLeft'?(i+tabs.length-1)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:null;
    if(index!==null){e.preventDefault();select(tabs[index].dataset.view,true);}};});
  let previous='lab';try{previous=localStorage.getItem('counseling-work-view')||'lab';}catch{}select(previous);
})();
