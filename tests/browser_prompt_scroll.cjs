const p=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('assert');
(async()=>{
 const fixture=spawn(process.env.GEMMA_TEST_PYTHON || (process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--state-summary','--p1-slow']);let browser;
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await p.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage(),errors=[],creates=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().endsWith('/api/paired/create'))creates.push(JSON.parse(r.postData()));});
  await page.setViewport({width:1440,height:900});await page.goto(url+'/lab');
  await page.waitForFunction(()=>document.querySelector('#tool-enabled-retrieve_inner_state')?.checked && document.querySelector('#counselor-version option[value=P3]'));
  const current=await page.evaluate(async()=>{const data=await api('/api/paired/counselor');return data.versions.find(v=>v.id==='P3').system_prompt;});
  const stale=current.replace(/## 가이드 조회[\s\S]*?(?=## 작업 연속성)/,'')+'\nSAVED_OLD_PROMPT';
  const create=async(prompt,long=false)=>page.evaluate(async({prompt,long})=>{
   const request=body();request.session_id=null;request.counselor_version='P3';request.counselor_system_prompt=prompt;
   request.messages=[{role:'user',content:'오늘은 어떤 이야기를 해보고 싶으세요?'}];request.speaker_modes={client:'manual',counselor:long?'model':'manual'};
   if(long){
    delete request.scenario.dialogue_turns;request.scenario.dialogue_format='messages';
    request.scenario.dialogue=Array.from({length:30},(_,i)=>({role:i%2?'client':'counselor',content:'초기 대화 '+i+': '+('과거에 나누었던 이야기를 읽고 있습니다. ').repeat(8)}));
   }
   const session=await api('/api/paired/create',request);await Paired.load(session.session_id);return session.session_id;
  },{prompt,long});
  const newChat=async()=>{
   const refreshed=page.waitForResponse(r=>r.url().endsWith('/api/paired/counselor') && r.status()===200);
   await page.click('#sessions-new');await refreshed;await page.click('#settings-counselor-tab');
   await page.waitForFunction(()=>!Paired.hasSession && !document.querySelector('#counselor-system-prompt').disabled);
  };
  const setModes=async counselorManual=>{
   await page.evaluate(counselorManual=>{document.querySelector('#manual-client').checked=true;document.querySelector('#manual-counselor').checked=counselorManual;document.querySelector('#manual-client').dispatchEvent(new Event('change'));},counselorManual);
   await page.waitForFunction(()=>!document.querySelector('#manual-client').disabled);
  };
  const enter=async value=>page.$eval('#paired-manual-text',(e,value)=>{e.value=value;e.dispatchEvent(new Event('input'));},value);
  const idle=()=>page.waitForFunction(()=>!Paired.running && !document.querySelector('#paired-manual-send').disabled);
  const bottom=()=>page.waitForFunction(()=>{const e=document.querySelector('#paired-transcript');return e.scrollHeight-e.clientHeight-e.scrollTop<=3;});
  const frame=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));

  // Reloading an old saved session must preserve its original prompt, while
  // the next new conversation must use the current version default.
  const oldId=await create(stale);await page.reload();
  await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
  assert.equal(await page.$eval('#counselor-system-prompt',e=>e.value),stale);
  assert((await page.$eval('#counselor-prompt-source',e=>e.textContent)).includes('현재 기본과 다름'));
  const beforeNew=creates.length;await newChat();
  await page.waitForFunction(prompt=>document.querySelector('#counselor-system-prompt').value===prompt,{},current);
  assert.equal(await page.$eval('#counselor-prompt-source',e=>e.textContent),'현재 기본 지침');
  assert.equal(creates.length,beforeNew,'new conversation should not itself run generation');
  await setModes(true);await enter('새 기본 지침으로 저장할 내담자 발화');await page.click('#paired-manual-send');await idle();
  assert.equal(creates.at(-1).counselor_system_prompt,current);
  const old=await page.evaluate(id=>api('/api/paired/session?session_id='+id),oldId);
  assert.equal(old.settings.counselor_system_prompt,stale,'saved session prompt changed');

  // Only intentional edits become reusable drafts. Switching versions and
  // starting another conversation retains those edits until explicitly reset.
  await newChat();const custom=current+'\nMANUAL_CUSTOM_INSTRUCTION';
  await page.$eval('#counselor-system-prompt',(e,value)=>{e.value=value;e.dispatchEvent(new Event('input'));},custom);
  assert.equal(await page.$eval('#counselor-prompt-source',e=>e.textContent),'직접 편집한 지침');
  await page.select('#counselor-version','P2');await page.select('#counselor-version','P3');
  assert.equal(await page.$eval('#counselor-system-prompt',e=>e.value),custom);
  await enter('직접 편집한 지침으로 저장할 발화');await page.click('#paired-manual-send');await idle();
  assert.equal(creates.at(-1).counselor_system_prompt,custom);
  await newChat();assert.equal(await page.$eval('#counselor-system-prompt',e=>e.value),custom);
  await page.click('#counselor-prompt-default');
  await page.waitForFunction(prompt=>document.querySelector('#counselor-system-prompt').value===prompt,{},current);
  assert.equal(await page.$eval('#counselor-prompt-source',e=>e.textContent),'현재 기본 지침');

  // A long real DOM transcript: sending from an old scroll position should
  // reveal the new utterance and follow streaming reasoning/tool output.
  await create(current,true);await page.waitForFunction(()=>document.querySelector('#paired-transcript').scrollHeight>1500);
  await page.$eval('#paired-transcript',e=>e.scrollTop=0);await frame();
  await enter('P3_EDIT: 자동 스크롤 첫 번째 확인');await page.click('#paired-manual-send');
  await page.waitForFunction(()=>Paired.running && document.querySelector('.paired-speech:last-child.execution-attempt .turn-process')?.textContent.includes('P3_REASON_0'));
  await bottom();await idle();await bottom();
  assert((await page.$eval('.paired-speech:last-child',e=>e.textContent)).includes('그 걱정 때문에'));

  // Once the user scrolls upward, subsequent stream updates must preserve
  // their reading position instead of dragging them back down.
  await enter('P3_EDIT: 자동 스크롤 두 번째 확인');await page.click('#paired-manual-send');
  await page.waitForFunction(()=>Paired.running && document.querySelector('.paired-speech:last-child.execution-attempt .turn-process')?.textContent.includes('P3_REASON_0'));
  await bottom();await page.$eval('#paired-transcript',e=>e.scrollTop=70);await frame();
  const readingTop=await page.$eval('#paired-transcript',e=>e.scrollTop);assert.equal(readingTop,70);await idle();await frame();
  const afterReading=await page.$eval('#paired-transcript',e=>({top:e.scrollTop,height:e.scrollHeight,client:e.clientHeight}));
  assert(Math.abs(afterReading.top-readingTop)<=2,'streaming moved the reading position: '+JSON.stringify({readingTop,...afterReading}));
  assert(await page.$eval('#paired-transcript',e=>e.scrollHeight-e.clientHeight-e.scrollTop>100));
  await setModes(true);await enter('내담자가 다시 전송하면 맨 아래로 이동');await page.click('#paired-manual-send');await idle();await bottom();
  await page.setViewport({width:390,height:900});await bottom();
  assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));
  assert.deepEqual(errors,[]);
  console.log('PASS: old-session prompt isolation, current defaults in new sessions, deliberate draft preservation/reset, manual-send scroll, streaming follow, reading-position preservation and mobile resize');
 }finally{if(browser)await browser.close();fixture.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exit(1);});
