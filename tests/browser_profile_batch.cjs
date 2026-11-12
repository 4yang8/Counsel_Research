const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),assert=require('node:assert/strict');
(async()=>{
 const script="from test_lab import fixture; from test_profile_batch import BatchModel; import time; s,m,t=fixture(model_handler=BatchModel); print('http://127.0.0.1:'+str(s.server_port),flush=True); time.sleep(120)";
 const fixture=spawn('python3',['-c',script],{env:{...process.env,PYTHONPATH:'src:tests'}});fixture.stderr.on('data',()=>{});let browser;
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1400,height:950});
  await page.goto(url+'/batch');await page.waitForFunction(()=>document.querySelectorAll('#profiles input').length===100&&!document.querySelector('#settings').disabled);
  assert.equal(await page.$eval('#intervention-setting',e=>getComputedStyle(e).display),'none');
  await page.select('#counselor','Q3p4_counsel');assert.notEqual(await page.$eval('#intervention-setting',e=>getComputedStyle(e).display),'none');
  await page.select('#counselor','Q3inner_counsel');
  assert.equal(await page.$eval('#intervention-setting',e=>getComputedStyle(e).display),'none');
  assert.ok(await page.$eval('#counselor-prompt',e=>e.value.includes('이전 선택의 의미')));
  await page.click('#select-20');assert.equal(await page.$$eval('#profiles input:checked',x=>x.length),20);
  await page.click('#select-none');await page.click('#profiles input');
  await page.$eval('#turns',e=>{e.value=2;e.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.click('#create');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('성공 2/2'),{timeout:30000});
  assert.equal(await page.$eval('#turn-progress',e=>e.value),100);
  assert.equal(await page.$eval('#rows',e=>e.children.length),2);
  assert.equal(await page.$eval('#error',e=>e.hidden),true);
  await page.click('#rows button');await page.waitForSelector('#detail[open]');
  assert.equal(await page.$eval('#detail-body',e=>e.querySelectorAll('.speech').length),4);
  assert.equal(await page.$eval('#detail-body',e=>e.querySelectorAll('details').length),2);
  await page.click('#close-detail');await page.reload();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('성공 2/2'));
  await page.screenshot({path:'/tmp/profile_batch_ui.png',fullPage:true});
  await page.setViewport({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false);
  await page.goto(url+'/lab');await page.waitForFunction(()=>document.querySelector('#experiment-version option[value=Q3inner_memory]'));
  await page.select('#experiment-version','Q3inner_memory');await page.waitForFunction(()=>!document.querySelector('#memory-access-settings').hidden);
  await page.waitForFunction(()=>document.querySelector('#subagent-prompt')?.value.includes('특성·배경') || [...document.querySelectorAll('textarea')].some(e=>e.value.includes('당신은 Q3 내담자의 특성')));
  assert.equal(await page.$eval('#experiment-version',e=>[...e.options].some(o=>o.value==='inner_memory_no')),true);
  assert.deepEqual(errors,[]);console.log('PASS: batch selection, parallel completion, progress, detail, reload, mobile and new version selectors');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
