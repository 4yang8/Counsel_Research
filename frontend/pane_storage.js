'use strict';
// Session pointers and version choices belong to a pane, never to another experiment.
// /lab without a pane parameter remains available as the standalone UI.
window.LabStorage=(() => {
  const id=new URLSearchParams(location.search).get('pane');
  const prefix=id && /^[a-zA-Z0-9_-]{1,80}$/.test(id)?'counseling-pane:'+id+':':'';
  return {
    prefix,
    getItem:key=>localStorage.getItem(prefix+key),
    setItem:(key,value)=>localStorage.setItem(prefix+key,value),
    removeItem:key=>localStorage.removeItem(prefix+key)
  };
})();
