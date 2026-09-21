# Continuation — where J.A.R.V.I.S. is right now

Paste this to pick up. Written 2026-08-28, end of a long session.

## Read these first

`README.md`, `PHASES.md`, then this. Everything below is verified by running
it, not by reading the code — and where it is NOT verified, it says so.

## Standing decisions — do not loosen without asking

* Nothing speaks unprompted. **One exception, added today and scoped:** while a
  job hunt is running, a batch that arrives on the ten-minute timer announces
  itself out loud. Nothing else speaks.
* He never presses submit — not on a job application, not on a YouTube upload.
  He finds, drafts, opens; Zero clicks send.
* Nothing on an application, a resume or a video description is ever invented.
* Writing stays inside `~/JarvisProjects`. No delete. Command allowlists only.
* The dashboard is not a git repo. Snapshot to `.backup/` before editing.

## How I want you to work

Verify, don't assume. Measure before designing. Never claim something works
without seeing it work. Tell me when I'm wrong. Comments explain why, never
what.

**Three things bit hard today. Do not repeat them:**

1. **`serve.py` does not hot-reload.** Restart it after ANY Python change. Two
   separate "it's broken" rounds were stale server code. It also caches its
   servable file list at startup, so a new file 404s until restart.
2. **A return value is not an outcome.** `prepare()` returned `ok: True` over a
   page with no form on it. `SendInput` returned success while Minecraft
   received nothing. `choose()` succeeded and was reported as a failure. Check
   the thing that actually happened, not the thing the function said.
3. **Heredocs through the Bash tool eat backslash escapes.** `\\n` becomes a
   real newline and `\b` becomes a backspace character, silently corrupting
   Python and JS string literals. Use the Edit/Write tools for anything with
   escapes. Also: if a patch script asserts, it may not have written the file —
   check, because one silent failure cost an hour.

---

# Built this session

## Design foundation and the build check

`templates/_base/style.css` gained the five components that were missing —
`.hero`/`.hero-content`, `.brand`/`.brand-mark`, `.nav-toggle` with a
collapsing `.site-nav`, `.media`/`.placeholder` drawn in CSS. `base.js` wires
the toggle. Verified at 1280 and 390, and verified to degrade without JS.

`files.check` is a deterministic post-build pass — no model in it. Class diff
against the stylesheets a page actually links, every local ref resolved, remote
images verified over the network, and a default `--accent` flagged. It runs in
`inspectBuild` **before** the vision pass and regardless of whether a vision
model is set. Replayed on `bean_and_brew`: 14 problems. On `resume`: 54.

**The shrink guard is in.** `write` refuses a change cutting an existing 30+
line file below 40% of both its lines and its bytes. `force` is not reachable
from the model — only `apply_change`, after a human approves the diff.

Full write-up: `PROMPT-better-ui.md`.

## Barge-in — settled

**On, and the on/off question was the wrong one.** `isEcho` compared only
against the sentence in flight while recognition lags seconds, so his own
earlier sentences read as a person talking. It also blocked anything under five
characters, so **"stop" and "wait" could not interrupt him**.

Measured over 22 labelled cases: **5 false cuts and 2 missed interruptions
before, 0 and 0 after.** Window 8s, threshold 0.8, whole-word matching — each
swept, not picked. `soundsLikeHim` on the submit path is deliberately untouched.

## Job hunt — `job_hunt_skill.py`

Fills the gap the Greenhouse/Ashby watchlist never could: `python-jobspy`
across LinkedIn, Indeed, Google Jobs and ZipRecruiter. Profile now searches
`barista, cashier, food prep, warehouse` in Lakewood/Long Beach — 54 rows
across all four types.

* Shares `jarvis_jobs.json` — one profile, one `seen[]` list.
* Dedup by **job identity** (title+company+location), so a re-post with a new
  board id cannot come back.
* Two-stage ranking: free local embedding over everything, then
  `gpt-oss:120b-cloud` over the top 10. Benchmarked: `phi3:mini` scored 30/30/30
  for two barista jobs and a Kubernetes job; `llama3.2:3b` gave two identical
  barista roles 80 and 40; `gpt-oss` gave 92/85/5.
* **The pop-up is drawn by a poller, not by the model.** `EXCHANGES: 0` and the
  card still appears. This was rebuilt three times before it held — see
  `zero-wants-visual-ui` in memory.
* `choose` runs tailor → prepare, follows "Apply on company site" → "Apply Now"
  up to 3 hops, fills what `FIELD_MAP` matches, refuses every submit control.
  **Verified on Kroger's real ATS.**
* Applications need a per-site login. `sign_in` opens the apply profile in an
  ordinary Edge window — Google refuses OAuth inside an automated browser, and
  that is not worked around.

Full write-up: `PROMPT-job-hunt.md`.

## Minecraft — `jarvis_minecraft.py`

Java 26.2, vanilla, singleplayer. No RCON, no bot library new enough. It types
`/fill` commands into the chat box, which cannot go out of date.

**15 structures:** house (7 palettes incl. treehouse and cave), beach_house,
theater, waterpark, amusement_park, football/soccer/basketball/baseball venues,
racetrack, target_store, walmart, starbucks, store.

* **`where`** reads exact coordinates via Minecraft's own **F3+C** shortcut. No
  OCR. This is what lifted everything out of "relative to wherever he stands".
* Builds are anchored absolutely and **recorded on disk** in
  `jarvis_minecraft.json`, so a new build **steps around** existing ones
  geometrically. Verified: theater at `[153,69,262]`, stadium placed at
  `[237,69,262]` automatically.
* `add` (stairs, interior, basement, terrace, garden, porch) never clears
  anything, so it cannot double a building.
* Every fill is **sliced to stay under `/fill`'s 32,768-block limit**. Ignoring
  that turned a clean-up into flattened terrain and floating mansions.
* Timing: `KEY_GAP 0.035 / CHAT_GAP 0.09`. Halving them dropped keystrokes and
  produced holed walls. Do not "optimise" without looking at the build.

**Only the house, beach house, theater and stadium have been seen in-game.**
The other eleven generate valid, gate-passing commands and nothing more.

---

# What I would do next

1. **Undo for Minecraft.** The register has every bounding box; one command
   per build removes it. Cheap, and it would have saved the mess today.
2. **Pixel art from an image** — map an attached picture to wool/concrete and
   build it. This is the honest answer to "build me an anime character", which
   generators cannot do.
3. **Architectural detail** — stairs and slabs for eaves and sills, recessed
   windows, pilasters, two-tone banding. What separates a box from a building.
4. **Look at the eleven untested structures** before adding more.
5. **Job hunt:** `choose` has still never run against a live Indeed posting
   end to end, only Kroger.

## Honest caveats

* Cars and anime characters do not suit fill-based generation. They need voxel
  models or the image converter.
* Modpacks are not possible in a useful sense: vanilla has no loader, mods load
  at startup, and 26.2 is too new for most. Datapacks are the achievable
  version.
* Pexels is dead. Google Custom Search needs the API enabled on project
  `focal-essence-506801-m4`.

## If this is a different machine

The dashboard is on the USB (`THEVOID`, E:). These are NOT:

* Python 3.14 + `pywin32`, `playwright`, `python-jobspy`, `pandas`, `numpy`,
  `pillow`
* Ollama. Only **`nomic-embed-text` (274 MB)** actually matters — everything
  else now uses `gpt-oss:120b-cloud`, which stores nothing locally.
* `~/JarvisProjects` — application packets and the Edge apply-profile.
* **Your API keys and settings, which live in the browser's localStorage.**
  A new machine's browser starts blank; you will re-enter them.

Start with `python serve.py 8123`, then open `http://localhost:8123`.
