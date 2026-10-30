const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn(process.env.COUNSELING_PYTHON || 'python3',[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing','--p1-slow']);let browser,page;fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1600,height:1000});
  await page.goto(url);
  const frameAt=async i=>(await page.$$('#experiment-panes iframe'))[i].contentFrame();
  const ready=async i=>{const f=await frameAt(i);await f.waitForFunction(()=>LabPane.ready,{timeout:45000});return f;};
  const set=async(id,value)=>page.$eval('#'+id,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},String(value));
  const finished=()=>page.waitForFunction(()=>!document.querySelector('#master-start').disabled && document.querySelector('#master-status').textContent.includes('실행 종료'),{timeout:90000});
  const sessions=async()=>Promise.all([0,1,2].map(async i=>(await frameAt(i)).evaluate(async()=>Paired.sessionId?api('/api/paired/session?session_id='+Paired.sessionId):null)));
  const first=await ready(0);await first.select('#quick-client','Q3x');await first.select('#quick-counselor','basic');
  await first.select('#quick-profile','011_guarded');await first.waitForFunction(()=>JSON.parse(document.querySelector('#edit-profile').value).profile_id==='gold_011');
  for(const [i,version] of [[1,'Q3n'],[2,'Q3sb']]){await page.click('#add-experiment');const f=await ready(i);await f.select('#quick-client',version);if(i===1)await f.select('#quick-profile','011_open');}
  await page.evaluate(()=>ExperimentWorkspace.activate(ExperimentWorkspace.tabs()[0].id));
  await page.$eval('#master-controller',e=>e.open=true);await page.click('#master-load');
  await set('master-temperature','0.35');await set('master-seed','123');await set('master-count','2');
  await page.waitForFunction(()=>document.querySelectorAll('.master-target').length===3 && !document.querySelector('#master-start').disabled);
  assert((await page.$eval('#master-targets',e=>e.textContent)).includes('조건 차이'));
  const prompts=await Promise.all([0,1,2].map(async i=>(await frameAt(i)).$eval('#system_prompt',e=>e.value)));
  // Applying to drafts should not create sessions and must preserve each version prompt.
  await page.click('#master-apply');await page.waitForFunction(()=>document.querySelector('#master-status').textContent.includes('3개 탭에 공통 조건을 적용'));
  assert.deepEqual(await sessions(),[null,null,null]);
  for(let i=0;i<3;i++){const f=await frameAt(i);assert.equal(await f.$eval('#system_prompt',e=>e.value),prompts[i]);assert.equal(await f.$eval('#temperature',e=>e.value),'0.35');assert.equal(await f.$eval('#seed',e=>e.value),'123');}
  await page.click('#master-start');await first.waitForFunction(()=>Paired.running && Paired.sessionId);
  assert.equal(await (await frameAt(1)).evaluate(()=>Paired.sessionId),null);
  assert(await (await frameAt(1)).evaluate(()=>LabPane.status().reserved));
  await finished();let saved=await sessions();assert(saved.every(s=>s.steps.length===2));assert.equal(new Set(saved.map(s=>s.session_id)).size,3);
  const baseline=saved[0].settings;
  for(let i=0;i<3;i++){
   const s=saved[i];assert.equal(s.settings.system_prompt,prompts[i]);assert.equal(s.settings.generation.temperature,.35);assert.equal(s.settings.generation.seed,123);
   assert.deepEqual(s.settings.scenario,baseline.scenario);assert.deepEqual(s.settings.messages,baseline.messages);
   assert.equal(s.settings.comparison.schedule,'sequential');assert(s.steps.every(step=>step.comparison.batch_id===s.settings.comparison.batch_id));
   assert(!(await (await frameAt(i)).evaluate(()=>LabPane.status().reserved)));
  }
  const old=saved.map(s=>s.session_id);
  // Invalid input is rejected before any existing view/session is replaced.
  await set('master-count','0');await page.click('#master-start');await page.waitForFunction(()=>document.querySelector('#master-status').textContent.includes('1~100'));
  assert.deepEqual((await sessions()).map(s=>s.session_id),old);
  await set('master-count','2');
  await page.click('#master-apply');await page.waitForFunction(()=>document.querySelector('#master-status').textContent.includes('기존 대화에는 초기 조건'));
  assert.deepEqual((await sessions()).map(s=>s.session_id),old);
  // Continuation respects frozen generation, even when the master options were edited.
  await set('master-temperature','.9');await set('master-count','1');await page.click('#master-continue');await finished();saved=await sessions();
  assert(saved.every(s=>s.steps.length===3 && s.settings.generation.temperature===.35));assert.deepEqual(saved.map(s=>s.session_id),old);
  // A pause finishes the current utterance; resume fills the original target count.
  await set('master-count','3');await page.click('#master-continue');await first.waitForFunction(()=>Paired.running);
  await page.click('#master-pause');await first.waitForFunction(()=>!Paired.running,{timeout:45000});
  await page.waitForFunction(()=>document.querySelector('#master-status').textContent.includes('일시정지했습니다'));
  assert.equal((await sessions())[1].steps.length,3);
  await page.click('#master-resume');await finished();saved=await sessions();assert(saved.every(s=>s.steps.length===6));
  // Parallel fresh execution replaces views only; old sessions remain readable.
  await page.select('#master-schedule','parallel');await set('master-count','2');await page.click('#master-start');
  await page.waitForFunction(()=>ExperimentWorkspace.tabs().every(t=>t.api.status().busy),{timeout:20000});
  await finished();saved=await sessions();assert(saved.every(s=>s.steps.length===2));assert(saved.every((s,i)=>s.session_id!==old[i]));
  for(const id of old)assert.equal((await page.evaluate(async id=>(await(await fetch('/api/paired/session?session_id='+id)).json()).steps.length,id)),6);
  // Select one tab, stop its in-flight turn, and keep the others untouched.
  await page.click('#master-select-none');await page.$eval('.master-target input',e=>{e.checked=true;e.dispatchEvent(new Event('change'));});
  const untouched=saved.slice(1).map(s=>s.session_id);await set('master-count','10');await page.click('#master-continue');await first.waitForFunction(()=>Paired.running);
  await page.click('#master-stop');await page.waitForFunction(()=>!document.querySelector('#master-start').disabled && document.querySelector('#master-status').textContent.includes('중단'),{timeout:45000});
  assert.deepEqual((await sessions()).slice(1).map(s=>s.session_id),untouched);assert((await sessions()).slice(1).every(s=>s.steps.length===2));
  // Persist controls and results, without automatically restarting generation.
  await page.screenshot({path:'/tmp/master_controller_desktop.png'});await page.reload();for(let i=0;i<3;i++)await ready(i);
  assert.equal(await page.$eval('#master-count',e=>e.value),'10');assert.equal(await page.$eval('#master-schedule',e=>e.value),'parallel');
  assert.equal(await page.$$eval('.master-target input:checked',els=>els.length),1);
  assert((await Promise.all([0,1,2].map(async i=>(await frameAt(i)).evaluate(()=>Paired.running)))).every(v=>!v));
  // A failed pane is reported while the remaining sequential jobs still complete.
  await page.click('#master-select-all');await set('master-count','1');await page.select('#master-schedule','sequential');
  let failOnce=true;const intercept=async req=>{
   if(failOnce && req.url().endsWith('/api/paired/step')){failOnce=false;return req.respond({status:200,contentType:'application/x-ndjson',body:JSON.stringify({type:'role_event',event:{type:'error',error:'MASTER_TEST_FAILURE'}})+'\n'});}
   return req.continue();
  };
  await page.setRequestInterception(true);page.on('request',intercept);await page.click('#master-start');await finished();
  assert((await page.$eval('#master-status',e=>e.textContent)).includes('오류 1개'));
  assert.equal(await page.$$eval('.master-target[data-status=completed]',els=>els.length),2);
  assert.equal(await page.$$eval('.master-target[data-status=error]',els=>els.length),1);
  page.off('request',intercept);await page.setRequestInterception(false);
  // Stop cancels queued jobs as well as the active job.
  await set('master-count','10');await page.click('#master-start');const current=await frameAt(0);await current.waitForFunction(()=>Paired.running && Paired.sessionId);
  assert(await current.evaluate(()=>document.querySelector('.paired-composer').inert && !document.querySelector('.lab-shell').inert));
  await page.click('#master-stop');await page.waitForFunction(()=>!document.querySelector('#master-start').disabled && document.querySelector('#master-status').textContent.includes('중단'),{timeout:45000});
  assert.equal(await (await frameAt(1)).evaluate(()=>Paired.sessionId),null);assert.equal(await (await frameAt(2)).evaluate(()=>Paired.sessionId),null);
  await page.setViewport({width:390,height:900});await page.screenshot({path:'/tmp/master_controller_mobile.png'});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));
  assert.deepEqual(errors,[]);console.log('PASS master: shared conditions, prompt isolation, sequential/parallel, pause/resume, stop, continuation, history, reload, mobile');
 }catch(error){if(page){console.error(await page.evaluate(()=>({status:document.querySelector('#master-status')?.textContent,targets:document.querySelector('#master-targets')?.textContent})).catch(()=>({})));await page.screenshot({path:'/tmp/master_controller_failure.png'}).catch(()=>{});}throw error;}
 finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
