const p=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('assert'),fs=require('fs');
(async()=>{
 const fixture=spawn(process.env.GEMMA_TEST_PYTHON || (process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing']);let browser;
 fixture.stderr.on('data',()=>{});
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await p.launch({headless:true,timeout:60000,args:['--no-sandbox','--disable-dev-shm-usage']});const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1500,height:1000});await page.goto(url+'/lab');
  await page.waitForFunction(()=>document.querySelector('#experiment-version option[value=Q2a]') && !document.querySelector('#experiment-version').disabled);
  const ready=(id,source)=>page.waitForFunction(({id,source})=>{const el=document.getElementById(id+'-file-status');return el && el.textContent.includes(source) && !el.textContent.includes('불러오는');},{},{id,source});
  const edit=(id,value)=>page.$eval('#'+id,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},value);
  const visible=id=>page.$eval('#'+id,e=>!!e.getClientRects().length);
  const saved=id=>page.waitForFunction(id=>document.getElementById(id+'-file-status').textContent.includes('파일 저장 완료'),{},id);
  const main='system_prompt-user-template',router='client-subagent-prompt-user-template',cmain='counselor-system-prompt-user-template',guide='counselor-subagent-prompt-user-template';
  await page.select('#experiment-version','Q2a');await ready(main,'Q2a/user.md');await ready(router,'Q2a/trait_router_user.md');
  assert(await visible(main));assert(await visible(router));
  const original=await page.$eval('#'+main,e=>e.value);
  assert(original.includes('{{dialogue}}'));assert(original.includes('{{state_notes}}'));
  await edit(main,original+'\nDISCARD');await page.click('#'+main+'-undo');await page.waitForFunction(({id,text})=>document.getElementById(id).value===text,{},{id:main,text:original});
  await edit(main,'BROWSER_MAIN\n'+original);await page.click('#'+main+'-save');await saved(main);
  const source=await page.evaluate(()=>api('/api/prompts/source?role=client&version=Q2a&kind=user'));
  assert(source.path.startsWith('/tmp/'));assert(fs.readFileSync(source.path,'utf8').startsWith('BROWSER_MAIN'));
  const originalRouter=await page.$eval('#'+router,e=>e.value);await edit(router,'BROWSER_ROUTER\n'+originalRouter);await page.click('#'+router+'-save');await saved(router);
  await edit(router,originalRouter+'{{wrong_name}}');await page.click('#'+router+'-save');await page.waitForFunction(id=>document.getElementById(id+'-file-status').textContent.includes('알 수 없는'),{},router);
  await page.click('#'+router+'-undo');await page.waitForFunction(id=>document.getElementById(id).value.startsWith('BROWSER_ROUTER'),{},router);
  await page.select('#experiment-version','Q2');await ready(main,'Q2/user.md');await page.select('#client-subagent-select','state_updater');await ready(router,'Q2/state_updater_user.md');
  assert((await page.$eval('#'+router,e=>e.value)).includes('{{selected_states}}'));
  await page.select('#experiment-version','state_summary');await ready('summary_system_prompt-user-template','state_summary/summary_user.md');
  assert(await visible('summary_system_prompt-user-template'));
  await page.select('#experiment-version','Q2a');await ready(main,'Q2a/user.md');assert((await page.$eval('#'+main,e=>e.value)).startsWith('BROWSER_MAIN'));
  await page.click('#settings-counselor-tab');await page.select('#counselor-version','P3a');await ready(cmain,'P3a/user.md');await ready(guide,'P3a/guide_agent_user.md');
  assert(await visible(cmain));assert(await visible(guide));
  for(const [id,marker] of [[cmain,'BROWSER_COUNSELOR'],[guide,'BROWSER_GUIDE']]){
    await edit(id,marker+'\n'+await page.$eval('#'+id,e=>e.value));await page.click('#'+id+'-save');await saved(id);
  }
  await page.$eval('#'+cmain,e=>e.scrollIntoView({block:'center'}));await page.screenshot({path:'/tmp/user_prompt_templates_ui.png'});
  await page.select('#counselor-version','C4');assert(!(await visible(cmain)));assert(!(await visible(guide)));
  await page.select('#counselor-version','P3a');await ready(cmain,'P3a/user.md');
  await page.setViewport({width:390,height:900});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));
  assert.deepEqual(errors,[]);console.log('PASS: main/subagent templates, variable validation, save/undo, version switching, summary editor, C4 hiding, mobile');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
