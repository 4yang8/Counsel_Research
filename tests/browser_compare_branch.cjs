const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),assert=require('node:assert/strict');
(async()=>{
 const script="from test_lab import fixture; from test_compare_branch import CompareModel; import time; s,m,t=fixture(model_handler=CompareModel); print('http://127.0.0.1:'+str(s.server_port),flush=True); time.sleep(120)";
 const fixture=spawn(process.env.COUNSELING_PYTHON||'python3',['-c',script],{env:{...process.env,PYTHONPATH:'src:tests'}});fixture.stderr.on('data',()=>{});let browser;
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1400,height:950});
  const shown=id=>page.$eval(id,e=>getComputedStyle(e).display!=='none');
  await page.goto(url+'/batch');await page.waitForFunction(()=>document.querySelectorAll('#profiles input').length===100&&!document.querySelector('#settings').disabled);
  // Default mode: the two-condition controls, no fork setting.
  assert.equal(await shown('#version-setting'),true);assert.equal(await shown('#fork-setting'),false);
  assert.equal(await page.$eval('#counselor',e=>e.value),'P3rs');
  assert.deepEqual(await page.$$eval('#mode option',o=>o.map(x=>x.value)),['versions','compare:Q3compare_memory','compare:Q3compare_memory_private_position',...['Q3cm','Q3cm_split','Q3cm_temporal','Q3cm_state','Q3cm_tools','Q3cm_review','Q3cm_full'].map(v=>'compare:'+v)]);
  for(const v of ['P3rs_sub_gate','P3rs_observe','P3rs_ledger','P3rs_tools','P3rs_dual','P3rs_hypothesis','P3rs_review','P3rs_full'])assert(await page.$(`#counselor option[value="${v}"]`));
  assert.deepEqual([await page.$eval('#concurrency',e=>e.value),await page.$eval('#retries',e=>e.value)],['100','3']);
  // Server choice: the current value, or 192.168.0.n on port 8002.
  assert.equal(await shown('#endpoint'),true);assert.equal(await shown('#endpoint-octet'),false);
  await page.select('#endpoint-mode','lan');assert.equal(await shown('#endpoint'),false);
  await page.$eval('#endpoint-octet',e=>{e.value=57;e.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.equal(await page.$eval('#endpoint-preview',e=>e.textContent),'http://192.168.0.57:8002/v1');
  await page.select('#endpoint-mode','current');
  await page.$eval('#concurrency',e=>{e.value=1;});
  await page.select('#mode','compare:Q3compare_memory');
  assert.equal(await shown('#version-setting'),false);assert.equal(await shown('#fork-setting'),true);
  assert.equal(await page.$eval('#counselor',e=>e.value),'P3rs');
  await page.select('#counselor','P3q');
  assert.ok(await page.$eval('#counselor-prompt',e=>e.value.includes('되짚기 표시')));
  assert.equal(await page.$eval('#fork-turn',e=>e.value),'4');
  await page.click('#select-none');await page.click('#profiles input');
  for(const [id,value] of [['#turns',4],['#fork-turn',3]])await page.$eval(id,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},value);
  assert.ok(await page.$eval('#estimate',e=>e.textContent.includes('공통 2턴')&&e.textContent.includes('갈래마다 2턴')&&e.textContent.includes('총 6개')));
  await page.click('#create');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('성공 3/3'),{timeout:30000});
  assert.ok(await page.$eval('#status',e=>e.textContent.includes('3턴부터 분기')));
  assert.equal(await page.$eval('#turn-progress',e=>e.value),100);
  const rows=await page.$$eval('#rows tr',trs=>trs.map(tr=>[...tr.children].slice(1,3).map(td=>td.textContent)));
  assert.deepEqual(rows,[['Q3compare_memory · 공통 구간','2/2'],['Q3compare_memory · 기록 제공','2/2'],['Q3compare_memory · 기록 미제공','2/2']]);
  assert.equal(await page.$eval('#error',e=>e.hidden),true);
  // Withheld branch: full dialogue, fork divider after the shared part, counselor-side revisit marks.
  await page.evaluate(()=>document.querySelectorAll('#rows button')[2].click());await page.waitForSelector('#detail[open]');
  assert.ok(await page.$eval('#detail-title',e=>e.textContent.includes('기록 미제공')));
  assert.equal(await page.$eval('#detail-body',e=>e.querySelectorAll('.speech').length),8);
  assert.equal(await page.$eval('#detail-body',e=>e.querySelectorAll('.divider').length),1);
  assert.equal(await page.$eval('#detail-body',e=>[...e.children].findIndex(c=>c.classList.contains('divider'))>=5),true);
  assert.ok(await page.$eval('#detail-body',e=>[...e.querySelectorAll('.mark')].some(m=>m.textContent.includes('내담자 1턴'))));
  assert.equal(await page.$eval('#detail-body',e=>e.querySelectorAll('details').length),4);
  await page.click('#close-detail');
  await page.evaluate(()=>document.querySelectorAll('#rows button')[0].click());await page.waitForSelector('#detail[open]');
  assert.equal(await page.$eval('#detail-body',e=>e.querySelectorAll('.speech').length),5);
  assert.equal(await page.$eval('#detail-body',e=>e.querySelectorAll('.divider').length),0);
  await page.click('#close-detail');await page.reload();await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('성공 3/3'));
  assert.equal(await page.$eval('#mode',e=>e.value),'compare:Q3compare_memory');
  assert.equal(await page.$eval('#counselor',e=>e.value),'P3q');
  assert.equal(await page.$eval('#concurrency',e=>e.value),'1');
  assert.equal(await page.$eval('#fork-turn',e=>e.value),'3');
  assert.equal(await page.$$eval('#profiles input:checked',e=>e.length),1);
  await page.click('#select-none');await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#profiles input').length===100&&!document.querySelector('#settings').disabled);
  assert.equal(await page.$$eval('#profiles input:checked',e=>e.length),0);
  await page.screenshot({path:process.env.SCREENSHOT||'/tmp/compare_branch_ui.png',fullPage:true});
  await page.setViewport({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false);
  // Switching back restores the two-condition controls.
  await page.setViewport({width:1400,height:950});await page.select('#mode','versions');
  assert.equal(await shown('#version-setting'),true);assert.equal(await shown('#fork-setting'),false);
  // The main lab must not request editable templates for code-built JSON inputs.
  const unsupportedRequests=[];
  page.on('request',request=>{
   const u=new URL(request.url());
   if(u.pathname==='/api/prompts/source' && u.searchParams.get('role')==='client' &&
      ['Q3inner_memory','inner_memory_no','Q3compare_memory'].includes(u.searchParams.get('version')) &&
      (u.searchParams.get('kind')==='user' || u.searchParams.get('kind')?.endsWith('_user')))unsupportedRequests.push(request.url());
  });
  await page.goto(url+'/lab');
  await page.waitForFunction(()=>window.PromptEditors && document.querySelector('#experiment-version option[value=Q3compare_memory]'));
  await page.select('#counselor-version','P3q');
  for(const version of ['Q3compare_memory','Q3inner_memory','inner_memory_no']){
   await page.select('#experiment-version',version);
   await page.evaluate(()=>PromptEditors.prepare());
   assert.equal(await page.$eval('#system_prompt-user-template',e=>e.closest('section').hidden),true);
   assert.equal(await page.$eval('#client-subagent-prompt-user-template',e=>e.closest('section').hidden),true);
   assert.ok(await page.$eval('#system_prompt-file-status',e=>e.textContent.includes('파일과 일치')));
  }
  await page.select('#experiment-version','Q1');await page.evaluate(()=>PromptEditors.prepare());
  assert.equal(await page.$eval('#system_prompt-user-template',e=>e.closest('section').hidden),false);
  assert.deepEqual(unsupportedRequests,[]);
  assert.deepEqual(errors,[]);console.log('PASS: compare batch, branch display, and main-lab prompt preparation without unsupported template requests');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
