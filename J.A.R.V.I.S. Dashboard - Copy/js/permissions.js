/* Mk VIII permission transport and UI. All classification/policy lives on the server. */
(function (J) {
  'use strict';
  const nativeFetch = window.fetch.bind(window), waiting = new Map(), connectionStatus = new Map();
  let session = null, routes = {}, init = null, signal = null, userText = '', background = false, turnReady = Promise.resolve(), turnId = null;
  const clone = value => JSON.parse(JSON.stringify(value));
  async function api(action, data) {
    if (action !== 'session' && !session) await initialize();
    const response = await nativeFetch('api/permissions/command', { method: 'POST', headers: {
      'content-type': 'application/json', 'X-Jarvis-UI': session || ''
    }, body: JSON.stringify(Object.assign({ action }, data || {})) });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || 'Permission broker unavailable; protected action stopped.');
    return result;
  }
  function initialize() {
    if (!init) init = api('session').then(result => { session = result.session; routes = result.routes; return result; }).catch(error => { init = null; throw error; });
    return init;
  }
  async function context() {
    await initialize();
    const state = J.agent && J.agent.current();
    const mission = state && ((J.agent.isActive && J.agent.isActive()) || (J.tasks && J.tasks.background())) ? state : null, durable = mission && mission.durable;
    const isBackground = background || !!(J.tasks && J.tasks.background());
    const extra = isBackground && J.tasks && J.tasks.permissionContext ? J.tasks.permissionContext() : {};
    await api('context', { context: Object.assign({ user_text: userText, background: isBackground,
      ...(turnId ? {turn_id:turnId} : {}),
      mission_id: mission && mission.id || '', task_run_id: durable && durable.runId || '' }, extra) });
  }
  function beginTurn(text, options, abortSignal) {
    signal = abortSignal; userText = options.background ? '' : String(text || ''); background = !!options.background;
    turnId = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2);
    turnReady = context(); turnReady.catch(() => {});
  }
  function endTurn() { signal = null; userText = ''; background = false; }
  let managementSession = null;
  async function managementFetch(url, options, proposed) {
    if (!managementSession) managementSession = (await api('session', {isolate:true})).session;
    const scoped = async (action, data) => {
      const response = await nativeFetch('api/permissions/command', {method:'POST',headers:{'content-type':'application/json','X-Jarvis-UI':managementSession},body:JSON.stringify(Object.assign({action},data||{}))});
      const result = await response.json(); if (!response.ok || !result.ok) throw new Error(result.error || 'Permission denied.'); return result;
    };
    await scoped('context', {context:{background:false,user_text:'',mission_id:'',task_run_id:'',turn_id:Date.now().toString(36)+Math.random().toString(36).slice(2)}});
    const decision = await scoped('check', {proposed}); let receipt = decision.receipt;
    if (decision.decision === 'DENY') throw new Error('PERMISSION_DENIED: ' + decision.reason);
    if (decision.decision === 'ASK') {
      show(); const answer = await new Promise(resolveWait => {
        const timer = setTimeout(()=>{waiting.delete(decision.approval.id);resolveWait({decision:'DENY'});},Math.max(1,decision.approval.expires_at*1000-Date.now()));
        waiting.set(decision.approval.id,result=>{clearTimeout(timer);waiting.delete(decision.approval.id);resolveWait(result);});
      });
      if (answer.decision !== 'ALLOW') throw new Error('PERMISSION_DENIED: task management approval denied or expired.');
      receipt = (await scoped('recover', {id:decision.approval.id,proposed})).receipt;
    }
    const headers = new Headers(options.headers || {});headers.delete('X-Jarvis-Interactive');headers.set('X-Jarvis-UI',managementSession);headers.set('X-Jarvis-Authorization',receipt);
    return nativeFetch(url,Object.assign({},options,{headers}));
  }
  async function resolve(id, choice) {
    const result = await api('resolve_approval', { id, choice });
    const live = waiting.get(id);
    if (live) live(result);
    else if (result.decision === 'ALLOW') {
      const state = await api('list'), approval = state.approvals.find(a => a.id === id);
      if (approval && approval.task_run_id) {
        await J.tasks.manage('resume_run', { run_id: approval.task_run_id }); await J.tasks.poll();
      } else await resume(id);
    }
    await refresh(); return result;
  }
  async function authorize(proposed) {
    await turnReady; await context();
    if (signal && signal.aborted) throw new Error('PERMISSION_DENIED: cancelled before authorization.');
    const exact = clone(proposed), result = await api('check', { proposed: exact });
    if (result.decision === 'DENY') throw new Error('PERMISSION_DENIED: ' + result.capability + ' — ' + result.reason);
    if (result.decision === 'ALLOW') return result.receipt;
    const approval = result.approval;
    J.emit('permission:waiting', approval);
    show();
    const outcome = await waitApproval(approval, signal);
    J.emit('permission:resolved', { id: approval.id, ok: outcome.decision === 'ALLOW', reason: outcome.reason || outcome.decision });
    if (outcome.decision !== 'ALLOW') throw new Error('PERMISSION_DENIED: approval ' + (outcome.reason || 'denied') + '; no action executed.');
    if (signal && signal.aborted) throw new Error('PERMISSION_DENIED: cancelled before execution.');
    await context();
    return (await api('recover', { id: approval.id, proposed: exact })).receipt;
  }
  function waitApproval(approval, activeSignal) {
    return new Promise(resolveWait => {
      let done = false;
      const settle = result => { if (done) return; done = true; clearTimeout(timer); waiting.delete(approval.id); if (activeSignal) activeSignal.removeEventListener('abort', stop); resolveWait(result); };
      const stop = () => settle({ decision: 'DENY', reason: 'cancelled' });
      const timer = setTimeout(() => settle({ decision: 'DENY', reason: 'expired' }), Math.max(1, approval.expires_at * 1000 - Date.now()));
      waiting.set(approval.id, settle);
      if (activeSignal) { activeSignal.addEventListener('abort', stop, { once: true }); if (activeSignal.aborted) stop(); }
    });
  }
  // One transport adapter also protects existing settings/task callers. It never changes provider requests.
  window.fetch = async function (url, options) {
    const parsed = new URL(typeof url === 'string' ? url : url.url, window.location.href), path = parsed.pathname;
    if (parsed.origin !== window.location.origin) return nativeFetch(url, options);
    if ((!options || String(options.method || 'GET').toUpperCase() === 'GET') && /^\/api\/(?:screenshot|search|fetch|knowledge|memory\/(?:all|due)|recall\/status|files\/capabilities|google\/status|spotify\/status|apps)$|^\/preview-file$/.test(path)) {
      try {
        const description = await api('route_action', {path,query:Object.fromEntries(parsed.searchParams)});
        const receipt = await authorize(description.proposed), headers = new Headers(options && options.headers || {});
        headers.set('X-Jarvis-UI', session); headers.set('X-Jarvis-Authorization', receipt);
        const response = await nativeFetch(url, Object.assign({}, options || {}, {headers}));
        if (response.ok && ['/api/spotify/status','/api/google/status'].includes(path)) {
          response.clone().json().then(data => {
            const connected = !!data.connected, prior = connectionStatus.get(path); connectionStatus.set(path, connected);
            if (prior !== undefined && prior !== connected) J.emit('skills:refresh');
          }).catch(() => {});
        }
        return response;
      } catch (error) { return new Response(JSON.stringify({ok:false,error:error.message}),{status:403,headers:{'content-type':'application/json'}}); }
    }
    if (!options || String(options.method || 'GET').toUpperCase() !== 'POST' || !/^\/api\/(?:files|desktop|google|spotify|recall|video|jobs|minecraft|lessons|tasks|memory|apply|hunt)\/command$|^\/api\/open$/.test(path)) return nativeFetch(url, options);
    await initialize();
    const body = JSON.parse(options.body || '{}'), tool = routes[path];
    // Lease mechanics are not model actions; existing task ownership validation remains authoritative.
    if (tool === 'tasks' && ['due','claim','check_claim','heartbeat','complete','fail','interrupt','get_run'].includes(body.action)) return nativeFetch(url, options);
    const proposed = { kind: 'route', tool, input: body };
    try {
      if (tool === 'tasks' && new Headers(options.headers || {}).get('X-Jarvis-Interactive') === '1') return await managementFetch(url, options, proposed);
      const receipt = await authorize(proposed);
      const headers = new Headers(options.headers || {}); headers.set('X-Jarvis-UI', session); headers.set('X-Jarvis-Authorization', receipt);
      const response = await nativeFetch(url, Object.assign({}, options, { headers }));
      if (response.ok && ['spotify','google'].includes(tool) && ['client_id','client_secret','disconnect'].some(key => Object.hasOwn(body,key))) J.emit('skills:refresh');
      return response;
    } catch (error) {
      return new Response(JSON.stringify({ ok: false, error: error.message }), { status: 403, headers: { 'content-type': 'application/json' } });
    }
  };
  async function dispatch(name, input, execute) {
    // Preflight mapping of every model action, including server-backed actions. Server-backed policy is enforced on exact HTTP arguments.
    await initialize();
    const mapped = await api('classify', { proposed: { kind: 'tool', tool: name, input } });
    if (!mapped.known) return 'FAILED - PERMISSION_DENIED: unknown tool/action.';
    if (mapped.server) {
      await turnReady; await context();
      const policy = await api('preflight', {proposed:{kind:'tool',tool:name,input}});
      if (policy.decision === 'DENY') {
        J.emit('permission:resolved', {id:'policy-denial',ok:false,reason:policy.capability + ' — ' + policy.reason});
        return 'FAILED - PERMISSION_DENIED: ' + policy.reason;
      }
      return execute();
    }
    try {
      const proposed = { kind: 'tool', tool: name, input: clone(input) }, receipt = await authorize(proposed);
      await api('consume', { proposed, receipt });
      if (signal && signal.aborted) return 'FAILED - PERMISSION_DENIED: cancelled.';
      return execute();
    } catch (e) { return 'FAILED - ' + e.message; }
  }
  async function resume(id) {
    await context();
    const saved = await api('recover', { id }); let action = saved.action;
    let output;
    if (action.kind === 'route') {
      const route = Object.keys(routes).find(path => routes[path] === action.tool);
      if (!route) throw new Error('No existing route for the pending action.');
      const response = await nativeFetch(route, { method: 'POST', headers: { 'content-type': 'application/json', 'X-Jarvis-UI': session, 'X-Jarvis-Authorization': saved.receipt }, body: JSON.stringify(action.input) });
      const result = await response.json(); output = result.ok ? result.summary || 'Exact approved action executed.' : 'FAILED - ' + result.error;
      if (J.agent) J.agent.observeFiles(action.input, result);
    } else {
      await api('consume', { proposed: action, receipt: saved.receipt });
      output = await J.brain.resumePermissionTool(action.tool, action.input);
    }
    const details = saved.classification || {};
    if (action.tool === 'files' && details.project_name) action = Object.assign({}, action, {input:Object.assign({},action.input,{project:details.project_name})});
    J.emit('permission:recovered', { id, action, output, classification: details });
    return output;
  }
  async function resumeRun(runId) {
    background = true; await context();
    const result = await api('list');
    if ((result.expired || []).some(a => a.task_run_id === runId)) throw new Error('Permission approval expired; request a fresh action.');
    const pending = result.approvals.filter(a => a.task_run_id === runId);
    for (const approval of pending) {
      if (approval.status === 'pending') {
        J.emit('permission:waiting', approval); show();
        const answer = await waitApproval(approval, null);
        J.emit('permission:resolved', {id:approval.id,ok:answer.decision==='ALLOW',reason:answer.reason||answer.decision});
        if (answer.decision !== 'ALLOW') throw new Error('Permission denied, cancelled or expired.');
      }
      if (!await J.tasks.ensureClaim()) throw new Error('Scheduled claim was lost before permission recovery.');
      await resume(approval.id);
    }
  }
  const surface = document.createElement('div'); surface.id = 'permissionsSurface'; surface.className = 'msg mission'; surface.hidden = true;
  const body = document.createElement('section'); body.className = 'msg-body permission-surface'; body.setAttribute('role', 'region'); body.setAttribute('aria-label', 'Permissions'); surface.appendChild(body);
  function el(tag, text) { const node = document.createElement(tag); if (text) node.textContent = text; return node; }
  function button(row, label, callback) {
    const node = el('button', label); node.className = 'mini'; node.type = 'button'; row.appendChild(node);
    node.addEventListener('click', async () => { node.disabled = true; try { await callback(); } catch (e) { J.toast(e.message, 'warn'); } finally { node.disabled = false; } });
  }
  async function refresh() {
    if (surface.hidden) return;
    await initialize(); const data = await api('list'); body.replaceChildren();
    const mission = J.agent && J.agent.current();
    (data.expired || []).filter(a => mission && (mission.pendingPermissions || []).includes(a.id)).forEach(a => J.emit('permission:resolved', {id:a.id,ok:false,reason:'expired'}));
    const top = el('div'); top.className = 'task-controls'; top.appendChild(el('b', 'PERMISSIONS')); button(top, 'close', () => { surface.hidden = true; }); body.appendChild(top);
    data.approvals.forEach(approval => {
      const card = el('article'); card.className = 'permission-card'; card.dataset.approvalId = approval.id;
      card.append(el('b', approval.status === 'approved' ? 'APPROVED · RESUME REQUIRED' : 'JARVIS REQUESTS PERMISSION'),
        el('p', approval.capability + ' · RISK ' + approval.risk.toUpperCase()), el('p', 'Target: ' + approval.target),
        el('p', 'Why: protected action requires your authorization.'),
        el('p', 'Grant scope: ' + approval.grant_scope + ' · Expires ' + new Date(approval.expires_at * 1000).toLocaleTimeString()),
        el('p', (approval.task_run_id ? 'Scheduled run: ' + approval.task_run_id : 'Mission: ' + (approval.mission_id || 'Interactive action'))));
      const choices = el('div'); choices.className = 'task-controls';
      const labels = { allow_once: 'Allow once', allow_session: 'Allow this session', allow_target: 'Allow this target', allow_always: 'Always allow', deny_once: 'Deny once', always_deny: 'Always deny' };
      if (approval.status === 'approved') button(choices, 'Resume approved action', async () => {
        if (approval.task_run_id) { await J.tasks.manage('resume_run', { run_id: approval.task_run_id }); await J.tasks.poll(); }
        else { await resume(approval.id); await refresh(); }
      });
      else approval.choices.forEach(choice => button(choices, labels[choice], () => resolve(approval.id, choice)));
      card.appendChild(choices); body.appendChild(card);
    });
    if (!data.approvals.length) body.appendChild(el('p', 'No pending approvals.'));
    data.policies.forEach(policy => {
      const row = el('article'); row.className = 'permission-card'; row.appendChild(el('p', policy.effect + ' ' + policy.capability + ' · ' + (policy.scope_value || 'global') + (policy.session_id ? ' · session' : ' · persistent')));
      const controls = el('div'); controls.className = 'task-controls';
      for (const effect of ['ASK', 'DENY']) button(controls, effect === 'ASK' ? 'Ask instead' : 'Deny instead', async () => { await api('update_policy', { id: policy.id, effect }); await refresh(); });
      button(controls, 'Revoke', async () => { await api('delete_policy', { id: policy.id }); await refresh(); }); row.appendChild(controls); body.appendChild(row);
    });
    button(body, 'Clear session grants', async () => { await api('clear_session'); await refresh(); });
    const details = el('details'); details.appendChild(el('summary', 'Recent decisions')); body.appendChild(details);
    const history = await api('history'); history.events.slice(0, 20).forEach(event => details.appendChild(el('p', event.decision + ' ' + event.capability + ' · ' + event.target + ' · ' + event.source)));
  }
  function show() {
    const convo = document.getElementById('convo'); if (!convo) return;
    convo.appendChild(surface); surface.hidden = false;
    refresh().then(() => { convo.scrollTop = convo.scrollHeight; surface.scrollIntoView({block:'nearest'}); }).catch(e => { body.textContent = e.message; });
  }
  J.permissions = { skillsCommand: async data => {
      await initialize();
      const response = await nativeFetch('api/skills/command', {method:'POST',headers:{'content-type':'application/json','X-Jarvis-UI':session},body:JSON.stringify(data)});
      const result = await response.json(); if (!response.ok || !result.ok) throw new Error(result.error || 'Skill management unavailable.'); return result;
    }, beginTurn, endTurn, dispatch, resolve, resumeRun, api, show, refresh,
    cancel: () => Array.from(waiting.values()).forEach(settle=>settle({decision:'DENY',reason:'cancelled'})),
    diagnostics: () => ({ waiting: waiting.size }) };
  const opener = document.getElementById('permissionsOpen'); if (opener) opener.addEventListener('click', show);
})(window.J);
