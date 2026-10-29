const puppeteer=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),fs=require('fs'),assert=require('assert');
(async()=>{
 const fixture=spawn(process.env.GEMMA_TEST_PYTHON || (process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing']);let browser;
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await puppeteer.launch({headless:true,timeout:60000,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage(),errors=[],creates=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',request=>{if(request.url().endsWith('/api/paired/create'))creates.push(JSON.parse(request.postData()));});
  await page.setViewport({width:1500,height:1000});await page.goto(url+'/lab');
  const source=(role,version,kind='system')=>page.evaluate(value=>api('/api/prompts/source?'+new URLSearchParams(value)),{role,version,kind});
  const waitPrompt=(id,value)=>page.waitForFunction(({id,value})=>{const e=document.getElementById(id);return e.value===value && !e.disabled;},{},{id,value});
  const select=async(id,value)=>{await page.select('#'+id,value);await page.evaluate(()=>PromptEditors.sync());};
  const edit=(id,value)=>page.$eval('#'+id,(e,value)=>{e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));},value);
  const run=async count=>{await page.click('#paired-next');await page.waitForFunction(count=>!Paired.running && document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 '+count+'개'),{},count);};
  const stored=id=>page.evaluate(id=>api('/api/paired/session?session_id='+id),id);
  const requests=async id=>page.evaluate(async id=>{
   const session=await api('/api/paired/session?session_id='+id),result=[];
   for(const attempt of session.attempts){
    const events=await api('/api/paired/events?'+new URLSearchParams({session_id:id,attempt:attempt.turn_id}));
    for(const event of events.events){if(event.type==='request')result.push({actor:attempt.actor,payload:event.payload});}
   }
   return result;
  },id);
  await page.waitForSelector('#experiment-version option[value=Q1]');
  await select('experiment-version','Q1');await page.click('#settings-counselor-tab');await select('counselor-version','P3a');
  const client=await source('client','Q1'),counselor=await source('counselor','P3a');
  assert(client.path.startsWith('/tmp/'));assert(counselor.path.startsWith('/tmp/'));
  await waitPrompt('system_prompt',client.content);await waitPrompt('counselor-system-prompt',counselor.content);
  await run(1);const oldId=await page.evaluate(()=>Paired.sessionId);
  assert.equal(creates[0].system_prompt,client.content);
  assert.equal(creates[0].counselor_system_prompt,counselor.content);
  const nextClient=client.content+'\nFRESH_CLIENT_FILE',nextCounselor=counselor.content+'\nFRESH_COUNSELOR_FILE';
  fs.writeFileSync(client.path,nextClient);fs.writeFileSync(counselor.path,nextCounselor);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
  await waitPrompt('system_prompt',client.content);await waitPrompt('counselor-system-prompt',counselor.content);
  await page.waitForFunction(()=>document.querySelector('#system_prompt-file-status').textContent.includes('저장된 세션의 지침'));
  await run(2);
  const oldRequests=await requests(oldId);
  assert(oldRequests.some(row=>row.actor==='client' && row.payload.messages[0].content===client.content));
  assert(oldRequests.some(row=>row.actor==='counselor' && row.payload.messages[0].content===counselor.content));
  assert(!oldRequests.some(row=>row.payload.messages[0].content.includes('FRESH_CLIENT_FILE') || row.payload.messages[0].content.includes('FRESH_COUNSELOR_FILE')));
  await page.click('#sessions-new');await select('experiment-version','Q1');
  await waitPrompt('system_prompt',nextClient);await waitPrompt('counselor-system-prompt',nextCounselor);
  await run(1);const newId=await page.evaluate(()=>Paired.sessionId);await run(2);
  const freshRequests=await requests(newId);
  assert(freshRequests.some(row=>row.actor==='client' && row.payload.messages[0].content===nextClient));
  assert(freshRequests.some(row=>row.actor==='counselor' && row.payload.messages[0].content===nextCounselor));
  assert.equal((await stored(oldId)).settings.system_prompt,client.content);
  assert.equal((await stored(oldId)).settings.counselor_system_prompt,counselor.content);
  // An untouched draft also refreshes if the file changes just before sending.
  await page.click('#sessions-new');await select('experiment-version','Q1');
  await waitPrompt('system_prompt',nextClient);await waitPrompt('counselor-system-prompt',nextCounselor);
  const sendClient=nextClient+'\nCHANGED_BEFORE_SEND',sendCounselor=nextCounselor+'\nCHANGED_BEFORE_SEND';
  fs.writeFileSync(client.path,sendClient);fs.writeFileSync(counselor.path,sendCounselor);
  await run(1);
  assert.equal(creates.at(-1).system_prompt,sendClient);assert.equal(creates.at(-1).counselor_system_prompt,sendCounselor);
  // Intentional unsaved edits survive version switches and file refreshes.
  await page.click('#sessions-new');await select('experiment-version','Q1');
  await waitPrompt('system_prompt',sendClient);await waitPrompt('counselor-system-prompt',sendCounselor);
  const draftClient=sendClient+'\nMY_CLIENT_DRAFT',draftCounselor=sendCounselor+'\nMY_COUNSELOR_DRAFT';
  await edit('system_prompt',draftClient);await edit('counselor-system-prompt',draftCounselor);
  await select('experiment-version','basic');await select('experiment-version','Q1');await waitPrompt('system_prompt',draftClient);
  await select('counselor-version','P2');await select('counselor-version','P3a');await waitPrompt('counselor-system-prompt',draftCounselor);
  fs.writeFileSync(client.path,sendClient+'\nNEWER_FILE');fs.writeFileSync(counselor.path,sendCounselor+'\nNEWER_FILE');
  await run(1);
  assert.equal(creates.at(-1).system_prompt,draftClient);assert.equal(creates.at(-1).counselor_system_prompt,draftCounselor);
  assert(fs.readFileSync(client.path,'utf8').endsWith('NEWER_FILE'));
  assert(fs.readFileSync(counselor.path,'utf8').endsWith('NEWER_FILE'));
  // New dialogue refreshes the screen even if no session has been created yet.
  await page.click('#sessions-new');await select('experiment-version','Q1');
  await waitPrompt('system_prompt',draftClient);
  await page.$eval('#client-prompt-undo',e=>e.click());await waitPrompt('system_prompt',sendClient+'\nNEWER_FILE');
  const idleClient=sendClient+'\nCHANGED_WHILE_IDLE';fs.writeFileSync(client.path,idleClient);
  await page.click('#sessions-new');await waitPrompt('system_prompt',idleClient);
  // A failed source read must stop creation instead of sending stale text.
  const previousCreates=creates.length;fs.renameSync(client.path,client.path+'.missing');
  try{
   await page.click('#paired-next');
   await page.waitForFunction(()=>!Paired.running && document.querySelector('#paired-status').textContent.includes('중단 / 오류'));
   assert.equal(creates.length,previousCreates);assert(!(await page.evaluate(()=>Paired.hasSession)));
  }finally{fs.renameSync(client.path+'.missing',client.path);}
  assert.deepEqual(errors,[]);
  console.log('PASS: restored session stays frozen; new Q1/P3a model requests use fresh files; refresh preserves intentional drafts; idle reset refreshes; failed reads stop stale submissions');
 }finally{if(browser)await browser.close();fixture.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exit(1);});
