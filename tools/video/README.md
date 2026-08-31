# Jarvis demo video recorder

Records a portfolio-ready 60-second demo of the Jarvis voice dashboard: drives the
real app in a real browser, captures it, and encodes an MP4 you can drop straight
into a website.

## Output

Written to `tools/video/out/`:

| File | What it is |
|---|---|
| `jarvis-demo.mp4` | H.264 High / yuv420p / faststart — plays everywhere, streams before fully downloaded |
| `jarvis-poster.jpg` | Still frame for the `<video poster>` attribute |
| `jarvis-demo.webm` | VP9 fallback (only with `--webm`) |

## Usage

```bash
cd tools/video
npm install

# against an already-running dev server
node record-demo.mjs --url http://localhost:5173 --captions

# or let it start the server and wait for the port itself
node record-demo.mjs --url http://localhost:5173 --cmd "npm run dev" --captions --webm
```

### Options

| Flag | Default | |
|---|---|---|
| `--url <url>` | `http://localhost:5173` | app to record |
| `--cmd "<cmd>"` | — | dev server to launch first |
| `--duration <s>` | `60` | exact output length |
| `--width` / `--height` | `1920` / `1080` | capture size |
| `--fps <n>` | `30` | output framerate |
| `--out <dir>` | `./out` | output directory |
| `--captions` | off | burn scene captions into the video |
| `--webm` | off | also emit a VP9 `.webm` |
| `--silent-audio` | off | add a silent AAC track (some embeds require one) |
| `--headed` | off | watch the browser while it records |

Set `CHROMIUM_PATH` if the machine already ships a browser and you don't want
Playwright downloading its own.

## Tailoring the demo

`demo-script.mjs` holds the storyboard — a `SCENES` array of
`{ at, title, sub, do }`, where `at` is seconds from the start of the recording.
Two things to edit:

1. **The `sel` map** at the top — CSS selectors for the real mic button, command
   input, response pane, etc. Anything left unmatched is skipped rather than
   failing the take, so you can wire them in one at a time.
2. **The scene list** — retime or rewrite the beats. The default runs:
   intro → voice activation → natural-language command → live telemetry →
   AI assistance → closing pull-back.

Interactions go through `maybeClick` / `maybeType`, and `glideScroll` /
`driftMouse` produce slow cinematic motion that reads far better on video than
instant jumps.

## Embedding it

```html
<video src="/jarvis-demo.mp4" poster="/jarvis-poster.jpg"
       autoplay muted loop playsinline preload="metadata"></video>
```

`muted` is required for autoplay in every current browser; `playsinline` stops
iOS Safari from going fullscreen.

## Notes on the pipeline

Two corrections happen automatically at encode time, both of which matter for a
clean result:

- **Pre-roll trim.** Playwright starts recording when the page is created, so the
  navigation and first-paint settle appear at the head of the raw file. The
  recorder measures that gap and seeks past it, so frame one is the loaded app.
- **Timeline rescale.** Chromium's screencast timestamps run long under capture
  load — typically 10–15% — which makes the app look sluggish and pushes the
  final scenes past the trim point. The recorder compares the raw file's duration
  against the measured wall-clock capture window and rescales with `setpts` so
  playback matches real time.

If the capture comes up shorter than `--duration`, the last frame is held to fill
the gap and a warning is printed — the output is always exactly `--duration` long.
