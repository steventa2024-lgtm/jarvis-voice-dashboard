/* ==========================================================================
   voice.js — speech in, speech out
   Recognition drives the orb through a real AnalyserNode. Speech output is
   queued by sentence so replies begin speaking while the model is still
   streaming. Both halves degrade to silence rather than to an error.
   ========================================================================== */

(function (J) {

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis || null;

  const supported = { listen: !!SR, speak: !!synth };

  let recognition = null;
  let wantListening = false;     // the user's intent, not the engine's state
  let engineRunning = false;
  let speaking = false;
  let speakingSince = 0;
  const BARGE_GRACE = 450;   // ms before an interruption is believed
  let restartTimer = null;

  /* ----------------------------------------------------------- preflight */

  /* Why can this browser not listen? Answered before touching any device API,
     because the two most common causes are environmental rather than technical,
     and a generic "microphone error" tells the user nothing they can act on.

     The big one: getUserMedia and SpeechRecognition both require a secure
     context. That means https, or http on localhost — and NOT a plain http LAN
     address, which is exactly how a dashboard tends to get opened on a
     television. */
  function diagnose() {
    if (!window.isSecureContext) {
      if (location.protocol === 'file:') {
        return {
          code: 'file',
          title: 'Opened as a file, so the microphone is blocked',
          detail: 'Browsers only allow microphone access over https or on localhost. '
                + 'Serve this folder instead: run start.bat, or '
                + '"python -m http.server 8123", then open http://localhost:8123.'
        };
      }
      return {
        code: 'insecure',
        title: 'Microphone blocked on ' + location.host,
        detail: 'A plain http address on the network is not a secure context, so the '
              + 'browser refuses microphone access. Open the dashboard at '
              + 'http://localhost:8123 on the machine itself, or put it behind https. '
              + 'The README has the television setup.'
      };
    }

    if (!SR) {
      return {
        code: 'unsupported',
        title: 'This browser has no speech recognition',
        detail: 'Chrome and Edge support it. Everything else here works — type instead.'
      };
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return {
        code: 'nodevice',
        title: 'No media device API in this browser',
        detail: 'Voice input is unavailable; typed input works.'
      };
    }

    return null;
  }

  function report(problem) {
    J.toast(problem.title + ' — ' + problem.detail, 'crit', 12000);
    J.log(problem.title, 'crit', 'sys');
    J.log(problem.detail, 'warn', 'sys');
    J.emit('mic-problem', problem);
  }

  /* Chrome can say whether permission is already granted or denied without
     prompting, which lets the interface be honest before the first click. */
  async function permissionState() {
    if (!navigator.permissions || !navigator.permissions.query) return 'unknown';
    try {
      const st = await navigator.permissions.query({ name: 'microphone' });
      return st.state;                        // granted | denied | prompt
    } catch (e) {
      return 'unknown';
    }
  }

  /* --------------------------------------------------------- audio devices */

  /* Labels stay blank until microphone permission has been granted — the
     browser will not tell a page what hardware exists before then. */
  async function listDevices() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      return { inputs: [], outputs: [], labelled: false };
    }
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      const pick = kind => devs.filter(d => d.kind === kind)
        .map(d => ({ id: d.deviceId, label: d.label || '' }));
      const inputs = pick('audioinput');
      return {
        inputs: inputs,
        outputs: pick('audiooutput'),
        labelled: inputs.some(d => d.label)
      };
    } catch (e) {
      return { inputs: [], outputs: [], labelled: false, error: e.message };
    }
  }

  /* Changing input means tearing the capture down and building it again —
     a MediaStream is bound to the device it was opened on. */
  async function switchDevice(deviceId) {
    J.set({ micDevice: deviceId || '' });

    const wasListening = wantListening;
    stopListening();

    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
    if (audioCtx) { try { await audioCtx.close(); } catch (e) {} audioCtx = null; }
    analyser = null;
    J.orb.attachAnalyser(null);

    if (wasListening) {
      const mic = await openMeter();
      if (!mic.ok) { report(mic); return mic; }
      await startListening();
    }
    return { ok: true };
  }

  /* ------------------------------------------------------------ mic meter */

  let audioCtx = null, micStream = null, analyser = null;

  /* A chosen input is a preference, not a requirement.

     `deviceId: { exact }` rejects with OverconstrainedError the moment that
     device is absent — a Bluetooth headset switched off is enough — and that
     name matches none of the handlers below, so it fell through to "Could not
     open the microphone" and the dashboard simply went deaf. No wake word, no
     voice, no clue why.

     Losing a headset must not cost the wake word. A pinned device that is not
     there falls back to whatever Windows calls default, and says so. */
  async function openStream(want) {
    if (!want) return navigator.mediaDevices.getUserMedia({ audio: true });

    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: { deviceId: { exact: want } }
      });
    } catch (e) {
      const gone = e && (e.name === 'OverconstrainedError' ||
                         e.name === 'ConstraintNotSatisfiedError' ||
                         e.name === 'NotFoundError');
      if (!gone) throw e;
      J.log('The chosen microphone is not available — using the system default instead',
            'warn', 'sys');
      return navigator.mediaDevices.getUserMedia({ audio: true });
    }
  }

  /* Opening the meter is also what raises the permission prompt. Doing it
     before starting recognition means a refusal surfaces as a real reason
     instead of a bare "not-allowed" from the recognition engine. */
  async function openMeter() {
    if (analyser) return { ok: true };

    try {
      micStream = await openStream(J.settings.micDevice);
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();

      // Chrome starts an AudioContext suspended unless it was created inside a
      // gesture; resuming is a no-op when it is already running.
      if (audioCtx.state === 'suspended') await audioCtx.resume().catch(function () {});

      const src = audioCtx.createMediaStreamSource(micStream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.72;
      src.connect(analyser);
      J.orb.attachAnalyser(analyser);

      const track = micStream.getAudioTracks()[0];
      J.log('Microphone open' + (track && track.label ? ' — ' + track.label : ''), 'ok', 'sys');

      /* A hands-free profile is 8kHz narrowband mono. It drives the meter
         perfectly well and it cannot drive the recogniser, which is the exact
         combination that looks like working and is not. */
      if (track && /hands-?free|bluetooth/i.test(track.label || '')) {
        J.log('That is a Bluetooth hands-free input — speech recognition usually gets '
            + 'nothing from it. A USB microphone is the reliable choice.', 'warn', 'sys');
      }
      return { ok: true };
    } catch (e) {
      const name = e && e.name;

      if (name === 'NotAllowedError' || name === 'SecurityError') {
        return { ok: false, code: 'denied',
          title: 'Microphone permission denied',
          detail: 'Click the padlock or camera icon in the address bar, set Microphone '
                + 'to Allow, then reload the page.' };
      }
      if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        return { ok: false, code: 'missing',
          title: 'No microphone found',
          detail: 'Windows reports no input device. Check Settings, System, Sound, Input '
                + '— a television connected over HDMI has no microphone of its own.' };
      }
      if (name === 'OverconstrainedError' || name === 'ConstraintNotSatisfiedError') {
        return { ok: false, code: 'gone',
          title: 'The chosen microphone is not connected',
          detail: 'Configuration, Microphone — pick a different input, or set it back '
                + 'to Default. A Bluetooth headset that is switched off does this.' };
      }
      if (name === 'NotReadableError' || name === 'TrackStartError') {
        return { ok: false, code: 'busy',
          title: 'Microphone is in use by another application',
          detail: 'Close whatever else is holding it — a call, a recorder, a game — '
                + 'then try again.' };
      }
      return { ok: false, code: 'unknown',
        title: 'Could not open the microphone',
        detail: (e && e.message) || String(e) };
    }
  }

  function closeMeter() {
    J.orb.attachAnalyser(null);
    if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
    if (audioCtx)  { audioCtx.close().catch(() => {}); audioCtx = null; }
    analyser = null;
  }

  /* ---------------------------------------------------------- recognition */

  function build() {
    if (!SR) return null;
    const r = new SR();
    r.continuous = true;
    r.interimResults = true;
    r.lang = navigator.language || 'en-US';
    r.maxAlternatives = 1;

    r.onstart = () => { engineRunning = true; };

    r.onresult = (event) => {
      let interim = '', final = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const res = event.results[i];
        if (res.isFinal) final += res[0].transcript;
        else interim += res[0].transcript;
      }

      if (interim || final) { heardSinceStart = true; netErrors = 0; }

      const box = J.$('#interim');
      if (box) box.textContent = interim ? interim.trim() : '';

      /* Barge-in. The moment they speak, stop talking over them.

         The grace period covers the commonest false trigger: the tail of
         their own question still arriving as he starts to answer it. */
      const heard = (final || interim).trim();
      if (speaking && heard && Date.now() - speakingSince > BARGE_GRACE
          && !isEcho(heard)) {
        J.log('Interrupted — ' + heard.slice(0, 40), 'warn', 'sys');
        shutUp();
        J.emit('interrupted', { heard: heard });
      }

      if (final.trim()) {
        if (box) box.textContent = '';
        const said = final.trim();
        if (soundsLikeHim(said)) {
          J.log('Ignored his own voice - "' + said.slice(0, 48) + '"', 'info', 'sys');
        } else {
          handleFinal(said);
        }
      }
    };

    r.onerror = (event) => {
      const err = event.error;
      if (err === 'no-speech' || err === 'aborted') return;   // routine, self-heals
      if (err === 'not-allowed' || err === 'service-not-allowed') {
        wantListening = false;
        paint();
        J.toast('Microphone access denied. Enable it in the address-bar permissions, then try again.', 'crit', 7000);
        J.log('Microphone permission denied', 'crit', 'sys');
        return;
      }
      /* `network` is what Chromium reports for anything wrong between here and
         the cloud speech service: no route, a refused request, a throttled
         client. It is emphatically NOT proof the machine is offline — saying
         so sent a whole morning chasing microphones and drivers while the
         weather widget sat there updating happily two inches away.

         It was also toast-only. The single error that was actually happening,
         on every session, left no trace in the log whatsoever. */
      if (err === 'network') {
        netErrors++;
        if (netErrors === 1 || netErrors % 10 === 0) {
          J.log('Speech service refused the recogniser (network) x' + netErrors
              + ' — this is not the machine being offline', 'crit', 'sys');
        }
        if (netErrors === 3) {
          J.toast('The speech service is refusing this browser. Everything else here is '
                + 'online, so this is the recogniser alone — not your microphone and not '
                + 'your connection. Open mic-test.html to confirm it.', 'crit', 15000);
        }
        return;
      }
      J.log('Recognition error: ' + err, 'warn', 'sys');
    };

    r.onend = () => {
      engineRunning = false;
      // Chrome ends the session on its own schedule; restart if still wanted.
      // Chrome ends sessions on its own schedule. Refusing to restart while
      // he is speaking left the mic shut for the rest of the reply.
      if (wantListening && (J.settings.bargeIn !== 'off' || !speaking)) {
        clearTimeout(restartTimer);
        if (Date.now() - lastSoundAt > IDLE_GATE_AFTER) {
          if (!gated) {
            gated = true;
            J.log('Quiet for a minute — holding the recogniser until there is sound',
                  'info', 'sys');
          }
          paint();
        } else {
          restartTimer = setTimeout(safeStart,
            netErrors > 2 ? Math.min(30000, 1000 * netErrors) : 260);
        }
      } else {
        paint();
      }
    };

    return r;
  }

  function safeStart() {
    if (!recognition || engineRunning || !wantListening) return;
    try { recognition.start(); }
    catch (e) { /* already starting — the onend handler will retry */ }
  }

  /* ---------------------------------------------------- wake / sleep ----

     The old behaviour made you say "Jarvis" before every single sentence,
     which is not how anyone talks. This is a two-state machine instead:

       asleep  — everything heard is discarded until the wake phrase arrives
       awake   — speech goes straight through, and each utterance resets a
                 timer; when it expires he drops back to asleep

     Speech recognition mangles the name constantly, so the wake pattern is
     generous. "Hey" is optional — "jarvis, what's the weather" wakes him too. */

  let awake = false;
  let sleepTimer = null;

  const WAKE = /\b(?:hey|hi|ok|okay|yo)?\s*(jarvis|jervis|jarvix|travis|charvis|服务|jarv)\b[,.:!\s]*/i;

  function idleMs() {
    const s = Number(J.settings.sleepAfter);
    return (isFinite(s) && s > 0 ? s : 60) * 1000;
  }

  function armSleep() {
    clearTimeout(sleepTimer);
    sleepTimer = setTimeout(goToSleep, idleMs());
  }

  function goToSleep() {
    if (!awake) return;
    awake = false;
    clearTimeout(sleepTimer);
    J.log('Back to standby — say "Hey Jarvis" to wake me', 'info', 'sys');
    J.emit('wake-state', false);
    paint();
  }

  function wakeUp(announce) {
    const was = awake;
    awake = true;
    armSleep();
    if (!was) {
      J.log('Awake — listening', 'ok', 'sys');
      J.emit('wake-state', true);
      if (announce && J.settings.speak) say('Yes?');
      paint();
    }
  }

  function isAwake() { return awake || !J.settings.wakeWord; }

  /* A question currently on screen, and what an answer to it looks like.

     The wake word exists so the room can be talked in without him joining in.
     But when a card is asking "Option 1, 2, 3, or decline all?", an answer to
     it is addressed to him by construction, and making Zero say "Hey Jarvis"
     first is the interface forgetting what it just asked — measured live as
     "Asleep — ignored: Option one." with the card still on the screen.

     Narrow on purpose. It matches an answer to a question that is actually
     posted, never a bare "one" or "three" in passing conversation. */
  let awaiting = null;

  function expect(pattern) { awaiting = pattern || null; }

  function handleFinal(text) {
    // Wake word off entirely: every utterance counts, as before.
    if (!J.settings.wakeWord) { J.emit('utterance', text); return; }

    const m = WAKE.exec(text);

    if (!awake) {
      if (!m && awaiting && awaiting.test(text)) {
        J.log('Answered what was on screen — ' + text, 'acc', 'sys');
        wakeUp(false);
        J.emit('utterance', text.trim());
        return;
      }
      if (!m) { J.log('Asleep — ignored: ' + text, 'info', 'sys'); return; }
      const rest = text.slice(m.index + m[0].length).trim();
      wakeUp(!rest);
      if (!rest) { J.status('listening', 'go ahead'); return; }
      J.emit('utterance', rest);
      return;
    }

    // Already awake. Strip the name if he was addressed again, then carry on.
    armSleep();
    const body = m ? text.slice(m.index + m[0].length).trim() : text.trim();
    if (!body) { J.status('listening', 'go ahead'); return; }
    J.emit('utterance', body);
  }

  /* --------------------------------------------- the deaf recogniser

     The failure this exists for: the meter danced, the reactor said
     LISTENING, and not one word was ever transcribed. Nothing was logged,
     because a recogniser fed silence fires `no-speech` and that is swallowed
     above as routine — so the only symptom was being ignored.

     The two halves do not share a device. getUserMedia opens whatever is
     chosen in Configuration; SpeechRecognition has no device API at all and
     always reads the Windows default input. When those differ, or when the
     default is a Bluetooth hands-free endpoint, the meter is fed and the
     recogniser is not.

     Sustained signal with nothing transcribed is not a quiet room. */
  const DEAF_AFTER = 12000;         // ms of sound with no transcript whatsoever
  const IDLE_GATE_AFTER = 60000;    // ms of silence before we stop asking the cloud
  const TICK = 250;
  const FLOOR = 0.06;               // peak deviation that counts as somebody being there
  let heardSinceStart = false;
  let deafReported = false;
  let deafTimer = null;
  let lastSoundAt = 0;
  let gated = false;                // idle: engine deliberately not running
  let netErrors = 0;                // consecutive refusals from the speech service

  function micLevel() {
    if (!analyser) return 0;
    const buf = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(buf);
    let peak = 0;
    for (let i = 0; i < buf.length; i++) {
      const d = Math.abs(buf[i] - 128);
      if (d > peak) peak = d;
    }
    return peak / 128;
  }

  /* ------------------------------------------------ the idle gate

     Left listening in an empty room, `onend` restarted the engine 260ms after
     every `no-speech`. That is a cycle of roughly seven seconds, which over a
     night of nobody being home is some five thousand sessions against a cloud
     speech service — for silence.

     The meter is local and free. Use it as the gate: while the room is quiet
     the engine is simply not run, and the instant there is sound it starts
     again. A quiet house now costs nothing at all.

     The gate only engages after a full minute of silence, so ordinary use —
     where somebody has made a noise in the last minute — behaves exactly as
     it always did, at the same 260ms. */
  function voiceLoop() {
    clearInterval(deafTimer);
    let loudFor = 0;

    deafTimer = setInterval(() => {
      if (!wantListening) return;

      const loud = micLevel() > FLOOR;
      if (loud) lastSoundAt = Date.now();

      /* Somebody is back. Start before they finish the first word — the wake
         pattern is loose by design and the tail is enough to match on. */
      if (gated && loud) {
        gated = false;
        J.log('Sound again — listening', 'info', 'sys');
        safeStart();
      }

      if (heardSinceStart || deafReported) return;

      // Decay, so a door slamming twice in a minute never adds up to a verdict.
      if (loud) loudFor += TICK; else loudFor = Math.max(0, loudFor - TICK / 2);
      if (loudFor < DEAF_AFTER) return;

      deafReported = true;
      const track = micStream && micStream.getAudioTracks()[0];
      const label = (track && track.label) || 'the chosen input';
      J.log('Sound is reaching the meter but nothing has been transcribed. Speech '
          + 'recognition reads the Windows default input, not "' + label + '"', 'crit', 'sys');
      J.toast('He can see your voice but cannot hear it. Speech recognition always uses the '
            + 'Windows default input, whatever is chosen here — set it to a USB microphone '
            + 'under Settings, System, Sound. A Bluetooth hands-free headset cannot drive it.',
            'crit', 14000);
    }, TICK);
  }

  async function startListening() {
    if (wantListening) return;

    const problem = diagnose();
    if (problem) { report(problem); return; }

    const mic = await openMeter();
    if (!mic.ok) { report(mic); return; }

    wantListening = true;
    heardSinceStart = false;
    deafReported = false;
    gated = false;
    netErrors = 0;
    lastSoundAt = Date.now();     // never gate before anyone has had a chance to speak
    if (!recognition) recognition = build();
    safeStart();
    voiceLoop();
    paint();
    J.log('Voice input engaged', 'acc', 'sys');
  }

  function stopListening() {
    if (!wantListening) return;
    wantListening = false;
    clearTimeout(restartTimer);
    clearInterval(deafTimer); deafTimer = null;
    gated = false;
    try { if (recognition) recognition.stop(); } catch (e) {}
    closeMeter();
    const box = J.$('#interim');
    if (box) box.textContent = '';
    paint();
    J.log('Voice input released', 'info', 'sys');
  }

  function toggle() { wantListening ? stopListening() : startListening(); }

  /* Whether to go deaf while talking.

     This used to be unconditional, and it is why interrupting him did nothing:
     the barge-in branch in onresult was written, correct, and unreachable,
     because the recogniser was stopped for the entire length of every reply.

     Keeping it open is what makes him interruptible. The cost is that on open
     speakers he hears himself, which isEcho() exists to absorb. */
  function pauseForSpeech() {
    if (J.settings.bargeIn === 'off' && engineRunning) {
      try { recognition.stop(); } catch (e) {}
    }
  }
  function resumeAfterSpeech() {
    if (wantListening) { clearTimeout(restartTimer); restartTimer = setTimeout(safeStart, 220); }
  }

  /* -------------------------------------------------------------- speaking */

  let queue = [];
  let pending = '';       // text streamed in but not yet a complete sentence
  let voices = [];

  function loadVoices() {
    if (!synth) return;
    voices = synth.getVoices() || [];
    J.emit('voices', voices);
  }

  function pickVoice() {
    if (!voices.length) loadVoices();
    if (J.settings.voiceURI) {
      const chosen = voices.find(v => v.voiceURI === J.settings.voiceURI);
      if (chosen) return chosen;
    }
    const lang = (navigator.language || 'en-US').slice(0, 2);
    // Prefer a natural-sounding local voice in the user's language.
    return voices.find(v => v.lang.startsWith(lang) && /natural|neural|online/i.test(v.name))
        || voices.find(v => v.lang.startsWith(lang))
        || voices[0] || null;
  }

  /* What is being said right now, normalised for comparison. Barge-in has to
     be able to tell the user's voice from the tail of its own, and the only
     signal available through SpeechRecognition is the words themselves. */
  let spokenNow = '';

  function norm(t) {
    return String(t || '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ')
           .replace(/\s+/g, ' ').trim();
  }

  /* Is what the microphone just heard simply J.A.R.V.I.S. hearing himself?

     On a headset this almost never fires — the speaker never reaches the mic.
     On open speakers it fires constantly, and without it he interrupts
     himself mid-sentence, every sentence, which is far worse than not being
     interruptible at all. */
  /* ---------------------------------------- telling his voice from yours

     The bug this exists for: on open speakers his own reply reaches the
     microphone, is transcribed, and is submitted as though Zero had said it.
     He then answers a question nobody asked.

     isEcho below already recognised this and only ever guarded barge-in -
     whether to stop talking. The submit path had no guard at all, so the code
     correctly identified his own voice and then processed it anyway.

     Two things it has to survive. spokenNow holds only the sentence in flight
     and is cleared the instant synthesis ends, so the tail of his speech still
     travelling to the microphone arrived with nothing to compare against. And
     recognition lags, so what comes back may be from several sentences ago.
     Hence a rolling window rather than one string.

     The safety property that matters: this returns false whenever he has not
     spoken recently, so it can never swallow something Zero actually said. */
  const ECHO_WINDOW = 20000;      // ms of his own speech worth remembering
  const ECHO_TAIL   = 2500;       // ms after he stops that his voice is still in the air
  const ECHO_MATCH  = 0.5;        // weak overlap - only counts while he is talking
  const ECHO_STRONG = 0.8;        // near-verbatim - counts whenever it arrives
  const ECHO_MIN    = 5;          // words needed before a match can stand on its own
  let spokenLog = [];
  let lastSpokeEnd = 0;

  function rememberSpoken(text) {
    const now = Date.now();
    spokenLog.push({ text: norm(text), at: now });
    spokenLog = spokenLog.filter(e => now - e.at < ECHO_WINDOW);
  }

  function soundsLikeHim(text) {
    const now = Date.now();
    const h = norm(text);
    if (!h) return false;

    const hay = [norm(spokenNow)]
      .concat(spokenLog.filter(e => now - e.at < ECHO_WINDOW).map(e => e.text))
      .filter(Boolean).join(' ');
    if (!hay) return false;

    /* Whether his voice could still physically be arriving. Recognition can
       deliver a final result seconds after the audio that produced it, which
       is why this window is generous rather than tight. */
    const live = speaking || now - lastSpokeEnd < ECHO_TAIL;

    const words = h.split(' ').filter(w => w.length > 2);

    /* A long, near-verbatim match needs no timing argument at all. Zero does
       not read his own answers back to him, so if a whole sentence he just
       said comes back through the microphone, it is the microphone. This is
       the case the timing gate missed: the transcript arrived late, he had
       stopped talking, and it sailed straight through as a user message. */
    if (words.length >= ECHO_MIN) {
      if (hay.indexOf(h) !== -1) return true;
      const hits = words.filter(w => hay.indexOf(w) !== -1).length;
      if (hits / words.length >= ECHO_STRONG) return true;
    }

    /* Anything shorter or weaker is only suspicious while he is actually
       talking. "playing today?" overlaps his answer completely and is a
       perfectly reasonable thing for Zero to ask once he has finished. */
    if (!live) return false;
    if (hay.indexOf(h) !== -1) return true;
    if (!words.length) return true;
    const hits = words.filter(w => hay.indexOf(w) !== -1).length;
    return hits / words.length >= ECHO_MATCH;
  }

  /* ------------------------------------------ the barge-in echo test

     Separate from soundsLikeHim above, and deliberately so. That one guards
     the submit path, where mistaking Zero for the speaker costs one repeated
     sentence. This one guards whether to STOP TALKING, where a mistake throws
     away the rest of a reply — shutUp clears the queue — so it is tuned
     against a different cost.

     The version this replaces compared only against spokenNow, the single
     sentence in flight. Speech is queued sentence by sentence and recognition
     lags seconds, so his own earlier sentences arrived when spokenNow had
     already moved on, matched nothing, and read as a person talking. Measured
     on a three-sentence reply: five false cuts out of nine.

     It also refused anything under five characters, which silently blocked
     "stop" and "wait" — the two likeliest interruptions there are.

     Three parameters, each measured over 22 labelled cases rather than picked:

       window     A short window is WORSE, not better: at 3s his own earlier
                  sentences fall out of the comparison and come back as false
                  cuts. 8s scored zero. Beyond that adds vocabulary without
                  adding accuracy.
       threshold  0.5 (what the submit path uses) suppressed 8 of 9 genuine
                  interruptions, because you interrupt ABOUT what is being
                  said — "cancel Thursday" while he says Thursday. 0.8 lets
                  those through and still catches every echo.
       matching   Whole words, not substrings. "not" matched inside "nothing",
                  which is what made "no not Thursday" read as his own voice.

     At 8s / 0.8 / whole-word: 22 of 22, no false cuts and no missed
     interruptions. */

  const BARGE_WINDOW = 8000;   // ms his own voice can still be arriving from
  const BARGE_MATCH  = 0.8;    // whole-word overlap that means it is him

  function isEcho(heard) {
    const h = norm(heard);
    if (!h) return true;
    const now = Date.now();

    const hay = [norm(spokenNow)]
      .concat(spokenLog.filter(e => now - e.at < BARGE_WINDOW).map(e => e.text))
      .filter(Boolean).join(' ');
    if (!hay) return false;      // he has not spoken; it cannot be his echo

    // Padded so a whole phrase matches on word boundaries.
    if ((' ' + hay + ' ').indexOf(' ' + h + ' ') !== -1) return true;

    const bag = new Set(hay.split(' '));
    const words = h.split(' ').filter(w => w.length > 2);
    if (!words.length) return true;          // "uh" is noise, not an interruption

    const hits = words.filter(w => bag.has(w)).length;
    return hits / words.length >= BARGE_MATCH;
  }

  function say(text) {
    if (!synth || !J.settings.speak) return;
    const clean = text
      .replace(/```[\s\S]*?```/g, ' code block omitted. ')
      .replace(/[*_`#>|]/g, '')
      .replace(/\[(.*?)\]\(.*?\)/g, '$1')
      .replace(/https?:\/\/\S+/g, 'link')
      .replace(/\s+/g, ' ')
      .trim();
    if (!clean) return;

    const u = new SpeechSynthesisUtterance(clean);
    const v = pickVoice();
    if (v) { u.voice = v; u.lang = v.lang; }
    u.rate = J.settings.rate;
    u.pitch = typeof J.settings.pitch === 'number' ? J.settings.pitch : 1;
    u.volume = 1;

    u.onstart = () => {
      spokenNow = clean;
      rememberSpoken(clean);
      speakingSince = Date.now();
      if (!speaking) { speaking = true; pauseForSpeech(); J.orb.setState('speaking'); }
    };
    u.onend = u.onerror = () => {
      if (!synth.speaking && !synth.pending) {
        speaking = false;
        lastSpokeEnd = Date.now();
        spokenNow = '';
        J.orb.setState(wantListening ? 'listening' : 'idle');
        resumeAfterSpeech();
        J.emit('spoken');
      }
    };

    synth.speak(u);
  }

  /* Feed streamed text in; speak it at sentence boundaries so the reply
     starts out loud long before the model finishes. */
  function feed(chunk) {
    if (!J.settings.speak || !synth) return;
    pending += chunk;

    // Speak up to the last completed sentence; keep the tail buffered.
    // Written without lookbehind so older Safari builds stay happy.
    const boundary = /[.!?…]["')\]]?(\s|$)|\n{2,}/g;
    let cut = -1, m;
    while ((m = boundary.exec(pending)) !== null) cut = m.index + m[0].length;

    if (cut > 0) {
      const ready = pending.slice(0, cut);
      pending = pending.slice(cut);
      if (ready.trim().length > 1) say(ready);
    }
  }

  function flush() {
    if (pending.trim()) { say(pending); }
    pending = '';
  }

  function shutUp() {
    pending = '';
    queue = [];
    if (synth) synth.cancel();
    speaking = false;
    J.orb.setState(wantListening ? 'listening' : 'idle');
    resumeAfterSpeech();
  }

  /* ----------------------------------------------------------------- paint */

  /* Speaking counts as activity: he should not fall asleep mid-answer. */
  function touch() { if (awake) armSleep(); }

  function paint() {
    const btn = J.$('#micBtn');
    if (btn) btn.classList.toggle('live', wantListening);
    if (!speaking) J.orb.setState(wantListening ? 'listening' : 'idle');
    J.emit('listening', wantListening);
  }

  /* ------------------------------------------------------------------ init */

  function init() {
    if (synth) {
      loadVoices();
      synth.addEventListener?.('voiceschanged', loadVoices);
      if (synth.onvoiceschanged === null) synth.onvoiceschanged = loadVoices;
    }
    const problem = diagnose();
    if (problem) {
      const btn = J.$('#micBtn');
      if (btn) {
        btn.disabled = true;
        btn.title = problem.title + ' — ' + problem.detail;
        btn.style.opacity = '.35';
      }
      J.log(problem.title, 'crit', 'sys');
      J.log(problem.detail, 'warn', 'sys');
      J.emit('mic-problem', problem);
    } else {
      permissionState().then(function (state) {
        if (state === 'granted' && J.settings.autoListen !== false) {
          /* Permission already granted, so this needs no click. Without it the
             wake word is a lie: he cannot hear "Hey Jarvis" until you have
             already walked over and tapped the microphone. */
          startListening();
        }
        if (state === 'denied') {
          J.emit('mic-problem', {
            code: 'denied',
            title: 'Microphone blocked for this site',
            detail: 'Click the padlock in the address bar, set Microphone to Allow, '
                  + 'then reload the page.'
          });
          J.log('Microphone permission is blocked for this site', 'crit', 'sys');
        } else {
          J.log('Voice input ready' + (state === 'granted' ? ' — permission already granted' : ''), 'ok', 'sys');
        }
      });
    }

    if (!synth) J.log('Speech synthesis unavailable — replies will be text only', 'warn', 'sys');
  }

  /* Rank the installed voices by how close they are to the character: British,
     male, and neural if the browser offers one. Scored rather than matched by
     name because the available set differs wildly between machines and
     browsers — Edge exposes online neural voices that Chrome does not. */
  function rankVoices() {
    const voices = synth ? synth.getVoices() : [];
    const MALE = /(george|ryan|thomas|daniel|oliver|arthur|james|guy|david|mark|brian|alfie|elliot|male)/i;
    const FEMALE = /(hazel|sonia|libby|maisie|zira|female|susan|linda|catherine|amy|emma)/i;

    return voices.map(v => {
      let score = 0;
      const n = v.name;
      if (/en[-_]GB/i.test(v.lang)) score += 60;          // British above all
      else if (/en[-_](AU|IE|IN|ZA)/i.test(v.lang)) score += 22;
      else if (/^en/i.test(v.lang)) score += 8;
      if (/natural|neural|online/i.test(n)) score += 30;   // far better synthesis
      if (MALE.test(n)) score += 25;
      if (FEMALE.test(n)) score -= 30;
      if (/desktop/i.test(n)) score -= 8;                  // the old SAPI set
      return { voice: v, score: score };
    }).sort((a, b) => b.score - a.score);
  }

  /* Pick the best available and tune the delivery. Returns what it found so
     the interface can be honest when nothing British is installed. */
  function useJarvisVoice() {
    const ranked = rankVoices();
    if (!ranked.length) return { ok: false, reason: 'no voices available in this browser' };

    const best = ranked[0];
    J.set({ voiceURI: best.voice.voiceURI, rate: 0.96, pitch: 0.85 });

    const british = /en[-_]GB/i.test(best.voice.lang);
    const neural = /natural|neural|online/i.test(best.voice.name);
    return {
      ok: true,
      name: best.voice.name,
      lang: best.voice.lang,
      british: british,
      neural: neural,
      reason: british
        ? (neural ? 'British and neural — as close as the browser gets'
                  : 'British, but not a neural voice')
        : 'no British voice is installed, so this is the nearest available'
    };
  }

  J.voice = {
    init, supported, diagnose, permissionState, rankVoices, useJarvisVoice,
    isAwake, wakeUp, goToSleep, touch, listDevices, switchDevice,
    isAwakeAlias: isAwake,
    heard: handleFinal,          // the recogniser's path, exposed so it can be tested
    expect,                      // a question on screen its answer may reply to
    startListening, stopListening, toggle,
    isListening: () => wantListening,
    isSpeaking:  () => speaking,
    say, feed, flush, shutUp,
    getVoices: () => voices
  };

})(window.J);
