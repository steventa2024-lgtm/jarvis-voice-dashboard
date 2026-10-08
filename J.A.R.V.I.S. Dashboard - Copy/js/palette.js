/* ==========================================================================
   palette.js — command palette (Ctrl+K)

   One list, filtered by a loose subsequence match so "clcv" still finds
   "clear conversation". Anything that does not match a command falls through
   to "ask J.A.R.V.I.S." — the palette is a question box as well as a menu, so
   Ctrl+K is never a dead end.
   ========================================================================== */

(function (J) {

  const overlay = J.$('#paletteOverlay');
  const field   = J.$('#paletteInput');
  const list    = J.$('#paletteList');
  if (!overlay || !field || !list) return;

  let rows = [];      // currently rendered commands
  let sel = 0;

  /* ============================================================== commands */

  const flip = key => () => {
    const next = !J.settings[key];
    J.set({ [key]: next });
    return next;
  };

  function commands() {
    const s = J.settings;
    const onOff = v => (v ? 'on' : 'off');

    return [
      { ico: '◇', name: 'Skills', hint: 'capabilities, availability and routing', run: () => { if (J.skills) J.skills.show(); } },
      { ico: '◇', name: 'Permissions', hint: 'grants, approvals and audit', run: () => { if (J.permissions) J.permissions.show(); } },
      { ico: '◷', name: 'Scheduled tasks', hint: 'automations, schedules and run history', run: () => { if (J.tasks) J.tasks.show(); } },
      { ico: '◉', name: 'Toggle voice input',
        hint: J.voice.isListening() ? 'currently listening' : 'engage the microphone',
        key: 'space', run: () => J.voice.toggle() },

      { ico: '◼', name: 'Stop speaking', hint: 'cut the current reply short',
        run: () => { J.brain.abort(); } },

      { ico: '♪', name: 'Spoken replies — ' + onOff(s.speak),
        hint: 'read answers aloud',
        run: () => { const v = flip('speak')(); if (!v) J.voice.shutUp(); J.toast('Spoken replies ' + onOff(v)); } },

      { ico: '☾', name: 'Wake word — ' + onOff(s.wakeWord),
        hint: 'require “Jarvis” before acting on speech',
        run: () => J.toast('Wake word ' + onOff(flip('wakeWord')())) },

      { ico: '⌕', name: 'Live web search — ' + onOff(s.webSearch),
        hint: 'let me look things up before answering',
        run: () => J.toast('Web search ' + onOff(flip('webSearch')())) },

      { ico: '◌', name: 'Show reasoning — ' + onOff(s.showThinking),
        hint: 'stream a summary of the thinking',
        run: () => J.toast('Reasoning summary ' + onOff(flip('showThinking')())) },

      { ico: '⬢', name: 'Model — Claude Opus 5', hint: 'most capable, slowest',
        run: () => pickModel('claude-opus-5') },
      { ico: '⬡', name: 'Model — Claude Sonnet 5', hint: 'the balanced default',
        run: () => pickModel('claude-sonnet-5') },
      { ico: '⬠', name: 'Model — Claude Haiku 4.5', hint: 'fastest, best for chat on a screen',
        run: () => pickModel('claude-haiku-4-5') },

      { ico: '↯', name: 'Effort — low', hint: 'snappiest replies', run: () => pickEffort('low') },
      { ico: '↯', name: 'Effort — medium', hint: 'balanced', run: () => pickEffort('medium') },
      { ico: '↯', name: 'Effort — high', hint: 'careful reasoning', run: () => pickEffort('high') },
      { ico: '↯', name: 'Effort — very high', hint: 'hardest problems, slowest', run: () => pickEffort('xhigh') },

      { ico: '◎', name: 'Face — Arc reactor', hint: 'the Iron Man core',
        run: () => face('reactor', '#35d6ff') },
      { ico: '☻', name: 'Face — Joker', hint: 'carved grin, green hair, black eye paint',
        run: () => face('joker', '#7ee04f') },
      { ico: '☺', name: 'Face — Grid', hint: 'the clean HUD wireframe',
        run: () => face('grid', '#35d6ff') },

      { ico: '◈', name: 'Accent — ice', run: () => accent('#35d6ff') },
      { ico: '◈', name: 'Accent — jade', run: () => accent('#00e5a0') },
      { ico: '◈', name: 'Accent — amber', run: () => accent('#ffb454') },
      { ico: '◈', name: 'Accent — rose', run: () => accent('#ff5f8f') },
      { ico: '◈', name: 'Accent — violet', run: () => accent('#a97bff') },

      { ico: '⛭', name: 'Configuration', hint: 'keys, voice, interface, memory',
        key: 'ctrl /', run: () => J.app.openSettings() },

      { ico: '⤢', name: 'Fullscreen', hint: 'the point of putting this on a television',
        run: () => {
          if (document.fullscreenElement) document.exitFullscreen();
          else document.documentElement.requestFullscreen?.().catch(() => {});
        } },

      { ico: '⟳', name: 'Refresh local conditions', hint: 'reread weather and location',
        run: () => J.telemetry.refreshWeather() },

      { ico: '°', name: 'Units — ' + (s.units === 'metric' ? 'switch to imperial' : 'switch to metric'),
        run: () => {
          const v = s.units === 'metric' ? 'imperial' : 'metric';
          J.set({ units: v });
          J.telemetry.refreshWeather();
          J.toast('Units set to ' + v);
        } },

      { ico: '⌫', name: 'Clear conversation', hint: 'wipe the transcript and my short-term context',
        run: () => J.brain.clearConversation() },

      { ico: '↧', name: 'Export transcript', hint: 'save this session as a text file',
        run: () => J.brain.exportTranscript() },

      { ico: '⌦', name: 'Clear system log',
        run: () => { const l = J.$('#log'); if (l) l.innerHTML = ''; } },

      { ico: '⌧', name: 'Forget everything I know about you',
        hint: J.brain.getMemories().length + ' facts stored',
        run: () => { J.brain.clearMemories(); J.toast('Memory cleared.', 'warn'); } }
    ];
  }

  function pickModel(id) {
    J.set({ model: id });
    J.$('#setModel').value = id;
    J.$('#modelVal').textContent = id;
    J.toast('Model set to ' + id);
  }

  function pickEffort(level) {
    J.set({ effort: level });
    J.$('#setEffort').value = level;
    J.toast('Effort set to ' + level);
  }

  function accent(hex) {
    J.set({ accent: hex });
    J.applyAccent(hex);
    J.toast('Accent updated');
  }

  /* Switching the face from the palette is a theme change, so it carries a
     matching accent with it. The Configuration dropdown changes only the mask,
     for anyone who wants a joker face with their own colour scheme. */
  function face(name, hex) {
    J.set({ mask: name, accent: hex });
    J.applyAccent(hex);
    const sel = J.$('#setMask');
    if (sel) sel.value = name;
    J.toast(name === 'joker' ? 'Why so serious?' : 'Grid face restored');
  }

  /* ================================================================ match */

  /* Subsequence scoring: every query character must appear in order.
     Consecutive hits and word-start hits score higher, so exact prefixes
     float to the top without needing a separate exact-match pass. */
  function score(text, query) {
    const t = text.toLowerCase(), q = query.toLowerCase();
    let ti = 0, points = 0, streak = 0;

    for (let qi = 0; qi < q.length; qi++) {
      const c = q[qi];
      if (c === ' ') continue;
      const found = t.indexOf(c, ti);
      if (found === -1) return -1;

      points += found === ti ? 4 + streak : 1;
      if (found === 0 || t[found - 1] === ' ' || t[found - 1] === '—') points += 3;
      streak = found === ti ? streak + 1 : 0;
      ti = found + 1;
    }
    return points - t.length * 0.01;   // gentle nudge toward shorter labels
  }

  function render(query) {
    const q = query.trim();
    const all = commands();

    rows = !q
      ? all
      : all
          .map(c => ({ c, s: score(c.name + ' ' + (c.hint || ''), q) }))
          .filter(r => r.s >= 0)
          .sort((a, b) => b.s - a.s)
          .map(r => r.c);

    // a query that matches nothing is almost certainly a question
    if (q && (!rows.length || q.length > 12 || /[?]$/.test(q))) {
      rows = [{
        ico: '➤', name: 'Ask J.A.R.V.I.S.', hint: q, key: '↵',
        run: () => J.app.send(q)
      }].concat(rows);
    }

    sel = 0;
    paint();
  }

  function paint() {
    list.innerHTML = '';

    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'p-item';
      empty.innerHTML = '<span class="p-ico">·</span><span class="p-txt"><b>No match</b></span>';
      list.appendChild(empty);
      return;
    }

    rows.forEach((c, i) => {
      const row = document.createElement('div');
      row.className = 'p-item' + (i === sel ? ' sel' : '');
      row.setAttribute('role', 'option');

      const ico = document.createElement('span');
      ico.className = 'p-ico';
      ico.textContent = c.ico || '·';

      const txt = document.createElement('span');
      txt.className = 'p-txt';
      const b = document.createElement('b');
      b.textContent = c.name;
      txt.appendChild(b);
      if (c.hint) {
        const s = document.createElement('span');
        s.textContent = c.hint;
        txt.appendChild(s);
      }

      row.append(ico, txt);

      if (c.key) {
        const k = document.createElement('span');
        k.className = 'p-key';
        k.textContent = c.key;
        row.appendChild(k);
      }

      row.addEventListener('mouseenter', () => { sel = i; mark(); });
      row.addEventListener('click', () => run(i));
      list.appendChild(row);
    });
  }

  function mark() {
    J.$$('.p-item', list).forEach((el, i) => el.classList.toggle('sel', i === sel));
  }

  function move(step) {
    if (!rows.length) return;
    sel = (sel + step + rows.length) % rows.length;
    mark();
    const el = J.$$('.p-item', list)[sel];
    if (el) el.scrollIntoView({ block: 'nearest' });
  }

  function run(i) {
    const c = rows[i === undefined ? sel : i];
    if (!c) return;
    close();
    try { c.run(); }
    catch (e) { J.toast('That command failed: ' + e.message, 'crit'); }
  }

  /* ================================================================= open */

  function open() {
    overlay.hidden = false;
    field.value = '';
    render('');
    field.focus();
  }

  function close() {
    overlay.hidden = true;
    field.value = '';
  }

  const isOpen = () => !overlay.hidden;

  field.addEventListener('input', () => render(field.value));

  field.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
    else if (e.key === 'Enter')  { e.preventDefault(); run(); }
    else if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'Tab')    { e.preventDefault(); move(e.shiftKey ? -1 : 1); }
  });

  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

  J.palette = { open, close, isOpen };

})(window.J);
