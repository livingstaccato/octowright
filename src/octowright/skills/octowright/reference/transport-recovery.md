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

## When Octowright Won't Come Back (do NOT fake a browser)

Two failure modes (see the flowchart): a **transient** drop recovers on one retry; a **gone** leader (killed, crashed, idle-exited, restarted) closes the client's stdio and the session **cannot recover in-session** — the human must reconnect.

**Signals Octowright is gone, not just slow:**
- Octowright tools are absent from your available tool list.
- A browser tool returns `Transport closed` / times out **and one retry still fails**.
- `octowright_status` itself is unreachable.

**The hard rule — never substitute a browser:** When Octowright is unavailable, do NOT open a URL with a shell command (`open`, `xdg-open`, `start`, `Bash`, `osascript`) and treat it as fulfilling a browser request. That launches an **unmanaged** browser you cannot drive, snapshot, fill, or record — it is NOT an Octowright session. Reporting "I opened a browser" for it is a false success: the user asked for the driven, recorded browser and got a tab you can't touch. This exact mistake (shell `open` reported as success) is the failure this section exists to prevent.

**Do not claim a browser is open** unless an Octowright tool returned a live `instance_id`.

**Required response when Octowright is down — stop and tell the user plainly:**
1. "Octowright's MCP server is disconnected — I can't drive a browser until it's reconnected."
2. If the daemon is down (`curl http://127.0.0.1:6286/api/health` fails), have them run `octowright restart` (or restart the `octowright serve` process).
3. Reconnect Octowright **in the MCP client they're using** — the command differs per client (see the table below). Give them the steps for *their* client, not a generic "reconnect."
4. Ask them to say when it shows connected, then resume with `browser_launch`.

### Reconnecting (steps vary by client — ask, don't guess)

Octowright talks to the MCP client over **stdio**, and stdio MCP servers generally do **not** auto-reconnect, so a manual reconnect or a client restart is needed — unlike HTTP/SSE servers, which some clients silently retry.

The exact reconnect command depends on the client **and its version**, and these UIs change often. Do NOT state a reconnect command you're not certain applies to the user's setup — a confident-but-wrong instruction sends them in circles.

- **Claude Code** (the one to state confidently): `/mcp` → select **octowright** → **Reconnect**. If it stays failed, restart Claude Code.
- **Any other client:** first **ask the user which MCP client they're using** (and version if they know it). Then have them use *that* client's own MCP **reconnect / refresh / toggle** control — usually under its MCP or Tools settings — or, if it has none, **restart the client app**. If you don't know the exact steps for their client, say so and ask them to reconnect via its MCP settings (or check that client's MCP docs) rather than guessing.

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
