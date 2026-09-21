# Job hunt, and languages — the prompt

Two features. Paste whichever half you want built.

---

# Part 1 · The job hunt

## What I want

A switch on the dashboard. While it is on, J.A.R.V.I.S. looks for jobs near me
and at companies I would work for, matches them against what I can actually
do, and puts the good ones in front of me: *"here is one — do you want it?"*

I keep talking to him normally the whole time. The search never blocks a
conversation and never interrupts one.

## What I verified before writing this

I probed the sources rather than assuming:

| Source | Result |
|---|---|
| **Indeed** | **HTTP 403 — blocked** |
| USAJOBS | HTTP 401 — works, needs a free key |
| Greenhouse | **HTTP 200 — open JSON, no key** |
| Ashby | **HTTP 200 — open JSON, no key** |
| Workable | **HTTP 200 — open JSON, no key** |
| Lever | documented public API |

One company's Greenhouse board returned **592 live postings** with title,
location, full description, posting date, deadline and the real apply URL.
Free, keyless, and meant to be read.

**Indeed will not work.** It returns 403 to automated requests and its terms
forbid scraping. That is not a matter of trying harder — the way past it is
CAPTCHA evasion and disguised traffic, which I am not building. LinkedIn is
the same story behind a login wall.

This turns out not to matter much, because **Indeed is an index, not the
destination.** The application itself almost always happens on Greenhouse,
Lever, Ashby, Workable or SmartRecruiters — which are open. Go to where the
jobs actually live instead of to the directory that points at them.

## The line on actually submitting

He should do everything up to the send:

**find → match → ask me → draft the answers and the letter → open the form
filled in → I press submit.**

He does not press submit. Three reasons, and the first is the real one:

1. **An application cannot be unsent.** A tailored letter addressed to the
   wrong company, a salary figure I would not have given, a fabricated year of
   experience — all of that lands in a real recruiter's inbox under my name
   and stays there. The cost of a mistake is asymmetric and permanent.
2. Most of these forms need an account, and I am not handing over passwords.
3. Several sit behind a CAPTCHA, which exists precisely to stop this.

This is not a reduction of what I asked for. I already said *"ask me if I like
this one"* — this is that gate, applied to the send as well as the shortlist.

**And nothing on an application may be invented.** If he is not certain of a
field — years of experience, a salary expectation, whether I hold a
certification — he leaves it blank and flags it. A confident wrong answer on a
job application is worse than an empty box.

## Build

### 1 · It runs beside the conversation, not inside it

This cannot live in the turn loop — that blocks, and I want to keep talking.
It is a background worker in `serve.py` with its own thread, polled by the
page. The same shape the document indexer already uses.

The toggle belongs in the topbar, next to `MODE`, so its state is always
visible. Off by default, and off means genuinely stopped.

### 2 · It reports into the inbox that already exists

Matches queue silently in the notice panel and the badge counts up. **The
standing decision holds: nothing speaks unprompted.** A job he found while I
was mid-sentence waits until I look.

The inbox grows a job card — title, company, location, pay if published, match
score and why — with **Interested / Pass / Open** on it. Passing teaches it;
see 5.

### 3 · Where it looks

- A **watchlist of companies** I name, resolved to their ATS board and polled.
  This is the highest-signal source by a distance.
- Keyword and location search across those boards.
- USAJOBS for public sector, with the free key.
- Optionally an aggregator with a real API — Adzuna and Jooble both have one —
  for broad local coverage.

Polling is gentle: a board every few minutes at most, `updated_at` respected,
nothing hammered. Anything already seen is skipped.

### 4 · Matching

He has `nomic-embed-text` and a working retrieval layer already. Embed my
profile against the posting and score it, then have the model explain the
score in one line. Hard filters first — location, remote, work authorisation,
salary floor — because a 92% match I am not eligible for is noise.

Needs a **profile**: my CV, skills, the floor, where I will work, what I will
not do. Stored locally, editable in Configuration, never sent anywhere except
into an application I approved.

### 5 · It learns what I turn down

Every Pass is a labelled example. Store it like an episode and use it to
re-rank. If I pass on six night-shift roles he should stop showing me the
seventh, without being told.

### 6 · The packet

On Interested, he prepares: a cover letter tailored to that posting, answers
to the common screening questions drawn only from my profile, and the
resume variant that fits. Saved as a folder under `JarvisProjects` so it is
reviewable, diffable and revertible like anything else he writes.

Then he opens the real application page in my browser — the `/api/open` path
already does this — with the packet on screen to paste from, or pre-filled
where the form allows it.

## Done when

- The toggle turns it on and off, and I can hold a full conversation with him
  while it runs.
- Nothing it finds ever speaks, toasts or interrupts.
- A match arrives in the inbox with a real apply URL that opens the actual
  posting.
- Interested produces a letter that quotes something specific from the posting
  and contains no fact that is not in my profile.
- Passing three similar roles visibly changes what is offered next.
- Off means no network requests at all.

---

# Part 2 · Languages

## What I want

Two things:

- **Translate.** "Read my screen and translate it." Also what I paste, and
  what I say.
- **Teach.** *"Teach me Spanish today"* — a real lesson, picking up where the
  last one stopped, that remembers what I already know.
- **ASL too**, with real pictures.

## Build

### 1 · Translate

`see_screen` already captures and sends to a vision model. Give it a translate
mode: return the original and the translation side by side, and say what
language it found rather than assuming.

Extend to the clipboard — he can already read it — and to speech, so a phrase
said at him comes back in English.

Do the translation with the model already in the conversation. It is good at
this and it costs nothing extra.

### 2 · Teach

The interesting half, and the infrastructure is already here.

- A **lesson** is a short spoken exchange, not a wall of vocabulary. Ten
  minutes, a handful of new items, used in sentences.
- **Spaced repetition** on top of the memory layer that already exists: each
  item gets a next-due date, and a lesson opens by testing what has come due.
  Getting it right pushes it further out; getting it wrong pulls it in.
- He is **speaking to me and I am speaking back** — this system is voice-first
  and that is the natural way to drill pronunciation. Use it.
- Track a real streak and a real count. "You know 240 words" should be true.

### 3 · ASL — and one thing to be honest about up front

**Most signs are movement, and a photograph cannot show movement.** A still of
a handshape is genuinely useful for fingerspelling and for a few static signs,
and genuinely misleading for the rest — the difference between many sign pairs
is direction or repetition, which a photo simply does not carry.

So build it in that order:

- **Fingerspelling first.** The 26 handshapes are static, photographs work,
  and it is the foundation everything else assumes you have.
- **Vocabulary with motion described in words** alongside the still, until
  there is something better.
- **Video or animation** for real signs, which needs a source. Bundle a small
  set with a licence that permits it, or link out to an established ASL
  dictionary rather than pretending a still is enough.

Do not ship photographs scraped from wherever and call it a course. And say
plainly in the interface that ASL has its own grammar and is not signed
English — teaching it as English-word flashcards teaches something that is not
the language.

## Done when

- "Read my screen and translate" returns the original and the translation, and
  names the language it detected.
- "Teach me Spanish today" starts where the last lesson ended and opens by
  testing what is due.
- Getting something wrong makes it come back sooner, and I can see that it did.
- The word count and the streak are counts of real things.
- Fingerspelling works with real images; anything motion-based says so rather
  than showing a still and hoping.
