# J.A.R.V.I.S. Phase 1: Agent Core

Phase 1 adds mission state around the existing browser brain. It does not add
another tool executor, tool definitions, backend routes, or execution permissions.
`js/agent.js` is a classic script loaded immediately after `core.js`.

## Flow and integration

User objective → conservative detection → structured planner → mission state →
existing brain/tool loop → actual tool evidence → verifier → receipt.

Simple chat, time/weather questions, memory requests, and opening Spotify bypass
planning. Dependent project/build/repair work, file organization, and research
with comparison normally qualify. Detection is deterministic; ordinary turns
have no additional model call. The heuristic is intentionally conservative.

The planner and verifier use the brain's current model, transport and failover.
They are quiet, tool-free requests. Their text and reasoning never reach the
transcript or speech. Plans contain outcome titles and verification criteria,
not private reasoning. A plan has 2–8 steps normally, at most 10; ten requires a
justification. Unknown fields, oversized text, invalid kinds and malformed JSON
are rejected. Duplicate or malformed IDs are normalized. The runtime retains
the trusted user objective rather than a model rewrite.

`brain.js` supplies narrow hooks for preparation, compact context, bounded
batches, dispatch checks, tool results, existing build-check metadata, verifier
requests, final receipts and cancellation. Existing provider defaults, tool
schemas, history limits and `MAX_TOOL_HOPS = 12` remain intact. The three-lane
limit remains; independent observations can run together, while changes and
validation are serial and keep their original dependency order.

## State and evidence

Mission states: `planning`, `ready`, `executing`, `verifying`, `waiting`,
`waiting_approval`, `blocked`, `completed`, `failed`, `cancelled`.

Step states: `pending`, `running`, `verifying`, `completed`, `failed`, `blocked`,
`skipped`. This phase does not automatically skip required work.

A completion proposal must reference actual evidence IDs and a concise result.
The runtime verifies references, observed success, step order and evidence kind.
A repair requires actual change evidence and a later successful validation in
the same project. Sequence numbers prevent a check performed before a write
from satisfying it, including actions that share a timestamp. Existing runner
exit codes, timeout state and HTML check problem/page counts are read directly.
A nonzero exit proves only a step explicitly requiring failure reproduction;
it cannot prove a successful repair or passing tests. A write acknowledgment
alone never completes a repair. Semantic adequacy against the stated criterion
is assessed by the model, with these deterministic gates as a safety net.

Evidence stores bounded, redacted summaries, IDs/references and selected metadata,
not repeated full outputs. At most 60 evidence items, 12 failures, 6 revisions,
and 4 corrections are retained. Completed-step evidence is retained preferentially.
Mission prompt context includes bounded recent observations and remaining outcomes.
Web/file/tool observations are marked as untrusted data, never policy.

The existing `inspectBuild()` check remains the only automatic build check.
Its `files:run`/`files:check` results are observed by the agent. Existing server
web-result events are also recognized. No new verification backend is added.
Existing episode recording receives compact mission status; unfinished missions
are not classified as successful precedents. Existing memory storage and its
anti-thrash rules remain unchanged.

## Recovery, interruption and review

There are at most two retries after an initial failed step attempt. Identical
failed arguments are refused unless a subsequent actual change in the same
project makes revalidation meaningful. Failure signatures survive replanning;
continuation cannot erase the retry limit. Materially changed observations may
produce a bounded replan. Verified completed steps are preserved.

“Stop”, “cancel that”, “never mind” and “abort”, plus the existing stop button,
cancel the mission and abort the current cancellable model request. No further
planned actions are dispatched. A tool already running without cancellation
support may finish; Phase 1 cannot roll back its effects. “Stop speaking” silences
speech without cancelling the mission. Voice recognition, wake word and barge-in
implementations are unchanged.

Corrections such as “No, don't modify that file” or “Use Python instead” pause
dispatch, abort the current model request, and revise pending work while retaining
verified steps. Existing busy-turn queuing carries the correction forward.

`diff-review` and `review-result` from the existing approval UI drive
`waiting_approval`. Only that UI can approve. Rejection, timeout or cancellation
blocks the mission and does not apply the proposed change.

`J.save('mission', ...)` persists a single lightweight mission. Reload restores
nonterminal missions as `waiting`, without dispatch. Explicit “continue mission”
or the card button is required. Clearing conversation cancels and drops this
mission snapshot. There is no durable task database or background resumption.

## Events, UI and diagnostics

The existing bus emits `mission:update` snapshots plus lifecycle events:
`mission:start`, `mission:plan`, `mission:step`, `mission:evidence`,
`mission:replan`, `mission:waiting`, `mission:complete`, `mission:failed`,
`mission:cancelled`. The compact transcript card listens to state events only.
It shows titles, text/symbol statuses, completion count, outcome and controls.
The final receipt lists actual changed paths and verified result summaries.
Rendering uses text nodes and bounded lists. No animation-frame loop is added.
Reactor rendering, geometry and animations are not modified or extended.

`J.agent.diagnostics()` returns ID, state, step/count, retries and last update.
Existing logs get status changes only, never raw mission prompts or credentials.

## Validation

Run from the repository root:

```text
node --test tests/agent-core.test.cjs
py -3.12 tests/phase1-regression.py
```

The regression runner exports the immutable Phase 0 safety SHA to a temporary
baseline. It checks all 17 existing Python files plus itself, all 16 browser JS
files, two CJS harnesses, 8 inline scripts, 25 original HTTP routes and offline
Pixabay behavior. Only the four intended existing frontend files may differ;
unchanged HTTP route statuses, cache headers and content types are required.

For native integration, start the existing server on an isolated test port:

```text
py -3.12 serve.py 18762 --no-open
node tests/agent-browser.cjs
```

Start the server from the dashboard folder; run the harness from the repository
root. The harness requires Playwright and Chromium. Set `PLAYWRIGHT_MODULE` to a
Playwright module path if needed, `JARVIS_TEST_URL` for another localhost port,
and optionally `JARVIS_TEST_ARTIFACTS` for screenshots/results. It configures the
server's existing file root to a newly created temporary fixture folder: use a
dedicated test server, not a dashboard currently doing real work. Provider SSE
responses and desktop launching are controlled fixtures; file reads, proposals,
review application and Python runs are real. No live paid model or account action
is required. Width checks cover 390, 768, 1280 and 1920 pixels in Chromium using
the app's existing reduced-motion behavior for stable geometry comparisons.
Mobile conversation content follows the existing document flow.

## Limits

Model-generated plans/verdicts can be inadequate; invalid or unsupported results
fail or leave work waiting/blocked rather than claiming completion. Validation
checks prove the observed result, not arbitrary semantic correctness. General
non-file changes do not yet have specialized deterministic verification adapters.
Known tool-less routes receive an informational blocked plan; capability discovery
is limited to existing routing metadata, and unknown incompatible providers may
return errors. Live provider accounts, physical microphone/speakers, Google,
Spotify accounts, and real screen vision require separate user-environment testing.
No future scheduled-task engine, Permission Broker, MCP, multi-agent system,
browser operator, framework migration or dashboard redesign is included.
