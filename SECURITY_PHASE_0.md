# Phase 0 security reconstruction

This branch reconstructs repository hygiene after the original local security
commits became unavailable. The private pre-sanitization GitHub recovery bundle
is stored separately under the local Windows user profile, outside OneDrive.

Exactly 23 approved credential, runtime and cache paths were purged from all
reachable history. The historical Pixabay fallback literal was removed from
exactly two retained blobs. The sanitized main and Claude branch IDs match the
previously approved deterministic results.

The root ignore rules exclude private credentials, runtime state, database
artifacts, caches and recovery bundles. Empty example JSON files document
safe local defaults; they are not automatically loaded and contain no personal
records, credentials or tokens. Restore private runtime data locally only.

Application source, dashboard visuals, voice, wake word, provider routing,
memory, integrations and routes are unchanged by Phase 0. No Mk VIII Phase 1
work is included. This report is not a claim of live integration testing.
