---
name: project-restart-targeting-gotcha
description: octowright restart ignores --http-port for targeting and defaults to 6286 — never drive it live to verify against a shared daemon
metadata:
  type: project
---

`octowright restart` picks which daemon to stop via `restart._restart_target_port()`:
it reads the **lockfile** port if the lock's pid is alive, else falls back to
`defaults.HTTP_PORT` (6286). The `--http-port` CLI flag is **only** passed to the
*spawn* of the new daemon — it does **NOT** scope the kill/sweep. So if the lock
is missing/stale, restart sweeps port 6286 regardless of `--http-port`.

**Why this bit me (2026-06-26):** while verifying the restart pid-identity gate, I
tried to isolate a live `octowright restart` with an isolated `OCTOWRIGHT_LOCK_PATH`
+ `--http-port 59999`. The fake-lock write threw (`LeaderInfo` requires `started_at`),
so the isolated lock was never written → read_lock None → target port defaulted to
**6286** → restart killed the real shared daemon (PID 72154) that another LLM was
using, taking down its 6 browsers. Recovered with a full `octowright restart`.

**How to apply:** To isolate a live restart test you MUST (1) actually write the
isolated lock (include `started_at`) and confirm the file exists, AND (2) set
`OCTOWRIGHT_HTTP_PORT=<isolated>` so the default fallback can't be 6286, AND (3)
pre-flight assert `restart._restart_target_port() == <isolated>` and abort otherwise.
Better: for a destructive CLI like restart, lean on the deterministic unit tests
(monkeypatch `_list_process_commands`/`read_lock`) and don't drive it live against a
shared daemon at all. See [[project-security-audit-backlog]].
