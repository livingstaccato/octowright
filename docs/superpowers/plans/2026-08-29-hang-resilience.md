# Hang Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make octowright degrade gracefully when a browser target stops answering, instead of hanging forever — by bounding the Playwright calls Playwright itself will not bound, backstopping every other call site with an active-duration watchdog, giving an unresponsive target the same agent-facing taxonomy a crash already has, and reporting per-engine health so "this engine is broken on this box" is one call rather than an investigation.

**Architecture:** Four layers, innermost first. A `bounded()` helper wraps the handful of Playwright calls with no `timeout` parameter. The operation gate gains an opt-in active-duration ceiling covering call sites the helper misses. A timeout publishes `SessionCrashedEvent(scope="unresponsive")` onto the existing event bus, so the existing notification path carries it. The pool records per-engine outcomes and `octowright_status` surfaces them.

**Tech Stack:** Python 3.11+, Playwright async API, anyio/asyncio, pytest.

**Spec:** none — this originates from a live incident, recorded below.

---

## The incident this comes from

On 2026-08-29 a full `make ci` run hung twice, once for 12.6 hours. Diagnosis:

- Playwright's **WebKit crashed on this machine** in a six-line script containing no octowright code (`launch` → `new_page` → `goto('about:blank')` → `evaluate('1+1')`). Chromium was fine. A force-reinstall changed the symptom from *hang* to `TargetClosedError` but did not fix it.
- The suite hung in `session.evaluate`, which calls `self._target().evaluate(expression)` with **no timeout**. Playwright's `evaluate` accepts no `timeout` argument, so nothing bounded it.
- **`page.on("crash")` never fired**, because a wedged target does not crash — it stops answering. None of octowright's crash machinery engaged.
- The operation gate did not help: it bounds *queue wait* only. `_ACTIVE_DURATION` is a metric recorded after the fact, not a limit.
- An MCP caller would eventually have been freed by the heartbeat ceiling (`HEARTBEAT_MAX_SECONDS`, 600s). A pytest run, embedder, or CLI caller has nothing.

**The codebase already contains the fix, applied exactly once.** `session/core_io_mixin.py:248` reads:

```python
# Cap at 10s: pages with busy JS (SPAs retrying auth, WebSocket floods)
# can hold the CDP evaluation lock for 60+ seconds, which stalls the
# asyncio event loop and delays every other MCP response in the process.
html = await asyncio.wait_for(target.content(), timeout=10.0)
```

Someone hit this for `content()` and bounded it there. The treatment never spread to the seven sibling call sites.

---

## Global Constraints

- **No underscore-packed module names.** Hierarchy goes in directories: create `foo/bar.py`, never a `foo_bar.py` / `foo_baz.py` pair.
- **No logic in `__init__.py`, ever.** It carries imports, re-exports, and `__all__` — nothing else. Implementation lives in named sibling modules.
- Every Playwright access must sit under a literal named operation boundary (`scripts/check_operation_gate_architecture.py`, enforced **per function**, not per call graph). Never add an entry to `scripts/_operation_gate_inventory.py` to silence it.
- File LOC cap **777** (`scripts/check_max_loc.py`); 4-line SPDX header on every Python file.
- `AGENTS.md` and `CLAUDE.md` stay byte-identical — edit `AGENTS.md`, then `cp AGENTS.md CLAUDE.md`.
- New metrics must be documented in `AGENTS.md`'s metrics table or `scripts/check_telemetry_docs.py` fails.
- Commit with `UV_FROZEN=1 git commit`; conventional-commit types; never `--no-verify`/`--no-gpg-sign`; no `Co-Authored-By` trailer; no mention of AI assistance.
- Never use `git checkout --`, `git stash`, or `git reset`.
- **A live octowright daemon runs on port 6286 in this checkout — never restart or kill it.**
- **Playwright WebKit is broken on the authoring machine.** Run tests with `-k "not webkit"` or accept WebKit failures as environmental; do not "fix" unrelated code chasing them.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/octowright/session/timeouts.py` **(new)** | `SessionCallTimeoutError`, the resolved budget, and the `bounded()` wrapper. Single-word module name — no underscore packing. |
| `src/octowright/session/core_page_mixin.py` **(modify)** | Bound 3 sites (2× `page.title()`, 1× `evaluate`). |
| `src/octowright/session/core_expect_mixin.py` **(modify)** | Bound 1 site (`evaluate`). |
| `src/octowright/session/core_ops_mixin.py` **(modify)** | Bound 3 sites (2× `page.title()`, 1× `page.content()`). |
| `src/octowright/session/core_io_mixin.py` **(modify)** | Route the existing 10s `wait_for` through `bounded()`, keeping its 10s budget and its comment. |
| `src/octowright/browser_pool/events.py` **(modify)** | Add `"unresponsive"` to `CrashScope`. |
| `src/octowright/session/operation_gate.py` **(modify)** | Opt-in active-duration ceiling. |
| `src/octowright/browser_pool/pool.py` **(modify)** | Per-engine health record. |
| `src/octowright/server/meta.py` **(modify)** | Surface `engine_health` under `octowright_status()["pool"]`. |
| `AGENTS.md` / `CLAUDE.md` **(modify)** | Document the knobs, the new crash scope, and the new metrics. |
| `tests/session/test_timeouts.py` **(new)** | Unit tests for the helper and its error. |
| `tests/session/test_operation_gate_active_timeout.py` **(new)** | Watchdog tests against a fake clock. |
| `tests/test_engine_health.py` **(new)** | Per-engine health record + status surface. |

---

### Task 1: Bound the calls Playwright will not bound

**Files:**
- Create: `src/octowright/session/timeouts.py`, `tests/session/test_timeouts.py`
- Modify: `core_page_mixin.py` (lines 316, 480, 484), `core_expect_mixin.py` (114), `core_ops_mixin.py` (151, 167, 284), `core_io_mixin.py` (248)

**Interfaces produced:**
- `class SessionCallTimeoutError(RuntimeError)`
- `def unbounded_call_timeout_seconds() -> float`
- `async def bounded(awaitable: Awaitable[T], *, operation: str, timeout: float | None = None) -> T`

**Design decision — this one is ON by default, unlike the repo's other new quotas.** `OCTOWRIGHT_WEBSOCKET_MAX_BYTES` and friends default off because they trade a working behaviour for a limit. This trades *hanging forever* for *failing in 30 seconds*, and the existing `content()` precedent is already unconditionally on at 10s. A default-off hang guard would not have caught this incident. `OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS` tunes it; a falsey token (`0`/`off`/`never`/`none`/`disabled`) restores the unbounded behaviour for anyone who needs it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/session/test_timeouts.py
import asyncio
import pytest

from octowright.session.timeouts import (
    SessionCallTimeoutError,
    bounded,
    unbounded_call_timeout_seconds,
)


async def test_returns_the_value_when_the_call_completes() -> None:
    async def quick() -> int:
        return 7

    assert await bounded(quick(), operation="browser_evaluate") == 7


async def test_raises_a_typed_error_when_the_call_hangs() -> None:
    async def wedged() -> None:
        await asyncio.sleep(3600)

    with pytest.raises(SessionCallTimeoutError, match="browser_evaluate"):
        await bounded(wedged(), operation="browser_evaluate", timeout=0.01)


async def test_the_error_names_the_budget_so_the_message_is_actionable() -> None:
    async def wedged() -> None:
        await asyncio.sleep(3600)

    with pytest.raises(SessionCallTimeoutError, match="0.01"):
        await bounded(wedged(), operation="browser_title", timeout=0.01)


def test_budget_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hang guard that defaults off would not have caught the incident."""
    monkeypatch.delenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", raising=False)
    assert unbounded_call_timeout_seconds() > 0


@pytest.mark.parametrize("token", ["0", "off", "never", "none", "disabled", "false", "no"])
def test_falsey_tokens_restore_unbounded_behaviour(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", token)
    assert unbounded_call_timeout_seconds() == 0.0


@pytest.mark.parametrize("raw", ["", "abc", "-5", "nan"])
def test_unparsable_or_nonpositive_falls_back_to_the_default(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never silently unbound on a typo — that is the failure mode being fixed."""
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", raw)
    assert unbounded_call_timeout_seconds() > 0


async def test_a_disabled_budget_does_not_wrap() -> None:
    async def quick() -> int:
        return 3

    assert await bounded(quick(), operation="browser_evaluate", timeout=0.0) == 3
```

- [ ] **Step 2: Run to verify it fails**

`uv run --active pytest tests/session/test_timeouts.py -q --no-cov` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement the module**

```python
# src/octowright/session/timeouts.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Budgets for the Playwright calls Playwright itself will not bound.

``click``/``type``/``goto`` all accept a ``timeout`` and octowright passes
``DEFAULT_ACTION_TIMEOUT_MS``. ``evaluate``, ``title`` and ``content`` accept
none, so a target that stops answering hangs the calling coroutine forever --
observed on 2026-08-29 as a full test suite wedged for 12.6 hours against a
broken WebKit, with ``page.on("crash")`` silent because a wedged target never
crashes, it just stops replying.

ON by default, unlike this repo's other new quotas: those trade a working
behaviour for a limit, while this trades hanging forever for failing in
thirty seconds. ``core_io_mixin``'s pre-existing 10s cap on ``content()`` is
the same call, already unconditional.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_UNBOUNDED_CALL_TIMEOUT_SECONDS = 30.0

_OFF_TOKENS = frozenset({"0", "off", "never", "none", "disabled", "false", "no"})


class SessionCallTimeoutError(RuntimeError):
    """A Playwright call with no timeout of its own outran its budget.

    Session-scoped, like the operation-gate errors: it means this one target
    stopped answering, never that the MCP transport should be restarted.
    """


def unbounded_call_timeout_seconds() -> float:
    """``OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS`` -- 0.0 means unbounded.

    Unparsable and non-positive values fall back to the default rather than
    disabling the guard: a typo must not silently reintroduce the hang this
    exists to prevent. Disabling is only ever an explicit falsey token.
    """
    raw = os.environ.get("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", "").strip().lower()
    if raw in _OFF_TOKENS:
        return 0.0
    if not raw:
        return DEFAULT_UNBOUNDED_CALL_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_UNBOUNDED_CALL_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_UNBOUNDED_CALL_TIMEOUT_SECONDS
    return value


async def bounded(awaitable: Awaitable[T], *, operation: str, timeout: float | None = None) -> T:
    """Await *awaitable* under a budget, raising ``SessionCallTimeoutError``.

    ``timeout=None`` resolves from the environment; ``0.0`` awaits unbounded.
    The operation name is in the message because the raised error is what an
    operator or agent sees -- ``asyncio.TimeoutError`` alone names nothing.
    """
    budget = unbounded_call_timeout_seconds() if timeout is None else timeout
    if budget <= 0:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=budget)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise SessionCallTimeoutError(
            f"{operation} did not answer within {budget}s -- the browser target is "
            "unresponsive. Relaunch this session; other sessions are unaffected."
        ) from exc
```

- [ ] **Step 4: Run to verify it passes**

- [ ] **Step 5: Apply at the seven unbounded sites**

Each becomes `await bounded(<the call>, operation="<the gated operation name>")`. Use the operation name already on the enclosing `@gated_operation` decorator so the message matches what telemetry shows. Sites:

| File | Line | Call |
|---|---|---|
| `core_page_mixin.py` | 316 | `self.page.title()` |
| `core_page_mixin.py` | 480 | `self.page.title()` |
| `core_page_mixin.py` | 484 | `self._target().evaluate(expression)` |
| `core_expect_mixin.py` | 114 | `self._target().evaluate(expression)` |
| `core_ops_mixin.py` | 151 | `self.page.title()` |
| `core_ops_mixin.py` | 167 | `self.page.content()` |
| `core_ops_mixin.py` | 284 | `self.page.title()` |

Then migrate `core_io_mixin.py:248` to `await bounded(target.content(), operation="browser_read_markdown", timeout=10.0)` — **keep its explicit 10s and its existing comment**; that budget was chosen for a documented reason (CDP evaluation lock) and is not the general one.

- [ ] **Step 6: Verify the gate scanner and LOC still pass**

`uv run --active python scripts/check_operation_gate_architecture.py && uv run --active python scripts/check_max_loc.py`

- [ ] **Step 7: Commit**

```bash
git add src/octowright/session/timeouts.py tests/session/test_timeouts.py src/octowright/session/core_*.py
UV_FROZEN=1 git commit -m "fix(session): bound the playwright calls playwright will not bound"
```

---

### Task 2: An unresponsive target gets the crash taxonomy

**Files:**
- Modify: `src/octowright/browser_pool/events.py`, `src/octowright/session/timeouts.py` (or the call sites), `AGENTS.md`/`CLAUDE.md`
- Test: `tests/test_unresponsive_target_event.py` **(new)**

**Interfaces consumed:** `SessionCallTimeoutError` from Task 1.

The problem this closes: a `SessionCallTimeoutError` currently surfaces as a bare tool error. Octowright already has a good taxonomy for a dead browser — `SessionCrashedEvent`, the `browser_crashed` notification with a `recovering` flag, `octowright_browser_crashed_total` — and an unresponsive target belongs in it.

- [ ] **Step 1: Widen `CrashScope`**

`src/octowright/browser_pool/events.py:18` currently reads:

```python
CrashScope = Literal["renderer", "process"]
```

becomes:

```python
# "renderer" is a Playwright page.on("crash"); "process" additionally took the
# browser down. "unresponsive" is neither -- the target is alive and simply
# stopped answering, which no Playwright event reports, so it is raised by the
# call budget in session/timeouts.py rather than observed.
CrashScope = Literal["renderer", "process", "unresponsive"]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_unresponsive_target_event.py
"""A wedged target must reach the agent through the crash taxonomy.

`page.on("crash")` is silent for an unresponsive target, so without this the
only signal is a raw error string and the agent cannot tell "relaunch this
session" from "the transport died".
"""

from octowright.browser_pool.events import SessionCrashedEvent


def test_unresponsive_is_a_valid_crash_scope() -> None:
    event = SessionCrashedEvent(
        instance_id="abc123",  # pragma: allowlist secret (fake instance id)
        kind="webkit",
        label=None,
        profile=None,
        scope="unresponsive",
        log_path="/tmp/x.jsonl",
    )
    assert event.scope == "unresponsive"
```

Extend this with a test that a `SessionCallTimeoutError` raised inside a gated session operation publishes exactly one `SessionCrashedEvent(scope="unresponsive")` on the pool's event bus. Read `tests/` for the established way to observe bus events and follow it.

- [ ] **Step 3: Publish the event when the budget is exceeded**

Wire it where the session can reach the pool's event bus — follow how `SessionCrashedEvent(scope="renderer")` is published from `browser_pool/listeners.py`, and mirror that path.

**Do not auto-recover an unresponsive target.** Renderer-crash recovery replaces the dead page, which is right for a crash and wrong here: the target may still be executing, and replacing it can thrash a browser that is merely slow. Surface and notify; let the caller decide. Say so in a comment.

- [ ] **Step 4: Document it**

In `AGENTS.md`'s crash/notification prose, note the third scope and why it exists (no Playwright event reports it), and that it does not auto-recover. Then `cp AGENTS.md CLAUDE.md` and run `scripts/check_agent_docs_sync.py`.

- [ ] **Step 5: Commit**

---

### Task 3: An active-duration ceiling on the operation gate

**Files:**
- Modify: `src/octowright/session/operation_gate.py`, `AGENTS.md`/`CLAUDE.md`
- Test: `tests/session/test_operation_gate_active_timeout.py` **(new)**

Task 1 bounds the seven call sites known today. This is the backstop for the ones nobody has found yet: the gate already knows `_active_since` and `_root_operation` for every in-flight operation, so it can notice one that has been running impossibly long without anyone enumerating call sites.

**This one is OFF by default**, unlike Task 1 — cancelling in-flight browser work is a heavier intervention than failing one call, and the repo's convention for new backstop quotas is opt-in. `OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS` enables it.

- [ ] **Step 1: Write the failing tests**

Cover, against a fake clock (follow the existing fake-clock pattern in `tests/session/test_operation_gate.py`):

1. Disabled by default — an operation active far past any budget is untouched when the env var is unset.
2. When enabled, an operation exceeding the ceiling is cancelled and the gate transitions to `broken`.
3. A subsequent operation on that gate is rejected fast with the gate's existing broken-state message, not queued.
4. An operation that finishes inside the ceiling is never touched.
5. `octowright_operation_active_timeout_total` increments exactly once per breach.
6. **Other sessions' gates are unaffected** — the whole point is that one wedged session fails alone.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

Resolve the budget with the same parser shape as Task 1 (falsey tokens off; unparsable falls back — here, falls back to *off*, since the feature itself is opt-in). On breach: record the metric, log at warning with the operation name and duration, cancel the owning task, and drive the gate to `broken` through its existing invariant path so the state machine stays consistent.

**Do not add a per-gate background task.** One timer per session multiplied by a large pool is real overhead for a rare event; prefer checking active duration from the existing housekeeping cycle, which already runs periodically and already walks sessions. Read `src/octowright/housekeeping.py` and add a job there following the numbered-job convention its module docstring describes.

- [ ] **Step 4: Document the knob and the metric**

`AGENTS.md`: the env-var list, and the metrics table (**required** — `scripts/check_telemetry_docs.py` fails on an undocumented metric). Then `cp AGENTS.md CLAUDE.md`.

- [ ] **Step 5: Commit**

---

### Task 4: Per-engine health in `octowright_status`

**Files:**
- Modify: `src/octowright/browser_pool/pool.py`, `src/octowright/server/meta.py`, `AGENTS.md`/`CLAUDE.md`
- Test: `tests/test_engine_health.py` **(new)**

Diagnosing the incident took an hour largely to establish "WebKit is broken on this machine, Chromium is fine." The pool sees every launch and every failure per engine and can simply say so.

- [ ] **Step 1: Write the failing tests**

Cover: a successful launch records `ok` for that kind with a timestamp; a failed launch records the error class; each kind is tracked independently (chromium healthy while webkit failing); the block is present in `octowright_status()["pool"]["engine_health"]`; and a kind never launched is absent rather than falsely reported healthy.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

Record per-kind outcomes in `BrowserPool` next to the existing launch bookkeeping (see `browser_pool/_metrics.py` for where launches are already observed). Surface under `octowright_status()["pool"]["engine_health"]` as, per kind, the last outcome, an ISO timestamp, and the error class name on failure.

**Record only the exception class name, never the message.** A launch failure message can carry a filesystem path or a profile name; the class name is the diagnostic signal and carries nothing sensitive. This mirrors why `octowright_browser_launch_failed_total` labels on `error` (the class) rather than the message.

- [ ] **Step 4: Document it** in `AGENTS.md`'s status/observability prose, then `cp AGENTS.md CLAUDE.md`.

- [ ] **Step 5: Commit**

---

## Self-Review

**Spec coverage:** no external spec; the four tasks map one-to-one onto the four gaps in the incident write-up (unbounded calls → T1, no taxonomy for wedged → T2, gate bounds queue not active duration → T3, engine health invisible → T4).

**Placeholder scan:** Tasks 1's code is complete and literal. Tasks 2-4 deliberately name the file and the pattern to follow (`listeners.py` for bus publication, `test_operation_gate.py` for the fake clock, `housekeeping.py` for the job convention) rather than inventing surrounding code I have not read line-by-line — each is a named lookup in a named file, not a "figure it out".

**Type consistency:** `bounded()`/`SessionCallTimeoutError`/`unbounded_call_timeout_seconds()` are defined in T1 and consumed by name in T2. `CrashScope`'s third member is added in T2 and used nowhere else.

**Ordering:** T1 before T2 (T2 consumes its error type). T3 and T4 are independent of both and of each other.

**Known risk:** WebKit is broken on the authoring machine, so the very engine that motivated this cannot be used to verify it end-to-end there. T1's tests use a plain `asyncio.sleep` rather than a real wedged browser precisely so they do not depend on reproducing the incident.
