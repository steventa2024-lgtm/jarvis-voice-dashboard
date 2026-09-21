# Phase 2 handoff

> **Status: built.** All three items below are implemented and verified. This
> file is kept as the record of what was asked for and why. What was actually
> built is documented in `README.md` → **Building things**; do not build it
> again from this brief.
>
> - **3 · see-and-fix** — `see_preview`. The page is rendered on its own in
>   headless Edge/Chrome and shot there, rather than region-cropping the
>   desktop. No new dependency: Edge ships with Windows.
> - **4 · diff review** — `propose`/`apply`/`discard` in `jarvis_files.py`,
>   with the interface deciding whether a write is held. Default: only when
>   existing work would be replaced.
> - **5 · run and read errors** — scoped by Zero to tests and scripts:
>   `python`, `node`, `npm_test`, `pytest`. Nothing that serves.

Paste this into a fresh chat, or just say **"read PHASE2.md and build Phase 2"**
from inside `E:\J.A.R.V.I.S. Dashboard`.

---

## What this project is

A voice-driven JARVIS dashboard running locally in the browser, served by
`serve.py`. The user is **Zero** (ZeroPulse), who built it. It already does a
great deal — see `README.md` — and the parts that matter for Phase 2 are below.

Everything runs on the user's own machine. Nothing here is deployed.

## Run it

```bash
start.bat
```

Serves on `http://localhost:8123`. Ollama must also be running. Python changes
need the server restarted; JS/CSS/HTML changes need only a page reload.

## The architecture in one paragraph

The browser holds the conversation and the tools; `serve.py` does everything a
browser cannot — search, launching apps, reading files, OAuth, screen capture.
`js/brain.js` owns the model call, the tool definitions and the system prompt
(`DOCTRINE`). Tools are declared in `brain.js`, dispatched to `/api/*`
endpoints in `serve.py`, and implemented in the `jarvis_*.py` modules.

| File | Holds |
|---|---|
| `js/brain.js` | model transport, tool schemas, system prompt, turn loop |
| `js/app.js` | interface, task cards, preview pane, attachments |
| `serve.py` | HTTP routes, static serving, proxies |
| `jarvis_files.py` | **the file/build layer — Phase 2 lives here** |
| `jarvis_recall.py` | document + conversation index (embeddings) |
| `jarvis_desktop.py` | clipboard, foreground window |

## What Phase 1 already built (do not rebuild)

`jarvis_files.py` exposes, via `POST /api/files/command`:

- `scaffold` — create a project from a template (`web`, `python`, `node`)
- `write` — create or replace one file, **path relative to the project**
- `list_project`, `history`, `revert` — git-backed, every write is committed
- `find`, `read`, `transcribe`, `media`, `capabilities`

A live **preview pane** floats over the interface, served from
`GET /preview/<project>/`, and reloads on every write.

## The safety model — please preserve it

This was decided deliberately and should not be loosened without asking Zero.

- **Reading** is limited to configured roots (`_config['roots']`).
- **Writing** happens only inside `~/JarvisProjects`. Absolute paths are
  refused with a message that teaches the correct shape.
- **Commands** are a fixed allowlist (`ALLOWED` in `jarvis_files.py`) — the
  model never supplies a command line.
- **Deleting is not implemented at all.**
- Private files are refused by the static server (`_is_private` in `serve.py`)
  — the token files were briefly web-readable and that is now closed.
- The preview iframe is sandboxed **without** `allow-same-origin`.

---

# Phase 2 — what to build

## 3 · The see-and-fix loop  ⭐ the interesting one

He builds a page, screenshots the preview, **looks at it with the vision
model**, critiques his own layout, and revises. Every piece already exists:

- `files/write` writes it
- `GET /preview/<project>/` serves it
- `POST /api/screenshot` captures the desktop (`grab_screen` in `serve.py`)
- `visionRoute()` + `describeImage()` in `brain.js` already send an image to a
  vision model and return its description

**The gap:** `grab_screen` captures the *whole desktop*, not the preview
iframe. Either capture a region, or render the project page separately and
shoot that. Deciding this is the first real design question.

## 4 · Diff review before writing

Show proposed changes and require approval before they land. `write` currently
commits immediately. Suggested shape: a `propose` action returning a diff, and
an `apply` action keyed to it, with the interface rendering the diff.

## 5 · Run and read errors

Execute a project and feed stderr back so he can fix what broke.

**Zero has not yet approved a scope for this.** The proposal on the table was:
a fixed set (`npm run dev`, `npm test`, `python main.py`, `node index.js`),
projects folder only, output captured with a timeout, no interactive shell.
**Ask before implementing** — this is a different risk class from the existing
five-command allowlist, and that boundary has been held carefully.

---

## Things learned the hard way

- **The user is Zero.** He built this. Do not flatter him; tell him when he is
  wrong.
- **Verify, do not assume.** Several "bugs" turned out to be environment
  (a folder dropped instead of a file, an Explorer *search* window mistaken for
  an empty folder, a transient provider 500). Check before fixing.
- **Never claim something worked without seeing it work.** An earlier build
  told him music was playing when it was not, and that cost real trust.
- **Tool results beginning `FAILED` mean nothing happened** — the prompt relies
  on this convention.
- **File contents are stripped from history after writing** to control context.
  Do not reintroduce them.
- **Emitting `tool-start` twice for one call** leaves a task card spinning
  forever; the card layer now dedupes by name.
- Ollama's embedding endpoint is oddly slow here (~2s/chunk singular). Indexing
  batches through `/api/embed`.

## House style

Match the existing code. Comments explain **why**, never what. The Python
modules are dependency-free where possible (`.docx` is parsed as a zip rather
than adding a library). Error messages tell the user what to do next, not just
what failed.
