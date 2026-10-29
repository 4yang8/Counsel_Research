const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn((process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing']);let browser;fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1500,height:1000});
  await page.goto(url+'/lab');await page.waitForFunction(()=>[...document.querySelector('#experiment-profile').options].some(o=>o.value==='011_open') && !document.querySelector('#experiment-version').disabled && !document.body.classList.contains('ui-booting'));
  console.log('ready');
  await page.select('#experiment-version','Q3');
  for(const [choice,id] of [['011_guarded','gold_011'],['011_open','trust_011_open']]){
   await page.select('#experiment-profile',choice);
   await page.waitForFunction(id=>JSON.parse(document.querySelector('#edit-profile').value).profile_id===id,{},id);
   const scenario=await page.evaluate(()=>selectedScenario());assert.equal(scenario.profile.profile_id,id);assert.deepEqual(scenario.dialogue,[]);assert(!scenario.dialogue_turns);
   assert.equal(await page.$$eval('#dialogue-editor .dialogue-turn',e=>e.length),0);
   await page.$eval('#profile-library-tab-'+choice,e=>e.click());
   assert.equal(await page.$eval('#profile-library-body',e=>e.dataset.profileId),id);
  }
  const source=await page.$eval('#profile-library-source',e=>e.textContent);assert(source.includes('trust_contrast/profile_011_open.json'));
  await page.evaluate(()=>{document.querySelector('#manual-counselor').checked=true;document.querySelector('#manual-counselor').dispatchEvent(new Event('change'));});
  await page.waitForSelector('#paired-next:not([hidden])',{visible:true});await page.click('#paired-next');
  await page.waitForFunction(()=>document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 1개') && !document.querySelector('#paired-manual-send').disabled,{timeout:30000});
  const session=await page.evaluate(()=>api('/api/paired/session?session_id='+Paired.sessionId));
  assert.equal(session.settings.scenario.profile.profile_id,'trust_011_open');assert.equal(session.settings.scenario.id,'trust_011_open');
  const note=await page.evaluate(()=>api('/api/client_notes?paired=1&session_id='+Paired.sessionId));
  assert(JSON.stringify(note.records[0].router_request).includes('기본 신뢰'));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
  assert.equal(await page.$eval('#experiment-profile',e=>e.value),'011_open');
  assert.equal(await page.$eval('#history-select',e=>e.value),'trust_011_open');
  assert.equal(await page.$eval('#edit-profile',e=>JSON.parse(e.value).profile_id),'trust_011_open');
  assert.deepEqual(errors,[]);console.log('PASS trust contrast: both profiles select/inspect, empty history, Q3 generation input, session restore');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
