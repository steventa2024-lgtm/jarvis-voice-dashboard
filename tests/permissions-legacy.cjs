/* Unchanged Phase 1/2 native harnesses, with explicit trusted fixture permissions.
   This adapter grants only the disposable fixture project; assertions are untouched. */
'use strict';
const path=require('node:path'),os=require('node:os');
const pw=require(process.env.PLAYWRIGHT_MODULE||path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const originalLaunch=pw.chromium.launch.bind(pw.chromium);
pw.chromium.launch=async options=>{
 const browser=await originalLaunch(options),originalContext=browser.newContext.bind(browser);
 browser.newContext=async options=>{
  const context=await originalContext(options),post=context.request.post.bind(context.request);
  const newPage=context.newPage.bind(context);
  context.newPage=async()=>{
   const page=await newPage(),prototype=Object.getPrototypeOf(page.locator('body'));
   if(!prototype.__jarvisPermissionWait){
    const click=prototype.click;
    prototype.click=async function(options){
     const label=await this.evaluate(node=>node.closest('.durable-task')&&node.tagName==='BUTTON'?node.textContent.trim():'');
     const action={'Pause':'pause','Resume schedule':'resume','Cancel schedule':'cancel'}[label];
     let completed;
     if(action){completed=this.page().waitForResponse(response=>{if(!response.url().endsWith('/api/tasks/command'))return false;try{return response.request().postDataJSON().action===action;}catch(e){return false;}});completed.catch(()=>{});}
     const result=await click.call(this,options);
     if(completed)await completed;
     return result;
    };
    prototype.__jarvisPermissionWait=true;
   }
   return page;
  };
  context.request.post=async(url,opts)=>{
   if(String(url).endsWith('/api/files/command')&&opts?.data?.configure){
    const base=String(url).slice(0,-'/api/files/command'.length),session=(await (await post(base+'/api/permissions/command',{data:{action:'session'}})).json()).session;
    const ui=async(action,data={})=>{const r=await post(base+'/api/permissions/command',{headers:{'X-Jarvis-UI':session},data:{action,...data}});const d=await r.json();if(!d.ok)throw new Error(JSON.stringify(d));return d;};
    const proposed={kind:'route',tool:'files',input:opts.data},decision=await ui('check',{proposed});
    if(decision.decision==='ASK')await ui('resolve_approval',{id:decision.approval.id,choice:'allow_once'});
    const token=decision.receipt||(await ui('recover',{id:decision.approval.id,proposed})).receipt;
    const response=await post(url,{...opts,headers:{...opts.headers,'X-Jarvis-UI':session,'X-Jarvis-Authorization':token}});
    await ui('create_policy',{capability:'WRITE_FILE',effect:'ALLOW',scope_type:'target',scope_value:'fixture'});
    await ui('create_policy',{capability:'EXECUTE_CODE',effect:'ALLOW',scope_type:'target',scope_value:'fixture'});
    return response;
   }
   return post(url,opts);
  };
  return context;
 };
 return browser;
};
if(!['agent-browser.cjs','tasks-browser.cjs'].includes(process.argv[2]))throw new Error('Select the original native regression harness.');
require(path.join(__dirname,process.argv[2]));
