const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn((process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing']);let browser;fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1500,height:1000});
  const versions=process.env.TEST_VERSION?[process.env.TEST_VERSION]:['Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F'];
  for(const version of versions){
   await page.goto(url+'/lab');await page.waitForFunction(()=>document.querySelector('#experiment-version option[value=Q2a_agentic_memory]') && !document.querySelector('#experiment-version').disabled);
   if(version!==versions[0])await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
   // Clear the last loaded conversation while retaining settings through the real UI.
   await page.waitForFunction(()=>!document.body.classList.contains('ui-booting'));
   await page.evaluate(()=>document.querySelector('#sessions-new').click());
   await page.waitForFunction(()=>!Paired.sessionId && !document.querySelector('#experiment-version').disabled);
   await page.select('#experiment-version',version);
   await page.waitForFunction(v=>document.querySelector('#experiment-version').value===v && document.querySelector(v==='Q2a_agentic_memory_F'?'#tool-enabled-update_client_notes':'#tool-enabled-update_client_states'),{},version);
   for(const name of (version==='Q2a_agentic_memory_F'?['stage_client_update','commit_client_utterance','update_client_notes']:['stage_client_update','commit_client_utterance','select_client_states','update_client_states']))assert(await page.$eval('#tool-enabled-'+name,e=>e.checked && e.disabled));
   const sources=await page.evaluate(async v=>Promise.all(['system','user','trait_router','trait_router_user'].map(kind=>api('/api/prompts/source?role=client&version='+v+'&kind='+kind))),version);
   assert(sources.every(s=>s.path.includes('/'+version+'/')));
   if(['Q2a_note_retrieve','Q2a_agentic_memory','Q2a_agentic_memory_F'].includes(version))assert(await page.$('#tool-enabled-retrieve_client_memory'));
   await page.evaluate(()=>{document.querySelector('#manual-counselor').checked=true;document.querySelector('#manual-counselor').dispatchEvent(new Event('change'));});
   await page.waitForSelector('#paired-next:not([hidden])',{visible:true});
   await page.click('#paired-next');await page.waitForFunction(()=>document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 1개') && !document.querySelector('#paired-manual-send').disabled,{timeout:30000});
   await page.click('#client-notes-tab');await page.waitForFunction(()=>document.querySelector('#client-notes-turn').options.length===2);
   assert((await page.$eval('#client-notes-path',e=>e.textContent)).includes('/'+version+'/'));
   const transcript=await page.$eval('#paired-transcript',e=>e.textContent);assert(transcript.includes(version==='Q2a_agentic_memory_F'?'Q2a_REASON_2':'Q2a_REASON_3'));assert(transcript.includes(version==='Q2a_agentic_memory_F'?'update_client_notes':'추가 · 상담사와의 관계적 유대'));
   if(['Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F'].includes(version))assert(transcript.includes('이번 대화 의도'));
   if(['Q2a_note_retrieve','Q2a_agentic_memory','Q2a_agentic_memory_F'].includes(version))assert(transcript.includes('근거 확인 종료 판단'));
   await page.$$eval('#paired-transcript .paired-speech-button',els=>els.findLast(e=>e.textContent.includes(' · 생성 ')).click());
   await page.click('#activation-tab');await page.waitForFunction(()=>document.querySelectorAll('.ac-block').length>0);assert(await page.$('.ac-message-group'));
   await page.click('#workflow-tab');assert((await page.$$('.wf-node')).length>4);
   await page.click('#session-notes-tab');await page.waitForFunction(v=>document.querySelector('#session-notes-body').textContent.includes(v==='Q2a_agentic_memory_F'?'# 내담자 노트':'## 현재 내면'),{},version);
   if(['Q2a_agentic_memory','Q2a_agentic_memory_F'].includes(version)){
    await page.$eval('#paired-manual-text',e=>{e.value='조금 준비할 시간을 갖고 이야기해도 괜찮아요.';e.dispatchEvent(new Event('input'));});
    await page.click('#paired-manual-send');
    await page.waitForFunction(()=>document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 2개') && !document.querySelector('#paired-manual-send').disabled,{timeout:30000});
    const note=await page.evaluate(()=>api('/api/client_notes?paired=1&session_id='+Paired.sessionId));
    const omittedId=[...note.records[1].notes_before.matchAll(/^- \[(n[a-f0-9]+)\]/gm)].map(m=>m[1]).find(id=>!note.records[1].note_selection.selected_ids.includes(id));assert(omittedId);
    await page.click('#activation-tab');
    await page.$$eval('#paired-transcript .paired-speech',els=>els.at(-1).querySelectorAll('.turn-model-call .turn-context-select')[1].click());
    await page.waitForFunction(id=>document.querySelector('.ac-block[data-agent="main"][data-component="note.item.'+id+'"][data-active="false"]'),{},omittedId);
    await page.$$eval('#paired-transcript .paired-speech',(els,v)=>els.at(-1).querySelectorAll('.turn-model-call .turn-context-select')[3].click(),version);
    await page.waitForFunction(id=>document.querySelector('.ac-block[data-agent="main"][data-component="note.item.'+id+'"][data-active="true"]'),{},omittedId);
   }
   await page.reload();await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
   assert.equal(await page.$eval('#experiment-version',e=>e.value),version);
   console.log('PASS browser',version);
  }
  await page.screenshot({path:'/tmp/memory_experiments_ui.png'});await page.setViewport({width:390,height:900});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));
  assert.deepEqual(errors,[]);
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
