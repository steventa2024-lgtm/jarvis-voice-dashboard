/* Native broker integration. Real HTTP/SQLite/file tools; controlled provider responses. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const base=process.env.JARVIS_TEST_URL||'http://localhost:18764',folder=process.env.JARVIS_PERMISSION_FIXTURE;
if(!folder)throw new Error('Use the disposable permission native server and set JARVIS_PERMISSION_FIXTURE.');
const file=path.join(folder,'fixture','main.py');fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,'print(42)\n');
const plan={title:'Safe fixture mission',steps:[{id:'inspect',title:'Inspect source',verification:'Real source read',kind:'observation'},{id:'fix',title:'Correct source',verification:'Real change and later passing run',kind:'change'},{id:'test',title:'Validate',verification:'Real run exits zero',kind:'validation'}]};
(async()=>{const browser=await chromium.launch({headless:true}),control=await browser.newContext(),results=[];let current;
const record=value=>{results.push(value);console.log(value)};
async function task(action,data={}){const r=await control.request.post(base+'/api/tasks/command',{data:{action,...data}});const d=await r.json();assert.equal(d.ok,true,JSON.stringify(d));return d;}
async function eventually(test){const end=Date.now()+20000;while(Date.now()<end){if(await test())return;await new Promise(r=>setTimeout(r,100));}assert.fail('Timed out');}
async function page(){const context=await browser.newContext({viewport:{width:1280,height:1080},reducedMotion:'reduce'}),page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));let count=0,mode='write';
await page.addInitScript(base=>{if(window!==window.top)return;localStorage.setItem('jarvis.v7.settings',JSON.stringify({provider:'openai',altBase:base+'/mock/v1',altModel:'fixture-model',apiKey:'EXAMPLE_FOR_OFFLINE_TEST_ONLY',speak:false,wakeWord:false,autoListen:false,critique:'off',reviewWrites:'off',buildCheck:'off',fastModel:''}));},base);
await page.route('**/*',async route=>{const u=new URL(route.request().url());
if(u.pathname==='/api/health')return route.fulfill({json:{tasks:true,files:true}});
if(u.pathname.endsWith('/chat/completions')){const body=route.request().postDataJSON(),system=body.messages[0].content;let text='',calls=[];
 if(system.includes('Create 2-8'))text=JSON.stringify(plan);
 else if(system.includes('Evaluate the stated verification')){const m=await page.evaluate(()=>J.agent.current()),s=m.steps[m.currentStep],good=m.evidence.filter(e=>e.success),completed=[];
 const read=good.find(e=>e.tool==='files:read'),write=good.find(e=>e.kind==='change'),run=good.find(e=>e.tool==='files:run');
 if(s?.id==='inspect'&&read)completed.push({id:s.id,summary:'Actual source read',evidence:[read.id]});
 if(s?.id==='fix'&&write&&run)completed.push({id:s.id,summary:'Actual fix validated',evidence:[write.id,run.id]},{id:'test',summary:'Exit zero',evidence:[run.id]});
 if(s?.id==='test'&&run)completed.push({id:s.id,summary:'Exit zero',evidence:[run.id]});
 text=JSON.stringify({completed,decision:'continue',reason:'Observed evidence'});
 }else {const m=await page.evaluate(()=>J.agent.current());count++;
 if(m?.currentStep===0)calls=[{name:'files',input:{action:'read',project:'fixture',path:'main.py'}}];
 else if(m?.currentStep===1&&!m.evidence.some(e=>e.kind==='change'&&e.success))calls=[{name:'files',input:{action:'write',project:'fixture',path:'main.py',content:'print(6 * 7)\n'}}];
 else if(m&&m.status!=='completed')calls=[{name:'files',input:{action:'run',project:'fixture',what:'python',entry:'main.py'}}];
 else text='Verified fixture result.';
 }
const delta=calls.length?{tool_calls:calls.map((call,index)=>({index,id:'call-'+count,type:'function',function:{name:call.name,arguments:JSON.stringify(call.input)}}))}:{content:text};
return route.fulfill({contentType:'text/event-stream',body:['data: '+JSON.stringify({model:'fixture-model',choices:[{index:0,delta}]}),'data: '+JSON.stringify({choices:[{index:0,delta:{},finish_reason:calls.length?'tool_calls':'stop'}]}),'data: [DONE]'].join('\n\n')+'\n\n'});
}if(['localhost','127.0.0.1'].includes(u.hostname))return route.continue();return route.abort();});
await page.goto(base);await page.waitForFunction(()=>window.J?.permissions&&J.app&&!document.body.classList.contains('booting'));
await page.evaluate(()=>{window.__speech=[];for(const name of ['feed','flush','say'])J.voice[name]=()=>window.__speech.push(name);speechSynthesis.speak=()=>window.__speech.push('synthesis');});
return {page,context,errors};}
async function choice(p,target,label){await p.locator('.permission-card').filter({hasText:target}).getByRole('button',{name:label,exact:true}).first().click();}
async function allowOnce(p){await choice(p,'fixture/main.py','Allow once');}
try{
 current=await page();let p=current.page;
 const read=await p.evaluate(async()=>{const r=await fetch('api/files/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action:'read',project:'fixture',path:'main.py'})});return r.json();});assert.equal(read.ok,true);record('Allowed read executes through real server and produces audit: PASS');
 let work=p.evaluate(()=>J.brain.send('Inspect this fixture project, fix its source and run the tests.'));
 await p.waitForFunction(()=>J.agent.current()?.status==='waiting_approval');assert.equal(fs.readFileSync(file,'utf8'),'print(42)\n');
 await allowOnce(p);await p.locator('.permission-card').filter({hasText:'EXECUTE_CODE'}).getByRole('button',{name:'Allow once',exact:true}).click();await work;assert.equal(fs.readFileSync(file,'utf8'),'print(6 * 7)\n');const result=await p.evaluate(()=>J.agent.current());if(result.status!=='completed')console.log(JSON.stringify(result,null,2));assert.equal(result.status,'completed');record('ASK suspends real Phase 1 tool; allow once resumes exact write and authorized code validation: PASS');
 // Server enforces even when browser interception is omitted.
 const raw=await control.request.post(base+'/api/files/command',{data:{action:'write',project:'fixture',path:'other.py',content:'print(1)'}});assert.equal(raw.status(),403);assert.equal(fs.existsSync(path.join(folder,'fixture','other.py')),false);record('Direct protected HTTP request cannot bypass broker: PASS');
 await p.evaluate(()=>J.agent.cancel());await current.context.close();current=await page();p=current.page;
 work=p.evaluate(async()=>{await J.permissions.api('context',{context:{background:false}});const r=await fetch('api/files/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action:'write',project:'fixture',path:'session-a.txt',content:'A'})});return r.json();});
 await choice(p,'session-a.txt','Allow this session');assert.equal((await work).ok,true);
 const sessionWrite=await p.evaluate(async()=>{const r=await fetch('api/files/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action:'write',project:'fixture',path:'session-b.txt',content:'B'})});return r.json();});assert.equal(sessionWrite.ok,true);record('Session project grant covers next matching file without a new general prompt: PASS');
 await p.reload();await p.waitForFunction(()=>window.J?.permissions&&J.app&&!document.body.classList.contains('booting'));
 work=p.evaluate(async()=>{const r=await fetch('api/files/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action:'write',project:'fixture',path:'deny.txt',content:'D'})});return r.json();});
 await choice(p,'deny.txt','Deny once');assert.equal((await work).ok,false);assert.equal(fs.existsSync(path.join(folder,'fixture','deny.txt')),false);record('Page restart invalidates session grant; denial performs no write: PASS');
 work=p.evaluate(async()=>{const r=await fetch('api/files/command',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action:'write',project:'fixture',path:'persistent.txt',content:'P'})});return r.json();});
 await choice(p,'persistent.txt','Allow this target');assert.equal((await work).ok,true);
 await p.reload();await p.waitForFunction(()=>window.J?.permissions&&J.app&&!document.body.classList.contains('booting'));
 let state=await p.evaluate(async()=>{await J.permissions.show();return J.permissions.api('list');});const grant=state.policies.find(x=>x.capability==='WRITE_FILE'&&x.scope_value.endsWith('/fixture'));assert.ok(grant);
 await p.evaluate(id=>J.permissions.api('delete_policy',{id}),grant.id);record('Persistent target grant survives page restart and can be revoked through trusted UI API: PASS');
 await p.evaluate(()=>J.permissions.api('create_policy',{capability:'EXECUTE_CODE',effect:'ALLOW',scope_type:'target',scope_value:'fixture'}));
 // Reload a live waiting mission. Exact action is server recovery state, not reconstructed by the model.
 fs.writeFileSync(file,'print(42)\n');work=p.evaluate(()=>J.brain.send('Inspect this fixture project, fix its source and run the tests.')).catch(()=>{});
 await p.waitForFunction(()=>J.agent.current()?.status==='waiting_approval');const mission=await p.evaluate(()=>J.agent.current().id);
 await p.reload();await work;await p.waitForFunction(()=>window.J?.permissions&&J.app&&!document.body.classList.contains('booting'));assert.equal(await p.evaluate(()=>J.agent.current().status),'waiting_approval');assert.equal(fs.readFileSync(file,'utf8'),'print(42)\n');
 await p.evaluate(()=>J.permissions.show());await allowOnce(p);await eventually(async()=>fs.readFileSync(file,'utf8')==='print(6 * 7)\n');assert.equal(await p.evaluate(()=>J.agent.current().id),mission);record('Reload while waiting restores approval; same mission executes exact stored action only after user approval: PASS');
 await p.evaluate(()=>J.agent.cancel());await current.context.close();current=await page();p=current.page;
 // Scheduled task must remain silent during permission waiting and final receipt.
 fs.writeFileSync(file,'print(42)\n');const scheduled=(await task('create',{title:'Broker safe scheduled write',objective:'Inspect the fixture project, fix source and validate.',schedule:{type:'once',at:Date.now()/1000+1},run_policy:'reviewed_writes'})).task;
 await new Promise(r=>setTimeout(r,1100));work=p.evaluate(()=>J.tasks.poll());await p.waitForFunction(()=>J.agent.current()?.status==='waiting_approval');
 await eventually(async()=>(await task('history',{task:scheduled.id})).runs[0]?.status==='waiting_approval');assert.equal(fs.readFileSync(file,'utf8'),'print(42)\n');
 // Background writes preserve exact diff review, then broker permission on apply.
 await p.getByRole('button',{name:'Apply',exact:true}).first().click();await p.getByRole('button',{name:'Allow once',exact:true}).first().click();await work;
 await eventually(async()=>(await task('history',{task:scheduled.id})).runs[0]?.status==='completed');let run=(await task('history',{task:scheduled.id})).runs[0];assert.equal(run.status,'completed');assert.deepEqual(await p.evaluate(()=>window.__speech),[]);
 const notices=await (await control.request.post(base+'/api/memory/command',{data:{action:'notices'}})).json();assert.equal(notices.notices.filter(n=>n.source.endsWith(run.id)&&n.text.includes('waiting')).length,1);record('Scheduled diff review + broker ASK → exact apply → same durable run completes; one waiting notice and no TTS: PASS');
 // Waiting durable action survives refresh; approved exact call executes before Phase 1 continuation.
 fs.writeFileSync(file,'print(42)\n');const interrupted=(await task('create',{title:'Broker resume fixture',objective:'Inspect fixture, fix source and validate.',schedule:{type:'once',at:Date.now()/1000+1},run_policy:'reviewed_writes'})).task;
 await new Promise(r=>setTimeout(r,1100));work=p.evaluate(()=>J.tasks.poll()).catch(()=>{});await p.getByRole('button',{name:'Apply',exact:true}).first().click();await p.getByRole('button',{name:'Allow once',exact:true}).first().waitFor();
 const prior=(await task('history',{task:interrupted.id})).runs[0];await new Promise(r=>setTimeout(r,800));await p.reload();await work;await p.waitForFunction(()=>window.J?.permissions&&J.app&&!document.body.classList.contains('booting'));
 await eventually(async()=>(await task('get_run',{run_id:prior.id})).run.status==='interrupted');await p.evaluate(()=>{window.__speech=[];for(const name of ['feed','flush','say'])J.voice[name]=()=>window.__speech.push(name);speechSynthesis.speak=()=>window.__speech.push('synthesis');J.permissions.show();});
 await allowOnce(p);await eventually(async()=>(await task('get_run',{run_id:prior.id})).run.status==='completed');const resumed=(await task('get_run',{run_id:prior.id})).run;assert.equal(resumed.mission_id,prior.mission_id);assert.deepEqual(await p.evaluate(()=>window.__speech),[]);record('Durable approval reload/resume retains run and mission identity, executes exact held action, remains silent: PASS');
 for(const width of [390,768,1280,1920]){await p.setViewportSize({width,height:1080});await p.waitForTimeout(800);const before=await p.evaluate(()=>({scrollY,orb:document.querySelector('#orb').getBoundingClientRect().toJSON(),input:document.querySelector('#input').getBoundingClientRect().toJSON()}));await p.evaluate(()=>J.permissions.show());await p.locator('.permission-surface').waitFor({state:'visible'});await p.waitForTimeout(400);
 const after=await p.evaluate(()=>({scrollY,width:innerWidth,scroll:document.documentElement.scrollWidth,orb:document.querySelector('#orb').getBoundingClientRect().toJSON(),input:document.querySelector('#input').getBoundingClientRect().toJSON(),panel:document.querySelector('.permission-surface').getBoundingClientRect().toJSON()}));const overlap=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;assert.equal(after.scroll,width);assert.equal(after.orb.width,before.orb.width);assert.equal(after.orb.height,before.orb.height);assert.ok(Math.abs(after.orb.top+after.scrollY-before.orb.top-before.scrollY)<1);assert.equal(overlap(after.panel,after.orb),false);assert.equal(overlap(after.panel,after.input),false);await p.locator('#permissionsSurface').getByRole('button',{name:'close',exact:true}).click();}
 record('Permission surface at 390/768/1280/1920: no overflow, reactor/composer overlap or geometry change: PASS');assert.deepEqual(current.errors,[]);console.log(JSON.stringify({pass:true,tests:results.length,results},null,2));
}finally{if(current)await current.context.close();await control.close();await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
