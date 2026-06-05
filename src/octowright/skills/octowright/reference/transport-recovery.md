---
name: transport-recovery
description: Reference for session_closed push notifications, MCP transport recovery, health checks, and the octowright restart command.
---

# Transport Recovery Reference

## Session-Close Push Notifications

Octowright sends a JSON-RPC push notification to the MCP client whenever a browser session leaves the pool — before the agent polls or tries a tool call.

**Method:** `notifications/octowright/session_closed`

**Params:**
```json
{
  "instance_id": "abc123",
  "kind": "chromium",
  "label": "user-label-or-null",
  "profile": "persona-name-or-null",
  "reason": "agent_close | user_close | external_disconnect | shutdown",
  "log_path": "/path/to/session.jsonl"
}
```

**Reason values:**
- `agent_close` — you called `browser_close` or `browser_close_all`.
- `user_close` — the human closed the window (or browser process exited cleanly). Playwright doesn't reliably distinguish "user clicked X" from a clean exit; both arrive as `user_close`.
- `shutdown` — daemon exiting (idle watchdog, SIGTERM, `octowright restart`).
- `external_disconnect` — reserved; not emitted today.

**What to do on receipt:**
1. Remove `instance_id` from any local tracking (open tabs, active macros, etc.).
2. If user-facing (`reason == "user_close"` or `"shutdown"`), surface the closure — the user may want to reopen.
3. If agent-internal and `reason == "user_close"`, reopen if the task needs it; otherwise abort cleanly.
4. **Do not** call `browser_close` for a session that already closed — it returns `KeyError`. The notification is the authoritative signal.

Push notifications flow through the follower→leader bridge automatically. Followers receive them without extra configuration.

## When Something's Wrong

```dot
graph TD
    Symptom["MCP tool call returns 'Transport closed'<br/>or hangs"] --> Health{"curl http://127.0.0.1:6286/api/health<br/>returns 200?"}
    Health -- "Yes — daemon is alive" --> Retry["Retry one Octowright MCP call.<br/>The follower bridge should fail fast<br/>and reconnect for the next call."]
    Retry --> Smoke["If the same client handle still fails,<br/>run scripts/bridge_reconnect_smoke.py<br/>to separate client-handle failure<br/>from daemon failure."]
    Health -- "No — port doesn't answer" --> DaemonFix["The daemon itself is gone or wedged.<br/>Run: octowright restart"]
    DaemonFix --> Verify["Verify: curl /api/health → 200"]
    Verify --> Retry
```

## Transport Recovery Steps

1. Check daemon health: `curl http://127.0.0.1:6286/api/health`
2. If health is good, retry one Octowright MCP call. The follower bridge should fail fast and reconnect.
3. If the same client handle still fails, run `uv run --active python scripts/bridge_reconnect_smoke.py` to distinguish a broken client handle from a broken daemon.
4. Do not run `octowright restart` unless daemon health fails or the user explicitly asks.

## `octowright restart`

```bash
octowright restart                   # stop → reap → start → probe
octowright restart --no-start        # stop + reap only (e.g. before reboot)
octowright restart --keep-browsers   # skip orphan-browser sweep
octowright restart --http-port 8766  # use a non-default port
octowright restart --timeout 30      # extend shutdown / health-probe budget
```

## What NOT to Do

- **Don't** `pkill -f octowright` then `octowright serve &` in a loop. The agent's stdio bridge is one-shot and won't reconnect.
- **Don't** spawn multiple `octowright serve` instances. They fight for the lockfile and HTTP port; the loser exits, leaving zombies that `octowright restart` has to clean up.
- **Don't** assume the daemon is dead because MCP fails. Always probe `/api/health` first. Most failures are a broken client stdio handle, not a broken daemon.
