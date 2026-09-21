# J.A.R.V.I.S. mk VII

A voice-driven Claude dashboard built to live fullscreen on a television. Grid
face, live device telemetry, local weather, web search, and persistent memory.
No build step, no package manager, no framework — open it and it runs.

## Running it

Double-click **`start.bat`**. It serves the folder and opens the browser at
`http://localhost:8123`.

Or by hand:

```bash
python serve.py
```

`serve.py` is `http.server` with two changes that matter: it sends no-cache
headers (otherwise Chrome silently serves you the previous version of a file
you just edited), and it is threaded (a single-threaded server stalls
half-way through loading the page when one connection hangs).

### It must be localhost or https — this is not optional

The dashboard **cannot** be opened as a `file://` path, and **cannot** reach
the microphone over a plain `http://192.168.x.x` address.

Browsers only grant `getUserMedia` and `SpeechRecognition` in a *secure
context*: `https`, or `http` on `localhost`. Everything else gets a hard
refusal from the browser before any of this code runs. If you land on a
non-secure origin the dashboard now says so in a red banner naming the host,
rather than failing silently.

## Putting it on the television

**The straightforward way** — run the browser on the machine itself and send
its picture to the TV over HDMI. The page is on `localhost`, so the microphone
works with no further setup. Press `⌘K` → **Fullscreen** and leave it.

**If the TV runs its own browser** and loads the dashboard across the network,
that origin is `http://192.168.x.x:8123`, which is not a secure context — so
voice input will not work. Typing still does. To get the microphone anyway,
pick one:

- Launch Chrome on the TV device with the origin whitelisted:

  ```bash
  chrome --unsafely-treat-insecure-origin-as-secure=http://192.168.4.33:8123
  ```

- Or put a real certificate in front of it (a reverse proxy, or a tunnel such
  as Cloudflare Tunnel / ngrok) and load it over `https`.

## Choosing a provider

Conversation needs a model behind it. Everything else — clock, battery,
network, weather, voice, the reactor — works without one.

There are two transports, chosen in Configuration → **Provider**.

### Anthropic (best quality, paid)

Talks to `api.anthropic.com` directly. Server-side **web search** and
**web fetch** only exist here, so this is the only setting where J.A.R.V.I.S.
can look things up live. Paste a key from
[console.anthropic.com](https://console.anthropic.com). It is usage-billed.

### OpenAI-compatible (where the free models are)

Anything speaking the OpenAI chat-completions dialect works: OpenRouter,
Groq, Together, DeepInfra, Gemini's compat endpoint, or a local Ollama. Set
three fields — base URL, key, model id.

| Service | Base URL | Free? | Browser-callable? |
|---|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | yes, models tagged `:free` | yes |
| Groq | `https://api.groq.com/openai/v1` | yes, fast, generous | yes |
| Ollama Cloud | `https://ollama.com/v1` | free base tier, capped | **no — needs the proxy** |
| Ollama (local) | `http://localhost:11434/v1` | yes, on your own machine | with `OLLAMA_ORIGINS` |

**"Browser-callable" is not a detail you can ignore.** A provider that sends no
CORS headers cannot be reached from a web page at all — Ollama Cloud answers
the preflight with `405`, so the request never leaves the browser. For those,
tick **Route through local server** in Configuration and `serve.py` makes the
call instead. It is on by default and harmless for providers that don't need
it.

That proxy only accepts requests from the machine it runs on. It would
otherwise be an open relay for anyone on your network.

### Saved connections

Configuration → **Saved connections** holds named provider configs. Picking one
loads its URL, key and model in a single click.

Edits follow the connection automatically — type a key or click a model and the
saved entry updates itself. The **Save** button is only for creating a new
entry under a different name. (An earlier build required pressing Save, which
meant a key could be typed, used, and then silently discarded by switching
connections; that failed later as an apparently rejected key, which was
thoroughly misleading.)

### Don't guess the model id

Press **List models** next to the Model id field. It asks the provider what it
actually serves, marks anything that costs nothing with a **free** badge, and
sorts those first — tick **free only** to hide the rest. OpenRouter reports 417
models of which 19 are currently free; that number moves, which is exactly why
the list is fetched rather than hardcoded. Click a row to switch to it. Model ids differ between services and
change often, so a name copied from documentation — including the default in
this repo — is a guess. (The listing call is a plain `GET`, so it cannot use
the proxy; on a CORS-blocked provider type the id by hand and Verify will
still confirm it.)

The base URL must end in `/v1`.

**Model ids change constantly** — do not trust a hardcoded list, including the
default in this repo. Open your provider's model list, filter to the free
ones, and copy the id exactly as they spell it. On OpenRouter free models
carry a `:free` suffix.

Web search works here too — see the next section. Streaming, the three
client-side tools, and the reasoning channel for models that expose one all
behave the same as on Anthropic.

## Calendar and mail

Read-only access to Google Calendar and Gmail, so "what does my day look like"
and "anything come in?" are answerable rather than guesses.

Configuration → Google:

1. **Open Google Cloud console** → new project
2. APIs & Services → enable **Google Calendar API** and **Gmail API**
3. OAuth consent screen → External → add yourself under **Test users**
4. Credentials → OAuth client ID → **Web application** → redirect URI
   `http://127.0.0.1:8123/google/callback`
5. Paste the Client ID *and* secret, press Connect

Google's desktop clients require the secret even with PKCE, which is why both
are asked for; both stay on the server. Leaving the consent screen in
**Testing** is fine for personal use — Google does not require verification
for that.

Scopes are `calendar.readonly` and `gmail.readonly`. He cannot send, reply,
delete or create. That was a deliberate choice: knowing the day is what makes
an assistant useful, and sending mail on your behalf is a different risk
entirely.

## Reminders and memory

Both live in `jarvis_state.json` on the server rather than in the browser.

A timer held in a page is not a reminder — it dies the moment the tab reloads,
which is exactly when you need it to survive. These are stored server-side and
the page polls for what is due, so a reminder still lands if the dashboard was
closed when its time came. Verified: set one, reloaded the page mid-flight, it
fired anyway.

"Remind me to check the oven in twenty minutes", "at 7pm", "tomorrow at 9" all
parse. Memory gained timestamps and a `recall` search, so he can look something
up rather than having every fact recited into his prompt each turn.

Moving off localStorage also means what he knows survives clearing site data,
and follows you between browsers.

## Seeing the screen

`see_screen` captures the desktop and asks a vision model about it — "what does
this error say", "is this right", "read that for me".

The main model cannot see, so this is a **second** model used only for images;
its answer becomes the tool result the main model reasons over. Set one in
Configuration → **Vision model** after pulling it:

```bash
ollama pull gemma4:cloud
```

`gemma4:cloud` runs remotely, so it costs no VRAM — worth it on an 8GB card.
`gemma4:e2b` runs locally if you would rather stay offline. The tool stays
hidden until a model is set.

Screenshots are downscaled to 1280px wide before leaving the server. A 4K frame
is enormous as base64 and a vision model reads the smaller one just as well.
Capture is refused from anywhere but this machine.

For a page he built himself, `see_preview` is the right tool rather than this
one — it renders that page alone instead of photographing the whole desktop.
See [Building things](#building-things).

## When a provider runs out

Free tiers exhaust, and they do it mid-sentence rather than politely in
advance. Ollama Cloud caps a session, OpenRouter rate-limits, Groq has a daily
ceiling.

So a failed call is inspected before it is surfaced. If the failure is
*exhaustion* — 429, 402, a quota message, a 5xx — he moves to the next saved
connection and retries the same request there. The exhausted one is rested for
fifteen minutes so he does not keep knocking, and a card appears saying which
provider he switched to.

The distinction matters: a bad model id or a malformed request fails
identically everywhere, so those are not retried. Failing over on those would
just multiply the same error across every provider you own.

Give each connection a working key and model in **Saved connections** and the
chain is as deep as you make it.

## Live task cards

A tool run of any length looks identical to a hang from outside. Cards float
over the stage while he works — "searching the web — billboard hot rap songs",
"checking live data — 100 usd to eur" — turn green with a timing when they
finish, red when they fail, and retire themselves.

## What the interface shows you

**The reactor carries state.** Its rotation slows almost to a stop while he is
reasoning and the core dims; both snap back the instant the first token
arrives, so the return to full brightness *is* the signal that the answer has
started. While listening, the core tracks your voice rather than only his.

**A wake pulse** throws one bright ring outward the moment "Hey Jarvis"
registers. Without it there is no way to tell he heard you until he starts
speaking, which is a long silence to sit through wondering.

**Result cards** render prices, rates and now-playing as something glanceable
rather than a sentence. They are built from the tool output, not from the
model's prose, so the numbers on screen are the ones the tool actually
returned. Gains and losses are coloured.

**Progress bars** for indexing and transcription are polled from the real job,
not animated to look busy — indexing thousands of chunks otherwise looks
identical to a hang.

## Model routing

A **Fast model** can be set for plainly conversational turns, so "turn it up"
does not wait on a reasoning model. The heuristic is deliberately biased toward
the main model: anything mentioning a tool, anything asking why or how, more
than one question, a long message, or a conversation already several turns deep
all go to the main model. A fast turn that reaches for a tool anyway is
escalated automatically.

**Measured on this setup, it is not worth using.** Against a cloud main model
that already answers in 2.7s, a warm local 3B saves 0.3s — and costs 6s the
first time, while it loads into VRAM. It pays off only when the main model is
genuinely slow, or when the fast model is hosted somewhere quick like Groq.
Leave it blank unless one of those is true.

## Self-check

**Self-check** re-reads a finished answer looking for claims the tool results
did not support, arithmetic slips, and false confidence. If it finds something,
a correction appears beneath the answer marked *"On checking that again"* —
shown rather than swapped in silently, because quietly rewriting something
already read is worse than admitting the change.

Roughly doubles the time and token cost of a turn. Off by default; *on hard
questions* is the sensible setting.

## Clipboard and foreground window

He can read what you last copied and see which application is in front. A
browser can do neither: clipboard access needs a user gesture and a permission
prompt, and no web API exposes the foreground window.

Both are worth using before answering something ambiguous — "how do I fix this"
means something different in a code editor than in a music player. He can also
copy text to the clipboard on request.

Clipboard contents are treated as data, not instructions.

## Document memory

He can search your own files — notes, contracts, manuals, code — semantically
rather than by keyword. Everything stays on the machine: chunks are embedded by
`nomic-embed-text` running in Ollama, and stored in `jarvis_recall.db`.

Point him at a folder and he indexes it. Unchanged files are skipped on a
re-run, so adding a few documents costs seconds rather than re-embedding
everything.

Reads `.txt .md` and every common code extension natively, `.docx` with no
extra library (it is a zip of XML), and `.pdf` if `pypdf` is installed.

Past conversations are indexed too, so "what did we decide about the wake word"
finds an exchange from weeks ago rather than falling out of context.

A note on speed: embedding measures about 2s per chunk on this machine through
the singular endpoint, which is per-call overhead rather than compute — the GPU
sits at 38%. Indexing therefore batches through `/api/embed`, roughly 2.4x
faster, and runs in the background with progress reported.

## Files, media, and projects

| Ask | He does |
|---|---|
| "find that invoice from March" | searches filenames across permitted folders |
| "read me that file" | returns its text |
| "transcribe this recording" | Whisper, locally |
| "convert this to mp3" | ffmpeg |
| "create a python project called X" | scaffolds a folder and runs setup |
| "run it" | executes the project and reads back what it printed |

The boundaries are explicit rather than implied:

- **Reading** is limited to a configured set of folders. Pointing an assistant
  at `C:\` invites it to read credentials it has no business seeing.
- **Writing** happens only inside the projects folder.
- **Commands** are a fixed allowlist — `git_init`, `npm_init`, `npm_install`,
  `pip_install`, `venv` — named individually. The model never supplies a
  command line, so there is no argument that makes `rm -rf` reachable.
- **Deleting is not implemented at all.** If he cannot delete, he cannot delete
  the wrong thing.

Project names are sanitised before use: `../escape` becomes `escape`, so
traversal cannot leave the projects folder.

`ffmpeg` is optional (`winget install Gyan.FFmpeg`); the media action says so
when it is missing rather than failing obscurely.

## Building things

Ask for something built and he builds it: scaffolds the project, writes each
file, and tells you what he made. He does not print a wall of code for you to
paste — that is the thing you asked to be saved from.

### The preview pane

A project appears in a floating pane over the interface as he writes it, served
from `/preview/<project>/` and reloaded on every write. Drag it by its bar,
reload it, open it in a real tab, close it.

It is deliberately kept off the dashboard's own static root, so a page he
generates cannot reach the dashboard's files, and the iframe is sandboxed
**without** `allow-same-origin`.

### He can see what he built

`see_preview` renders a project page in a headless browser and asks the vision
model what is on it — layout, alignment, spacing, anything cut off, overlapping
or unreadable. He fixes what it found and looks again.

```
"look at the coffee-shop page as a phone would see it"
```

The frame he looked at is put in the transcript beside his answer. A
description is not evidence; the screenshot is, and it is how you tell a real
observation from a confident one.

Two things this is **not**:

- It is not `see_screen`. That photographs the whole desktop — the dashboard,
  the preview pane at whatever size it happens to be, and everything else — so
  most of what would reach the vision model is not the page.
- It is not the preview pane. The pane can be closed, covered, or scrolled;
  the render is independent of all three and always uses a known viewport.

Rendering uses Edge, Chrome or Brave — whichever is installed — in headless
mode. Edge ships with Windows, so on a normal machine this needs nothing
installed. `width` and `height` set the viewport: `width: 390` is a phone.

It needs a **Vision model** set in Configuration, the same one `see_screen`
uses. Without it the tool stays hidden.

### Approving a change before it lands

Every write is committed, so nothing is ever lost. But by the time you read
"Rewrote style.css" the previous file is already gone, and reading a diff
afterwards is not the same as agreeing to it beforehand.

Configuration → **Approve changes to files**:

| Setting | What happens |
|---|---|
| When he replaces existing work | *default.* New files land immediately; a change to an existing file shows the diff and waits |
| Every write | Including new files. Safest, and a ten-file build is ten clicks |
| Never | Behaves as it did before — changes land straight away |

The card shows the file, `+`/`−` counts, his own note about why, and the diff
itself. **Apply** writes and commits it; **Reject** writes nothing at all, and
he is told plainly that you refused rather than that something went wrong.

The turn genuinely waits on you — the task card says so. Stopping the turn, or
leaving it three minutes, discards the change and writes nothing.

The decision belongs to the setting, never to the model. He calls `write`
exactly as before and the interface decides whether you see it first; a
guarantee the model can opt out of is not a guarantee.

### The loop closes itself

Writing files is not building something. The doctrine tells him to run it and
look at it afterwards, and a good model does — but asking is not the same as
knowing, and this failure is silent: files on disk, a confident summary, and
nobody ever checked. Before this existed he would finish a build by saying
*"you can execute it with `python main.py`"* — telling you to run the thing he
had never run.

So the harness checks. After a turn that wrote into a project it runs the
project, or renders the page and asks the vision model about it, then hands
him what it found:

```
[automatic build check — this came from the interface, not from the user]
I ran it.
main.py exited 1 after 0.1s — it did NOT succeed.
Traceback (most recent call last): ...
```

He reads the file, fixes it, and — in practice — runs it again himself to
confirm. A check note in the transcript records that it happened.

Four things keep it from becoming a nuisance:

- **Two rounds, maximum.** A critic can always find one more thing to improve,
  and a loop with no floor never gives the screen back.
- **Only after something new lands.** A round that fixes nothing does not
  trigger another identical inspection.
- **Only what the turn touched.** Writing a README is not a reason to run your
  program and hand you a traceback you did not ask about. A page is checked
  when markup, styles or scripts change; the project is run when code does.
- **It stands down when he checks himself.** Call `run` or `see_preview` after
  writing and the harness stays out of the way — it would only cost another
  render to say what he already knows.

A page with no vision model set is skipped rather than half-checked. Turn the
whole thing off in Configuration → **Check the build afterwards**.

### Running it and reading the error

`run` executes a project and hands back everything it printed, exit code
included, so he can fix what actually broke instead of guessing.

| Runner | What it does |
|---|---|
| `python` | runs `main.py`, or a `.py` file you name |
| `node` | runs `index.js`, or a `.js` file you name |
| `npm_test` | `npm test`, if there is a `package.json` |
| `pytest` | `pytest -q` |

A project with its own `.venv` is run with that interpreter rather than the
system one.

This is a second allowlist, separate from the setup commands, and it is
narrow on purpose:

- Four named jobs. There is no way to hand over a command line.
- Projects folder only. The file to run must resolve inside the project.
- **No stdin.** A script that asks for input fails immediately with `EOFError`
  rather than hanging until the timeout.
- 90 seconds by default, 180 at most, and the whole process tree is killed —
  `npm test` is npm, which is node, which is the runner, and killing only the
  first leaves two behind.
- A run stopped at the timeout is reported as stopped, not as failed. They are
  different facts and he is told to say so.

Nothing that serves is on the list. `npm run dev` never exits, so it could only
ever end at the timeout, and a task that always ends the same way teaches you
nothing.

## Live data sources

Search is the wrong instrument for a question with an exact answer. "What is a
dollar worth in yen" through a search engine means reading a snippet off a page
that may be months stale; through the ECB's own feed it is a number, current,
in 200ms.

So he also has `lookup`, a set of narrow sources that return facts:

| Source | Gives him | Key |
|---|---|---|
| `currency` | ECB rates via Frankfurter | none |
| `crypto` | coin prices and 24h change | none |
| `wikipedia` | encyclopaedia summaries | none |
| `news` | world headlines from RSS | none |
| `tech_news` | Hacker News front page | none |
| `tv_tonight` | TV schedule by country | none |
| `daylight` | sunrise, sunset, day length | none |

Not one of them needs an account. The prompt tells him to check this tool
before searching, because when a source fits it is both faster and more
accurate than reading a snippet.

Each source is a separate function, so one going dark is reported as itself
rather than taking the tool down. Feeds for `news` are in `DEFAULT_FEEDS` at
the top of `jarvis_knowledge.py` — change them to whatever you actually read.

## Web search

A browser cannot call a search engine. CORS forbids it, and no search API
sends the headers that would permit it — which is why Anthropic runs their
`web_search` on their own servers.

`serve.py` solves it by being the page's own origin. It exposes two endpoints
that the browser is allowed to call, and it does the outbound request itself:

| Endpoint | Does |
|---|---|
| `/api/search?q=` | queries a search engine, returns titles, URLs, snippets |
| `/api/fetch?url=` | retrieves one page, strips it to readable text |

`brain.js` probes for these at boot. If they answer, the model is handed
matching `web_search` and `web_fetch` tools and the system prompt is rewritten
to tell it they exist. If you are not serving through `serve.py`, the tools are
withheld and the prompt tells the model it has no lookup — so it never claims
to have searched when it did not.

**This means web search works on free providers.** It is not tied to
Anthropic.

### Search backends

Keyless search is best-effort, and that is a property of the web rather than of
this code. DuckDuckGo now answers scrapers with a captcha page. Mojeek is the
default because it runs its own crawler and does not challenge scrapers — but
it throttles after a burst, and a throttled engine returns an empty page rather
than an error, so searches simply start coming back empty.

If you want search to be dependable, put a **Brave Search key** in
Configuration (2,000 queries a month, free, from `brave.com/search/api`). With
a key present it is tried first and the scraped engines become fallbacks.

Order tried: Brave (if a key exists) → Mojeek → DuckDuckGo.

### Legacy: the environment variable

Out of the box it scrapes DuckDuckGo's HTML endpoint. No key, no account,
nothing to configure — but it is scraped markup, so it will break whenever
they change their template.

For something reliable, set a Brave Search key (2,000 queries/month free) and
`serve.py` will prefer it automatically:

```bash
setx BRAVE_API_KEY "your-key-here"
```

Restart the server afterwards.

### A note on `/api/fetch`

It takes a URL chosen by the model and retrieves it using this server's
network access. That is a classic SSRF shape, so it refuses any host that
resolves to a private, loopback, link-local, or reserved address — a page
cannot talk the model into probing your LAN or a cloud metadata endpoint
through it. Only `http` and `https` are allowed.

Treat fetched page text as untrusted input, because it is: anything the model
reads on the open web is data, not instructions.

### Ollama — the best free setup, and the simplest

If the Ollama desktop app is installed and signed in, point the dashboard at
**`http://localhost:11434/v1`** and leave the key **blank**.

That is the whole configuration. Local Ollama authenticates itself, so no API
key ever touches the browser — including for cloud models, which it fetches on
your behalf after `ollama signin`. It also sidesteps the CORS problem entirely,
because localhost is not a cross-origin request.

```bash
ollama signin
ollama pull gpt-oss:120b-cloud
```

Then set Model id to `gpt-oss:120b-cloud` and press **List models** to confirm
it appears. You get a 120-billion-parameter model, answering in about three
seconds, with no key in the dashboard and no proxy needed.

Note that `gpt-oss` is a *reasoning* model: it spends output tokens thinking
before it speaks. That is why a low `max_tokens` returns a truncated answer.
Its reasoning arrives on a separate stream field, which the dashboard routes to
the trace panel rather than the reply — turn it on with **Show reasoning
summary** if you want to watch it work.

### Running a model on your own hardware



Ollama runs the model on your own hardware. No key, no account, no per-token
cost, and it works with the network down.

```bash
ollama pull qwen3:8b
```

It must be told to accept the dashboard as an origin, or the browser's CORS
check will block every request:

```bash
setx OLLAMA_ORIGINS "http://localhost:8123"
```

Restart Ollama afterwards. Then set base URL `http://localhost:11434/v1`,
leave the key blank, and use the model name you pulled.

Quality is well below a frontier model — an 8B model will disappoint on hard
technical questions in a way a hosted 70B-class model will not. But it is
genuinely free, entirely private, and works with the network down.

**Sizing it to your GPU.** The model has to fit in VRAM or it spills to system
RAM and slows to a crawl. Roughly, at 4-bit quantisation: an 8B model wants
about 5 GB, a 14B about 9 GB, a 32B about 20 GB. On an 8 GB card, 7-9B is the
sweet spot.

## First run

1. Gear icon, top right, or `Ctrl+/`
2. Pick a **Provider** and fill in its fields
3. Press **Verify key** — it makes one 1-token request and tells you plainly
   whether the key works, instead of leaving you to guess

The **LINK** chip in the top bar shows the state at a glance: `no key`,
`bad key`, or `ready`.

The key is held in this browser's `localStorage` and sent only to
`api.anthropic.com`. It is never committed anywhere. Do not do this on a shared
machine — see *Holding the key server-side* below for the safer arrangement.

## Controlling the machine

He is wired into the desktop, not just the page.

| Ask for | He does |
|---|---|
| "open Spotify" | launches the installed desktop application |
| "take me to youtube" | opens it in your real default browser |
| "set a timer for 10 minutes" | timer, with a spoken alert |
| "make the accent orange" | restyles the interface |

### Spotify

Media keys are blind toggles. The Spotify Web API is not: it can start a
**named** track or playlist and report what is genuinely playing.

| Say | He does |
|---|---|
| "play Techno Bangers" | searches your library and starts that playlist |
| "play Let's Do It by Playboi Carti" | starts that track |
| "what's playing?" | reads the actual track, artist and device |
| "set spotify to 40%" | exact volume, not four blind key presses |

**Setup** — Configuration → Spotify:

1. Create an app at `developer.spotify.com/dashboard`
2. Add **`http://127.0.0.1:8123/callback`** as a Redirect URI
3. Paste the Client ID and press **Connect**

That redirect URI is not a typo and `localhost` will not work: Spotify rejects
it and accepts only an explicit loopback literal. Because the callback
therefore lands on a different origin from the dashboard, the OAuth flow is
handled entirely by `serve.py`, which also means **the tokens never touch the
browser** — they live in `spotify_auth.json` (gitignored), not in localStorage
where any injected script could read them.

Authorisation uses PKCE, so there is no client secret to keep anywhere.

Two things to know: **starting playback requires Spotify Premium** (the API
returns 403 otherwise, and he will tell you so), and Spotify needs an *active*
device — a freshly opened desktop client is "available" but not "active", so
the first play of a session transfers playback to it automatically.

### Media and volume

| Ask for | He sends |
|---|---|
| "play" / "pause" | the play/pause media key |
| "skip this" / "go back" | next / previous track |
| "turn it up" / "quieter" | volume up / down, four steps |
| "mute" | mute toggle |
| "lock my pc" | LockWorkStation |

These are the **global Windows media keys**, so they drive whatever is actually
playing — Spotify, a browser tab, VLC — with no per-application integration.

The endpoint sends a fixed set of virtual key codes chosen by name. There is no
way to ask it for an arbitrary keystroke, so it cannot be turned into a way to
type into whatever window happens to be focused.

One honest limitation: **play/pause is a toggle and he cannot see the result.**
He reports that he pressed it, not that music is now playing, because he has no
way to know which of the two happened.

### How app launching works, and its limits

`serve.py` scans your Start Menu and builds a list of what is installed —
106 entries on the machine this was written on. When the model asks to open
something it supplies **a name, never a command line**, and that name is
matched against the list. Nothing outside it can be started, and the endpoint
refuses any request that did not come from this machine.

The matcher is built for dictated speech, so it survives extra words and
mishearing: `open up spotify on my pc`, `spotifi` and `disc cord` all resolve
correctly. If nothing matches, the failure tells the model what *is* installed
so it can offer a real alternative.

Installers, uninstallers and readme shortcuts are filtered out — launching
those by voice is never what "open X" meant.

### Why links go through the server too

`window.open()` fires from an async model reply, so there is no user gesture
behind it and Chrome blocks it outright. Earlier builds then claimed the page
had opened when nothing had. Links now go to `serve.py`, which hands them to
the real default browser — no popup blocker involved.

## Wake word and standby

With the wake word on (the default), he is a two-state machine rather than a
gate on every sentence:

- **Standby** — everything heard is discarded until you say **"Hey Jarvis"**.
- **Awake** — talk normally. No need to say his name again. Follow-ups like
  "and tomorrow?" work.

After a minute of silence he returns to standby. Change it in Configuration →
**Return to standby after**, or set it to *never*. Finishing a reply counts as
activity, so a long answer cannot put him to sleep mid-sentence.

The wake pattern is deliberately loose, because speech recognition mangles the
name constantly — "hey jarvis", "jarvis", "hi jervis" and several common
mishearings all wake him.

## Controls

| | |
|---|---|
| `Space` (hold) | talk — release to send |
| `Enter` | send typed message |
| `Ctrl+K` | command palette; also works as a question box |
| `Ctrl+/` | configuration |
| `Esc` | stop generating, or stop speaking |
| click the face | toggle the microphone |

Turn on **Wake word** in configuration to leave the mic open permanently and
have it respond only to "Jarvis…". That is the hands-free mode for a TV.

## What it can do for itself

The model has real tools, not just chat:

- **web_search / web_fetch** — looks things up before answering. On by default.
- **control_interface** — opens URLs, plays things on YouTube, sets timers,
  restyles the interface, toggles fullscreen, switches units.
- **remember / forget** — stores durable facts about you across sessions.
  Review or wipe them under Configuration → Memory.

## Holding the key server-side

If you put a tiny proxy at `api/chat` that forwards to the Messages API with
the key attached, and answer `api/health` with `{"jarvis":true}`, the dashboard
detects it at boot and stops using the browser-held key entirely. Nothing else
changes.

## Architecture

Classic scripts on `window.J`, deliberately — so the folder still runs when
served by anything at all. Load order matters and is fixed in `index.html`.

| file | responsibility |
|---|---|
| `js/core.js` | state, settings, storage, log, toasts, markdown |
| `js/starfield.js` | parallax backdrop |
| `js/orb.js` | HUD ring, spectrum, sweep arcs |
| `js/face.js` | the grid face — 3D wireframe head |
| `js/telemetry.js` | clock, battery, network, load, weather |
| `js/voice.js` | speech recognition and synthesis |
| `js/brain.js` | Messages API, streaming, tool loop, prompt caching |
| `js/palette.js` | command palette |
| `js/app.js` | DOM wiring, transcript, boot |

Modules communicate through `J.on` / `J.emit`, never by reaching into each
other. Adding a panel means adding a listener, not editing five files.

### Notes on speed

- The system prompt and tool schemas sit in one cached block, and a second
  cache breakpoint rides the newest settled assistant turn. After the first
  message of a session the whole prefix is reused instead of re-read — watch
  the system log for `Prompt cache hit`.
- Streamed text appends to a text node; markdown is parsed **once**, when the
  turn ends. Re-parsing every chunk is what makes streaming UIs feel heavy.
- Replies begin speaking at the first sentence boundary, while the rest is
  still streaming.
- The face batches its mesh into four depth buckets and strokes each once —
  about a dozen canvas calls a frame, and no `shadowBlur` anywhere.

## The face

Three masks, switchable in Configuration → **Face**, or from the command
palette:

- **Arc reactor** *(default)* — the Iron Man core. Ten coil segments in a
  housing ring, a counter-rotating inner assembly, and a hot centre. The
  segments are wired to ten bands of the audio spectrum, so the ring lights up
  around him in the shape of his own voice.
- **Grid** — the clean HUD wireframe, coloured from the interface accent.
- **Joker** — bone-white mesh, green mane, black eye paint, and a carved grin
  running up onto the cheeks. The grin is what the voice drives, so speech
  opens the smile rather than a mouth line.

Grid and Joker are the same 3D head: a stack of rings whose width and depth
come from a head profile resampled through a Catmull-Rom spline, with the brow,
sockets, nose and mouth added as gaussian bands. The reactor is not a head at
all and draws straight in polar coordinates. Nothing is a bitmap.

## Browser support

Chrome or Edge. Speech recognition is `webkitSpeechRecognition`, which Firefox
does not implement — everything else works there, but you will have to type.
The Battery API is Chromium-only; the panel says so when it is unavailable.

## When something is not working

The system log in the right-hand rail records what happened and why. Most
problems are one of three things:

| Symptom | Cause |
|---|---|
| "I need an Anthropic API key" after pasting one | Older builds only saved the field on blur. Fixed — it now saves as you type. Press **Verify key** to confirm. |
| Microphone button dimmed, red banner | Not a secure context. See *It must be localhost or https* above. |
| Microphone permission denied | Padlock icon in the address bar → Microphone → Allow → reload. |
| No microphone found | No input device on the machine. A TV over HDMI has no microphone of its own. |
| `404` from an OpenAI-compatible provider | Base URL is wrong, or the model id does not exist. The URL must end in `/v1`. |
| `401`/`403` from an OpenAI-compatible provider | Key rejected. Check you pasted the key for *that* service. |
| "Could not reach…" on a local Ollama | CORS. Set `OLLAMA_ORIGINS` to `http://localhost:8123` and restart it. |
| He offers to search but never does | You are on the OpenAI-compatible transport, which has no search tool. Switch to Anthropic for live lookups. |
