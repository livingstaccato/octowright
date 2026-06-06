---
name: octowright
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

## Rules

### 0. Advisor Check + Pattern → Macro

Bootstrap once per session: call `octowright_status`, inspect the `advisor` block, surface any `macro_candidate` suggestion before starting your task.

Record a macro observation for every multi-step deterministic workflow (3+ browser tool calls). Stable `signature` + one-line `summary`. Two matching signatures → candidate appears.

**Full details:** `reference/macros-and-advisor.md`

### 1. Launch Protocol

Match the call to the launch shape. Call `browser_suggest_for_url` for real-internet domains where the user didn't name a persona. Skip it for `127.0.0.1` / `localhost` / `file://` and agent-named personas.

**Full details:** `reference/launch-and-personas.md`

### 2. Teardown — User-Facing vs Agent-Internal

**Agent-internal** work: always call `browser_close` immediately after the task.

**User-facing** work (user said "show me", "open", "navigate", "let me see"): pass `protected=True` on launch. Leave the window open. Don't pass `viewport_w`/`viewport_h`. Don't call close-capable tools (`browser_close`, `browser_close_all`, `browser_capture_and_close`) unless asked — and even then you'll need `force=True` on a protected browser.

**Full details:** `reference/launch-and-personas.md`

### 3. Debugging Hierarchy

1. `browser_snapshot` → aria-tree
2. `browser_list_frames` → check for iframes
3. `golden_assert` → structural comparison

**Full details:** `reference/debugging.md`

### 4. Macro Management

Check `macro_list` before manually implementing a common flow. Update macros via `macro_save` when selectors change. Nominate repeated workflows via the Advisor.

**Full details:** `reference/macros-and-advisor.md`

## Common Mistakes

| Mistake | Consequence | Fix |
|:--------|:------------|:----|
| Leaving an agent-internal browser open | Memory exhaustion, zombie processes | `browser_close` after each task |
| Closing a user-facing browser the user is still using | User loses their view | Pass `protected=True` on user-facing launches; close-capable tools will refuse without `force=True` |
| Passing `viewport_w`/`viewport_h` on a user-facing launch | Viewport locked; user can't resize | Omit viewport args; only set for agent-internal screenshot work |
| Manual login repetition | High token usage, fragile scripts | Use Personas to persist session state |
| Guessing selectors | Frequent failures on DOM changes | Use `browser_snapshot` for the aria-tree |
| Overlooking iframes | Tools fail to find visible elements | `browser_list_frames` → `browser_switch_frame` |
| Driving 3+ steps without recording an Advisor observation | Macro candidate never surfaces | Record on first occurrence; threshold is two matching signatures |

## Rationalization Table

| Excuse | Reality |
|:-------|:--------|
| "It's just a quick check, I don't need a persona." | At a real-internet domain, raw browsers are fragile and lack cookies. `suggest_for_url` takes 2 seconds. (On `127.0.0.1`/`localhost`/`file://`, skip it.) |
| "This workflow probably won't repeat." | Recording is one cheap tool call. The candidate threshold is two matching signatures — skip the first occurrence and you can never reach two. |
| "I'll close the browser at the end of the session." | If the agent crashes or hits its turn limit, the browser becomes a zombie. Close internal-use browsers after each task. |
| "I'll set viewport_w/h so the screenshot is reproducible." | Locks the user's window so they can't resize. Only set a viewport for agent-internal screenshot work. |
| "I know the selector by heart." | Sites change. `browser_snapshot` gives the aria-tree, which is more durable. |

## Quick Reference

| Task | Tool |
|:-----|:-----|
| Find best persona (real-domain launches only) | `browser_suggest_for_url` |
| Check Advisor guidance | `octowright_advisor_status` |
| Record repeated workflow | `octowright_advisor_record_macro_observation(source="llm", signature, summary)` |
| Persist a preference | `octowright_advisor_set_preference(suggestion_type, preference)` |
| Start user-facing session | `browser_launch(protected=True)` — label/profile auto-set from git repo + username |
| Start agent-internal session | `browser_launch(ephemeral=True)` |
| Launch to a URL immediately | `browser_quick_launch(url, ...)` — same auto-label as `browser_launch` |
| Protect/unprotect after launch | `browser_set_protected(instance_id, protected=True/False)` |
| Click by ARIA role/label/text/test-id | `browser_click(instance_id, role=, label=, text=, test_id=, timeout_ms=)` |
| Fill by ARIA role/label/test-id | `browser_fill(instance_id, value, label=, role=, test_id=, timeout_ms=)` |
| Wait for element/text/JS expression | `browser_wait_for(instance_id, selector=, text=, expression=, timeout_ms=)` |
| Same action across N browsers | `browser_each(action, instance_ids?, ...)` — navigate \| resize \| evaluate \| wait_for \| screenshot |
| Fix failing macro | `browser_snapshot` → `macro_save` |
| Cleanup agent-internal browser | `browser_close` |
| Capture and close an agent-internal browser | `browser_capture_and_close`; pass `force=True` only if the protected browser close was explicitly requested |
| Screenshot user-facing window without disturbing it | `GET /api/sessions/{id}/screenshot/now` |
| Probe daemon health | `curl http://127.0.0.1:6286/api/health` |
| Daemon is wedged | `octowright restart` (shell, not MCP) |
| MCP `Transport closed` | See `reference/transport-recovery.md` |
| Session evicted unexpectedly | See `reference/transport-recovery.md` |

## Reference Files

- `reference/launch-and-personas.md` — launch decision tree, `stabilize`, teardown discipline
- `reference/macros-and-advisor.md` — Advisor pattern, signature writing, macro lifecycle
- `reference/debugging.md` — snapshot, frames, goldens, selector strategy
- `reference/transport-recovery.md` — push notifications, health check, bridge, restart flow

## Slash Commands

- `/octowright:record <task>` — record a new macro for a browser workflow
- `/octowright:replay <name>` — replay a saved macro against a session
- `/octowright:scenario <name>` — launch and orchestrate a named scenario
