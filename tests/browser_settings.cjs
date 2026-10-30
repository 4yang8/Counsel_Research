const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn(process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3',[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing']);
 let browser;fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',data=>resolve(data.toString().trim()));fixture.once('exit',code=>reject(Error('fixture '+code)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const ready=()=>page.waitForFunction(()=>window.LabPane?.ready,{timeout:40000});
  const read=()=>page.evaluate(()=>({
   model:document.querySelector('#model-select').value,profile:document.querySelector('#quick-profile').value,
   profileId:JSON.parse(document.querySelector('#edit-profile').value).profile_id,
   temperature:document.querySelector('#temperature').value,topP:document.querySelector('#top_p').value,
   tokens:document.querySelector('#max_tokens').value,seed:document.querySelector('#seed').value,
   thinking:document.querySelector('#thinking-mode').value,endpoint:TurnConnection.value(),
   manual:document.querySelector('#manual-counselor').checked,automatic:document.querySelector('#paired-mode').checked,
   limit:document.querySelector('#paired-limit').value,budget:document.querySelector('#counselor-main-budget').value,
   budgetEnabled:document.querySelector('#counselor-main-budget-enabled').checked,
   budgetDisabled:document.querySelector('#counselor-main-budget').disabled
  }));
  await page.goto(url+'/lab');await ready();
  const initial=await read();assert.equal(initial.model,'Qwen/Qwen3.8-Flash-Next-FP8');assert.equal(initial.tokens,'4096');assert.equal(initial.profile,'011_guarded');
  await page.select('#quick-client','Q3b');await page.waitForFunction(()=>currentVersion==='Q3b' && !document.querySelector('#experiment-version').disabled);
  await page.select('#quick-counselor','P3a');await page.select('#quick-profile','002');
  await page.select('#model-select','Qwen/Qwen3.8-27B-FP8');await page.select('#thinking-mode','off');
  await page.evaluate(()=>{
   const set=(id,value)=>{const el=document.getElementById(id);if(el.type==='checkbox')el.checked=value;else el.value=value;el.dispatchEvent(new Event(el.tagName==='SELECT' || el.type==='checkbox'?'change':'input',{bubbles:true}));};
   for(const [id,value] of Object.entries({temperature:'0.35',top_p:'0.8',max_tokens:'8192',seed:'123','counselor-main-budget-enabled':true,'counselor-main-budget':'640','manual-counselor':true,'paired-mode':true,'paired-limit':'12'}))set(id,value);
   set('turn-endpoint-mode','lan');set('turn-endpoint-octet','86');set('turn-endpoint-port','8002');
  });
  const chosen=await read();assert.equal(chosen.budgetDisabled,false);
  // Reload immediately, before the debounced save can run.
  await page.reload();await ready();assert.deepEqual(await read(),chosen);
  assert.deepEqual(await page.evaluate(()=>[currentVersion,document.querySelector('#counselor-version').value]),['Q3b','P3a']);
  // Create a manual session without making a model request.
  await page.evaluate(async()=>{
   const el=document.querySelector('#manual-client');el.checked=true;el.dispatchEvent(new Event('change',{bubbles:true}));
   document.querySelector('#paired-manual-text').value='설정 보존 확인';document.querySelector('#paired-manual-text').dispatchEvent(new Event('input',{bubbles:true}));
  });
  await page.click('#paired-manual-send');await page.waitForFunction(()=>Paired.hasSession && !Paired.running && !document.querySelector('#paired-manual-send').disabled);
  const sessionId=await page.evaluate(()=>Paired.sessionId);
  await page.evaluate(()=>{
   const mode=document.querySelector('#paired-mode');mode.checked=true;mode.dispatchEvent(new Event('change',{bubbles:true}));
   const limit=document.querySelector('#paired-limit');limit.value='17';limit.dispatchEvent(new Event('input',{bubbles:true}));
   const port=document.querySelector('#turn-endpoint-port');port.value='8003';port.dispatchEvent(new Event('input',{bubbles:true}));
   const text=document.querySelector('#paired-manual-text');text.value='다음 발화 초안';text.dispatchEvent(new Event('input',{bubbles:true}));
  });
  await page.reload();await ready();
  assert.equal(await page.evaluate(()=>Paired.sessionId),sessionId);
  const active=await read();assert.equal(active.automatic,true);assert.equal(active.limit,'17');assert.equal(active.endpoint,'http://192.168.0.86:8003/v1');
  assert.equal(active.model,chosen.model);assert.equal(active.tokens,chosen.tokens);
  assert.equal(await page.$eval('#paired-manual-text',el=>el.value),'다음 발화 초안');
  await page.click('#sessions-new');await page.waitForFunction(()=>!Paired.hasSession && !document.querySelector('#experiment-version').disabled);
  assert.equal(await page.$eval('#model-select',el=>el.value),chosen.model);
  assert.deepEqual(errors,[]);
  console.log('PASS settings: Flash Next defaults, immediate reload, standalone options, active-session controls, composer, new-conversation model.');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
