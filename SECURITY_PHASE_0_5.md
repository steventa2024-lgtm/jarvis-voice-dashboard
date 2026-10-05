# Phase 0.5 security reconstruction

The historical hard-coded Pixabay fallback has been removed from all reachable
history. Both public branch tips exactly match the approved sanitized SHAs.
The 23-path history purge list is recorded separately and must not be broadened
without approval. The private recovery bundle is outside this repository and
outside cloud-sync folders; do not upload, attach, commit or share it.

Video footage uses PIXABAY_API_KEY from the server process environment. Set it
before starting the server, then restart after changing it. The .env.example
file documents the variable; the application does not automatically read it.
An absent, empty or whitespace-only key returns actionable configuration guidance
before render creates directories or calls external tools. Direct stock fetching
also refuses missing configuration before network activity. With a configured
key, ranking, stock selection, narration, ffmpeg commands, listing generation,
review and publish-packet behavior are preserved.

The only application source change is this configuration guard in jarvis_video.py.
No dashboard, reactor, voice, wake word, routing, memory, Google, Spotify,
Minecraft, job, lesson, project, event-bus or endpoint code is changed.
No Mk VIII Phase 1 work is included. Validation reports are stored outside the
repository; offline checks do not constitute live integration testing.
