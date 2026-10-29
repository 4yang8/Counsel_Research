const p=require(process.env.PUPPETEER_MODULE || '/home2/sjyang114/.npm/_npx/d62b6517736c1e35/node_modules/puppeteer');
const {spawn}=require('child_process');const path=require('path');
(async()=>{
 const fixture=spawn(process.env.GEMMA_TEST_PYTHON || (process.env.COUNSELING_PYTHON || process.env.GEMMA_TEST_PYTHON || 'python3'),[path.join(__dirname,'test_lab.py'),'--serve']);let browser;
 try{
  const url=await new Promise((resolve,reject)=>{fixture.stdout.once('data',d=>resolve(d.toString().trim()));fixture.once('exit',c=>reject(Error('fixture '+c)));});
  browser=await p.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage(),counts={},errors=[];let configReads=0,generations=0;
  page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);
  page.on('request',request=>{
   const pathname=new URL(request.url()).pathname;
   if(pathname==='/api/paired/step' || pathname==='/api/chat')generations++;
   if(pathname==='/api/config' && ++configReads===1)return request.respond({status:400,contentType:'text/html',body:'<!DOCTYPE html><h1>Bad Request</h1>'});
   if(pathname.startsWith('/api/http-test/')){
    counts[pathname]=(counts[pathname] || 0)+1;
    const name=pathname.split('/').at(-1);
    if(name==='protocol' && counts[pathname]===1)return request.respond({status:400,contentType:'application/json',body:JSON.stringify({error:'Bad request',request_error:true})});
    if(name==='protocol')return request.respond({status:200,contentType:'application/json',body:'{"recovered":true}'});
    if(name==='validation')return request.respond({status:400,contentType:'application/json',body:'{"error":"선택한 설정이 올바르지 않습니다."}'});
    if(name==='broken-json')return request.respond({status:200,contentType:'application/json',body:'{broken'});
    return request.respond({status:name==='stream-ok'?200:400,contentType:'text/html',body:'<!DOCTYPE html><h1>Bad Request</h1>'});
   }
   request.continue();
  });
  await page.goto(url+'/lab');await page.waitForFunction(()=>document.querySelector('#scenario').textContent.includes('lab_meeting_handover') && document.querySelector('#c4-source').textContent.includes('C4 ·'));
  if(configReads!==2 || generations!==0)throw Error('initial config was not recovered safely');
  const test=async(name,post=false,stream=false)=>page.evaluate(async({name,post,stream})=>{
   const path='/api/http-test/'+name;
   try{
    if(stream){const response=await fetch(path,{method:'POST'});await LabHTTP.requireStream(response,path);return {ok:true};}
    return {ok:true,result:await LabHTTP.requestJSON(path,post?{}:undefined)};
   }catch(error){return {ok:false,error:error.message};}
  },{name,post,stream});
  const recovered=await test('protocol');if(!recovered.result?.recovered || counts['/api/http-test/protocol']!==2)throw Error('JSON protocol error did not retry once');
  const html=await test('html');if(html.ok || !html.error.includes('/api/http-test/html · HTTP 400') || !html.error.includes('HTML') || counts['/api/http-test/html']!==2)throw Error('GET retry/message failed');
  for(const name of ['post','stream','stream-ok']){
   const result=await test(name,true,name.startsWith('stream'));
   if(result.ok || !result.error.includes('/api/http-test/'+name) || result.error.includes('Unexpected token') || counts['/api/http-test/'+name]!==1)throw Error('POST/stream was retried or poorly explained: '+name);
  }
  const validation=await test('validation');if(!validation.error.includes('선택한 설정이 올바르지 않습니다.') || counts['/api/http-test/validation']!==1)throw Error('validation message lost/retried');
  const broken=await test('broken-json');if(!broken.error.includes('올바르지 않은 JSON') || counts['/api/http-test/broken-json']!==1)throw Error('invalid JSON message/retry');
  if(errors.length)throw Error('page errors: '+errors.join(', '));
  console.log('PASS: initial HTML recovery, JSON protocol recovery, bounded GET retries, no POST retries, stream validation, original server errors');
 }finally{if(browser)await browser.close();fixture.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exit(1);});
