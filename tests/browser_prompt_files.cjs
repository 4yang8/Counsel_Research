const p=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('assert'),fs=require('fs');
(async()=>{
 const fixture=spawn(process.env.GEMMA_TEST_PYTHON || (process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--state-summary','--prompt-editing']);let browser;
 // Drain server request logs so the subprocess pipe cannot stall HTTP replies.
 fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await p.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1500,height:1000});await page.goto(url+'/lab');
  await page.waitForFunction(()=>config && document.querySelector('#counselor-version option[value=P3]') && document.querySelector('#system_prompt-file-status').textContent.includes('파일과 일치'));
  const read=(role,version,kind='system')=>page.evaluate(args=>api('/api/prompts/source?'+new URLSearchParams(args)),{role,version,kind});
  const edit=(id,value)=>page.$eval('#'+id,(e,value)=>{e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));},value);
  const saved=id=>page.waitForFunction(id=>document.getElementById(id+'-file-status').textContent.includes('파일 저장 완료'),{},id);
  await page.click('#settings-counselor-tab');await page.select('#counselor-version','P3');
  await page.waitForFunction(()=>document.querySelector('#counselor-system-prompt-file-status').textContent.includes('/P3/system.md'));
  const original=await read('counselor','P3');assert(original.path.startsWith('/tmp/'));
  await edit('counselor-system-prompt',original.content+'\nDISCARD_ME');
  await page.click('#counselor-prompt-default');
  await page.waitForFunction(text=>document.querySelector('#counselor-system-prompt').value===text,{},original.content);
  assert.equal(fs.readFileSync(original.path,'utf8'),original.content);
  const changed=original.content+'\n원본 파일에 저장한 상담사 지침\n';
  await edit('counselor-system-prompt',changed);await page.click('#counselor-system-prompt-save');await saved('counselor-system-prompt');
  assert.equal(fs.readFileSync(original.path,'utf8'),changed);
  await page.select('#counselor-version','P2');await page.select('#counselor-version','P3');
  assert.equal(await page.$eval('#counselor-system-prompt',e=>e.value),changed);
  await page.click('#settings-client-tab');
  for(const [id,kind,undo] of [['system_prompt','system','client-prompt-undo'],['summary_system_prompt','summary','summary-prompt-undo']]){
   const current=await read('client','state_summary',kind);assert(current.path.startsWith('/tmp/'));
   await edit(id,current.content+'\nDISCARD_CLIENT');await page.click('#'+undo);
   await page.waitForFunction(({id,content})=>document.getElementById(id).value===content,{}, {id,content:current.content});
   assert.equal(fs.readFileSync(current.path,'utf8'),current.content);
   const updated=current.content+'\n저장한 내담자 '+kind+' 지침\n';await edit(id,updated);await page.click('#'+id+'-save');await saved(id);
   assert.equal(fs.readFileSync(current.path,'utf8'),updated);
  }
  await page.select('#experiment-version','basic');
  await page.waitForFunction(()=>document.querySelector('#system_prompt-file-status').textContent.includes('system_prompt'));
  assert(await page.$eval('#summary-prompt-actions',e=>e.hidden));
  const basic=await read('client','basic');const configBefore=JSON.parse(fs.readFileSync(basic.path,'utf8'));
  await edit('system_prompt','기본 내담자 파일 저장 테스트');await page.click('#system_prompt-save');await saved('system_prompt');
  assert.deepEqual(JSON.parse(fs.readFileSync(basic.path,'utf8')),{...configBefore,system_prompt:'기본 내담자 파일 저장 테스트'});
  await page.select('#experiment-version','state_summary');await page.click('#settings-counselor-tab');await page.select('#counselor-version','P3');
  // Reload defaults from disk, then freeze them in a real saved session.
  await page.reload();await page.waitForFunction(()=>config && document.querySelector('#counselor-version option[value=P3]'));
  await page.click('#settings-counselor-tab');await page.select('#counselor-version','P3');
  assert.equal(await page.$eval('#counselor-system-prompt',e=>e.value),changed);
  const sessionId=await page.evaluate(async()=>{const request=body();request.counselor_version='P3';request.speaker_modes={client:'manual',counselor:'manual'};const session=await api('/api/paired/create',request);await Paired.load(session.session_id);return session.session_id;});
  await page.click('#settings-tab');await page.click('#settings-counselor-tab');
  assert(!(await page.$eval('#counselor-system-prompt',e=>e.disabled)));
  const later=changed+'\n다음 세션용 수정\n';await edit('counselor-system-prompt',later);await page.click('#counselor-system-prompt-save');await saved('counselor-system-prompt');
  const old=await page.evaluate(id=>api('/api/paired/session?session_id='+id),sessionId);
  assert.equal(old.settings.counselor_system_prompt,changed);
  await page.click('#sessions-new');await page.click('#settings-counselor-tab');
  await page.waitForFunction(value=>!Paired.hasSession && document.querySelector('#counselor-system-prompt').value===value,{},later);
  // A changed file rejects stale saves while preserving the user's draft.
  const source=await read('counselor','P3');await edit('counselor-system-prompt',later+'\nMY_DRAFT');
  await page.evaluate(source=>api('/api/prompts/save',{...source,content:source.content+'\nOTHER_EDITOR'}),source);
  await page.click('#counselor-system-prompt-save');
  await page.waitForFunction(()=>document.querySelector('#counselor-system-prompt-file-status').textContent.includes('원본이 변경'));
  assert((await page.$eval('#counselor-system-prompt',e=>e.value)).endsWith('MY_DRAFT'));
  await page.click('#counselor-prompt-default');
  await page.waitForFunction(()=>document.querySelector('#counselor-system-prompt').value.endsWith('OTHER_EDITOR'));
  assert.equal(await page.$eval('#counselor-prompt-source',e=>e.textContent),'현재 기본 지침');
  await page.setViewport({width:390,height:900});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));
  assert.deepEqual(errors,[]);
  console.log('PASS: file save/undo for counselor and both client prompts, basic config preservation, reload, version switching, frozen sessions, conflicts and mobile');
 }finally{if(browser)await browser.close();fixture.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exit(1);});
