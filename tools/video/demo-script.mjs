/**
 * The 60-second storyboard.
 *
 * Each scene fires at a fixed timestamp. Every interaction goes through the
 * `maybe*` helpers, so a selector that doesn't exist yet degrades to "keep
 * filming" instead of aborting the take -- you can wire selectors in one at a
 * time and re-record after each.
 *
 * >> EDIT THE `sel` MAP AND THE SCENE ACTIONS TO MATCH THE REAL DASHBOARD. <<
 */

/** Selectors for the real app. Fill these in; empty ones are skipped. */
const sel = {
  micButton:    '[data-testid="mic"], button[aria-label*="voice" i], button[aria-label*="mic" i]',
  commandInput: '[data-testid="command-input"], input[type="text"], textarea',
  responsePane: '[data-testid="response"], [class*="response" i]',
  widgetGrid:   '[data-testid="widgets"], [class*="widget" i], [class*="grid" i]',
  settingsBtn:  '[data-testid="settings"], button[aria-label*="settings" i]',
};

const wait = ms => new Promise(r => setTimeout(r, ms));

async function maybeClick(page, selector, { timeout = 1500 } = {}) {
  if (!selector) return false;
  try {
    const el = page.locator(selector).first();
    await el.waitFor({ state: 'visible', timeout });
    await el.click({ timeout });
    return true;
  } catch { return false; }
}

async function maybeType(page, selector, text, { delay = 55, submit = true } = {}) {
  if (!selector) return false;
  try {
    const el = page.locator(selector).first();
    await el.waitFor({ state: 'visible', timeout: 1500 });
    await el.click({ timeout: 1500 });
    await el.fill('');
    await el.type(text, { delay });
    if (submit) await page.keyboard.press('Enter');
    return true;
  } catch { return false; }
}

/** Slow, cinematic scroll -- reads far better on video than a jump. */
async function glideScroll(page, deltaY, ms = 1600) {
  const steps = Math.max(1, Math.round(ms / 16));
  const per = deltaY / steps;
  for (let i = 0; i < steps; i++) {
    await page.mouse.wheel(0, per);
    await wait(16);
  }
}

/** Drift the cursor so the frame never looks frozen. */
async function driftMouse(page, from, to, ms = 900) {
  const steps = Math.max(1, Math.round(ms / 16));
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; // easeInOutQuad
    await page.mouse.move(from[0] + (to[0] - from[0]) * e, from[1] + (to[1] - from[1]) * e);
    await wait(16);
  }
}

/**
 * Scenes are `{ at, title, sub, do }`. `at` is seconds from record start.
 * Keep the last scene ending a beat before `duration` so the video doesn't
 * cut mid-motion.
 */
export const SCENES = [
  {
    at: 0.5, title: 'J.A.R.V.I.S.', sub: 'Voice-interactive command dashboard',
    async do(page) { await driftMouse(page, [960, 900], [960, 540], 1400); },
  },
  {
    at: 7, title: 'Voice activation', sub: 'Wake word opens the live audio channel',
    async do(page) { await maybeClick(page, sel.micButton); await wait(2500); },
  },
  {
    at: 17, title: 'Natural-language command', sub: '"What\'s my system status?"',
    async do(page) { await maybeType(page, sel.commandInput, "What's my system status?"); await wait(3000); },
  },
  {
    at: 28, title: 'Real-time telemetry', sub: 'Widgets stream live data as it arrives',
    async do(page) { await glideScroll(page, 420, 1800); await wait(1500); },
  },
  {
    at: 39, title: 'AI assistance', sub: 'Contextual answers from the assistant layer',
    async do(page) { await maybeType(page, sel.commandInput, 'Summarize today’s activity'); await wait(3000); },
  },
  {
    at: 50, title: 'Built with', sub: 'React · Web Speech API · WebSockets',
    async do(page) { await glideScroll(page, -420, 1800); },
  },
];

export async function runScenes(page, ctx) {
  const { caption, elapsed, duration } = ctx;
  for (const scene of SCENES) {
    if (scene.at >= duration) break;
    const waitMs = (scene.at - elapsed()) * 1000;
    if (waitMs > 0) await wait(waitMs);
    await caption(scene.title, scene.sub);
    try {
      await scene.do?.(page);
    } catch (err) {
      console.log('   scene skipped:', scene.title, '-', err.message);
    }
  }
  await caption('');
}
