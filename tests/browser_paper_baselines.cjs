const p=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('assert');
(async()=>{
  const fixture=spawn(process.env.COUNSELING_PYTHON || 'python3',[path.join(__dirname,'test_paper_baselines.py'),'--serve'],
    {env:{...process.env,PYTHONPATH:path.join(__dirname,'../src')+':'+__dirname}});
  let browser;let stderr='';
  fixture.stderr.on('data',d=>stderr+=d.toString());
  try{
    const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',code=>reject(Error('fixture '+code+' '+stderr)));});
    browser=await p.launch({headless:true,args:['--no-sandbox']});
    const page=await browser.newPage(),errors=[];
    page.on('pageerror',e=>{errors.push(e.message);console.error('pageerror:',e.message);});
    await page.goto(url+'/lab');
    await page.waitForFunction(()=>document.querySelector('#experiment-version option[value="Q3patientact"]'));
    await page.evaluate(async()=>{await Paired.ready;});
    await page.select('#experiment-version','Q3patientact');
    await page.waitForFunction(()=>document.querySelector('#paper-settings')?.dataset.version==='Q3patientact');
    await page.evaluate(async()=>{await versionReady;await PromptEditors.prepare();});
    assert.equal(await page.$$eval('#paper-profile option',rows=>rows.length),40);
    await page.select('#paper-profile','2');
    assert.equal(await page.evaluate(()=>body().paper_options.profile_index),2);
    assert.equal(await page.evaluate(()=>body().messages.at(-1).content),'안녕하세요. 오늘 어떤 이야기를 나누고 싶으세요?');
    await page.evaluate(async()=>{await document.querySelector('#paired-next').onclick();});
    const sid=await page.evaluate(()=>Paired.sessionId);
    assert(sid);
    const saved=await page.evaluate(id=>api('/api/paired/session?session_id='+id),sid);
    assert.equal(saved.steps.length,1);
    assert.equal(saved.settings.paper_options.profile_index,2);
    assert.equal(saved.public_dialogue.length,2);
    assert.equal(await page.$eval('#paper-options',e=>e.disabled),true);
    await page.reload();
    await page.waitForFunction(id=>window.Paired?.sessionId===id,{},sid);
    await page.waitForFunction(()=>document.querySelector('#paper-options')?.value.includes('"profile_index": 2'));
    await page.evaluate(async()=>{await Paired.ready;});
    await page.waitForFunction(()=>!Paired.running && !busy && !settingsLoading);
    assert.equal(await page.evaluate(()=>body().paper_options.profile_index),2);
    await page.evaluate(()=>Paired.reset(false));
    for(const version of ['Q3cars','Q3calmit']){
      await page.select('#experiment-version',version);
      await page.evaluate(async()=>{await versionReady;await PromptEditors.prepare();});
      if(version==='Q3calmit'){
        await page.select('#counselor-version','Q3calmit_counsel');
        await page.evaluate(async()=>{await PromptEditors.prepare();});
        await page.$eval('#paper-options',e=>{e.value='{"max_speaker_turns":2}';e.dispatchEvent(new Event('input',{bubbles:true}));});
      }
      const result=await page.evaluate(()=>document.querySelector('#paired-next').onclick());
      assert.equal(result.status,'completed',JSON.stringify(result));
      const current=await page.evaluate(()=>Paired.sessionId);
      const session=await page.evaluate(id=>api('/api/paired/session?session_id='+id),current);
      assert.equal(session.client_version,version);
      assert.equal(session.steps.length,1);
      if(version==='Q3calmit'){
        assert.equal(session.terminated,true);
        assert.equal(await page.$eval('#paired-next',e=>e.disabled),true);
      }else await page.evaluate(()=>Paired.reset(false));
    }
    assert.deepEqual(errors,[]);
    console.log('PASS: paper versions, native profile picker, prompt loading, generation, frozen restore, CALM-IT termination');
  }finally{
    if(browser)await browser.close();
    fixture.kill('SIGTERM');
  }
})().catch(e=>{console.error(e);process.exit(1);});
