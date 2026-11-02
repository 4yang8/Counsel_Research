// Mock HTTP model only: actual UI selection, persisted treatment and source loading.
const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
  const fixture=spawn(process.env.COUNSELING_PYTHON || 'python3',[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing'],{
    env:{...process.env,PYTHONPATH:[path.join(__dirname,'../src'),__dirname,path.join(__dirname,'../experiments')].join(path.delimiter),PYTHONDONTWRITEBYTECODE:'1'}});
  let browser,fixtureError='';fixture.stderr.on('data',d=>fixtureError+=d.toString());
  try{
    const url=await new Promise((resolve,reject)=>{
      fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c+'\n'+fixtureError)));
    });
    browser=await puppeteer.launch({headless:true,pipe:true,args:['--no-sandbox','--disable-dev-shm-usage']});
    const page=await browser.newPage(),errors=[],creates=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(r.url().endsWith('/api/paired/create'))creates.push(JSON.parse(r.postData()));});
    await page.setViewport({width:1500,height:1000});await page.goto(url+'/lab');
    await page.waitForFunction(()=>window.LabPane?.ready && !document.querySelector('#experiment-version').disabled && document.querySelector('#counselor-version option[value=Q3p4_counsel]'));
    await page.select('#experiment-version','Q3p4');await page.select('#counselor-version','Q3p4_counsel');
    await page.click('#settings-counselor-tab');
    await page.waitForFunction(()=>!document.querySelector('#counselor-intervention-setting').hidden && !document.querySelector('#counselor-intervention-condition').disabled);
    assert.equal(await page.$eval('#counselor-intervention-condition',e=>e.value),'A');
    await page.waitForFunction(()=>document.querySelector('#counselor-system-prompt-user-template').value.includes('{{intervention_condition}}'));
    const source=await page.evaluate(()=>api('/api/prompts/source?role=counselor&version=Q3p4_counsel&kind=system'));
    assert.equal(await page.$eval('#counselor-system-prompt',e=>e.value),source.content);
    await page.select('#counselor-intervention-condition','B');await page.select('#claim-memory-condition','summary_only');
    await page.evaluate(()=>LabPane.save());await page.reload();
    await page.waitForFunction(()=>window.LabPane?.ready && !document.body.classList.contains('pane-restoring'));
    assert.equal(await page.$eval('#counselor-version',e=>e.value),'Q3p4_counsel');
    assert.equal(await page.$eval('#counselor-intervention-condition',e=>e.value),'B','draft treatment restored');
    const common=await page.evaluate(()=>LabPane.common());
    assert.equal(common.values['counselor-intervention-condition'].value,'B','common settings carry treatment');
    await page.select('#counselor-intervention-condition','A');
    await page.evaluate(async value=>{await LabPane.validateCommon(value,{fresh:true});await LabPane.applyCommon(value,{fresh:true});},common);
    assert.equal(await page.$eval('#counselor-intervention-condition',e=>e.value),'B','common settings restore treatment');
    await page.evaluate(()=>{const e=document.querySelector('#paired-mode');e.checked=false;e.dispatchEvent(new Event('change'));});
    for(let n=1;n<=2;n++){
      await page.waitForSelector('#paired-next:not([hidden]):not(:disabled)',{visible:true});await page.click('#paired-next');
      await page.waitForFunction(n=>!Paired.running && document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 '+n+'개'),{timeout:30000},n);
    }
    assert.equal(creates.length,1);assert.equal(creates[0].counselor_intervention_condition,'B');
    assert.equal(creates[0].claim_memory_condition,'summary_only');
    assert.equal(await page.$eval('#counselor-intervention-condition',e=>e.disabled),true,'session treatment locked');
    const saved=await page.evaluate(()=>api('/api/paired/session?session_id='+Paired.sessionId));
    assert.equal(saved.settings.counselor_intervention_condition,'B');assert.equal(saved.steps.length,2);
    const journal=await page.evaluate(value=>api('/api/paired/events?'+new URLSearchParams(value)),{session_id:saved.session_id,attempt:saved.attempts[1].turn_id});
    const requests=journal.events.filter(e=>e.type==='request');assert.equal(requests.length,1);
    assert(requests[0].payload.messages[1].content.startsWith('## 이번 세션의 개입 조건\n\nB\n'));
    await page.reload();await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
    assert.equal(await page.$eval('#counselor-intervention-condition',e=>e.value),'B');
    assert.equal(await page.$eval('#counselor-intervention-condition',e=>e.disabled),true);
    await page.click('#sessions-new');await page.waitForFunction(()=>!document.querySelector('#counselor-intervention-condition').disabled);
    await page.select('#counselor-version','basic');
    assert.equal(await page.$eval('#counselor-intervention-setting',e=>e.hidden),true);
    await page.select('#counselor-version','Q3p4_counsel');
    assert.equal(await page.$eval('#counselor-intervention-condition',e=>e.value),'B');
    assert.deepEqual(errors,[]);
    console.log('PASS: P4 A/B UI, editable prompt/template, draft and common settings, Q3p4 mock pair, frozen treatment, reload and new dialogue');
  }catch(error){console.error(fixtureError.slice(-2500));throw error;}
  finally{if(browser)await browser.close();fixture.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exit(1);});
