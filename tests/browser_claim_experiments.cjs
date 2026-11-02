const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
  const fixture=spawn(process.env.COUNSELING_PYTHON || 'python3',[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing'],
    {env:{...process.env,PYTHONPATH:[path.join(__dirname,'../src'),__dirname,process.env.PYTHONPATH || ''].join(path.delimiter),PYTHONDONTWRITEBYTECODE:'1'}});
  let fixtureError='';fixture.stderr.on('data',d=>{fixtureError+=d.toString();});let browser;
  try{
    const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c+'\n'+fixtureError)));});
    browser=await puppeteer.launch({headless:true,pipe:true,args:['--no-sandbox','--disable-dev-shm-usage']});
    for(const [version,condition] of [['Q3p1','summary_only'],['Q3p2','intent_unlinked'],['Q3p3','retrospective'],['Q3p4','summary_only'],['Q3p5','summary_only']]){
      const context=await browser.createBrowserContext(),page=await context.newPage(),errors=[];
      page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1500,height:1000});
      await page.goto(url+'/lab');
      await page.waitForFunction(()=>document.querySelector('#experiment-version option[value=Q3p5]') && window.LabPane?.ready && !document.querySelector('#experiment-version').disabled);
      await page.select('#experiment-version',version);
      await page.waitForFunction(()=>!document.querySelector('#claim-memory-setting').hidden && !document.querySelector('#q3-trajectory-editor').hidden);
      await page.select('#claim-memory-condition',condition);
      await page.evaluate(()=>LabPane.save());await page.reload();
      await page.waitForFunction(()=>window.LabPane?.ready && !document.body.classList.contains('pane-restoring'));
      assert.equal(await page.$eval('#experiment-version',e=>e.value),version);
      assert.equal(await page.$eval('#claim-memory-condition',e=>e.value),condition,'draft condition restore');
      if(version==='Q3p3'){
        await page.select('#client-subagent-select','intent_reconstructor');
        await page.waitForFunction(()=>document.querySelector('#client-subagent-prompt-user-template').value.includes('{{claim_input}}'));
      }
      await page.evaluate(()=>{document.querySelector('#manual-counselor').checked=true;document.querySelector('#manual-counselor').dispatchEvent(new Event('change'));});
      await page.waitForSelector('#paired-next:not([hidden])',{visible:true});await page.click('#paired-next');
      await page.waitForFunction(()=>document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 1개') && !document.querySelector('#paired-manual-send').disabled,{timeout:30000});
      await page.type('#paired-manual-text','앞서 동의하셨는데 지금은 어떻게 느끼세요?');await page.click('#paired-manual-send');
      await page.waitForFunction(()=>document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 2개') && !document.querySelector('#paired-manual-send').disabled,{timeout:30000}).catch(async error=>{
        console.error(version,await page.evaluate(async()=>({status:document.querySelector('#paired-status').textContent,
          notice:document.querySelector('#notice').textContent,count:document.querySelector('#paired-dialogue-count').textContent,
          text:document.querySelector('#paired-transcript').textContent.slice(-1000)})));throw error;
      });
      const notes=await page.evaluate(()=>api('/api/client_notes?paired=1&session_id='+Paired.sessionId));
      assert.equal(notes.records.length,2);assert.equal(notes.records[1].claim_experiment.memory_condition,condition);
      assert.equal(await page.$eval('#claim-memory-condition',e=>e.disabled),true,'session condition locked');
      assert.equal(Object.keys(notes.records[1].claim_experiment.auxiliary_calls).length,{Q3p1:0,Q3p2:0,Q3p3:1,Q3p4:1,Q3p5:2}[version]);
      await page.click('#client-notes-tab');
      await page.waitForFunction(()=>document.querySelector('#client-notes-body').textContent.includes('주장별 실험'));
      await page.click('#session-notes-tab');
      await page.waitForFunction(()=>document.querySelector('#session-notes-body').textContent.includes('현재 궤적 진행'));
      await page.reload();await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
      assert.equal(await page.$eval('#claim-memory-condition',e=>e.value),condition,'session condition restore');
      assert.equal(await page.$eval('#claim-memory-condition',e=>e.disabled),true);
      assert.deepEqual(errors,[]);console.log('PASS '+version+': conditions, draft/reload, two client turns, observers, notes');
      await context.close();
    }
  }finally{if(browser)await browser.close();fixture.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exit(1);});
