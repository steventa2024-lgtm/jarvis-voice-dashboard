# Mk VIII Phase 3: central Permission Broker

The existing tool dispatcher and HTTP handlers ask one deterministic Python
authority before consequential work. No model call evaluates permissions, and
no model-visible tool can manage policy or resolve approvals.

`tool → classification → policy → ALLOW / ASK / DENY → existing execution`

## Enforcement and integration

`jarvis_permissions.py` owns capability mappings, risk, precedence, grants,
approval state, receipts and bounded audit history. `serve.py` supplies actual
resource resolution and invokes the broker before protected POST handlers and
read routes. An unauthorized direct HTTP write also fails. Existing file roots,
path checks, fixed runner names, shrink guard and exact diff review still apply.

`js/permissions.js` is a classic script after `core.js` and `agent.js`. It
transports exact arguments, suspends calls, renders controls, and handles explicit
recovery. The small `brain.js` dispatcher hook flags unknown model actions before
execution. Internal file operations (`propose`, `apply`, etc.) cannot be requested
as model tools. The transport covers existing settings and management requests
as well as tool HTTP calls. Provider requests retain their existing transport.

Phase 1 retains its planner, tool loop, verifier and retries. Permission events
reuse its `waiting_approval`, evidence, blocking and persistence. Phase 2 retains
its scheduler, queue, claims and leases. Its checkpoint validator additionally
accepts at most ten bounded approval IDs. The legacy worker policy is used only
when the broker module is absent in offline tests; the installed runtime uses
server policy. Runtime claim/heartbeat/result callbacks remain fenced operations,
not model-visible permission actions.

Trusted task buttons use an isolated request context that shares the page's
session grants. User management remains interactive even while the worker is
background-restricted; it cannot change a concurrent worker request's context.
The model tool never receives this UI transport flag. Cancellation also releases
restored approval waiters; recovering one approval never clears other pending
permissions.

## Capabilities and defaults

The canonical vocabulary is in `RISKS` and the complete current action mapping is
in `MAP`, `CLIENT_MAP` and `read_action`. Tests flag unclassified model action
enums. Supported groups include files/projects, execution/tests, launching and
media, clipboard/screen, browser reads/form preparation, calendar/email reads,
Spotify, memory, jobs/hunt, lessons, video, Minecraft and task management.

| Risk | Examples | Interactive default |
|---|---|---|
| low | file/project reads, search, screen, mail/calendar reads | ALLOW |
| medium | launch, playback, new scaffold, fixed named tests, task management | ALLOW |
| high | existing file write, rollback, Python/Node entry point, form preparation | ASK |
| critical | lock, clear data, security configuration | ASK; direct lock/conversation-clear phrases may ALLOW once |

Unknown actions are denied. Deletion, installation, purchases/payments, system
shutdown, sending email/messages, modifying calendar and final form submission
are unavailable and hard denied. Vocabulary does not introduce tools.

Background defaults allow existing observations and named test/check tools.
Other capabilities require an applicable explicit grant or approval. Read-only
task policy still forbids writes, even with a grant; reviewed-writes tasks still
use exact diff review. Scheduled work cannot manage tasks. Existing internal
episode/notice bookkeeping stays available. Python/Node entry points are
`EXECUTE_CODE`, not ordinary read operations; permission never expands the
runner allowlist.

## Policy precedence and scope

1. Unknown/unavailable actions and mandatory task boundaries deny.
2. Any applicable explicit user DENY overrides grants.
3. Exact/target policies precede global policies; longer scopes precede shorter
   scopes, then matching session policies and newer policies break ties.
4. Default risk/background policy applies when no user policy matches.

File scope is a normalized actual project root, not a reusable project nickname.
Changing configured roots does not transfer an old grant. Resource containment
requires an exact match or a slash boundary, so `PulseUI` never covers
`PulseUI-other`. Actual paths are resolved with Windows path semantics and the
server's configured root. Low-level sandbox checks remain authoritative.

Session grants apply only to one page session. A reload gets a new session; a
server restart clears session policies. Persistent target grants survive restart.
Global automatic grants are limited to low risk. Lock, clear/security changes
and unavailable capabilities never receive reusable allow grants. Revocation or
policy changes invalidate outstanding receipts. Recovered actions also recheck
current policy and resource identity.

## Approval lifecycle and receipts

ASK creates a pending request with a ten-minute lifetime and suspends execution.
Cards show capability/action, actual target, risk and mission/run identity.
Choices are allow once, allow this session, allow this target, deny once and
always deny. Low risk may additionally offer always allow. Inappropriate
persistent choices are omitted. Stored policies can be revoked or changed to
ASK/DENY, and session grants can be cleared without editing JSON.

Approval is a trusted UI operation. The server issues a random 60-second receipt,
stores only its hash, and atomically consumes it once. The fingerprint covers
exact tool/route arguments, capability, resolved resource, held-proposal content
hash, mission/run identity and background context. Plain interactive calls also
bind to a trusted turn identity. Changing file, contents, root, mission or run
invalidates authorization. A receipt cannot be replayed. A fresh proposed action
requires a fresh receipt even under a grant.

Denied/expired identical actions cannot immediately reprompt in the same
mission/turn. Denial is evidence; work blocks or follows the existing bounded
replanning mechanism. Nothing is fabricated as completed.

## Recovery and silence

Pending recovery arguments survive reload in the permission database. They do
not execute automatically. An approval click explicitly resumes the exact saved
call. For durable work, explicit approval requeues the same interrupted run;
the claimed worker restores the existing mission and performs the approved call
before Phase 1 continues. Verified progress is retained. Leases, claim fencing,
heartbeats and existing silent notices remain in use. No permission completion
path calls speech synthesis.

Held diffs remain the existing in-memory file proposals. A server restart loses
those proposals, so an old apply approval safely fails and requires a fresh
proposal/review. Sensitive arguments are replaced by placeholders in recovery
storage; only their fingerprint is retained. They can be approved while the
original call is live, but must be re-entered after reload. Recognized secrets,
targets and errors are redacted; audit records never store tool bodies or prompts.

## Storage and UI

`J.A.R.V.I.S. Dashboard - Copy/jarvis_permissions.db`, schema 1:
`permission_policies`, `approval_requests`, `authorization_receipts`,
`permission_events`. Standard-library SQLite uses short transactions, WAL and a
five-second busy timeout. Initialization is repeatable and unknown schema
versions fail safely. The audit retains 1,000 decisions; resolved request cleanup
retains thirty days. DB/WAL/SHM are ignored and HTTP-private.

Inbox → Permissions or Ctrl+K → Permissions opens a compact, scrollable section
at the end of the existing transcript. It shows pending approvals, policies and
recent reason codes. Text nodes prevent HTML injection. No modal, render loop,
reactor edit, composer relocation or dashboard redesign is introduced.

## Validation

From the repository root:

```text
py -3.12 tests/permissions_test.py
py -3.12 tests/tasks_storage_test.py
node --test tests/agent-core.test.cjs tests/tasks-core.test.cjs tests/permissions-agent.test.cjs
py -3.12 tests/phase3-regression.py
py -3.12 tests/permissions-security.py
```

For native tests, create a disposable folder and run
`tests/permissions-native-server.py PORT FOLDER`. Set `JARVIS_TEST_URL` and
`JARVIS_PERMISSION_FIXTURE`, then run `node tests/permissions-browser.cjs`.
The original native harnesses run with `node tests/permissions-legacy.cjs
agent-browser.cjs` and `tasks-browser.cjs`; the adapter explicitly authorizes
only disposable fixture configuration and project writes/code execution. Their
original assertions and immutable baselines are unchanged. The adapter waits
for task-button HTTP completion before immediate state assertions, accounting
for deterministic authorization round trips. The Phase 3 HTTP
comparison normalizes Windows text line endings only, and still checks exact
intended source changes, route statuses, cache headers and content types.

## Limits

This is local policy, not OS-user authentication or protection from hostile
same-origin scripts or a compromised local process. The model tool surface has
no policy-management operation. Existing project runners execute user project
programs; they are not a new OS process sandbox, and approved program code can
have effects outside what its tool receipt describes. Treat code execution
grants accordingly. Already-dispatched effects cannot be undone by revocation.

Live providers, physical voice devices and real account services require
separate configured-environment checks. No deletion, package installation,
purchase/payment, submission/sending tool, browser operator, MCP or future phase
is added.

Example interactions: “Open Spotify” remains direct; editing a protected file
asks unless a matching grant exists; approving a project grant still preserves
its diff review; a scheduled write waits silently; Permissions → Revoke makes
the next matching action ask again.
