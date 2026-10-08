'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),cp=require('node:child_process');
const root=path.join(__dirname,'..'),app=path.join(root,'J.A.R.V.I.S. Dashboard - Copy'),fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/phase3-tools.json'),'utf8'));
const order=['tasks','spotify','knowledge','google','memory','vision','recall','files','preview','video','jobs','job_hunt','minecraft','lessons','translate','desktop','web'];
const snapshot={ok:true,schema_version:1,generation:1,health:{loaded:17},invalid:[],infrastructure:[],skills:order.map(id=>({...JSON.parse(fs.readFileSync(path.join(app,'skills',id,'skill.json'),'utf8')),enabled:true,status:'available',reason:'Trusted adapter installed.'}))};
const clone=value=>JSON.parse(JSON.stringify(value));
function node(){return{setAttribute(){},appendChild(){},append(){},addEventListener(){},replaceChildren(){},scrollIntoView(){},hidden:true};}
async function setup(options={}){
 const events={},calls=[];const J={settings:{provider:'openai',visionModel:'fixture',webSearch:true},tasks:{background:()=>false},load:(k,d)=>d,emit:(name,data)=>{calls.push([name,data]);(events[name]||[]).forEach(fn=>fn(data));},on:(name,fn)=>(events[name]??=[]).push(fn),permissions:{dispatch:async(name,input,run)=>{calls.push(['permission',name,input]);return options.deny?'FAILED - denied':run();},skillsCommand:async()=>{}},toast:()=>{}};
 let remoteDisabled=false,outage=false;
 const fetch=async url=>{if(outage)throw Error('offline');return{ok:!remoteDisabled,json:async()=>url.includes('/tool/')?{ok:!remoteDisabled,skill:snapshot.skills.find(s=>s.tools.some(t=>url.endsWith(t.name)))}:clone(snapshot)};};
 const window={J,addEventListener(){}};const ctx={window,document:{createElement:node,getElementById:()=>null},fetch,localStorage:{setItem(){}},setTimeout,clearTimeout,console};
 vm.runInNewContext(fs.readFileSync(path.join(app,'js/skills.js'),'utf8'),ctx);vm.runInNewContext(fs.readFileSync(path.join(app,'js/skill-adapters.js'),'utf8'),ctx);
 await J.skills.load();J.skills.setCapabilities(Object.fromEntries(['tasks','spotify','knowledge','google','memory','vision','recall','files','video','jobs','hunt','minecraft','lessons','desktop','search','fetch'].map(k=>[k,true])));
 return {J,ctx,calls,disableRemote:()=>{remoteDisabled=true;},outage:()=>{outage=true;}};
}
function instrumentBrain(source,J){const ctx={window:{J},AbortController,TextDecoder,URLSearchParams,setTimeout,clearTimeout};vm.runInNewContext(source.replace('})(window.J);','J.testTools={toolList,toOpenAITools}; })(window.J);'),ctx);return J.testTools;}
const original=cp.execFileSync('git',['-C',root,'show','9d55e7c18660f99045471076a988d6e9b010edd7:J.A.R.V.I.S. Dashboard - Copy/js/brain.js'],{encoding:'utf8'});
test('captured schemas are exactly the immutable Phase 3 definitions',()=>{
 const names=[...original.matchAll(/  const ([A-Z_]+_TOOL) = \{/g)].map(m=>m[1]),J={load:(k,d)=>d,on:()=>{},settings:{}};
 vm.runInNewContext(original.replace('})(window.J);',`J.snapshot={CLIENT_TOOLS,WEB_TOOLS,${names.join(',')}};})(window.J);`),{window:{J}});
 for(const [key,value] of Object.entries(J.snapshot))assert.deepEqual(clone(value),fixture[key]);
});
test('OpenAI and Anthropic tool lists preserve all schemas and exact order across equivalent capabilities',async()=>{
 const x=await setup(),brain=instrumentBrain(fs.readFileSync(path.join(app,'js/brain.js'),'utf8'),x.J),keys=['TASK_TOOL','SPOTIFY_TOOL','KNOWLEDGE_TOOL','GOOGLE_TOOL','MEMORY_TOOL','VISION_TOOL','RECALL_TOOL','FILES_TOOL','PREVIEW_TOOL','VIDEO_TOOL','JOBS_TOOL','HUNT_TOOL','MINECRAFT_TOOL','LESSONS_TOOL','TRANSLATE_TOOL','DESKTOP_TOOL'];
 const expected=[...fixture.CLIENT_TOOLS,...keys.map(k=>fixture[k]),...fixture.WEB_TOOLS];assert.equal(expected.length,21);assert.deepEqual(clone(brain.toolList()),expected);
 assert.deepEqual(clone(brain.toOpenAITools()),expected.map(t=>({type:'function',function:{name:t.name,description:t.description,parameters:t.input_schema}})));
 x.J.settings.provider='anthropic';assert.deepEqual(clone(brain.toolList()),[...expected.slice(0,-2),{type:'web_search_20260209',name:'web_search',max_uses:8},{type:'web_fetch_20260209',name:'web_fetch',max_uses:5}]);
 x.J.settings.visionModel='';assert.equal(brain.toolList().some(t=>t.name==='see_screen'),false);assert.equal(brain.toolList().some(t=>t.name==='see_preview'),false);
 x.J.settings.webSearch=false;assert.equal(brain.toolList().some(t=>t.name==='web_search'),false);x.J.tasks.background=()=>true;assert.equal(brain.toolList().some(t=>t.name==='tasks'),false);
});
test('every adapter uses the same existing handler and original argument shape',async()=>{
 const x=await setup(),seen=[],h={};for(const name of ['spotify','runLookup','googleCmd','memoryCmd','seeScreen','recallCmd','videoCmd','jobsCmd','huntCmd','simpleCmd','lessonsCmd','translateCmd','seePreview','guardedWrite','filesCmd','desktopCmd','runSearch','runFetch'])h[name]=(...args)=>{seen.push([name,args]);return 'Actual '+name;};x.J.brain={skillRuntime:h};x.J.tasks.modelCommand=(...args)=>{seen.push(['modelCommand',args]);return 'Actual task';};
 const i={action:'read',query:'query',value:42,source:'clipboard',text:'text',when:'tomorrow',question:'question',folder:'folder',project:'fixture',path:'main.py',width:390,height:800,to:'Spanish'};
 const cases=[['tasks','modelCommand',[i,'trusted objective']],['spotify','spotify',['read','query',42]],['lookup','runLookup',['clipboard','query']],['google','googleCmd',['read','query']],['reminders','memoryCmd',['read','text','tomorrow']],['see_screen','seeScreen',['question']],['recall','recallCmd',['read','query']],['files','filesCmd',[i]],['see_preview','seePreview',['fixture','question','main.py',390,800]],['video','videoCmd',[i]],['jobs','jobsCmd',[i]],['job_hunt','huntCmd',[i]],['minecraft','simpleCmd',['api/minecraft/command',i,'minecraft']],['lessons','lessonsCmd',[i]],['translate','translateCmd',['clipboard','Spanish']],['desktop','desktopCmd',['read','text']],['web_search','runSearch',['query']],['web_fetch','runFetch',[undefined]]];
 for(const [tool,name,args]of cases){seen.length=0;await x.J.skills.execute(tool,i,{userText:'trusted objective'});assert.deepEqual(seen,[[name,args]],tool);assert.ok(x.calls.some(c=>c[0]==='permission'&&c[1]===tool));}
 seen.length=0;await x.J.skills.execute('files',{action:'write'},{userText:'trusted objective'});assert.equal(seen[0][0],'guardedWrite');seen.length=0;await x.J.skills.execute('recall',{action:'index',folder:'folder'},{});assert.deepEqual(seen,[['recallCmd',['index',null,'folder']]]);
});
test('denial and disabled stale-tab lookup prevent adapter execution',async()=>{
 const x=await setup({deny:true});let calls=0;x.J.brain={skillRuntime:{filesCmd:()=>{calls++;}}};assert.match(await x.J.skills.execute('files',{action:'read'},{}),/^FAILED/);assert.equal(calls,0);
 const y=await setup();y.J.brain=x.J.brain;y.disableRemote();assert.match(await y.J.skills.execute('files',{action:'read'},{}),/^FAILED/);assert.equal(calls,0);assert.equal(y.calls.some(c=>c[0]==='permission'),false);
});
test('registry outage retains core schemas and blocks integration dispatch without hidden fallback',async()=>{
 const x=await setup(),brain=instrumentBrain(fs.readFileSync(path.join(app,'js/brain.js'),'utf8'),x.J);x.outage();await assert.rejects(x.J.skills.load(true));assert.deepEqual(clone(brain.toolList()),fixture.CLIENT_TOOLS);assert.match(await x.J.skills.execute('files',{action:'read'},{}),/^FAILED/);
});
test('schema snapshots are stable and caller mutation cannot change cached tools',async()=>{const x=await setup(),first=x.J.skills.tools();first[0].name='mutated';assert.equal(x.J.skills.tools()[0].name,'tasks');assert.deepEqual(x.J.skills.tools(),x.J.skills.tools());});
test('unknown adapters and adapter exceptions fail with FAILED',async()=>{const x=await setup();x.J.brain={skillRuntime:{filesCmd:()=>{throw Error('controlled failure');}}};assert.match(await x.J.skillAdapters.execute('../../evil',{},{}),/^FAILED/);assert.match(await x.J.skills.execute('files',{action:'read'},{}),/^FAILED/);});
test('existing handler implementations are unchanged from immutable Phase 3',async()=>{
 const x=await setup();instrumentBrain(fs.readFileSync(path.join(app,'js/brain.js'),'utf8'),x.J);const current=x.J.brain.skillRuntime,names=Object.keys(current),J={load:(k,d)=>d,on:()=>{},settings:{}};
 vm.runInNewContext(original.replace('})(window.J);',`J.handlers={${names.join(',')}};})(window.J);`),{window:{J}});
 for(const name of names)assert.equal(current[name].toString().replace(/\r\n/g,'\n'),J.handlers[name].toString().replace(/\r\n/g,'\n'),name);
});
test('tool availability is equivalent across provider, settings, background and capability masks',async()=>{
 const x=await setup(),current=instrumentBrain(fs.readFileSync(path.join(app,'js/brain.js'),'utf8'),x.J);
 const flags={tasks:'hasTasks',spotify:'hasSpotify',knowledge:'hasKnowledge',google:'hasGoogle',memory:'hasMemory',vision:'hasVision',recall:'hasRecall',files:'hasFiles',video:'hasVideo',jobs:'hasJobs',hunt:'hasHunt',minecraft:'hasMinecraft',lessons:'hasLessons',desktop:'hasDesktop',search:'localSearch',fetch:'localSearch'};
 const J={load:(k,d)=>d,on:()=>{},settings:x.J.settings,tasks:{tool:fixture.TASK_TOOL,background:()=>false}};
 const setter=Object.entries(flags).filter(([key])=>key!=='fetch').map(([key,value])=>value+'=!!c.'+key+';').join('');
 vm.runInNewContext(original.replace('})(window.J);',`J.previous={toolList,setCaps:c=>{${setter}}};})(window.J);`),{window:{J}});
 const all=Object.fromEntries(Object.keys(flags).map(key=>[key,true])),masks=[all,Object.fromEntries(Object.keys(flags).map(key=>[key,false])),...Object.keys(flags).map(key=>({...all,[key]:false}))];
 for(const provider of ['openai','anthropic'])for(const visionModel of ['fixture',''])for(const webSearch of [true,false])for(const background of [true,false])for(const mask of masks){
   x.J.settings.provider=provider;x.J.settings.visionModel=visionModel;x.J.settings.webSearch=webSearch;x.J.tasks.background=J.tasks.background=()=>background;x.J.skills.setCapabilities(mask);J.previous.setCaps(mask);
   const actual=clone(current.toolList()),expected=clone(J.previous.toolList()),description=JSON.stringify({provider,visionModel,webSearch,background,mask});
   assert.deepEqual(actual.map(t=>t.name),expected.map(t=>t.name),description);assert.deepEqual(actual,expected,description);
 }
});
