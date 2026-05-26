---
name: using-octowright
description: Use when automating web browsers using the Octowright MCP server, managing multiple personas, or debugging browser actions.
---

# Using Octowright

## Overview
Octowright is a high-observability browser orchestrator. It uses a **Leader-Follower** model to manage multiple browsers across different engines (Chromium, Firefox, WebKit) while maintaining persistent identities (Personas) and recording every action to JSONL.

## Core Principle: Resource Discipline & Efficiency
**Every browser instance is a resource.** Launch efficiently. Close rigorously for **agent-internal** browsers (snapshots, scripted verification, recording work). **User-facing** browsers — the ones the user asked you to show or open — stay open until *they* close them.

## Workflow: The Octowright Loop

```dot
graph TD
    Start[Task Start] --> Status[octowright_status]
    Status --> Advisor[Inspect advisor block]
    Advisor --> NeedSuggest{Real-internet URL<br/>and user didn't<br/>name a persona?}
    NeedSuggest -- Yes --> Suggest[browser_suggest_for_url]
    NeedSuggest -- No --> Launch[browser_launch]
    Suggest --> Launch
    Launch --> Act[Perform Actions]
    Act --> Success{Success?}
    Success -- No --> Debug[browser_snapshot / aria-tree]
    Debug --> Fix[Update Macro / Selector]
    Fix --> Audience
    Success -- Yes --> Audience{User-facing<br/>launch?}
    Audience -- Yes --> Leave[Leave window open]
    Audience -- No --> Close[browser_close]
```

## Rules of Engagement

### 0. Advisor Check + Pattern → Macro

Octowright Advisor is a local guidance layer that reports preferences, recent tool usage, and suggestions. Treat it as a first-class collaborator, not optional telemetry — it's the only mechanism that turns repeated agent work into reusable macros.

**Per-session bootstrap (do this once per conversation):**
- Call `octowright_status` the first time Octowright comes up. Inspect its `advisor` block: note current preferences and any pending `suggestions`.
- Surface any `macro_candidate` suggestion to the user before doing your task — don't sit on it.

**Pattern → Macro recording (do this every time, not just on the second occurrence):**

The mechanism: `octowright_advisor_record_macro_observation(source="llm", signature, summary)` appends an observation. **Two observations with identical `signature` strings** produce a `macro_candidate` suggestion in the next status call. The candidate is **always prompt-only** — even with `preference="automatic"`, macros are never auto-saved. The agent's job is to record cleanly and surface the candidate; the user's job is to approve `macro_save`.

A single observation is harmless on its own. So record proactively — don't wait until you've spotted the repeat to start. By the time you see the second instance, you'd have to back-record the first one anyway.

**When to record an observation:**
- A multi-step workflow you're driving by hand (login, fill+submit a form, navigate-and-extract). Anything that's three or more browser tool calls in a deterministic sequence.
- A workflow that targets a persona/site combination you've seen before in this session.
- A workflow that would obviously break if you had to type its selectors again under time pressure.

**When NOT to record:**
- One-off exploration where the next steps depend on what you find.
- Single-action tasks (one click, one navigate) — those aren't macro-shaped.
- Anything driven by user input that varies per call (the `signature` must be stable, so if the only consistent thing is "the user said this", there's nothing to deduplicate on).

**Writing a good `signature`:**
- **Stable across runs.** No timestamps, UUIDs, search terms, dates, or per-call inputs. Two calls separated by an hour must produce the same string.
- **Identity-bearing.** Combine the *action shape* with the *target* — e.g. `"discord-login:dante"`, `"github-pr-comment:scenario-reviewer"`, `"canvas-claim-5-tiles:player-1"`. Don't use just the action (`"login"`) or just the target (`"discord"`).
- **Short.** Slug-style (`a-z0-9-:`), ideally under 60 chars. The advisor slugifies it for the suggestion `id`.

**Writing a good `summary`:**
- One short prose line a human will recognize at a glance: `"Logged in as Dante on Discord via the standard email-then-2FA flow"`.
- Don't paste selectors or URLs that contain credentials — `_redact_summary` strips obvious credential patterns but isn't bulletproof.

**After the candidate appears:**
- The next `octowright_status` or `octowright_advisor_status` call will show a `macro_candidate` suggestion under `advisor.suggestions`.
- **Surface it to the user.** Phrase it as a question, not a fait accompli: *"I've done X twice this session — want me to save it as a macro named `<name>`?"*
- If yes: call `macro_save(name, actions=[...])` building the action list from the workflow you just ran.
- If no: call `octowright_advisor_set_preference("macro_candidate", "no")` so it stops suggesting.
- If "do this automatically next time": set `"automatic"`. Even then, macros stay prompt-only by design — you still ask before saving. (The "automatic" semantics only apply to `profile_change` suggestions.)

**Don't:**
- Skip recording because "it might not repeat" — recording is cheap, the threshold is 2, and a single orphan observation costs nothing.
- Inflate signatures with per-call variables (e.g. `"search-wikipedia:python"` then `"search-wikipedia:rust"` — those don't match, no candidate).
- Save a macro without asking, even when the preference is `automatic`.

### 1. The Launch Protocol

Match the call to the launch shape; don't pay the suggest-tax on launches that obviously don't need it.

- **WHEN TO CALL `browser_suggest_for_url`**: a real-internet domain (`github.com`, `discord.com`, `gmail.com`, your work URLs) where the user gave a vague request and didn't name a persona. The suggest call exists to catch "I forgot Dante is already logged into discord.com".
- **WHEN TO SKIP IT**: loopback / `127.0.0.1` / `localhost` / `file://`, agent-internal scripted work (screenshots, smoke tests), or any launch where the user named the persona explicitly. The `browser_launch` tool itself documents this: *if `suggest_for_url` would report `ephemeral_ok: true`, the launch is fine without a profile.* Don't make the call just to confirm what's obvious.
- **PREFER**: when there *is* a suggested persona, use it — keeps session/cookies/credentials warm and saves you a manual login.
- **STABILIZE**: `stabilize: true` is for **deterministic test runs**, not "mission-critical" tasks. It freezes `Date.now()` to a fixed 2023 epoch, makes `requestAnimationFrame` synchronous, and kills CSS animations + transitions globally. Useful for golden-tree diffs and recorded macros that must replay identically. **Don't** set it on a user-facing browser or a real-site login — locking the clock at 2023 breaks anything time-aware (session expiry, scheduled UI, OTP windows).

### 2. Teardown — User-Facing vs Agent-Internal

Distinguish two kinds of browser launches; teardown discipline differs.

**Agent-internal** (you launched it to do scripted work the user won't see directly — taking a snapshot, recording a macro, verifying a fact):
- **ALWAYS** call `browser_close` immediately after the task finishes.
- **EMERGENCY**: Use `browser_close_all` if you lose track of session IDs.

**User-facing** (the user said "show me", "open", "navigate to", "launch", "let me see", or otherwise wants to look at or interact with the window):
- **LEAVE IT OPEN.** The user controls when to close. They may keep clicking, resizing, or inspecting after you finish.
- **DO NOT pass `viewport_w` / `viewport_h`** unless the user gave a specific size. Without those args, the launch defaults to a responsive viewport so the user can resize the window naturally — locking it to a fixed size breaks that affordance.
- **DO NOT** call `browser_close` or `browser_close_all` unless the user asks you to.
- If you genuinely need to take a screenshot of a user-facing browser, prefer the HTTP route `GET /api/sessions/{id}/screenshot/now` (returns image bytes inline; doesn't touch the user's window or close anything).

If you can't tell which kind of launch a task wants, lean toward leaving it open — the cost of a lingering browser is small; the cost of closing a window the user was still using is large.

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

## Session-Close Push Notifications

Octowright sends a JSON-RPC **push notification** to the MCP client whenever a
browser session leaves the pool — before the agent polls or tries a tool call.

**Method**: `notifications/octowright/session_closed`

**Params**:
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

**Reason values**:
- `agent_close` — you called `browser_close` or `browser_close_all`.
- `user_close` — the human closed the window (or the browser process exited
  cleanly). Playwright does not reliably distinguish "user clicked X" from a
  clean browser process exit, so both arrive as `user_close`.
- `shutdown` — the daemon is exiting (idle watchdog, SIGTERM, `octowright restart`).
- `external_disconnect` — reserved for future use; not emitted today.

**What to do when you receive one**:
1. Remove `instance_id` from any local tracking (open tabs, active macros, etc.).
2. If the window was **user-facing** (`reason == "user_close"` or `"shutdown"`), surface the closure to the user immediately — they may want to reopen it.
3. If the window was **agent-internal** and `reason == "user_close"`, reopen it if the task needs it; otherwise abort the task cleanly.
4. Do **not** call `browser_close` for a session that already closed — it will return a `KeyError`. The notification is the authoritative signal that the session is gone.

**Bridge propagation**: notifications flow through the follower→leader bridge automatically. If your MCP client is a follower, it still receives `notifications/octowright/session_closed` without any extra configuration.

## When Something's Wrong

Octowright has two distinct "is it working?" surfaces and they fail
independently. Diagnose correctly *before* killing anything.

### Decision flow

```dot
graph TD
    Symptom["MCP tool call returns 'Transport closed'<br/>or hangs"] --> Health{"curl http://127.0.0.1:8765/api/health<br/>returns 200?"}
    Health -- "Yes — daemon is alive" --> Retry["Retry one Octowright MCP call.<br/>The follower bridge should fail fast<br/>and reconnect for the next call."]
    Retry --> Smoke["If the same client handle still fails,<br/>run scripts/bridge_reconnect_smoke.py<br/>to separate client-handle failure<br/>from daemon failure."]
    Health -- "No — port doesn't answer" --> DaemonFix["The daemon itself is gone or wedged.<br/>Run: octowright restart"]
    DaemonFix --> Verify["Verify: curl /api/health → 200"]
    Verify --> Retry
```

### Transport Recovery

If an Octowright MCP call returns `Transport closed` or times out:

1. Check daemon health with `curl http://127.0.0.1:8765/api/health`.
2. If health is good, retry one Octowright MCP call. The follower bridge should
   now fail fast and reconnect for the next call.
3. If the same client handle still fails, run
   `uv run --active python scripts/bridge_reconnect_smoke.py` to distinguish a
   broken client handle from a broken daemon.
4. Do not run `octowright restart` unless daemon health fails or the user
   explicitly asks for a restart.

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
| Leaving an agent-internal browser open | Memory exhaustion, "Zombie" processes. | `browser_close` in a `finally` block logic. (User-facing browsers, where the user said "show me", stay open until *they* close them.) |
| Closing a user-facing browser the user is still looking at | User loses their view; resizing/inspection interrupted. | If the user said "show me", "open", "navigate", leave it open. Only `browser_close` when the user asks. |
| Passing `viewport_w` / `viewport_h` on a user-facing launch | Window viewport is locked; user can't resize naturally. | Omit the viewport args; the default is responsive. Only set a viewport when the user gave a specific size or the launch is for an internal screenshot. |
| Manual login repetition | High token usage, fragile scripts. | Use **Personas** to persist session state. |
| Guessing selectors | Frequent failures due to DOM changes. | Use `browser_snapshot` for the A11y tree. |
| Overlooking iframes | Tools fail to find elements that are visible. | Use `browser_list_frames` then `browser_switch_frame`. |

## Red Flags - STOP and Start Over
If you catch yourself doing these, you are violating the Octowright workflow:
- Launching a browser at a **real-internet domain** without calling `browser_suggest_for_url` when the user didn't name a persona. (Skipping suggest on `127.0.0.1`, `localhost`, `file://`, or user-named-persona launches is fine — see Section 1.)
- Attempting to "manually" log in when a Persona already exists.
- Forgetting to call `browser_close` after **agent-internal** work (close it! you can always re-launch).
- Calling `browser_close` on a **user-facing** browser the user asked you to open (don't — they're still using it).
- Guessing selectors after a failure instead of checking `browser_snapshot`.
- Setting `stabilize: true` on a user-facing browser or real-site login (it freezes the clock at 2023; breaks time-aware features).
- Driving a 3+ step workflow without recording an Advisor macro observation. The threshold for surfacing a macro candidate is two matching `signature` strings — if you don't record on the first occurrence, you can't reach two on the second.

## Rationalization Table

| Excuse | Reality |
| :--- | :--- |
| "It's just a quick check on github.com, I don't need a persona." | At a real-internet domain, raw browsers are fragile and lack cookies. `suggest_for_url` takes 2 seconds and saves minutes of manual login. (On `127.0.0.1` / `localhost` / `file://`, the "I don't need a persona" excuse is correct — skip suggest.) |
| "This workflow probably won't repeat, no point recording an Advisor observation." | Recording is one cheap tool call; observations FIFO out at 100. The candidate threshold is two matching signatures — if you don't record the first occurrence, you can't reach two on the second. Record proactively. |
| "I'll close the browser at the very end of the session." | If the agent crashes or exceeds its turn limit, the browser becomes a "Zombie". Close internal-use browsers AFTER EACH TASK. (User-facing browsers stay open — the user closes them.) |
| "I'll set viewport_w/h so the screenshot is reproducible." | Locks the user's window to a fixed viewport so they can't resize. Only set a viewport for agent-internal screenshot work, or when the user explicitly asked for a size. |
| "I know the selector by heart." | Sites change. `browser_snapshot` provides the A11y tree which is more durable than your memory. |

## Quick Reference

| Task | Tool |
| :--- | :--- |
| Find best persona (real-domain launches only) | `browser_suggest_for_url` |
| Check Advisor guidance | `octowright_advisor_status` |
| Record repeated workflow (call on every occurrence, stable signature) | `octowright_advisor_record_macro_observation(source="llm", signature, summary)` |
| Persist a preference | `octowright_advisor_set_preference(suggestion_type, preference)` |
| Start session | `browser_launch(kind, profile, ...)` |
| Robust interaction by aria role/label/test-id | `browser_click_by(role=, label=, test_id=, timeout_ms=)` |
| Wait for an element/text to appear | `browser_wait_for(instance_id, selector=, text=, timeout_ms=)` |
| Fix failing macro | `browser_snapshot` -> `macro_save` |
| Cleanup (agent-internal browser) | `browser_close` |
| Cleanup (user-facing browser) | Don't — user closes when they're done |
| Screenshot a user-facing window without disturbing it | `curl /api/sessions/{id}/screenshot/now` |
| Probe daemon health | `curl http://127.0.0.1:8765/api/health` |
| Daemon is wedged | `octowright restart` (shell, not MCP) |
| MCP `Transport closed` | Probe health, retry once, then run `scripts/bridge_reconnect_smoke.py` |
