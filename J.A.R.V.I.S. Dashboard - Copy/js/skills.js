/* Cached validated metadata. Server owns manifests, enabled state and permissions. */
(function (J) {
  'use strict';
  let snapshot=null,loading=null,probe={},settingsTimer=null;
  const copy=value=>JSON.parse(JSON.stringify(value));
  async function load(force) {
    if (loading) return loading;
    if (snapshot && !force) return snapshot;
    loading=fetch('api/skills').then(async response=>{
      const data=await response.json(); if (!response.ok || !data.ok || data.schema_version!==1) throw new Error('Skill registry unavailable.');
      snapshot=data; J.emit('skills:update',{generation:data.generation,health:data.health}); return snapshot;
    }).catch(error=>{snapshot=null;J.emit('skills:unavailable',{reason:'Registry unavailable; integration tools stopped.'});throw error;}).finally(()=>{loading=null;});
    return loading;
  }
  function context(extra) {
    return Object.assign({provider:J.settings.provider==='openai'?'openai':'anthropic',visionModel:!!J.settings.visionModel,webSearch:!!J.settings.webSearch,
      background:!!(J.tasks&&J.tasks.background()),taskModule:!!J.tasks,capabilities:probe},extra||{});
  }
  function usable(skill,ctx) {
    if (!skill.enabled || !['available','degraded'].includes(skill.status)) return false;
    const a=skill.availability,hosted=skill.runtime.adapter==='web'&&ctx.provider==='anthropic';
    const flags=a.capabilities.map(name=>!!ctx.capabilities[name]);
    if (!hosted && flags.length && !(a.mode==='any'?flags.some(Boolean):flags.every(Boolean))) return false;
    return a.client_requirements.every(r=>({vision_model:ctx.visionModel,web_search:ctx.webSearch,foreground:!ctx.background,task_module:ctx.taskModule})[r]);
  }
  function list(extra) {
    if (!snapshot) return [];
    const ctx=context(extra);
    return copy(snapshot.skills).map(skill=>Object.assign(skill,!usable(skill,ctx)&&skill.status!=='disabled'?{status:'unavailable',reason:skill.reason==='Trusted adapter installed.'?'Required server capability or client setting is unavailable.':skill.reason}:{}));
  }
  function tools(extra) {
    if (!snapshot) return [];
    const ctx=context(extra),out=[];
    snapshot.skills.filter(skill=>usable(skill,ctx)).forEach(skill=>{
      if (ctx.provider==='anthropic'&&skill.provider_tools) out.push(...skill.tools.map(tool=>skill.provider_tools.anthropic[tool.name]));
      else out.push(...skill.tools);
    });
    return copy(out);
  }
  async function execute(name,input,executionContext,authorized) {
    if (!snapshot) return 'FAILED - Skill registry unavailable.';
    const skill=snapshot.skills.find(s=>s.tools.some(t=>t.name===name));
    if (!skill || !usable(skill,context())) return 'FAILED - Skill "'+name+'" is disabled, invalid or unavailable.';
    try {
      // Cheap cached server lookup fences stale tabs/recovered calls; no disk scan or availability probe.
      const response=await fetch('api/skills/tool/'+encodeURIComponent(name));const data=await response.json();
      if (!response.ok || !data.ok) return 'FAILED - Skill "'+name+'" is disabled, invalid or unavailable.';
      if (!J.permissions || !J.skillAdapters) return 'FAILED - Required authorization/adapter infrastructure unavailable.';
      const run=()=>J.skillAdapters.execute(data.skill.runtime.adapter,input,Object.assign({},executionContext,{tool:name}));
      return authorized ? await run() : await J.permissions.dispatch(name,input,run);
    } catch (error) { return 'FAILED - Skill registry unavailable; no action dispatched.'; }
  }
  async function refresh() {
    await J.permissions.skillsCommand({action:'refresh'});await load(true);render();return snapshot;
  }
  async function setEnabled(id,enabled) {
    await J.permissions.skillsCommand({action:'set_enabled',id,enabled});await load(true);render();
    try { localStorage.setItem('jarvis-skill-change',String(Date.now())); } catch (e) {}
  }
  function summary() {
    if (!snapshot) return 'Registry unavailable. Integration tools cannot execute.';
    const skills=list();return 'Available skills: '+skills.filter(s=>['available','degraded'].includes(s.status)).map(s=>s.id).join(', ')+
      '. Unavailable/disabled: '+skills.filter(s=>!['available','degraded'].includes(s.status)).map(s=>s.id+' ('+s.status+')').join(', ')+'.';
  }
  const surface=document.createElement('div');surface.id='skillsSurface';surface.className='msg mission';surface.hidden=true;
  const body=document.createElement('section');body.className='msg-body skills-surface';body.setAttribute('role','region');body.setAttribute('aria-label','Skills');surface.appendChild(body);
  function el(tag,text){const node=document.createElement(tag);if(text)node.textContent=text;return node;}
  function button(row,text,action){const node=el('button',text);node.className='mini';node.type='button';row.appendChild(node);node.addEventListener('click',async()=>{node.disabled=true;try{await action();}catch(e){J.toast(e.message,'warn');}finally{node.disabled=false;}});}
  function render(){
    if(surface.hidden)return;body.replaceChildren();const top=el('div');top.className='task-controls';top.appendChild(el('b','SKILLS'));button(top,'Refresh',refresh);button(top,'close',()=>{surface.hidden=true;});body.appendChild(top);
    if(!snapshot){body.appendChild(el('p','Registry unavailable. Normal chat remains available.'));return;}
    list().forEach(skill=>{const row=el('article');row.className='skill-card';row.dataset.skillId=skill.id;
      row.append(el('b',skill.name+' · '+skill.status.toUpperCase()),el('p',skill.reason));
      const detail=el('details');detail.appendChild(el('summary','Details · v'+skill.version));
      detail.append(el('p',skill.description),el('p','Tools: '+skill.tools.map(t=>t.name).join(', ')),el('p','Permissions: '+[...new Set(Object.values(skill.permissions).flatMap(v=>Object.values(v)))].join(', ')),el('p','Health: manifest/schema/permissions valid; trusted adapter registered.'));row.appendChild(detail);
      button(row,skill.enabled?'Disable':'Enable',()=>setEnabled(skill.id,!skill.enabled));body.appendChild(row);
    });
    snapshot.invalid.forEach(skill=>body.appendChild(el('p',skill.id+' · INVALID · '+skill.reason)));
    snapshot.infrastructure.forEach(skill=>body.appendChild(el('p',skill.name+' · REQUIRED · cannot be disabled')));
  }
  async function show(){const convo=document.getElementById('convo');if(!convo)return;convo.appendChild(surface);surface.hidden=false;try{await load();}catch(e){}render();convo.scrollTop=convo.scrollHeight;surface.scrollIntoView({block:'nearest'});}
  J.skills={load,refresh,list,tools,execute,executeAuthorized:(name,input,ctx)=>execute(name,input,ctx,true),summary,get:id=>list().find(s=>s.id===id),enabled:id=>list().some(s=>s.id===id&&['available','degraded'].includes(s.status)),
    setEnabled,show,diagnostics:()=>snapshot?copy({generation:snapshot.generation,health:snapshot.health,invalid:snapshot.invalid}):{unavailable:true},setCapabilities:value=>{probe=Object.assign({},value);render();}};
  const opener=document.getElementById('skillsOpen');if(opener)opener.addEventListener('click',show);
  const changed=()=>{clearTimeout(settingsTimer);settingsTimer=setTimeout(()=>{refresh().catch(()=>{});},400);};
  J.on('settings',changed);J.on('skills:refresh',changed);
  window.addEventListener('storage',event=>{if(event.key==='jarvis-skill-change')load(true).then(render).catch(()=>{});});
})(window.J);
