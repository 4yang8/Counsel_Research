const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn(process.env.COUNSELING_PYTHON || 'python3',[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing']);let browser;fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  for(const version of ['Q3n','Q3sb','Q3x']){
   const context=await browser.createBrowserContext(),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1500,height:1000});
   await page.goto(url+'/lab');await page.waitForFunction(()=>document.querySelector('#experiment-version option[value=Q3x]') && !document.querySelector('#experiment-version').disabled && !document.body.classList.contains('ui-booting') && !document.body.classList.contains('pane-restoring'));
   await page.select('#experiment-version',version);
   await page.waitForFunction(()=>!document.querySelector('#q3-trajectory-editor').hidden && document.querySelector('#client-trajectory').value.includes('신뢰 형성'));
   await page.waitForSelector('#tool-enabled-stage_client_update',{timeout:10000});
   for(const name of ['stage_client_update','commit_client_utterance','retrieve_client_memory',...(version==='Q3n'?['select_client_note_sources']:[])])assert(await page.$eval('#tool-enabled-'+name,e=>e.checked && e.disabled));
   if(version==='Q3sb'){
    await page.select('#client-subagent-select','note_summarizer');
    await page.waitForFunction(()=>document.querySelector('#client-subagent-prompt').value.includes('# 기록 요약 담당'));
    await page.$eval('#client-subagent-prompt',e=>{e.value+='\nBROWSER_SUMMARY_SOURCE\n';e.dispatchEvent(new Event('input'));});
    await page.$eval('#client-subagent-prompt-save',e=>e.click());
    await page.waitForFunction(()=>document.querySelector('#client-subagent-prompt-file-status').textContent.includes('파일 저장 완료'));
    await page.waitForFunction(()=>document.querySelector('#client-subagent-prompt-user-template').value.includes('{{source_bundle}}'));
   }
   await page.evaluate(()=>{document.querySelector('#manual-counselor').checked=true;document.querySelector('#manual-counselor').dispatchEvent(new Event('change'));});
   await page.waitForSelector('#paired-next:not([hidden])',{visible:true});await page.click('#paired-next');
   await page.waitForFunction(()=>document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 1개') && !document.querySelector('#paired-manual-send').disabled,{timeout:30000}).catch(async error=>{console.error(await page.evaluate(()=>({status:document.querySelector('#paired-status').textContent,notice:document.querySelector('#notice').textContent,transcript:document.querySelector('#paired-transcript').textContent.slice(-5000)})));throw error;});
   const note=await page.evaluate(()=>api('/api/client_notes?paired=1&session_id='+Paired.sessionId));assert.equal(note.records.length,1);
   const rec=note.records[0];assert.equal(rec.experiment_condition,version);assert.equal(rec.trajectory_progress.movement,rec.trajectory_assessment.movement);
   assert.equal(rec.model_requests.length,version==='Q3n'?3:2);
   const transcript=await page.$eval('#paired-transcript',e=>e.textContent);
   assert(transcript.includes('발화 전 현재 궤적 판단'));
   if(version==='Q3sb'){
    assert(transcript.includes('기록 요약 결과'));assert(transcript.includes('Q3_SUMMARY_REASON'));
    assert(rec.subagent_requests.note_summarizer.messages[0].content.includes('BROWSER_SUMMARY_SOURCE'));
   }
   await page.click('#client-notes-tab');await page.waitForFunction(()=>document.querySelector('#client-notes-turn').options.length===2);
   const text=await page.$eval('#client-notes-body',e=>e.textContent);assert(text.includes('기록 방식'));assert(text.includes('reasoning과 출력 쌍 전체'));assert(text.includes('실제 공개 발화 원문'));
   if(version==='Q3n')assert(text.includes('선택한 원문'));
   if(version==='Q3sb')assert(text.includes('요약 담당 반환 원문'));
   await page.click('#session-notes-tab');await page.waitForFunction(()=>document.querySelector('#session-notes-body').textContent.includes('현재 궤적 진행'));
   assert.equal((await page.$$('#session-notes-body [data-actor=client] .session-note-box')).length,3);
   await page.$$eval('#paired-transcript .paired-speech-button',els=>els.findLast(e=>e.textContent.includes(' · 생성 ')).click());
   await page.click('#activation-tab');await page.waitForFunction(()=>document.querySelectorAll('.ac-block').length>0);
   await page.click('#workflow-tab');assert((await page.$$('.wf-node')).length>3);
   assert.equal((await page.$$('.wf-node[data-kind=recording]')).length,1);
   assert((await page.$eval('.wf-node[data-kind=recording]',e=>e.textContent)).includes('Python 기록'));
   if(version==='Q3sb')assert.equal((await page.$$('.wf-edge.automatic_summary')).length,1);
   await page.reload();await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
   assert.equal(await page.$eval('#experiment-version',e=>e.value),version);
   await page.click('#client-notes-tab');await page.waitForFunction(()=>document.querySelector('#client-notes-body').textContent.includes('기록 방식'));
   await page.screenshot({path:'/tmp/'+version+'_recording_ui.png'});
   await page.setViewport({width:390,height:900});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));
   assert.deepEqual(errors,[]);console.log('PASS '+version+': selection, generation, recording, sources, context, workflow, reload');
   await context.close();
  }
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
