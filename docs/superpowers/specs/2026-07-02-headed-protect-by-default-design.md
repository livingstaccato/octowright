# Headed browsers protect-by-default — design

**Date:** 2026-07-02
**Status:** approved (pre-implementation)

## Problem

octowright never auto-closes a live browser, but it does not stop an *agent*
from closing one. A coding agent's reflex `browser_close` after a screenshot
destroys a headed window the user was told to watch ("show me" → the agent
closes it a beat later). The only existing guard is `protected`, which is
opt-in per-launch or global-all via `OCTOWRIGHT_PROTECT_BROWSERS=1` — there is
no way to protect *user-facing* browsers while leaving *agent-internal*
headless scratch browsers freely closeable.

## Goal

Headed (user-facing) browsers resist a reflex `browser_close` by default, with
zero impact on headless/CI browsers, and an explicit escape hatch for both the
operator (env) and the agent (per-launch `protected=False` / `force=True`).

Non-goals: changing headless behavior; changing how octowright evicts a browser
the *user* closes externally; any new close-prevention mechanism beyond the
existing `protected` flag (we reuse it, we don't invent a parallel one).

## Behavior

### Effective-protection rule

When the caller does **not** pass `protected` explicitly, the launch path
computes the effective value:

```
if OCTOWRIGHT_PROTECT_BROWSERS == "1":      protected = True    # all (existing)
elif PROTECT_HEADED and resolved_headed and not ephemeral:
                                            protected = True    # new
else:                                       protected = False
```

Precedence (highest first):

1. Explicit `protected=True` / `protected=False` on the launch call — always wins.
2. `OCTOWRIGHT_PROTECT_BROWSERS=1` — protect every browser (existing behavior).
3. `OCTOWRIGHT_PROTECT_HEADED` (default **on**) — protect headed, non-ephemeral.
4. Otherwise unprotected.

### Carve-outs

- **Headless** browsers are never auto-protected (CI/agent-internal untouched).
- **Ephemeral** headed browsers stay closeable — `ephemeral` is an explicit
  "throwaway" signal that conflicts with "user will look at it," so it opts out
  of the headed default. (An operator who wants even ephemeral headed browsers
  protected can pass `protected=True` or set `OCTOWRIGHT_PROTECT_BROWSERS=1`.)
- Existing close-capable tools already honor `protected`, so auto-protecting a
  headed browser automatically covers `browser_close`, `browser_close_all`, and
  `browser_capture_and_close`.
- Internal rollback/teardown/crash-recovery paths (and scenario `stop`) already
  close with `force=True`; they are unaffected.

## Configuration

- New default `PROTECT_HEADED_DEFAULT` in `defaults.py`:
  `os.environ.get("OCTOWRIGHT_PROTECT_HEADED", "1").strip() != "0"` → **on** by
  default; `OCTOWRIGHT_PROTECT_HEADED=0` disables it. (Mirrors the existing
  `PROTECT_BROWSERS_DEFAULT` style.)
- `OCTOWRIGHT_PROTECT_BROWSERS=1` retains its meaning (protect all, incl.
  headless) and outranks the headed default.

## Implementation

The decision must happen where `headed` is **resolved** — `headed=None`
auto-detection and `OCTOWRIGHT_HEADLESS` are not known at the MCP-tool signature
layer. Therefore the tool signature only carries intent; the pool resolves it.

### Tool signatures (`server/browser/lifecycle.py`)

- `browser_launch` and `browser_quick_launch`: change `protected: bool =
  PROTECT_BROWSERS_DEFAULT` → `protected: bool | None = None`. `None` = "pool
  decides"; explicit `True`/`False` are passed through unchanged. Update the
  docstrings to describe the headed-default and the two escape hatches.

### Launch chokepoint (`browser_pool/options.py`)

- `options.py` already assembles launch kwargs and knows `resolved_headed`,
  `ephemeral`, and reads `PROTECT_BROWSERS_DEFAULT`. Add a small pure helper
  `resolve_protected(explicit, *, headed, ephemeral) -> tuple[bool, str]`
  returning `(protected, reason)` where `reason ∈ {"explicit", "all_default",
  "headed_default", "unprotected"}`. This is the single point every launch path
  flows through (`browser_launch`, `browser_quick_launch`, `spawn_roster`,
  scenarios), so the rule is enforced once.

### Session state (`session/core.py`)

- Store `protected_reason: str` alongside `protected` on the session so the
  refusal message can explain *why*. Default `"explicit"` for existing
  construction paths (back-compat).

### Refusal message (`browser_pool` close path / `errors.py`)

- When a close is refused, tailor the message by `protected_reason`:
  - `headed_default` → *"Browser {id} is headed/user-facing and protected by
    default (OCTOWRIGHT_PROTECT_HEADED). Pass force=True to close it, or relaunch
    with protected=False for scripted headed work."*
  - `explicit` / `all_default` → the current message (mentions `force=True`).

## Testing

Unit — `resolve_protected` matrix:

| explicit | headed | ephemeral | PROTECT_ALL | PROTECT_HEADED | → protected | reason |
|----------|--------|-----------|-------------|----------------|-------------|--------|
| True     | any    | any       | any         | any            | True        | explicit |
| False    | any    | any       | any         | any            | False       | explicit |
| None     | any    | any       | 1           | any            | True        | all_default |
| None     | True   | False     | 0           | on             | True        | headed_default |
| None     | True   | True      | 0           | on             | False       | unprotected |
| None     | False  | any       | 0           | on             | False       | unprotected |
| None     | True   | False     | 0           | off            | False       | unprotected |

Integration:

- `browser_launch` headed (default) → `session.protected is True`,
  `protected_reason == "headed_default"`; `browser_close` refuses without
  `force`; `force=True` closes.
- `browser_launch` headless → not protected → `browser_close` succeeds.
- `browser_launch(protected=False)` headed → closeable.
- `OCTOWRIGHT_PROTECT_HEADED=0` + headed → not protected.
- Refusal message for `headed_default` contains the tailored guidance.

## Docs

- AGENTS.md / CLAUDE.md: update the **Protected close behavior** section and add
  `OCTOWRIGHT_PROTECT_HEADED` to the env-var list.

## Risks / blast radius

- Headless (CI, agent-internal) is untouched → CI is unaffected.
- Interactive agents that launch a headed browser for scripted work and close it
  now hit a refusal; the tailored message tells them to pass `force=True` or
  relaunch `protected=False`. This is the intended friction (it protects the
  user's window); the escape hatch is one arg.
