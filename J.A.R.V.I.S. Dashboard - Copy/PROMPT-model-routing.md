# Gemini 3 Flash, and routing by size — the prompt

Paste this to start the work.

---

## What I want

Gemini 3 Flash in the brain core alongside Claude, and J.A.R.V.I.S. choosing
between them **per request**, by himself. A small ask should not cost a big
model. A hard one should never be answered by a small one.

Every existing feature stays exactly as it is. This adds to the core; it does
not rearrange it.

## Do not build what is already there

Two-tier routing already exists and works. Read it before writing anything.

| | |
|---|---|
| `difficulty(text)` — `brain.js:1975` | binary `simple` / `hard`. Two regexes — `TOOLISH`, `THINKY` — plus four heuristics: length > 220, two question marks, `history.length > 8`, and a 120-char floor |
| `pickModel(text)` — `brain.js:1991` | routes down to `J.settings.fastModel`, logs it, emits `routed` |
| escalation — `brain.js:2691` | a fast turn that reaches for a tool is escalated to the main model automatically |
| `visionRoute()` — `brain.js:940` | **the important one.** Pins one model to a *named saved connection* — its own base URL, its own key, local-proxy aware |

The routing brain is done. What is missing is the ability to route to a model
on a **different provider**, and more than two tiers.

## Why this is not "add a third model id"

`pickModel` swaps the model *name* and nothing else. The request still goes
out on whatever transport `J.settings.provider` names — one global setting,
`anthropic` or `openai`.

The core is currently Claude Opus 5 on the Anthropic transport. Gemini speaks
the OpenAI dialect. So routing down to Flash is **a change of transport, not a
change of name**, and `fastModel` as it stands cannot express that.

The answer is already written, for a different model. `visionRoute()` exists
because — in its own comment — *"the vision model is very often on a different
service from the one driving the conversation."* That is exactly this problem,
solved, ten months ago. Generalise it rather than inventing a second mechanism.

## The measurement that says this will work, and the one that says prove it

The README is blunt about the last attempt:

> **Measured on this setup, it is not worth using.** Against a cloud main model
> that already answers in 2.7s, a warm local 3B saves 0.3s — and costs 6s the
> first time, while it loads into VRAM. It pays off only when the main model is
> genuinely slow, or when the fast model is hosted somewhere quick.

Flash is hosted somewhere quick. That is precisely the case the measurement
carved out — so the instinct is sound. **That is a reason to measure again, not
a reason to skip measuring.** Two numbers before any of this is called done:
median latency per tier, and how often the router picks wrong.

**The cost nobody counts:** the system prompt and tool schemas sit in one
cached block, with a second breakpoint on the newest settled assistant turn.
Route a turn to another provider and it gets **no cache hit at all** — it pays
full prefix cost, currently ~6,650 tokens of system prompt and schemas. On a
two-word turn that can cost more than the big model would have. Measure whether
routing down is still a win once the lost cache is on the bill. If it is not,
say so and stop — a measured no is a real result.

## Build

### 1 · Generalise the connection route

Lift `visionRoute()` into `route(connName)` — resolve a saved connection by
name to base URL, key and local-proxy headers; fall back to the current
provider when the name is empty or missing. `visionRoute()` becomes
`route(J.settings.visionConn)` and must behave identically afterwards.

### 2 · Give the fast model a connection, exactly as vision has one

Add `fastConn` beside `fastModel`, mirroring `visionConn` / `visionModel`:
same picker, same fallback, same Configuration layout. A model id with no
connection keeps working the way it does today.

### 3 · Get the real model id from the provider

The README's own rule: **"a name copied from documentation — including the
default in this repo — is a guess."** Press **List models** against the Gemini
compatible endpoint and take the id it actually serves. Do not hardcode one,
and do not take one from this file.

### 4 · Widen the tiers, keeping the bias

Two tiers become three at most: **fast** for plain conversation, **main** for
reasoning and tools, and the existing failover chain underneath. Keep the
existing bias exactly as written in the comment at `brain.js:1962` — *a wrong
route downward is a bad answer, which is expensive; a wrong route upward is a
couple of seconds, which is not.* Do not soften `TOOLISH` or `THINKY` to send
more traffic to Flash. If the tiers do not earn their keep, the answer is fewer
tiers, not a looser classifier.

### 5 · Show which model answered

`routed` is already emitted and the top bar already carries a `CORE` chip.
Whatever answered should be visible on the turn without opening the log. If a
turn was escalated, that should be visible too — an escalation is the router
admitting it was wrong, and those are the ones worth seeing.

### 6 · Measure, then write the numbers down

Twenty turns across the range — "turn it up", "what's the weather", "why is
this failing", "scaffold a landing page". Record per tier: median latency,
cache hit or miss, and every misroute. Put the table in the README the way the
last routing measurement was, including if the answer is that it is not worth
it.

## Constraints

- **No behaviour changes anywhere else.** Failover, self-check, the build
  loop, vision, jobs, lessons, the doctrine — all untouched.
- No new dependencies, no build step, no framework. Classic scripts on
  `window.J`, modules talking through `J.on` / `J.emit`.
- Nothing speaks unprompted. A route is a log line and a chip, never a remark.
- The failover chain must still walk saved connections when a provider
  exhausts, and must not be confused by a turn that routed away from the main
  provider.
- Keys stay where they already live. Nothing new in `localStorage` that is not
  already there.

## A warning about "every feature stays the same"

There is no way to verify that claim today. `DOCTRINE` is ~4,450 tokens of
behavioural rules with no regression protection whatsoever, and this change
touches the path every single turn takes.

So either do **PHASES 7** — the eval suite, thirty scripted turns with
assertions — before this, or accept that "nothing else changed" is an opinion
rather than a fact. If it is going to be an opinion, say so plainly rather than
asserting the stronger thing.

## Done when

- A plainly conversational turn is answered by Flash, and the interface says so
  without opening the log.
- A turn mentioning a tool, asking why or how, or running long goes to the main
  model — with the same classifier, not a loosened one.
- A fast turn that reaches for a tool still escalates, exactly as it does now.
- Vision still works, on its own connection, unchanged.
- Failover still walks the chain when a provider exhausts.
- The measured latency per tier is in the README, cache cost included — and if
  the numbers say routing is not worth it, that is written down too.
