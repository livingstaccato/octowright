---
name: launch-and-personas
description: Reference for browser_suggest_for_url decision logic, stabilize flag, and user-facing vs agent-internal teardown discipline.
---

# Launch & Personas Reference

## When to Call `browser_suggest_for_url`

Call it when **all three** are true:
1. The URL is a real-internet domain (`github.com`, `discord.com`, `gmail.com`, your work URLs)
2. The user didn't explicitly name a persona
3. The launch isn't obviously agent-internal ephemeral work

Skip it when **any** of these apply:
- URL is loopback / `127.0.0.1` / `localhost` / `file://`
- User named the persona explicitly in their request
- Agent-internal scripted work (screenshots, smoke tests, golden diffs) where login state doesn't matter

The `browser_launch` tool docs say: *if `suggest_for_url` would report `ephemeral_ok: true`, the launch is fine without a profile.* Don't call suggest just to confirm what's obvious.

When suggest returns a persona, **use it** — keeps session/cookies/credentials warm and saves a manual login.

## `stabilize: true`

Use for **deterministic test runs only**: freezes `Date.now()` to a fixed 2023 epoch, makes `requestAnimationFrame` synchronous, kills CSS animations globally. This makes golden-tree diffs and macro replays produce identical output across runs.

**Do NOT** set it on:
- User-facing browsers — locking the clock at 2023 breaks anything time-aware
- Real-site logins — breaks session expiry, OTP windows, scheduled UI
- Any browser the user will interact with

## Teardown Discipline

### Agent-Internal Launches

Launched to do scripted work the user won't see directly: snapshots, macro recording, smoke tests, golden diffs, data extraction.

**Rules:**
- **Always** call `browser_close` immediately after the task finishes.
- Use `browser_close_all` if you lose track of session IDs.
- Never leave these open "for later" — if the agent crashes, the browser becomes a zombie.

### User-Facing Launches

The user said "show me", "open", "navigate to", "launch", "let me see", or otherwise wants to look at or interact with the window.

**Rules:**
- **Leave it open.** The user controls when to close.
- **Do not** pass `viewport_w` / `viewport_h` unless the user gave a specific size. Without those args, the launch defaults to a responsive viewport so the user can resize naturally.
- **Do not** call `browser_close` or `browser_close_all` unless the user asks.
- If you need a screenshot of a user-facing browser, use `GET /api/sessions/{id}/screenshot/now` — it returns image bytes without touching the window.

### When You Can't Tell

Lean toward leaving it open. The cost of a lingering browser is small; the cost of closing a window the user was still using is large.
