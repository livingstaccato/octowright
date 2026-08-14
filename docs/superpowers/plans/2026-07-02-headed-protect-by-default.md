# Headed Browsers Protect-by-Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make headed (user-facing) browsers `protected` by default so a reflex `browser_close` can't destroy a window the user is watching, with zero impact on headless/CI browsers.

**Architecture:** Reuse the existing `protected` flag. Move the protect decision from the MCP-tool signature (where `headed` is still unresolved) to the pool launch path (where `headed` resolves). A pure `resolve_protected(explicit, headed, ephemeral) -> (bool, reason)` helper centralizes the rule; the `reason` drives a tailored close-refusal message.

**Tech Stack:** Python 3.11, pytest, dataclasses, FastMCP, Playwright.

## Global Constraints

- No file may exceed **500 LOC** (pre-commit `max loc` hook; `defaults.py` is already at its 550 ceiling — do not add lines to it beyond the one constant below, which fits).
- Env-var defaults live in `defaults.py` as named constants (project rule: no inline literals).
- AGENTS.md and CLAUDE.md must stay in sync (pre-commit `agent docs sync` hook) — any doc edit goes in **both**.
- Commit messages: Conventional Commits; **no** mention of AI/Claude.
- Run tests with `uv run --active pytest ... --no-cov -p no:randomly`.
- Precedence (highest first): explicit `protected` arg → `OCTOWRIGHT_PROTECT_BROWSERS=1` (all) → `OCTOWRIGHT_PROTECT_HEADED` (headed, default on) → unprotected.
- `protected_reason ∈ {"explicit", "all_default", "headed_default", "unprotected"}`.

---

### Task 1: `PROTECT_HEADED_DEFAULT` constant + `resolve_protected` helper

**Files:**
- Modify: `src/octowright/defaults.py:210` (add one constant after `PROTECT_BROWSERS_DEFAULT`)
- Modify: `src/octowright/browser_pool/options.py` (add `resolve_protected` + import `defaults` module-qualified)
- Test: `tests/test_resolve_protected.py` (create)

**Interfaces:**
- Produces: `defaults.PROTECT_HEADED_DEFAULT: bool` and
  `octowright.browser_pool.options.resolve_protected(explicit: bool | None, *, headed: bool, ephemeral: bool) -> tuple[bool, str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_resolve_protected.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The protect-by-default resolver (`browser_pool/options.resolve_protected`)."""

from __future__ import annotations

import pytest

from octowright import defaults
from octowright.browser_pool.options import resolve_protected


@pytest.mark.parametrize(
    ("explicit", "headed", "ephemeral", "protect_all", "protect_headed", "exp_protected", "exp_reason"),
    [
        (True, False, False, False, False, True, "explicit"),
        (False, True, False, True, True, False, "explicit"),
        (None, True, False, True, False, True, "all_default"),
        (None, True, False, False, True, True, "headed_default"),
        (None, True, True, False, True, False, "unprotected"),  # ephemeral headed opts out
        (None, False, False, False, True, False, "unprotected"),  # headless never
        (None, True, False, False, False, False, "unprotected"),  # protect_headed off
    ],
)
def test_resolve_protected_matrix(
    monkeypatch, explicit, headed, ephemeral, protect_all, protect_headed, exp_protected, exp_reason
):
    monkeypatch.setattr(defaults, "PROTECT_BROWSERS_DEFAULT", protect_all)
    monkeypatch.setattr(defaults, "PROTECT_HEADED_DEFAULT", protect_headed)
    assert resolve_protected(explicit, headed=headed, ephemeral=ephemeral) == (exp_protected, exp_reason)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --active pytest tests/test_resolve_protected.py --no-cov -p no:randomly`
Expected: FAIL — `ImportError: cannot import name 'resolve_protected'`.

- [ ] **Step 3: Add the constant to `defaults.py`**

After line 210 (`PROTECT_BROWSERS_DEFAULT: bool = ...`), add:

```python
# When OCTOWRIGHT_PROTECT_HEADED is on (default), a HEADED, non-ephemeral browser
# launches protected so an agent's reflex browser_close can't destroy a window
# the user is watching. Headless (CI/agent-internal) is untouched. `=0` opts out;
# outranked by OCTOWRIGHT_PROTECT_BROWSERS=1 (protect all).
PROTECT_HEADED_DEFAULT: bool = os.environ.get("OCTOWRIGHT_PROTECT_HEADED", "1").strip() != "0"
```

- [ ] **Step 4: Add `resolve_protected` to `options.py`**

At the top of `options.py`, ensure the module is imported qualified (so tests can monkeypatch the constants):

```python
from octowright import defaults
```

Add the helper (module level, near the top after imports):

```python
def resolve_protected(explicit: bool | None, *, headed: bool, ephemeral: bool) -> tuple[bool, str]:
    """Decide a browser's effective `protected` flag + the reason it was chosen.

    Precedence: an explicit arg wins; else OCTOWRIGHT_PROTECT_BROWSERS protects
    all; else a headed, non-ephemeral browser is protected by default; else not.
    The reason drives the close-refusal message.
    """
    if explicit is not None:
        return explicit, "explicit"
    if defaults.PROTECT_BROWSERS_DEFAULT:
        return True, "all_default"
    if defaults.PROTECT_HEADED_DEFAULT and headed and not ephemeral:
        return True, "headed_default"
    return False, "unprotected"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --active pytest tests/test_resolve_protected.py --no-cov -p no:randomly`
Expected: PASS (7 cases).

- [ ] **Step 6: Commit**

```bash
git add src/octowright/defaults.py src/octowright/browser_pool/options.py tests/test_resolve_protected.py
git commit -m "feat(pool): add resolve_protected + OCTOWRIGHT_PROTECT_HEADED default"
```

---

### Task 2: `LaunchOptions` sentinel + carry `protected_reason`

**Files:**
- Modify: `src/octowright/browser_pool/options.py:38` (field) and `:65` (from_mapping)
- Test: `tests/test_launch_options_protected.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `LaunchOptions.protected: bool | None` (default `None` = "pool decides") and `LaunchOptions.protected_reason: str` (default `"explicit"`), populated by the pool in Task 4.

- [ ] **Step 1: Write the failing test**

Create `tests/test_launch_options_protected.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""LaunchOptions carries an unset `protected` sentinel through from_mapping."""

from __future__ import annotations

from octowright.browser_pool.options import LaunchOptions


def test_protected_absent_is_none_sentinel():
    opts = LaunchOptions.from_mapping({"kind": "chromium"})
    assert opts.protected is None
    assert opts.protected_reason == "explicit"


def test_protected_explicit_true_carries_through():
    opts = LaunchOptions.from_mapping({"kind": "chromium", "protected": True})
    assert opts.protected is True


def test_protected_explicit_false_carries_through():
    opts = LaunchOptions.from_mapping({"kind": "chromium", "protected": False})
    assert opts.protected is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --active pytest tests/test_launch_options_protected.py --no-cov -p no:randomly`
Expected: FAIL — `test_protected_absent_is_none_sentinel` gets `False` (current default), not `None`.

- [ ] **Step 3: Change the field + from_mapping**

`options.py` line 38, change:

```python
    protected: bool = False
```
to:
```python
    protected: bool | None = None
    protected_reason: str = "explicit"
```

`options.py` line 65, change:

```text
            protected=options.get("protected", PROTECT_BROWSERS_DEFAULT),
```
to:
```text
            protected=options.get("protected"),
```

Remove the now-unused `from octowright.defaults import PROTECT_BROWSERS_DEFAULT` at line 42 **only if** nothing else in `from_mapping` uses it (grep first: `grep -n PROTECT_BROWSERS_DEFAULT src/octowright/browser_pool/options.py`; `resolve_protected` uses `defaults.PROTECT_BROWSERS_DEFAULT`, not this local import).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --active pytest tests/test_launch_options_protected.py --no-cov -p no:randomly`
Expected: PASS.

- [ ] **Step 5: Run the full options test file to check for regressions**

Run: `uv run --active pytest tests/ --no-cov -p no:randomly -k "launch_option or options"`
Expected: PASS (no test relied on the old `protected=False` default; if one does, it was asserting the pre-resolution default and should be updated to expect `None`).

- [ ] **Step 6: Commit**

```bash
git add src/octowright/browser_pool/options.py tests/test_launch_options_protected.py
git commit -m "feat(pool): LaunchOptions protected sentinel + protected_reason"
```

---

### Task 3: `BrowserSession.protected_reason` field

**Files:**
- Modify: `src/octowright/session/core.py:72` (add field after `protected`)
- Test: `tests/test_session_protected_reason.py` (create)

**Interfaces:**
- Produces: `BrowserSession.protected_reason: str` (default `"explicit"`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_protected_reason.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""BrowserSession exposes protected_reason (defaults to 'explicit')."""

from __future__ import annotations

from dataclasses import fields

from octowright.session.core import BrowserSession


def test_browser_session_has_protected_reason_field():
    names = {f.name for f in fields(BrowserSession)}
    assert "protected_reason" in names
    reason_field = next(f for f in fields(BrowserSession) if f.name == "protected_reason")
    assert reason_field.default == "explicit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --active pytest tests/test_session_protected_reason.py --no-cov -p no:randomly`
Expected: FAIL — `"protected_reason" not in names`.

- [ ] **Step 3: Add the field**

`session/core.py` line 72, after `protected: bool = False`, add:

```python
    protected_reason: str = "explicit"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --active pytest tests/test_session_protected_reason.py --no-cov -p no:randomly`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/session/core.py tests/test_session_protected_reason.py
git commit -m "feat(session): add protected_reason field"
```

---

### Task 4: Wire the resolution into the launch path

**Files:**
- Modify: `src/octowright/browser_pool/pool.py:146` (after `headless` resolves)
- Modify: `src/octowright/browser_pool/launch_pipeline.py:209-222` and `:403` (pass `protected_reason` to both `BrowserSession(...)` constructions)
- Test: `tests/test_protect_headed_launch_live.py` (create)

**Interfaces:**
- Consumes: `resolve_protected` (Task 1), `LaunchOptions.protected`/`.protected_reason` (Task 2), `BrowserSession.protected_reason` (Task 3).
- Produces: a launched session whose `.protected` / `.protected_reason` reflect the rule.

- [ ] **Step 1: Write the failing test**

Create `tests/test_protect_headed_launch_live.py` (uses the live-pool fixture pattern; check an existing live pool test e.g. `tests/test_pool_launch_live.py` for the exact fixture name and mirror it — likely `pool` built via `BrowserPool()` with `OCTOWRIGHT_HEADLESS` control):

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Headed browsers launch protected by default; headless do not."""

from __future__ import annotations

import pytest

from octowright import defaults


@pytest.mark.asyncio
async def test_headed_launch_is_protected_by_default(live_pool):
    res = await live_pool.launch({"kind": "chromium", "headed": True})
    session = live_pool.get(res["instance_id"])
    assert session.protected is True
    assert session.protected_reason == "headed_default"


@pytest.mark.asyncio
async def test_headless_launch_is_not_protected(live_pool):
    res = await live_pool.launch({"kind": "chromium", "headed": False})
    session = live_pool.get(res["instance_id"])
    assert session.protected is False


@pytest.mark.asyncio
async def test_explicit_false_overrides_headed_default(live_pool):
    res = await live_pool.launch({"kind": "chromium", "headed": True, "protected": False})
    session = live_pool.get(res["instance_id"])
    assert session.protected is False


@pytest.mark.asyncio
async def test_protect_headed_env_off(monkeypatch, live_pool):
    monkeypatch.setattr(defaults, "PROTECT_HEADED_DEFAULT", False)
    res = await live_pool.launch({"kind": "chromium", "headed": True})
    session = live_pool.get(res["instance_id"])
    assert session.protected is False


@pytest.mark.asyncio
async def test_ephemeral_headed_stays_closeable(live_pool):
    res = await live_pool.launch({"kind": "chromium", "headed": True, "ephemeral": True})
    session = live_pool.get(res["instance_id"])
    assert session.protected is False
```

Note: if the repo has no `live_pool` fixture, add one to `tests/conftest.py` mirroring the existing live-pool setup, or adapt to the fixture the other `*_live.py` pool tests use. Confirm with `grep -rn "def live_pool\|BrowserPool()" tests/`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --active pytest tests/test_protect_headed_launch_live.py --no-cov -p no:randomly`
Expected: FAIL — `session.protected is False` for the headed case (resolution not wired yet).

- [ ] **Step 3: Wire resolution in `pool.py`**

In `_launch_impl`, immediately after line 146
(`headless = not headed if headed is not None else HEADLESS_DEFAULT`), add:

```python
        # Decide the effective protected flag now that headed is resolved
        # (the tool layer passes None to mean "pool decides"). Headed,
        # non-ephemeral browsers protect by default so a reflex browser_close
        # can't destroy a window the user is watching.
        launch_options.protected, launch_options.protected_reason = resolve_protected(
            launch_options.protected, headed=not headless, ephemeral=launch_options.ephemeral
        )
```

Add the import near the other `browser_pool.options` imports in `pool.py`:

```python
from octowright.browser_pool.options import LaunchOptions, resolve_protected
```
(If `LaunchOptions` is already imported on its own line, extend that import.)

- [ ] **Step 4: Pass `protected_reason` to both `BrowserSession(...)` sites**

In `launch_pipeline.py`, at the construction near line 209-222, after `protected=launch_options.protected,` add:

```text
        protected_reason=launch_options.protected_reason,
```

Do the same at the second construction near line 403 (`protected=launch_options.protected,` → add the `protected_reason=` line beneath it).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --active pytest tests/test_protect_headed_launch_live.py --no-cov -p no:randomly`
Expected: PASS (5 cases).

- [ ] **Step 6: Regression — relaunch preserves protection**

Run the existing relaunch/handoff live tests to confirm a relaunched browser keeps its original protection (the relaunch path passes the original concrete `protected`, so `resolve_protected` sees an explicit bool):

Run: `uv run --active pytest tests/ --no-cov -p no:randomly -k "relaunch or handoff"`
Expected: PASS. If a relaunch now flips an explicitly-unprotected headed browser to protected, the relaunch path is passing `protected=None` instead of the original bool — fix by having the relaunch options carry `protected=<original session.protected>` before calling `pool.launch`.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/browser_pool/pool.py src/octowright/browser_pool/launch_pipeline.py tests/test_protect_headed_launch_live.py
git commit -m "feat(pool): protect headed browsers by default at launch"
```

---

### Task 5: MCP tool signatures — `protected` sentinel

**Files:**
- Modify: `src/octowright/server/browser/lifecycle.py:137` (`browser_launch`) and `:252` (`browser_quick_launch`) — change default; update docstrings at `:104`
- Test: `tests/test_browser_launch_protected_tool_live.py` (create)

**Interfaces:**
- Consumes: the wired pool (Task 4).
- Produces: `browser_launch` / `browser_quick_launch` with `protected: bool | None = None`, forwarding the sentinel to the pool.

- [ ] **Step 1: Write the failing test**

Create `tests/test_browser_launch_protected_tool_live.py` (mirror an existing `server/browser` live tool test for how the daemon/tool is driven; if the tools are called through the live pool + MCP, adapt accordingly):

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""browser_launch defaults protected to None (pool decides) → headed protected."""

from __future__ import annotations

import inspect

from octowright.server.browser import lifecycle


def test_browser_launch_protected_default_is_none():
    sig = inspect.signature(lifecycle.browser_launch)
    assert sig.parameters["protected"].default is None


def test_browser_quick_launch_protected_default_is_none():
    sig = inspect.signature(lifecycle.browser_quick_launch)
    assert sig.parameters["protected"].default is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --active pytest tests/test_browser_launch_protected_tool_live.py --no-cov -p no:randomly`
Expected: FAIL — default is `PROTECT_BROWSERS_DEFAULT` (a bool), not `None`.

- [ ] **Step 3: Change both signatures**

`lifecycle.py` line 137 and line 252, change:

```python
protected: bool = (PROTECT_BROWSERS_DEFAULT,)
```
to:
```python
protected: bool | None = (None,)
```

Remove the now-unused `PROTECT_BROWSERS_DEFAULT` import at `lifecycle.py:21` **only if** no other reference remains (`grep -n PROTECT_BROWSERS_DEFAULT src/octowright/server/browser/lifecycle.py`).

- [ ] **Step 4: Update the docstring at line ~104**

Replace the `protected` docstring paragraph with:

```
"protected: leave unset (None) to use the default policy — HEADED browsers are "
"protected automatically (a reflex close is refused) while headless ones stay "
"closeable. Pass protected=True to force protection, or protected=False to allow "
"a normal close (e.g. scripted headed work). OCTOWRIGHT_PROTECT_HEADED=0 disables "
"the headed default; OCTOWRIGHT_PROTECT_BROWSERS=1 protects everything. "
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --active pytest tests/test_browser_launch_protected_tool_live.py --no-cov -p no:randomly`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/server/browser/lifecycle.py tests/test_browser_launch_protected_tool_live.py
git commit -m "feat(server): browser_launch protected sentinel (pool decides)"
```

---

### Task 6: Tailored close-refusal message

**Files:**
- Modify: `src/octowright/browser_pool/lifecycle.py:34-38` (the `ProtectedBrowserCloseError` message)
- Test: `tests/test_protected_close_message.py` (create)

**Interfaces:**
- Consumes: `session.protected_reason` (Tasks 3-4).

- [ ] **Step 1: Write the failing test**

Create `tests/test_protected_close_message.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A refused headed-default close teaches the caller how to proceed."""

from __future__ import annotations

import pytest

from octowright.browser_pool.errors import ProtectedBrowserCloseError
from octowright.browser_pool.lifecycle import _protected_close_message  # pure helper (Step 3)


def test_headed_default_message_mentions_force_and_relaunch():
    msg = _protected_close_message("browser-1", "headed_default")
    assert "force=True" in msg
    assert "protected=False" in msg
    assert "headed" in msg.lower()


def test_explicit_message_is_generic():
    msg = _protected_close_message("browser-1", "explicit")
    assert "force=True" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --active pytest tests/test_protected_close_message.py --no-cov -p no:randomly`
Expected: FAIL — `ImportError: cannot import name '_protected_close_message'`.

- [ ] **Step 3: Extract a message helper + use it**

In `browser_pool/lifecycle.py`, add a pure helper near the top:

```python
def _protected_close_message(instance_id: str, reason: str) -> str:
    if reason == "headed_default":
        return (
            f"browser {instance_id!r} is headed/user-facing and protected by default "
            "(OCTOWRIGHT_PROTECT_HEADED). Pass force=True to close it, or relaunch with "
            "protected=False for scripted headed work."
        )
    return (
        f"browser {instance_id!r} is protected; pass force=True to close it. "
        "Protected browsers are meant to stay open for the user."
    )
```

Change the raise at lines 34-38 to:

```python
    if getattr(session, "protected", False) and not force:
        raise ProtectedBrowserCloseError(
            _protected_close_message(instance_id, getattr(session, "protected_reason", "explicit"))
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --active pytest tests/test_protected_close_message.py --no-cov -p no:randomly`
Expected: PASS.

- [ ] **Step 5: Regression — existing protected-close tests**

Run: `uv run --active pytest tests/ --no-cov -p no:randomly -k "protected"`
Expected: PASS. If a test asserts the exact old message string, update it to match the `explicit`-reason branch (unchanged wording).

- [ ] **Step 6: Commit**

```bash
git add src/octowright/browser_pool/lifecycle.py tests/test_protected_close_message.py
git commit -m "feat(pool): tailor protected-close refusal by reason"
```

---

### Task 7: Documentation

**Files:**
- Modify: `AGENTS.md` and `CLAUDE.md` — the **Protected close behavior** section + the env-var list (both files, identical edits — docs-sync hook)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update the Protected close behavior section**

In both `AGENTS.md` and `CLAUDE.md`, append to the "Protected close behavior" section:

```
Headed (user-facing) browsers are `protected` **by default** so an agent's
reflex `browser_close` can't destroy a window the user is watching: when a
launch doesn't pass `protected` explicitly, a resolved-headed, non-ephemeral
browser gets `protected=True` (reason `headed_default`), while headless
(CI/agent-internal) browsers stay closeable. Precedence: explicit `protected`
arg > `OCTOWRIGHT_PROTECT_BROWSERS=1` (all) > `OCTOWRIGHT_PROTECT_HEADED`
(headed, default on) > unprotected. The refusal message is tailored by
`session.protected_reason`. Ephemeral headed browsers stay closeable
(throwaway intent). Internal relaunch/handoff/teardown close with `force=True`
and are unaffected.
```

- [ ] **Step 2: Add the env var to the env-var list**

In both files' "Env Var Configuration" list, add:

```
- `OCTOWRIGHT_PROTECT_HEADED` — protect HEADED, non-ephemeral browsers at launch
  by default (a reflex `browser_close` is refused; `force=True` still closes).
  **ON by default**; `=0` disables. Headless is never auto-protected.
  Outranked by `OCTOWRIGHT_PROTECT_BROWSERS=1` (protect all). Parser/const:
  `defaults.PROTECT_HEADED_DEFAULT`; resolver `browser_pool.options.resolve_protected`.
```

- [ ] **Step 3: Verify docs sync + LOC**

Run: `uv run --active pre-commit run agent-docs-sync --all-files || true` (or `make lint`); also `wc -l AGENTS.md CLAUDE.md` — both edits must be identical.
Expected: docs-sync passes.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md CLAUDE.md
git commit -m "docs: headed browsers protect-by-default + OCTOWRIGHT_PROTECT_HEADED"
```

---

### Task 8: Full lint + suite

**Files:** none (verification).

- [ ] **Step 1: Full lint**

Run: `make lint`
Expected: exit 0. (Watch `max loc` on `options.py` and `lifecycle.py` — if either is near 500, the helper additions may push it over; if so, that file needs a split, which is out of scope here — flag it.)

- [ ] **Step 2: Targeted suite**

Run: `uv run --active pytest tests/ --no-cov -p no:randomly -k "protect or launch_option or session_protected or resolve_protected"`
Expected: PASS.

- [ ] **Step 3: Broader pool/server regression**

Run: `uv run --active pytest tests/ --no-cov -p no:randomly -k "pool or lifecycle or relaunch or handoff or scenario"`
Expected: PASS.

---

## Self-Review

**Spec coverage:**
- Effective-protection rule → Task 1 (`resolve_protected`) + Task 4 (wiring). ✓
- `OCTOWRIGHT_PROTECT_HEADED` default on → Task 1 constant. ✓
- Headless never / ephemeral carve-out → Task 1 matrix + Task 4 live tests. ✓
- Precedence (explicit > all > headed > off) → Task 1 order + tests. ✓
- Tool `protected: bool | None = None` sentinel → Task 5. ✓
- Resolve at chokepoint (`pool._launch_impl`) covering roster/scenarios → Task 4 (all launches flow through `_launch_impl`). ✓
- `protected_reason` on session → Task 3; tailored message → Task 6. ✓
- close_all / capture_and_close covered → they already honor `protected`; auto-protection covers them (no separate task needed). ✓
- Scenario teardown uses `force=True` → unaffected, noted in Task 4/docs. ✓
- Docs → Task 7. ✓

**Placeholder scan:** no TBD/TODO; every code step shows code; every run step shows command + expected. The only conditional instructions ("remove import only if unused", "mirror the live_pool fixture") are grounded guards with the exact grep to run, not vague hand-waves.

**Type consistency:** `resolve_protected(explicit: bool | None, *, headed: bool, ephemeral: bool) -> tuple[bool, str]` used identically in Task 1 and Task 4. `protected_reason: str` consistent across Tasks 2/3/4/6. Reasons `{"explicit","all_default","headed_default","unprotected"}` consistent. Sentinel `None` consistent Tasks 2/4/5.
