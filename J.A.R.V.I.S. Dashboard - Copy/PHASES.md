# Where J.A.R.V.I.S. goes next

A menu, not a sequence. Pick by number — "build 6", "do 2 and 11" — and nothing
here depends on anything else here unless it says so.

## Already built

| | |
|---|---|
| **Phase 1** | The file layer: scaffold, write, git-backed history, revert, the preview pane |
| **Phase 2** | See-and-fix (`see_preview`), diff review before writing, run-and-read-errors |
| **Phase 3** | The closed build loop — the harness runs or looks at what he built and hands back what it found |
| **1 · Retrieval** | Facts are embedded and retrieved against what was said, in the per-turn block rather than the system prompt |
| **2 · Episodic** | Successful runs are recorded and offered as precedent when a similar request arrives |
| **3 · Consolidation** | Near-identical facts merge; anything that might be a correction is surfaced, never resolved silently |
| **5 · Standing brief** | Turns that scroll out of context are folded into a rolling brief instead of being dropped |

### Standing decision — the noise budget

**Nothing speaks unprompted.** Anything he notices queues silently until Zero
looks at it. Agreed before 11 and 12 were built; do not loosen it without
asking.

Everything below is written against the code as it stands. Effort is honest:
**an afternoon** means one sitting, **a session** means a working day,
**a project** means it will take several and should be split.

---

# A · Memory — what he knows and how he keeps it

### 1 · Retrieve memories instead of injecting them — *an afternoon*

`memories` is a flat list pasted into the system prompt on every single turn.
Fine at fifty facts. Ruinous at five hundred: thousands of tokens each turn,
attention diluted across all of them, and the prompt cache prefix churning
every time one is added.

You already have the machinery — `jarvis_recall.py` embeds and searches with
`nomic-embed-text`. Route memory through the same top-k retrieval keyed on the
current message. Cheaper *and* sharper, and it is the prerequisite for 2, 3
and 4.

### 2 · Episodic memory — let him get better with use — *a session*

Recall stores documents and conversations. It does not store **what worked**.

Log outcome-tagged episodes: *"landing page requested → scaffold web → wrote
index/style/app → looked at 390px → fixed hero overflow → user accepted."*
Retrieve on similar requests and put the shape of the successful run in front
of him before he starts.

This is the difference between an assistant with experience and one that
begins from nothing every morning. Depends on 1.

### 3 · Consolidation and contradiction — *an afternoon*

Nothing currently merges "he lives in LA" with "he moved to Long Beach". Both
sit there, both get injected, and he averages them into something false.

A pass that merges duplicates, expires stale facts, and — the valuable part —
**notices contradictions and asks** rather than silently picking one.

### 4 · Entity memory — *a project*

Promote people, projects, devices and recurring places to first-class records
with attributes, instead of loose sentences. "What did I say about the
sneaker shop?" becomes a lookup rather than a similarity search.

Real work. Do 1 and 3 first and see whether you still need it.

### 5 · Summarise instead of truncating — *a session*

`MAX_TURNS_KEPT = 40` drops the oldest turns off a cliff. In a long evening
the beginning of the conversation simply ceases to exist — including the part
where you told him what you were doing and why.

Replace the cliff with a rolling summary: fold the dropped turns into a short
standing brief that rides along in the prompt. Cheap, and it makes long
sessions coherent.

---

# B · Reasoning — how well he thinks

### 6 · A self-check with evidence — *an afternoon*

`critique()` re-reads its own prose looking for unsupported claims. It cannot
check anything. It could: re-run the code, re-look at the page, re-fetch the
figure, compare a number against the tool result that produced it.

A critique backed by evidence is worth ten from memory. The build loop already
proves the pattern works — this generalises it beyond building.

### 7 · An eval suite for the doctrine — *a session*

Unglamorous. Highest long-term value of anything on this page.

`DOCTRINE` is ~4,450 tokens of behavioural rules with **no regression
protection whatsoever**. Every edit risks silently breaking one — and you have
already been bitten: the build that claimed music was playing when it was not,
which cost real trust.

Thirty scripted turns with assertions. *Given a failed tool result, does he say
so? Given "go to sleep", does he avoid `lock_screen`? Given a rejected diff,
does he stop resending the same file?* Run it after every prompt change.

### 8 · Calibrated uncertainty — *an afternoon*

The doctrine says to separate what he knows from what he is inferring. Nothing
checks that he does. Make it structural: have him mark claims, and surface
low-confidence ones differently on screen — a different colour, not a hedge in
the prose. You would see at a glance which half of an answer to trust.

### 9 · Two models, reconciled — *a session*

For genuinely hard questions, ask two different models and have a third pass
reconcile them. Expensive per turn, so gate it behind the same `difficulty()`
heuristic that already drives fast-model routing. Good for research and
analysis, pointless for "turn it up".

### 10 · Plan before acting, visibly — *a session*

For anything multi-step he currently improvises and you watch tool cards go by
with no idea how many are left. A declared plan — five steps, ticked off as
they complete, revisable mid-run — turns an opaque process into a legible one.
Pairs naturally with 2.

---

# C · Agency — the leap from reactive to present

### 11 · Scheduled and background work — *a session*

Reminders already survive a restart. Generalise that into recurring work he
performs rather than announces: *"every morning at eight, check the calendar,
the weather and whether last night's build still passes, and have it ready."*

The infrastructure is mostly there — `jarvis_memory.py` polls for due items;
this extends it from notifications to actions.

### 12 · Watch and notice — *a project*

Give him triggers instead of only prompts. A folder gains a file, a project's
tests start failing, the calendar changes, a tracked price moves. Anything he
watches, he can mention when it matters.

**This is the biggest change in how he feels to live with** — the difference
between a tool you operate and something that is present in the room. It is
also the easiest to make annoying, so it needs a firm noise budget from the
start.

### 13 · A task queue that survives a reload — *a session*

Long jobs currently die with the tab. A durable queue with visible progress —
generalising the pattern the document indexer already uses — makes
"transcribe these forty recordings" a thing you start and walk away from.

### 27 · Design vocabulary in context — *an afternoon*

He builds on a design foundation he has never been shown. Measured on
`bean_and_brew`: 11 classes used, 2 that exist, an 18% hit rate. Put the class
list where he can see it and check the misses deterministically after a build.
Full write-up in `PROMPT-better-ui.md`.

### 28 · A house style he can be given — *a session*

Beyond "use the tokens": a short, opinionated set of rules about hierarchy,
density, contrast and restraint that rides in the prompt for build turns only.
The difference between a page that is styled and one that is designed is
judgement, and judgement can be written down.

### 29 · Reference-driven design — *a session*

Let him take a screenshot or a URL as a design brief. `see_preview` and the
vision model already exist; this points them at something you admire instead
of at his own output, and asks what to borrow. Turns "make it look better"
into "make it look more like that".

### 30 · Component memory — *a session*

When a component comes out well, keep it. A library of blocks he has built and
you approved — a hero, a pricing table, a nav — retrieved the way episodes
are. Builds compound instead of restarting.

---

# D · Perception — what he can take in

### 14 · Ambient context — *an afternoon*

He can read the foreground window and the clipboard, but only when he thinks
to. Sample them quietly and keep a short rolling note of what you have been
doing, so "how do I fix this" resolves without a round trip.

Privacy-sensitive by construction. It should be obvious when it is on and
trivial to turn off.

### 15 · Capture a region, not the desktop — *an afternoon*

`see_screen` grabs everything, then a vision model reads a thumbnail of your
whole 1920×1080. For "what does this error say" that is mostly wasted. Let him
capture the foreground window alone, or a rectangle you drag.

### 16 · Presence — *a project*

Wake when someone is in the room; stand down when nobody is. Camera or
Bluetooth proximity. Very much a wall-display feature, and the one most likely
to feel like magic.

---

# E · Building — extending Phase 2 and 3

### 17 · Plan the file set first — *a session*

He writes files one at a time, deciding as he goes, which is why the fourth
one sometimes contradicts the first. Have him declare the file list and their
responsibilities up front, then fill them in. Fewer incoherent builds.

### 18 · Write tests, then satisfy them — *a session*

`run` already supports `pytest` and `npm test`, and nothing generates tests to
run. Have him write the test first, watch it fail, then write code until it
passes — with the closed loop from Phase 3 driving the iteration. This is the
single biggest jump in *code quality* available.

### 19 · Check three widths, not one — *an afternoon*

`see_preview` takes a `width`. Render 390, 768 and 1280 in one pass and
critique the set. Most layout defects are responsive defects, and right now
they are only found if he happens to ask for a phone.

### 20 · Package what he built — *an afternoon*

A finished project has no exit. Zip it, or copy it somewhere you actually
keep things. Small, and it closes the story.

---

# F · Trust and legibility

### 21 · Show the receipts — *an afternoon*

When he asserts something, make its source clickable: which tool call, which
file, which search result. You already surface shot cards and diff cards; this
extends the principle to claims.

### 22 · Cost and budget — *an afternoon*

Tokens are counted per turn but nothing tracks spend over a session or a week,
and nothing warns before an expensive one. On a paid key that matters.

### 23 · Undo the whole turn — *an afternoon*

`revert` undoes a project one commit at a time. A single "undo what he just
did" — across every project a turn touched — is the safety net that makes the
whole file layer relaxing rather than tense.

---

# G · Infrastructure

### 24 · Route by capability, not just difficulty — *an afternoon*

`difficulty()` picks fast versus main. Route on what the turn actually
*needs*: touches private files → a local model; needs current facts → the
cloud; plain chat → the fast one.

### 25 · A context budget manager — *a session*

Nothing measures how close a turn is to the ceiling until it fails. The
system prompt alone is ~6,650 tokens with tool schemas. Measure it, show it,
and shed the least valuable material first — old tool results before recent
conversation.

### 26 · Degrade gracefully offline — *an afternoon*

Half the tools assume a network. Decide what he is when there is none, and
say so plainly instead of failing tool by tool.

---

## If you want a recommendation

**1 → 7 → 12.**

Start with **1** because it is an afternoon, it makes every later memory
feature possible, and it improves every single turn immediately.

Then **7**, because you are about to keep changing the doctrine and you
currently have no way to know when a change breaks something you already
fixed. It is the least exciting item here and the one you will be most glad
of in three months.

Then **12**, because it is the only item that changes what he *is* rather than
how well he does what he already does.
