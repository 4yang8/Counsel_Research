const assert=require('node:assert/strict');
const trace=require('../static/paired_trace.js');
const attempt=trace.create('client','test',1,'basic','state_summary');
const payload={messages:[{role:'system',content:'system'},{role:'user',content:'question'}],tools:[]};
trace.accept(attempt,{type:'state_summary',summary:'BEFORE_SUMMARY',state_before:{trust:'low'}});
trace.accept(attempt,{type:'request',payload,iteration:1});
trace.accept(attempt,{type:'chunk',data:{choices:[{delta:{content:'before tool'}}]}});
trace.accept(attempt,{type:'tool_start',call_id:'call-1',name:'retrieve_inner_state',arguments:'{}'});
trace.accept(attempt,{type:'tool_result',call_id:'call-1',name:'retrieve_inner_state',model_content:'SOURCE_RECORD'});
const second={messages:[...attempt.steps.at(-1).messages],tools:[]};
trace.accept(attempt,{type:'request',payload:second,iteration:2});
trace.accept(attempt,{type:'state_record',record:{inner_state:{trust:'AFTER_STATE'},feelings:['safe']}});
trace.accept(attempt,{type:'client_record',model_response:'PRIVATE_MODEL_OUTPUT',response:'public'});
trace.accept(attempt,{type:'done',response:'public'});
assert.equal(attempt.steps[0].messages.length,2);
assert.equal(attempt.steps[1].messages.at(-1).role,'assistant');
assert.equal(attempt.steps[1].trace[0].status,'running');
assert.equal(attempt.steps[2].messages.at(-1).role,'tool');
assert.equal(attempt.steps[2].trace[0].result,'SOURCE_RECORD');
assert.deepEqual(attempt.steps[3].payload.messages,second.messages);
assert.equal(attempt.steps[0].extras.inner,undefined);
assert.equal(attempt.steps[3].extras.inner,undefined);
assert.equal(attempt.steps.at(-1).extras.inner.inner_state.trust,'AFTER_STATE');
assert.equal(attempt.steps.at(-1).messages.at(-1).content,'PRIVATE_MODEL_OUTPUT');
assert.equal(attempt.utterance,'public');
payload.messages[0].content='MUTATED';
assert.equal(attempt.steps[0].payload.messages[0].content,'system');
assert.equal(attempt.steps[0].messages[0].content,'system');
const child=trace.create('client','nested',2,'C4','state_summary');
trace.accept(child,{type:'request',payload,iteration:1});
trace.accept(child,{type:'agent_event',agent_id:'child',agent_name:'조회 담당',parent_agent_id:'main',depth:1,agent_kind:'summary',event:{type:'request',payload,iteration:1}});
trace.accept(child,{type:'agent_event',agent_id:'child',agent_name:'조회 담당',parent_agent_id:'main',depth:1,event:{type:'done',response:'child result'}});
assert.equal(child.status,'running');
assert.equal(child.steps.at(-1).agentId,'child');
assert.equal(child.steps.at(-1).agentMeta.depth,1);
assert.equal(child.steps.at(-1).messages.at(-1).content,'child result');
assert.equal(child.steps[0].messages.length,2);
// Tool snapshots use the exact transmitted assistant, including reasoning.
const reasoning=trace.create('counselor','reasoning',2,'P1','state_summary');
trace.accept(reasoning,{type:'request',payload:{messages:[{role:'user',content:'BASE_NOTE'}]},iteration:1});
const calls=['a','b'].map(id=>({id,type:'function',function:{name:'update_notes',arguments:'{}'}}));
const assistant={role:'assistant',content:'',reasoning:'ACTUAL_REASONING',tool_calls:calls};
for(const call of calls){
 trace.accept(reasoning,{type:'tool_start',iteration:1,call_id:call.id,name:'update_notes',arguments:'{}',assistant_message:assistant});
 trace.accept(reasoning,{type:'tool_result',call_id:call.id,name:'update_notes',model_content:'SAVED'});
}
const messages=reasoning.steps.at(-1).messages;
assert.equal(messages.filter(m=>m.role==='assistant').length,1);
assert.deepEqual(messages.find(m=>m.role==='assistant'),assistant);
assert.equal(reasoning.steps[0].messages.length,1);
assert.equal(reasoning.steps[0].messages[0].content,'BASE_NOTE');
// Saved note changes belong to their individual tool call, including replay
// of repeated writes to the same file. Earlier model/start steps stay intact.
const notes=trace.create('counselor','notes',2,'P2','state_summary');
trace.accept(notes,{type:'request',payload,iteration:1});
for(const [id,target,text] of [['n1','client','client change'],['n2','counselor','first plan'],['n3','counselor','updated plan']]){
 trace.accept(notes,{type:'tool_start',call_id:id,name:'update_notes',arguments:JSON.stringify({target})});
 const start=notes.steps.at(-1);
 trace.accept(notes,{type:'version_event',version:'P2',section:target+'_notes',action:'updated',data:{target,content:text,diff:['--- old','+++ new','+ '+text]}});
 assert.equal(start.trace.find(t=>t.id===id).noteChanges,undefined);
 trace.accept(notes,{type:'tool_result',call_id:id,name:'update_notes',model_content:'saved'});
 assert.equal(notes.steps.at(-1).trace.find(t=>t.id===id).noteChanges[0].content,text);
}
assert.deepEqual(notes.steps.at(-1).trace.map(t=>t.noteChanges[0].content),['client change','first plan','updated plan']);
assert.equal(notes.steps[0].trace.length,0);
assert.deepEqual(notes.pendingNoteChanges,{});
console.log('PASS: immutable context snapshots, tool-call/result accumulation, time-bounded state, private/public separation and child completion');
// Renamed historical journals keep their original payload. Map their version
// only in a Q1a replay; a future independent Q2 must keep its own records.
const legacy=trace.create('client','legacy',1,'P3a','Q1a');
const oldEvent={type:'version_event',version:'Q2',section:'drafts',action:'staged',data:{draft_id:'draft_1',utterance:'기존 발화'}};
trace.accept(legacy,oldEvent);
assert.equal(legacy.extras.versions.Q1a.drafts.current.utterance,'기존 발화');
assert.equal(legacy.extras.versions.Q2,undefined);
assert.equal(oldEvent.version,'Q2');
const future=trace.create('client','future',1,'P3a','Q2');trace.accept(future,oldEvent);
assert.equal(future.extras.versions.Q1a,undefined);
assert.equal(future.extras.versions.Q2.drafts.current.draft_id,'draft_1');
console.log('PASS: historical Q2 replay maps to Q1a without rewriting provenance or capturing a future Q2');
