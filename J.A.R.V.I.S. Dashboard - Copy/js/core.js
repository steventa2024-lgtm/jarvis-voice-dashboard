/* ==========================================================================
   core.js — shared state, settings, storage, logging, toasts, helpers
   Everything hangs off the single global `J`. Classic script (no modules) so
   the dashboard still runs when opened straight off the filesystem.
   ========================================================================== */

window.J = (function () {

  const $  = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* ---------------------------------------------------------------- storage */

  const KEY = 'jarvis.v7.';

  function load(name, fallback) {
    try {
      const raw = localStorage.getItem(KEY + name);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) { return fallback; }
  }

  function save(name, value) {
    try { localStorage.setItem(KEY + name, JSON.stringify(value)); }
    catch (e) { /* private mode or quota — settings just won't persist */ }
  }

  function drop(name) {
    try { localStorage.removeItem(KEY + name); } catch (e) {}
  }

  /* --------------------------------------------------------------- settings */

  const DEFAULTS = {
    apiKey:    '',
    model:     'claude-opus-5',
    effort:    'medium',
    webSearch: true,
    showThinking: false,
    userName:  'Zero',        // who he is talking to, and who built him
    persona:   '',
    speak:     true,
    wakeWord:  true,          // require "Hey Jarvis" before he acts on speech

    /* Whether the microphone stays open while he talks, so you can cut him
       off. Off means he finishes every sentence no matter what you say. */
    bargeIn:   'on',          // on | off

    /* Seconds of nothing before the display drops to the clock. 0 turns it
       off entirely — a wall screen and a desk monitor want very different
       numbers here. */
    ambientAfter: 180,
    sleepAfter: 60,           // seconds of silence before he returns to standby
    autoListen: true,         // open the mic at load, so the wake word works untouched
    linkMode:  'card',        // card | open — whether a link takes over the screen
    micDevice: '',            // deviceId, or '' for the system default
    searchKey: '',            // Brave Search key; keyless engines throttle
    googleKey: '',            // Google Programmable Search key, 100/day free
    googleCx:  '',            // ...and its Search Engine ID
    voiceURI:  '',
    rate:      0.96,          // a shade under natural; hurried reads as chatty
    pitch:     0.85,          // lower carries authority. 1.0 is the browser default
    accent:    '#35d6ff',
    mask:      'reactor',     // reactor | grid | joker — see face.js

    /* Which service answers. Defaults to the local Ollama, because it needs no
       key in the browser, is not blocked by CORS, and serves cloud models once
       `ollama signin` has run — so a fresh browser works with no setup at all.
       'anthropic' talks to api.anthropic.com directly instead. */
    provider:  'openai',
    altBase:   'http://localhost:11434/v1',
    altKey:    '',                              // local Ollama authenticates itself
    altModel:  'gpt-oss:120b-cloud',
    visionModel: '',          // e.g. gemma4:cloud - the main model cannot see
    visionConn: '',           // saved-connection name, or '' to reuse the main one

    /* A smaller model for trivial turns. Routing is biased hard toward the main
       model — see difficulty() in brain.js — so this only catches plain chat. */
    fastModel: '',
    critique:  'off',         // off | hard | always

    /* Whether a write is shown as a diff before it lands. 'overwrite' asks
       only when existing work would be replaced — creating a file has nothing
       to lose, and asking about all of them turns a ten-file build into ten
       clicks. */
    reviewWrites: 'overwrite',   // off | overwrite | all

    /* After a turn that wrote into a project, run it or look at it and hand
       the findings back so he can fix them. The doctrine already asks him to;
       this is what makes it happen when he does not. */
    buildCheck: 'on',            // on | off

    /* Saved provider configurations, so switching between a local Ollama and
       OpenRouter is one click rather than three fields retyped from memory. */
    connections: [
      { name: 'Ollama (local)', base: 'http://localhost:11434/v1',
        key: '', model: 'gpt-oss:120b-cloud' },
      { name: 'OpenRouter', base: 'https://openrouter.ai/api/v1',
        key: '', model: '' },
      { name: 'Groq', base: 'https://api.groq.com/openai/v1',
        key: '', model: '' }
    ],

    /* Route model calls through serve.py rather than straight from the page.
       Necessary for providers that send no CORS headers — Ollama Cloud answers
       the preflight with 405, so the browser cannot reach it at all. Harmless
       for the ones that do. */
    routeLocal: true,
    density:   140,
    units:     'metric'
  };

  const settings = Object.assign({}, DEFAULTS, load('settings', {}));

  function set(patch) {
    Object.assign(settings, patch);
    save('settings', settings);
    emit('settings', settings);
  }

  function resetSettings() {
    const key = settings.apiKey;           // never silently destroy the key
    Object.assign(settings, DEFAULTS, { apiKey: key });
    save('settings', settings);
    emit('settings', settings);
  }

  /* ----------------------------------------------------------------- events */

  const handlers = {};

  function on(name, fn) {
    (handlers[name] = handlers[name] || []).push(fn);
    return () => off(name, fn);
  }

  function off(name, fn) {
    if (!handlers[name]) return;
    handlers[name] = handlers[name].filter(h => h !== fn);
  }

  function emit(name, payload) {
    (handlers[name] || []).forEach(fn => {
      try { fn(payload); }
      catch (e) { console.error('[jarvis] handler failed for "' + name + '"', e); }
    });
  }

  /* ------------------------------------------------------------------- log */

  const LOG_CAP = 400;
  let logEl = null;
  let logFilter = 'all';

  function log(text, level, channel) {
    level = level || 'info';
    channel = channel || 'sys';
    if (!logEl) logEl = $('#log');
    if (!logEl) return;

    const row = document.createElement('div');
    row.className = 'log-row ' + level;
    row.dataset.ch = channel;

    const t = document.createElement('time');
    t.textContent = new Date().toLocaleTimeString('en-GB', { hour12: false });

    const p = document.createElement('p');
    p.textContent = text;

    row.append(t, p);
    if (logFilter !== 'all' && logFilter !== channel) row.classList.add('hide');

    logEl.appendChild(row);
    while (logEl.children.length > LOG_CAP) logEl.removeChild(logEl.firstChild);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setLogFilter(ch) {
    logFilter = ch;
    if (!logEl) logEl = $('#log');
    if (!logEl) return;
    $$('.log-row', logEl).forEach(r => {
      r.classList.toggle('hide', ch !== 'all' && r.dataset.ch !== ch);
    });
  }

  function logText() {
    if (!logEl) logEl = $('#log');
    if (!logEl) return '';
    return $$('.log-row', logEl)
      .map(r => r.querySelector('time').textContent + '  ' + r.querySelector('p').textContent)
      .join('\n');
  }

  /* ---------------------------------------------------------------- toasts */

  function toast(message, level, ms) {
    const host = $('#toasts');
    if (!host) return;
    const el = document.createElement('div');
    el.className = 'toast ' + (level || '');
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 320);
    }, ms || 4600);
  }

  /* ---------------------------------------------------------------- status */

  function status(main, sub) {
    const a = $('#statusText'), b = $('#statusSub');
    if (a && main !== undefined && main !== null) a.textContent = main;
    if (b && sub !== undefined && sub !== null) b.textContent = sub;
  }

  function mode(name, dotClass) {
    const m = $('#modeVal'); if (m) m.textContent = name;
    const d = $('#brandDot');
    if (d) d.className = 'brand-dot' + (dotClass ? ' ' + dotClass : '');
  }

  /* --------------------------------------------------------------- helpers */

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const dpr = () => Math.min(window.devicePixelRatio || 1, 2);

  function fitCanvas(canvas) {
    const r = canvas.getBoundingClientRect();
    const s = dpr();
    const w = Math.max(1, Math.round(r.width  * s));
    const h = Math.max(1, Math.round(r.height * s));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    return { w: r.width, h: r.height, s: s };
  }

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function hexToRgb(hex) {
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [53, 214, 255];
  }

  /* Accent is applied to the CSS custom properties the whole sheet reads. */
  function applyAccent(hex) {
    const [r, g, b] = hexToRgb(hex);
    const root = document.documentElement.style;
    root.setProperty('--accent', hex);
    root.setProperty('--accent-2', `rgb(${Math.round(r * .6)},${Math.round(g * .6)},${Math.round(b * .6)})`);
    root.setProperty('--accent-ink', `rgb(${Math.round(r * .09)},${Math.round(g * .13)},${Math.round(b * .17)})`);
    root.setProperty('--line-hot', `rgba(${r},${g},${b},0.35)`);
  }

  /* ------------------------------------------------------ tiny markdown */
  /* Deliberately small: headings, bold, italics, code, lists, links, tables.
     Everything is escaped first, so model output can never inject markup. */

  function md(src) {
    let s = esc(src);

    // fenced code first — protect its contents from every other rule
    const blocks = [];
    s = s.replace(/```([\w+-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      blocks.push('<pre><code>' + code.replace(/\n$/, '') + '</code></pre>');
      return ' CODE' + (blocks.length - 1) + ' ';
    });

    s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    s = s.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
    s = s.replace(/^##\s+(.+)$/gm,  '<h2>$1</h2>');
    s = s.replace(/^#\s+(.+)$/gm,   '<h1>$1</h1>');

    const lines = s.split('\n');
    const out = [];
    let list = null;   // 'ul' | 'ol' | null
    let table = null;  // array of row-cell-arrays

    const closeList  = () => { if (list)  { out.push('</' + list + '>'); list = null; } };
    const closeTable = () => {
      if (!table) return;
      const head = table[0];
      const body = table.slice(1);
      out.push('<table><thead><tr>' + head.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>'
        + body.map(r => '<tr>' + r.map(c => '<td>' + c + '</td>').join('') + '</tr>').join('')
        + '</tbody></table>');
      table = null;
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const raw  = line.trim();

      // table row
      if (/^\|.*\|$/.test(raw)) {
        const cells = raw.slice(1, -1).split('|').map(c => c.trim());
        if (/^[\s|:-]+$/.test(raw)) continue;          // separator row
        closeList();
        (table = table || []).push(cells);
        continue;
      }
      closeTable();

      const ul = /^[-*+]\s+(.*)$/.exec(raw);
      const ol = /^(\d+)[.)]\s+(.*)$/.exec(raw);

      if (ul) {
        if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; }
        out.push('<li>' + ul[1] + '</li>');
        continue;
      }
      if (ol) {
        if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; }
        out.push('<li>' + ol[2] + '</li>');
        continue;
      }
      closeList();

      if (!raw) continue;
      if (/^<(h[1-3]|pre|table)/.test(raw) || raw.indexOf(' CODE') === 0) { out.push(raw); continue; }
      out.push('<p>' + line + '</p>');
    }
    closeList();
    closeTable();

    return out.join('').replace(/ CODE(\d+) /g, (_, n) => blocks[+n]);
  }

  /* --------------------------------------------------------------- exports */

  return {
    $, $$,
    settings, set, resetSettings, DEFAULTS,
    load, save, drop,
    on, off, emit,
    log, setLogFilter, logText, toast, status, mode,
    reducedMotion, dpr, fitCanvas, clamp, esc, hexToRgb, applyAccent, md
  };

})();
