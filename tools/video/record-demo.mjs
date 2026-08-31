#!/usr/bin/env node
/**
 * Records a portfolio-ready demo video of the Jarvis voice dashboard.
 *
 *   node record-demo.mjs --url http://localhost:5173
 *   node record-demo.mjs --url http://localhost:5173 --cmd "npm run dev" --captions
 *
 * Produces, in ./out:
 *   jarvis-demo.mp4    H.264 / yuv420p / faststart  <- the one for your site
 *   jarvis-demo.webm   VP9 fallback (with --webm)
 *   jarvis-poster.jpg  first-frame poster image
 */
import { chromium } from 'playwright';
import ffmpegPath from 'ffmpeg-static';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, rm, readdir, rename } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const execFileAsync = promisify(execFile);
const here = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const opts = {
    url: 'http://localhost:5173',
    cmd: null,
    duration: 60,
    width: 1920,
    height: 1080,
    fps: 30,
    out: path.join(here, 'out'),
    captions: false,
    webm: false,
    silentAudio: false,
    headed: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === '--url') opts.url = next();
    else if (a === '--cmd') opts.cmd = next();
    else if (a === '--duration') opts.duration = Number(next());
    else if (a === '--width') opts.width = Number(next());
    else if (a === '--height') opts.height = Number(next());
    else if (a === '--fps') opts.fps = Number(next());
    else if (a === '--out') opts.out = path.resolve(next());
    else if (a === '--captions') opts.captions = true;
    else if (a === '--webm') opts.webm = true;
    else if (a === '--silent-audio') opts.silentAudio = true;
    else if (a === '--headed') opts.headed = true;
    else if (a === '--help' || a === '-h') { printHelp(); process.exit(0); }
    else throw new Error(`Unknown flag: ${a}`);
  }
  return opts;
}

function printHelp() {
  console.log(`
Usage: node record-demo.mjs [options]

  --url <url>        App URL to record          (default http://localhost:5173)
  --cmd "<command>"  Dev server to start first, and wait for --url to answer
  --duration <sec>   Exact output length        (default 60)
  --width/--height   Capture size               (default 1920x1080)
  --fps <n>          Output framerate           (default 30)
  --out <dir>        Output directory           (default ./out)
  --captions         Burn scene captions into the video
  --webm             Also emit a VP9 .webm
  --silent-audio     Add a silent AAC track (some embeds want one)
  --headed           Show the browser while recording
`.trim());
}

const log = (...a) => console.log('  ', ...a);

async function waitForServer(url, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { method: 'GET' });
      if (res.status < 500) return;
    } catch { /* not up yet */ }
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error(`Server at ${url} did not respond within ${timeoutMs / 1000}s`);
}

async function ffprobeDuration(file) {
  // ffmpeg-static ships ffmpeg only; parse duration out of stderr.
  const { stderr } = await execFileAsync(ffmpegPath, ['-i', file], { encoding: 'utf8' })
    .catch(e => ({ stderr: e.stderr ?? '' }));
  const m = /Duration:\s*(\d+):(\d+):(\d+\.\d+)/.exec(stderr);
  if (!m) return null;
  return (+m[1]) * 3600 + (+m[2]) * 60 + parseFloat(m[3]);
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const { url, duration, width, height, fps, out } = opts;

  await mkdir(out, { recursive: true });
  const rawDir = path.join(out, '.raw');
  await rm(rawDir, { recursive: true, force: true });
  await mkdir(rawDir, { recursive: true });

  let server = null;
  let preRoll = 0;
  let captureWall = 0;
  if (opts.cmd) {
    log(`starting dev server: ${opts.cmd}`);
    server = spawn(opts.cmd, { shell: true, stdio: 'ignore', detached: true });
  }

  try {
    log(`waiting for ${url}`);
    await waitForServer(url);

    log(`launching chromium ${width}x${height}`);
    const browser = await chromium.launch({
      headless: !opts.headed,
      // Set CHROMIUM_PATH when the machine already ships a browser (CI images,
      // sandboxes) instead of letting Playwright download its own.
      ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
      args: [
        // A voice dashboard needs a microphone: hand it a synthetic one so
        // getUserMedia resolves instead of blocking on a permission prompt.
        '--use-fake-ui-for-media-stream',
        '--use-fake-device-for-media-stream',
        '--autoplay-policy=no-user-gesture-required',
        '--hide-scrollbars',
        '--force-device-scale-factor=1',
      ],
    });

    const context = await browser.newContext({
      viewport: { width, height },
      deviceScaleFactor: 1,
      permissions: ['microphone'],
      recordVideo: { dir: rawDir, size: { width, height } },
      colorScheme: 'dark',
      reducedMotion: 'no-preference',
    });

    const page = await context.newPage();
    // recordVideo starts rolling here, not when the scenes do -- everything
    // between this point and the first scene is pre-roll to be trimmed off.
    const videoStartedAt = Date.now();
    page.on('console', m => { if (m.type() === 'error') log('page error:', m.text()); });

    await page.goto(url, { waitUntil: 'networkidle', timeout: 60_000 });

    if (opts.captions) await installCaptionOverlay(page);

    // Let first paint and any intro animation settle before the clock starts.
    await page.waitForTimeout(1200);

    const { runScenes } = await import('./demo-script.mjs');
    const started = Date.now();
    log(`recording ~${duration}s`);
    await runScenes(page, {
      duration,
      startedAt: started,
      caption: opts.captions
        ? (text, sub) => page.evaluate(([t, s]) => window.__jarvisCaption?.(t, s), [text, sub]).catch(() => {})
        : async () => {},
      elapsed: () => (Date.now() - started) / 1000,
    });

    // Hold until the full duration has actually elapsed.
    const remaining = duration * 1000 - (Date.now() - started);
    if (remaining > 0) await page.waitForTimeout(remaining);

    const captureEndedAt = Date.now();
    await context.close();          // flushes the .webm
    await browser.close();
    preRoll = (started - videoStartedAt) / 1000;
    captureWall = (captureEndedAt - videoStartedAt) / 1000;
    log(`capture complete (pre-roll ${preRoll.toFixed(2)}s, wall ${captureWall.toFixed(2)}s)`);
  } finally {
    if (server?.pid) { try { process.kill(-server.pid); } catch {} }
  }

  // --- encode -------------------------------------------------------------
  const raws = (await readdir(rawDir)).filter(f => f.endsWith('.webm'));
  if (!raws.length) throw new Error('Playwright produced no video file.');
  const raw = path.join(rawDir, raws[0]);

  const actual = await ffprobeDuration(raw);

  // Playwright's screencast timestamps run long under load, so the raw file's
  // timeline is stretched relative to real time -- left alone the app appears
  // to run in slow motion and the closing scenes fall outside the trim.
  // Rescale by the measured ratio so playback matches what actually happened.
  let stretch = 1;
  if (actual && captureWall > 1) {
    const ratio = actual / captureWall;
    if (ratio > 1.02 || ratio < 0.98) stretch = Math.min(3, Math.max(0.5, ratio));
  }
  const seekRaw = preRoll * stretch;            // seek in the stretched domain
  const usable = (actual ?? duration + seekRaw) / stretch - preRoll;
  log(`raw capture: ${actual?.toFixed(2) ?? '?'}s (timeline ${stretch.toFixed(3)}x real time)`);
  log(`usable footage after correction: ${usable.toFixed(2)}s`);
  if (usable < duration - 0.5) {
    log(`WARNING: only ${usable.toFixed(1)}s of scene footage for a ${duration}s video;`);
    log('         the last frame will be held to fill the gap.');
  }

  const mp4 = path.join(out, 'jarvis-demo.mp4');
  const vf = [
    ...(stretch !== 1 ? [`setpts=PTS/${stretch.toFixed(6)}`] : []),
    `scale=${width}:${height}:force_original_aspect_ratio=decrease`,
    `pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:color=black`,
    // If the capture came up short, freeze the last frame rather than
    // ending early -- the output is always exactly `duration`.
    `tpad=stop_mode=clone:stop_duration=${Math.max(0, duration - usable) + 1}`,
    `fps=${fps}`,
  ].join(',');

  const args = [
    '-y', '-ss', seekRaw.toFixed(3), '-i', raw,
    '-vf', vf,
    '-t', String(duration),
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '20',
    '-profile:v', 'high', '-level', '4.0',
    '-pix_fmt', 'yuv420p',
    '-movflags', '+faststart',
  ];
  if (opts.silentAudio) {
    args.splice(1, 0, '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000');
    args.push('-c:a', 'aac', '-b:a', '128k', '-shortest');
  } else {
    args.push('-an');
  }
  args.push(mp4);

  log('encoding mp4');
  await execFileAsync(ffmpegPath, args);

  const poster = path.join(out, 'jarvis-poster.jpg');
  log('extracting poster');
  await execFileAsync(ffmpegPath, [
    '-y', '-ss', String(Math.min(3, duration / 4)), '-i', mp4,
    '-frames:v', '1', '-q:v', '3', poster,
  ]);

  if (opts.webm) {
    const webm = path.join(out, 'jarvis-demo.webm');
    log('encoding webm');
    await execFileAsync(ffmpegPath, [
      '-y', '-i', mp4, '-c:v', 'libvpx-vp9', '-crf', '33', '-b:v', '0',
      '-row-mt', '1', '-an', webm,
    ]);
  }

  await rm(rawDir, { recursive: true, force: true });
  console.log(`\nDone -> ${out}`);
}

/** Lower-third caption overlay, burned into the recording. */
async function installCaptionOverlay(page) {
  await page.evaluate(() => {
    const el = document.createElement('div');
    el.id = '__jarvis_caption';
    el.style.cssText = [
      'position:fixed', 'left:56px', 'bottom:52px', 'z-index:2147483647',
      'pointer-events:none', 'opacity:0', 'transition:opacity .45s ease',
      'font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif',
      'max-width:60vw',
    ].join(';');
    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px">
        <div style="width:3px;height:34px;background:#22d3ee;border-radius:2px;
                    box-shadow:0 0 14px #22d3ee"></div>
        <div>
          <div id="__jc_t" style="color:#f1f5f9;font-size:27px;font-weight:650;
               letter-spacing:-.01em;text-shadow:0 2px 14px rgba(0,0,0,.85)"></div>
          <div id="__jc_s" style="color:#94a3b8;font-size:16px;font-weight:450;
               margin-top:3px;text-shadow:0 2px 12px rgba(0,0,0,.85)"></div>
        </div>
      </div>`;
    document.body.appendChild(el);
    let hideTimer;
    window.__jarvisCaption = (text, sub = '') => {
      clearTimeout(hideTimer);
      if (!text) { el.style.opacity = '0'; return; }
      document.getElementById('__jc_t').textContent = text;
      document.getElementById('__jc_s').textContent = sub;
      el.style.opacity = '1';
      hideTimer = setTimeout(() => { el.style.opacity = '0'; }, 5200);
    };
  });
}

main().catch(err => { console.error('\nFAILED:', err.message); process.exit(1); });
