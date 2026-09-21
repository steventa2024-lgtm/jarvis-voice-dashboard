/* ==========================================================================
   face.js — the grid face

   A wireframe head projected in real 3D. The silhouette is not an ellipsoid
   with a taper bolted on — that reads as an egg no matter how it is tuned.
   Instead the head is a stack of rings whose width and depth come from a head
   profile (PROFILE below), resampled through a Catmull-Rom spline into a
   lookup table at boot. Jaw, cheekbone, temple and parietal are control
   points, so the outline is a skull by construction.

   On top of that: a flattened face plane, a brow ridge, recessed eye sockets,
   a nose and a mouth, all as gaussian bands along the height. Eyes blink, the
   head drifts and looks around, and the mouth is driven by the audio spectrum
   — from a real AnalyserNode when the mic is open.

   Two masks share all of that geometry:
     · grid  — the clean HUD face, coloured from the interface accent
     · joker — bone-white mesh, green eyes and hair, and a carved grin running
               up onto the cheeks. The grin replaces the mouth as the thing the
               voice drives, so speech opens the smile.

   Cost control: every mesh line lands in one of four depth buckets and each
   bucket is stroked once, so the whole head costs about a dozen stroke calls a
   frame rather than a thousand. There is no shadowBlur anywhere; glow is a
   wide faint pass under a thin bright one, which is far cheaper and reads the
   same from across a room.
   ========================================================================== */

(function (J) {

  const canvas = J.$('#face');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  /* ============================================================ proportions */

  /* height (0 = base of chin, 1 = crown), half-width, half-depth */
  const PROFILE = [
    [0.00, 0.12, 0.18],   // underside of the chin
    [0.06, 0.32, 0.52],   // chin
    [0.15, 0.50, 0.72],   // jaw corner
    [0.26, 0.62, 0.86],   // lower cheek
    [0.40, 0.71, 0.94],   // cheekbone
    [0.55, 0.77, 0.98],   // temple, eye line
    [0.70, 0.78, 0.97],   // widest point of the skull
    [0.85, 0.68, 0.85],   // parietal
    [0.94, 0.50, 0.63],
    [1.00, 0.18, 0.24]    // crown
  ];

  const Y_CHIN = -1.06, Y_CROWN = 1.00;

  const EYE_H   = 0.505, EYE_TH = 0.53;
  const BROW_H  = 0.575;
  const NOSE_H  = 0.305;
  const MOUTH_H = 0.175;

  /* the carved grin: how far around the head it reaches, and how far its
     corners ride up the cheeks */
  const GRIN_TH   = 1.16;    // reaches almost to the silhouette edge
  const GRIN_LIFT = 0.205;   // corners climb to cheekbone height
  const GRIN_N    = 48;

  /* ------------------------------------------------------------- topology */

  const MER     = 18;      // meridians (vertical lines)
  const MER_SEG = 32;
  const PAR     = 16;      // parallels (horizontal rings)
  const PAR_SEG = 44;
  const BARS    = 34;      // mouth / grin resolution
  const TIERS   = 4;       // depth buckets
  const CULL    = -0.60;
  const DIST    = 3.4;     // perspective camera distance
  const STRANDS = 26;      // hair

  /* ================================================================= masks */

  const MASKS = {
    reactor: {
      accentDriven: true,
      reactor: true,
      hair: false, grin: false, smear: false
    },
    grid: {
      accentDriven: true,
      hair: false, grin: false, smear: false
    },
    joker: {
      accentDriven: false,
      hair: true, grin: true, smear: true,
      line:   [226, 232, 224],   // bone-white mesh
      iris:   [128, 232,  96],   // sickly green
      mouth:  [214,  32,  48],   // the grin
      strand: [ 92, 198,  80]    // hair
    }
  };

  let maskName = 'grid';
  let mask = MASKS.grid;

  /* ----------------------------------------------------------------- state */

  let W = 0, H = 0, cx = 0, cy = 0, R = 1;
  let t = 0, raf = null;
  let state = 'idle';                 // idle | listening | thinking | speaking

  let analyser = null, freq = null;
  let level = 0;
  const bars = new Float32Array(BARS);

  let yaw = 0, pitch = 0, yawTo = 0, pitchTo = 0, lookIn = 60;
  let cosY = 1, sinY = 0, cosP = 1, sinP = 0;

  let blink = 0, blinkPhase = -1, blinkIn = 120;
  let scan = 0;
  let intensity = 0.55;

  /* Fires once when the wake phrase lands, and decays. There is otherwise no
     way to tell he heard you until he starts speaking, which is a long silence
     to sit through wondering. */
  let wakeFlash = 0;

  /* Rises while he is thinking and collapses the moment text arrives. Used to
     slow the assembly down and dim it, so the snap back to full brightness is
     the visible signal that the answer has started. */
  let ponder = 0;

  /* scratch — allocated once */
  const p3 = [0, 0, 0];
  const q3 = [0, 0, 0];
  let tiers = [];
  const silL = new Float32Array(PAR * 2);
  const silR = new Float32Array(PAR * 2);
  let silN = 0;
  const grinX = new Float32Array(GRIN_N);
  const grinY = new Float32Array(GRIN_N);
  const topX  = new Float32Array(GRIN_N);
  const topY  = new Float32Array(GRIN_N);
  const botX  = new Float32Array(GRIN_N);
  const botY  = new Float32Array(GRIN_N);
  let grinVisible = false;

  /* ======================================================== profile spline */

  const LUT = 192;
  const lutW = new Float32Array(LUT);
  const lutD = new Float32Array(LUT);

  function catmull(p0, p1, p2, p3_, u) {
    const u2 = u * u, u3 = u2 * u;
    return 0.5 * (2 * p1
      + (-p0 + p2) * u
      + (2 * p0 - 5 * p1 + 4 * p2 - p3_) * u2
      + (-p0 + 3 * p1 - 3 * p2 + p3_) * u3);
  }

  /* Walk the spline finely, then rebin by height so runtime lookups are a flat
     array index instead of a search. */
  function buildProfile() {
    const n = PROFILE.length;
    const STEPS = 900;
    const hs = new Float32Array(STEPS + 1);
    const ws = new Float32Array(STEPS + 1);
    const ds = new Float32Array(STEPS + 1);

    for (let s = 0; s <= STEPS; s++) {
      const u = (s / STEPS) * (n - 1);
      const i = Math.min(n - 2, Math.floor(u));
      const f = u - i;
      const a = PROFILE[Math.max(0, i - 1)];
      const b = PROFILE[i];
      const c = PROFILE[i + 1];
      const d = PROFILE[Math.min(n - 1, i + 2)];
      hs[s] = catmull(a[0], b[0], c[0], d[0], f);
      ws[s] = catmull(a[1], b[1], c[1], d[1], f);
      ds[s] = catmull(a[2], b[2], c[2], d[2], f);
    }

    let j = 0;
    for (let k = 0; k < LUT; k++) {
      const h = k / (LUT - 1);
      while (j < STEPS - 1 && hs[j + 1] < h) j++;
      const span = hs[j + 1] - hs[j];
      const f = span > 1e-6 ? J.clamp((h - hs[j]) / span, 0, 1) : 0;
      lutW[k] = ws[j] + (ws[j + 1] - ws[j]) * f;
      lutD[k] = ds[j] + (ds[j + 1] - ds[j]) * f;
    }
  }

  function lookup(table, h) {
    const f = J.clamp(h, 0, 1) * (LUT - 1);
    const i = Math.min(LUT - 2, f | 0);
    return table[i] + (table[i + 1] - table[i]) * (f - i);
  }

  /* =============================================================== surface */

  /* (theta around the head, h up the head) → a point on the surface.
     theta 0 faces the viewer; ±PI is the back of the skull. */
  function surface(theta, h, out) {
    const st = Math.sin(theta), ctv = Math.cos(theta);
    const front = ctv > 0 ? ctv : 0;

    const w = lookup(lutW, h);
    // the face plane is flatter than the back of the skull
    const d = lookup(lutD, h) * (1 - 0.20 * front);

    let x = w * st;
    let z = d * ctv;
    const y = Y_CHIN + h * (Y_CROWN - Y_CHIN);

    if (front > 0.001) {
      const band = (c, sd) => { const k = (h - c) / sd; return Math.exp(-k * k); };
      z += front * 0.055 * band(BROW_H,  0.045);                              // brow ridge
      z -= front * 0.065 * band(EYE_H,   0.042);                              // eye sockets
      z += front * 0.150 * band(NOSE_H,  0.080) * Math.exp(-(x * x) / 0.012); // nose
      z -= front * 0.040 * band(MOUTH_H, 0.030);                              // mouth
      z += front * 0.035 * band(0.075,   0.045);                              // chin
    }

    out[0] = x; out[1] = y; out[2] = z;
  }

  /* Rotate by yaw/pitch and project in place. Returns the perspective factor so
     2D features drawn on top can scale with depth. */
  function project(out) {
    const x = out[0], y = out[1], z = out[2];

    const x1 =  x * cosY + z * sinY;
    const z1 = -x * sinY + z * cosY;
    const y2 =  y * cosP - z1 * sinP;
    const z2 =  y * sinP + z1 * cosP;

    const k = DIST / (DIST - z2);
    out[0] = cx + x1 * R * k;
    out[1] = cy - y2 * R * k;
    out[2] = z2;
    return k;
  }

  function tierOf(z) {
    const n = (z + 0.7) / 1.7;
    return J.clamp(Math.floor(n * TIERS), 0, TIERS - 1) | 0;
  }

  function resize() {
    const box = J.fitCanvas(canvas);
    W = box.w; H = box.h;
    ctx.setTransform(box.s, 0, 0, box.s, 0, 0);
    cx = W / 2;
    cy = H / 2;
    R  = Math.min(W, H) * 0.28;
  }

  /* ================================================================= audio */

  function attachAnalyser(node) {
    analyser = node;
    freq = node ? new Uint8Array(node.frequencyBinCount) : null;
  }

  function sampleAudio() {
    if (!analyser || !freq) return null;
    analyser.getByteFrequencyData(freq);
    const usable = Math.floor(freq.length * 0.42);   // voice energy sits low
    let sum = 0;
    for (let i = 0; i < BARS; i++) {
      const v = freq[Math.floor((i / BARS) * usable)] / 255;
      sum += v;
      bars[i] += (v - bars[i]) * 0.45;
    }
    return sum / BARS;
  }

  /* Speech synthesis exposes no output stream, so a speaking mouth has to be
     driven synthetically. Two detuned rates plus a per-band phase offset read
     as syllables rather than as one pulsing sine. */
  function synth() {
    if (state === 'speaking') {
      const a = 0.40 + 0.28 * Math.sin(t * 0.21) + 0.16 * Math.sin(t * 0.57 + 1.3);
      for (let i = 0; i < BARS; i++) {
        const shape = 0.35 + 0.65 * Math.abs(Math.sin(i * 0.38 + t * 0.16));
        bars[i] += (a * shape - bars[i]) * 0.34;
      }
      return a;
    }
    if (state === 'thinking') {
      const a = 0.17 + 0.06 * Math.sin(t * 0.10);
      for (let i = 0; i < BARS; i++) bars[i] += (a * 0.45 - bars[i]) * 0.10;
      return a;
    }
    const a = state === 'listening' ? 0.13 : 0.06;
    for (let i = 0; i < BARS; i++) bars[i] += (a * 0.35 - bars[i]) * 0.07;
    return a;
  }

  /* ================================================================ colour */

  let accentRGB = [53, 214, 255];

  function refreshMask() {
    accentRGB = J.hexToRgb(J.settings.accent);
    const want = MASKS[J.settings.mask] ? J.settings.mask : 'grid';
    maskName = want;
    mask = MASKS[want];
    canvas.dataset.mask = want;
  }

  /* Each channel falls back to the accent, so the grid mask needs no palette of
     its own and the joker mask overrides only what it cares about. */
  function chan(name) { return mask[name] || accentRGB; }

  function rgba(c, a) { return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a + ')'; }

  const line  = a => rgba(chan('line'), a);
  const iris  = a => rgba(chan('iris'), a);
  const lips  = a => rgba(chan('mouth'), a);
  const hairC = a => rgba(chan('strand'), a);

  /* a brighter relative of a channel, for highlights */
  function bright(name, a) {
    const c = chan(name);
    return 'rgba(' + Math.min(255, c[0] + 90) + ',' + Math.min(255, c[1] + 40) + ','
         + Math.min(255, c[2] + 60) + ',' + a + ')';
  }

  /* fixed pseudo-random, so hair strands do not jitter frame to frame */
  function hash(i) {
    const x = Math.sin(i * 127.1 + 3.7) * 43758.5453;
    return x - Math.floor(x);
  }

  /* =============================================================== drawing */

  function drawMesh() {
    tiers = [new Path2D(), new Path2D(), new Path2D(), new Path2D()];
    silN = 0;

    /* ---- parallels, and the silhouette they imply ---- */
    for (let j = 0; j < PAR; j++) {
      const h = 1 - j / (PAR - 1);

      let prev = -1, open = false;
      let minX = Infinity, maxX = -Infinity, minY = 0, maxY = 0;

      for (let i = 0; i <= PAR_SEG; i++) {
        const th = -Math.PI + (i / PAR_SEG) * Math.PI * 2;
        surface(th, h, p3);
        project(p3);

        if (p3[2] < CULL) { open = false; prev = -1; continue; }

        const tier = tierOf(p3[2]);
        const path = tiers[tier];
        if (!open || tier !== prev) path.moveTo(p3[0], p3[1]);
        else path.lineTo(p3[0], p3[1]);
        open = true; prev = tier;

        if (p3[0] < minX) { minX = p3[0]; minY = p3[1]; }
        if (p3[0] > maxX) { maxX = p3[0]; maxY = p3[1]; }
      }

      if (minX !== Infinity) {
        silL[silN * 2] = minX; silL[silN * 2 + 1] = minY;
        silR[silN * 2] = maxX; silR[silN * 2 + 1] = maxY;
        silN++;
      }
    }

    /* ---- meridians ---- */
    for (let i = 0; i < MER; i++) {
      const th = -Math.PI + (i / MER) * Math.PI * 2;
      let prev = -1, open = false;

      for (let j = 0; j <= MER_SEG; j++) {
        const h = 1 - j / MER_SEG;
        surface(th, h, p3);
        project(p3);

        if (p3[2] < CULL) { open = false; prev = -1; continue; }

        const tier = tierOf(p3[2]);
        const path = tiers[tier];
        if (!open || tier !== prev) path.moveTo(p3[0], p3[1]);
        else path.lineTo(p3[0], p3[1]);
        open = true; prev = tier;
      }
    }

    /* ---- one stroke per depth bucket ---- */
    const base = [0.06, 0.13, 0.26, 0.46];
    ctx.lineCap = 'round';
    for (let i = 0; i < TIERS; i++) {
      ctx.strokeStyle = line(base[i] * intensity * 1.8);
      ctx.lineWidth = 0.7 + i * 0.22;
      ctx.stroke(tiers[i]);
    }
    // a single wide faint pass over the front bucket, in place of a blur
    ctx.strokeStyle = line(0.05 * intensity);
    ctx.lineWidth = 4;
    ctx.stroke(tiers[TIERS - 1]);
  }

  function drawSilhouette() {
    if (silN < 2) return;
    ctx.beginPath();
    ctx.moveTo(silR[0], silR[1]);
    for (let i = 1; i < silN; i++) ctx.lineTo(silR[i * 2], silR[i * 2 + 1]);
    for (let i = silN - 1; i >= 0; i--) ctx.lineTo(silL[i * 2], silL[i * 2 + 1]);
    ctx.closePath();

    ctx.strokeStyle = line(0.09 * intensity);
    ctx.lineWidth = 6;
    ctx.stroke();
    ctx.strokeStyle = line(0.60 * intensity + level * 0.25);
    ctx.lineWidth = 1.4;
    ctx.stroke();
  }

  /* Sweep line clipped to the head by interpolating the silhouette, rather than
     clipping the canvas or compositing an offscreen buffer. */
  function drawScan() {
    if (J.reducedMotion || silN < 2) return;

    scan = (scan + (state === 'thinking' ? 0.019 : 0.0055)) % 1.4;
    if (scan > 1) return;                                   // pause between passes

    const f = scan * (silN - 1);
    const i = Math.min(silN - 2, f | 0);
    const m = f - i;

    const lx = silL[i * 2] + (silL[(i + 1) * 2] - silL[i * 2]) * m;
    const rx = silR[i * 2] + (silR[(i + 1) * 2] - silR[i * 2]) * m;
    const ly = silL[i * 2 + 1] + (silL[(i + 1) * 2 + 1] - silL[i * 2 + 1]) * m;

    const fade = Math.sin(scan * Math.PI);
    const grad = ctx.createLinearGradient(lx, ly, rx, ly);
    grad.addColorStop(0, line(0));
    grad.addColorStop(0.5, bright('line', 0.6 * fade * intensity));
    grad.addColorStop(1, line(0));

    ctx.strokeStyle = grad;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(lx, ly);
    ctx.lineTo(rx, ly);
    ctx.stroke();
  }

  /* ---- hair ----

     Strands root along the hairline and sweep up and outward as cubics, so the
     silhouette is a mane rather than a row of spikes. Two passes at different
     lengths give it depth without a second geometry pass: the long dim layer
     reads as volume behind the bright short one. */
  function drawHairLayer(scale, alpha, width, seed) {
    ctx.beginPath();
    for (let i = 0; i < STRANDS; i++) {
      const f = i / (STRANDS - 1);
      const th = -1.50 + f * 3.00;
      const baseH = 0.70 + hash(i + seed) * 0.22;
      surface(th, baseH, p3);
      project(p3);
      if (p3[2] < -0.30) continue;                    // round the back, hidden

      const bx = p3[0], by = p3[1];
      const off = bx - cx;
      // strands lean away from the centre line, hardest at the temples
      const lean = off * (0.85 + hash(i + 11) * 0.5);
      const len = R * scale * (0.72 + hash(i + seed + 40) * 0.62);
      const sway = J.reducedMotion ? 0 : Math.sin(t * 0.010 + i * 1.7) * R * 0.030;

      ctx.moveTo(bx, by);
      ctx.bezierCurveTo(
        bx + lean * 0.20, by - len * 0.42,
        bx + lean * 0.85, by - len * 0.80,
        bx + lean * 1.25 + sway, by - len
      );
    }

    ctx.lineCap = 'round';
    ctx.strokeStyle = hairC(alpha * 0.22 * intensity);
    ctx.lineWidth = width * 3.2;
    ctx.stroke();
    ctx.strokeStyle = hairC(alpha * intensity);
    ctx.lineWidth = width;
    ctx.stroke();
  }

  function drawHair() {
    drawHairLayer(0.62, 0.34, 2.4, 90);    // long, dim — volume
    drawHairLayer(0.40, 0.70, 1.6, 0);     // short, bright — definition
  }

  /* ---- dark paint smeared around the eye socket ---- */
  function drawSmear(side) {
    surface(EYE_TH * side, EYE_H, p3);
    const k = project(p3);
    if (p3[2] < -0.10) return;

    const sx = p3[0], sy = p3[1];
    const w = 0.175 * R * k;
    const h = 0.260 * R * k;
    const out = side;                     // the smear trails toward the temple

    /* Against a black background dark paint cannot read as dark — what
       registers is the mesh disappearing underneath it. So the fill is
       near-opaque at the centre.

       Two things matter here. The gradient must reach zero alpha *before* the
       furthest corner of the shape, or canvas clamps to the last stop and the
       polygon shows up as a flat translucent quad hanging off the side of the
       head. And there is no rim stroke, for the same reason: paint smeared on
       skin has no outline. */
    const far = w * 2.6;
    ctx.beginPath();
    ctx.moveTo(sx - w * 1.05, sy - h * 0.50);
    ctx.lineTo(sx + w * (0.80 + out * 0.55), sy - h * 0.95);
    ctx.lineTo(sx + w * (0.90 + out * 0.80), sy + h * 0.65);
    ctx.lineTo(sx - w * 0.85, sy + h * 0.95);
    ctx.closePath();

    const g = ctx.createRadialGradient(sx, sy, w * 0.1, sx, sy, far);
    g.addColorStop(0.00, 'rgba(3,9,3,0.97)');
    g.addColorStop(0.42, 'rgba(4,12,4,0.86)');
    g.addColorStop(0.72, 'rgba(6,18,6,0.34)');
    g.addColorStop(1.00, 'rgba(6,18,6,0)');
    ctx.fillStyle = g;
    ctx.fill();
  }

  /* Brow lines. Cheap, and they do more for legibility as a face than any
     amount of extra mesh density. The joker mask arches them. */
  function drawBrow(side) {
    const arch = mask.grin ? 0.055 : 0;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i <= 5; i++) {
      const f = i / 5;
      const th = (0.14 + f * 0.52) * side;
      surface(th, BROW_H + arch * Math.sin(f * Math.PI), p3);
      project(p3);
      if (p3[2] < -0.05) { started = false; continue; }
      if (!started) { ctx.moveTo(p3[0], p3[1]); started = true; }
      else ctx.lineTo(p3[0], p3[1]);
    }
    const a = state === 'thinking' ? 1.0 : 0.78;
    ctx.strokeStyle = line(a * intensity);
    ctx.lineWidth = mask.grin ? 2.1 : 1.6;
    ctx.stroke();
  }

  function drawNose() {
    surface(0, NOSE_H + 0.145, p3);      // bridge, between the brows
    project(p3);
    surface(0, NOSE_H - 0.02, q3);       // tip
    project(q3);
    if (p3[2] < -0.05 && q3[2] < -0.05) return;

    ctx.beginPath();
    ctx.moveTo(p3[0], p3[1]);
    ctx.lineTo(q3[0], q3[1]);

    const tipX = q3[0], tipY = q3[1];
    for (const side of [-1, 1]) {
      surface(0.20 * side, NOSE_H - 0.055, p3);
      project(p3);
      ctx.moveTo(tipX, tipY);
      ctx.lineTo(p3[0], p3[1]);
    }

    ctx.strokeStyle = line(0.72 * intensity);
    ctx.lineWidth = 1.8;
    ctx.stroke();
  }

  function drawEye(side) {
    surface(EYE_TH * side, EYE_H, p3);
    const k = project(p3);
    if (p3[2] < -0.10) return;                              // rotated out of view

    const sx = p3[0], sy = p3[1];
    const w = 0.158 * R * k;
    const lid = 1 - blink;
    const h = w * 0.42 * lid;

    const open = state === 'listening' ? 1.14
               : state === 'thinking'  ? 0.76
               : state === 'speaking'  ? 1.02 : 0.94;

    /* lens: two arcs meeting at the corners */
    ctx.beginPath();
    ctx.moveTo(sx - w, sy);
    ctx.quadraticCurveTo(sx, sy - h * 1.55 * open, sx + w, sy);
    ctx.quadraticCurveTo(sx, sy + h * 1.25, sx - w, sy);
    ctx.closePath();

    ctx.fillStyle = mask.smear ? 'rgba(2,6,2,0.72)' : line(0.06 + level * 0.10);
    ctx.fill();
    ctx.strokeStyle = line(0.18 * intensity);
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.strokeStyle = line(0.85 * intensity);
    ctx.lineWidth = 1.3;
    ctx.stroke();

    if (blink > 0.7) return;                                // shut — no iris

    /* the iris drifts with the head turn, so the gaze looks intentional */
    const gaze = yaw * R * k * 0.07;
    const ir = w * 0.38 * (1 + level * 0.20) * lid;
    const g = ctx.createRadialGradient(sx + gaze, sy, ir * 0.12, sx + gaze, sy, ir);
    g.addColorStop(0, bright('iris', 0.95 * intensity));
    g.addColorStop(0.45, iris(0.70 * intensity));
    g.addColorStop(1, iris(0));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(sx + gaze, sy, ir, 0, Math.PI * 2);
    ctx.fill();
  }

  /* ---- the plain mouth ---- */
  function drawMouth() {
    surface(0, MOUTH_H, p3);
    const k = project(p3);
    if (p3[2] < -0.10) return;

    const sx = p3[0], sy = p3[1];
    const half = 0.21 * R * k;
    const step = (half * 2) / (BARS - 1);
    const live = state === 'speaking' || state === 'listening';
    const reach = 0.10 * R * k * (state === 'speaking' ? 1 : 0.30);

    /* resting line — the mouth exists even in silence */
    ctx.strokeStyle = line(0.78 * intensity);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(sx - half, sy);
    ctx.lineTo(sx + half, sy);
    ctx.stroke();

    if (!live) return;

    ctx.lineCap = 'round';
    ctx.beginPath();
    for (let i = 0; i < BARS; i++) {
      // bell envelope — the corners of a mouth move less than the middle
      const env = Math.sin((i / (BARS - 1)) * Math.PI);
      const v = J.clamp(bars[i], 0, 1) * env * reach;
      if (v < 0.5) continue;
      const x = sx - half + i * step;
      ctx.moveTo(x, sy - v);
      ctx.lineTo(x, sy + v * 0.7);
    }
    ctx.strokeStyle = line(0.13 * intensity);
    ctx.lineWidth = 4.5;
    ctx.stroke();
    ctx.strokeStyle = bright('line', 0.75 * intensity);
    ctx.lineWidth = 1.8;
    ctx.stroke();
  }

  /* ---- the carved grin ----

     Sampled along the surface with its height rising toward the corners, so it
     follows the cheeks in 3D instead of being pasted flat across the face. The
     voice opens it along the curve normal, which is what makes it read as a
     mouth rather than a painted line. */
  function buildGrin() {
    grinVisible = false;
    for (let i = 0; i < GRIN_N; i++) {
      const u = -1 + 2 * (i / (GRIN_N - 1));
      surface(u * GRIN_TH, MOUTH_H + GRIN_LIFT * u * u, p3);
      project(p3);
      grinX[i] = p3[0];
      grinY[i] = p3[1];
      if (p3[2] > -0.10) grinVisible = true;
    }
  }

  /* unit normal to the grin at sample i, in screen space */
  function grinNormal(i, out) {
    const a = Math.max(0, i - 1), b = Math.min(GRIN_N - 1, i + 1);
    const tx = grinX[b] - grinX[a], ty = grinY[b] - grinY[a];
    const len = Math.hypot(tx, ty) || 1;
    out[0] = -ty / len;
    out[1] =  tx / len;
  }

  function drawGrin() {
    buildGrin();
    if (!grinVisible) return;

    const n = [0, 0];
    const speaking = state === 'speaking';
    const reach = 0.155 * R * (speaking ? 1 : state === 'listening' ? 0.32 : 0);

    /* the cut itself */
    ctx.beginPath();
    ctx.moveTo(grinX[0], grinY[0]);
    for (let i = 1; i < GRIN_N; i++) ctx.lineTo(grinX[i], grinY[i]);
    ctx.lineCap = 'round';
    ctx.strokeStyle = 'rgba(20,2,4,0.85)';      // the cut itself, sunk in
    ctx.lineWidth = 7;
    ctx.stroke();
    ctx.strokeStyle = lips(0.22 * intensity);   // bloom around it
    ctx.lineWidth = 11;
    ctx.stroke();
    ctx.strokeStyle = lips(1.0 * intensity);
    ctx.lineWidth = 3.4;
    ctx.stroke();

    /* scar ticks crossing the ends of the cut */
    ctx.beginPath();
    for (let s = 0; s < 2; s++) {
      for (let m = 0; m < 3; m++) {
        const i = s === 0 ? 2 + m * 3 : GRIN_N - 3 - m * 3;
        grinNormal(i, n);
        const half = (7.0 - m * 1.4) * (R / 130);
        ctx.moveTo(grinX[i] - n[0] * half, grinY[i] - n[1] * half);
        ctx.lineTo(grinX[i] + n[0] * half, grinY[i] + n[1] * half);
      }
    }
    ctx.strokeStyle = lips(0.72 * intensity);
    ctx.lineWidth = 1.6;
    ctx.stroke();

    if (reach <= 0) return;

    /* the mouth opening: offset along the normal, driven by the spectrum */
    for (let i = 0; i < GRIN_N; i++) {
      const u = i / (GRIN_N - 1);
      const env = Math.sin(u * Math.PI);
      const band = bars[Math.min(BARS - 1, Math.floor(u * BARS))] || 0;
      const v = J.clamp(band, 0, 1) * env * reach;
      grinNormal(i, n);
      topX[i] = grinX[i] - n[0] * v;
      topY[i] = grinY[i] - n[1] * v;
      botX[i] = grinX[i] + n[0] * v * 0.72;
      botY[i] = grinY[i] + n[1] * v * 0.72;
    }

    ctx.beginPath();
    ctx.moveTo(topX[0], topY[0]);
    for (let i = 1; i < GRIN_N; i++) ctx.lineTo(topX[i], topY[i]);
    for (let i = GRIN_N - 1; i >= 0; i--) ctx.lineTo(botX[i], botY[i]);
    ctx.closePath();
    ctx.fillStyle = 'rgba(22,2,5,0.94)';
    ctx.fill();
    // the interior is dark on a dark page, so the lip edge does the work
    ctx.strokeStyle = lips(1.0 * intensity);
    ctx.lineWidth = 2.4;
    ctx.stroke();

    /* teeth — only once the mouth is genuinely open */
    if (!speaking) return;
    ctx.beginPath();
    for (let i = 3; i < GRIN_N - 3; i += 3) {
      if (Math.hypot(botX[i] - topX[i], botY[i] - topY[i]) < 4) continue;
      ctx.moveTo(topX[i], topY[i]);
      ctx.lineTo(botX[i], botY[i]);
    }
    ctx.strokeStyle = 'rgba(240,244,236,0.62)';
    ctx.lineWidth = 1.1;
    ctx.stroke();
  }

  /* ============================================================== reactor */

  /* The arc reactor: ten coil segments in a housing ring, a rotating inner
     assembly, and a hot core. It is not a face, so none of the head geometry
     above runs for this mask — it draws straight in polar coordinates about
     the centre.

     The thing that makes it feel alive rather than decorative is that the ten
     segments are wired to ten bands of the audio spectrum. When J.A.R.V.I.S.
     speaks, the ring lights up around him in the shape of his own voice. */
  const TAU = Math.PI * 2;
  const COILS = 10;

  function drawReactor() {
    /* Rotation carries the state. Thinking slows almost to a stop — a machine
       working on something hard, rather than idling. Listening speeds up. */
    const rate = state === 'thinking' ? 0.0035 * (1 - ponder * 0.72)
               : state === 'listening' ? 0.0035 + level * 0.010
               : 0.0035;
    const spin = J.reducedMotion ? 0 : t * rate;
    const R0 = R * 1.32;                       // housing
    const R1 = R0 * 0.76;                      // coil outer
    const R2 = R0 * 0.50;                      // coil inner
    const R3 = R0 * 0.36;                      // core well

    /* The core tracks your voice while listening, not only his while speaking.
       Thinking pulls it down so the return to full is unmistakable, and the
       wake flash overrides everything for a moment. */
    let hot = 0.55 + level * 0.75;
    if (state === 'listening') hot = 0.55 + level * 1.45;
    if (state === 'thinking') hot *= (1 - ponder * 0.45);
    hot += wakeFlash * 1.3;

    ctx.save();
    ctx.translate(cx, cy);

    function ring(r, a, w) {
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, TAU);
      ctx.strokeStyle = line(a * intensity);
      ctx.lineWidth = w;
      ctx.stroke();
    }

    /* ---- housing ---- */
    ring(R0, 0.10, 7);                          // bloom, in place of a blur
    ring(R0, 0.70, 2.0);
    ring(R0 * 0.90, 0.28, 1.0);
    ring(R2 * 0.92, 0.30, 1.0);

    /* ---- graduation ticks ---- */
    ctx.beginPath();
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * TAU;
      const ca = Math.cos(a), sa = Math.sin(a);
      const rOut = R0 * 0.885;
      const rIn = R0 * (i % 6 === 0 ? 0.80 : 0.85);
      ctx.moveTo(ca * rOut, sa * rOut);
      ctx.lineTo(ca * rIn, sa * rIn);
    }
    ctx.strokeStyle = line(0.40 * intensity);
    ctx.lineWidth = 1;
    ctx.stroke();

    /* ---- the ten coils, each riding a band of the spectrum ---- */
    for (let i = 0; i < COILS; i++) {
      const a0 = (i / COILS) * TAU + spin;
      const a1 = a0 + (TAU / COILS) * 0.74;

      const band = bars[Math.floor((i / COILS) * BARS)] || 0;
      const energy = 0.30 + J.clamp(band, 0, 1) * 0.70;

      ctx.beginPath();
      ctx.arc(0, 0, R1, a0, a1);
      ctx.arc(0, 0, R2, a1, a0, true);
      ctx.closePath();

      const g = ctx.createRadialGradient(0, 0, R2, 0, 0, R1);
      g.addColorStop(0, line(0.30 * energy * intensity));
      g.addColorStop(1, line(0.06 * energy * intensity));
      ctx.fillStyle = g;
      ctx.fill();

      ctx.strokeStyle = line((0.35 + 0.55 * energy) * intensity);
      ctx.lineWidth = 1.3;
      ctx.stroke();
    }

    /* ---- inner assembly, counter-rotating so the two rings read apart ---- */
    ctx.save();
    ctx.rotate(-spin * 1.6);
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = (i / 3) * TAU;
      ctx.moveTo(Math.cos(a) * R3 * 1.02, Math.sin(a) * R3 * 1.02);
      ctx.lineTo(Math.cos(a + TAU / 3) * R3 * 1.02, Math.sin(a + TAU / 3) * R3 * 1.02);
    }
    ctx.strokeStyle = line(0.55 * intensity);
    ctx.lineWidth = 1.6;
    ctx.stroke();
    ctx.restore();

    ring(R3, 0.60, 1.6);

    /* ---- the core ---- */
    const cr = R3 * 0.80 * (1 + level * 0.16);
    const core = ctx.createRadialGradient(0, 0, 0, 0, 0, cr);
    core.addColorStop(0.00, 'rgba(255,255,255,' + (0.92 * hot).toFixed(3) + ')');
    core.addColorStop(0.30, bright('line', 0.80 * hot));
    core.addColorStop(0.70, line(0.42 * hot));
    core.addColorStop(1.00, line(0));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(0, 0, cr, 0, TAU);
    ctx.fill();

    /* a wide, very faint halo so the whole assembly sits in its own light */
    const halo = ctx.createRadialGradient(0, 0, cr * 0.6, 0, 0, R0 * 1.5);
    halo.addColorStop(0, line(0.16 * hot));
    halo.addColorStop(1, line(0));
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(0, 0, R0 * 1.5, 0, TAU);
    ctx.fill();

    /* ---- the wake pulse: one ring, thrown outward and gone ---- */
    if (wakeFlash > 0.01) {
      const grow = 1 - wakeFlash;                 // 0 at the moment of waking
      const ring = R0 * (0.9 + grow * 0.9);
      ctx.beginPath();
      ctx.arc(0, 0, ring, 0, TAU);
      ctx.strokeStyle = bright('line', wakeFlash * 0.85);
      ctx.lineWidth = 1 + wakeFlash * 3;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(0, 0, ring, 0, TAU);
      ctx.strokeStyle = line(wakeFlash * 0.22);
      ctx.lineWidth = 10 * wakeFlash;
      ctx.stroke();
    }

    /* ---- spectrum spokes outside the housing, only when there is a voice ---- */
    const reach = state === 'speaking' ? R0 * 0.30
                : state === 'listening' ? R0 * 0.16 : 0;
    if (reach > 0) {
      ctx.beginPath();
      for (let i = 0; i < BARS; i++) {
        const a = (i / BARS) * TAU + spin * 0.4;
        const v = J.clamp(bars[i], 0, 1) * reach;
        if (v < 1) continue;
        const ca = Math.cos(a), sa = Math.sin(a);
        const r1 = R0 * 1.03;
        ctx.moveTo(ca * r1, sa * r1);
        ctx.lineTo(ca * (r1 + v), sa * (r1 + v));
      }
      ctx.lineCap = 'round';
      ctx.strokeStyle = line(0.14 * intensity);
      ctx.lineWidth = 5;
      ctx.stroke();
      ctx.strokeStyle = bright('line', 0.72 * intensity);
      ctx.lineWidth = 1.8;
      ctx.stroke();
    }

    ctx.restore();
  }

  /* ================================================================= frame */

  function updateBlink() {
    if (blinkPhase >= 0) {
      blinkPhase += 0.15;
      if (blinkPhase >= 1) {
        blinkPhase = -1;
        blink = 0;
        blinkIn = 130 + Math.random() * 300;
        return;
      }
      // shuts fast, opens slower — a symmetric blink looks mechanical
      blink = blinkPhase < 0.35 ? blinkPhase / 0.35 : 1 - (blinkPhase - 0.35) / 0.65;
      return;
    }
    blink = 0;
    if (--blinkIn <= 0 && !J.reducedMotion) blinkPhase = 0;
  }

  function motion() {
    /* Pick somewhere new to look every couple of seconds and ease toward it.
       Constant small motion is what separates "alive" from "screensaver". */
    if (!J.reducedMotion && --lookIn <= 0) {
      const range = state === 'thinking' ? 0.34 : 0.22;
      yawTo = (Math.random() - 0.5) * range;
      pitchTo = (Math.random() - 0.5) * range * 0.5;
      lookIn = 90 + Math.random() * 160;
    }
    yaw += (yawTo - yaw) * 0.015;
    pitch += (pitchTo - pitch) * 0.015;

    if (!J.reducedMotion) {
      yaw += Math.sin(t * 0.0075) * 0.0010;
      pitch += Math.sin(t * 0.0052) * 0.0007;
    }

    cosY = Math.cos(yaw);  sinY = Math.sin(yaw);
    cosP = Math.cos(pitch); sinP = Math.sin(pitch);

    updateBlink();

    const want = state === 'speaking'  ? 1.00
               : state === 'listening' ? 0.92
               : state === 'thinking'  ? 0.80 : 0.55;
    intensity += (want - intensity) * 0.05;

    // ponder swells slowly while thinking, and is cut instantly elsewhere
    const wantPonder = state === 'thinking' ? 1 : 0;
    ponder += (wantPonder - ponder) * (wantPonder ? 0.012 : 0.25);

    if (wakeFlash > 0) wakeFlash = Math.max(0, wakeFlash - 0.022);
  }

  function draw() {
    // A hidden or collapsed panel measures zero; drawing a negative radius
    // throws, and the throw lands before the next frame is scheduled — which
    // would kill the loop for good. Skip the frame instead.
    if (R < 2) return;

    ctx.clearRect(0, 0, W, H);

    const audio = sampleAudio();
    const raw = audio !== null ? audio : synth();
    level += (raw - level) * 0.22;

    motion();

    /* The reactor is not a head, so it short-circuits the entire face
       pipeline rather than sharing it. */
    if (mask.reactor) { drawReactor(); t++; return; }

    if (mask.hair) drawHair();          // behind the head
    drawMesh();
    drawSilhouette();
    drawScan();
    if (mask.smear) { drawSmear(-1); drawSmear(1); }
    drawBrow(-1);
    drawBrow(1);
    drawNose();
    drawEye(-1);
    drawEye(1);
    if (mask.grin) drawGrin();
    else drawMouth();

    t++;
  }

  function frame() { draw(); raf = requestAnimationFrame(frame); }
  function start() { if (raf === null) raf = requestAnimationFrame(frame); }
  function stop()  { if (raf !== null) { cancelAnimationFrame(raf); raf = null; } }

  function setState(next) {
    if (next === state) return;
    state = next;
    canvas.dataset.state = next;
    if (next === 'thinking' || next === 'listening') lookIn = Math.min(lookIn, 20);
  }

  /* ================================================================== wire */

  window.addEventListener('resize', resize);
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));
  J.on('settings', refreshMask);
  J.on('core-state', setState);
  J.on('wake-state', up => { if (up) flashWake(); });

  buildProfile();
  refreshMask();
  resize();
  start();

  function flashWake() { wakeFlash = 1; }

  J.face = {
    setState, attachAnalyser, flashWake,
    getState: () => state,
    getMask: () => maskName,
    masks: Object.keys(MASKS)
  };

})(window.J);
