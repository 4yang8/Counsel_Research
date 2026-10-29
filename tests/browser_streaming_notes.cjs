const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn(process.env.GEMMA_TEST_PYTHON || (process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--state-summary']);let browser;
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1440,height:1000});await page.goto(url+'/lab');
  await page.waitForFunction(()=>document.querySelector('#tool-options input') && document.querySelector('#counselor-version option[value=P2]'));
  assert.equal(await page.$$eval('#dialogue-editor input:checked',rows=>rows.length),0);
  assert((await page.$$eval('#dialogue-editor .dialogue-turn',rows=>rows.length))>0);
  const noSeed=await page.evaluate(()=>body());assert.deepEqual(noSeed.scenario.dialogue,[]);assert(!noSeed.scenario.dialogue_turns);
  // The server accepts zero example turns and still has a separate opening.
  await page.evaluate(async()=>{const request=body();request.counselor_version='P2';const saved=await api('/api/paired/create',request);if(saved.public_dialogue.length!==1)throw Error('seed leaked into session');});
  await page.click('#visual-tab');
  await page.evaluate(()=>{
   const host=document.createElement('article');host.className='paired-speech counselor';host.id='stream-test';document.querySelector('#paired-transcript').append(host);
   window.__attempt=PairedTrace.create('counselor','ui-stream',1,'P2','state_summary');
   window.__push=event=>{PairedTrace.accept(window.__attempt,event);LabVersionViews.mountAttempt(host,window.__attempt,()=>{});};
   window.__push({type:'version_event',version:'P2',section:'client_notes',action:'snapshot',data:{content:'old note'}});
   window.__push({type:'request',payload:{messages:[{role:'user',content:'BASE'}]},iteration:1});
   window.__push({type:'chunk',data:{choices:[{delta:{reasoning:'추론 내용입니다.\n'.repeat(100)}}]}});
  });
  const reasoning=await page.$eval('#stream-test .turn-reasoning-text',e=>({height:e.clientHeight,scroll:e.scrollHeight,overflow:getComputedStyle(e).overflowY,open:e.closest('details').open,weight:getComputedStyle(e.previousElementSibling.querySelector('strong')).fontWeight,font:getComputedStyle(e.previousElementSibling.querySelector('strong')).fontSize}));
  assert(reasoning.height<=200 && reasoning.scroll>reasoning.height && reasoning.overflow==='auto' && reasoning.open);
  assert(Number(reasoning.weight)>=700 && parseFloat(reasoning.font)>=13);
  await page.$eval('#stream-test .turn-reasoning-text',e=>e.scrollTop=20);
  await page.evaluate(()=>window.__push({type:'chunk',data:{choices:[{delta:{reasoning:'추가 추론'}}]}}));
  assert.equal(await page.$eval('#stream-test .turn-reasoning-text',e=>e.scrollTop),20);
  await page.evaluate(()=>window.__push({type:'chunk',data:{choices:[{delta:{tool_calls:[{index:0,id:'note-1',function:{name:'update_notes',arguments:'{"target":"client","edits":[{"old_text":"old note","new_text":"new'}}]}}]}}));
  await page.waitForFunction(()=>document.querySelector('#stream-test .note-added')?.textContent==='+new');
  assert((await page.$eval('#stream-test .turn-tool-status',e=>e.textContent)).includes('작성 중'));
  assert.equal(await page.$eval('#stream-test .note-deleted',e=>e.textContent),'-old note');
  assert.equal(await page.evaluate(()=>window.__attempt.steps[0].messages.length),1);
  await page.evaluate(()=>window.__toolBox=document.querySelector('#stream-test .turn-tool-call'));
  await new Promise(r=>setTimeout(r,160));
  await page.evaluate(()=>{
   window.__push({type:'chunk',data:{choices:[{delta:{tool_calls:[{index:0,function:{arguments:' note"}]}'}}]}}]}});
   window.__push({type:'tool_start',call_id:'note-1',name:'update_notes',arguments:JSON.stringify({target:'client',edits:[{old_text:'old note',new_text:'new note'}]})});
  });
  assert((await page.$eval('#stream-test .turn-tool-status',e=>e.textContent)).includes('적용 중'));
  assert(await page.evaluate(()=>window.__toolBox===document.querySelector('#stream-test .turn-tool-call')));
  await page.evaluate(()=>{
   window.__push({type:'version_event',version:'P2',section:'client_notes',action:'updated',data:{content:'committed actual',diff:['--- old','+++ new','-old note','+committed actual']}});
   window.__push({type:'tool_result',call_id:'note-1',name:'update_notes',model_content:'saved'});
  });
  assert.equal(await page.$eval('#stream-test .turn-tool-status',e=>e.textContent),'✓ 저장 완료');
  assert.equal(await page.$eval('#stream-test .note-added',e=>e.textContent),'+committed actual');
  assert(await page.$eval('#stream-test .turn-tool-preview-label',e=>e.hidden));
  assert.equal(await page.$eval('#stream-test .turn-tool-changes',e=>e.dataset.preview),'false');
  await page.screenshot({path:'/tmp/counseling_streaming_diff.png'});
  await page.evaluate(()=>{
   window.__push({type:'request',payload:{messages:[{role:'user',content:'BASE'}]},iteration:2});
   const args=JSON.stringify({target:'client',edits:[{old_text:'old note',new_text:'failed note'}]});
   window.__push({type:'chunk',data:{choices:[{delta:{tool_calls:[{index:0,id:'note-2',function:{name:'update_notes',arguments:args}}]}}]}});
   window.__push({type:'tool_start',call_id:'note-2',name:'update_notes',arguments:args});
   window.__push({type:'tool_result',call_id:'note-2',name:'update_notes',is_error:true,model_content:'failed'});
  });
  assert.equal(await page.$eval('#stream-test .turn-tool-call[data-status=error] .turn-tool-status',e=>e.textContent),'저장 실패');
  assert((await page.$eval('#stream-test .turn-tool-call[data-status=error] .turn-tool-preview-label',e=>e.textContent)).includes('적용되지 않은'));
  // Whole-note submissions get the same provisional display as edit lists.
  await page.evaluate(()=>{
   window.__push({type:'request',payload:{messages:[{role:'user',content:'BASE'}]},iteration:3});
   window.__push({type:'chunk',data:{choices:[{delta:{tool_calls:[{index:0,id:'note-3',function:{name:'update_notes',arguments:'{"target":"client","content":"new replacement'}}]}}]}});
  });
  assert.equal(await page.$eval('#stream-test .turn-tool-call[data-status=drafting] .note-added',e=>e.textContent),'+new replacement');
  await page.evaluate(()=>window.__push({type:'cancelled',error:'cancelled'}));
  assert((await page.$eval('#stream-test .turn-tool-call[data-status=interrupted] .turn-tool-status',e=>e.textContent)).includes('미적용'));
  // Explicitly enabled examples and their checkbox states survive reload.
  await page.click('#settings-tab');await page.click('#settings-dialogue-tab');
  await page.evaluate(()=>{for(const input of [...document.querySelectorAll('#dialogue-editor input')].slice(0,3)){input.checked=true;input.dispatchEvent(new Event('change',{bubbles:true}));}});
  assert.equal((await page.evaluate(()=>body())).scenario.dialogue.length,4);
  await page.evaluate(async()=>{const saved=await api('/api/paired/create',{...body(),counselor_version:'P2'});await Paired.load(saved.session_id);});
  await page.reload();await page.waitForFunction(()=>window.Paired?.hasSession && !window.Paired.running);
  assert.equal(await page.$$eval('#dialogue-editor input:checked',rows=>rows.length),3);
  await page.evaluate(()=>localStorage.removeItem('counseling-paired-session'));await page.reload();
  await page.waitForFunction(()=>document.querySelector('#tool-options input') && document.querySelector('#dialogue-editor input'));
  assert.equal(await page.$$eval('#dialogue-editor input:checked',rows=>rows.length),0);
  assert.deepEqual(errors,[]);console.log('PASS: empty initial history, opt-in examples, saved-session restore, bounded reasoning, streamed draft/apply/saved/error/cancel states and stable tool box');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
