# Job hunt — the prompt

Paste this to work on the hunt. Rewritten 2026-08-28 after the first version
under-delivered.

## Why the first version missed

The original opened with "I already have a fully functional local AI assistant"
and then specified all five features from scratch. That reads as greenfield, so
four things got designed a second time:

| Specified as new | Already existed |
|---|---|
| `seen_jobs.json` for dedup | `jarvis_jobs.json` → `seen[]`, 2,939 ids |
| `resume_profile.json` | the `profile` block in the same file |
| an Ollama match engine | `score_job()` embeddings **and** `analyse()` |
| `browser-use` fill-and-stop | `prepare()`, verified: 5 filled, 3 refused |

It also named `python-jobspy`, `browser-use`, `langchain_ollama` and
`llama3.1:8b` / `qwen2.5:14b` — none of which were installed or pulled. A spec
that names tools without checking they are present sends the work down a path
that cannot run.

And the biggest one: it never said *where the skill plugs in*. So a module was
written and left unwired, and J.A.R.V.I.S. answered "start my job search" with
a web search and an open browser tab, because that was the only door he had.

**A capability that is not registered does not exist.** State the wiring or it
will not happen.

---

## The prompt

> Act as my senior Python engineer on J.A.R.V.I.S., the dashboard in
> `E:\J.A.R.V.I.S. Dashboard`. Read `PROMPT-continue.md` first — the standing
> decisions there are binding.
>
> **What already exists, which you must reuse rather than rebuild:**
>
> - `jarvis_jobs.py` + `jarvis_jobs.json` — the store. `profile` is my resume
>   and applicant details; `seen[]` is every listing I have already rejected or
>   applied to; `jobs[]` is what is live. `score_job()` scores by embedding.
> - `jarvis_apply.py` — `shortlist`, `analyse`, `tailor`, `prepare`. `prepare()`
>   fills a form with Playwright and stops. Its submit refusal is deterministic
>   and verified against a live form. **Do not replace this.**
> - `_ask(model, system, user)` in `jarvis_apply.py` — one call to Ollama. Use
>   it rather than adding langchain.
> - Modules expose `command(action, **kw)`, routed by `serve.py` as
>   `/api/<name>/command`, surfaced to the model as a tool in `js/brain.js`.
>
> **The gap:** the watched boards are Greenhouse/Ashby/Lever/Workable —
> engineering ATSs. My profile targets barista and food service work. They will
> never carry it.
>
> **Build a `job_hunt_skill.py` that:**
>
> 1. Scrapes LinkedIn, Indeed, Google Jobs and ZipRecruiter with
>    `python-jobspy`, driven by the profile's `titles` and `locations`.
> 2. Drops anything already in `seen[]`. New rows join the same store — one
>    dedup list, not two.
> 3. Ranks cheaply with the local embedding, then scores only the survivors
>    with a model. Ollama Cloud is capped; do not spend it on obvious misses.
> 4. Offers three at a time with match percentages, takes "1, 2, 3, or decline
>    all", and on a decline banks those three and fetches three more ten
>    minutes later. No two listings in one batch may read as the same job.
> 5. Hands a chosen job to `jarvis_apply.prepare()`. Keep the engine behind one
>    swappable name so it can be changed later. Nothing presses submit, ever.
>
> **Wire it in, in the same turn:** the `serve.py` import, the capability flag
> in `/api/health`, the route, and the tool in `js/brain.js` — including a
> description blunt enough to beat `web_search` for "start my job search", and
> an edit to the existing `jobs` tool so it stops absorbing hourly-work intent.
> Restart `serve.py`; it does not hot-reload.
>
> **Verify before you tell me it works.** Run a live search. Show me the rows,
> the scores, and the three that come back. Benchmark the scoring model against
> at least one job I am obviously right for and one I am obviously wrong for —
> if the spread is flat, the model is wrong and the ranking is meaningless.
> Never write to `jarvis_jobs.json` during testing; copy it.
>
> Check what is installed before you specify it. If something needs pulling,
> tell me and stop.

---

## Done when

- "Start my job search" reaches `job_hunt`, not `web_search`, not a browser tab.
- Three real listings come back with spread scores and a numbered prompt.
- Declining banks all three and the next batch is different.
- Choosing one opens its form, filled, with nothing submitted.
- `jarvis_jobs.json` has exactly one dedup list in it.

## Status — 2026-08-28

All built and wired. Measured live: 80 barista listings for Los Angeles in 24s
(ZipRecruiter 403s and is skipped; Google Jobs returned nothing). `start`
returns a scored batch of three in ~55-60s.

Scoring model chosen by benchmark, not by preference — see the comment block at
the top of `job_hunt_skill.py`. `phi3:mini` returned 30/30/30 for two barista
jobs and a Kubernetes job; `llama3.2:3b` scored two near-identical barista jobs
80 and 40. `gpt-oss:120b-cloud` returned 92/85/5 and is the default, with
`llama3.2:3b` as the offline fallback.

### The apply chain — four bugs found by running it

`choose()` reached `prepare()` and did nothing useful. All four were only
visible by running it, and three predate this work:

1. **`prepare()` never read form labels.** It looked at `aria-label`,
   `placeholder`, `name` and `id` only, so `<label><span>First name</span>
   <input name="fn">` reported as `"fn"` and matched nothing in `FIELD_MAP`.
   Zero of six fields filled; six of six after.
2. **The browser closed on return.** `prepare` returned from inside
   `with sync_playwright()`, which tore the driver down and took the window
   with it — the form was filled and destroyed before anyone could read it.
   The driver is now held in `_OPEN` (cap 3) with a `close_forms` action.
3. **It opened Playwright's bundled Chrome on a blank profile.** Every real
   application is behind a login, and a fresh automated profile is refused
   one — so `indeed.com/viewjob` bounced to `secure.indeed.com/auth` and the
   form was never reachable. Now real Edge with a persistent profile at
   `~/JarvisProjects/.apply-browser`. **Sign in once there and it sticks** —
   verified across a full browser restart.
4. **`DRAFTER` was `jarvis-r1:8b`, which timed out at 182s** and so could not
   produce the packet `prepare` requires. `gpt-oss:120b-cloud` does it in 13s.

`_unsupported()` was also flagging `energetic, proven, strong, core, fast,
paced` on an honest letter — a warning nobody reads is not a guarantee. Now 1
flag instead of 12, still catching fabricated employers, schools, credentials
and figures (`6 years`, `10 years`, `47%`, ServSafe, AWS, Stanford).

`form-test.html` in the dashboard root is the fixture all of this was measured
against, so the chain can be re-checked without touching a real employer.

Still not done: a run against a live posting. Real forms are messier than the
fixture, and Indeed in particular may not expose its fields until signed in.
