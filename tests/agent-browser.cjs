/* Native browser integration harness. Model replies are fixtures; file tools are real. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const pw=process.env.PLAYWRIGHT_MODULE||path.join(process.env.USERPROFILE||os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {chromium}=require(pw);const base=process.env.JARVIS_TEST_URL||'http://localhost:18762';
const fixtureRoot=fs.mkdtempSync(path.join(os.tmpdir(),'jarvis-phase1-fixture-'));fs.mkdirSync(path.join(fixtureRoot,'fixture'));const file=path.join(fixtureRoot,'fixture','main.py');
const plan={title:'Repair fixture',steps:[{id:'inspect',title:'Inspect project source',verification:'Actual main.py contents read',kind:'observation'},{id:'reproduce',title:'Reproduce the failing program',verification:'Actual syntax failure and exit captured',kind:'validation'},{id:'fix',title:'Implement minimal correction',verification:'main.py changed and subsequent program run passes',kind:'change'},{id:'test',title:'Run validation',verification:'Program exits zero and prints 42',kind:'validation'}]};
(async()=>{const browser=await chromium.launch({headless:true});const results=[];
try {
 const request=await browser.newContext();const cfg=await request.request.post(base+'/api/files/command',{data:{configure:true,roots:[fixtureRoot],projects:fixtureRoot}});assert.equal((await cfg.json()).ok,true);
 async function session(provider='openai') {
  const context=await browser.newContext({viewport:{width:1280,height:1080},reducedMotion:'reduce'});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let mode='chat',execution=0,plans=0,controls=0;const requests=[],launched=[],dispatched=[];
  await page.addInitScript(({provider,base})=>{if(window!==window.top)return;localStorage.setItem('jarvis.v7.settings',JSON.stringify({provider,altBase:base+'/mock/v1',altModel:'fixture-model',apiKey:'EXAMPLE_FOR_OFFLINE_TEST_ONLY',speak:false,wakeWord:false,autoListen:false,critique:'off',reviewWrites:'all',buildCheck:'on',fastModel:'',showThinking:true}));},{provider,base});
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.pathname==='/api/health')return route.fulfill({json:{files:true,launch:true}});
   if(u.pathname==='/api/open'){launched.push(route.request().postDataJSON());return route.fulfill({json:{ok:true}});}
   if(u.pathname==='/api/files/command'){const d=route.request().postDataJSON();dispatched.push(d.action);return route.continue();}
   if(u.pathname.endsWith('/chat/completions')||u.hostname==='api.anthropic.com') {
    const body=route.request().postDataJSON();const system=provider==='openai'?body.messages[0].content:body.system[0].text;
    requests.push({kind:system.startsWith('Return only JSON')?'control':'execution',tools:!!body.tools});
    let text='',calls=[];
    const quiet = system.startsWith('Return only JSON');
    if(system.includes('Create 2-8')){plans++;assert.equal(body.tools,undefined);text=JSON.stringify(mode==='readonly'?{title:'Inspect without modifying',steps:[{id:'check-source',title:'Read unchanged source',verification:'Actual source read',kind:'observation'},{id:'test',title:'Run validation',verification:'Program exit zero',kind:'validation'}]}:mode==='automatic'?{...plan,steps:plan.steps.filter(s=>s.id!=='reproduce')}:plan);}
    else if(system.includes('Evaluate the stated verification')){
     controls++;assert.equal(body.tools,undefined);const m=await page.evaluate(()=>J.agent.current());const done=[];const s=m.steps[m.currentStep];
     if(['coding','automatic','correction','readonly'].includes(mode)){
      const read=m.evidence.find(e=>e.tool==='files:read'&&e.success);
      const bad=m.evidence.find(e=>e.tool==='files:run'&&!e.success);
      const write=m.evidence.find(e=>e.kind==='change'&&e.success);
      const good=m.evidence.slice().reverse().find(e=>e.tool==='files:run'&&e.success);
      if(['inspect','check-source'].includes(s.id)&&read)done.push({id:s.id,summary:'Actual source read',evidence:[read.id]});
      if(s.id==='reproduce'&&bad)done.push({id:'reproduce',summary:'Syntax failure reproduced with a real nonzero exit',evidence:[bad.id]});
      if(s.id==='test'&&good)done.push({id:'test',summary:'Actual program exit 0; output 42',evidence:[good.id]});
      if(s.id==='fix'&&write&&good){done.push({id:'fix',summary:'Minimal correction validated: exit 0, output 42',evidence:[write.id,good.id]},{id:'test',summary:'Program exit 0; output 42',evidence:[good.id]});}
     }
     text=JSON.stringify({completed:done,decision:'continue',reason:'Only observed evidence used',plan:null});
    } else {
     execution++;
     if(mode==='coding'){
      const seq=[{action:'read',project:'fixture',path:'main.py'},{action:'run',project:'fixture',what:'python',entry:'main.py'},{action:'write',project:'fixture',path:'main.py',content:'print(6 * 7)\n'},{action:'run',project:'fixture',what:'python',entry:'main.py'}];
      if(execution<=seq.length)calls=[{id:'call-'+execution,name:'files',input:seq[execution-1]}];else text='Finished the verified repair.';
     } else if(mode==='automatic'||mode==='correction'||mode==='readonly'){
      const read={action:'read',project:'fixture',path:'main.py'},write={action:'write',project:'fixture',path:'main.py',content:'print(6 * 7)\n'},run={action:'run',project:'fixture',what:'python',entry:'main.py'};
      const seq=mode==='readonly'?[read,run]:[read,write];
      if(execution<=seq.length)calls=[{id:'call-'+execution,name:'files',input:seq[execution-1]}];else text='Check the observed outcome.';
     } else if(mode==='cancel-chain')calls=[{id:'read',name:'files',input:{action:'read',project:'fixture',path:'main.py'}},{id:'run',name:'files',input:{action:'run',project:'fixture',what:'python',entry:'main.py'}},{id:'write',name:'files',input:{action:'write',project:'fixture',path:'main.py',content:'print(99)\n'}}];
     else if(mode==='failure')calls=[{id:'fail-'+execution,name:'files',input:{action:'read',project:'fixture',path:'absent.py'}}];
     else if(mode==='approval'||mode==='cancel')calls=[{id:'write',name:'files',input:{action:'write',project:'fixture',path:'main.py',content:'print(99)\n'}}];
     else if(mode==='spotify'&&execution===1)calls=[{id:'open',name:'control_interface',input:{action:'open_app',query:'Spotify'}}];
     else text='DNS maps names to network addresses.';
    }
    let events;
    if(provider==='openai'){
     const delta=calls.length?{tool_calls:calls.map((c,index)=>({index,id:c.id,type:'function',function:{name:c.name,arguments:JSON.stringify(c.input)}}))}:{content:text};
     if(quiet)delta.reasoning='PRIVATE_PLAN_THOUGHT_FIXTURE';
     events=['data: '+JSON.stringify({model:'fixture-model',choices:[{index:0,delta}]}),'data: '+JSON.stringify({choices:[{index:0,delta:{},finish_reason:calls.length?'tool_calls':'stop'}]}),'data: [DONE]'].join('\n\n')+'\n\n';
    }else{
     const es=[{type:'message_start',message:{model:'fixture-model'}}];let i=0;
     if(quiet){es.push({type:'content_block_start',index:i,content_block:{type:'thinking',thinking:''}},{type:'content_block_delta',index:i,delta:{type:'thinking_delta',thinking:'PRIVATE_PLAN_THOUGHT_FIXTURE'}},{type:'content_block_stop',index:i});i++;}
     if(text){es.push({type:'content_block_start',index:i,content_block:{type:'text',text:''}},{type:'content_block_delta',index:i,delta:{type:'text_delta',text}},{type:'content_block_stop',index:i});i++;}
     for(const c of calls)es.push({type:'content_block_start',index:i,content_block:{type:'tool_use',id:c.id,name:c.name,input:{}}},{type:'content_block_delta',index:i,delta:{type:'input_json_delta',partial_json:JSON.stringify(c.input)}},{type:'content_block_stop',index:i++});
     es.push({type:'message_delta',delta:{stop_reason:calls.length?'tool_use':'end_turn'}});events=es.map(e=>'data: '+JSON.stringify(e)+'\n\n').join('');
    }
    return route.fulfill({status:200,contentType:'text/event-stream',body:events});
   }
   if(u.hostname==='localhost'||u.hostname==='127.0.0.1')return route.continue();
   return route.abort();
  });
  await page.goto(base);await page.waitForFunction(()=>window.J&&J.app&&!document.body.classList.contains('booting'));
  return {page,context,errors,requests,launched,dispatched,setMode:x=>{mode=x;execution=0;},counts:()=>({plans,controls,execution})};
 }
 // A/B: normal chat uses no planning, launch bridge remains the existing tool path.
 let x=await session();x.setMode('chat');await x.page.evaluate(()=>J.brain.send('Explain how DNS works.'));assert.equal(x.counts().plans,0);assert.equal(await x.page.locator('.mission-card').count(),0);results.push('B informational chat bypass: PASS');
 x.setMode('spotify');await x.page.evaluate(()=>J.brain.send('Open Spotify'));assert.equal(x.counts().plans,0);assert.ok(x.launched.length>=1);results.push('A Spotify existing launch bridge (mock desktop action): PASS');await x.context.close();
 for(const provider of ['openai','anthropic']){
  fs.writeFileSync(file,'print(6 * )\n');x=await session(provider);x.setMode('coding');
  const action=x.page.evaluate(()=>J.brain.send('Inspect this project, reproduce the error, fix it and run validation.'));
  await x.page.waitForFunction(()=>J.agent.current()?.status==='waiting_approval',{timeout:30000});
  assert.equal(fs.readFileSync(file,'utf8'),'print(6 * )\n');
  // Review UI owns buttons; click its real approve button.
  await x.page.getByRole('button',{name:/apply|approve/i}).first().click();
  await action;
  const m=await x.page.evaluate(()=>J.agent.current());assert.equal(m.status,'completed',JSON.stringify(m));assert.equal(await x.page.locator('.mission-card').count(),1);assert.equal(m.steps.filter(s=>s.status==='completed').length,4);assert.equal(fs.readFileSync(file,'utf8'),'print(6 * 7)\n');assert.ok(m.evidence.some(e=>e.exitCode!==null&&e.success));
  assert.equal(await x.page.locator('.trace').count(),0);assert.ok(!(await x.page.locator('#convo').innerText()).includes('"completed":'));assert.ok(!(await x.page.locator('#convo').innerText()).includes('PRIVATE_PLAN_THOUGHT_FIXTURE'));
  results.push('C real read → nonzero Python run → reviewed write → zero Python run, '+provider+': PASS');assert.deepEqual(x.errors,[]);await x.context.close();
 }
 x=await session();x.setMode('failure');await x.page.evaluate(()=>J.brain.send('Inspect this project, fix the issue and test it'));assert.equal(await x.page.evaluate(()=>J.agent.current().status),'blocked');assert.equal(x.dispatched.filter(a=>a==='read').length,1);results.push('D identical failed call blocked before second execution: PASS');await x.context.close();
 for(const mode of ['approval','cancel']){
  fs.writeFileSync(file,'print(42)\n');x=await session();x.setMode(mode);
  const action=x.page.evaluate(()=>J.brain.send('Inspect this project, fix the issue and test it'));
  await x.page.waitForFunction(()=>J.agent.current()?.status==='waiting_approval');
  if(mode==='cancel')await x.page.evaluate(()=>J.brain.send('cancel that'));
  else await x.page.getByRole('button',{name:/reject|discard/i}).first().click();
  await action;assert.equal(fs.readFileSync(file,'utf8'),'print(42)\n');assert.equal(await x.page.evaluate(()=>J.agent.current().status),mode==='cancel'?'cancelled':'blocked');assert.equal(x.dispatched.filter(a=>a==='apply').length,0);
  results.push((mode==='cancel'?'E cancellation':'F approval rejection')+' leaves fixture untouched: PASS');await x.context.close();
 }
 // Existing automatic build-check path is observed rather than replaced.
 fs.writeFileSync(file,'print(6 * )\n');x=await session();x.setMode('automatic');
 await x.page.evaluate(()=>{window.__checks=0;J.on('build-check',()=>window.__checks++);});
 let action=x.page.evaluate(()=>J.brain.send('Inspect this project, fix the error and validate it'));
 await x.page.waitForFunction(()=>J.agent.current()?.status==='waiting_approval');await x.page.getByRole('button',{name:'Apply',exact:true}).first().click();await action;
 assert.equal(await x.page.evaluate(()=>J.agent.current().status),'completed');assert.ok(await x.page.evaluate(()=>window.__checks)>0);assert.equal(x.dispatched.filter(a=>a==='run').length,1);results.push('Existing automatic build check supplies real validation evidence: PASS');await x.context.close();
 // Correction during approval preserves already verified inspection and discards the old write.
 fs.writeFileSync(file,'print(42)\n');x=await session();x.setMode('correction');
 action=x.page.evaluate(()=>J.brain.send('Inspect this project, fix the error and validate it'));
 await x.page.waitForFunction(()=>J.agent.current()?.status==='waiting_approval');const verified=await x.page.evaluate(()=>J.agent.current().steps[0]);assert.equal(verified.status,'completed');
 x.setMode('readonly');await x.page.evaluate(()=>J.brain.send("No, don't modify that file. Just inspect and validate it."));await action;
 await x.page.waitForFunction(()=>J.agent.current()?.revisions.length>0&&J.agent.current()?.status==='completed');
 assert.equal(fs.readFileSync(file,'utf8'),'print(42)\n');assert.equal(x.dispatched.filter(a=>a==='apply').length,0);assert.equal(await x.page.evaluate(()=>J.agent.current().steps[0].completedAt),verified.completedAt);results.push('Native correction preserves verified step and does not apply rejected file: PASS');await x.context.close();
 // Cancellation between dependent calls retains complete tool-result protocol history.
 x=await session();x.setMode('cancel-chain');await x.page.evaluate(()=>J.on('tool-start',t=>{if(t.name==='files'&&t.input?.action==='run')J.brain.abort();}));
 await x.page.evaluate(()=>J.brain.send('Inspect this project, fix the error and validate it'));assert.equal(await x.page.evaluate(()=>J.agent.current().status),'cancelled');assert.equal(x.dispatched.filter(a=>a==='run'||a==='write'||a==='propose').length,0);
 const paired=await x.page.evaluate(()=>J.brain.getHistory().filter(m=>m.role==='user'&&Array.isArray(m.content)&&m.content[0]?.type==='tool_result').at(-1));assert.equal(paired.content.length,3);results.push('Dependent cancellation skips remaining tools and keeps all tool result IDs paired: PASS');await x.context.close();
 // Responsive card uses the existing transcript; compare reactor/composer geometry to baseline.
 x=await session();const geometry=[];
 for(const width of [390,768,1280,1920]){
  await x.page.setViewportSize({width,height:1080});await x.page.reload();await x.page.waitForFunction(()=>window.J&&J.app&&!document.body.classList.contains('booting'));
  await x.page.evaluate(()=>J.brain.clearConversation());
  const before=await x.page.evaluate(()=>({reactor:document.querySelector('#orb').getBoundingClientRect().toJSON(),composer:document.querySelector('#input').getBoundingClientRect().toJSON()}));
  await x.page.evaluate(plan=>{J.agent.start(J.agent.normalizePlan(plan,'safe visual fixture'));J.agent.run();},plan);
  await x.page.waitForFunction(()=>Number(getComputedStyle(document.querySelector('.msg.mission')).opacity)>0.99);
  const after=await x.page.evaluate(()=>{const r=document.querySelector('#orb').getBoundingClientRect(),c=document.querySelector('.mission-card').getBoundingClientRect(),input=document.querySelector('#input').getBoundingClientRect();const overlap=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,reactor:r.toJSON(),composer:input.toJSON(),card:c.toJSON(),overlapReactor:overlap(r,c),overlapComposer:overlap(input,c)};});
  assert.equal(after.scrollWidth,width);assert.deepEqual(after.reactor,before.reactor);assert.equal(after.composer.width,before.composer.width);assert.equal(after.composer.height,before.composer.height);assert.ok(after.composer.bottom<=1080);if(width>900)assert.deepEqual(after.composer,before.composer);assert.equal(after.overlapReactor,false);assert.equal(after.overlapComposer,false);geometry.push(after);
  const out=process.env.JARVIS_TEST_ARTIFACTS;if(out)await x.page.screenshot({path:path.join(out,'phase1-'+width+'.png')});
 }
 results.push('Responsive mission card: 390/768/1280/1920, no reactor geometry change, horizontal overflow or overlap: PASS');
 await x.page.reload();await x.page.waitForFunction(()=>window.J&&J.app&&!document.body.classList.contains('booting'));assert.equal(await x.page.evaluate(()=>J.agent.current().status),'waiting');assert.equal(await x.page.evaluate(()=>J.brain.isBusy()),false);results.push('Native reload restores waiting without execution: PASS');assert.deepEqual(x.errors,[]);
 const report={results,geometry,liveModels:false,realAccountActions:false,fixtureRoot};if(process.env.JARVIS_TEST_ARTIFACTS)fs.writeFileSync(path.join(process.env.JARVIS_TEST_ARTIFACTS,'phase1-browser-results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 await x.context.close();await request.close();
} finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
