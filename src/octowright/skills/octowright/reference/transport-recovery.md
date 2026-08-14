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
  "reason": "agent_close | user_close | external_disconnect | crashed | shutdown",
  "log_path": "/path/to/session.jsonl"
}
```

**Reason values:**
- `agent_close` — you called `browser_close` or `browser_close_all`.
- `user_close` — the human closed the window (or browser process exited cleanly). Playwright doesn't reliably distinguish "user clicked X" from a clean exit; both arrive as `user_close`.
- `shutdown` — daemon exiting (idle watchdog, SIGTERM, `octowright restart`).
- `external_disconnect` — the browser process disappeared without an explicit close call, including a session lost to a shared-driver death.
- `crashed` — renderer-crash recovery was exhausted for this session.

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

These steps apply when you are in **Claude Code** (the main session, with Bash access) and Octowright returns `Transport closed`. In **any other context** (Codex subagent, background task, etc.), skip straight to "When Octowright Won't Come Back" — you cannot fix the connection yourself.

1. Retry **one** Octowright MCP call. The follower bridge fails fast and reconnects on the next call; a single transient drop recovers here.
2. If it still fails, check daemon health: `curl http://127.0.0.1:6286/api/health`
3. If health is **200 OK**, the daemon is alive but the client handle is broken — tell the user to reconnect via `/mcp` in Claude Code.
4. If health **fails**, the daemon is down — tell the user to run `octowright restart` (or `uv run --directory <octowright-path> octowright restart`), then reconnect via `/mcp`.
5. Do not retry more than once before telling the user.

## When Octowright Won't Come Back — STOP AND TELL THE USER

Two failure modes: a **transient** drop recovers on one retry; a **gone** leader closes the client's stdio and **cannot recover in-session** — the human must reconnect.

**Signals Octowright is gone, not just slow:**
- Octowright tools are absent from your available tool list.
- A browser tool returns `Transport closed` / times out **and one retry still fails**.
- `octowright_status` itself is unreachable.

**Forbidden actions — these burn tokens and never fix the connection:**
- Running `octowright restart`, `uv run octowright restart`, or any shell variant to restart the daemon yourself. The `octowright` binary is **not on the agent's shell PATH** in Codex, background tasks, or most subagent environments. Even if it were, restarting the daemon closes the MCP stdio connection — it does NOT reconnect the MCP client. The human must reconnect their MCP client after any restart.
- Running `which octowright`, `find`, or any filesystem search to locate the binary. This wastes tokens and cannot fix a disconnected MCP client.
- Probing `curl http://127.0.0.1:6286/api/health` as a diagnostic step outside of Claude Code. Even if the daemon answers, only the MCP client reconnecting fixes the stdio handle.
- Opening a URL with any shell command (`open`, `xdg-open`, `start`, `osascript`, `Bash`) and treating it as a browser session. That is an unmanaged, undriveable browser — not Octowright.
- Writing Playwright test scripts or any raw Playwright code as a substitute. The user asked for a driven, recorded Octowright session. A script is a different deliverable.
- Continuing to work on the browser task by any other means. The task requires Octowright. Without it, stop.

**Do not claim a browser is open** unless an Octowright tool returned a live `instance_id`.

**Required response when Octowright is down — one message, then stop:**
1. "Octowright is disconnected — I can't drive a browser until it's reconnected."
2. If in Claude Code: `/mcp` → select **octowright** → **Reconnect**. If it stays failed, ask them to also run `octowright restart` first.
3. For any other client: ask which MCP client they're using, then have them use its reconnect/refresh control or restart it.
4. Wait for them to confirm Octowright is back. Then resume.

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
octowright restart --kill-followers  # also kill stale MCP follower processes (full reset)
```

### `--kill-followers`

By default `restart` only kills the leader daemon and leaves bare `octowright serve`
follower processes alone — killing them would sever any connected MCP client's
stdio transport. Use `--kill-followers` when you know all sessions are already dead
(e.g. after killing stale zombies manually, or after a machine reboot) and want a
completely clean slate:

```bash
# Full reset: kill daemon + all stale followers, then start a fresh daemon
octowright restart --kill-followers
```

**WARNING:** this immediately terminates every follower process. Any MCP client with
an active connection (Claude Code, Codex, etc.) will lose its transport and need to
reconnect.

## What NOT to Do

- **Don't** `pkill -f octowright` then `octowright serve &` in a loop. The agent's stdio bridge is one-shot and won't reconnect.
- **Don't** spawn multiple `octowright serve` instances. They fight for the lockfile and HTTP port; the loser exits, leaving zombies that `octowright restart` has to clean up.
- **Don't** assume the daemon is dead because MCP fails. Always probe `/api/health` first. Most failures are a broken client stdio handle, not a broken daemon.
