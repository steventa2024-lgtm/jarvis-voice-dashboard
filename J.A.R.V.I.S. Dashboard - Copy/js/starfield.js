/* ==========================================================================
   starfield.js — parallax backdrop
   Cheap by construction: DPR capped, paused while the tab is hidden, and
   fully static under prefers-reduced-motion.
   ========================================================================== */

(function (J) {

  const canvas = J.$('#starfield');
  if (!canvas) return;

  const ctx = canvas.getContext('2d', { alpha: true });
  let stars = [];
  let W = 0, H = 0, S = 1;
  let raf = null;

  function resize() {
    const box = J.fitCanvas(canvas);
    W = box.w; H = box.h; S = box.s;
    ctx.setTransform(S, 0, 0, S, 0, 0);
    seed();
  }

  function seed() {
    const count = J.settings.density | 0;
    stars = [];
    for (let i = 0; i < count; i++) {
      const layer = i % 3;                      // 0 far … 2 near
      stars.push({
        x: Math.random() * W,
        y: Math.random() * H,
        r: 0.4 + layer * 0.35 + Math.random() * 0.3,
        v: (0.045 + layer * 0.05) * (0.6 + Math.random() * 0.8),
        a: 0.20 + layer * 0.16 + Math.random() * 0.22,
        tw: Math.random() * Math.PI * 2,
        tws: 0.008 + Math.random() * 0.02
      });
    }
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    for (let i = 0; i < stars.length; i++) {
      const s = stars[i];
      s.tw += s.tws;
      const alpha = s.a * (0.68 + 0.32 * Math.sin(s.tw));
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(190,225,255,' + alpha.toFixed(3) + ')';
      ctx.fill();

      s.y += s.v;
      if (s.y > H + 2) { s.y = -2; s.x = Math.random() * W; }
    }
  }

  function frame() {
    draw();
    raf = requestAnimationFrame(frame);
  }

  function start() {
    if (raf !== null) return;
    if (J.reducedMotion) { draw(); return; }   // one static render, no loop
    raf = requestAnimationFrame(frame);
  }

  function stop() {
    if (raf === null) return;
    cancelAnimationFrame(raf);
    raf = null;
  }

  window.addEventListener('resize', () => { resize(); if (J.reducedMotion) draw(); });
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));
  J.on('settings', () => { seed(); if (J.reducedMotion) draw(); });

  resize();
  start();

})(window.J);
