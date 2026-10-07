'use strict';
const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm'), path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../J.A.R.V.I.S. Dashboard - Copy/js/agent.js'),'utf8');
function setup(saved) {
 const store={mission:saved},listeners={};const J={load:(key,fallback)=>store[key]??fallback,save:(key,value)=>store[key]=JSON.parse(JSON.stringify(value)),on:(name,fn)=>(listeners[name]??=[]).push(fn),emit:(name,value)=>(listeners[name]||[]).forEach(fn=>fn(value))};
 vm.runInNewContext(source,{window:{J},Date,Math,Set,Map,JSON,Object,String,Array,Number,Error});return {J,a:J.agent,store};
}
const plan={title:'Safe check',steps:[{id:'read',title:'Read source',verification:'Actual read',kind:'observation'},{id:'test',title:'Run validation',verification:'Exit zero',kind:'validation'}]};
test('scheduled objectives force the existing planner even when ordinary heuristic bypasses',async()=>{
 const {a}=setup();let plans=0;a.attachRun({taskId:'task',runId:'run'});await a.prepare('Check calendar and weather',async()=>{plans++;return plan;},['google'],true);assert.equal(plans,1);assert.equal(a.current().durable.runId,'run');assert.equal(a.current().status,'executing');
});
test('durable snapshot import preserves verified work but cannot dispatch until explicit Phase 1 resume',async()=>{
 const {a}=setup();a.attachRun({taskId:'task',runId:'run'});a.start(a.normalizePlan(plan,'Safe check'));a.run();const call={id:'r',name:'files',input:{action:'read'}};a.beforeTool(call);const proof=a.recordToolResult(call,'Actual contents');a.verifyStep('read','Actual read',[proof.id]);const saved=a.current();a.detachRun();const x=setup();x.a.attachRun({taskId:'task',runId:'run'});assert.equal(x.a.importSnapshot(saved),true);assert.equal(x.a.current().steps[0].status,'completed');assert.equal(x.a.current().steps[0].completedAt,saved.steps[0].completedAt);assert.equal(x.a.executing(),false);assert.equal(x.a.beforeTool({id:'w',name:'files',input:{action:'write'}}),false);await x.a.prepare('Resume the mission',()=>assert.fail('Must reuse saved plan'),['files'],true);assert.equal(x.a.current().currentStep,1);assert.equal(x.a.current().status,'executing');
});
test('snapshot for another run is refused',()=>{const {a}=setup();a.attachRun({taskId:'task',runId:'new'});assert.equal(a.importSnapshot({version:1,steps:[{}],durable:{runId:'old'}}),false);});
test('scheduled policy blocks before evidence or execution and leaves foreground semantics unchanged',()=>{
 const {a,J}=setup();a.start(a.normalizePlan(plan,'safe'));a.run();J.tasks={allowTool:()=>false};assert.equal(a.beforeTool({id:'w',name:'files',input:{action:'write'}}),false);assert.equal(a.current().status,'blocked');assert.equal(a.current().evidence.length,0);
 const x=setup();x.a.start(x.a.normalizePlan(plan,'safe'));x.a.run();assert.equal(x.a.beforeTool({id:'w',name:'files',input:{action:'write'}}),true);
});
test('interrupted durable reload cannot automatically run and keeps run link',()=>{
 const {a,store}=setup();a.attachRun({taskId:'task',runId:'run'});a.start(a.normalizePlan(plan,'safe'));a.run();const restored=setup(store.mission);assert.equal(restored.a.current().status,'waiting');assert.equal(restored.a.current().durable.runId,'run');assert.equal(restored.a.executing(),false);
});
test('completed queued work still requires original Phase 1 validation evidence',()=>{
 const {a}=setup();a.attachRun({taskId:'task',runId:'run'});a.start(a.normalizePlan(plan,'safe'));a.run();assert.equal(a.verifyStep('read','Pretend read',['invented']),false);assert.equal(a.current().steps[0].status,'running');
});

test('worker interruption stays distinct from user cancellation and preserves verified steps',()=>{
 const {a}=setup();a.attachRun({taskId:'task',runId:'run'});a.start(a.normalizePlan(plan,'safe'));a.run();const call={id:'r',name:'files',input:{action:'read'}};a.beforeTool(call);const proof=a.recordToolResult(call,'Actual read');a.verifyStep('read','Actual read',[proof.id]);const completedAt=a.current().steps[0].completedAt;a.cancel();assert.equal(a.current().status,'cancelled');a.interrupt();assert.equal(a.current().status,'waiting');assert.equal(a.current().steps[0].completedAt,completedAt);assert.equal(a.executing(),false);
});

const workerSource=fs.readFileSync(path.join(__dirname,'../J.A.R.V.I.S. Dashboard - Copy/js/tasks.js'),'utf8');
function workerSetup(shared={}) {
 const events={},requests=[];const J={load:(key,fallback)=>shared[key]??fallback,save:(key,value)=>shared[key]=value,on:(name,callback)=>(events[name]??=[]).push(callback)};
 const document={getElementById:()=>null,addEventListener:()=>{}};
 vm.runInNewContext(workerSource,{window:{J},document,Date,Math,JSON,Object,String,Array,Number,Error,Promise,setTimeout,clearTimeout,setInterval,clearInterval,AbortController,
   fetch:async(url,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>({ok:true,task:{id:'fixture'}})};}});
 return {J,requests};
}
test('scheduling detection preserves informational questions and ordinary task listing',()=>{
 const {J}=workerSetup();for(const text of ["what's the weather tomorrow",'Explain how DNS works.','Show my scheduled tasks.','Open Spotify','Tell me about scheduling algorithms'])assert.equal(J.tasks.schedulingIntent(text),false,text);
 for(const text of ['Every morning at 8 check my calendar and weather','Jarvis, run this project check tonight at 8','Tomorrow, inspect my project and run the tests','Can you schedule a morning brief?'])assert.equal(J.tasks.schedulingIntent(text),true,text);
});
test('model task tool omits runtime mechanics and requires trusted scheduling intent',async()=>{
 const {J,requests}=workerSetup();assert.ok(!J.tasks.tool.input_schema.properties.action.enum.includes('claim'));assert.ok(!J.tasks.tool.input_schema.properties.action.enum.includes('heartbeat'));
 assert.match(await J.tasks.modelCommand({action:'claim'},'Show tasks'),/^FAILED/);assert.match(await J.tasks.modelCommand({action:'create'},'Research the webpage'),/^FAILED/);assert.equal(requests.length,0);
});
test('repeated model creation has one idempotency key per user turn',async()=>{
 const {J,requests}=workerSetup();const input={action:'create',title:'Brief',objective:'Check calendar',schedule:'every morning at 8'};
 J.tasks.beginUserTurn();await J.tasks.modelCommand(input,'Every morning at 8 check calendar');await J.tasks.modelCommand(Object.fromEntries(Object.entries(input).reverse()),'Every morning at 8 check calendar');assert.equal(requests[0].request_key,requests[1].request_key);
 J.tasks.beginUserTurn();await J.tasks.modelCommand(input,'Every morning at 8 check calendar');assert.notEqual(requests[1].request_key,requests[2].request_key);
});
test('worker base survives while concurrent tab identities differ',()=>{
 const shared={};const first=workerSetup(shared),second=workerSetup(shared);assert.ok(first.J.tasks.diagnostics().worker.startsWith(shared['task-worker']));assert.ok(second.J.tasks.diagnostics().worker.startsWith(shared['task-worker']));assert.notEqual(first.J.tasks.diagnostics().worker,second.J.tasks.diagnostics().worker);
});

test('fully verified checkpoint can finish receipt delivery without replay after interruption',()=>{
 const {a}=setup();a.attachRun({taskId:'task',runId:'run'});a.start(a.normalizePlan(plan,'safe'));a.run();let call={id:'read',name:'files',input:{action:'read',project:'fixture'}};a.beforeTool(call);let proof=a.recordToolResult(call,'Actual read');a.verifyStep('read','Actual read',[proof.id]);call={id:'run',name:'files',input:{action:'run',project:'fixture'}};a.beforeTool(call);a.observeFiles(call.input,{ok:true,exit:0,summary:'Actual exit zero'});proof=a.recordToolResult(call,'Actual exit zero');a.verifyStep('test','Actual exit zero',[proof.id]);assert.equal(a.current().status,'completed');const saved=a.current();const resumed=setup();resumed.a.attachRun({taskId:'task',runId:'run'});assert.equal(resumed.a.importSnapshot(saved),true);assert.equal(resumed.a.current().status,'completed');assert.equal(resumed.a.current().id,saved.id);assert.equal(resumed.a.beforeTool({id:'repeat',name:'files',input:{action:'run'}}),false);assert.equal(resumed.a.receipt().completed,2);
});
