# browser_a11y_dragdrop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `browser_a11y_dragdrop`, an MCP tool that drives keyboard (WAI-ARIA APG) drag-and-drop — grab with a key, move with keys, drop with a key, poll-verify, release on failure — as the accessible counterpart to the mouse-only `browser_drag`.

**Architecture:** A pure engine module (`session/a11y_dragdrop.py`) holds the grab → navigate → drop → verify → release state machine and is unit-tested against a fake page with no browser. A thin `@gated_operation` session method delegates to it and records the action; a new MCP tool module exposes it; `_ACTION_MAP` and the macro linter make it replayable. The engine takes `session` as its first parameter and opens its own literal `session.operation(...)` boundary — copying `session/aria_redaction.py` — because the operation-gate architecture scanner is per-function syntax analysis and will not follow a call graph.

**Tech Stack:** Python 3.11+, Playwright (async API), the `mcp` MCPServer, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-13-browser-a11y-dragdrop-design.md`

---

## Spec corrections — read before Task 1

The spec is approved and its behavioural design stands unchanged. Four of its
**architectural** claims were checked against `main` and are wrong or
incomplete. Where this plan and the spec disagree, **this plan wins**; each
correction below is evidence-backed.

1. **The LOC argument is void.** Spec §3 says both mixins "have no headroom"
   against a 500-line ceiling. The ceiling is **777** (`scripts/check_max_loc.py:10`),
   and the files are `core_ops_mixin.py` 463, `core_page_mixin.py` 535,
   `server/browser/input.py` 381. All have room.
   **The new-files decision is kept anyway, on different grounds:** the engine
   is a state machine worth testing without a browser, which requires it to be
   importable and callable independently of the mixin. Do not cite LOC as the
   reason in comments or commit messages.

2. **The spec predates the Browser Session Operation Gate, and its proposed
   engine signature fails lint.** `scripts/check_operation_gate_architecture.py`
   (in `make lint`) statically requires every Playwright access to sit under a
   *literal* named operation boundary. Spec §3 proposes
   `run_a11y_dragdrop(target, **params)` — "no recorder dependency", taking a
   Playwright target directly. That form was probed against the real scanner:

   ```python
   # src/octowright/session/_gateprobe.py
   async def run_thing(target, key: str) -> None:
       await target.press(key)
   ```
   ```
   Ungated or unclassified Playwright access found:
     - src/octowright/session/_gateprobe.py:2 in run_thing (ungated Playwright access)
   ```

   `target` is in the scanner's `SEED_PARAM_NAMES`, so the access is seen and
   demanded to be gated. None of the four `APPROVED_BYPASS_CLASSES`
   (`event-critical`, `teardown-only`, `cached-property-only`,
   `launch-time-before-session-publication`) describe an ordinary user action.

3. **The fix is the `aria_redaction.py` pattern.** That module is the working
   precedent for a helper that touches Playwright: every function takes
   `session: Any` first and opens `async with session.operation("<literal>"):`
   around its Playwright calls. `gated_operation` is re-entrant for the same
   task, so nesting inside the mixin's own lease is free rather than a
   deadlock. Its own docstring states the reason to copy verbatim: *"Gating it
   here, rather than trusting that every current and future caller already
   holds one, is what keeps this call site visible to the operation-gate
   architecture scanner as gated on its own terms."*

4. **`Frame` has no `.keyboard`.** Spec §3 says the engine "takes a Playwright
   Page/Frame-like target". Measured on the installed Playwright:
   `Page.keyboard` exists, **`Frame.keyboard` does not**; `Locator.press` and
   `Locator.focus` do. So element lookup goes through `session._target()`
   (page *or* frame, per `session/core.py:228`) while free keystrokes go
   through `session.page.keyboard`. An engine that reached for
   `target.keyboard` would crash on any frame-scoped call.

---

## Global Constraints

- File LOC cap is **777** lines, enforced by `scripts/check_max_loc.py` in `make lint`.
- Every Playwright access must sit under a literal named operation boundary (`scripts/check_operation_gate_architecture.py`). Operation names are validated at import time and must be string literals.
- `AGENTS.md` and `CLAUDE.md` must stay **byte-identical** (`scripts/check_agent_docs_sync.py`). Edit `AGENTS.md`, then `cp AGENTS.md CLAUDE.md`.
- Commits use conventional-commit types (`feat:`, `fix:`, `test:`, `docs:`), enforced by commitlint. Never add a `Co-Authored-By` trailer; never mention AI assistance.
- Commit with `UV_FROZEN=1 git commit` so the lockfile check does not re-resolve.
- Never bypass commit signing (`--no-gpg-sign` / `--no-verify`).
- Exactly one `verify_*` field is required on every call — zero silently "succeeds" without checking anything, more than one is ambiguous about which gates success (spec §4).
- Default keys are `grab_key="Space"`, `drop_key="Space"`, `release_key="Escape"`, configurable — not hardcoded (spec §2).
- The tool returns its result dict on ordinary verify failure and raises only for infrastructure failures (spec §7).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/octowright/session/a11y_dragdrop.py` **(new)** | The engine. Validation, the grab→nav→drop→verify→release state machine, the poll loop. Takes `session` first; opens its own `session.operation("browser_a11y_dragdrop")` lease. No recorder, no MCP types. |
| `src/octowright/session/core_ops_mixin.py` **(modify)** | Thin `@gated_operation("browser_a11y_dragdrop")` delegate that calls the engine and records the action. |
| `src/octowright/server/browser/a11y.py` **(new)** | The `@mcp.tool browser_a11y_dragdrop` wrapper plus its WAI-ARIA APG docstring. |
| `src/octowright/server/browser/__init__.py` **(modify)** | Import the new module. **Without this the decorator never runs and the tool silently does not exist.** |
| `src/octowright/macros/runtime.py` **(modify)** | `_ACTION_MAP["a11y_dragdrop"] = "a11y_dragdrop"`. |
| `src/octowright/macros/lint.py` **(modify)** | `_SIMPLE_REQUIRED` entry + a verify-arity check mirroring the tool's own validation. |
| `AGENTS.md` / `CLAUDE.md` **(modify)** | Feature section + tool-count correction (130 → 131). |
| `tests/session/test_a11y_dragdrop.py` **(new)** | Engine unit tests against a fake page. No browser. |
| `tests/fixtures/a11y_dragdrop.html` **(new)** | Self-hosted page implementing both APG variants. |
| `tests/test_a11y_dragdrop_live.py` **(new)** | Integration test driving the fixture through the real tool. |

`allowed_fields_for` in `macros/lint_fields.py` needs **no** edit: it derives
the permitted field set from the session method's signature
(`_session_method_params`), so adding the method is enough. Adding a literal
list there would be the hand-maintained drift that module exists to avoid.

---

### Task 1: The engine

**Files:**
- Create: `src/octowright/session/a11y_dragdrop.py`
- Test: `tests/session/test_a11y_dragdrop.py`

**Interfaces:**
- Consumes: `session.operation(name)` (async context manager, re-entrant per task); `session._target()` → Page-or-Frame with `.locator(sel)`; `session.page.keyboard.press(key)`.
- Produces:
  - `async def run_a11y_dragdrop(session: Any, *, source_selector: str, nav_key: str = "tab", nav_direction: str | None = None, nav_key_sequence: list[str] | None = None, max_nav_steps: int = 12, grab_key: str = "Space", drop_key: str = "Space", release_key: str = "Escape", grabbed_predicate_js: str | None = None, verify_js: str | None = None, verify_selector_appears: str | None = None, verify_selector_gone: str | None = None, verify_text_contains: str | None = None, verify_timeout_ms: int = 2000, verify_poll_ms: int = 100) -> dict[str, Any]`
  - `def validate_params(...) -> None` — raises `ValueError`; called before any page interaction.
  - `class A11yDragDropError(Exception)` — infrastructure failures only.
  - Result keys: `ok`, `grabbed`, `dropped`, `verified`, `released`, `stage_reached`, `nav_steps_taken`.
  - `stage_reached` ∈ `{"failed_grab", "navigated", "dropped", "verified", "failed_verify"}`.

- [ ] **Step 1: Write the failing validation tests**

```python
# tests/session/test_a11y_dragdrop.py
import pytest

from octowright.session.a11y_dragdrop import validate_params


def test_rejects_zero_verify_fields() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_params(nav_key="tab", nav_key_sequence=None, verify_fields_set=0)


def test_rejects_two_verify_fields() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_params(nav_key="tab", nav_key_sequence=None, verify_fields_set=2)


def test_keys_mode_requires_a_sequence() -> None:
    with pytest.raises(ValueError, match="nav_key_sequence"):
        validate_params(nav_key="keys", nav_key_sequence=None, verify_fields_set=1)


def test_non_keys_mode_rejects_a_sequence() -> None:
    with pytest.raises(ValueError, match="nav_key_sequence"):
        validate_params(nav_key="tab", nav_key_sequence=["Tab"], verify_fields_set=1)


def test_accepts_the_valid_shapes() -> None:
    validate_params(nav_key="tab", nav_key_sequence=None, verify_fields_set=1)
    validate_params(nav_key="arrow", nav_key_sequence=None, verify_fields_set=1)
    validate_params(nav_key="keys", nav_key_sequence=["Tab", "Enter"], verify_fields_set=1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --active pytest tests/session/test_a11y_dragdrop.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.session.a11y_dragdrop'`

- [ ] **Step 3: Create the module with validation only**

```python
# src/octowright/session/a11y_dragdrop.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Keyboard (WAI-ARIA APG) drag-and-drop: grab, navigate, drop, verify, release.

Takes ``session`` first and opens its own literal ``session.operation(...)``
lease, exactly like ``session/aria_redaction.py``. That is not ceremony: the
operation-gate architecture scanner is per-function syntax analysis and does
not follow call graphs, so a helper that accepted a bare Playwright target
would be reported as ungated Playwright access even when every caller already
held a lease. ``gated_operation`` is re-entrant for the owning task, so
nesting inside the mixin's lease costs nothing.

Keystrokes go through ``session.page.keyboard`` rather than the active target:
``Frame`` has no ``.keyboard`` attribute (``Page`` does), so a frame-scoped
call would crash on the first key press. Element lookup still uses
``session._target()`` so a frame-scoped selector resolves in its own frame.
"""

from __future__ import annotations

from typing import Any, Final

NAV_MODES: Final = frozenset({"tab", "arrow", "keys"})

_TAB_KEYS: Final[dict[str, str]] = {"forward": "Tab", "backward": "Shift+Tab"}
_ARROW_KEYS: Final[dict[str, str]] = {
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
}

DEFAULT_TAB_DIRECTION: Final = "forward"
DEFAULT_ARROW_DIRECTION: Final = "down"

# The accessible-name-free default: a widget in grab mode almost always keeps
# focus on the grabbed element, so "is the source still focused" is the check
# that works without the caller knowing anything about the widget.
_DEFAULT_GRABBED_JS: Final = "(el) => document.activeElement === el"


class A11yDragDropError(Exception):
    """An infrastructure failure that makes the result meaningless.

    Deliberately NOT raised for an ordinary failed verify -- that is a normal
    outcome the caller reads off ``stage_reached`` (spec section 7).
    """


def validate_params(*, nav_key: str, nav_key_sequence: list[str] | None, verify_fields_set: int) -> None:
    """Reject impossible parameter combinations before any key is sent.

    Both rules exist because the failure is otherwise silent: zero verify
    fields makes every call "succeed" without checking anything, and a
    ``nav_key_sequence`` alongside ``tab``/``arrow`` leaves it genuinely
    ambiguous which navigation the caller meant.
    """
    if nav_key not in NAV_MODES:
        raise ValueError(f"nav_key must be one of {sorted(NAV_MODES)}, got {nav_key!r}")
    if nav_key == "keys" and not nav_key_sequence:
        raise ValueError("nav_key='keys' requires a non-empty nav_key_sequence")
    if nav_key != "keys" and nav_key_sequence:
        raise ValueError(f"nav_key_sequence is only valid with nav_key='keys', not {nav_key!r}")
    if verify_fields_set != 1:
        raise ValueError(
            f"exactly one of verify_js/verify_selector_appears/verify_selector_gone/"
            f"verify_text_contains is required, got {verify_fields_set}"
        )
```

- [ ] **Step 4: Run to verify validation passes**

Run: `uv run --active pytest tests/session/test_a11y_dragdrop.py -q --no-cov`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the failing engine tests with a fake page**

Append to `tests/session/test_a11y_dragdrop.py`:

```python
from contextlib import asynccontextmanager
from typing import Any

from octowright.session.a11y_dragdrop import run_a11y_dragdrop


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self._page, self._selector = page, selector

    async def focus(self, **_kw: Any) -> None:
        if self._selector in self._page.missing:
            raise RuntimeError(f"no element for {self._selector}")
        self._page.events.append(("focus", self._selector))

    async def evaluate(self, _js: str, *_args: Any, **_kw: Any) -> Any:
        self._page.events.append(("evaluate", self._selector))
        return self._page.grabbed_result

    async def count(self) -> int:
        return 0 if self._selector in self._page.missing else 1


class FakeKeyboard:
    def __init__(self, page: "FakePage") -> None:
        self._page = page

    async def press(self, key: str) -> None:
        self._page.events.append(("press", key))


class FakePage:
    """Models only what the engine touches: locator/keyboard/evaluate."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.missing: set[str] = set()
        self.grabbed_result: Any = True
        self.verify_results: list[Any] = [True]
        self.keyboard = FakeKeyboard(self)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def evaluate(self, _js: str, *_args: Any, **_kw: Any) -> Any:
        self.events.append(("verify", None))
        return self.verify_results.pop(0) if len(self.verify_results) > 1 else self.verify_results[0]


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.leases: list[str] = []

    def _target(self) -> FakePage:
        return self.page

    @asynccontextmanager
    async def operation(self, name: str, **_kw: Any):
        self.leases.append(name)
        yield


async def test_happy_path_tab_navigation() -> None:
    page = FakePage()
    session = FakeSession(page)
    result = await run_a11y_dragdrop(
        session, source_selector="#item", nav_key="tab", max_nav_steps=3, verify_js="() => true"
    )
    assert result["stage_reached"] == "verified"
    assert result["ok"] is True and result["verified"] is True
    assert result["released"] is False, "a verified drop already exits grab mode; no Escape"
    assert result["nav_steps_taken"] == 3
    assert ("press", "Tab") in page.events


async def test_failed_grab_short_circuits_without_release() -> None:
    page = FakePage()
    page.grabbed_result = False
    session = FakeSession(page)
    result = await run_a11y_dragdrop(session, source_selector="#item", verify_js="() => true")
    assert result["stage_reached"] == "failed_grab"
    assert result["grabbed"] is False and result["dropped"] is False
    assert result["released"] is False, "nothing entered grab mode, so nothing to release"
    assert ("press", "Escape") not in page.events


async def test_failed_verify_presses_release() -> None:
    page = FakePage()
    page.verify_results = [False]
    session = FakeSession(page)
    result = await run_a11y_dragdrop(
        session, source_selector="#item", verify_js="() => false", verify_timeout_ms=30, verify_poll_ms=10
    )
    assert result["stage_reached"] == "failed_verify"
    assert result["dropped"] is True and result["verified"] is False
    assert result["released"] is True
    assert ("press", "Escape") in page.events


async def test_arrow_mode_sends_the_direction_key() -> None:
    page = FakePage()
    session = FakeSession(page)
    await run_a11y_dragdrop(
        session,
        source_selector="#i",
        nav_key="arrow",
        nav_direction="left",
        max_nav_steps=2,
        verify_js="() => true",
    )
    assert [e for e in page.events if e == ("press", "ArrowLeft")] == [("press", "ArrowLeft")] * 2


async def test_keys_mode_sends_the_sequence_once() -> None:
    page = FakePage()
    session = FakeSession(page)
    result = await run_a11y_dragdrop(
        session,
        source_selector="#i",
        nav_key="keys",
        nav_key_sequence=["Tab", "Tab", "End"],
        verify_js="() => true",
    )
    pressed = [k for kind, k in page.events if kind == "press"]
    assert pressed[1:4] == ["Tab", "Tab", "End"]
    assert result["nav_steps_taken"] == 3


async def test_exception_after_grab_releases_before_propagating() -> None:
    page = FakePage()
    session = FakeSession(page)

    async def boom(_js: str, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("page detached")

    page.evaluate = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="page detached"):
        await run_a11y_dragdrop(session, source_selector="#i", verify_js="() => true")
    assert ("press", "Escape") in page.events, "grabbed widget must not be left stuck"


async def test_engine_takes_a_named_operation_lease() -> None:
    page = FakePage()
    session = FakeSession(page)
    await run_a11y_dragdrop(session, source_selector="#i", verify_js="() => true")
    assert "browser_a11y_dragdrop" in session.leases
```

- [ ] **Step 6: Run to verify the engine tests fail**

Run: `uv run --active pytest tests/session/test_a11y_dragdrop.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'run_a11y_dragdrop'`

- [ ] **Step 7: Implement the engine**

Append to `src/octowright/session/a11y_dragdrop.py`:

```python
import asyncio
import time


def _nav_keys(
    nav_key: str, nav_direction: str | None, nav_key_sequence: list[str] | None, max_nav_steps: int
) -> list[str]:
    """The exact key presses navigation will send, resolved up front.

    Returning a concrete list (rather than deciding per step) is what makes
    ``nav_steps_taken`` truthful for all three modes: ``keys`` sends its
    sequence once, while ``tab``/``arrow`` repeat one key ``max_nav_steps``
    times.
    """
    if nav_key == "keys":
        return list(nav_key_sequence or [])
    if nav_key == "arrow":
        key = _ARROW_KEYS[nav_direction or DEFAULT_ARROW_DIRECTION]
    else:
        key = _TAB_KEYS[nav_direction or DEFAULT_TAB_DIRECTION]
    return [key] * max_nav_steps


def _count_verify_fields(
    verify_js: str | None,
    verify_selector_appears: str | None,
    verify_selector_gone: str | None,
    verify_text_contains: str | None,
) -> int:
    return sum(1 for v in (verify_js, verify_selector_appears, verify_selector_gone, verify_text_contains) if v)


async def _check_verify(
    session: Any, *, verify_js, verify_selector_appears, verify_selector_gone, verify_text_contains
) -> bool:
    """One evaluation of whichever verify shape the caller chose."""
    target = session._target()
    if verify_js is not None:
        return bool(await target.evaluate(verify_js))
    if verify_selector_appears is not None:
        return await target.locator(verify_selector_appears).count() > 0
    if verify_selector_gone is not None:
        return await target.locator(verify_selector_gone).count() == 0
    return bool(await target.evaluate("(needle) => document.body.innerText.includes(needle)", verify_text_contains))


async def run_a11y_dragdrop(
    session: Any,
    *,
    source_selector: str,
    nav_key: str = "tab",
    nav_direction: str | None = None,
    nav_key_sequence: list[str] | None = None,
    max_nav_steps: int = 12,
    grab_key: str = "Space",
    drop_key: str = "Space",
    release_key: str = "Escape",
    grabbed_predicate_js: str | None = None,
    verify_js: str | None = None,
    verify_selector_appears: str | None = None,
    verify_selector_gone: str | None = None,
    verify_text_contains: str | None = None,
    verify_timeout_ms: int = 2000,
    verify_poll_ms: int = 100,
) -> dict[str, Any]:
    """One atomic keyboard drag attempt. Never retries -- that is the caller's job."""
    validate_params(
        nav_key=nav_key,
        nav_key_sequence=nav_key_sequence,
        verify_fields_set=_count_verify_fields(
            verify_js, verify_selector_appears, verify_selector_gone, verify_text_contains
        ),
    )

    result: dict[str, Any] = {
        "ok": False,
        "grabbed": False,
        "dropped": False,
        "verified": False,
        "released": False,
        "stage_reached": "failed_grab",
        "nav_steps_taken": 0,
    }

    async with session.operation("browser_a11y_dragdrop"):
        target = session._target()
        keyboard = session.page.keyboard

        # --- Grab -------------------------------------------------------
        source = target.locator(source_selector)
        await source.focus()
        await keyboard.press(grab_key)
        grabbed = bool(await source.evaluate(grabbed_predicate_js or _DEFAULT_GRABBED_JS))
        if not grabbed:
            return result
        result["grabbed"] = True

        # Everything past this point holds a grabbed widget, so every exit
        # path -- including an unhandled exception -- must release it. This
        # `finally` IS the bug the tool generalizes: a grab that succeeded
        # with a drop that did not left the widget stuck in grab mode,
        # indistinguishable from a grab that never registered.
        try:
            for key in _nav_keys(nav_key, nav_direction, nav_key_sequence, max_nav_steps):
                await keyboard.press(key)
                result["nav_steps_taken"] += 1
            result["stage_reached"] = "navigated"

            await keyboard.press(drop_key)
            result["dropped"] = True
            result["stage_reached"] = "dropped"

            # Poll in THIS task. Never spawn one: `gated_operation` re-enters
            # only for the owning task, so a helper task calling back into a
            # gated session method would queue behind the lease this frame is
            # still holding and deadlock until the queue timeout.
            deadline = time.monotonic() + verify_timeout_ms / 1000
            while True:
                if await _check_verify(
                    session,
                    verify_js=verify_js,
                    verify_selector_appears=verify_selector_appears,
                    verify_selector_gone=verify_selector_gone,
                    verify_text_contains=verify_text_contains,
                ):
                    result["verified"] = True
                    result["ok"] = True
                    result["stage_reached"] = "verified"
                    return result
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(verify_poll_ms / 1000)

            await keyboard.press(release_key)
            result["released"] = True
            result["stage_reached"] = "failed_verify"
            return result
        except Exception:
            await keyboard.press(release_key)
            result["released"] = True
            raise
```

- [ ] **Step 8: Run the engine tests**

Run: `uv run --active pytest tests/session/test_a11y_dragdrop.py -q --no-cov`
Expected: PASS (12 tests)

- [ ] **Step 9: Run the architecture gate — this is the step the spec would have failed**

Run: `uv run --active python scripts/check_operation_gate_architecture.py`
Expected: `OK` / no findings. If it reports `a11y_dragdrop.py ... ungated Playwright access`, the `async with session.operation(...)` block is not wrapping every Playwright call — fix the block, do not add a bypass entry.

- [ ] **Step 10: Commit**

```bash
git add src/octowright/session/a11y_dragdrop.py tests/session/test_a11y_dragdrop.py
UV_FROZEN=1 git commit -m "feat(session): keyboard WAI-ARIA drag-and-drop engine"
```

---

### Task 2: Session method

**Files:**
- Modify: `src/octowright/session/core_ops_mixin.py` (add after `drag`, near line 278)
- Test: `tests/session/test_a11y_dragdrop.py` (append)

**Interfaces:**
- Consumes: `run_a11y_dragdrop(session, *, source_selector=..., ...) -> dict[str, Any]` from Task 1.
- Produces: `BrowserSession.a11y_dragdrop(...) -> dict[str, Any]` — same keyword names as the engine, so `macros/lint_fields._session_method_params` derives the macro's allowed fields from this signature automatically.

- [ ] **Step 1: Write the failing test**

```python
async def test_session_method_records_the_action() -> None:
    from octowright.session.core_ops_mixin import SessionOpsMixin

    class Recorder:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, Any]]] = []

        def record(self, kind: str, **fields: Any) -> None:
            self.events.append((kind, fields))

    page = FakePage()

    class Session(SessionOpsMixin):
        def __init__(self) -> None:
            self.page = page
            self.recorder = Recorder()
            self.leases: list[str] = []

        def _target(self) -> FakePage:
            return page

        @asynccontextmanager
        async def operation(self, name: str, **_kw: Any):
            self.leases.append(name)
            yield

    session = Session()
    result = await session.a11y_dragdrop(source_selector="#item", verify_js="() => true")
    assert result["verified"] is True
    kind, fields = session.recorder.events[0]
    assert kind == "a11y_dragdrop"
    assert fields["source_selector"] == "#item"
    assert fields["verify_js"] == "() => true"
    assert "ok" not in fields, "record the inputs, not the outcome"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --active pytest tests/session/test_a11y_dragdrop.py::test_session_method_records_the_action -q --no-cov`
Expected: FAIL — `AttributeError: 'Session' object has no attribute 'a11y_dragdrop'`

- [ ] **Step 3: Add the delegate**

Insert into `src/octowright/session/core_ops_mixin.py` immediately after the `drag` method (currently ending line 278), and add `from octowright.session.a11y_dragdrop import run_a11y_dragdrop` to the imports at the top:

```python
    @gated_operation("browser_a11y_dragdrop")
    async def a11y_dragdrop(
        self,
        source_selector: str,
        nav_key: str = "tab",
        nav_direction: str | None = None,
        nav_key_sequence: list[str] | None = None,
        max_nav_steps: int = 12,
        grab_key: str = "Space",
        drop_key: str = "Space",
        release_key: str = "Escape",
        grabbed_predicate_js: str | None = None,
        verify_js: str | None = None,
        verify_selector_appears: str | None = None,
        verify_selector_gone: str | None = None,
        verify_text_contains: str | None = None,
        verify_timeout_ms: int = 2000,
        verify_poll_ms: int = 100,
    ) -> dict[str, Any]:
        """Keyboard (WAI-ARIA APG) drag-and-drop; see ``session/a11y_dragdrop.py``.

        The engine takes its own re-entrant lease, so this decorator's lease is
        the root one and the operation name stays ``browser_a11y_dragdrop``
        throughout.
        """
        result = await run_a11y_dragdrop(
            self,
            source_selector=source_selector,
            nav_key=nav_key,
            nav_direction=nav_direction,
            nav_key_sequence=nav_key_sequence,
            max_nav_steps=max_nav_steps,
            grab_key=grab_key,
            drop_key=drop_key,
            release_key=release_key,
            grabbed_predicate_js=grabbed_predicate_js,
            verify_js=verify_js,
            verify_selector_appears=verify_selector_appears,
            verify_selector_gone=verify_selector_gone,
            verify_text_contains=verify_text_contains,
            verify_timeout_ms=verify_timeout_ms,
            verify_poll_ms=verify_poll_ms,
        )
        # Record the INPUTS only. Replay re-runs the action; recording the
        # outcome would make a replayed macro carry a stale verdict, and the
        # recorded field names must match this method's parameter names so
        # `lint_fields.allowed_fields_for` derives them from the signature.
        self.recorder.record(
            "a11y_dragdrop",
            source_selector=source_selector,
            nav_key=nav_key,
            nav_direction=nav_direction,
            nav_key_sequence=nav_key_sequence,
            max_nav_steps=max_nav_steps,
            grab_key=grab_key,
            drop_key=drop_key,
            release_key=release_key,
            grabbed_predicate_js=grabbed_predicate_js,
            verify_js=verify_js,
            verify_selector_appears=verify_selector_appears,
            verify_selector_gone=verify_selector_gone,
            verify_text_contains=verify_text_contains,
            verify_timeout_ms=verify_timeout_ms,
            verify_poll_ms=verify_poll_ms,
        )
        return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --active pytest tests/session/test_a11y_dragdrop.py -q --no-cov`
Expected: PASS (13 tests)

- [ ] **Step 5: Check LOC headroom and the gate**

Run: `uv run --active python scripts/check_max_loc.py && uv run --active python scripts/check_operation_gate_architecture.py`
Expected: both OK. `core_ops_mixin.py` goes from 463 to roughly 530 lines — well under 777.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/session/core_ops_mixin.py tests/session/test_a11y_dragdrop.py
UV_FROZEN=1 git commit -m "feat(session): a11y_dragdrop session method with recording"
```

---

### Task 3: MCP tool, registration, and docs

**Files:**
- Create: `src/octowright/server/browser/a11y.py`
- Modify: `src/octowright/server/browser/__init__.py`
- Modify: `AGENTS.md`, then `cp AGENTS.md CLAUDE.md`
- Test: `tests/test_a11y_dragdrop_tool.py`

**Interfaces:**
- Consumes: `BrowserSession.a11y_dragdrop(...)` from Task 2; `browser_operation(pool, instance_id, "name")` from `octowright.server.browser._operation`.
- Produces: MCP tool `browser_a11y_dragdrop(instance_id, source_selector, ..., response_mode=None) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing registration test**

```python
# tests/test_a11y_dragdrop_tool.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations


def test_tool_is_importable_from_the_browser_package() -> None:
    """A new tool module that nobody imports registers nothing.

    `server/browser/__init__.py` imports each submodule for its decorator side
    effect; a module left out of that list has its `@mcp.tool` never run, so
    the tool silently does not exist at runtime while every unit test passes.
    """
    from octowright.server.browser import browser_a11y_dragdrop

    assert callable(browser_a11y_dragdrop)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --active pytest tests/test_a11y_dragdrop_tool.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'browser_a11y_dragdrop'`

- [ ] **Step 3: Create the tool module**

```python
# src/octowright/server/browser/a11y.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Accessibility-oriented browser tools (keyboard WAI-ARIA drag-and-drop)."""

from __future__ import annotations

from typing import Any

from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation
from octowright.server.browser.views import _with_outline


@mcp.tool(
    description=(
        "Keyboard (WAI-ARIA APG) drag-and-drop: focus the source, press grab_key, "
        "send navigation keys, press drop_key, then poll a verify check. This is the "
        "ACCESSIBLE counterpart to browser_drag, which drives a synthetic mouse and "
        "cannot operate widgets that only implement the keyboard pattern. "
        "One atomic attempt per call -- it never retries and never switches navigation "
        "strategy; that is the caller's orchestration to own. "
        "Exactly one of verify_js / verify_selector_appears / verify_selector_gone / "
        "verify_text_contains is REQUIRED: with no check the call would report success "
        "without having confirmed anything. "
        "Returns a structured result on ordinary failure rather than raising, so the "
        "caller can read stage_reached ('failed_grab' | 'navigated' | 'dropped' | "
        "'verified' | 'failed_verify') and decide what to do. It raises only when the "
        "result would be meaningless (selector matches nothing, frame detached). "
        "If verification fails, release_key is pressed so the widget is not left stuck "
        "in grab mode."
    ),
)
async def browser_a11y_dragdrop(
    instance_id: str,
    source_selector: str,
    nav_key: str = "tab",
    nav_direction: str | None = None,
    nav_key_sequence: list[str] | None = None,
    max_nav_steps: int = 12,
    grab_key: str = "Space",
    drop_key: str = "Space",
    release_key: str = "Escape",
    grabbed_predicate_js: str | None = None,
    verify_js: str | None = None,
    verify_selector_appears: str | None = None,
    verify_selector_gone: str | None = None,
    verify_text_contains: str | None = None,
    verify_timeout_ms: int = 2000,
    verify_poll_ms: int = 100,
    response_mode: str | None = None,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_a11y_dragdrop") as session:
        result = await session.a11y_dragdrop(
            source_selector=source_selector,
            nav_key=nav_key,
            nav_direction=nav_direction,
            nav_key_sequence=nav_key_sequence,
            max_nav_steps=max_nav_steps,
            grab_key=grab_key,
            drop_key=drop_key,
            release_key=release_key,
            grabbed_predicate_js=grabbed_predicate_js,
            verify_js=verify_js,
            verify_selector_appears=verify_selector_appears,
            verify_selector_gone=verify_selector_gone,
            verify_text_contains=verify_text_contains,
            verify_timeout_ms=verify_timeout_ms,
            verify_poll_ms=verify_poll_ms,
        )
        return await _with_outline(instance_id, result, response_mode)
```

- [ ] **Step 4: Register the module**

In `src/octowright/server/browser/__init__.py`, add to the submodule import block (keep alphabetical — it goes first, before `artifact_manifest`):

```python
from octowright.server.browser import a11y as _a11y  # noqa: F401
```

and add to the re-export block:

```python
from octowright.server.browser.a11y import browser_a11y_dragdrop
```

Then add `"browser_a11y_dragdrop"` to that file's `__all__` if one is present.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run --active pytest tests/test_a11y_dragdrop_tool.py -q --no-cov`
Expected: PASS

- [ ] **Step 6: Update the tool count and add the feature section in AGENTS.md**

`AGENTS.md` line 277 currently reads "The full MCP tool surface is 130 tools on a core install (137 with the `terminal` session-kind plugin...". Change `130` → `131` and `137` → `138`.

Add a new `###` section after `### Protected close behavior`:

```markdown
### Keyboard (WAI-ARIA) drag-and-drop

`browser_drag` drives Playwright's `drag_and_drop`, a synthetic mouse sequence. It cannot operate a widget that implements only the **keyboard** WAI-ARIA APG pattern — grab with a key, move with keys, drop with a key — which is what accessible drag-and-drop widgets usually implement. `browser_a11y_dragdrop` is that counterpart.

One atomic attempt per call: grab → navigate → drop → poll-verify → release-on-failure. It deliberately does **not** retry or switch navigation strategy; that stays in the caller's orchestration, the same boundary `browser_click` draws by not retrying against alternate selectors.

**Exactly one `verify_*` field is required.** There is no universal cross-widget "it worked" signal, so a heuristic that sometimes works would be worse than an explicit contract: with no check the call would report success having confirmed nothing. Verification **polls** (`verify_timeout_ms` / `verify_poll_ms`) rather than checking once, because most drag flakiness is post-drop animation and reflow settling.

It **returns** its result on an ordinary failed verify instead of raising — a deliberate deviation from `expect_*`, which raises to abort a script when a precondition fails. This tool exists so the caller can decide what a failed drop means, and raising would force every caller into `try/except` just to read `stage_reached` (`failed_grab` | `navigated` | `dropped` | `verified` | `failed_verify`). It raises only when the result would be meaningless: the selector matches nothing, or the frame detached.

The release-on-failure path is the whole point rather than a nicety. A grab that succeeded with a drop that did not leaves the widget stuck in grab mode, which is **indistinguishable from a grab that never registered** — the exact failure this generalizes from a hand-rolled implementation in a real test harness. Any exit after a successful grab, unhandled exceptions included, presses `release_key`.

Two implementation constraints worth knowing before editing it. Keystrokes go through `session.page.keyboard`, not the active target: **`Frame` has no `.keyboard`** (measured; `Page` does), so a frame-scoped call would crash on the first press — element lookup still goes through `session._target()` so frame-scoped selectors resolve in their own frame. And the verify loop polls **in the calling task**: `gated_operation` re-enters only for the owning task, so a spawned helper calling back into a gated session method would queue behind the lease its own parent still holds and deadlock until the queue timeout.
```

- [ ] **Step 7: Sync the docs copy and verify**

```bash
cp AGENTS.md CLAUDE.md
uv run --active python scripts/check_agent_docs_sync.py
```
Expected: `Agent instruction docs are in sync.`

- [ ] **Step 8: Commit**

```bash
git add src/octowright/server/browser/a11y.py src/octowright/server/browser/__init__.py \
        tests/test_a11y_dragdrop_tool.py AGENTS.md CLAUDE.md
UV_FROZEN=1 git commit -m "feat(server): browser_a11y_dragdrop MCP tool"
```

**Note on capability profiles:** do **not** add this tool to any entry in
`src/octowright/server/profiles.py`. Its mouse sibling `browser_drag` appears
in no profile either (verified by grep), so it registers only when no
`OCTOWRIGHT_PROFILE` filter is set. Matching that keeps the two drag tools
available under exactly the same conditions; adding only the accessible one to
`advanced` would make the accessible path available in configurations where
the mouse path is not, which is a surprising asymmetry to debug.

---

### Task 4: Macro replay and linting

**Files:**
- Modify: `src/octowright/macros/runtime.py` (`_ACTION_MAP`, near line 78)
- Modify: `src/octowright/macros/lint.py` (`_SIMPLE_REQUIRED` near line 43; new check near line 269)
- Test: `tests/test_a11y_dragdrop_macro.py`

**Interfaces:**
- Consumes: `BrowserSession.a11y_dragdrop` from Task 2 (the `_ACTION_MAP` value is this method name).
- Produces: replayable action kind `"a11y_dragdrop"`; lint issue text `action 'a11y_dragdrop' requires exactly one verify_* field`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_a11y_dragdrop_macro.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.macros.lint import lint_macro
from octowright.macros.lint_fields import allowed_fields_for
from octowright.macros.runtime import _ACTION_MAP


def test_action_is_dispatchable() -> None:
    assert _ACTION_MAP["a11y_dragdrop"] == "a11y_dragdrop"


def test_allowed_fields_derive_from_the_session_signature() -> None:
    """No hand-maintained field list -- the signature is the source of truth."""
    allowed = allowed_fields_for("a11y_dragdrop")
    assert {"source_selector", "nav_key", "verify_js", "max_nav_steps"} <= allowed


def _macro(action: dict) -> dict:
    return {"name": "m", "actions": [action]}


def test_lint_requires_a_source_selector() -> None:
    issues = lint_macro(_macro({"action": "a11y_dragdrop", "verify_js": "() => true"}))
    assert any("source_selector" in str(i) for i in issues)


def test_lint_rejects_zero_verify_fields() -> None:
    issues = lint_macro(_macro({"action": "a11y_dragdrop", "source_selector": "#i"}))
    assert any("exactly one verify_" in str(i) for i in issues)


def test_lint_rejects_two_verify_fields() -> None:
    issues = lint_macro(
        _macro(
            {
                "action": "a11y_dragdrop",
                "source_selector": "#i",
                "verify_js": "() => true",
                "verify_text_contains": "done",
            }
        )
    )
    assert any("exactly one verify_" in str(i) for i in issues)


def test_lint_accepts_a_well_formed_action() -> None:
    issues = lint_macro(_macro({"action": "a11y_dragdrop", "source_selector": "#i", "verify_js": "() => true"}))
    assert issues == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --active pytest tests/test_a11y_dragdrop_macro.py -q --no-cov`
Expected: FAIL — `KeyError: 'a11y_dragdrop'` on the first test.

- [ ] **Step 3: Register the action for replay**

In `src/octowright/macros/runtime.py`, add to `_ACTION_MAP` after the `"click_by"`/`"fill_by"` entries:

```python
    "a11y_dragdrop": "a11y_dragdrop",
```

- [ ] **Step 4: Add the lint rules**

In `src/octowright/macros/lint.py`, add to `_SIMPLE_REQUIRED` after the `"drag": (),` entry:

```python
    "a11y_dragdrop": ("source_selector",),
```

Add this check beside `_check_simple_drag_fields` (around line 269):

```python
_A11Y_VERIFY_FIELDS: tuple[str, ...] = (
    "verify_js",
    "verify_selector_appears",
    "verify_selector_gone",
    "verify_text_contains",
)


def _check_a11y_dragdrop_verify_arity(action: dict[str, Any], kind: str, report: _Report) -> None:
    """Verify ARITY, mirroring the tool's own validation.

    The linter is the only thing standing between a hand-edited macro and a
    drag step that checks nothing: with zero verify fields the action reports
    success without having confirmed the drop, and with two it is ambiguous
    which one gates success. The tool rejects both at call time; without this
    the macro would lint clean and fail at replay.
    """
    if kind != "a11y_dragdrop":
        return
    provided = [f for f in _A11Y_VERIFY_FIELDS if action.get(f)]
    if len(provided) != 1:
        report(
            f"action 'a11y_dragdrop' requires exactly one verify_* field "
            f"({', '.join(_A11Y_VERIFY_FIELDS)}), got {len(provided)}"
        )
```

Then wire it into the dispatch block at `src/octowright/macros/lint.py:174-176`, which currently reads:

```python
    _check_simple_locator_fields(action, kind, _report)
    _check_simple_drag_fields(action, kind, _report)
    _check_simple_required_fields(action, kind, _report)
```

Add the new check between the drag and required-fields lines:

```python
    _check_simple_locator_fields(action, kind, _report)
    _check_simple_drag_fields(action, kind, _report)
    _check_a11y_dragdrop_verify_arity(action, kind, _report)
    _check_simple_required_fields(action, kind, _report)
```

This block runs from `_check_simple`, which `lint.py:475` reaches only for kinds
present in `_SIMPLE_REQUIRED` — which is why the `"a11y_dragdrop": ("source_selector",)`
entry above is a prerequisite, not merely an additional check. Without it the
action falls through to `_LINT_HANDLERS` and neither rule ever runs.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run --active pytest tests/test_a11y_dragdrop_macro.py -q --no-cov`
Expected: PASS (6 tests)

- [ ] **Step 6: Run the replay-classification invariant test**

Run: `uv run --active pytest tests/test_replay_passive_covers_recorder.py -q --no-cov`
Expected: PASS. This test scans `recorder.record` call sites and fails on any event kind that is not classified as replayable, skipped, or passive. Task 2 added a new `record("a11y_dragdrop", ...)` call site; Step 3 classified it as replayable. If this fails, the `_ACTION_MAP` entry is missing or misspelled.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/macros/runtime.py src/octowright/macros/lint.py tests/test_a11y_dragdrop_macro.py
UV_FROZEN=1 git commit -m "feat(macros): replay and lint a11y_dragdrop actions"
```

---

### Task 5: Live fixture and integration test

**Files:**
- Create: `tests/fixtures/a11y_dragdrop.html`
- Create: `tests/test_a11y_dragdrop_live.py`

**Interfaces:**
- Consumes: `browser_a11y_dragdrop` from Task 3; `BrowserPool` from `octowright.browser_pool.pool`.
- Produces: nothing later tasks depend on — this is the terminal verification task.

- [ ] **Step 1: Create the fixture**

```html
<!-- tests/fixtures/a11y_dragdrop.html -->
<!doctype html>
<meta charset="utf-8">
<title>a11y dragdrop fixture</title>
<style>
  li { padding: 4px; border: 1px solid #ccc; margin: 2px; list-style: none; }
  [aria-grabbed="true"] { background: #ffd; }
</style>

<h1>Arrow-key sortable list</h1>
<ul id="list">
  <li id="a" tabindex="0" aria-grabbed="false">Alpha</li>
  <li id="b" tabindex="0" aria-grabbed="false">Bravo</li>
  <li id="c" tabindex="0" aria-grabbed="false">Charlie</li>
</ul>
<p id="status"></p>

<script>
  // Minimal WAI-ARIA APG keyboard drag: Space grabs, arrows move the grabbed
  // item, Space drops, Escape cancels. Deliberately vanilla so the test has no
  // external dependency and runs headless in CI.
  let grabbed = null;
  document.addEventListener("keydown", (e) => {
    const el = document.activeElement;
    if (!el || el.tagName !== "LI") return;
    if (e.key === " " || e.code === "Space") {
      e.preventDefault();
      if (grabbed === el) {
        grabbed.setAttribute("aria-grabbed", "false");
        document.getElementById("status").textContent = "dropped " + grabbed.id;
        grabbed = null;
      } else {
        grabbed = el;
        el.setAttribute("aria-grabbed", "true");
        document.getElementById("status").textContent = "grabbed " + el.id;
      }
    } else if (grabbed && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const list = document.getElementById("list");
      const sib = e.key === "ArrowDown" ? grabbed.nextElementSibling : grabbed.previousElementSibling;
      if (sib) {
        if (e.key === "ArrowDown") list.insertBefore(sib, grabbed);
        else list.insertBefore(grabbed, sib);
        grabbed.focus();
      }
    } else if (e.key === "Escape" && grabbed) {
      grabbed.setAttribute("aria-grabbed", "false");
      document.getElementById("status").textContent = "cancelled";
      grabbed = null;
    }
  });
</script>
```

- [ ] **Step 2: Write the failing integration test**

```python
# tests/test_a11y_dragdrop_live.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser

FIXTURE = (Path(__file__).parent / "fixtures" / "a11y_dragdrop.html").resolve()


@pytest.fixture
async def session(tmp_path: Path):
    pool = BrowserPool(recordings_dir=tmp_path)
    inst = await pool.launch(kind="chromium", headed=False, url=FIXTURE.as_uri())
    try:
        yield pool.get(inst["instance_id"])
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()


async def test_arrow_drag_moves_the_item(session) -> None:
    """Bravo moves above Alpha, verified by reading the real DOM order."""
    result = await session.a11y_dragdrop(
        source_selector="#b",
        nav_key="arrow",
        nav_direction="up",
        max_nav_steps=1,
        verify_js="() => document.getElementById('list').firstElementChild.id === 'b'",
    )
    assert result["stage_reached"] == "verified", result
    assert result["ok"] is True and result["released"] is False


async def test_failed_verify_releases_the_widget(session) -> None:
    """A check that can never pass must leave nothing grabbed."""
    result = await session.a11y_dragdrop(
        source_selector="#a",
        nav_key="arrow",
        nav_direction="down",
        max_nav_steps=1,
        verify_js="() => false",
        verify_timeout_ms=300,
        verify_poll_ms=50,
    )
    assert result["stage_reached"] == "failed_verify"
    assert result["released"] is True
    still_grabbed = await session.page.evaluate("() => document.querySelectorAll('[aria-grabbed=\"true\"]').length")
    assert still_grabbed == 0, "release_key did not exit grab mode"
```

- [ ] **Step 3: Run to verify the integration tests fail against a missing fixture**

Run: `uv run --active pytest tests/test_a11y_dragdrop_live.py -q --no-cov`
Expected: FAIL if the fixture was not created in Step 1, PASS once it is. If the first test fails with `stage_reached == "failed_grab"`, the fixture's Space handler is not setting `aria-grabbed` — the engine's default grabbed predicate checks `document.activeElement === el`, so also confirm the `<li>` elements carry `tabindex="0"`.

- [ ] **Step 4: Run the full gate**

Run: `{ make ci; echo "MAKE_EXIT=$?"; } 2>&1 | tail -30`
Expected: `MAKE_EXIT=0`. **Read the `MAKE_EXIT` line, not the shell's apparent success** — a wrapper's exit code is not `make`'s, and this repo has been burned by that exact confusion.

- [ ] **Step 5: Headed acceptance pass**

Launch the fixture in a real headed browser and drive it once by hand, capturing a before/after screenshot:

```bash
uv run --active python - <<'PY'
import asyncio
from pathlib import Path
from octowright.browser_pool.pool import BrowserPool

FIXTURE = (Path("tests/fixtures/a11y_dragdrop.html")).resolve().as_uri()

async def main() -> None:
    pool = BrowserPool()
    inst = await pool.launch(kind="chromium", headed=True, url=FIXTURE)
    session = pool.get(inst["instance_id"])
    await session.screenshot("a11y-before.png")
    result = await session.a11y_dragdrop(
        source_selector="#b", nav_key="arrow", nav_direction="up", max_nav_steps=1,
        verify_js="() => document.getElementById('list').firstElementChild.id === 'b'",
    )
    print(result)
    await session.screenshot("a11y-after.png")

asyncio.run(main())
PY
```

Expected: `stage_reached='verified'`, and the two screenshots show Bravo above Alpha. Confirm by eye that an actual browser did the drag — green assertions alone are not the acceptance criterion here (spec §9.3). The browser is left open deliberately; close it yourself.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/a11y_dragdrop.html tests/test_a11y_dragdrop_live.py
UV_FROZEN=1 git commit -m "test: live a11y_dragdrop fixture and integration coverage"
```

---

## Self-Review

**1. Spec coverage.**

| Spec requirement | Task |
|---|---|
| §2 tool, three nav modes, configurable keys | 1, 3 |
| §2 verify sugar + poll-until-timeout | 1 |
| §2 structured result, no raise on verify failure | 1, 3 |
| §2 recording + macro replay (`a11y_dragdrop` kind) | 2, 4 |
| §2 self-hosted fixture + headed acceptance | 5 |
| §4 parameter surface + pre-interaction validation | 1 |
| §5 five-stage sequence incl. defensive `finally` | 1 |
| §6 return shape | 1 |
| §7 error convention | 1, 3 |
| §8 `_ACTION_MAP`, lint required-field check | 4 |
| §9 three test layers | 1, 5 |

No gaps. Out-of-scope items (retry, heuristic verify, mouse drag, touch, nav-mode auto-detect) have no task, correctly.

**2. Placeholder scan.** No TBD/TODO; every code step carries real code. Two
issues found on review and fixed rather than annotated: Task 4 Step 4 said
"find that call site" for wiring `_check_a11y_dragdrop_verify_arity` (now the
verbatim `lint.py:174-176` block, before and after, plus why the
`_SIMPLE_REQUIRED` entry is a prerequisite for it to run at all), and Task 5's
fixture carried a transcription typo in the `else if` line (now corrected in
the block itself).

**3. Type consistency.** `run_a11y_dragdrop` is keyword-only after `session` in
Task 1 and called that way in Task 2. The session method's parameter names match
the engine's exactly, which Task 4's `allowed_fields_for` test depends on. The
`stage_reached` values in Task 1's implementation match those asserted in Tasks
1 and 5 and documented in Task 3. `_A11Y_VERIFY_FIELDS` (Task 4) lists the same
four fields `_count_verify_fields` counts (Task 1).

**4. Known risk not resolvable from reading.** Task 5's fixture must actually
implement the APG pattern the engine expects. Step 3 names the two most likely
mismatches (`aria-grabbed` not set; missing `tabindex`) and how each surfaces,
because a fixture bug and an engine bug present identically as
`stage_reached="failed_grab"`.
