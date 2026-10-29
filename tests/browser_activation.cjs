const p=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const fixture=spawn(process.env.GEMMA_TEST_PYTHON || (process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve','--prompt-editing','--p1-slow']);fixture.stderr.on('data',()=>{});let browser;
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await p.launch({headless:true,timeout:60000,args:['--no-sandbox','--disable-dev-shm-usage']});const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:1500,height:1000});await page.goto(url+'/lab');
  await page.waitForFunction(()=>document.querySelector('#experiment-version option[value=Q2a]') && !document.querySelector('#experiment-version').disabled);
  await page.click('#activation-tab');assert.equal(await page.$eval('.lab-shell',e=>e.dataset.view),'activation');
  await page.evaluate(()=>{
   window.actTest=PairedTrace.create('counselor','activation-fixture',1,'P3a','Q2a');
   window.actEvent=e=>PairedTrace.accept(actTest,e);
   window.actShow=i=>Journey.inspectExternal({id:actTest.id,number:1,steps:actTest.steps,question:'테스트',done:false},i);
   window.actPayload={model:'mock',messages:[{role:'system',content:'시스템 지침 MAIN_ONLY'},{role:'user',content:'## 공개 대화\n\n내담자: 평가가 걱정돼요.\n\n## 현재 노트\n\n선생님의 판단이 걱정된다.'}],tools:[{type:'function',function:{name:'read_notes',description:'기록 조회',parameters:{type:'object'}}}]};
   actEvent({type:'request',payload:actPayload,iteration:1});actShow(0);
  });
  const roles=()=>page.$$eval('#activation-blocks .ac-block[data-active=true]',els=>els.map(e=>e.dataset.role));
  const content=id=>page.$eval('#'+id,e=>e.textContent);
  assert.deepEqual(await roles(),['system','user','tools']);
  await page.click('#activation-blocks [data-role=user]');assert((await content('activation-detail')).includes('평가가 걱정돼요'));
  await page.evaluate(()=>{actEvent({type:'chunk',data:{choices:[{delta:{reasoning:'지금의 걱정을 확인한다.',tool_calls:[{index:0,id:'r1',function:{name:'read_notes',arguments:'{"path":'}}]}}]}});actShow(0);});
  assert((await content('activation-output')).includes('도구 호출 작성 중'));assert((await content('activation-output')).includes('지금의 걱정'));
  await page.evaluate(()=>{actEvent({type:'tool_start',name:'read_notes',call_id:'r1',arguments:'{"path":"notes.md"}',assistant_message:{role:'assistant',content:'',reasoning:'지금의 걱정을 확인한다.',tool_calls:[{id:'r1',type:'function',function:{name:'read_notes',arguments:'{"path":"notes.md"}'}}]}});actEvent({type:'tool_result',name:'read_notes',call_id:'r1',model_content:'새로 읽은 기록 TOOL_ONLY'});actShow(2);});
  assert.deepEqual(await roles(),['system','user','tools']);assert((await content('activation-results')).includes('TOOL_ONLY'));
  await page.evaluate(()=>{actEvent({type:'request',iteration:2,payload:{...actPayload,messages:structuredClone(actTest.steps.at(-1).messages)}});actShow(3);});
  assert.deepEqual(await roles(),['system','user','assistant','tool','tools']);assert.equal(await page.$$eval('#activation-blocks [data-active=true][data-role=tool]',els=>els.length),1);
  await page.click('#activation-blocks [data-role=assistant]');assert((await content('activation-detail')).includes('reasoning'));
  await page.click('#activation-blocks [data-role=tool]');assert((await content('activation-detail')).includes('TOOL_ONLY'));
  await page.evaluate(()=>{actEvent({type:'agent_event',agent_id:'child',agent_name:'가이드 서브에이전트',parent_agent_id:'main',depth:1,event:{type:'request',iteration:1,payload:{messages:[{role:'system',content:'CHILD_SYSTEM'},{role:'user',content:'CHILD_USER'}]}}});actShow(4);});
  assert.deepEqual(await roles(),['system','user']);assert((await content('activation-status')).includes('별도 서브에이전트 입력'));assert(!(await page.$$eval('#activation-blocks [data-active=true]',els=>els.map(e=>e.textContent).join(''))).includes('MAIN_ONLY'));
  assert(await page.$('#activation-blocks [data-active=false][data-agent=main]'));
  assert(await page.$$eval('#activation-blocks .ac-block',els=>els.every((e,i)=>!i || e.getBoundingClientRect().top>=els[i-1].getBoundingClientRect().bottom)));
  await page.select('#agent-picker','main');assert.deepEqual(await roles(),['system','user','assistant','tool','tools']);
  await page.select('#agent-picker','auto');await page.$eval('#stage-rail button',e=>e.click());assert.deepEqual(await roles(),['system','user','tools']);
  await page.evaluate(()=>actShow(3));await page.$eval('#activation-blocks [data-role=tool]',e=>e.click());await page.screenshot({path:'/tmp/activation_context.png'});
  await page.click('#sessions-new');await page.click('#settings-tab');await page.click('#settings-client-tab');await page.select('#experiment-version','Q2a');
  await page.waitForFunction(()=>document.querySelector('#tool-enabled-update_client_states'));
  await page.evaluate(()=>{document.querySelector('#manual-counselor').checked=true;document.querySelector('#manual-counselor').dispatchEvent(new Event('change'));});
  await page.click('#activation-tab');await page.click('#paired-next');
  await page.waitForFunction(()=>document.querySelector('#paired-dialogue-count').textContent.includes('새로 생성 1개') && !document.querySelector('#paired-manual-send').disabled,{timeout:30000});
  assert.equal(await page.$eval('.lab-shell',e=>e.dataset.view),'activation');
  await page.waitForFunction(()=>document.querySelector('#activation-title').textContent.includes('모델 호출 4'));
  assert.equal((await roles()).filter(role=>role==='tool').length,3);assert((await content('activation-output')).includes('update_client_states'));assert(!(await content('activation-output')).includes('조금 안심되지만 자세한 이야기는'));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#paired-status').textContent.includes('불러옴'));
  await page.click('#activation-tab');await page.$$eval('#paired-transcript .paired-speech-button',els=>els.findLast(e=>e.textContent.includes(' · 생성 ')).click());
  await page.waitForFunction(()=>document.querySelector('#activation-title').textContent.includes('모델 호출 4'));
  // Select individual regions within one message, independently of other tabs.
  const regions='.paired-speech:last-child .turn-context-select';
  assert((await page.$$(regions)).length>=8);
  await page.$$eval(regions,els=>els[0].click());
  await page.waitForFunction(()=>document.querySelector('#activation-title').textContent.includes('모델 호출 1'));
  assert.equal((await roles()).filter(role=>role==='tool').length,0);
  assert.equal(await page.$eval('.paired-speech:last-child .turn-model-call',e=>getComputedStyle(e).borderTopStyle),'dashed');
  assert.equal(await page.$eval('.paired-speech:last-child .turn-model-call',e=>getComputedStyle(e).borderTopWidth),'3px');
  assert.equal(await page.$$eval('.turn-context-select[aria-pressed=true]',els=>els.length),1);
  // Tool result selection retains the generating request and shows its new result separately.
  await page.$eval('.paired-speech:last-child .turn-tool-call .turn-context-select',e=>e.click());
  assert((await content('activation-title')).includes('모델 호출 1'));
  assert.equal(await page.$eval('#activation-results-box',e=>e.hidden),false);
  // Returning to Raw always selects the turn, regardless of which chat region was chosen.
  await page.click('#raw-tab');
  const rawEnd=await content('raw-json');
  await page.$$eval(regions,els=>els[0].click());
  assert.equal(await content('raw-json'),rawEnd);
  assert.equal(await page.$$eval('.turn-context-select[aria-pressed=true]',els=>els.length),0);
  await page.click('#activation-tab');
  assert((await content('activation-title')).includes('모델 호출 1'));
  // Public utterance came from commit_client_utterance (call 2), not final update (call 4).
  await page.$eval('.paired-speech:last-child .paired-utterance-button',e=>e.click());
  assert((await content('activation-title')).includes('모델 호출 2'));
  await page.click('#workflow-tab');
  const workflowStep=await page.$eval('#stage-rail .selected',e=>e.textContent);
  await page.$$eval(regions,els=>els[0].click());
  assert.equal(await page.$eval('#stage-rail .selected',e=>e.textContent),workflowStep);
  await page.click('#activation-tab');
  // A keyboard-selected request has the same mapping as pointer selection.
  await page.$eval('.paired-speech:last-child .turn-model-call .turn-context-select',e=>e.focus());
  await page.keyboard.press('Enter');assert((await content('activation-title')).includes('모델 호출 1'));
  await page.screenshot({path:'/tmp/activation_selection.png'});
  await page.click('#session-notes-tab');
  await page.waitForFunction(()=>document.querySelector('#session-notes-body [data-actor=client] pre'));
  assert((await content('session-notes-body')).includes('선택을 존중받았다고'));
  assert(await page.$('#session-notes-body [data-actor=counselor]'));
  assert.equal(await page.$eval('.workspace',e=>getComputedStyle(e).display),'none');
  await page.$eval('.paired-speech:last-child .paired-utterance-button',e=>e.click());
  assert.equal(await page.$eval('.lab-shell',e=>e.dataset.view),'session_notes');
  await page.screenshot({path:'/tmp/current_session_notes.png'});
  await page.setViewport({width:390,height:900});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)));assert.deepEqual(errors,[]);
  console.log('PASS: activation tab, block colors/raw, streaming tools, input/result boundary, subagent/agent switch, step selection, live Q2a, reload, mobile');
 }finally{if(browser)await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exit(1);});
