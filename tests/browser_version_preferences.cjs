const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn(process.env.GEMMA_TEST_PYTHON || (process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing']);let browser;fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.goto(url+'/lab');
  const ready=()=>page.waitForFunction(()=>!document.querySelector('#experiment-version').disabled && document.querySelector('#counselor-version option[value=P3a]') && document.querySelector('#counselor-system-prompt').value);
  const values=()=>page.evaluate(()=>[document.querySelector('#experiment-version').value,document.querySelector('#counselor-version').value]);
  await ready();assert.equal(await page.$eval('#model-select',el=>el.value),'Qwen/Qwen3.8-27B-FP8');await page.select('#experiment-version','Q2a_trait_background');await page.select('#counselor-version','P3a');
  await page.reload();await ready();assert.deepEqual(await values(),['Q2a_trait_background','P3a']);
  await page.click('#sessions-new');await ready();assert.deepEqual(await values(),['Q2a_trait_background','P3a']);
  await page.evaluate(()=>{document.querySelector('#manual-counselor').checked=true;document.querySelector('#manual-counselor').dispatchEvent(new Event('change'));});
  await page.click('#paired-next');
  await page.waitForFunction(()=>document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 1개') && !document.querySelector('#paired-manual-send').disabled,{timeout:30000});
  const old=await page.evaluate(()=>Paired.sessionId);assert(old);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));assert.deepEqual(await values(),['Q2a_trait_background','P3a']);
  await page.evaluate(()=>{localStorage.setItem('counseling-last-client-version','state_summary');localStorage.setItem('counseling-last-counselor-version','basic');});
  await page.click('#sessions-new');await ready();assert.deepEqual(await values(),['Q2a_trait_background','P3a']);
  assert.equal(await page.$eval('#model-select',el=>el.value),'Qwen/Qwen3.8-27B-FP8');
  await page.select('#experiment-version','Q2a');await page.select('#counselor-version','P2');
  await page.evaluate(id=>Paired.load(id),old);assert.deepEqual(await values(),['Q2a_trait_background','P3a']);
  assert.deepEqual(await page.evaluate(()=>['client','counselor'].map(r=>localStorage.getItem('counseling-last-'+r+'-version'))),['Q2a_trait_background','P3a']);
  await page.click('#sessions-new');await ready();assert.deepEqual(await values(),['Q2a_trait_background','P3a']);
  await page.select('#experiment-version','Q2a');await page.select('#counselor-version','P2');
  await page.reload();await ready();assert.deepEqual(await values(),['Q2a','P2']);
  await page.evaluate(()=>{localStorage.removeItem('counseling-version-preferences-format');localStorage.removeItem('counseling-paired-session');localStorage.setItem('counseling-last-client-version','state_summary');localStorage.setItem('counseling-last-counselor-version','basic');});
  await page.reload();await ready();assert.deepEqual(await values(),['Q2a_trait_background','P3a']);
  await page.evaluate(()=>localStorage.clear());await page.reload();await ready();
  assert.deepEqual(await values(),['Q2a_trait_background','P3a']);
  assert.equal(await page.$eval('#model-select',el=>el.value),'Qwen/Qwen3.8-27B-FP8');
  await page.evaluate(()=>{localStorage.setItem('counseling-last-client-version','deleted');localStorage.setItem('counseling-last-counselor-version','deleted');});
  await page.reload();await ready();const defaults=await page.evaluate(async()=>[(await api('/api/versions')).default,(await api('/api/paired/counselor')).default]);
  assert.deepEqual(await values(),defaults);assert.deepEqual(errors,[]);
  console.log('PASS: version preferences survive reload/new conversation, new conversation preserves displayed versions despite stale preferences, first-use migrates latest session, Qwen FP8 default, invalid versions fall back');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
