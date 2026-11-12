'use strict';
// Browser-local drafts: saving a form never changes an already-created job.
window.formSettings = function(key, root, {exclude=[], enabled=true}={}) {
  let saved={};
  try {const value=JSON.parse(localStorage.getItem(key));if(value&&typeof value==='object')saved=value;} catch (_) {}
  const fields=()=>[...root.querySelectorAll('input[id], select[id], textarea[id]')].filter(e=>!exclude.includes(e.id)&&!e.readOnly);
  return {
    get data(){return saved;},
    restore(ids=null){for(const e of fields()){
      if(ids&&!ids.includes(e.id))continue;
      const value=saved.fields?.[e.id];if(value===undefined)continue;
      if(e.type==='checkbox'){if(typeof value==='boolean')e.checked=value;}
      else if(typeof value==='string'&&(e.tagName!=='SELECT'||[...e.options].some(o=>o.value===value)))e.value=value;
    }},
    save(extra={}){if(!enabled)return;
      saved={...saved,...extra,fields:Object.fromEntries(fields().map(e=>[e.id,e.type==='checkbox'?e.checked:e.value]))};
      try{localStorage.setItem(key,JSON.stringify(saved));}catch(_){}
    },
    checks(id,values){if(!Array.isArray(values))return;const set=new Set(values);document.getElementById(id).querySelectorAll('input').forEach(e=>e.checked=!e.disabled&&set.has(e.value));}
  };
};
