const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn((process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing','--p1-slow']);let browser;
 fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1600,height:1000});
  await page.goto(url);
  const frameAt=async i=>{const frames=await page.$$('#experiment-panes iframe');return frames[i].contentFrame();};
  const ready=async i=>{const frame=await frameAt(i);await frame.waitForFunction(()=>window.LabPane?.ready,{timeout:40000});return frame;};
  const activate=async i=>{await page.$$eval('[role=tab]',(els,i)=>els[i].click(),i);return ready(i);};
  let first=await ready(0);
  assert(await page.$eval('#experiment-tabs-row',e=>e.hidden));
  assert((await page.$eval('#experiment-tabs-header',e=>e.offsetHeight))<40);
  await first.select('#quick-client','Q3');await first.select('#quick-counselor','P1');await first.select('#quick-profile','011_guarded');
  await first.waitForFunction(()=>currentVersion==='Q3' && JSON.parse(document.querySelector('#edit-profile').value).profile_id==='gold_011');
  assert.equal(await first.$eval('#counselor-version',e=>e.value),'P1');
  await first.select('#experiment-version','Q3a');await first.waitForFunction(()=>document.querySelector('#quick-client').value==='Q3a');
  await first.select('#experiment-profile','011_open');await first.waitForFunction(()=>document.querySelector('#quick-profile').value==='011_open');
  await first.select('#quick-profile','011_guarded');
  await first.evaluate(()=>{document.querySelector('#temperature').value='0.35';document.querySelector('#temperature').dispatchEvent(new Event('input',{bubbles:true}));});
  // All six combinations remain live, rather than reusing one global session.
  const expected=[['Q3a','011_guarded'],['Q3a','011_open'],['Q3','011_guarded'],['Q3','011_open'],['Q3b','011_guarded'],['Q3b','011_open']];
  for(let i=1;i<6;i++){
   await page.click('#add-experiment');const frame=await ready(i);
   assert.equal(await frame.$eval('#temperature',e=>e.value),'0.35');
   await frame.select('#quick-client',expected[i][0]);await frame.select('#quick-profile',expected[i][1]);
   await frame.waitForFunction((version)=>currentVersion===version,{},expected[i][0]);
  }
  assert.equal(await page.$$eval('[role=tab]',els=>els.length),6);
  await page.screenshot({path:'/tmp/counseling_workspace_settings.png'});
  assert(!(await page.$eval('#experiment-tabs-row',e=>e.hidden)));
  for(let i=0;i<6;i++){
   const frame=await activate(i);
   assert.deepEqual(await frame.evaluate(()=>[document.querySelector('#experiment-version').value,document.querySelector('#experiment-profile').value]),expected[i]);
  }
  first=await activate(0);
  // Start a delayed counselor request, then leave the pane while it streams.
  await first.evaluate(()=>{const e=document.querySelector('#manual-client');e.checked=true;e.dispatchEvent(new Event('change',{bubbles:true}));});
  await first.type('#paired-manual-text','첫 번째 독립 실험입니다.');await first.click('#paired-manual-send');
  await first.waitForFunction(()=>Paired.running && Paired.sessionId);
  const firstId=await first.evaluate(()=>Paired.sessionId);
  const second=await activate(1);
  assert.equal(await second.evaluate(()=>Paired.sessionId),null);
  assert(await page.$eval('.experiment-tab .tab-close',e=>e.disabled));
  await second.evaluate(()=>{const e=document.querySelector('#manual-counselor');e.checked=true;e.dispatchEvent(new Event('change',{bubbles:true}));});
  await second.click('#paired-next');await second.waitForFunction(()=>Paired.sessionId);
  assert.notEqual(await second.evaluate(()=>Paired.sessionId),firstId);
  await first.waitForFunction(()=>!Paired.running && document.querySelector('#paired-status').textContent.includes('직접 입력 대기'),{timeout:60000});
  await second.waitForFunction(()=>!Paired.running,{timeout:60000});
  const actual=await first.evaluate(()=>api('/api/paired/session?session_id='+Paired.sessionId));
  assert(actual.public_dialogue.some(x=>x.content.includes('첫 번째 독립 실험')));
  const other=await second.evaluate(()=>api('/api/paired/session?session_id='+Paired.sessionId));
  assert(!other.public_dialogue.some(x=>x.content.includes('첫 번째 독립 실험')));
  await activate(0);assert(await first.$eval('#quick-client',e=>e.disabled));
  // New-conversation retains this pane's chosen versions, not another pane's.
  await activate(5);const draft=await ready(5);
  await draft.evaluate(()=>{document.querySelector('#paired-manual-text').value='미전송 초안';document.querySelector('#paired-manual-text').dispatchEvent(new Event('input',{bubbles:true}));LabPane.save();});
  await page.click('#experiment-tabs-toggle');assert(await page.$eval('#experiment-tabs-row',e=>e.hidden));
  await page.reload();
  assert.equal(await page.$$eval('#experiment-panes iframe',els=>els.length),6);
  for(let i=0;i<6;i++)await ready(i);
  assert(await page.$eval('#experiment-tabs-row',e=>e.hidden));
  const restored=await ready(5);assert.equal(await restored.$eval('#paired-manual-text',e=>e.value),'미전송 초안');
  for(let i=0;i<6;i++)assert.deepEqual(await (await frameAt(i)).evaluate(()=>[document.querySelector('#quick-client').value,document.querySelector('#quick-profile').value]),expected[i]);
  assert.equal(await (await frameAt(0)).evaluate(()=>Paired.sessionId),firstId);
  await page.click('#experiment-tabs-toggle');await activate(0);
  await page.$$eval('.tab-close',els=>els[0].click());
  assert.equal(await page.$$eval('#experiment-panes iframe',els=>els.length),5);
  assert.equal((await page.evaluate(async id=>(await (await fetch('/api/paired/session?session_id='+id)).json()).session_id,firstId)),firstId);
  await page.screenshot({path:'/tmp/counseling_workspace_desktop.png'});
  await page.setViewport({width:390,height:900});await new Promise(r=>setTimeout(r,200));
  assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));
  assert(!(await (await ready(0)).evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));
  await page.screenshot({path:'/tmp/counseling_workspace_mobile.png'});
  assert.deepEqual(errors,[]);
  console.log('PASS workspace: 6 independent panes, quick settings two-way sync, background generation, reload, collapse, close preserves sessions, mobile');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
