const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn((process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing']);let browser;fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1500,height:1000});
  await page.goto(url+'/lab');await page.waitForFunction(()=>document.querySelector('#experiment-version option[value=Q3a]') && !document.querySelector('#experiment-version').disabled && !document.body.classList.contains('ui-booting'));
  await page.select('#experiment-version','Q3a');
  await page.waitForFunction(()=>!document.querySelector('#q3-trajectory-editor').hidden && document.querySelector('#client-trajectory').value.includes('신뢰 형성'));
  for(const name of ['stage_client_update','commit_client_utterance','update_client_notes','retrieve_client_memory','update_trajectory_progress','update_turn_plan'])assert(await page.$eval('#tool-enabled-'+name,e=>e.checked && e.disabled));
  await page.$eval('#client-trajectory',e=>{e.value+='\n### UI 편집 확인\nBROWSER_TRAJECTORY\n';e.dispatchEvent(new Event('input'));});
  await page.$eval('#client-trajectory-save',e=>e.click());
  await page.waitForFunction(()=>document.querySelector('#client-trajectory-file-status').textContent.includes('파일 저장 완료'));
  await page.$eval('#client-trajectory',e=>{e.value+=' UNSAVED';e.dispatchEvent(new Event('input'));});
  await page.$eval('#client-trajectory-undo',e=>e.click());
  await page.waitForFunction(()=>!document.querySelector('#client-trajectory').value.includes('UNSAVED'));
  await page.evaluate(()=>{document.querySelector('#manual-counselor').checked=true;document.querySelector('#manual-counselor').dispatchEvent(new Event('change'));});
  await page.waitForSelector('#paired-next:not([hidden])',{visible:true});await page.click('#paired-next');
  await page.waitForFunction(()=>document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 1개') && !document.querySelector('#paired-manual-send').disabled,{timeout:30000});
  const transcript=await page.$eval('#paired-transcript',e=>e.textContent);
  assert(transcript.includes('update_trajectory_progress'));assert(transcript.includes('Q2a_REASON_3'));assert(transcript.includes('이번 궤적 계획'));assert(transcript.includes('update_turn_plan'));assert(transcript.includes('로컬 작업 계획'));assert((await page.$$('.turn-plan-step[data-status=completed]')).length>0);
  const note=await page.evaluate(()=>api('/api/client_notes?paired=1&session_id='+Paired.sessionId));assert.equal(note.records.length,1);assert(note.records[0].trajectory_source.includes('BROWSER_TRAJECTORY'));assert.equal(note.records[0].local_plan_history.length,3);assert.equal(note.records[0].local_plan.steps[0].status,'completed');
  await page.click('#session-notes-tab');await page.waitForFunction(()=>document.querySelector('#session-notes-body').textContent.includes('현재 궤적 진행'));
  assert((await page.$$('#session-notes-body [data-actor=client] .session-note-box')).length===4);
  await page.click('#client-notes-tab');await page.waitForFunction(()=>document.querySelector('#client-notes-turn').options.length===2);
  assert((await page.$eval('#client-notes-body',e=>e.textContent)).includes('궤적 진행'));
  await page.select('#client-notes-mode','diff');assert((await page.$eval('#client-notes-body',e=>e.textContent)).includes('이번 잠정 계획'));assert((await page.$eval('#client-notes-body',e=>e.textContent)).includes('계획 변경 3'));
  await page.$$eval('#paired-transcript .paired-speech-button',els=>els.findLast(e=>e.textContent.includes(' · 생성 ')).click());
  await page.click('#activation-tab');await page.waitForFunction(()=>document.querySelectorAll('.ac-block').length>0);
  assert((await page.$eval('#activation-view',e=>e.textContent).catch(()=>page.$eval('.results-scroll',e=>e.textContent))).includes('궤적'));
  await page.click('#workflow-tab');assert((await page.$$('.wf-node')).length>4);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
  assert.equal(await page.$eval('#experiment-version',e=>e.value),'Q3a');
  await page.click('#session-notes-tab');await page.waitForFunction(()=>document.querySelector('#session-notes-body').textContent.includes('현재 궤적 진행'));
  await page.screenshot({path:'/tmp/q3a_ui.png'});await page.setViewport({width:390,height:900});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));
  assert.deepEqual(errors,[]);console.log('PASS Q3a browser: editing, undo, generation, notes, context, workflow, reload, mobile');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
