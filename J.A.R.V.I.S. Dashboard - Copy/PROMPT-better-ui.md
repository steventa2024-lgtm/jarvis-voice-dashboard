# Better websites and UI — the prompt

Paste this to start the work.

---

## Status — 2026-08-28

Items **1, 2, 3 and 4 are built and verified**. Item 5 is not started.

The foundation gained the five components that were genuinely absent —
`.hero` `.hero-content`, `.brand` `.brand-mark`, `.nav-toggle` with a
collapsing `.site-nav`, and `.media` `.placeholder` drawn in CSS so an image
placeholder cannot 404. `base.js` wires the toggle. Verified at 1280 and 390
through the project's own headless render, and verified to degrade correctly:
strip `data-nav` and every link is visible again, so a page whose script never
loads does not hide its navigation behind a dead button.

The whole class vocabulary now rides in the doctrine — all 34 names, not the
six it used to list — together with the two decisions that are always his
(`--accent`, and `.placeholder` instead of a dead placeholder service).

`files.check` is the deterministic pass: it diffs the classes in the markup
against the classes the page actually links, resolves every `<script src>`,
`<link href>` and `<img src>`, verifies remote images over the network with a
bounded budget, and flags an accent left at the default. No model in it. It
runs in `inspectBuild` **before** the vision pass and, unlike the old check,
runs whether or not a vision model is set — that early return meant a machine
with no vision model was never checking a build at all.

Replayed on the original evidence:

```
bean_and_brew   14 problems   9 undefined classes (.btn-primary → .btn?,
                              .nav-links → .site-nav?), script.js missing,
                              4 dead placeholder images, default --accent
resume          54 problems   every foundation class it uses, undefined
_selftest        0 problems   a freshly scaffolded site, 4 pages, clean
```

`resume` is the shrink disaster made visible: `style.css` is 18 lines where
the foundation is 267, so all four pages reference a stylesheet that no longer
defines anything. Full 4-page check costs 0.21s.

**The shrink guard is in.** `write` refuses any change that cuts an existing
file of 30+ lines below 40% of both its lines and its bytes, and `force` is
not reachable from the model — only `apply_change`, after a human has approved
the diff, sets it. When review is switched off a refused write falls through
to the proposal path rather than failing, so the one case that has already
cost real work gets a person's eyes even when review is off. The diff card
also says in words what `+13/−262` was hiding, because both disasters were
approved from a card showing exactly those numbers.

Not verified: a live model turn going through any of this. The endpoint, the
guard, the card and the components were each exercised directly. What has not
been watched is the model receiving a check report and fixing what it names —
which is the thing item 5 would judge.

---

## What I want

Sites he builds should look designed. Right now they look like a first draft
with the stylesheet missing.

## The evidence, not the impression

He built `bean_and_brew` from the `site` template. The template worked: four
pages, `base.js`, and the full design foundation copied in. Then he wrote
`index.html` himself, and here is what he wrote:

```html
<header class="hero">
  <nav class="nav">
    <div class="logo">Bean & Brew</div>
    <ul class="nav-links">…</ul>
    <div class="nav-toggle">☰</div>
  </nav>
  <div class="hero-content">
    <h1>Step Into the Future of Coffee</h1>
    <a href="#menu" class="btn-primary">Explore Menu</a>
```

Measured against the stylesheet sitting in the same folder:

| | |
|---|---|
| Classes used | 11 |
| Classes that **exist** in `style.css` | 2 — `.grid`, `.card` |
| **Hit rate** | **18%** |
| Missing | `hero` `hero-content` `nav` `nav-links` `nav-toggle` `logo` `btn-primary` `about` `contact` |

So nine of eleven class names are invented. Those elements fall through to bare
browser defaults — no page gutter, no type scale, no spacing, no components.
That is the entire reason the page looks unstyled. **The foundation is not
being ignored on purpose; he does not know what is in it.**

Three more faults in the same file:

- **Every image is broken.** All four point at `via.placeholder.com`, which is
  dead — `HTTP 000`, the connection does not open. He cannot see this, because
  a broken image is invisible to a description of a screenshot.
- **`<script src="script.js">` does not exist.** The template ships `base.js`.
  A 404 on every page load.
- **`--accent` was never changed** from the default blue, on a coffee site.
  One line would have retinted the entire page.

## Why it happens

The doctrine tells him to use the foundation. It never tells him **what is in
it**. To find out he would have to read 10KB of CSS, which he does not do
before writing markup — so he invents plausible class names instead, and
plausible is not the same as present.

Nothing catches it afterwards either. The build check renders the page and
asks a vision model how it looks; a page of unstyled defaults reads as "plain"
rather than "the stylesheet is not being used", so the report is vague and no
fix follows.

## Build

### 1 · Put the vocabulary in front of him

He cannot use what he has not been told exists. A compact reference — the
class list with one line each — belongs where he will actually see it: the
tool description, or a `capabilities`-style action that returns it.

```
container narrow · stack stack-lg · section · grid grid-2 · cluster between
center · card card-hover · btn btn-ghost · field · tag · eyebrow lead muted
site-header site-nav site-footer · reveal stagger on-scroll
```

Cheapest fix on this page and probably the largest single improvement.

### 2 · Check the classes, not just the looks

The harness already renders the page after a build. Have it also diff the
classes used in the markup against the classes defined in the CSS, and report
the misses by name:

```
You used .hero, .nav-links, .btn-primary — none of these exist in style.css.
Either use the foundation's classes or define yours.
```

That is a deterministic check with no model in the loop, and it would have
caught this build outright. Same pass should flag `<script src>` and
`<link href>` pointing at files that are not in the project.

### 3 · Images that are not broken

Ban `via.placeholder.com` — it is dead, and it is the default reach for every
model trained before it went. Give him something that works:

- inline SVG placeholders, generated locally, no network at all — preferred,
  since a built page should not depend on a third party to look finished
- or `picsum.photos`, which is alive, if a real photograph matters

Whichever, the build check should fail a page whose images do not load. A
broken image is invisible in a vision description and obvious to a human.

### 4 · Make the design decisions that are actually his

He is allowed to choose, and should:

- **Set `--accent`** to something the subject warrants. A coffee shop is not
  `#4f7cff`.
- Choose the type pairing, the density, and what to cut.
- Replace the placeholder copy rather than shipping it.

Say so explicitly, because "use the foundation" currently reads to him as
"leave everything at its default".

### 5 · Then judge it properly

Once the page is actually using the system, the critique can be about design
rather than about missing CSS. Ask for the things that separate a designed
page from a styled one: is there one clear focal point, is the vertical rhythm
consistent, is there enough contrast between sections, does the eye know where
to go second.

## Constraints

- No new dependencies, no build step.
- The foundation is meant to be **edited**, not treated as a framework. If a
  page needs a component that is not there, he should add it to `style.css`
  properly — tokens, not hardcoded pixels — rather than inventing a class and
  leaving it undefined.

## Done when

- A freshly built page uses the foundation for **at least 80%** of its
  classes, and any new class it introduces is actually defined in `style.css`.
- No page ships an image that fails to load, and no page references a file
  that is not in the project.
- `--accent` is set deliberately on every build.
- Rebuilding `bean_and_brew` from the same request produces a page that looks
  designed at 1280 and at 390.
