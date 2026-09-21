/* ==========================================================================
   orb.js — the reactive core
   A layered canvas HUD: tick ring, radial spectrum, sweep arcs, glowing core.
   When the mic is live the spectrum is driven by a real AnalyserNode, so the
   orb tracks the user's voice instead of animating on a timer.
   ========================================================================== */

(function (J) {

  const canvas = J.$('#orb');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const BARS = 72;

  let W = 0, H = 0, cx = 0, cy = 0, R = 0;
  let t = 0;                       // master clock, advances per frame
  let state = 'idle';              // idle | listening | thinking | speaking
  let analyser = null;
  let freq = null;
  let level = 0;                   // smoothed overall amplitude, 0..1
  let bars = new Float32Array(BARS);
  let raf = null;

  function resize() {
    const box = J.fitCanvas(canvas);
    W = box.w; H = box.h;
    ctx.setTransform(box.s, 0, 0, box.s, 0, 0);
    cx = W / 2; cy = H / 2;
    R = Math.min(W, H) / 2 - 6;
  }

  /* ------------------------------------------------------------ audio feed */

  function attachAnalyser(node) {
    analyser = node;
    freq = node ? new Uint8Array(node.frequencyBinCount) : null;
    if (J.face) J.face.attachAnalyser(node);   // the face rides the same feed
  }

  function sampleAudio() {
    if (!analyser || !freq) return null;
    analyser.getByteFrequencyData(freq);
    // Voice energy lives low in the spectrum; sample the useful bins only.
    const usable = Math.floor(freq.length * 0.45);
    let sum = 0;
    for (let i = 0; i < BARS; i++) {
      const idx = Math.floor((i / BARS) * usable);
      const v = freq[idx] / 255;
      sum += v;
      bars[i] += (v - bars[i]) * 0.42;
    }
    return sum / BARS;
  }

  /* Fallback envelope when there is no analyser: a breathing shape that is
     obviously synthetic rather than pretending to be audio. */
  function synthEnvelope() {
    if (state === 'speaking') {
      const a = 0.34 + 0.3 * Math.sin(t * 0.19) + 0.16 * Math.sin(t * 0.51 + 1.1);
      for (let i = 0; i < BARS; i++) {
        const target = a * (0.45 + 0.55 * Math.abs(Math.sin(i * 0.42 + t * 0.13)));
        bars[i] += (target - bars[i]) * 0.3;
      }
      return a;
    }
    if (state === 'thinking') {
      const a = 0.2 + 0.08 * Math.sin(t * 0.11);
      for (let i = 0; i < BARS; i++) {
        const target = a * (0.3 + 0.7 * Math.abs(Math.sin(i * 0.31 - t * 0.07)));
        bars[i] += (target - bars[i]) * 0.16;
      }
      return a;
    }
    const a = 0.10 + 0.045 * Math.sin(t * 0.045);
    for (let i = 0; i < BARS; i++) bars[i] += (a - bars[i]) * 0.06;
    return a;
  }

  /* Each state gets its own weight and colour, because "is he listening or
     just on?" should be answerable from across the room without reading the
     word underneath. The difference used to be a few percent of alpha, which
     is invisible at four metres.

       idle      dim, slow, cool
       listening accent, and it MOVES with your voice
       thinking  hot, fast, restless
       speaking  warm, pulsing with the envelope */
  const STATE_STYLE = {
    idle:      { hue: null, gain: 0.25, speed: 0.6,  glow: 0.0 },
    listening: { hue: null, gain: 1.00, speed: 1.0,  glow: 0.55 },
    thinking:  { hue: 42,   gain: 0.55, speed: 2.6,  glow: 0.40 },
    speaking:  { hue: 152,  gain: 0.90, speed: 1.3,  glow: 0.65 }
  };

  function styleNow() { return STATE_STYLE[state] || STATE_STYLE.idle; }

  /* --------------------------------------------------------------- drawing */

  function accent(alpha) {
    const [r, g, b] = J.hexToRgb(J.settings.accent);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  function ring(radius, width, alpha, from, to) {
    ctx.beginPath();
    ctx.arc(cx, cy, radius, from === undefined ? 0 : from, to === undefined ? Math.PI * 2 : to);
    ctx.strokeStyle = accent(alpha);
    ctx.lineWidth = width;
    ctx.stroke();
  }

  function tickRing(radius, count, len, alpha, rot, everyN) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rot);
    ctx.strokeStyle = accent(alpha);
    for (let i = 0; i < count; i++) {
      const long = everyN && i % everyN === 0;
      const l = long ? len * 2.1 : len;
      ctx.lineWidth = long ? 1.6 : 1;
      ctx.globalAlpha = long ? 1 : 0.55;
      const a = (i / count) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * radius, Math.sin(a) * radius);
      ctx.lineTo(Math.cos(a) * (radius + l), Math.sin(a) * (radius + l));
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function spectrum(inner, maxLen) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.lineCap = 'round';
    for (let i = 0; i < BARS; i++) {
      const v = J.clamp(bars[i], 0, 1);
      const a = (i / BARS) * Math.PI * 2 - Math.PI / 2;
      const len = 2 + v * maxLen;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * inner, Math.sin(a) * inner);
      ctx.lineTo(Math.cos(a) * (inner + len), Math.sin(a) * (inner + len));
      ctx.strokeStyle = accent(0.25 + v * 0.6);
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    ctx.restore();
  }


  function draw() {
    if (R < 2) return;              // zero-sized canvas — see face.js

    ctx.clearRect(0, 0, W, H);

    const audio = sampleAudio();
    const raw = audio !== null ? audio : synthEnvelope();
    level += (raw - level) * 0.2;

    const busy = state === 'listening' || state === 'speaking' || state === 'thinking';
    const spin = J.reducedMotion ? 0 : t;

    // outer tick ring — slow counter-rotation
    tickRing(R * 0.96, 60, 5, 0.30, -spin * 0.0022, 5);

    // structural rings — kept clear of the face silhouette
    ring(R * 0.86, 1, 0.16);
    ring(R * 0.98, 1, 0.08);

    // sweep arcs — the "working" indicator
    const st0 = styleNow();
    const sweepAlpha = Math.min(1, 0.3 + st0.gain * 0.5 + (busy ? 0.15 : 0));
    const sweepSpeed = 0.017 * st0.speed;
    ring(R * 0.90, 2, sweepAlpha, spin * sweepSpeed, spin * sweepSpeed + 1.15);
    ring(R * 0.90, 2, sweepAlpha * 0.7, spin * sweepSpeed + Math.PI, spin * sweepSpeed + Math.PI + 0.62);
    ring(R * 0.72, 1.5, sweepAlpha * 0.55, -spin * sweepSpeed * 1.7, -spin * sweepSpeed * 1.7 + 2.1);

    // voice spectrum — radiates outward so the grid face stays legible
    spectrum(R * 0.74, R * 0.17);

    /* The live ring. Its radius follows the microphone, so when he is
       listening the reactor visibly breathes with the room rather than
       animating on a timer that ignores you. */
    const st = styleNow();
    if (st.gain > 0.3) {
      const swell = level * st.gain;
      ring(R * (0.62 + swell * 0.16), 1 + swell * 2.6,
           Math.min(1, 0.22 + swell * 1.5));
      if (st.glow && swell > 0.04) {
        ctx.save();
        ctx.globalAlpha = Math.min(0.5, swell * st.glow * 1.6);
        ctx.shadowBlur = 24 * swell;
        ctx.shadowColor = accent(1);
        ring(R * (0.62 + swell * 0.16), 1.5, 0.5);
        ctx.restore();
      }
    }

    t += 1;
  }

  function frame() {
    draw();
    raf = requestAnimationFrame(frame);
  }

  function start() { if (raf === null) raf = requestAnimationFrame(frame); }
  function stop()  { if (raf !== null) { cancelAnimationFrame(raf); raf = null; } }

  function setState(next) {
    state = next;
    canvas.dataset.state = next;
    J.emit("core-state", next);                // face.js listens for this
  }

  window.addEventListener('resize', resize);
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));

  resize();
  start();

  J.orb = { setState, attachAnalyser, getState: () => state };

})(window.J);
