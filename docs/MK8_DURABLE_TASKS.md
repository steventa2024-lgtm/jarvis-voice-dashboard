# Mk VIII Phase 2: durable tasks

The Python task engine stores objectives, schedules, execution identities and compact Phase 1 checkpoints. It does not call models, define a second planner, or execute tools. The existing browser Agent Core remains the only planner, executor and verifier.

`scheduler → durable queue → atomic browser claim → Phase 1 mission → verified receipt → durable result → existing silent notice queue`

## Storage and API

`J.A.R.V.I.S. Dashboard - Copy/jarvis_tasks.db` uses SQLite schema version 1: `tasks`, `task_runs`, `task_events`. Initialization is transactional and repeatable; unknown schema versions are rejected without replacing data. Short-lived connections use WAL, foreign keys and a five-second busy timeout. The existing `.gitignore` already covers `.db`, `.db-wal`, and `.db-shm`. The existing server private-file rules deny HTTP access to these files.

`POST /api/tasks/command` accepts bounded JSON from the loopback host and same browser origin. It never accepts SQL or a database path. Runtime commands include `due`, `claim`, `check_claim`, `heartbeat`, `complete`, `fail`, `interrupt`, and `get_run`. The model-visible `tasks` tool exposes only user management: create, list/get, pause/resume/cancel schedule, history, run now, resume/restart/cancel run. Exact titles work when unambiguous. Ambiguous titles require an ID.

Task states: `active`, `paused`, `cancelled`. Run states: `queued`, `claimed`, `running`, `waiting_approval`, `interrupted`, `completed`, `failed`, `cancelled`. Phase 1 retains its existing mission vocabulary. A blocked or failed mission finalizes the durable run as failed; unverified waiting work becomes interrupted. No scheduler-level automatic retries are introduced.

## Scheduling

Accepted schedules include future epoch timestamps or ISO datetimes, `in 2 hours`, `tomorrow at 9am`, `tonight at 8`, `every day at 8am`, `every morning at 8`, `every weekday at 7am`, `every Monday at 9am`, and `every 2 hours`. Relative times reuse the existing reminder parser. Bare ambiguous hours require AM/PM or explicit 24-hour notation. Structured API schedules support `once`, `interval` (60 seconds through seven days), and `calendar` (local hour/minute and weekday numbers, Monday=0).

Epoch timestamps represent instants; calendar schedules use the PC timezone and display it explicitly. Calendar recurrence is computed from the definition, never from completion time. Nonexistent DST wall times are skipped; a repeated local wall time has one occurrence. Changing the PC timezone changes future wall-time computation; timezone selection is not implemented.

When Jarvis was off, recurring schedules coalesce to one latest due occurrence. A unique occurrence key prevents repeated due polling from duplicating it. A partial unique index permits at most one outstanding run per task, including interrupted work. If an older run is outstanding, later occurrences coalesce into that outstanding work and the recurrence advances. Resolve the interrupted run to permit future runs. `run_now` preserves recurrence and returns an already outstanding run rather than creating a duplicate. Pause/cancel schedule affect future occurrences only; cancel run is separate. Resume schedule computes the next future recurring occurrence.

## Worker and recovery

The scheduler checks every ten seconds. An available dashboard asks for work every fifteen seconds; the server is authoritative about due time. A persistent non-secret worker base ID receives a distinct suffix for each tab/page. Atomic claims receive a fresh fencing nonce, ninety-second lease and twenty-second heartbeat. Each tool batch checks ownership; internal file application and automatic build checks also check ownership. Already dispatched subprocesses or network calls may finish after cancellation; no new dependent actions are dispatched by a worker that has lost its claim.

Mission events checkpoint bounded Phase 1 state with task/run/mission identity. Summaries are redacted and transcripts/private reasoning are not stored. Completion requires completed Phase 1 steps containing evidence. Existing planner, verification criteria, anti-thrash, bounded retries, tool lanes, build inspection and episodic memory remain in use.

Reload uses the previous tab claim to record interruption; an unacknowledged recovery pointer is retained. A crashed/closed tab eventually loses its lease and becomes interrupted, never automatically queued for replay. `resume_run` imports the saved Phase 1 plan, preserves completed step timestamps/evidence, and explicitly continues pending work. A missing plan requires explicit restart. Restart creates a new run. Review in-flight actions before resuming after a crash: a checkpoint cannot prove the result of an operation that finished after connectivity was lost. No write is automatically replayed or approved.

The database survives server and PC restarts. Once `serve.py` starts, due work materializes. If no browser worker is open, work stays queued. There is no headless model worker and no execution while the PC is off. Browser timer throttling may delay claims and cause safe interruption.

## Safety and silence

Default `read_only` runs permit inspection, search, recall, Google reads, Spotify state and existing project validation tools. Other actions are blocked. The optional `reviewed_writes` policy permits file writes only through the existing proposal/diff review; scheduled execution forces review even if interactive review is disabled or a file is new. No deletion, installation, email, purchases, desktop actions, or new permission escalation is introduced. Project validation still executes project code through the existing file runner safeguards.

Only trusted user requests create objectives. Tool/web/file content remains untrusted data under Phase 1 planner and verifier rules. Background tasks cannot manage/create other tasks. Runtime claim/heartbeat mechanics are not model tools. Model task creation/run-now retries have per-turn idempotency keys.

Scheduled results never call TTS: background transcript feed/flush are suppressed, self-critique speech paths are skipped, and tools that start voice timers are disallowed. Ordinary interactive speech is unchanged. Completion, failure, interruption or approval waiting deliver factual notices through the existing memory queue. Durable event delivery markers and optional existing-notice dedupe keys prevent duplicate callbacks or delivery retries from adding notices, including notices already read. The existing queue retains at most 200 notices; manually clearing notices also clears their dedupe record.

## UI, events and diagnostics

Inbox → **automations**, or Ctrl+K → **Scheduled tasks**, opens compact controls in the existing transcript. The surface scrolls internally and never overlays the reactor or composer. It supports create, pause/resume/cancel schedule, run now and recent run history with explicit resume/restart/cancel controls. Scheduled progress uses the existing Phase 1 mission card labeled `SCHEDULED MISSION`; no second progress component is created.

Existing `J.on/J.emit` events remain canonical. Added events are `tasks:available`, `tasks:changed`; mission updates drive checkpointing. `J.tasks.diagnostics()` reports worker availability and current durable run ID. `J.agent.diagnostics()` retains mission diagnostics. No secrets or full prompts are logged.

## Verification

Run from the repository root:

```text
py -3.12 tests/tasks_storage_test.py
node --test tests/agent-core.test.cjs tests/tasks-core.test.cjs
py -3.12 tests/phase2-regression.py
```

The Phase 2 regression runner compares against immutable Phase 1 SHA `2fca0bca8b2d9d9145b67fa6c65bca450ce94864`; the original Phase 1 tests and baselines are untouched. It checks original Python/JS/inline syntax, the 25 HTTP routes, offline Pixabay behavior and the explicit narrow changed-file set.

For native Chromium testing, start `tests/tasks-native-server.py PORT TEMP_FOLDER` with a disposable folder, then set `JARVIS_TEST_URL` and run `node tests/tasks-browser.cjs`. It uses real SQLite and real Python/file execution with controlled OpenAI-compatible SSE responses, never paid model calls or account actions. `node tests/agent-browser.cjs` also runs the original Phase 1 integration suite. `JARVIS_TEST_ARTIFACTS` optionally saves screenshots and results.

Live provider/account services and a physical PC reboot are not acceptance simulations. They require configured services and separate operational testing. This phase does not add a Permission Broker, MCP, skills registry, specialist agents or other later-phase features.
