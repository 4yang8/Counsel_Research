const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn(process.env.COUNSELING_PYTHON || 'python3',[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing']);let browser;
 fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  const api=async(route,body)=>{const r=await fetch(url+route,body?{method:'POST',headers:{'Content-Type':'application/json','X-Gemma-Request':'1'},body:JSON.stringify(body)}:{});const text=await r.text();assert(r.ok,text);return route.endsWith('/step')?text:JSON.parse(text);};
  browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewport({width:1500,height:1000});await page.goto(url+'/routing');await page.waitForFunction(()=>document.querySelector('#routing-status').textContent.includes('0개 세션'));
  const cfg=await api('/api/config'),versions=await api('/api/versions');
  for(const version of ['Q3n','Q3sb','Q3x']){
   const spec=versions.versions.find(v=>v.id===version),scenario=structuredClone(cfg.scenario);scenario.dialogue_format='messages';scenario.dialogue=[];delete scenario.dialogue_turns;
   const s=await api('/api/paired/create',{experiment_version:version,counselor_version:'basic',scenario,generation:cfg.generation,system_prompt:spec.system_prompt,
    messages:[{role:'user',content:'준비할 시간을 갖고 이야기해도 괜찮아요.'}],comparison:{batch_id:'routing-fixture',schedule:'sequential',requested_utterances:5}});
   for(let i=0;i<5;i++){const events=(await api('/api/paired/step',{session_id:s.session_id,expected_step:i})).trim().split('\n').map(JSON.parse);assert.equal(events.at(-1).event.type,'done');}
  }
  const catalog=await api('/api/routing/catalog');assert.equal(catalog.sessions.length,3);
  const before=await Promise.all(catalog.sessions.map(s=>api('/api/paired/session?session_id='+s.session_id)));
  await page.goto(url+'/routing?batch=routing-fixture');await page.waitForFunction(()=>document.querySelectorAll('.session-card').length===3).catch(async e=>{console.log('DASHBOARD ERROR',errors,await page.$eval('#routing-status',e=>e.textContent));throw e;});
  assert.equal(await page.$$eval('.heatmap',els=>els.length),6);
  assert.equal(await page.$$eval('.session-card',els=>els.every(e=>e.textContent.includes('내담자 기록 3/3턴'))),true);
  await page.$eval('.heatmap .cell.selected',e=>e.click());
  assert(await page.$eval('.inspector',e=>e.textContent.includes('내담자 1턴')&&e.textContent.includes('관련성')));
  await page.$$eval('.session-card',cards=>cards[0].querySelectorAll('.heatmap')[1].querySelector('.cell.selected').click());
  assert(await page.$eval('.inspector',e=>e.textContent.includes('선택됨')&&e.textContent.includes('기록 출처')));
  await page.screenshot({path:'/tmp/routing_dashboard_fixture_desktop.png',fullPage:false});
  await page.$eval('#routing-from',e=>e.value='2');await page.$eval('#routing-to',e=>e.value='3');await page.click('#routing-load');
  await page.waitForFunction(()=>[...document.querySelectorAll('.session-card')].every(e=>e.textContent.includes('표시 범위 2~3턴')));
  assert.equal(await page.$$eval('.summary-table tbody',els=>els.every(e=>e.children.length===2)),true);
  await page.evaluate(()=>{window.routingExports=[];HTMLAnchorElement.prototype.click=function(){if(this.download)window.routingExports.push(this.download);};});
  await page.click('#routing-json');await page.click('#routing-csv');assert.deepEqual(await page.evaluate(()=>window.routingExports),['routing-analysis.json','routing-turns.csv']);
  await page.$eval('#routing-to',e=>e.value='1');await page.click('#routing-load');assert(await page.$eval('#routing-status',e=>e.textContent.includes('범위를 확인')));
  await page.setViewport({width:390,height:900});await page.evaluate(()=>scrollTo(0,0));
  assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));await page.screenshot({path:'/tmp/routing_dashboard_fixture_mobile.png'});
  assert.deepEqual(await Promise.all(catalog.sessions.map(s=>api('/api/paired/session?session_id='+s.session_id))),before);
  assert.deepEqual(errors,[]);console.log('PASS routing: empty catalog, three-version batch, heatmaps and raw details, range, exports, mobile, source preservation');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
