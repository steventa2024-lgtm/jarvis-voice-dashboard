/* Trusted code bindings. JSON can select only these identities, never code or paths. */
(function (J) {
  'use strict';
  const adapters = Object.freeze({
    tasks: (i,c,h) => J.tasks ? J.tasks.modelCommand(i,c.userText) : 'FAILED - Task module unavailable.',
    spotify: (i,c,h) => h.spotify(i.action,i.query,i.value),
    knowledge: (i,c,h) => h.runLookup(i.source,i.query),
    google: (i,c,h) => h.googleCmd(i.action,i.query),
    memory: (i,c,h) => h.memoryCmd(i.action,i.text,i.when),
    vision: (i,c,h) => h.seeScreen(i.question),
    recall: (i,c,h) => i.action === 'status' ? h.recallCmd('status') : i.action === 'index' ? h.recallCmd('index',null,i.folder) : h.recallCmd(i.action,i.query),
    files: async (i,c,h) => {
      if (i.action === 'write') return h.guardedWrite(i);
      const out = await h.filesCmd(i);
      if (i.action === 'scaffold' && !/^FAILED/.test(out)) J.emit('preview',{project:(i.name||'').trim().replace(/[^A-Za-z0-9 _-]/g,'').replace(/\s+/g,'-')});
      return out;
    },
    preview: (i,c,h) => h.seePreview(i.project,i.question,i.path,i.width,i.height),
    video: (i,c,h) => h.videoCmd(i),
    jobs: (i,c,h) => h.jobsCmd(i),
    job_hunt: (i,c,h) => h.huntCmd(i),
    minecraft: (i,c,h) => h.simpleCmd('api/minecraft/command',i,'minecraft'),
    lessons: (i,c,h) => h.lessonsCmd(i),
    translate: (i,c,h) => h.translateCmd(i.source,i.to),
    desktop: (i,c,h) => h.desktopCmd(i.action,i.text),
    web: (i,c,h) => c.tool === 'web_search' ? h.runSearch(i.query) : h.runFetch(i.url)
  });
  J.skillAdapters = Object.freeze({execute: async (adapter,input,context) => {
    if (!Object.hasOwn(adapters,adapter) || !J.brain || !J.brain.skillRuntime) return 'FAILED - Trusted skill adapter unavailable.';
    try { return await adapters[adapter](input,context,J.brain.skillRuntime); }
    catch (error) { return 'FAILED - Skill adapter failed: ' + (error && error.message || 'unavailable'); }
  }});
})(window.J);
