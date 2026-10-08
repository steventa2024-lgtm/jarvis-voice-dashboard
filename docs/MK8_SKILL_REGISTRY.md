# Mk VIII Phase 4 — trusted built-in Skill Registry

The registry moves integration schema/routing knowledge out of the conversation
engine. It discovers **17 built-in skill manifests / 18 integration tools**.
Three core tools remain: `control_interface`, `remember`, `forget`. The equivalent
fully configured tool list is still **21 tools**, in its previous order.

## Architecture

`skills/*/skill.json → jarvis_skills.py → /api/skills → js/skills.js → brain tool list`

`model tool call → registry resolution → Phase 3 authorization → trusted adapter → existing handler`

The browser registry caches validated metadata. Server-side cached lookup fences
stale tabs and recovered calls. Protected HTTP execution also checks the current
enabled registry before reaching its original handler. Neither path scans disk or
probes services per token/action. Manifests do not execute anything.

The existing implementations remain in place. `brain.js` exports a small frozen
legacy handler interface for `js/skill-adapters.js`. It no longer contains the
migrated schemas or integration-specific dispatch chain. Future trusted adapters
can call their own application modules without adding cases to brain dispatch.

Agent Core, task queue/scheduler and Permission Broker are required infrastructure.
They cannot be disabled through Skills and have no model-facing policy tools.

## Manifest format

Schema version 1 fields:

```json
{
  "schema_version": 1,
  "id": "spotify",
  "name": "Spotify",
  "version": "1.0.0",
  "description": "Existing Spotify integration.",
  "tools": [{"name": "spotify", "description": "Read playback or pause Spotify.",
    "input_schema": {"type": "object", "properties": {
      "action": {"type": "string", "enum": ["current", "pause"]}
    }, "required": ["action"]}}],
  "runtime": {"kind": "client_adapter", "adapter": "spotify"},
  "permissions": {"spotify": {"current": "READ_SPOTIFY", "pause": "CONTROL_SPOTIFY"}},
  "availability": {"capabilities": ["spotify"], "mode": "all", "client_requirements": []}
}
```

This is a shape illustration: a real manifest contains complete tool objects and
every declared action. Current examples are the tracked built-in manifests.
All current tools use client adapters because their existing browser handlers
also transform results and emit UI/evidence events, including those backed by
server routes. Unsupported runtime kinds fail closed; future MCP/operator kinds
require deliberate trusted-code additions.

IDs and tool names are safe lowercase identifiers (48 characters). Names are
bounded to 80 characters, descriptions to 8,192, files to 64 KiB. Versions use a
bounded semantic version form. Unknown fields, duplicate JSON keys, unsupported
versions, unknown adapters and adapter/tool mismatches are rejected.

The JSON-schema subset is the existing `type`, `properties`, `required`, `enum`,
`items`, `description`: at most eight nesting levels, 256 schema nodes, 64 object
properties and 64 enum entries. Arbitrary schema extension/evaluation keywords
are not supported. Manifests cannot name modules, executables or filesystem paths
as handlers. Immediate trusted skill folders are sorted; symlinks are rejected.
Duplicate IDs/tools exclude every conflicting manifest, never last-write-wins.
Other valid skills continue loading when one is invalid.

## Permission integration

Manifest capability declarations must exactly agree with the existing Phase 3
classification for the named actions. Unknown capability names or attempts to
substitute read access for a mutation reject that manifest. The broker retains
risk levels, contextual classification, hard restrictions, grants, receipts and
ALLOW/ASK/DENY decisions. Existing policies retain the same canonical names.

Context-dependent overrides still belong to the broker: for example a named test
runner differs from a Python/Node entry point. Manifest metadata is descriptive;
it is not an authorization grant or risk override. Existing file roots, fixed
runner commands, diff approval and background restrictions remain unchanged.
File tools cannot write registry manifests or enable-state files. Like Phase 3,
this is local policy, not an OS sandbox for approved user project programs.

## Registry API and trusted management

- `GET /api/skills`: cached validated snapshot.
- `GET /api/skills/status`: cached health and diagnostics.
- `GET /api/skills/<id>`: installed skill metadata.
- `GET /api/skills/tool/<name>`: enabled routing identity; disabled/invalid skills fail.
- `POST /api/skills/command`: trusted UI `list`, `get`, `status`, `refresh`, `set_enabled`.

Mutation requires the existing local-origin UI session transport. The model has
no registry-management tool. There is no upload, installation, download, arbitrary
import, package manager or marketplace route.

## Availability, caching and enable state

Server capability metadata reuses the existing health definition. Public OAuth
connection booleans are sampled on startup/refresh without returning account
details or secrets. Spotify/Google when disconnected and unconfigured video are
DEGRADED: their existing setup/failure workflow remains exposed, as before.
Missing server capabilities are UNAVAILABLE. Browser settings filter vision,
preview and web tools; tasks are omitted during background work. Preview requires
the Files skill. Translation retains the previous desktop-or-vision availability
rule; its individual source may still need configuration.

The trusted adapter table defines stable ordering matching Phase 3. OAuth changes
or settings changes request a debounced refresh; users may refresh manually.
Enable/disable changes notify other tabs. There is no constant polling or render
loop. A stale tab cannot execute a skill disabled on the server.

`jarvis_skills_state.json` and `.tmp` are ignored runtime state. Atomic replacement
persists only schema version and per-skill enabled booleans, without editing
manifests. State survives page/server/PC restart once Jarvis starts. Malformed
state fails closed for integration tools and requires trusted local recovery;
core chat/infrastructure remain. Disabling affects future dispatch, not effects
already dispatched. Pending protected HTTP calls are checked again at execution.

## Planner, durable tasks and errors

The existing planner receives the current tool names/descriptions plus a bounded
available/unavailable skill summary. Task databases do not store manifest copies.
Resumed objectives use the current registry, and unavailable actions produce
`FAILED` evidence instead of fabricated execution. Original tool names and
episodic precedents stay valid.

Registry load failure preserves normal/tool-less chat and the three explicit core
tools. Integration dispatch fails closed; there is no hidden static tool fallback.
Approval waiting, exact call recovery, verified steps and silent notices remain
owned by Phases 1–3. Nothing speaks unprompted.

## UI

Inbox → Skills or Ctrl+K → Skills opens a compact scrollable transcript surface.
It shows availability, reason, version, descriptions, tool names, capability
metadata, diagnostics and enable state. Required infrastructure is listed without
disable controls. Disabling Memory & Reminders removes reminder commands; core
remember/forget, existing due-reminder delivery and notice bookkeeping remain required. Text nodes prevent HTML injection. Reactor, composer, voice and
dashboard geometry are untouched.

## Add a future trusted built-in skill

1. Implement the integration in reviewed application code, retaining its safeguards.
2. Register its trusted adapter in `js/skill-adapters.js` and its identity/tool names
   in the server `ADAPTERS` allowlist. Call its module directly; no new brain case.
3. Add a schema-version-1 manifest under the trusted built-in skills root.
4. Declare its model name/description/input schema and keep compatibility intentional.
5. Map actions through the central Permission Broker using existing canonical
   capabilities; add deliberate broker mappings when introducing a new tool name.
6. Add a known availability probe and conservative unavailable/degraded behavior.
7. Test schema, routing, authorization, background behavior, failures and UI.

Dropping arbitrary code or a JSON handler path into a folder does not install or
execute it. Runtime installation and future MCP/operators are out of scope.

## Validation and limits

```text
py -3.12 tests/skills_test.py
node --test tests/skills-core.test.cjs tests/agent-core.test.cjs tests/tasks-core.test.cjs tests/permissions-agent.test.cjs
py -3.12 tests/permissions_test.py
py -3.12 tests/tasks_storage_test.py
py -3.12 tests/phase4-regression.py
node tests/skills-browser.cjs
py -3.12 tests/skills-security.py
```

`tests/fixtures/phase3-tools.json` was captured before changing brain.js and is
checked against immutable Phase 3 Git source. Model schemas, required fields,
enums, descriptions, ordering and provider-native web formats remain equivalent.
All adapters are checked against original argument shapes. A native test reproduced
the existing tool-less specialist sending tool schemas despite `tools: false`;
that declared route now omits the tool field. Routing/streaming are unchanged. Existing permission
enum coverage now also reads the migrated manifests; its assertions are retained.
The new Phase 4 regression runner accounts for added files while preserving old
HTTP statuses/cache/type comparisons and offline Pixabay behavior. Old baseline
files and native scenario assertions are untouched.

Native tests use real Windows Chromium, disposable local storage/files/servers
and controlled provider/Spotify responses. They do not use live accounts or
paid model calls. Physical voice devices and real OAuth/playback need separate
configured-environment checks. Bridge availability is not proof every external
service/action succeeds; existing handlers still report those failures honestly.
