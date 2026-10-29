const assert=require('node:assert/strict');
const trace=require('../static/paired_trace.js');
const preview=trace.notePreview;
const base='## 노트\n기존 내용\n계속 유지';
assert.deepEqual(preview('{"target":"client","edits":[{"old_text":"미완성').lines,[]);
assert.deepEqual(preview('{"target":"client","edits":[{"old_text":"기존 내용","new_text":"새\\n문장').lines,[
 {kind:'deleted',text:'기존 내용'},{kind:'added',text:'새'},{kind:'added',text:'문장'}]);
assert.equal(preview('{"target":"client","edits":[').target,'client');
assert.deepEqual(preview('{"content":"\\uD55C\\uAE').lines,[{kind:'added',text:'한'}]);
assert.deepEqual(preview('{"content":"\\uD83D').lines,[]);
assert.deepEqual(preview('{"content":"\\uD83D\\uDE00"}').lines,[{kind:'added',text:'😀'}]);
assert.deepEqual(preview('{"content":"## 노트\\n',base).lines,[]);
assert.deepEqual(preview(JSON.stringify({content:base}),base).lines,[]);
assert.deepEqual(preview(JSON.stringify({content:'## 노트\n새 내용\n계속 유지'}),base).lines,[
 {kind:'deleted',text:'기존 내용'},{kind:'added',text:'새 내용'}]);
assert(!preview('{"content":"## 노트\\n새',base).lines.some(l=>l.text==='계속 유지'));
assert.deepEqual(preview('{"content":"bad\\q').lines,[]);
// Streaming is a display-only draft. Execution promotes the same entry only
// after a complete tool_start; result/diff is authoritative and time-bounded.
const attempt=trace.create('counselor','draft',1,'P2','Q1');
trace.accept(attempt,{type:'request',payload:{messages:[{role:'user',content:'BASE'}]},iteration:1});
const args=JSON.stringify({target:'client',edits:[{old_text:'old',new_text:'new'}]});
trace.accept(attempt,{type:'chunk',data:{choices:[{delta:{tool_calls:[{index:0,id:'n',function:{name:'update_notes',arguments:args.slice(0,35)}}]}}]}});
assert.equal(attempt.steps.length,1);assert.equal(attempt.steps[0].messages.length,1);assert.equal(attempt.steps[0].trace.length,0);
trace.accept(attempt,{type:'chunk',data:{choices:[{delta:{tool_calls:[{index:0,function:{arguments:args.slice(35)}}]}}]}});
assert.equal(attempt.calls[0].toolDrafts[0].arguments,args);
const draftKey=attempt.calls[0].toolDrafts[0].draftKey;
trace.accept(attempt,{type:'tool_start',call_id:'n',name:'update_notes',arguments:args});
assert.equal(attempt.calls[0].toolDrafts[0].status,'running');
assert.equal(attempt.steps.at(-1).trace[0].draftKey,draftKey);
trace.accept(attempt,{type:'tool_result',call_id:'n',name:'update_notes',model_content:'FAILED',is_error:true});
assert.equal(attempt.steps.at(-1).trace[0].status,'error');
assert.equal(attempt.steps.at(-1).trace[0].noteChanges,undefined);
assert.equal(attempt.steps[0].messages.length,1);
console.log('PASS: partial JSON, escapes, replacement previews, no phantom deletions, immutable raw context and draft execution identity');
