# Running J.A.R.V.I.S. on your own

Nothing here depends on Claude Code. The dashboard is a folder of static files
plus one Python server; once it is on a drive you can start it and forget it.

## Every day

Double-click **`start.bat`**. A console window opens, your browser opens at
`http://localhost:8123`, and that is it.

Leave the console window open — closing it stops the server. To stop
deliberately, close that window or press `Ctrl+C` in it.

**Ollama must also be running.** It is a background app with a tray icon; if
you quit it, the dashboard will say the model is unreachable.

## Moving it to a flash drive

Copy the whole folder. From a terminal in `E:\J.A.R.V.I.S. Dashboard`:

```bash
xcopy /E /I /Y "E:\J.A.R.V.I.S. Dashboard" "F:\JARVIS"
```

Change `F:` to whatever letter the drive gets. Or just drag the folder across
in Explorer — it is the same thing.

Then run `start.bat` from the drive. Everything resolves relative to wherever
the files are, so no paths need editing.

### What travels with it

| File | Holds |
|---|---|
| `spotify_auth.json` | your Spotify tokens |
| `google_auth.json` | your Google tokens |
| `jarvis_state.json` | memories and reminders |

These are **credentials**. On a flash drive they are as safe as the drive is —
anyone who picks it up can read them. If that matters, delete those three files
before copying and reconnect on the other machine; everything else is just code.

Settings that live in the browser — API keys you typed, voice, wake word — do
**not** travel, because `localStorage` belongs to the browser rather than the
folder. You will retype those once on a new machine.

### Requirements on the other machine

- **Python** — `python.org`, tick *Add python.exe to PATH*
- **Ollama** — `ollama.com`, then `ollama signin` and
  `ollama pull gpt-oss:120b-cloud`
- **Chrome or Edge** — Firefox has no speech recognition

Nothing else. No `pip install`, no `npm install`. The only optional extra is
Pillow for screen capture:

```bash
pip install pillow
```

## Changing the port

If 8123 is taken:

```bash
python serve.py 8200
```

One catch: the Spotify and Google redirect URIs have the port baked into them.
If you change it, update the redirect URI in both consoles to match, or those
two integrations will stop connecting.

## When something is wrong

The **system log** in the right-hand rail says what happened and why. Most
faults are one of:

| Symptom | Cause |
|---|---|
| Page will not load | `start.bat` is not running, or a second copy is already on the port |
| "Model unreachable" | Ollama is not running |
| Microphone dead | Not on `localhost` — see the README |
| Search returns nothing | Keyless engines throttle; add a Google or Brave key |
| Spotify will not play | Spotify is closed, or the account is not Premium |

## Editing it yourself

Everything is plain files, no build step.

| File | Does |
|---|---|
| `index.html` | layout and settings panel |
| `style.css` | all appearance |
| `js/brain.js` | the model, the tools, **the system prompt** |
| `js/voice.js` | microphone, speech, wake word |
| `js/face.js` | the arc reactor |
| `serve.py` | the server and its API endpoints |
| `jarvis_*.py` | Spotify, Google, memory, live data |

His personality is the `DOCTRINE` array near the top of `js/brain.js`. Edit it,
reload the page, and he changes — no restart needed. Only Python changes need
the server restarting.
