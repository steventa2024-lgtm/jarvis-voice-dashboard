# Live cards — the prompt

Paste this to start the work.

---

## What I want

When I ask J.A.R.V.I.S. something with a **shape** to the answer — a score, a
game, a price, a forecast, a meeting, a track — I want the data to arrive as a
**floating card that animates in**, not only as a paragraph he speaks.

The case that prompted this: I asked whether the Atlanta Braves were playing.
I got a sentence. I wanted a card — teams, score or start time, inning or
status, where it is on. The sentence is still fine; it is what he says out
loud. The card is what I look at.

## What actually happened, from the screenshot

The system log for that one question:

```
16:50:16  Read:   espn.com/mlb/game/_/gameId/401816669
16:50:17  Tool:   web_search {"query":"Atlanta Braves August 25 2026 schedule"}
16:50:19  Searched: 6 hits via duckduckgo
16:50:19  Tool:   web_fetch baseball-reference.com/teams/ATL/2026-schedule-scores
16:50:20  Read:   2026 Atlanta Braves Schedule
16:50:20  Tool:   web_search {"query":"Atlanta Braves August 25 2026 game schedule"}
```

**WEB LOOKUPS: 12.** For one question with a single factual answer. The
doctrine says *"Two searches is a lot. Four is a bug."* — this was three times
that, and the request was aborted before it finished.

What twelve lookups bought:

> The Braves have a game today (August 25 2026) against the Los Angeles
> Dodgers at Truist Park.

No start time. No channel. No score. The thing that would make a card worth
looking at is exactly the thing all that searching failed to produce — because
scraping a schedule page for prose is a bad way to get four structured fields.

So the sports source is not a nicety on top of the card. It is what makes the
card have anything in it.

### And the pop-ups you already have look like this

Nine task chips stacked down the stage — `reading a page…` five times,
`searching the web…` four times. That is a bug, not volume:

`taskStart()` dedupes by name, but only against tasks still **running**.
`taskEnd()` removes the entry from `liveTasks` immediately and then leaves the
element on screen for another 1100–3540ms to play its retire animation. Any
new call with the same name inside that window finds an empty map and builds a
second chip. Twelve rapid lookups produce a wall.

Fix that first. It is the existing pop-up animation, it is the most visible
motion on the screen, and no amount of new card design compensates for it.

- A retiring chip must be reused if the same task restarts before it is gone.
- Cap the visible stack regardless, and collapse repeats to `searching ×4`.

### There is an obvious place to put a card

The stage is nearly empty — one line of reply at the bottom, the orb above,
and several hundred pixels of nothing between them. That gap is where a card
belongs. It does not need to overlap anything.

## Why the current design cannot do it

Three things are in the way, and only fixing all three gets the result:

1. **There is no sports source.** `lookup` serves `stocks, currency, crypto,
   wikipedia, news, tech_news, tv_tonight, daylight`. A question about a game
   falls through to `web_search`, which returns prose by construction.

2. **Cards are reverse-engineered from English.** `resultCard()` in `app.js`
   rebuilds a card by regex-matching the same sentence the model reads —
   `/\([A-Z^.]{1,6}\)\s[\d,]+\.\d/` and friends. Reword a source and the card
   silently stops appearing, with nothing to tell you it did.

3. **Only two tools emit cards at all** — `lookup` and `spotify`. Weather,
   calendar, mail, files, recall, the build loop: none. And the cards that do
   appear are inline in the transcript, static, arriving after the text.

## Build

### 1 · Structured cards, not parsed prose

Tools return a `card` object **alongside** `summary`, and the interface renders
that. The model keeps reading `summary`; the screen stops guessing.

```
{ ok: true,
  summary: "Braves play the Mets tonight at 7:20pm on FS1.",
  card: {
    kind: "game",
    title: "Braves at Mets",
    subtitle: "MLB · tonight",
    rows:  [ {k: "First pitch", v: "7:20 pm"},
             {k: "Coverage",    v: "FS1"} ],
    accent: "live" | "ok" | "warn" | null,
    note: "Times are local."
  } }
```

Keep the existing regex parsers as a fallback for anything not yet emitting a
card, so nothing that works today stops working.

### 2 · A floating card layer

Cards animate into a layer over the stage rather than appending inline.

- Enter with real motion — rise and settle, not a fade. Stagger the rows.
- **Auto-retire** after ~20s, unless hovered or pinned.
- Click to **pin**; pinned cards stay until dismissed.
- Every card also drops into the transcript when it retires, so nothing is
  lost by not looking up in time.
- Honour `prefers-reduced-motion`: no movement, just appear.
- Never cover the composer.

### 3 · Card kinds

Start with these, all sharing one layout with different accents:

| kind | For |
|---|---|
| `game` | a fixture, live score, or result |
| `quote` | stocks, crypto, currency |
| `forecast` | weather beyond the rail's snapshot |
| `agenda` | the next meeting or two |
| `track` | what is playing |
| `stat` | anything else that is a small set of labelled numbers |

### 4 · A sports source in `lookup`

Add `sports`. ESPN's public JSON endpoints need no key and cover MLB, NFL, NBA,
NHL and the major football leagues. It should answer three shapes: *is X
playing*, *what was the score*, and *what is on today*.

## Constraints

- **No new dependencies**, no build step. Plain CSS animation and one small
  JS module, like everything else here.
- **The card never replaces what he says.** If the card is on screen and he is
  silent, the feature has failed.
- **A card is never a guess.** It is rendered from tool data or it does not
  appear. If a field is unknown, omit the row — do not print "—" and hope.
- Cards appear in response to something I asked. This is not the notice queue
  and it does not touch the noise budget.

## Done when

- Asking about a Braves game produces a floating card with the fixture, and he
  still says the sentence.
- That question costs **one** lookup, not twelve, and the card carries the
  start time and the channel — the fields the prose answer never found.
- Firing twelve tool calls in ten seconds produces one chip per kind with a
  repeat count, never a stack of nine.
- Asking about NVDA produces a quote card from structured data, not regex.
- Turning on reduced motion removes the movement and keeps the card.
- Unplugging the sports source degrades to prose with no error and no empty
  card.
- A card left alone retires itself and is findable in the transcript
  afterwards.
