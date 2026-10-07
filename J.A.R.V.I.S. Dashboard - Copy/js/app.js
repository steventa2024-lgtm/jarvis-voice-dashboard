/* ==========================================================================
   app.js — wiring

   Owns the DOM: boot sequence, transcript rendering, composer, settings sheet,
   keyboard. Every other module talks to this one through J.on/J.emit rather
   than by reaching into the page, so nothing here is load-order sensitive
   beyond core.js existing first.

   Streaming render: deltas append into a text node and the markdown parser
   runs once, at the end. Re-parsing on every chunk is what makes streaming
   chat UIs feel heavy, and it is entirely avoidable.
   ========================================================================== */

(function (J) {

  const convo = J.$('#convo');
  const input = J.$('#input');

  /* Mission progress is event-driven and confined to the transcript. */
  let missionCard = null;
  function renderMission(m) {
    if (!m) return;
    if (!missionCard || !missionCard.isConnected || missionCard.dataset.mission !== m.id) {
      missionCard = bubble('mission', 'MISSION');
      missionCard.classList.add('mission-card');
      missionCard.dataset.mission = m.id;
    }
    missionCard.replaceChildren();
    const heading = document.createElement('strong');
    heading.textContent = m.title;
    const state = document.createElement('div');
    state.className = 'mission-state'; state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite');
    const done = m.steps.filter(s => s.status === 'completed').length;
    state.textContent = (m.durable ? 'SCHEDULED MISSION · ' : '') + m.status.toUpperCase().replace(/_/g, ' ') + ' · ' + done + '/' + m.steps.length;
    const list = document.createElement('ol');
    list.className = 'mission-steps';
    const symbols = { completed: '✓', running: '●', verifying: '◉', failed: '!', blocked: '!', skipped: '–', pending: '○' };
    for (const s of m.steps) {
      const li = document.createElement('li');
      li.textContent = (symbols[s.status] || '○') + ' ' + s.title + ' — ' + s.status;
      if (s.resultSummary) li.title = s.resultSummary;
      list.appendChild(li);
    }
    missionCard.append(heading, state, list);
    if (m.outcome) { const note = document.createElement('div'); note.className = 'mission-note'; note.textContent = m.outcome; missionCard.appendChild(note); }
    if (!['completed', 'failed', 'cancelled'].includes(m.status)) {
      const controls = document.createElement('div'); controls.className = 'mission-controls';
      if (['waiting', 'blocked'].includes(m.status) && m.steps.length) {
        const resume = document.createElement('button'); resume.type = 'button'; resume.textContent = 'Continue mission';
        resume.addEventListener('click', () => J.brain.send('continue mission')); controls.appendChild(resume);
      }
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel mission';
      cancel.addEventListener('click', () => J.brain.abort()); controls.appendChild(cancel); missionCard.appendChild(controls);
    }
    stickToBottom(false);
  }
  J.on('mission:update', renderMission);
  J.on('conversation-cleared', () => { if (missionCard) missionCard.closest('.msg').remove(); missionCard = null; });

  /* ============================================================ transcript */

  let live = null;        // the assistant bubble currently streaming
  let buffer = '';        // text accumulated for this turn
  let thinkBox = null;
  let pendingPaint = false;

  function nearBottom() {
    return convo.scrollHeight - convo.scrollTop - convo.clientHeight < 140;
  }

  function stickToBottom(force) {
    if (force || nearBottom()) convo.scrollTop = convo.scrollHeight;
  }

  function bubble(kind, tag) {
    const row = document.createElement('div');
    row.className = 'msg ' + kind;

    const t = document.createElement('div');
    t.className = 'msg-tag';
    t.textContent = tag;

    /* .msg is a flex row: tag beside content. Everything that stacks — the
       reasoning trace, tool notes, the bubble itself, citation chips — lives
       in a column of its own, so live.parentNode is always that column. */
    const col = document.createElement('div');
    col.className = 'msg-col';

    const body = document.createElement('div');
    body.className = 'msg-body';

    col.appendChild(body);
    row.append(t, col);
    convo.appendChild(row);
    return body;
  }

  function addUser(text) {
    const body = bubble('user', 'YOU');
    body.textContent = text;
    stickToBottom(true);
  }

  function addError(text) {
    const body = bubble('err', 'FAULT');
    body.textContent = text;
    stickToBottom(true);
    J.log(text, 'crit', 'net');
  }

  /* Text arrives in small deltas; painting is coalesced to one write per
     frame so a fast stream cannot outrun the compositor. */
  function paint() {
    pendingPaint = false;
    if (!live) return;
    live.textContent = buffer;
    stickToBottom();
  }

  function queuePaint() {
    if (pendingPaint) return;
    pendingPaint = true;
    requestAnimationFrame(paint);
  }

  function note(text, cls) {
    if (!live) return null;
    const n = document.createElement('div');
    n.className = 'tool-note ' + (cls || '');
    n.textContent = text;
    live.parentNode.insertBefore(n, live);
    stickToBottom();
    return n;
  }

  /* ================================================================ turn */

  function beginTurn(text) {
    addUser(text);

    const body = bubble('jarvis', 'J.A.R.V.I.S.');
    body.classList.add('streaming');
    live = body;
    buffer = '';
    thinkBox = null;

    J.mode('processing', 'busy');
    J.status('thinking', 'working on it');
    J.orb.setState('thinking');
    J.$('#stopBtn').hidden = false;
    J.$('#sendBtn').hidden = true;
  }

  function endTurn() {
    if (live) {
      live.classList.remove('streaming');
      // one markdown pass, now that the text is final
      if (buffer.trim()) live.innerHTML = J.md(buffer);
      else if (!live.textContent.trim()) live.closest('.msg').remove();
    }
    if (!J.tasks || !J.tasks.background()) J.voice.flush();

    live = null;
    buffer = '';
    thinkBox = null;

    J.mode('standby', 'live');
    J.status('standby', hintLine());
    if (!J.voice.isSpeaking()) J.orb.setState(J.voice.isListening() ? 'listening' : 'idle');
    J.$('#stopBtn').hidden = true;
    J.$('#sendBtn').hidden = false;
    stickToBottom();
  }

  function hintLine() {
    return J.voice.isListening()
      ? 'listening — just talk'
      : 'hold space to speak · ctrl+k for commands';
  }

  J.on('turn-start', beginTurn);

  J.on('text', chunk => {
    buffer += chunk;
    queuePaint();
    if (!J.tasks || !J.tasks.background()) J.voice.feed(chunk);
    if (J.orb.getState() === 'thinking' && !J.settings.speak) J.orb.setState('speaking');
    J.status('responding', null);
  });

  J.on('thinking', chunk => {
    if (!J.settings.showThinking || !live) return;
    if (!thinkBox) {
      thinkBox = document.createElement('div');
      thinkBox.className = 'trace';
      live.parentNode.insertBefore(thinkBox, live);
    }
    thinkBox.textContent += chunk;
    stickToBottom();
  });

  const TOOL_LABEL = {
    web_search:        'searching the web',
    web_fetch:         'reading a page',
    control_interface: 'operating the interface',
    remember:          'committing to memory',
    forget:            'discarding a memory'
  };

  J.on('tool-start', info => {
    note((TOOL_LABEL[info.name] || info.name) + '…');
    if (info.name === 'web_search') J.telemetry.bump('search');
    J.status('working', TOOL_LABEL[info.name] || info.name);
  });

  J.on('tool-done', () => {
    const notes = J.$$('.tool-note', convo);
    const last = notes[notes.length - 1];
    if (last) last.classList.add('done');
  });

  /* Search and fetch results carry their sources; surface them as chips so an
     answer that leaned on the web says so on screen. */
  J.on('tool-result', block => {
    const items = Array.isArray(block.content) ? block.content : [block.content];
    const links = items.filter(i => i && i.url);
    if (!links.length || !live) return;

    const wrap = document.createElement('div');
    wrap.className = 'cites';
    links.slice(0, 8).forEach(i => {
      const a = document.createElement('a');
      a.className = 'cite';
      a.href = i.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      let host = i.url;
      try { host = new URL(i.url).hostname.replace(/^www\./, ''); } catch (e) {}
      a.textContent = host;
      a.title = i.title || i.url;
      wrap.appendChild(a);
    });
    live.parentNode.insertBefore(wrap, live.nextSibling);
    stickToBottom();
  });

  J.on('paused', () => J.status('working', 'continuing'));
  J.on('turn-error', msg => { addError(msg); });
  J.on('turn-end', endTurn);

  /* ============================================================== sending */

  function send(text) {
    text = (text || '').trim();
    if (!text) return;
    input.value = '';
    autosize();
    suggestions([]);
    J.brain.send(text);
  }

  J.on('utterance', text => {
    J.log('Heard: ' + text, 'acc', 'sys');
    send(text);
  });

  /* ============================================================ composer */

  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 150) + 'px';
  }

  input.addEventListener('input', autosize);

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(input.value);
    }
  });

  J.$('#sendBtn').addEventListener('click', () => send(input.value));
  J.$('#stopBtn').addEventListener('click', () => J.brain.abort());
  J.$('#micBtn').addEventListener('click', () => J.voice.toggle());
  J.$('#orbHit').addEventListener('click', () => J.voice.toggle());

  J.on('listening', on => {
    J.status(on ? 'listening' : 'standby', hintLine());
    if (on) J.log('Microphone live', 'ok', 'sys');
  });

  /* ---- suggestions ---- */

  const STARTERS = [
    'What is worth knowing today?',
    'Explain quantum error correction like I already know linear algebra.',
    'What is the weather doing for the rest of the day?',
    'Set a timer for 20 minutes.',
    'Make the interface amber.'
  ];

  function suggestions(list) {
    const host = J.$('#suggestions');
    host.innerHTML = '';
    list.forEach(text => {
      const b = document.createElement('button');
      b.className = 'sugg';
      b.textContent = text;
      b.addEventListener('click', () => send(text));
      host.appendChild(b);
    });
  }

  /* ================================================================ alert */

  const alertBox = J.$('#alert');

  function showAlert(title, detail) {
    J.$('#alertTitle').textContent = title;
    J.$('#alertDetail').textContent = detail;
    alertBox.hidden = false;
  }

  J.$('#alertClose').addEventListener('click', () => { alertBox.hidden = true; });

  /* An environmental fault — no secure context, denied permission, no input
     device — is not something a toast should carry away after five seconds. */
  J.on('mic-problem', p => showAlert(p.title, p.detail));

  /* A link he wants to show, rendered into the transcript instead of seizing
     the screen. One click and it opens — the user decides when. */
  J.on('link-card', ({ url, why }) => {
    let host = url;
    try { host = new URL(url).hostname.replace(/^www\./, ''); } catch (e) {}

    const card = document.createElement('a');
    card.className = 'link-card';
    card.href = url;
    card.target = '_blank';
    card.rel = 'noopener noreferrer';
    card.innerHTML = '<span class="link-ico">&#8599;</span>'
      + '<span class="link-text"><b>' + J.esc(host) + '</b><span>' + J.esc(url) + '</span></span>';

    const convo = J.$('#convo');
    if (convo) { convo.appendChild(card); convo.scrollTop = convo.scrollHeight; }
  });

  /* ---- diff review ----

     A change he wants to make, shown before it exists. The turn is genuinely
     blocked on this card, so it has to be readable at a glance and obvious to
     answer: what file, how much of it, and two buttons. */

  /* ---- the job batch ----

     Three matches, on screen, with the buttons to answer them.

     This exists because relying on the model to relay them does not work.
     Observed live: gpt-oss received all three with their scores and replied
     with nothing but "Option 1, 2, or 3, or decline all?" — the question
     without the options. The listings are data the interface already has, so
     the interface draws them and the reply can be as terse as it likes.

     The buttons speak as the user rather than calling the tool directly, so a
     click and "option two" out loud take exactly the same path. */
  /* Is this round already on screen anywhere?

     Deliberately asks the DOM rather than trusting a variable. A variable said
     "already drawn" and was wrong: the first poll drew the card, then the
     session restore rebuilt #convo underneath it, and because the variable
     still held the round it was never drawn again. The hunt showed "choose" in
     the header with nothing on screen to choose from. */
  function huntOnScreen(round) {
    return !!document.querySelector('[data-hunt-round="' + round + '"]');
  }

  /* The hunt finished because a job was picked. Shown, not narrated. */
  J.on('hunt-chosen', ({ job, filled, url, followed }) => {
    const pop = showPop({
      title: 'Application form open',
      accent: 'ok',
      sticky: true,
      items: [
        { title: (job.title || 'the listing') + (job.company ? '  ·  ' + job.company : ''),
          rows: [{ k: 'Filled', v: (filled && filled.length ? filled.length : 0) + ' field(s)' },
                 { k: 'Path', v: followed || 'opened directly' }] },
        { text: 'Review it and press submit yourself. Nothing was submitted.' }
      ]
    });
    if (pop && url) {
      const link = document.createElement('div');
      link.className = 'pop-actions';
      const a = document.createElement('a');
      a.className = 'pop-btn';
      a.textContent = 'Where it landed';
      a.href = url; a.target = '_blank'; a.rel = 'noopener';
      a.addEventListener('click', ev => ev.stopPropagation());
      link.appendChild(a);
      pop.appendChild(link);
    }
    J.log('Application form opened — ' + (job.title || 'listing') + ', '
        + ((filled && filled.length) || 0) + ' field(s) filled, nothing submitted', 'ok', 'sys');
  });

  J.on('hunt-batch', ({ round, key, batch, fresh }) => {
    if (!Array.isArray(batch) || !batch.length) return;
    if (key == null) key = round;
    if (key != null && huntOnScreen(key)) return;

    /* The one thing that speaks unprompted, and only this.
       "Nothing speaks unprompted" is a standing rule and stays one; Zero
       asked for a single exception, scoped to a hunt he switched on himself:
       a fresh three arriving on the ten-minute timer says so out loud, so it
       can be answered without watching the screen. `fresh` is set only by the
       poller, so a batch he asked for is announced by the reply, not twice.
       Stops the moment the hunt stops. */
    if (fresh && J.settings.speak && J.voice && J.voice.say) {
      J.voice.say('Sorry for the interruption. I found ' + batch.length
                + ' new job matches you may want to look at.');
    }

    /* The pop-up, which is what was actually asked for.

       It lives in the pop host rather than the transcript, so nothing that
       rebuilds the conversation can take it away, and it is sticky because a
       card you are meant to choose from must not retire itself while you are
       reading it. */
    const pop = showPop({
      title: 'Three job matches' + (round ? ' · batch ' + round : ''),
      accent: 'ok',
      sticky: true,
      items: batch.slice(0, 3).map((j, i) => ({
        title: (i + 1) + '.  ' + (j.title || 'untitled') + '   ' + (j.score != null ? j.score + '%' : ''),
        rows: [{ k: j.company || 'company not named',
                 v: (j.location || '') + (j.source ? '  ·  ' + j.source : '') }]
      }))
    });

    if (pop) {
      pop.setAttribute('data-hunt-round', String(key));
      const picks = document.createElement('div');
      picks.className = 'pop-actions';
      batch.slice(0, 3).forEach((j, i) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'pop-btn';
        b.textContent = String(i + 1);
        b.title = j.title || '';
        b.addEventListener('click', ev => {
          ev.stopPropagation();          // the card toggles pinned on click
          picks.querySelectorAll('button').forEach(x => { x.disabled = true; });
          if (J.voice && J.voice.expect) J.voice.expect(null);
          send('Option ' + (i + 1));
        });
        picks.appendChild(b);
      });
      const skip = document.createElement('button');
      skip.type = 'button';
      skip.className = 'pop-btn ghost';
      skip.textContent = 'Decline all';
      skip.addEventListener('click', ev => {
        ev.stopPropagation();
        picks.querySelectorAll('button').forEach(x => { x.disabled = true; });
        send('Decline all — show me the next batch.');
      });
      picks.appendChild(skip);
      pop.appendChild(picks);
    }

    /* Tell the recogniser what an answer to this looks like, so "let's go with
       option one" reaches him without a wake word while the card is up. */
    if (J.voice && J.voice.expect) {
      J.voice.expect(/\b(?:option|number)\s*(?:one|two|three|1|2|3)\b|\bdecline(?:\s+all)?\b|\bnext\s+batch\b/i);
    }

    const card = document.createElement('div');
    card.className = 'hunt-card';
    card.setAttribute('data-hunt-round', String(key));

    const head = document.createElement('div');
    head.className = 'hunt-head';
    head.textContent = 'Batch ' + (round || 1) + ' — three matches';
    card.appendChild(head);

    batch.forEach((job, i) => {
      const row = document.createElement('div');
      row.className = 'hunt-row';

      const num = document.createElement('span');
      num.className = 'hunt-num';
      num.textContent = String(i + 1);

      const body = document.createElement('div');
      body.className = 'hunt-body';
      const title = document.createElement('a');
      title.className = 'hunt-title';
      title.textContent = job.title || 'untitled';
      if (job.url) { title.href = job.url; title.target = '_blank'; title.rel = 'noopener'; }
      const where = document.createElement('div');
      where.className = 'hunt-where';
      where.textContent = (job.company || 'company not named') + ' · ' + (job.location || '')
                        + (job.source ? '  ·  ' + job.source : '');
      body.append(title, where);
      if (job.why) {
        const why = document.createElement('div');
        why.className = 'hunt-why';
        why.textContent = job.why;
        body.appendChild(why);
      }

      const score = document.createElement('span');
      score.className = 'hunt-score';
      score.textContent = (job.score != null ? job.score : '—') + '%';

      row.append(num, body, score);
      card.appendChild(row);
    });

    const actions = document.createElement('div');
    actions.className = 'hunt-actions';
    batch.forEach((_, i) => {
      const b = document.createElement('button');
      b.className = 'btn';
      b.type = 'button';
      b.textContent = 'Option ' + (i + 1);
      b.addEventListener('click', () => {
        actions.querySelectorAll('button').forEach(x => { x.disabled = true; });
        send('Option ' + (i + 1));
      });
      actions.appendChild(b);
    });
    const no = document.createElement('button');
    no.className = 'btn ghost';
    no.type = 'button';
    no.textContent = 'Decline all';
    no.addEventListener('click', () => {
      actions.querySelectorAll('button').forEach(x => { x.disabled = true; });
      send('Decline all — show me the next batch.');
    });
    actions.appendChild(no);
    card.appendChild(actions);

    convo.appendChild(card);
    stickToBottom(true);
  });

  J.on('diff-review', ({ id, project, path, diff, added, removed, existed, why, warn }) => {
    const card = document.createElement('div');
    card.className = 'diff-card';

    const head = document.createElement('div');
    head.className = 'diff-head';
    head.innerHTML =
      '<span class="diff-verb">' + (existed ? 'replace' : 'create') + '</span>'
      + '<span class="diff-path"></span>'
      + '<span class="diff-stat"><b class="up">+' + added + '</b> '
      + '<b class="down">&minus;' + removed + '</b></span>';
    head.querySelector('.diff-path').textContent = project + '/' + path;
    card.appendChild(head);

    /* "+13 −262" is an accurate summary of a catastrophe and reads like an
       ordinary edit. Twice now the whole design foundation has been approved
       away from a card showing exactly that, so when a change replaces a file
       rather than editing it, the card says so in words above the diff. */
    if (warn) {
      const alarm = document.createElement('div');
      alarm.className = 'diff-warn';
      alarm.textContent = warn;
      card.appendChild(alarm);
    }

    if (why) {
      const note = document.createElement('div');
      note.className = 'diff-why';
      note.textContent = why;
      card.appendChild(note);
    }

    const body = document.createElement('pre');
    body.className = 'diff-body';
    for (const line of String(diff || '').split('\n')) {
      const row = document.createElement('span');
      row.className = 'dl ' + (line.startsWith('+++') || line.startsWith('---') ? 'meta'
                             : line.startsWith('@@') ? 'hunk'
                             : line.startsWith('+') ? 'add'
                             : line.startsWith('-') ? 'del' : '');
      row.textContent = line || ' ';
      body.appendChild(row);
    }
    card.appendChild(body);

    const row = document.createElement('div');
    row.className = 'diff-actions';
    const yes = document.createElement('button');
    yes.className = 'btn';
    yes.type = 'button';
    yes.textContent = 'Apply';
    const no = document.createElement('button');
    no.className = 'btn ghost';
    no.type = 'button';
    no.textContent = 'Reject';
    row.append(yes, no);
    card.appendChild(row);

    card.dataset.reviewId = id;

    /* The click is a request, not the outcome. Marking the card applied here
       would be a claim about the disk made before anything touched it — and a
       proposal can still be refused, most often because it expired. The real
       verdict arrives on review-result. */
    let answered = false;
    function decide(verdict) {
      if (answered) return;
      answered = true;
      row.remove();
      const waiting = document.createElement('div');
      waiting.className = 'diff-verdict';
      waiting.textContent = verdict === 'approve' ? 'applying…' : 'discarding…';
      card.appendChild(waiting);
      J.emit('review-decision', { id: id, verdict: verdict });
    }

    yes.addEventListener('click', () => decide('approve'));
    no.addEventListener('click', () => decide('reject'));

    convo.appendChild(card);
    stickToBottom(true);
    yes.focus();

    // the turn really is blocked on this; the spinner should say so
    taskStart('files', { action: 'write', path: 'waiting on you' });
  });

  /* What actually happened to it, once the disk has answered. Also closes a
     card nobody ever clicked — an expired proposal must not keep offering an
     Apply button that can no longer do anything. */
  J.on('review-result', ({ id, ok, text }) => {
    const card = convo.querySelector('.diff-card[data-review-id="' + CSS.escape(id) + '"]');
    if (!card) return;

    card.classList.remove('approved', 'rejected');
    card.classList.add(ok ? 'approved' : 'rejected');

    const actions = card.querySelector('.diff-actions');
    if (actions) actions.remove();

    let line = card.querySelector('.diff-verdict');
    if (!line) {
      line = document.createElement('div');
      line.className = 'diff-verdict';
      card.appendChild(line);
    }
    line.textContent = text;
  });

  /* ---- what he saw ----

     The vision model's description is what reaches the conversation, and a
     description is not evidence. Putting the actual frame on screen is what
     lets the user tell a real observation from a confident one. */

  J.on('shot', ({ dataUrl, project, path, width, height }) => {
    const card = document.createElement('figure');
    card.className = 'shot-card';

    const img = document.createElement('img');
    /* The real dimensions up front, so the card reserves its space before the
       image decodes and the transcript does not jump under the user. Not
       lazy-loaded: there is no network fetch to defer on a data URL, and a
       card below the fold stayed collapsed to a single pixel. */
    img.width = width;
    img.height = height;
    img.src = dataUrl;
    img.alt = 'Rendered ' + project + '/' + path;

    const cap = document.createElement('figcaption');
    cap.textContent = project + '/' + path + ' — ' + width + '×' + height;

    /* The card is a thumbnail — at 500px across, a 1280px page is unreadable.
       Clicking opens the frame at the size it was actually rendered. */
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.title = 'Click to see it full size';
    const open = () => showLightbox(dataUrl, project, path, width, height);
    card.addEventListener('click', open);
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });

    card.append(img, cap);
    convo.appendChild(card);
    stickToBottom(true);
  });

  /* ---- full-size view ----

     A screenshot he took is evidence, and evidence you cannot read is not
     doing its job. This shows the frame at its rendered size, and offers the
     live page in a real browser beside it — the image is a moment, the page
     is the thing. */

  const lightbox = J.$('#lightbox');
  const lightboxImg = J.$('#lightboxImg');
  let lightboxProject = null;

  function showLightbox(dataUrl, project, path, width, height) {
    if (!lightbox) return;
    lightboxProject = project;
    lightboxImg.src = dataUrl;
    lightboxImg.alt = 'Rendered ' + project + '/' + path;
    J.$('#lightboxTitle').textContent = project + '/' + path + ' — ' + width + '×' + height;
    lightbox.hidden = false;
    J.$('#lightboxClose').focus();
  }

  function hideLightbox() {
    if (!lightbox || lightbox.hidden) return;
    lightbox.hidden = true;
    lightboxImg.src = '';           // a full-size data URL is worth releasing
    lightboxProject = null;
  }

  if (lightbox) {
    J.$('#lightboxClose').addEventListener('click', hideLightbox);
    J.$('#lightboxOpen').addEventListener('click', () => {
      if (lightboxProject) openExternally(previewURL(lightboxProject));
    });
    // clicking the backdrop closes; clicking the image or bar must not
    lightbox.addEventListener('click', e => {
      if (e.target === lightbox) hideLightbox();
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && !lightbox.hidden) { e.stopPropagation(); hideLightbox(); }
    }, true);
  }

  /* The build was checked. A render leaves a shot card behind, but a run
     leaves nothing at all — and a check you cannot see is indistinguishable
     from one that never happened. */
  J.on('build-check', ({ project, mode, subject }) => {
    const note = document.createElement('div');
    note.className = 'check-note';
    note.textContent = 'checked the build — ' + (mode || 'inspected') + ' '
                     + project + '/' + (subject || '');
    convo.appendChild(note);
    stickToBottom(true);
  });

  /* Finishing a reply counts as activity — the idle countdown should run from
     the end of the exchange, not from the moment the user stopped talking. */
  J.on('turn-end', () => J.voice.touch());

  J.on('wake-state', up => {
    document.body.classList.toggle('awake', up);
    J.status(up ? 'listening' : 'standby',
             up ? 'listening — just talk' : 'say “Hey Jarvis” to wake me');
  });

  /* ============================================================= keyboard */

  const typing = () => {
    const a = document.activeElement;
    return a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT' || a.isContentEditable);
  };

  let spaceHeld = false;

  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      if (J.palette && J.palette.isOpen()) return;          // palette handles its own
      if (!J.$('#settingsOverlay').hidden) { closeSettings(); return; }
      if (J.brain.isBusy()) J.brain.abort();
      else J.voice.shutUp();
      return;
    }

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      J.palette.open();
      return;
    }

    if ((e.ctrlKey || e.metaKey) && e.key === '/') { e.preventDefault(); openSettings(); return; }

    /* Hold space to talk — only outside a text field, and only as a hold, so a
       tap does not toggle the mic on and off. */
    if (e.code === 'Space' && !typing() && !e.repeat) {
      e.preventDefault();
      spaceHeld = true;
      if (!J.voice.isListening()) J.voice.startListening();
    }

    // any other printable key jumps focus into the composer
    if (!typing() && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      input.focus();
    }
  });

  window.addEventListener('keyup', e => {
    if (e.code === 'Space' && spaceHeld) {
      spaceHeld = false;
      if (!typing()) J.voice.stopListening();
    }
  });

  /* ============================================================= settings */

  const overlay = J.$('#settingsOverlay');

  function openSettings() {
    adoptTypedKeys();
    fillDevices();
    refreshSpotify();
    refreshGoogle();
    overlay.hidden = false;
    renderVoices();
    renderMemories();
    J.$('#setKey').focus();
  }
  function closeSettings() { overlay.hidden = true; }

  J.$('#settingsBtn').addEventListener('click', openSettings);
  J.$('#settingsClose').addEventListener('click', closeSettings);
  overlay.addEventListener('click', e => { if (e.target === overlay) closeSettings(); });
  J.$('#paletteBtn').addEventListener('click', () => J.palette.open());

  /* ---- bind each control to one setting ---- */

  function bind(sel, key, kind, after) {
    const el = J.$(sel);
    if (!el) return;

    if (kind === 'check') el.checked = !!J.settings[key];
    else el.value = J.settings[key];

    /* Text fields commit on every keystroke. `change` only fires when a field
       loses focus, so pasting an API key and closing the sheet with Escape
       used to discard it silently — the dashboard would keep insisting there
       was no key. Selects and checkboxes genuinely want `change`. */
    const ev = (kind === 'check' || el.tagName === 'SELECT') ? 'change' : 'input';
    el.addEventListener(ev, () => {
      const v = kind === 'check'  ? el.checked
              : kind === 'number' ? parseFloat(el.value)
              : el.value;
      J.set({ [key]: v });
      if (after) after(v);
    });
  }

  bind('#setKey',      'apiKey',       'text');
  bind('#setModel',    'model',        'text', v => { J.$('#modelVal').textContent = v; });
  bind('#setEffort',   'effort',       'text');
  bind('#setSearch',   'webSearch',    'check');
  bind('#setThinking', 'showThinking', 'check');
  bind('#setUserName', 'userName',     'text');
  bind('#setPersona',  'persona',      'text');
  bind('#setSpeak',    'speak',        'check', v => { if (!v) J.voice.shutUp(); });
  bind('#setWake',     'wakeWord',     'check');
  bind('#setBargeIn',  'bargeIn',      'text');
  bind('#setAmbient',  'ambientAfter', 'number');
  bind('#setVoice',    'voiceURI',     'text');
  bind('#setRate',     'rate',         'number', v => { J.$('#rateVal').textContent = v.toFixed(2) + '×'; });
  bind('#setDensity',  'density',      'number', v => { J.$('#densVal').textContent = v; });
  bind('#setUnits',    'units',        'text',   () => J.telemetry.refreshWeather());
  bind('#setMask',     'mask',         'text');
  bind('#setProvider', 'provider',     'text',   showProviderFields);
  bind('#setAltBase',  'altBase',      'text');
  bind('#setAltKey',   'altKey',       'text');
  bind('#setAltModel', 'altModel',     'text');
  bind('#setVisionModel', 'visionModel', 'text');
  bind('#setVisionConn',  'visionConn',  'text');
  bind('#setFastModel',   'fastModel',   'text');
  bind('#setPopSeconds',  'popSeconds',  'text');
  bind('#setCritique',    'critique',    'text');
  bind('#setReviewWrites', 'reviewWrites', 'text');
  bind('#setBuildCheck',   'buildCheck',   'text');
  bind('#setRouteLocal', 'routeLocal', 'check');
  bind('#setSleepAfter', 'sleepAfter', 'text');
  bind('#setAutoListen', 'autoListen', 'check');
  bind('#setLinkMode',   'linkMode',   'text');
  bind('#setSearchKey',  'searchKey',  'text');
  bind('#setGoogleKey',  'googleKey',  'text');
  bind('#setGoogleCx',   'googleCx',   'text');
  bind('#setPitch',    'pitch',        'number', v => { J.$('#pitchVal').textContent = v.toFixed(2); });

  const jarvisVoiceBtn = J.$('#voiceJarvis');
  if (jarvisVoiceBtn) jarvisVoiceBtn.addEventListener('click', () => {
    const r = J.voice.useJarvisVoice();
    const note = J.$('#voiceNote');

    if (!r.ok) {
      if (note) { note.textContent = r.reason; note.className = 'key-state crit'; }
      return;
    }

    renderVoices();
    J.$('#setRate').value = J.settings.rate;
    const p = J.$('#setPitch');
    if (p) p.value = J.settings.pitch;

    if (note) {
      note.textContent = r.name + ' — ' + r.reason;
      note.className = 'key-state ' + (r.british ? 'ok' : 'warn');
    }
    if (!r.british) {
      note.textContent += ' — install one: Windows Settings, Time & language,'
        + ' Speech, Manage voices, Add English (United Kingdom).';
      fetch('api/open', { method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ settings: 'speech' }) }).catch(() => {});
    }
    J.voice.say('Good evening. All systems are online.');
  });

  /* ---- audio devices ---- */

  async function fillDevices() {
    const sel = J.$('#setMicDevice');
    const note = J.$('#micDeviceNote');
    const outList = J.$('#outputList');
    if (!sel) return;

    const d = await J.voice.listDevices();

    sel.innerHTML = '';
    const dflt = document.createElement('option');
    dflt.value = '';
    dflt.textContent = 'System default';
    sel.appendChild(dflt);

    d.inputs.forEach((dev, i) => {
      const o = document.createElement('option');
      o.value = dev.id;
      o.textContent = dev.label || ('Microphone ' + (i + 1));
      sel.appendChild(o);
    });
    sel.value = J.settings.micDevice || '';

    if (note) {
      note.textContent = d.labelled
        ? d.inputs.length + ' input device(s) found'
        : 'Allow the microphone once and reopen this panel to see device names.';
      note.className = 'key-state' + (d.labelled ? ' ok' : '');
    }

    if (outList) {
      outList.textContent = d.outputs.length
        ? d.outputs.map(o => o.label).filter(Boolean).join(' · ') || 'names hidden until permission'
        : 'none detected';
    }
  }

  const micSel = J.$('#setMicDevice');
  if (micSel) micSel.addEventListener('change', async () => {
    const res = await J.voice.switchDevice(micSel.value);
    if (res && res.ok) J.toast('Microphone switched', 'ok');
  });

  fillDevices();

  /* Only ever show the fields belonging to the selected provider — two sets of
     key boxes on screen at once is how people end up filling in the wrong one. */
  function showProviderFields() {
    const want = J.settings.provider || 'anthropic';
    document.querySelectorAll('[data-provider]').forEach(el => {
      el.hidden = el.getAttribute('data-provider') !== want;
    });
  }
  showProviderFields();

  J.$('#rateVal').textContent = J.settings.rate.toFixed(2) + '×';
  J.$('#pitchVal').textContent = (J.settings.pitch || 1).toFixed(2);
  J.$('#densVal').textContent = J.settings.density;

  /* ---- accent swatches ---- */

  const ACCENTS = ['#35d6ff', '#00e5a0', '#ffb454', '#ff5f8f', '#a97bff', '#ff4d3d', '#e8eef6'];

  function renderSwatches() {
    const host = J.$('#swatches');
    host.innerHTML = '';
    ACCENTS.forEach(hex => {
      const b = document.createElement('button');
      b.className = 'sw' + (hex.toLowerCase() === J.settings.accent.toLowerCase() ? ' on' : '');
      b.style.background = hex;
      b.style.color = hex;
      b.title = hex;
      b.addEventListener('click', () => {
        J.set({ accent: hex });
        J.applyAccent(hex);
        renderSwatches();
      });
      host.appendChild(b);
    });
  }

  /* ---- voices ---- */

  function renderVoices() {
    const sel = J.$('#setVoice');
    const list = J.voice.getVoices();
    sel.innerHTML = '';

    const auto = document.createElement('option');
    auto.value = '';
    auto.textContent = list.length ? 'Automatic — best available' : 'No voices reported by this browser';
    sel.appendChild(auto);

    list.forEach(v => {
      const o = document.createElement('option');
      o.value = v.voiceURI;
      o.textContent = v.name + ' — ' + v.lang + (v.localService ? '' : ' (network)');
      sel.appendChild(o);
    });
    sel.value = J.settings.voiceURI || '';
  }

  J.on('voices', renderVoices);

  /* ---- memories ---- */

  function renderMemories() {
    const host = J.$('#memList');
    const list = J.brain.getMemories();
    host.innerHTML = '';
    J.$('#memCount').textContent = list.length;

    list.forEach((fact, i) => {
      const row = document.createElement('div');
      row.className = 'mem';

      const p = document.createElement('p');
      p.textContent = fact;

      const x = document.createElement('button');
      x.textContent = '✕';
      x.title = 'Forget this';
      x.addEventListener('click', () => { J.brain.removeMemory(i); renderMemories(); });

      row.append(p, x);
      host.appendChild(row);
    });
  }

  J.on('memories', renderMemories);

  J.$('#memClear').addEventListener('click', () => {
    if (!J.brain.getMemories().length) return;
    J.brain.clearMemories();
    renderMemories();
    J.toast('Memory cleared.', 'warn');
  });

  /* ---- data ---- */

  J.$('#convoClear').addEventListener('click', () => {
    J.brain.clearConversation();
    J.toast('Conversation cleared.');
  });

  J.$('#convoExport').addEventListener('click', () => J.brain.exportTranscript());

  J.$('#resetAll').addEventListener('click', () => {
    J.resetSettings();
    J.applyAccent(J.settings.accent);
    J.toast('Settings reset. The API key was kept.', 'warn');
    setTimeout(() => location.reload(), 900);
  });

  J.on('conversation-cleared', () => suggestions(STARTERS.slice(0, 3)));

  /* ---- log filters ---- */

  J.$$('[data-logfilter]').forEach(btn => {
    btn.addEventListener('click', () => {
      J.$$('[data-logfilter]').forEach(b => b.classList.remove('is-on'));
      btn.classList.add('is-on');
      J.setLogFilter(btn.dataset.logfilter);
    });
  });

  J.$('#logCopy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(J.logText());
      J.toast('Log copied.', 'ok', 2200);
    } catch (e) {
      J.toast('Clipboard unavailable in this context.', 'warn');
    }
  });

  /* ---- cognition status ---- */

  /* The key can arrive at any moment and there is no address bar on a
     television, so nothing here may require a reload. */
  function refreshCognition() {
    const el = J.$('#keyState');
    const chip = J.$('#linkChip');
    const val = J.$('#linkVal');

    function show(state, sheet, topbar) {
      if (el) { el.textContent = sheet; el.className = 'key-state ' + state; }
      if (chip) chip.className = 'chip ' + state;
      if (val) val.textContent = topbar;
    }

    if (J.brain.getTransport() === 'proxy') {
      show('ok', 'online — key held server-side', 'proxy');
      return;
    }

    if (J.settings.provider === 'openai') {
      const base = (J.settings.altBase || '').trim();
      const local = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/.test(base);
      if (!base)                        show('crit', 'no base URL set', 'no url');
      else if (!J.settings.altModel)    show('crit', 'no model id set', 'no model');
      else if (!J.settings.altKey && !local) show('crit', 'no key for ' + base, 'no key');
      else show('ok', 'configured — press Verify to confirm it answers', 'ready');
      return;
    }

    const key = (J.settings.apiKey || '').trim();
    if (!key) {
      show('crit', 'no key — conversation disabled', 'no key');
    } else if (!/^sk-ant-/.test(key)) {
      show('warn', 'that does not look like an Anthropic key (expected sk-ant-…)', 'bad key');
    } else {
      show('ok', 'key stored — press Verify to confirm it works', 'ready');
    }
  }

  /* Clears the "no cognition" notice once a key exists, so the transcript
     stops contradicting the interface. */
  function dismissKeyNotice() {
    if (!J.brain.ready()) return;
    const notice = J.$('#keyNotice');
    if (notice) notice.closest('.msg').remove();
    if (!J.brain.isBusy()) J.mode('standby', 'live');
  }

  J.on('settings', () => {
    J.$('#modelVal').textContent = J.settings.model;
    refreshCognition();
    dismissKeyNotice();
  });

  /* ---- verify the key for real ---- */

  /* ---- saved connections ---- */

  function connections() {
    return Array.isArray(J.settings.connections) ? J.settings.connections : [];
  }

  function renderVisionConns() {
    const sel = J.$('#setVisionConn');
    if (!sel) return;
    sel.innerHTML = '';

    const same = document.createElement('option');
    same.value = '';
    same.textContent = 'same as the main provider';
    sel.appendChild(same);

    for (const c of connections()) {
      const o = document.createElement('option');
      o.value = c.name;
      o.textContent = c.name;
      sel.appendChild(o);
    }
    sel.value = J.settings.visionConn || '';
  }

  function renderConnections() {
    renderVisionConns();
    const sel = J.$('#connPicker');
    if (!sel) return;
    const list = connections();
    sel.innerHTML = '';

    const now = document.createElement('option');
    now.value = '';
    now.textContent = '— current settings —';
    sel.appendChild(now);

    list.forEach((c, i) => {
      const local = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/.test(c.base || '');
      const o = document.createElement('option');
      o.value = String(i);
      // a local endpoint needs no key; saying "no key" there reads as a fault
      o.textContent = c.name + (c.key || local ? '' : ' — key needed');
      sel.appendChild(o);
    });

    // show which saved entry matches what is live, if any
    const active = list.findIndex(c => c.base === J.settings.altBase);
    if (active >= 0) sel.value = String(active);
  }

  /* Keep the saved entry in step with the live fields.
     Without this, typing a key and then switching connections discarded it,
     and clicking a model in the list changed the live setting but not the
     saved one - so coming back later found an empty connection and reported
     it as a rejected key. */
  function persistToActiveConnection() {
    const list = connections();
    const i = list.findIndex(c => c.base === J.settings.altBase);
    if (i < 0) return;

    const c = list[i];
    if (c.key === J.settings.altKey && c.model === J.settings.altModel) return;

    list[i] = Object.assign({}, c, {
      key: J.settings.altKey || '',
      model: J.settings.altModel || ''
    });
    J.set({ connections: list });
    renderConnections();
  }

  J.on('settings', persistToActiveConnection);

  const connPicker = J.$('#connPicker');
  if (connPicker) connPicker.addEventListener('change', () => {
    const c = connections()[Number(connPicker.value)];
    if (!c) return;
    J.set({ altBase: c.base, altKey: c.key || '', altModel: c.model || '' });
    J.$('#setAltBase').value = c.base;
    J.$('#setAltKey').value = c.key || '';
    J.$('#setAltModel').value = c.model || '';
    J.$('#connName').value = c.name;
    lastModelRows = null;
    J.$('#modelList').innerHTML =
      '<p class="model-empty">Press <b>List models</b> to see what this connection serves.</p>';
    J.toast('Switched to ' + c.name, 'ok');
    J.log('Connection switched to ' + c.name, 'ok', 'net');

    const local = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/.test(c.base || '');
    if (!c.key && !local) {
      J.$('#setAltKey').focus();
      J.toast('This connection has no key yet — paste one.', 'warn', 7000);
    } else if (!c.model) {
      J.toast('No model chosen — press List models and pick one.', 'warn', 7000);
    }
  });

  const connSave = J.$('#connSave');
  if (connSave) connSave.addEventListener('click', () => {
    const name = (J.$('#connName').value || '').trim();
    if (!name) { J.toast('Give the connection a name first.', 'warn'); return; }

    const list = connections().slice();
    const entry = { name: name, base: J.settings.altBase,
                    key: J.settings.altKey, model: J.settings.altModel };
    const at = list.findIndex(c => c.name.toLowerCase() === name.toLowerCase());
    if (at >= 0) list[at] = entry; else list.push(entry);

    J.set({ connections: list });
    renderConnections();
    J.toast('Saved "' + name + '"', 'ok');
  });

  const connDelete = J.$('#connDelete');
  if (connDelete) connDelete.addEventListener('click', () => {
    const i = Number(J.$('#connPicker').value);
    const list = connections().slice();
    if (!list[i]) { J.toast('Pick a saved connection to delete.', 'warn'); return; }
    const gone = list.splice(i, 1)[0];
    J.set({ connections: list });
    renderConnections();
    J.toast('Deleted "' + gone.name + '"', 'warn');
  });

  const freeOnlyBox = J.$('#freeOnly');
  if (freeOnlyBox) freeOnlyBox.addEventListener('change', () => {
    if (lastModelRows) renderModelList(lastModelRows.map(r => r.id));
  });

  renderConnections();

  const listBtn = J.$('#altModelList');
  if (listBtn) listBtn.addEventListener('click', async () => {
    const state = J.$('#altModelState');
    const box = J.$('#altModelOptions');
    listBtn.disabled = true;
    state.textContent = 'asking…';
    state.className = 'key-state';

    adoptTypedKeys();
    const res = await J.brain.listModels();
    listBtn.disabled = false;
    lastModelRows = res.rows || null;

    if (!res.ok) {
      state.textContent = res.message;
      state.className = 'key-state crit';
      return;
    }

    box.innerHTML = '';
    for (const id of res.ids) {
      const opt = document.createElement('option');
      opt.value = id;
      box.appendChild(opt);
    }
    renderModelList(res.ids);

    /* A free-text field with a datalist gives no hint that anything arrived,
       so say so and put the cursor where the list will drop down. */
    state.textContent = res.ids.length + ' available'
      + (res.freeCount ? ', ' + res.freeCount + ' free' : '')
      + ' — click one below to switch';
    state.className = 'key-state ok';
    J.log('Provider serves ' + res.ids.length + ' models', 'ok', 'net');
    J.$('#setAltModel').focus();
  });

  /* The model id is a free-text box, which is fine for typing but useless for
     browsing. This turns the provider's answer into a list you can click. */
  let lastModelRows = null;

  function renderModelList(ids) {
    const host = J.$('#modelList');
    if (!host) return;
    host.innerHTML = '';

    if (!ids || !ids.length) {
      host.innerHTML = '<p class="model-empty">Nothing returned.</p>';
      return;
    }

    const freeOnly = J.$('#freeOnly') && J.$('#freeOnly').checked;
    const meta = lastModelRows || [];
    const infoOf = id => meta.find(r => r.id === id) || {};

    for (const id of ids) {
      const info = infoOf(id);
      if (freeOnly && meta.length && !info.free) continue;

      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'model-row' + (id === J.settings.altModel ? ' active' : '');
      row.innerHTML = '<span class="dot"></span><span>' + J.esc(id) + '</span>'
        + (info.free ? '<span class="tag free">free</span>' : '')
        + (id === J.settings.altModel ? '<span class="tag">active</span>' : '');

      row.addEventListener('click', () => {
        J.set({ altModel: id });
        const field = J.$('#setAltModel');
        if (field) field.value = id;
        renderModelList(ids);
        J.toast('Switched to ' + id, 'ok');
        J.log('Model switched to ' + id, 'ok', 'net');
      });

      host.appendChild(row);
    }
  }

  /* ---- spotify ---- */

  async function refreshSpotify() {
    const el = J.$('#spotifyState');
    if (!el) return;
    try {
      const s = await (await fetch('api/spotify/status')).json();
      if (s.connected) {
        el.textContent = 'connected as ' + (s.user || 'your account')
          + (s.product && s.product !== 'premium' ? ' — ' + s.product + ', playback needs Premium' : '');
        el.className = 'key-state ' + (s.product === 'premium' || !s.product ? 'ok' : 'warn');
      } else if (s.has_client_id) {
        el.textContent = 'client ID saved — press Connect';
        el.className = 'key-state warn';
      } else {
        el.textContent = 'not connected';
        el.className = 'key-state';
      }
    } catch (e) {
      el.textContent = 'bridge unavailable — is serve.py running?';
      el.className = 'key-state crit';
    }
  }

  /* A revision arrives after the answer is already on screen, so it is shown as
     a visible correction rather than swapped in silently. Quietly rewriting
     something the user has already read is worse than admitting the change. */
  J.on('revised', text => {
    const msgs = document.querySelectorAll('.msg.jarvis');
    const last = msgs[msgs.length - 1];
    if (!last) return;

    const note = document.createElement('div');
    note.className = 'revision';
    note.innerHTML = '<b>On checking that again</b>' + J.md(text);
    last.querySelector('.msg-col').appendChild(note);

    const convo = J.$('#convo');
    if (convo) convo.scrollTop = convo.scrollHeight;
    J.toast('Revised after a self-check', 'warn', 6000);
  });

  /* ---- live preview ----

     Points at /preview/<project>/, which serve.py maps to the project folder.
     Reloaded on every write, so the page updates as he builds it. */

  const previewPane = J.$('#preview');
  const previewFrame = J.$('#previewFrame');
  let previewProject = null;

  function showPreview(project) {
    if (!previewPane || !project) return;
    previewProject = project;
    previewPane.hidden = false;
    J.$('#previewTitle').textContent = project;
    reloadPreview();
  }

  function reloadPreview() {
    if (!previewFrame || !previewProject) return;
    // cache-bust, or an edited file keeps showing its previous version
    previewFrame.src = 'preview/' + encodeURIComponent(previewProject)
                     + '/?t=' + Date.now();

    // flash the border so a change is noticed without watching for it
    previewPane.classList.remove('updated');
    void previewPane.offsetWidth;              // restart the animation
    previewPane.classList.add('updated');
  }

  J.on('preview', ({ project }) => {
    if (!project) return;
    showPreview(project);
  });

  const pReload = J.$('#previewReload');
  if (pReload) pReload.addEventListener('click', reloadPreview);

  /* The dashboard lives fullscreen on a television, so window.open lands in
     another tab of the same kiosk browser — which is not "open it in my
     browser". serve.py hands the URL to the real default browser instead, the
     same path a link card takes. */
  function previewURL(project) {
    return location.origin + '/preview/' + encodeURIComponent(project) + '/';
  }

  async function openExternally(url) {
    try {
      const d = await (await fetch('api/open', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ url: url })
      })).json();
      if (d.ok) return J.toast('Opened in your browser.', 'ok', 2600);
    } catch (e) { /* no server-side launcher; the tab below still works */ }
    window.open(url, '_blank', 'noopener');
  }

  const pOpen = J.$('#previewOpen');
  if (pOpen) pOpen.addEventListener('click', () => {
    if (previewProject) openExternally(previewURL(previewProject));
  });

  /* ---- bigger ----

     Two sizes rather than a free-for-all: the docked corner panel for glancing
     at a build as it happens, and a near-fullscreen one for actually reading
     the page. The grip below covers everything in between. */

  const pExpand = J.$('#previewExpand');
  let expanded = false;
  let docked = null;              // inline geometry to restore on collapse

  function setExpanded(on) {
    if (!previewPane) return;
    expanded = on;
    if (on) {
      docked = {
        left: previewPane.style.left, top: previewPane.style.top,
        right: previewPane.style.right, bottom: previewPane.style.bottom,
        width: previewPane.style.width, height: previewPane.style.height
      };
      previewPane.classList.add('big');
      // clear inline geometry so the class governs it, whether or not it was dragged
      previewPane.style.left = previewPane.style.top = '';
      previewPane.style.right = previewPane.style.bottom = '';
      previewPane.style.width = previewPane.style.height = '';
    } else {
      previewPane.classList.remove('big');
      if (docked) Object.assign(previewPane.style, docked);
    }
    if (pExpand) {
      pExpand.textContent = on ? 'Shrink' : 'Expand';
      pExpand.setAttribute('aria-pressed', String(on));
    }
  }

  if (pExpand) pExpand.addEventListener('click', () => setExpanded(!expanded));

  /* Escape backs out of the big view before it does anything else — it is the
     most modal thing on screen at that moment. */
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && expanded && previewPane && !previewPane.hidden) {
      e.stopPropagation();
      setExpanded(false);
    }
  }, true);

  /* Drag the corner to any size in between. Anchoring flips to left/top first,
     the same conversion the title-bar drag makes, or a pane held by its right
     edge grows away from the pointer.

     When it runs out of room it slides rather than stops. Clamping the width
     to whatever was left of the screen meant a pane docked in the right-hand
     corner — where it starts — could only grow about sixteen pixels before
     the grip went dead under the pointer. */
  (function makeResizable() {
    const grip = J.$('#previewGrip');
    if (!grip || !previewPane) return;
    const EDGE = 8;
    let sizing = false;
    let startX = 0, startY = 0, startW = 0, startH = 0, startL = 0, startT = 0;

    grip.addEventListener('pointerdown', e => {
      if (expanded) setExpanded(false);
      previewPane.classList.add('placed');
      const r = previewPane.getBoundingClientRect();
      sizing = true;
      startX = e.clientX; startY = e.clientY;
      startW = r.width; startH = r.height;
      startL = r.left; startT = r.top;
      previewPane.style.left = r.left + 'px';
      previewPane.style.top = r.top + 'px';
      previewPane.style.right = 'auto';
      previewPane.style.bottom = 'auto';
      previewPane.style.transition = 'none';
      // capture is what keeps the drag alive once the pointer crosses the
      // iframe; a device that will not give it up must not kill the resize
      try { grip.setPointerCapture(e.pointerId); } catch (err) {}
      e.preventDefault();
    });

    grip.addEventListener('pointermove', e => {
      if (!sizing) return;
      const w = Math.max(280, Math.min(window.innerWidth  - EDGE * 2, startW + e.clientX - startX));
      const h = Math.max(200, Math.min(window.innerHeight - EDGE * 2, startH + e.clientY - startY));
      previewPane.style.width = w + 'px';
      previewPane.style.height = h + 'px';
      previewPane.style.left = Math.max(EDGE, Math.min(startL, window.innerWidth  - w - EDGE)) + 'px';
      previewPane.style.top  = Math.max(EDGE, Math.min(startT, window.innerHeight - h - EDGE)) + 'px';
    });

    grip.addEventListener('pointerup', e => {
      sizing = false;
      previewPane.style.transition = '';
      try { grip.releasePointerCapture(e.pointerId); } catch (err) {}
    });
  })();

  const pClose = J.$('#previewClose');
  if (pClose) pClose.addEventListener('click', () => {
    previewPane.classList.add('closing');
    setTimeout(() => {
      previewPane.hidden = true;
      previewPane.classList.remove('closing');
      previewFrame.src = 'about:blank';
      previewProject = null;
    }, 220);
  });

  /* Draggable by its bar. A floating panel that cannot be moved is just a
     panel in a different place. */
  (function makeDraggable() {
    const bar = document.querySelector('.preview-bar');
    if (!bar || !previewPane) return;
    let dragging = false, ox = 0, oy = 0;

    bar.addEventListener('pointerdown', e => {
      if (e.target.closest('button')) return;       // let the buttons work
      previewPane.classList.add('placed');
      const r = previewPane.getBoundingClientRect();
      dragging = true;
      ox = e.clientX - r.left;
      oy = e.clientY - r.top;
      previewPane.style.transition = 'none';
      bar.setPointerCapture(e.pointerId);
    });

    bar.addEventListener('pointermove', e => {
      if (!dragging) return;
      const w = previewPane.offsetWidth, h = previewPane.offsetHeight;
      const x = Math.max(8, Math.min(window.innerWidth - w - 8, e.clientX - ox));
      const y = Math.max(8, Math.min(window.innerHeight - h - 8, e.clientY - oy));
      previewPane.style.left = x + 'px';
      previewPane.style.top = y + 'px';
      previewPane.style.right = 'auto';
      previewPane.style.bottom = 'auto';
    });

    bar.addEventListener('pointerup', e => {
      dragging = false;
      previewPane.style.transition = '';
      try { bar.releasePointerCapture(e.pointerId); } catch (err) {}
    });
  })();

  /* ---- attachments ----

     Two kinds, handled differently. An image goes to the vision model, because
     the model driving the conversation usually cannot see; its description is
     what enters the transcript. A document is extracted to text and prepended
     to the message. Either way what reaches the main model is text, which is
     what makes this work regardless of which model is selected. */

  const attached = [];
  const attachHost = J.$('#attachments');

  function renderAttachments() {
    if (!attachHost) return;
    attachHost.innerHTML = '';
    attachHost.hidden = attached.length === 0;

    attached.forEach((a, i) => {
      const chip = document.createElement('div');
      chip.className = 'attach-chip';

      if (a.kind === 'image') {
        const img = document.createElement('img');
        img.className = 'attach-thumb';
        img.src = a.dataUrl;
        chip.appendChild(img);
      } else {
        const box = document.createElement('span');
        box.className = 'attach-kind';
        box.textContent = (a.ext || 'txt').slice(0, 4).toUpperCase();
        chip.appendChild(box);
      }

      const name = document.createElement('span');
      name.className = 'attach-name';
      name.textContent = a.name;
      chip.appendChild(name);

      const x = document.createElement('button');
      x.className = 'attach-x';
      x.innerHTML = '&#10005;';
      x.title = 'Remove';
      x.addEventListener('click', () => { attached.splice(i, 1); renderAttachments(); });
      chip.appendChild(x);

      attachHost.appendChild(chip);
    });
  }

  function readFile(file) {
    return new Promise(resolve => {
      const ext = (file.name.split('.').pop() || '').toLowerCase();
      const isImage = /^image\//.test(file.type);
      const reader = new FileReader();

      reader.onload = () => {
        if (isImage) {
          resolve({ kind: 'image', name: file.name, ext: ext, dataUrl: reader.result });
        } else {
          resolve({ kind: 'text', name: file._rel || file.name, ext: ext,
                    text: String(reader.result || '').slice(0, 60000) });
        }
      };
      reader.onerror = () => resolve(null);

      if (isImage) reader.readAsDataURL(file);
      else if (/pdf|docx?$/.test(ext)) {
        /* Binary formats need the server, which already knows how to read them
           for the document index. */
        resolve({ kind: 'server', name: file.name, ext: ext, file: file });
      } else reader.readAsText(file);
    });
  }

  const MAX_ATTACH = 40;          // a small source folder, not a whole disk

  async function addFiles(list) {
    const files = Array.from(list || []);
    if (files.length > MAX_ATTACH) {
      J.toast('That is ' + files.length + ' files — attaching the first ' + MAX_ATTACH + '.',
              'warn', 7000);
    }

    for (const file of files.slice(0, MAX_ATTACH)) {
      if (file.size > 25 * 1024 * 1024) {
        J.toast(file.name + ' is over 25MB — too large to attach.', 'warn', 7000);
        continue;
      }
      const a = await readFile(file);
      if (!a) {
        J.toast('Could not read ' + file.name + ' — it may be a binary, or empty.',
                'crit', 7000);
        continue;
      }

      if (a.kind === 'server') {
        J.toast('Reading ' + a.name + '…', 'ok');
        const body = new FormData();
        body.append('file', a.file);
        try {
          const res = await fetch('api/extract', { method: 'POST', body: body });
          const d = await res.json();
          if (!d.ok) { J.toast(d.error || 'Could not read it', 'crit', 8000); continue; }
          attached.push({ kind: 'text', name: a.name, ext: a.ext, text: d.text });
        } catch (e) {
          J.toast('Could not read ' + a.name, 'crit');
          continue;
        }
      } else {
        attached.push(a);
      }
    }
    renderAttachments();
  }

  const fileInput = J.$('#fileInput');
  const attachBtn = J.$('#attachBtn');
  if (attachBtn && fileInput) {
    /* Plain click picks files; hold Shift to pick a whole folder. Dropping a
       folder works either way. */
    attachBtn.addEventListener('click', e => {
      const folder = J.$('#folderInput');
      if (e.shiftKey && folder) folder.click();
      else fileInput.click();
    });
    attachBtn.title = 'Attach files — Shift-click for a folder — or drop them anywhere';

    const folderInput = J.$('#folderInput');
    if (folderInput) folderInput.addEventListener('change', () => {
      const picked = Array.from(folderInput.files || []);
      for (const f of picked) f._rel = f.webkitRelativePath || f.name;
      addFiles(picked);
      folderInput.value = '';
    });
    fileInput.addEventListener('change', () => {
      addFiles(fileInput.files);
      fileInput.value = '';
    });
  }

  /* Drag onto anywhere, not just the button. */
  let dragDepth = 0;
  window.addEventListener('dragenter', e => {
    e.preventDefault(); dragDepth++; document.body.classList.add('dragging');
  });
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('dragleave', () => {
    if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove('dragging'); }
  });
  /* Directories arrive as entries, not files. Reading one as a file fails with
     an opaque error, which is what "Could not read sneaker-simulator" was —
     a folder, not a broken file. This walks it instead. */

  const DROP_SKIP = /^(node_modules|\.git|__pycache__|venv|\.venv|dist|build|\.next|target|\.idea|\.vscode)$/i;

  function readEntries(reader) {
    return new Promise(res => reader.readEntries(res, () => res([])));
  }

  async function walkEntry(entry, out, depth) {
    if (out.length >= MAX_ATTACH || depth > 6) return;

    if (entry.isFile) {
      await new Promise(res => entry.file(f => {
        // keep the relative path, so he can see the shape of the project
        f._rel = entry.fullPath ? entry.fullPath.replace(/^\//, '') : f.name;
        out.push(f);
        res();
      }, res));
      return;
    }

    if (entry.isDirectory) {
      if (DROP_SKIP.test(entry.name)) return;
      const reader = entry.createReader();
      for (;;) {
        const batch = await readEntries(reader);
        if (!batch.length) break;
        for (const child of batch) await walkEntry(child, out, depth + 1);
        if (out.length >= MAX_ATTACH) break;
      }
    }
  }

  window.addEventListener('drop', async e => {
    e.preventDefault();
    dragDepth = 0; document.body.classList.remove('dragging');
    if (!e.dataTransfer) return;

    const items = Array.from(e.dataTransfer.items || []);
    const entries = items
      .map(it => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null))
      .filter(Boolean);

    if (entries.some(en => en.isDirectory)) {
      J.toast('Reading that folder…', 'ok');
      const collected = [];
      for (const en of entries) await walkEntry(en, collected, 0);
      if (!collected.length) {
        J.toast('That folder held nothing readable.', 'warn', 7000);
        return;
      }
      addFiles(collected);
      return;
    }

    if (e.dataTransfer.files) addFiles(e.dataTransfer.files);
  });

  /* Paste an image straight from the clipboard. */
  window.addEventListener('paste', e => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    const files = [];
    for (const it of items) if (it.kind === 'file') files.push(it.getAsFile());
    if (files.length) { e.preventDefault(); addFiles(files); }
  });

  /* Hand the queue to brain.js and clear it. */
  J.takeAttachments = function () {
    if (!attached.length) return null;
    const out = attached.slice();
    attached.length = 0;
    renderAttachments();
    return out;
  };

  /* ---- result cards ----

     Rendered from the tool result rather than from the model's prose, so the
     numbers on screen are the ones the tool actually returned. The model still
     writes its sentence; this sits alongside it. */

  function resultCard(title, rows, note) {
    const card = document.createElement('div');
    card.className = 'rcard';

    const head = document.createElement('div');
    head.className = 'rcard-head';
    head.textContent = title;
    card.appendChild(head);

    const body = document.createElement('div');
    body.className = 'rcard-rows';
    for (const r of rows) {
      const line = document.createElement('div');
      line.className = 'rcard-row';

      const k = document.createElement('span');
      k.className = 'k'; k.textContent = r.k;
      const v = document.createElement('span');
      v.className = 'v'; v.textContent = r.v;
      line.appendChild(k); line.appendChild(v);

      if (r.d) {
        const d = document.createElement('span');
        d.className = 'd ' + (/^\+/.test(r.d) ? 'up' : /^-/.test(r.d) ? 'down' : '');
        d.textContent = r.d;
        line.appendChild(d);
      }
      body.appendChild(line);
    }
    card.appendChild(body);

    if (note) {
      const n = document.createElement('div');
      n.className = 'progress-note';
      n.textContent = note;
      card.appendChild(n);
    }

    const convo = J.$('#convo');
    if (convo) { convo.appendChild(card); convo.scrollTop = convo.scrollHeight; }
  }

  /* Parse the shapes the tools actually emit. Deliberately narrow: if a line
     does not match, nothing is drawn and the prose still carries the answer. */
  J.on('tool-card', ({ tool, source, text }) => {
    if (!text) return;

    // "Apple Inc. (AAPL) 310.34 USD +1.55%; NVIDIA (NVDA) 208.48 USD -7.35%"
    if (source === 'stocks' || /\([A-Z^.]{1,6}\)\s[\d,]+\.\d/.test(text)) {
      const rows = [];
      for (const part of text.split(';')) {
        const m = part.match(/^\s*(.+?)\s*\(([^)]+)\)\s*([\d,]+\.?\d*)\s*(\w+)?\s*([+-][\d.]+%)?/);
        if (m) rows.push({ k: m[1] + ' (' + m[2] + ')', v: m[3] + (m[4] ? ' ' + m[4] : ''), d: m[5] || '' });
      }
      if (rows.length) return resultCard('markets', rows);
    }

    // "100 USD = 85.73 EUR; 100 USD = 159.12 JPY"
    if (source === 'currency') {
      const rows = [];
      for (const part of text.split(';')) {
        const m = part.match(/^\s*([\d.,]+\s*\w{3})\s*=\s*([\d.,]+\s*\w{3})/);
        if (m) rows.push({ k: m[1], v: m[2] });
      }
      if (rows.length) return resultCard('exchange', rows);
    }

    // "bitcoin $78,981.00 (+1.6% in 24h)"
    if (source === 'crypto') {
      const rows = [];
      for (const part of text.split(';')) {
        const m = part.match(/^\s*(\S+)\s*\$([\d,.]+)\s*(?:\(([+-][\d.]+)%)?/);
        if (m) rows.push({ k: m[1], v: '$' + m[2], d: m[3] ? m[3] + '%' : '' });
      }
      if (rows.length) return resultCard('crypto', rows);
    }

    // "Now playing: Track — Artist on DEVICE"
    if (tool === 'spotify' && /^(Now playing|Started)/.test(text)) {
      const m = text.replace(/^(Now playing|Started)\s*:?\s*/, '');
      return resultCard('spotify', [{ k: m.split(' on ')[0], v: '' }],
                        m.indexOf(' on ') > -1 ? m.split(' on ').pop() : '');
    }
  });

  /* ---- progress ---- */

  const liveProgress = new Map();

  function progressCard(id, label) {
    if (liveProgress.has(id)) return liveProgress.get(id);
    const el = document.createElement('div');
    el.className = 'progress';
    el.innerHTML = '<div class="progress-top"><b></b><span></span></div>'
      + '<div class="progress-track"><div class="progress-fill" style="width:0%"></div></div>'
      + '<div class="progress-note"></div>';
    el.querySelector('b').textContent = label;
    const convo = J.$('#convo');
    if (convo) { convo.appendChild(el); convo.scrollTop = convo.scrollHeight; }
    liveProgress.set(id, el);
    return el;
  }

  J.on('progress', ({ id, label, done, total, note, finished }) => {
    const el = progressCard(id, label || id);
    const pct = total ? Math.round((done / total) * 100) : 0;
    el.querySelector('.progress-top span').textContent =
      total ? done + ' / ' + total + '  ' + pct + '%' : '';
    el.querySelector('.progress-fill').style.width = (finished ? 100 : pct) + '%';
    el.querySelector('.progress-note').textContent = note || '';

    if (finished) {
      el.classList.add('done');
      el.querySelector('.progress-top span').textContent = 'complete';
      liveProgress.delete(id);
    }
  });

  /* Poll the indexer whenever one is running, so the bar is real rather than
     an animation pretending to be one. */
  let indexPoll = null;
  function watchIndexing() {
    if (indexPoll) return;
    indexPoll = setInterval(async () => {
      try {
        const s = await (await fetch('api/recall/status')).json();
        if (!s.running) {
          J.emit('progress', { id: 'index', label: 'indexing documents',
                               done: s.done, total: s.total,
                               note: s.chunks + ' chunks from ' + s.files + ' files',
                               finished: true });
          clearInterval(indexPoll); indexPoll = null;
          return;
        }
        J.emit('progress', { id: 'index', label: 'indexing documents',
                             done: s.done, total: s.total, note: s.file || '' });
      } catch (e) {
        clearInterval(indexPoll); indexPoll = null;
      }
    }, 1200);
  }
  J.on('indexing-started', watchIndexing);

  /* ---- live task cards ---- */

  /* A tool run of any length looks identical to a hang from the outside. These
     say what is happening while it happens, then retire themselves. */
  const taskHost = J.$('#tasks');
  const liveTasks = new Map();
  const retiring = new Map();     // ended, still animating out, still reusable
  const MAX_CHIPS = 5;

  const TASK_LABELS = {
    web_search: 'searching the web',
    web_fetch: 'reading a page',
    lookup: 'checking live data',
    spotify: 'controlling Spotify',
    google: 'reading your calendar',
    reminders: 'setting a reminder',
    see_screen: 'looking at your screen',
    see_preview: 'looking at the page',
    build_check: 'checking the build',
    control_interface: 'operating the machine',
    remember: 'committing to memory',
    forget: 'forgetting that'
  };

  /* "files — write" says nothing a spinner did not. Naming the actual job
     matters most here, because these are the long ones. */
  const FILE_VERBS = {
    write: 'writing', propose: 'preparing a change', run: 'running it',
    scaffold: 'creating the project', read: 'reading', find: 'searching for it',
    list_project: 'listing the project', history: 'checking what changed',
    revert: 'undoing that', transcribe: 'transcribing', media: 'converting',
    render: 'rendering the page'
  };

  function taskLabel(name, input) {
    if (name === 'files' && input && input.action) {
      const verb = FILE_VERBS[input.action] || input.action.replace(/_/g, ' ');
      const what = input.path || input.entry || input.name || input.project
                || input.query || '';
      return what ? verb + ' — ' + String(what).slice(0, 34) : verb;
    }
    const base = TASK_LABELS[name] || name.replace(/_/g, ' ');
    if (!input) return base;
    const detail = input.question || input.query || input.text || input.source
                || input.project || input.action || '';
    return detail ? base + ' — ' + String(detail).slice(0, 34) : base;
  }

  function taskStart(name, input) {
    if (!taskHost) return;

    // already running under this name: refresh its label, do not add a second
    const running = liveTasks.get(name);
    if (running) {
      running.runs++;
      const label = running.el.querySelector('.task-label');
      if (label && input) {
        label.textContent = taskLabel(name, input)
          + (running.runs > 1 ? '  ×' + running.runs : '');
      }
      return;
    }

    /* A chip that is retiring is still on screen for up to 3.5 seconds while
       it plays its exit. The old code deleted it from liveTasks the instant
       the task ended, so the next call of the same kind found an empty map
       and built a second chip — twelve rapid lookups produced a wall of nine.
       Catch it on the way out and reuse it. */
    const leaving = retiring.get(name);
    if (leaving) {
      clearTimeout(leaving.fade);
      clearTimeout(leaving.gone);
      retiring.delete(name);
      leaving.el.classList.remove('done', 'fail', 'leaving');
      leaving.el.querySelector('.task-ms').textContent = '';
      const label = leaving.el.querySelector('.task-label');
      if (label && input) {
        label.textContent = taskLabel(name, input)
          + (leaving.runs > 1 ? '  ×' + leaving.runs : '');
      }
      liveTasks.set(name, { el: leaving.el, at: performance.now(), runs: leaving.runs + 1 });
      return;
    }

    const el = document.createElement('div');
    el.className = 'task';
    el.innerHTML = '<span class="task-spin"></span>'
      + '<span class="task-label"></span><span class="task-ms"></span>';
    el.querySelector('.task-label').textContent = taskLabel(name, input);
    taskHost.appendChild(el);
    liveTasks.set(name, { el: el, at: performance.now(), runs: 1 });

    // however badly things go, the stage never becomes a wall of chips
    while (taskHost.children.length > MAX_CHIPS) taskHost.removeChild(taskHost.firstChild);
  }

  function taskEnd(name, failed) {
    const t = liveTasks.get(name);
    if (!t) return;
    liveTasks.delete(name);

    const ms = Math.round(performance.now() - t.at);
    t.el.classList.add(failed ? 'fail' : 'done');
    t.el.querySelector('.task-ms').textContent =
      ms < 1000 ? ms + 'ms' : (ms / 1000).toFixed(1) + 's';

    // Held, not forgotten — see taskStart. Reusable until it is actually gone.
    const rec = { el: t.el, runs: t.runs || 1, fade: 0, gone: 0 };
    rec.fade = setTimeout(() => {
      t.el.classList.add('leaving');
      rec.gone = setTimeout(() => { t.el.remove(); retiring.delete(name); }, 340);
    }, failed ? 3200 : 1100);
    retiring.set(name, rec);
  }

  J.on('tool-start', d => taskStart(d.name, d.input));
  J.on('tool-done', d => taskEnd(d.name, d.failed));

  /* Safety net. A tool that throws before its result is read would otherwise
     leave a card spinning for the rest of the session. */
  J.on('turn-end', () => {
    for (const name of Array.from(liveTasks.keys())) taskEnd(name, false);
  });

  /* A switch mid-answer deserves its own card — it explains a sudden change in
     tone or speed that would otherwise look like a glitch. */
  J.on('routed', r => {
    if (!taskHost) return;
    const el = document.createElement('div');
    el.className = 'task done';
    el.innerHTML = '<span class="task-spin"></span><span class="task-label"></span>';
    el.querySelector('.task-label').textContent =
      (r.why === 'escalated' ? 'escalated to ' : 'fast path — ') + r.model;
    taskHost.appendChild(el);
    setTimeout(() => {
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 340);
    }, 2600);
  });

  J.on('provider-switched', c => {
    if (!taskHost) return;
    const el = document.createElement('div');
    el.className = 'task done';
    el.innerHTML = '<span class="task-spin"></span><span class="task-label"></span>';
    el.querySelector('.task-label').textContent = 'switched to ' + c.name;
    taskHost.appendChild(el);
    setTimeout(() => {
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 340);
    }, 4200);
  });

  /* ---- reminders ---- */

  /* Polled rather than timed in-page. A setTimeout dies with the tab, which is
     precisely when a reminder most needs to survive; asking the server what is
     due means one that came due while the dashboard was shut still lands. */
  async function pollReminders() {
    try {
      const d = await (await fetch('api/memory/due')).json();
      if (!d.ok || !d.due || !d.due.length) return;

      for (const r of d.due) {
        const late = r.late_by > 90
          ? ' (due ' + Math.round(r.late_by / 60) + ' minutes ago)'
          : '';
        showAlert('Reminder', r.text + late);
        J.log('Reminder due: ' + r.text, 'warn', 'sys');
        J.toast('Reminder: ' + r.text, 'warn', 15000);
        if (J.settings.speak) J.voice.say('Reminder. ' + r.text);
      }
    } catch (e) { /* server not reachable; nothing to announce */ }
  }

  pollReminders();
  setInterval(pollReminders, 20000);

  /* ---- google ---- */

  async function refreshGoogle() {
    const el = J.$('#googleState');
    if (!el) return;
    try {
      const g = await (await fetch('api/google/status')).json();
      if (g.connected) {
        el.textContent = 'connected as ' + (g.user || 'your account');
        el.className = 'key-state ok';
      } else if (g.has_credentials) {
        el.textContent = 'credentials saved - press Connect';
        el.className = 'key-state warn';
      } else {
        el.textContent = 'not connected';
        el.className = 'key-state';
      }
    } catch (e) {
      el.textContent = 'bridge unavailable - is serve.py running?';
      el.className = 'key-state crit';
    }
  }

  const gConsole = J.$('#googleOpenConsole');
  if (gConsole) gConsole.addEventListener('click', () => {
    window.open('https://console.cloud.google.com/apis/credentials', '_blank', 'noopener');
  });

  const gCopy = J.$('#googleCopyUri');
  if (gCopy) gCopy.addEventListener('click', async () => {
    const uri = J.$('#googleRedirect').textContent.trim();
    try {
      await navigator.clipboard.writeText(uri);
      J.toast('Redirect URI copied', 'ok');
    } catch (e) {
      const r = document.createRange();
      r.selectNodeContents(J.$('#googleRedirect'));
      const sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(r);
      J.toast('Selected - press Ctrl+C', 'warn');
    }
  });

  const gConnect = J.$('#googleConnect');
  if (gConnect) gConnect.addEventListener('click', () => {
    const id = (J.$('#googleClientId').value || '').trim();
    const sec = (J.$('#googleSecret').value || '').trim();
    if (!id || !sec) { J.toast('Both the Client ID and secret are needed.', 'warn'); return; }
    window.open('api/google/login?' + new URLSearchParams({
      client_id: id, client_secret: sec
    }), '_blank');
    J.toast('Approve the permissions in the new tab, then come back.', 'ok', 9000);
    setTimeout(refreshGoogle, 7000);
  });

  const gForget = J.$('#googleForget');
  if (gForget) gForget.addEventListener('click', async () => {
    await fetch('api/google/command', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ disconnect: true })
    });
    J.toast('Google disconnected', 'ok');
    refreshGoogle();
  });

  refreshGoogle();

  const spDash = J.$('#spotifyOpenDash');
  if (spDash) spDash.addEventListener('click', () => {
    window.open('https://developer.spotify.com/dashboard', '_blank', 'noopener');
  });

  const spCopy = J.$('#spotifyCopyUri');
  if (spCopy) spCopy.addEventListener('click', async () => {
    const uri = J.$('#spotifyRedirect').textContent.trim();
    try {
      await navigator.clipboard.writeText(uri);
      J.toast('Redirect URI copied', 'ok');
    } catch (e) {
      /* Clipboard access can be refused; selecting it is just as good. */
      const r = document.createRange();
      r.selectNodeContents(J.$('#spotifyRedirect'));
      const sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(r);
      J.toast('Selected — press Ctrl+C', 'warn');
    }
  });

  const spConnect = J.$('#spotifyConnect');
  if (spConnect) spConnect.addEventListener('click', () => {
    const id = (J.$('#spotifyClientId').value || '').trim();
    if (!id) { J.toast('Paste your Spotify client ID first.', 'warn'); return; }
    /* The consent screen has to be a real navigation, and it comes back to
       127.0.0.1 rather than here — so it gets its own tab. */
    window.open('api/spotify/login?client_id=' + encodeURIComponent(id), '_blank');
    J.toast('Approve the permissions in the new tab, then come back.', 'ok', 9000);
    setTimeout(refreshSpotify, 6000);
  });

  const spForget = J.$('#spotifyForget');
  if (spForget) spForget.addEventListener('click', async () => {
    await fetch('api/spotify/command', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ disconnect: true })
    });
    J.toast('Spotify disconnected', 'ok');
    refreshSpotify();
  });

  refreshSpotify();

  /* Pull the visible field values into settings. Browsers autofill password
     inputs without firing an input event, so the box can show dots while the
     stored value is empty. Called before anything that uses the key. */
  function adoptTypedKeys() {
    const pairs = [['#setAltKey', 'altKey'], ['#setKey', 'apiKey'],
                   ['#setSearchKey', 'searchKey'], ['#setGoogleKey', 'googleKey']];
    const patch = {};
    for (const [sel, key] of pairs) {
      const el = J.$(sel);
      if (el && el.value && el.value !== J.settings[key]) patch[key] = el.value;
    }
    if (Object.keys(patch).length) {
      J.set(patch);
      J.log('Adopted ' + Object.keys(patch).join(', ') + ' from the form', 'info', 'sys');
    }
    showKeyState();
  }

  /* Say plainly what is stored, because a password field tells you nothing. */
  function showKeyState() {
    const el = J.$('#altKeyState');
    if (!el) return;
    const k = (J.settings.altKey || '').trim();
    const base = (J.settings.altBase || '');
    const local = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/.test(base);

    if (k) {
      el.textContent = 'key stored — ' + k.length + ' characters, starts ' + k.slice(0, 7) + '…';
      el.className = 'key-state ok';
    } else if (local) {
      el.textContent = 'no key stored — correct for a local endpoint';
      el.className = 'key-state';
    } else {
      el.textContent = 'NO KEY STORED — if the box above looks filled, click into it and retype';
      el.className = 'key-state crit';
    }
  }

  J.on('settings', showKeyState);

  /* Model ids are provider-specific and the two families look nothing alike:
     Ollama says "gemma4:cloud", OpenRouter says "google/gemma-4-26b-a4b-it:free".
     Pointing one at the other is a guaranteed 404, and the only clue would be a
     failure at the moment he tries to look at the screen. Catch it here. */
  function checkVisionPairing() {
    const note = J.$('#visionNote');
    if (!note) return;

    const model = (J.settings.visionModel || '').trim();
    if (!model) {
      note.textContent = 'vision is off — no model set';
      note.className = 'key-state';
      return;
    }

    const where = (J.settings.visionConn || '').trim();
    const list = Array.isArray(J.settings.connections) ? J.settings.connections : [];
    const conn = where ? list.find(c => c.name === where) : null;
    const base = conn ? conn.base : J.settings.altBase;
    const isLocal = /localhost|127\.0\.0\.1/.test(base || '');
    const looksOllama = /^[a-z0-9._-]+:[a-z0-9._-]+$/i.test(model) && model.indexOf('/') === -1;
    const looksHosted = model.indexOf('/') !== -1;

    if (looksOllama && !isLocal) {
      note.textContent = '"' + model + '" is an Ollama name but ' + (where || 'this connection')
        + ' is not Ollama — it will 404. Hosted providers spell it like '
        + 'google/gemma-4-26b-a4b-it:free';
      note.className = 'key-state crit';
    } else if (looksHosted && isLocal) {
      note.textContent = '"' + model + '" is a hosted model id but this points at a local '
        + 'Ollama — it will 404. Locally it is spelled gemma4:cloud';
      note.className = 'key-state crit';
    } else if (/\s/.test(model)) {
      note.textContent = '"' + model + '" is not a model id - it looks like a picker '
        + 'label. Model ids never contain a space; try "' 
        + model.replace(/\s+on\s+.*$/i, '').trim() + '"';
      note.className = 'key-state crit';
    } else {
      note.textContent = 'vision ready — ' + model + ' on ' + (where || 'the main provider');
      note.className = 'key-state ok';
    }
  }

  /* Clean a stored picker-label once, at boot, so the field shows the truth
     without the user having to touch it. */
  (function healVisionModel() {
    const raw = (J.settings.visionModel || '').trim();
    const fixed = raw.replace(/\s+on\s+.*$/i, '').trim();
    if (fixed && fixed !== raw) {
      J.set({ visionModel: fixed });
      const el = J.$('#setVisionModel');
      if (el) el.value = fixed;
      J.log('Vision model id cleaned to "' + fixed + '"', 'ok', 'sys');
    }
  })();

  J.on('settings', checkVisionPairing);
  checkVisionPairing();

  /* ---- search credentials ----

     These save as you type like every other setting, which is correct and
     completely invisible. With no button and no feedback there was no way to
     tell a saved key from a typo, and a wrong key does not announce itself:
     the server just falls through to the next backend and returns results
     from somewhere else entirely. */

  /* The field label reads "Search engine ID (cx)", which is an easy thing to
     paste the whole of. Clean it on the way out rather than failing later. */
  function tidyCredential(sel, key) {
    const el = J.$(sel);
    if (!el) return;
    el.addEventListener('change', () => {
      const cleaned = el.value.trim().replace(/^(cx|key|id)\s*[:=]\s*/i, '').trim();
      if (cleaned === el.value) return;
      el.value = cleaned;
      J.set({ [key]: cleaned });
    });
  }
  /* The model picker lists rows as "<id> on <connection>", and that whole
     label is easy to end up with in the field - which is what happened here:
     visionModel was stored as "google/gemma-4-26b-a4b-it:free on OpenRouter".
     A model id never contains a space, so every vision call 404'd and
     see_preview failed silently, taking the build check with it. */
  (function tidyModelId() {
    for (const sel of ['#setVisionModel', '#setAltModel', '#setFastModel']) {
      const el = J.$(sel);
      if (!el) continue;
      const key = { '#setVisionModel': 'visionModel', '#setAltModel': 'altModel',
                    '#setFastModel': 'fastModel' }[sel];
      el.addEventListener('change', () => {
        const cleaned = el.value.trim().replace(/\s+on\s+.*$/i, '').trim();
        if (!cleaned || cleaned === el.value) return;
        el.value = cleaned;
        J.set({ [key]: cleaned });
      });
    }
  })();

  tidyCredential('#setGoogleCx', 'googleCx');
  tidyCredential('#setGoogleKey', 'googleKey');
  tidyCredential('#setSearchKey', 'searchKey');

  const searchBtn = J.$('#searchTest');
  if (searchBtn) searchBtn.addEventListener('click', async () => {
    const el = J.$('#searchState');
    searchBtn.disabled = true;
    el.textContent = 'searching…';
    el.className = 'key-state';

    const headers = {};
    if (J.settings.searchKey) headers['X-Search-Key'] = J.settings.searchKey;
    if (J.settings.googleKey) headers['X-Google-Key'] = J.settings.googleKey;
    if (J.settings.googleCx)  headers['X-Google-CX']  = J.settings.googleCx;

    try {
      const res = await fetch('api/search?q=' + encodeURIComponent('current time in london'),
                              { headers: headers });
      const d = await res.json();

      if (!d.ok) {
        el.textContent = d.error || 'every backend failed';
        el.className = 'key-state crit';
      } else {
        const n = (d.results || []).length;
        const wantGoogle = !!(J.settings.googleKey && J.settings.googleCx);
        const why = (d.skipped || []).find(x => /^google/.test(x));

        if (wantGoogle && d.engine !== 'google') {
          el.textContent = why
            ? 'Google failed — ' + why.replace(/^google:\s*/, '').replace(/\.\s*$/, '')
              + '. ' + d.engine + ' answered instead.'
            : 'Google was skipped; ' + d.engine + ' answered instead. Check the key and the ID.';
          el.className = 'key-state crit';
        } else {
          el.textContent = d.engine + ' answered — ' + n + ' result' + (n === 1 ? '' : 's');
          el.className = 'key-state ok';
        }
      }
    } catch (err) {
      el.textContent = 'could not reach the local search proxy — is serve.py running?';
      el.className = 'key-state crit';
    }
    searchBtn.disabled = false;
  });

  const verifyBtn = J.$('#keyVerify');
  if (verifyBtn) verifyBtn.addEventListener('click', async () => {
    const el = J.$('#keyState');
    verifyBtn.disabled = true;
    el.textContent = 'checking…';
    el.className = 'key-state';

    adoptTypedKeys();

    /* Verify costs a real request, and on a free tier those are scarce. Pressing
       it repeatedly is how a working key ends up looking broken. */
    const now = Date.now();
    if (verifyBtn._last && now - verifyBtn._last < 8000) {
      el.textContent = 'give it a few seconds — each check costs a request from your quota';
      el.className = 'key-state warn';
      verifyBtn.disabled = false;
      return;
    }
    verifyBtn._last = now;

    const result = await J.brain.verify();
    el.textContent = result.message;
    el.className = 'key-state ' + (result.ok ? 'ok' : 'crit');
    verifyBtn.disabled = false;

    if (result.ok) {
      J.toast('Cognition online.', 'ok');
      dismissKeyNotice();
      J.log('API key verified against ' + result.model, 'ok', 'net');
    } else {
      J.log('Key verification failed: ' + result.message, 'crit', 'net');
    }
  });

  /* ========================================================== restore */

  /* Replays saved history into the transcript. Tool traffic is skipped — it is
     noise on reload; only what was actually said gets rebuilt. */
  function restore() {
    const history = J.brain.getHistory();
    let shown = 0;

    history.forEach(m => {
      const text = typeof m.content === 'string'
        ? m.content
        : (Array.isArray(m.content)
            ? m.content.filter(b => b.type === 'text').map(b => b.text).join('\n').trim()
            : '');
      if (!text) return;

      if (m.role === 'user') { addUser(text); shown++; }
      else { bubble('jarvis', 'J.A.R.V.I.S.').innerHTML = J.md(text); shown++; }
    });

    if (shown) {
      stickToBottom(true);
      J.log('Restored ' + shown + ' messages from the previous session', 'info', 'sys');
    }
    return shown;
  }

  /* ============================================================== boot */

  function usingOpenAIName() {
    return J.settings.provider === 'openai'
      ? (J.settings.altModel || 'openai-compatible')
      : (J.settings.model || 'anthropic');
  }

  const STEPS = [
    ['spinning up interface',   14],
    ['reading device telemetry', 32],
    ['calibrating grid face',    50],
    ['probing cognition uplink', 72],
    ['restoring session',        88],
    ['systems nominal',         100]
  ];

  /* The boot used to be theatre: six captions on a timer, the same every
     time, whether or not the thing they named actually worked. Now each step
     reports what it found, and the panels arm one at a time as it goes — so
     a failed subsystem is visible during boot rather than discovered later. */
  function armPanel(n) {
    const panels = document.querySelectorAll('.rail .panel, .topbar');
    if (panels[n]) panels[n].classList.add('armed');
  }

  async function boot() {
    const bar = J.$('#bootBar'), sub = J.$('#bootSub');
    let armed = 0;

    const step = (i, detail) => new Promise(done => {
      const [label, pct] = STEPS[i];
      sub.textContent = detail ? label + ' — ' + detail : label;
      bar.style.width = pct + '%';
      armPanel(armed++);
      setTimeout(done, J.reducedMotion ? 40 : 170);
    });

    J.applyAccent(J.settings.accent);
    renderSwatches();
    J.$('#modelVal').textContent = J.settings.model;
    autosize();

    await step(0, J.settings.mask + ' face, ' + J.settings.accent);

    J.telemetry.init();
    const snap = J.telemetry.snapshot() || {};
    await step(1, [snap.battery && snap.battery.percent != null
                     ? snap.battery.percent + '% cell' : null,
                   snap.network && snap.network.type ? snap.network.type : null]
                  .filter(Boolean).join(', ') || 'no sensors');

    J.voice.init();
    await step(2, J.settings.speak ? 'voice out on' : 'muted');

    const transport = await J.brain.resolveTransport();
    await step(3, transport === 'proxy' ? 'via local proxy'
                : (usingOpenAIName() || transport));

    const restored = restore();
    if (J.agent) renderMission(J.agent.current());
    renderMemories();
    await step(4, restored ? restored + ' messages' : 'clean session');

    await step(5, 'ready');

    refreshCognition();
    document.body.classList.remove('booting');
    J.mode('standby', 'live');
    J.status('standby', hintLine());
    J.log('J.A.R.V.I.S. mk VII online', 'ok', 'sys');

    if (!J.brain.ready()) {
      const body = bubble('jarvis', 'J.A.R.V.I.S.');
      body.id = 'keyNotice';
      body.innerHTML = J.md(
        'Interface is up, cognition is not. I need an Anthropic API key before I can hold a conversation.\n\n' +
        '- Open **Configuration** — the gear, top right, or `Ctrl+/`\n' +
        '- Paste a key from `console.anthropic.com`\n\n' +
        'It stays in this browser and goes only to `api.anthropic.com`. ' +
        'Everything else on this dashboard — telemetry, weather, voice — is already running.'
      );
      J.mode('standby', 'err');
      J.toast('No API key configured. Open Configuration to enable conversation.', 'warn', 9000);
      stickToBottom(true);
    } else if (!restored) {
      const body = bubble('jarvis', 'J.A.R.V.I.S.');
      body.textContent = transport === 'proxy'
        ? 'Online, key held server-side. Ask me anything.'
        : 'Online. Ask me anything — hold space to speak, or just type.';
      suggestions(STARTERS.slice(0, 3));
      stickToBottom(true);
    }

    input.focus();
  }

  J.app = { send, openSettings, closeSettings, suggestions, restore };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();


  /* ======================================================== the right rail

     Four panels sharing one card. The rail was already full — a fifth card
     would have made every other card shorter rather than making anything new
     visible, which is the usual way a dashboard stops being one.

     Each pane fetches only when it is shown. A projects walk and a media scan
     both touch the disk, and doing that every few seconds for a panel nobody
     is looking at is how a wall display starts costing something. */

  const railTabs = J.$('#railTabs');

  function showPane(which) {
    if (!railTabs) return;
    railTabs.querySelectorAll('[data-tab]').forEach(b =>
      b.classList.toggle('is-on', b.dataset.tab === which));
    document.querySelectorAll('.tabpane').forEach(p =>
      p.hidden = p.dataset.pane !== which);

    if (which === 'projects') loadProjects();
    if (which === 'media') loadMedia();
    if (which === 'inbox') loadNotices();
    if (which === 'jobs') loadJobs();
  }

  if (railTabs) {
    railTabs.addEventListener('click', e => {
      const btn = e.target.closest('[data-tab]');
      if (btn) showPane(btn.dataset.tab);
    });
  }

  async function filesApi(payload) {
    const res = await fetch('api/files/command', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return await res.json();
  }

  async function memApi(payload) {
    const res = await fetch('api/memory/command', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return await res.json();
  }

  function ago(t) {
    const s = Math.max(0, (Date.now() / 1000) - t);
    if (s < 90) return 'just now';
    if (s < 5400) return Math.round(s / 60) + 'm ago';
    if (s < 172800) return Math.round(s / 3600) + 'h ago';
    return Math.round(s / 86400) + 'd ago';
  }

  function human(n) {
    const u = ['B', 'kB', 'MB', 'GB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(n < 10 ? 1 : 0) : n) + ' ' + u[i];
  }

  /* ---- projects ---------------------------------------------------- */

  const projList = J.$('#projList');

  async function loadProjects() {
    if (!projList) return;
    projList.textContent = 'reading…';
    let d;
    try { d = await filesApi({ action: 'projects' }); }
    catch (e) { projList.textContent = 'Could not reach the server.'; return; }

    J.$('#projNote').textContent = d.projects.length
      ? d.projects.length + ' projects' : '';
    projList.innerHTML = '';

    if (!d.projects.length) {
      projList.innerHTML = '<p class="pane-empty">Nothing built yet. Ask him to '
        + 'scaffold something.</p>';
      return;
    }

    for (const p of d.projects) {
      const row = document.createElement('div');
      row.className = 'prow';

      const head = document.createElement('div');
      head.className = 'prow-head';
      const nm = document.createElement('b');
      nm.textContent = p.name;
      const meta = document.createElement('span');
      meta.className = 'prow-meta';
      meta.textContent = p.files + ' files · ' + human(p.bytes) + ' · ' + ago(p.mtime)
                       + (p.commits ? ' · ' + p.commits + ' commits' : '');
      head.append(nm, meta);

      const acts = document.createElement('div');
      acts.className = 'prow-acts';

      if (p.page) {
        const prev = document.createElement('button');
        prev.className = 'mini'; prev.type = 'button'; prev.textContent = 'preview';
        prev.addEventListener('click', () => J.emit('preview', { project: p.name }));
        acts.appendChild(prev);
      }
      if (p.runner) {
        const run = document.createElement('button');
        run.className = 'mini'; run.type = 'button'; run.textContent = 'run';
        run.addEventListener('click', async () => {
          run.disabled = true; run.textContent = 'running…';
          const r = await filesApi({ action: 'run', project: p.name,
                                     what: p.runner, entry: p.entry });
          run.disabled = false; run.textContent = 'run';
          showPaneOut(row, r.summary || r.error, r.ok && r.exit === 0);
        });
        acts.appendChild(run);
      }

      const hist = document.createElement('button');
      hist.className = 'mini'; hist.type = 'button'; hist.textContent = 'history';
      hist.addEventListener('click', async () => {
        const r = await filesApi({ action: 'history', project: p.name });
        showPaneOut(row, r.summary || r.error, true);
      });
      acts.appendChild(hist);

      row.append(head, acts);
      projList.appendChild(row);
    }
  }

  /* Output belongs under the row that produced it — a shared output box at
     the bottom makes you work out which button you pressed. */
  function showPaneOut(row, text, ok) {
    let box = row.querySelector('.prow-out');
    if (!box) {
      box = document.createElement('pre');
      box.className = 'prow-out';
      row.appendChild(box);
    }
    box.classList.toggle('bad', !ok);
    box.textContent = String(text || '').slice(0, 4000);
  }

  /* ---- media bay --------------------------------------------------- */

  const mediaList = J.$('#mediaList');
  const mediaJob = J.$('#mediaJob');
  const MEDIA_JOBS = [
    ['web', 'For the web — 1280 wide, streaming-ready'],
    ['silent_web', 'Muted hero background'],
    ['vertical', 'Vertical 9:16 — cropped'],
    ['vertical_fit', 'Vertical 9:16 — blurred sides, nothing cropped'],
    ['square', 'Square 1:1'],
    ['to_gif', 'Animated GIF'],
    ['compress', 'Compress'],
    ['to_mp3', 'Extract audio as MP3'],
    ['to_wav', 'Extract audio as WAV'],
    ['mute', 'Strip the audio'],
    ['half_speed', 'Half speed'],
    ['double_speed', 'Double speed'],
    ['thumbnail', 'Grab a frame'],
    ['join', 'Join every ticked clip, in order']
  ];

  if (mediaJob && !mediaJob.options.length) {
    for (const [value, label] of MEDIA_JOBS) {
      const o = document.createElement('option');
      o.value = value; o.textContent = label;
      mediaJob.appendChild(o);
    }
  }

  async function loadMedia() {
    if (!mediaList) return;
    mediaList.textContent = 'scanning…';
    let d;
    try { d = await filesApi({ action: 'media_files' }); }
    catch (e) { mediaList.textContent = 'Could not reach the server.'; return; }

    const note = J.$('#mediaNote');
    if (!d.ffmpeg) {
      note.textContent = 'ffmpeg missing';
      mediaList.innerHTML = '<p class="pane-empty">ffmpeg is not installed, so none '
        + 'of these jobs can run. Install it with <code>winget install Gyan.FFmpeg</code>'
        + ' and press refresh.</p>';
      return;
    }
    note.textContent = d.count + ' files';
    mediaList.innerHTML = '';

    if (!d.files.length) {
      mediaList.innerHTML = '<p class="pane-empty">No video or audio under the folders '
        + 'he can read.</p>';
      return;
    }

    for (const f of d.files) {
      const row = document.createElement('label');
      row.className = 'mrow';
      const tick = document.createElement('input');
      tick.type = 'checkbox';
      tick.value = f.path;
      const nm = document.createElement('span');
      nm.className = 'mrow-name';
      nm.textContent = f.name;
      nm.title = f.path;
      const meta = document.createElement('span');
      meta.className = 'mrow-meta';
      meta.textContent = f.human + ' · ' + f.where;
      row.append(tick, nm, meta);
      mediaList.appendChild(row);
    }
  }

  const mediaGo = J.$('#mediaGo');
  if (mediaGo) mediaGo.addEventListener('click', async () => {
    const out = J.$('#mediaOut');
    const picked = [...mediaList.querySelectorAll('input:checked')].map(i => i.value);
    const job = mediaJob.value;

    out.hidden = false;
    out.classList.remove('bad');

    if (!picked.length) { out.textContent = 'Tick a file first.'; return; }
    if (job === 'join' && picked.length < 2) {
      out.textContent = 'Joining needs at least two ticked files.';
      return;
    }

    mediaGo.disabled = true;
    out.textContent = job === 'join'
      ? 'Joining ' + picked.length + ' clips…'
      : 'Running ' + job + '…';

    try {
      const r = job === 'join'
        ? await filesApi({ action: 'join', paths: picked })
        : await filesApi({ action: 'media', media_action: job, path: picked[0] });

      out.classList.toggle('bad', !r.ok);
      out.textContent = r.summary || r.error || 'Nothing came back.';

      /* Show the result rather than describing it. A still from the output is
         the difference between "wrote a file" and knowing it is right. */
      if (r.ok && r.output && /\.(mp4|mov|mkv|webm|gif)$/i.test(r.output)) {
        const t = await filesApi({ action: 'media', media_action: 'thumbnail',
                                   path: r.output, start: '1' });
        if (t.ok && t.output) {
          const img = document.createElement('img');
          img.className = 'media-thumb';
          img.alt = 'Frame from the result';
          img.src = 'preview-file?path=' + encodeURIComponent(t.output) + '&t=' + Date.now();
          out.appendChild(img);
        }
      }
      loadMedia();
    } catch (e) {
      out.classList.add('bad');
      out.textContent = e.message || String(e);
    } finally {
      mediaGo.disabled = false;
    }
  });

  const mediaRefresh = J.$('#mediaRefresh');
  if (mediaRefresh) mediaRefresh.addEventListener('click', loadMedia);
  const projRefresh = J.$('#projRefresh');
  if (projRefresh) projRefresh.addEventListener('click', loadProjects);

  /* ---- the notice queue -------------------------------------------- */

  /* Nothing here speaks. A notice changes a number in the corner and waits.
     That is the whole agreement, and it is the reason this can exist at all. */

  const noticeList = J.$('#noticeList');
  const inboxCount = J.$('#inboxCount');

  function setUnseen(n) {
    if (!inboxCount) return;
    inboxCount.textContent = n;
    inboxCount.hidden = !n;
  }

  async function loadNotices() {
    if (!noticeList) return;
    let d;
    try { d = await memApi({ action: 'notices' }); }
    catch (e) { noticeList.textContent = 'Could not reach the server.'; return; }

    setUnseen(d.unseen);
    J.$('#inboxNote').textContent = d.unseen ? d.unseen + ' unread' : '';
    noticeList.innerHTML = '';

    if (!d.notices.length) {
      noticeList.innerHTML = '<p class="pane-empty">Nothing noticed. This fills up '
        + 'on its own; it never interrupts.</p>';
      return;
    }

    for (const n of d.notices) {
      const row = document.createElement('div');
      row.className = 'nrow' + (n.seen ? ' seen' : '');
      const t = document.createElement('time');
      t.textContent = ago(n.at);
      const body = document.createElement('span');
      body.className = 'nrow-text';
      body.textContent = n.text + (n.repeats > 1 ? '  (×' + n.repeats + ')' : '');
      const src = document.createElement('em');
      src.textContent = n.source || n.kind || '';
      row.append(t, body, src);
      noticeList.appendChild(row);
    }
  }

  const inboxSeen = J.$('#inboxSeen');
  if (inboxSeen) inboxSeen.addEventListener('click', async () => {
    await memApi({ action: 'notices_seen', text: 'all' });
    loadNotices();
  });

  const inboxClear = J.$('#inboxClear');
  if (inboxClear) inboxClear.addEventListener('click', async () => {
    await memApi({ action: 'notices_clear' });
    loadNotices();
  });

  /* The badge is the only thing that polls, and slowly. Everything else waits
     to be looked at. */
  async function pollUnseen() {
    try {
      const d = await memApi({ action: 'notices' });
      setUnseen(d.unseen);
    } catch (e) { /* offline; the number simply does not change */ }
  }
  pollUnseen();
  setInterval(pollUnseen, 45000);

  /* Anything can queue a notice without knowing where it goes. */
  J.on('notice', async ({ text, kind, source }) => {
    if (!text) return;
    await memApi({ action: 'notice', text: text });
    pollUnseen();
  });


  /* ==================================================== floating pop-ups

     A card that rises into the empty middle of the stage, holds for about ten
     seconds, and leaves. For things with a shape — a score, a fixture, a
     headline — where a sentence tells you the fact but a card lets you take
     it in without reading.

     Rules it lives by:
       · It never replaces what he says. The card is the glance; the sentence
         is still spoken and still lands in the transcript.
       · It is built from structured tool data, never from parsing his prose.
         A card assembled by regex out of an English sentence is a card that
         silently stops appearing the day a source rewords itself.
       · It never covers the composer.
       · Hovering holds it. Clicking pins it until dismissed.
       · Whatever happens, it also goes into the transcript, so missing it
         costs nothing. */
  /* How long a glanceable card stays. Configurable because the right answer
     depends on the card: a score is read in two seconds, a video you are about
     to publish under your own name is not. Sticky cards ignore this entirely. */
  function popHold() {
    const secs = parseInt(J.settings.popSeconds, 10);
    return (secs >= 2 && secs <= 120) ? secs * 1000 : 10000;
  }          // what Zero asked for: about ten seconds
  const POP_MAX = 3;

  let popHost = J.$('#pops');
  if (!popHost) {
    popHost = document.createElement('div');
    popHost.className = 'pops';
    popHost.id = 'pops';
    popHost.setAttribute('aria-live', 'polite');
    const stage = document.querySelector('.stage');
    if (stage) stage.appendChild(popHost);
  }

  function teamSide(t) {
    const side = document.createElement('div');
    side.className = 'pop-side';
    if (t.logo) {
      const img = document.createElement('img');
      /* Not lazy. There are two of these, they are on screen the moment the
         card appears, and the card is gone in ten seconds — deferring them
         just means the logos arrive after the thing they belong to has left. */
      img.src = t.logo;
      img.alt = '';
      /* A logo that fails is not a reason to show a broken picture icon in
         the middle of a scoreboard. */
      img.addEventListener('error', () => img.remove());
      side.appendChild(img);
    }
    const nm = document.createElement('span');
    nm.className = 'pop-team';
    nm.textContent = t.name || t.abbr || '';
    side.appendChild(nm);
    if (t.record) {
      const rec = document.createElement('em');
      rec.textContent = t.record;
      side.appendChild(rec);
    }
    return side;
  }

  function gameRow(item) {
    const row = document.createElement('div');
    row.className = 'pop-game';
    const t = item.teams || {};
    const away = t.away || {}, home = t.home || {};

    row.appendChild(teamSide(away));

    const mid = document.createElement('div');
    mid.className = 'pop-mid';
    if (item.state === 'pre') {
      const at = document.createElement('b');
      at.className = 'pop-soon';
      at.textContent = item.status || 'scheduled';
      mid.appendChild(at);
    } else {
      const sc = document.createElement('b');
      sc.textContent = (away.score != null ? away.score : '–') + ' – '
                     + (home.score != null ? home.score : '–');
      mid.appendChild(sc);
      const st = document.createElement('span');
      st.className = 'pop-status' + (item.state === 'in' ? ' live' : '');
      st.textContent = item.status || '';
      mid.appendChild(st);
    }
    row.appendChild(mid);
    row.appendChild(teamSide(home));
    return row;
  }

  function kvRow(r) {
    const row = document.createElement('div');
    row.className = 'pop-kv';
    const k = document.createElement('span'); k.textContent = r.k;
    const v = document.createElement('b');    v.textContent = r.v;
    row.append(k, v);
    return row;
  }

  /* Show a card. `card` is { kind, title, items[] } or { kind, title, rows[] }. */
  function showPop(card) {
    if (!popHost || !card) return;

    const el = document.createElement('div');
    el.className = 'pop' + (card.accent ? ' accent-' + card.accent : '');

    const head = document.createElement('div');
    head.className = 'pop-head';
    const title = document.createElement('span');
    title.className = 'pop-title';
    title.textContent = card.title || '';
    const close = document.createElement('button');
    close.className = 'pop-x';
    close.type = 'button';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';
    head.append(title, close);
    el.appendChild(head);

    const body = document.createElement('div');
    body.className = 'pop-body';

    /* Three, not more. Four games made the card 398px tall, which put its
       bottom edge over the composer — and a pop-up that covers the thing you
       type into is not a pop-up, it is an obstacle. */
    for (const item of (card.items || []).slice(0, 3)) {
      if (item.teams) {
        body.appendChild(gameRow(item));
        const extras = [];
        if (item.on) extras.push(item.on);
        if (item.venue) extras.push(item.venue);
        if (extras.length) {
          const foot = document.createElement('div');
          foot.className = 'pop-sub';
          foot.textContent = extras.join('  ·  ');
          body.appendChild(foot);
        }
      } else if (item.rows) {
        if (item.title) {
          const h = document.createElement('div');
          h.className = 'pop-sub';
          h.textContent = item.title;
          body.appendChild(h);
        }
        item.rows.forEach(r => body.appendChild(kvRow(r)));
      } else if (item.text) {
        const line = document.createElement('div');
        line.className = 'pop-line';
        line.textContent = item.text;
        body.appendChild(line);
      }
    }
    for (const r of (card.rows || [])) body.appendChild(kvRow(r));

    el.appendChild(body);
    popHost.appendChild(el);

    // Oldest goes first when the stack is full.
    while (popHost.children.length > POP_MAX) popHost.firstChild.remove();

    /* Most cards are glanceable and should get out of the way. A card you are
       meant to ACT on is different - one that retires itself while you are
       reading the description is worse than no card at all. Sticky cards start
       pinned and only leave when dismissed. */
    let pinned = !!card.sticky;
    let timer = pinned ? null : setTimeout(retire, popHold());
    if (pinned) el.classList.add('pinned');

    function retire() {
      if (pinned) return;
      el.classList.add('going');
      setTimeout(() => el.remove(), 300);
    }
    function hold() { clearTimeout(timer); }
    function resume() { if (!pinned) timer = setTimeout(retire, 2600); }

    el.addEventListener('mouseenter', hold);
    el.addEventListener('mouseleave', resume);
    el.addEventListener('click', e => {
      if (e.target === close) return;
      pinned = !pinned;
      el.classList.toggle('pinned', pinned);
      if (pinned) hold(); else resume();
    });
    close.addEventListener('click', () => { pinned = false; clearTimeout(timer); retire(); });

    return el;
  }

  /* Structured cards from a tool. The old regex parsers still run for the
     sources that have not been given a card payload yet. */
  J.on('pop-card', card => showPop(card));


  /* ======================================================== the job hunt

     Runs on the server, on its own thread, so it cannot block a conversation.
     This side only asks what it found.

     It never announces anything. Matches raise a number on a tab and wait —
     the same agreement the notice queue lives under. A job found while Zero
     is mid-sentence is still there when he looks up. */

  async function jobsApi(payload) {
    const res = await fetch('api/jobs/command', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return await res.json();
  }

  const jobsToggle = J.$('#jobsToggle');
  const jobsState = J.$('#jobsState');
  const jobsList = J.$('#jobsList');
  const jobsCount = J.$('#jobsCount');

  function paintJobsState(s) {
    if (!jobsToggle) return;
    // Two pollers share this chip on different intervals. Without this the
    // slower one paints "off" over a live hunt for up to eight seconds, which
    // reads as the hunt dying and coming back.
    if (huntActive) return;
    const on = !!(s && s.running);
    jobsToggle.classList.toggle('is-on', on);
    jobsToggle.setAttribute('aria-pressed', String(on));
    if (jobsState) {
      jobsState.textContent = !s ? '--'
        : s.scanning ? 'scanning' : (on ? 'on' : 'off');
    }
    const waiting = (s && s.counts && s.counts.new) || 0;
    if (jobsCount) {
      jobsCount.textContent = waiting;
      jobsCount.hidden = !waiting;
    }
  }

  async function jobsStatus() {
    try { paintJobsState(await jobsApi({ action: 'status' })); }
    catch (e) { /* server down; the chip simply stops changing */ }
  }

  /* ---- the guided hunt, watched rather than reported ----

     The batch card used to be drawn from the tool result, which meant it only
     appeared if the model called the tool AND relayed it. Both failed in
     practice: with a restored conversation he decided a batch was already on
     the table and called nothing at all, so no card was drawn even though the
     server had three matches waiting.

     The batches also arrive on their own — the loop replaces them every ten
     minutes whether or not anyone is talking. There is no tool call to hang a
     card on at all in that case.

     So the page watches the session instead. A new round number means a new
     three, and the card is drawn from that. Silently: this draws, it never
     speaks, and it never sends anything to the model. */
  const HUNT_POLL = 8000;
  let huntRound = null;
  let huntActive = false;

  async function huntApi(payload) {
    const res = await fetch('api/hunt/command', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return await res.json();
  }
  J.huntApi = huntApi;                 // the batch card's buttons use it

  async function huntPoll() {
    let s;
    try { s = await huntApi({ action: 'pending' }); }
    catch (e) { return; }
    if (!s || !s.ok) return;

    const was = huntActive;
    huntActive = !!s.running;
    paintHuntState(s);
    if (was && !huntActive) jobsStatus();     // hand the chip back

    if (!s.batch || !s.batch.length) {
      if (!s.running) huntRound = null;      // session ended; next start is new
      return;
    }
    // huntOnScreen, not a remembered round: if anything removed the card the
    // next poll puts it back, which is what "show me a top 3 whenever you find
    // one" actually requires.
    const key = s.seq != null ? s.seq : s.round;
    if (key === huntRound && huntOnScreen(key)) return;
    huntRound = key;
    J.emit('hunt-batch', { round: s.round, key: key, batch: s.batch, fresh: true });
  }

  /* The HUNT chip covers both hunts, because from the outside they are one
     thing: is he looking for work right now. */
  function paintHuntState(s) {
    if (!jobsToggle || !s) return;
    if (!s.running) return;                  // leave the chip to jobsStatus
    jobsToggle.classList.add('is-on');
    jobsToggle.setAttribute('aria-pressed', 'true');
    if (jobsState) jobsState.textContent = s.batch && s.batch.length ? 'choose' : 'hunting';
  }

  setInterval(huntPoll, HUNT_POLL);
  huntPoll();

  if (jobsToggle) jobsToggle.addEventListener('click', async () => {
    /* One chip, two hunts, and the guided one wins while it is running.
       Clicking it while it read "choose" used to stop the OTHER hunt - the
       background board watcher - leaving the label frozen on a session that
       was still going. Whatever the chip is showing is what it controls. */
    try {
      const h = await huntApi({ action: 'pending' });
      if (h && h.ok && h.running) {
        await huntApi({ action: 'stop' });
        huntActive = false;
        J.log('Job hunt stopped', 'warn', 'sys');
        return jobsStatus();
      }
    } catch (e) { /* the server is down; nothing to toggle */ }

    /* Starting it does not go through the model.

       "Start job search" spoken aloud reached a model that decided, from a
       conversation it half-remembered, that a form was still open and answered
       about that instead — no tool call, no batch, nothing. A button cannot
       change its mind. The background board watcher keeps its own control in
       the jobs pane; this chip drives the hunt whose state it displays. */
    const s = await jobsApi({ action: 'status' }).catch(() => ({}));
    if (s && s.has_profile === false) {
      J.toast('Set the job profile first — Configuration → Job hunt. '
            + 'Without it there is nothing to match against.', 'warn', 7000);
      showPane('jobs');
      return;
    }

    J.log('Job hunt starting — scraping and scoring, about a minute', 'ok', 'sys');
    if (jobsState) jobsState.textContent = 'hunting';
    jobsToggle.classList.add('is-on');
    huntActive = true;

    const r = await huntApi({ action: 'start' }).catch(e => ({ ok: false, error: e.message }));
    if (!r.ok) {
      huntActive = false;
      jobsStatus();
      return J.toast(r.error || 'The hunt could not start.', 'warn', 7000);
    }
    huntPoll();                       // draw the batch without waiting for the tick
  });

  function jobRow(job) {
    const row = document.createElement('div');
    row.className = 'jrow state-' + (job.state || 'new');

    const head = document.createElement('div');
    head.className = 'jrow-head';
    const score = document.createElement('span');
    score.className = 'jrow-score';
    score.textContent = Math.round((job.score || 0) * 100);
    const title = document.createElement('b');
    title.textContent = job.title;
    head.append(score, title);

    const meta = document.createElement('div');
    meta.className = 'jrow-meta';
    meta.textContent = [job.company, job.location].filter(Boolean).join(' · ');

    const acts = document.createElement('div');
    acts.className = 'jrow-acts';

    const open = document.createElement('button');
    open.className = 'mini'; open.type = 'button'; open.textContent = 'open';
    open.addEventListener('click', () => openExternally(job.url));

    const yes = document.createElement('button');
    yes.className = 'mini'; yes.type = 'button'; yes.textContent = 'interested';
    yes.addEventListener('click', async () => {
      await jobsApi({ action: 'decide', id: job.id, verdict: 'interested' });
      loadJobs();
    });

    const no = document.createElement('button');
    no.className = 'mini'; no.type = 'button'; no.textContent = 'pass';
    no.addEventListener('click', async () => {
      await jobsApi({ action: 'decide', id: job.id, verdict: 'passed' });
      loadJobs();
    });

    acts.append(open, yes, no);
    row.append(head, meta, acts);
    return row;
  }

  async function loadJobs() {
    if (!jobsList) return;
    jobsList.textContent = 'reading…';
    let d, s;
    try {
      d = await jobsApi({ action: 'list' });
      s = await jobsApi({ action: 'status' });
    } catch (e) { jobsList.textContent = 'Could not reach the server.'; return; }

    paintJobsState(s);
    const note = J.$('#jobsNote');
    if (note) {
      note.textContent = s.has_profile
        ? (s.watching + ' companies' + (s.running ? ' · hunting' : ''))
        : 'no profile yet';
    }

    jobsList.innerHTML = '';
    if (!s.has_profile) {
      jobsList.innerHTML = '<p class="pane-empty">He has nothing to match against '
        + 'yet. Press <b>profile</b> and tell him what you do, where you will work, '
        + 'and what to leave out.</p>';
      return;
    }
    const rows = (d.jobs || []).filter(j => j.state !== 'passed');
    if (!rows.length) {
      jobsList.innerHTML = '<p class="pane-empty">Nothing yet. Turn <b>HUNT</b> on in '
        + 'the top bar, or press <b>scan now</b> for one sweep.</p>';
      return;
    }
    rows.forEach(j => jobsList.appendChild(jobRow(j)));
  }

  const jobsScan = J.$('#jobsScan');
  if (jobsScan) jobsScan.addEventListener('click', async () => {
    jobsScan.disabled = true;
    jobsScan.textContent = 'scanning…';
    const r = await jobsApi({ action: 'scan' });
    jobsScan.disabled = false;
    jobsScan.textContent = 'scan now';
    /* The count alone is not a result. "0 new" and "0 new, 94 in the wrong
       place, 56 excluded by your keywords" are the same number and completely
       different information. */
    J.toast(r.summary || r.error, r.ok ? 'ok' : 'warn', 9000);
    loadJobs();
  });

  /* The profile. Deliberately a plain form rather than a conversation — this
     is the thing every match is scored against, and it should be editable
     without asking him to re-say it. */
  const jobsProfileBtn = J.$('#jobsProfile');
  if (jobsProfileBtn) jobsProfileBtn.addEventListener('click', async () => {
    const d = await jobsApi({ action: 'profile' });
    const p = d.profile || {};
    const box = document.createElement('div');
    box.className = 'jprofile';
    box.innerHTML =
        '<label>Job titles you want<textarea id="jpTitles" rows="2"></textarea></label>'
      + '<label>Skills and tools<textarea id="jpSkills" rows="3"></textarea></label>'
      + '<label>What you are after<textarea id="jpWants" rows="2"></textarea></label>'
      + '<label>Where you will work<textarea id="jpLoc" rows="2" '
      + 'placeholder="Los Angeles, remote"></textarea></label>'
      + '<label>Never show me<textarea id="jpEx" rows="2" '
      + 'placeholder="manager, director, night shift"></textarea></label>'
      + '<div class="jdrop" id="jDrop" tabindex="0" role="button">'
      + '<b>Drop your resume here</b>'
      + '<span>PDF, Word or text &mdash; or click to choose. He reads it, keeps it '
      + 'on this machine, and writes you a stronger version.</span></div>'
      + '<label>Resume<textarea id="jpResume" rows="6" '
      + 'placeholder="Dropped above, or paste it here. Stored on this machine and only '
      + 'ever leaves it inside an application you approved."></textarea></label>'
      + '<div class="jprofile-acts"><button class="btn tiny" id="jpSave">Save</button>'
      + '<button class="btn tiny ghost" id="jpCancel">Cancel</button></div>';
    jobsList.innerHTML = '';
    jobsList.appendChild(box);

    J.$('#jpTitles').value = p.titles || '';
    J.$('#jpSkills').value = p.skills || '';
    J.$('#jpWants').value = p.wants || '';
    J.$('#jpLoc').value = p.locations || '';
    J.$('#jpEx').value = p.exclude || '';
    J.$('#jpResume').value = p.resume || '';

    wireResumeDrop();
    J.$('#jpCancel').addEventListener('click', loadJobs);
    J.$('#jpSave').addEventListener('click', async () => {
      await jobsApi({ action: 'set_profile', profile: {
        titles: J.$('#jpTitles').value, skills: J.$('#jpSkills').value,
        wants: J.$('#jpWants').value, locations: J.$('#jpLoc').value,
        exclude: J.$('#jpEx').value, resume: J.$('#jpResume').value
      }});
      J.toast('Profile saved.', 'ok');
      loadJobs();
    });
  });

    /* Dropping a resume is the shortest path into this whole feature, so it has
     to work with a file rather than a paste. PDFs and Word documents cannot be
     read in a browser at all; the server already knows how, because the
     document indexer needed the same thing. */
  async function readResumeFile(file) {
    const drop = J.$('#jDrop');
    const box = J.$('#jpResume');
    if (!file || !box) return;

    if (file.size > 8 * 1024 * 1024) {
      if (drop) drop.dataset.note = 'That file is too large.';
      return;
    }

    if (drop) { drop.classList.add('busy'); drop.dataset.note = 'reading ' + file.name; }
    try {
      const form = new FormData();
      form.append('file', file, file.name);
      const d = await (await fetch('api/extract', { method: 'POST', body: form })).json();
      if (!d.ok) {
        if (drop) drop.dataset.note = d.error || 'Could not read that file.';
        return;
      }
      box.value = d.text;
      await jobsApi({ action: 'set_profile', profile: { resume: d.text } });
      if (drop) drop.dataset.note = d.name + ' — ' + d.text.length + ' characters read';
      J.log('Resume read: ' + d.name, 'ok', 'sys');
      J.toast('Resume stored. Asking him to review it.', 'ok', 5000);

      /* Hand it straight to the conversation. Reviewing a CV is judgement, not
         parsing — it belongs with the model, and routing it through a normal
         turn means the rewrite lands in a project like any other file he
         writes: diffable, revertible, openable. */
      J.brain.send(
        'I have just uploaded my resume and it is stored in my job profile. '
        + 'Read it, tell me plainly what is weak about it — vague lines, missing '
        + 'numbers, anything a recruiter would skim past — and then scaffold a '
        + 'project called resume and write an improved version into it as '
        + 'resume.md. Keep every fact true to what I gave you and invent nothing. '
        + 'Here it is:' + String.fromCharCode(10, 10) + d.text.slice(0, 6000));
    } catch (e) {
      if (drop) drop.dataset.note = e.message || String(e);
    } finally {
      if (drop) drop.classList.remove('busy');
    }
  }

  function wireResumeDrop() {
    const drop = J.$('#jDrop');
    if (!drop) return;
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.accept = '.pdf,.docx,.doc,.txt,.md,.rtf';
    picker.hidden = true;
    drop.appendChild(picker);

    drop.addEventListener('click', () => picker.click());
    drop.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); picker.click(); }
    });
    picker.addEventListener('change', () => {
      if (picker.files && picker.files[0]) readResumeFile(picker.files[0]);
    });

    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => {
      e.preventDefault(); e.stopPropagation(); drop.classList.add('over');
    }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
      e.preventDefault(); e.stopPropagation(); drop.classList.remove('over');
    }));
    drop.addEventListener('drop', e => {
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) readResumeFile(f);
    });
  }

  /* ---- a job worth seeing gets a card ----

     Still no announcement and no sound. The card rises, holds for its ten
     seconds and retires into the transcript, exactly like a score. The
     agreement was that nothing speaks, not that nothing appears. */

  let lastNewCount = null;

  async function jobsPop() {
    try {
      const s = await jobsApi({ action: 'status' });
      const n = (s.counts && s.counts.new) || 0;
      if (lastNewCount === null) { lastNewCount = n; return; }
      if (n <= lastNewCount) { lastNewCount = n; return; }

      const arrived = n - lastNewCount;
      lastNewCount = n;

      const d = await jobsApi({ action: 'list', state: 'new' });
      const top = (d.jobs || []).slice(0, 3);
      if (!top.length) return;

      J.emit('pop-card', {
        title: arrived === 1 ? 'A job worth a look' : arrived + ' jobs worth a look',
        accent: 'ok',
        items: top.map(j => ({
          title: j.title,
          rows: [
            { k: 'Company', v: j.company + (j.location ? ' · ' + j.location : '') },
            { k: 'Match', v: Math.round(j.score * 100) + '%' }
          ]
        }))
      });
      J.log(arrived + ' new job match(es)', 'ok', 'sys');
    } catch (e) { /* offline; the tab badge still carries the count */ }
  }

  setInterval(jobsPop, 45000);

  jobsStatus();
  setInterval(jobsStatus, 30000);


  /* ============================================== 6 · ambient mode

     A wall display spends most of its life not being talked to. Rather than
     sitting on a conversation nobody is reading, it drops to the three things
     worth seeing from across a room: the time, the weather, and what is
     playing.

     Everything wakes it — voice, a key, the mouse, a reply arriving. It is a
     screensaver, not a mode you have to leave. */

  const ambient = J.$('#ambient');
  function ambientAfter() {
    const n = Number(J.settings.ambientAfter);
    return Number.isFinite(n) ? n * 1000 : 180000;
  }
  let ambientTimer = null;
  let ambientOn = false;

  function ambientPaint() {
    const now = new Date();
    const c = J.$('#ambClock');
    if (c) c.textContent = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    const dt = J.$('#ambDate');
    if (dt) {
      dt.textContent = now.toLocaleDateString(undefined,
        { weekday: 'long', day: 'numeric', month: 'long' });
    }

    /* Read from the panels that are already correct rather than fetching
       again — the weather card is refreshed on its own schedule and there is
       no reason for two sources of the same number. */
    const temp = J.$('#wxTemp'), desc = J.$('#wxDesc');
    const at = J.$('#ambTemp'), ad = J.$('#ambDesc');
    if (at && temp) at.textContent = temp.textContent.trim();
    if (ad && desc) ad.textContent = desc.textContent.trim();

    const np = J.$('#spotifyState');
    const box = J.$('#ambTrack');
    if (box && np) {
      const text = (np.textContent || '').trim();
      const playing = text && !/^(nothing|not playing|paused|--)/i.test(text);
      box.hidden = !playing;
      if (playing) {
        const parts = text.split(/\s+[—–-]\s+/);
        J.$('#ambTrackName').textContent = parts[0] || text;
        J.$('#ambArtist').textContent = parts.slice(1).join(' — ');
      }
    }
  }

  function ambientEnter() {
    if (!ambient || ambientOn || J.brain.isBusy()) return;
    ambientOn = true;
    ambientPaint();
    ambient.hidden = false;
    /* Force the reflow rather than waiting for a frame. requestAnimationFrame
       does not fire in a tab that is not compositing — a backgrounded window,
       another desktop — and the panel would then be present but permanently
       transparent, which looks exactly like a bug. */
    void ambient.offsetWidth;
    ambient.classList.add('in');
    J.log('Ambient mode', 'info', 'sys');
  }

  function ambientExit() {
    if (!ambient || !ambientOn) return;
    ambientOn = false;
    ambient.classList.remove('in');
    setTimeout(() => { if (!ambientOn) ambient.hidden = true; }, 420);
  }

  function ambientTouch() {
    ambientExit();
    clearTimeout(ambientTimer);
    const wait = ambientAfter();
    if (wait > 0) ambientTimer = setTimeout(ambientEnter, wait);
  }

  if (ambient) {
    ['pointerdown', 'keydown', 'wheel'].forEach(ev =>
      document.addEventListener(ev, ambientTouch, { passive: true }));
    J.on('turn-end', ambientTouch);
    J.on('wake-state', up => { if (up) ambientTouch(); });
    J.on('heard', ambientTouch);
    setInterval(() => { if (ambientOn) ambientPaint(); }, 1000);
    ambientTouch();
  }

  /* ======================================= 13 · the diagnostic sweep

     One scanline down the whole dashboard, every few minutes. It says the
     machine is awake and watching itself — the same reason a heartbeat
     monitor beeps when nothing is wrong.

     Rare on purpose. Something that passes constantly stops being noticed and
     starts being noise. */

  const sweepEl = J.$('#diagSweep');
  const SWEEP_EVERY = 240000;          // four minutes

  function runSweep() {
    if (!sweepEl || J.reducedMotion || document.hidden || ambientOn) return;
    sweepEl.classList.remove('run');
    void sweepEl.offsetWidth;          // restart the animation
    sweepEl.classList.add('run');
  }

  if (sweepEl) {
    setTimeout(runSweep, 8000);
    setInterval(runSweep, SWEEP_EVERY);
    /* A sweep on every reply would be constant. One after a build or a long
       job is the moment it actually reads as "checked". */
    J.on('build-check', runSweep);
  }

})(window.J);
