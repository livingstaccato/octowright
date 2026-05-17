---
name: using-octowright
description: Use when automating web browsers using the Octowright MCP server, managing multiple personas, or debugging browser actions.
---

# Using Octowright

## Overview
Octowright is a high-observability browser orchestrator. It uses a **Leader-Follower** model to manage multiple browsers across different engines (Chromium, Firefox, WebKit) while maintaining persistent identities (Personas) and recording every action to JSONL.

## Core Principle: Resource Discipline & Efficiency
**Every browser instance is a resource.** You must launch efficiently and close rigorously.

## Workflow: The Octowright Loop

```dot
graph TD
    Start[Task Start] --> Status[octowright_status]
    Status --> Advisor[Inspect advisor block]
    Advisor --> Suggest[browser_suggest_for_url]
    Suggest --> Launch[browser_launch with Persona]
    Launch --> Act[Perform Actions]
    Act --> Success{Success?}
    Success -- Yes --> Close[browser_close]
    Success -- No --> Debug[browser_snapshot / aria-tree]
    Debug --> Fix[Update Macro / Selector]
    Fix --> Close
```

## Rules of Engagement

### 0. Advisor Check
Octowright Advisor is a local guidance layer that reports preferences, recent
tool usage, and suggestions.
- **REQUIRED**: Call `octowright_status` once when Octowright first comes up and
  inspect its `advisor` block.
- **CHECK**: Call `octowright_advisor_status` when deciding whether to suggest
  saving repeated work as a macro or changing `OCTOWRIGHT_PROFILE`.
- **OBSERVE**: When you notice the same workflow repeated, call
  `octowright_advisor_record_macro_observation(source="llm", signature=..., summary=...)`.
  Two matching signatures produce a macro-candidate suggestion.
- **RESPECT**: Advisor preferences can suppress suggestion types. Macro
  candidates are always prompt-only; never save a macro without user approval.

### 1. The Launch Protocol
Never "guess" a persona or launch a raw browser without checking for existing state.
- **REQUIRED**: Call `browser_suggest_for_url` before `browser_launch`.
- **PREFER**: Use the suggested persona to maintain login state and credentials.
- **STABILIZE**: Set `stabilize: true` in `browser_launch` for mission-critical tasks (it adds a brief settling delay).

### 2. Mandatory Teardown
Resource leaks cause system instability.
- **ALWAYS** call `browser_close` immediately after a task finishes (or fails).
- **EMERGENCY**: Use `browser_close_all` if you lose track of session IDs.

### 3. Debugging Hierarchy
When an action fails (e.g., selector not found):
1.  **Snapshot**: Use `browser_snapshot` to get the `aria-tree`. The accessibility tree is more stable than the raw DOM.
2.  **Inspect**: Use `browser_list_frames` if you suspect the element is inside an iframe.
3.  **Goldens**: Use `golden_assert` to compare the current page against a known good state if the failure is visual or structural.

### 4. Macro Management
- **REUSE**: Check `macro_list` before manually implementing a common flow (like login).
- **EVOLVE**: If a macro fails, update it using `macro_save` after identifying the correct new selectors. Don't just patch the current session.
- **NOMINATE**: If a manual workflow repeats, record an Advisor macro observation
  so the user can decide whether it should become a saved macro.

## When Something's Wrong

Octowright has two distinct "is it working?" surfaces and they fail
independently. Diagnose correctly *before* killing anything.

### Decision flow

```dot
graph TD
    Symptom["MCP tool call returns 'Transport closed'<br/>or hangs"] --> Health{"curl http://127.0.0.1:8765/api/health<br/>returns 200?"}
    Health -- "Yes — daemon is alive" --> ClientFix["Your MCP client lost its stdio bridge.<br/>RESTART YOUR AGENT (Claude Code /<br/>Codex CLI). Do NOT touch the daemon."]
    Health -- "No — port doesn't answer" --> DaemonFix["The daemon itself is gone or wedged.<br/>Run: octowright restart"]
    DaemonFix --> Verify["Verify: curl /api/health → 200"]
    Verify --> ClientFix
```

### What 'Transport closed' actually means

The MCP transport between your agent (Claude Code, Codex CLI) and the
Octowright daemon is **stdio-based, established once at MCP-client startup,
and does not auto-reconnect**. When you see ``Transport closed``:

- **First, probe the daemon directly:** ``curl http://127.0.0.1:8765/api/health``.
  If it returns ``{"ok":true,"version":"..."}`` the daemon is fine.
- **If the daemon is healthy, restarting it will NOT help** — the broken
  thing is your agent's MCP client connection, which only reconnects when
  your agent process restarts. Killing the daemon makes things worse: the
  agent's stale stdio handle still won't reconnect, *and* you've also lost
  the only entity that knows what browsers were live.
- **If the daemon is unreachable, then restart it:** ``octowright restart``.
  That command stops every ``octowright serve`` it finds, reaps orphan
  Playwright browsers, spawns a fresh daemon, and probes ``/api/health``
  until it answers — done correctly and atomically, not as a sequence of
  ``pgrep`` / ``kill`` / ``serve`` shell calls.

### `octowright restart`

```bash
octowright restart                        # stop → reap → start → probe
octowright restart --no-start             # stop + reap only (e.g. before reboot)
octowright restart --keep-browsers        # skip orphan-browser sweep
octowright restart --http-port 8766       # use a non-default port
octowright restart --timeout 30           # extend shutdown / health-probe budget
```

### What NOT to do

- **Don't** ``pkill -f octowright`` and then ``octowright serve &`` in a loop
  hoping the MCP transport reconnects. It won't. The agent's stdio bridge is
  one-shot.
- **Don't** spawn multiple ``octowright serve`` instances thinking the second
  will take over for the first. They fight for the lockfile and HTTP port and
  the loser exits, leaving zombie processes that ``octowright restart`` then
  has to clean up.
- **Don't** assume the daemon is dead because MCP fails — always probe
  ``/api/health`` first. Most "Octowright is broken" reports are actually
  "my MCP client lost its stdio handle and I need to restart my agent".

## Common Mistakes

| Mistake | Consequence | Fix |
| :--- | :--- | :--- |
| Leaving browsers open | Memory exhaustion, "Zombie" processes. | Always `browser_close` in a `finally` block logic. |
| Manual login repetition | High token usage, fragile scripts. | Use **Personas** to persist session state. |
| Guessing selectors | Frequent failures due to DOM changes. | Use `browser_snapshot` for the A11y tree. |
| Overlooking iframes | Tools fail to find elements that are visible. | Use `browser_list_frames` then `browser_switch_frame`. |

## Red Flags - STOP and Start Over
If you catch yourself doing these, you are violating the Octowright workflow:
- Launching a browser without calling `browser_suggest_for_url` first.
- Attempting to "manually" log in when a Persona already exists.
- Forgetting to call `browser_close` because "I'm not done yet" (Close it! You can always re-launch).
- Guessing selectors after a failure instead of checking `browser_snapshot`.

## Rationalization Table

| Excuse | Reality |
| :--- | :--- |
| "It's just a quick check, I don't need a persona." | Raw browsers are fragile and lack cookies. `suggest_for_url` takes 2 seconds and saves minutes of manual login. |
| "I'll close the browser at the very end of the session." | If the agent crashes or exceeds its turn limit, the browser becomes a "Zombie". Close it AFTER EACH TASK. |
| "I know the selector by heart." | Sites change. `browser_snapshot` provides the A11y tree which is more durable than your memory. |

## Quick Reference

| Task | Tool |
| :--- | :--- |
| Find best persona | `browser_suggest_for_url` |
| Check Advisor guidance | `octowright_advisor_status` |
| Record repeated workflow | `octowright_advisor_record_macro_observation` |
| Start session | `browser_launch(kind, profile, ...)` |
| Robust interaction | `browser_click(..., wait_for="visible")` |
| Fix failing macro | `browser_snapshot` -> `macro_save` |
| Cleanup | `browser_close` |
| Probe daemon health | `curl http://127.0.0.1:8765/api/health` |
| Daemon is wedged | `octowright restart` (shell, not MCP) |
| MCP `Transport closed` | Restart your agent, not the daemon |
