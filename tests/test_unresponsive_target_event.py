# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A wedged target must reach the agent through the crash taxonomy.

`page.on("crash")` is silent for an unresponsive target, so without this the
only signal is a raw error string and the agent cannot tell "relaunch this
session" from "the transport died".

Covers:
- ``scope="unresponsive"`` is a valid ``CrashScope`` (Task 2, Step 1).
- A ``SessionCallTimeoutError`` raised inside a gated session operation
  publishes exactly one ``SessionCrashedEvent(scope="unresponsive")`` on the
  pool's event bus (Task 2, Step 3), via ``SessionOperationGate``'s
  ``on_call_timeout`` hook, wired by ``BrowserSession.__post_init__`` to
  ``BrowserSession._notify_call_timeout``.
- Nesting does not multiply the notification: the INNERMOST gated operation
  to see a ``SessionCallTimeoutError`` escape it fires the hook, and marks
  the exception instance (``_mark_call_timeout_published``) so an ancestor
  frame that also sees it (still propagating, or via its own ``__cause__``
  walk) stays silent -- not "the root lease", which review round 3 (R1)
  found false for a caller that swallows the error inside its own root
  lease (``macros/artifacts.py``'s ``macro_artifact_run``, ``run_sequence
  (stop_on_failure=False)``): nothing ever escapes a root frame there for a
  root-only check to see, so those two shapes published nothing until this
  fix.
- An ordinary exception (not a ``SessionCallTimeoutError``, and not wrapping
  one via ``__cause__``) never publishes — the hook is specific to the
  call-budget timeout, not "any gated error".
- The published event never sets ``recovering=True`` — an unresponsive
  target is deliberately never auto-recovered (see ``session/timeouts.py``
  and ``session/core.py``'s ``_notify_call_timeout`` docstring).
- TWO tests go through real production call sites rather than hand-raising
  ``SessionCallTimeoutError`` inside a bare ``session.operation(...)`` block
  (review finding F2 on 2026-08-29's Task 2 review: the hand-raised tests
  above pin the mechanism but not the wiring, which is exactly how the
  macro/scenario gap in F1 shipped unnoticed): ``session.evaluate()`` really
  timing out via ``@gated_operation`` + ``bounded()``, and
  ``macros.execution.run_macro`` wrapping the timeout in its own
  ``RuntimeError`` (review finding F1) and still publishing via the gate's
  ``__cause__``-chain walk.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool import incidents
from octowright.browser_pool.events import SessionCrashedEvent
from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.server.meta import octowright_status
from octowright.session import BrowserSession
from octowright.session.operation_gate import _call_timeout_cause
from octowright.session.timeouts import SessionCallTimeoutError


@pytest.fixture(autouse=True)
def _reset_incidents() -> None:
    """Isolates the process-global incidents ring (browser_pool/incidents.py)
    per test, matching the established convention in test_crash_recovery.py."""
    incidents.reset()


def test_unresponsive_is_a_valid_crash_scope() -> None:
    # NOTE (review finding F4): this proves nothing at runtime by itself --
    # `Literal` is not enforced by Python, and `SessionCrashedEvent` does no
    # validation, so this assignment would succeed even if "unresponsive"
    # were never added to `CrashScope` at all. The REAL enforcement is
    # static: mypy checks the literal `scope="unresponsive"` construction in
    # `session/core.py`'s `_notify_call_timeout` against the `CrashScope`
    # Literal, and `make lint` runs mypy on every commit. This test exists
    # only as a readable, greppable pin of the value, not as coverage.
    event = SessionCrashedEvent(
        instance_id="abc123",  # pragma: allowlist secret (fake instance id)
        kind="webkit",
        label=None,
        profile=None,
        scope="unresponsive",
        log_path="/tmp/x.jsonl",
    )
    assert event.scope == "unresponsive"


@pytest.fixture
def fake_session_kwargs(tmp_path: Path) -> dict[str, object]:
    return {
        "instance_id": "wedged-session",
        "kind": "webkit",
        "label": "test-label",
        "url": "https://octowright.com",
        "browser": None,
        "context": MagicMock(),
        "page": MagicMock(),
        "recorder": MagicMock(),
        "log_path": tmp_path / "wedged.jsonl",
        "profile": "test-persona",
    }


async def _assert_nothing_else_arrives(sub: object) -> None:
    """Deterministic "no second event" check (review finding F5).

    ``session_event_bus.publish_nowait`` schedules delivery via
    ``loop.call_soon`` on the SAME loop, and any (hypothetical, buggy) second
    publish happens synchronously during the same ``finally``-block unwind
    that produced the first one -- i.e. before the awaiting coroutine ever
    resumes at ``sub.get()``. So a single ``await asyncio.sleep(0)`` lets
    every already-scheduled callback run, and a plain, instant
    ``get_nowait()`` after that is a deterministic check for "nothing else
    queued" -- no sleep-and-hope timeout needed.
    """
    await asyncio.sleep(0)
    with pytest.raises(asyncio.QueueEmpty):
        sub._subscriber.queue.get_nowait()  # type: ignore[attr-defined]


async def test_call_timeout_publishes_unresponsive_event(fake_session_kwargs: dict[str, object]) -> None:
    """A ``SessionCallTimeoutError`` raised inside a gated session operation
    publishes exactly one ``SessionCrashedEvent(scope="unresponsive")``."""
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]

    async with session_event_bus.subscribe() as sub:
        with pytest.raises(SessionCallTimeoutError):
            async with session.operation("browser_evaluate"):
                raise SessionCallTimeoutError("browser_evaluate did not answer within 30.0s")

        received = await asyncio.wait_for(sub.get(), timeout=1.0)

    assert isinstance(received, SessionCrashedEvent)
    assert received.scope == "unresponsive"
    assert received.instance_id == "wedged-session"
    assert received.kind == "webkit"
    assert received.label == "test-label"
    assert received.profile == "test-persona"
    assert received.recovering is False
    assert received.log_path == str(fake_session_kwargs["log_path"])


async def test_nested_gated_operation_publishes_exactly_once(fake_session_kwargs: dict[str, object]) -> None:
    """A timeout raised inside a REENTRANT (nested) gated operation still
    surfaces through the outer lease, but must publish only once — not once
    per ``session.operation(...)`` frame it propagates through."""
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]

    async def _inner() -> None:
        # Reentrant: same task already owns the gate via the outer
        # `session.operation("macro_run")` below, so this does not queue.
        async with session.operation("macro_check"):
            raise SessionCallTimeoutError("macro_check did not answer within 30.0s")

    async with session_event_bus.subscribe() as sub:
        with pytest.raises(SessionCallTimeoutError):
            async with session.operation("macro_run"):
                await _inner()

        received = await asyncio.wait_for(sub.get(), timeout=1.0)
        assert received.scope == "unresponsive"

        # The nested (non-root) frame must not have published a second event.
        await _assert_nothing_else_arrives(sub)


async def test_ordinary_error_does_not_publish_unresponsive_event(fake_session_kwargs: dict[str, object]) -> None:
    """Only a ``SessionCallTimeoutError`` (directly, or reachable via
    ``__cause__``) triggers the hook — a plain exception with no such cause
    propagating out of a gated operation must not publish."""
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]

    async with session_event_bus.subscribe() as sub:
        with pytest.raises(RuntimeError, match="boom"):
            async with session.operation("browser_click"):
                raise RuntimeError("boom")

        await _assert_nothing_else_arrives(sub)


async def test_wrapped_non_timeout_error_does_not_publish(fake_session_kwargs: dict[str, object]) -> None:
    """A wrapped exception whose ``__cause__`` chain does NOT contain a
    ``SessionCallTimeoutError`` must not publish either — the gate's
    ``__cause__``-chain walk (added for review finding F1) is specific to
    that one exception type, not "any wrapped error"."""
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]

    async with session_event_bus.subscribe() as sub:
        with pytest.raises(RuntimeError, match="wrapped"):
            async with session.operation("browser_click"):
                try:
                    raise ValueError("bad selector")
                except ValueError as exc:
                    raise RuntimeError("wrapped") from exc

        await _assert_nothing_else_arrives(sub)


# ─── real call sites (review finding F2) ──────────────────────────────────


async def test_real_evaluate_call_site_publishes_via_bounded(
    monkeypatch: pytest.MonkeyPatch, fake_session_kwargs: dict[str, object]
) -> None:
    """Goes through the REAL production call site — ``session.evaluate()``,
    decorated ``@gated_operation("browser_evaluate")``, calling
    ``bounded(self._target().evaluate(expression), ...)`` internally —
    rather than hand-raising ``SessionCallTimeoutError`` inside a bare
    ``session.operation(...)`` block. Proves the WIRING (decorator -> bounded
    -> gate -> hook -> event bus), not just the gate mechanism the tests
    above already pin.
    """
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", "0.2")

    async def _never_returns(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()  # never set -> bounded()'s timeout cancels this

    page = MagicMock()
    page.evaluate = _never_returns
    session = BrowserSession(**{**fake_session_kwargs, "page": page})  # type: ignore[arg-type]

    async with session_event_bus.subscribe() as sub:
        # A second outer bound, deliberately separate from bounded()'s own
        # 0.2s budget above (review round 3, R2): if bounded() ever regresses
        # out of core_page_mixin.evaluate() -- stops actually bounding the
        # call -- this test must fail fast rather than hang the suite
        # indefinitely (no pytest-timeout plugin is configured here). A
        # regression makes this asyncio.timeout(2.0) fire a plain
        # TimeoutError instead of SessionCallTimeoutError, which
        # pytest.raises below rejects -- a clean, fast test failure instead
        # of exactly the hang this whole plan exists to prevent.
        with pytest.raises(SessionCallTimeoutError):
            async with asyncio.timeout(2.0):
                await session.evaluate("1")

        received = await asyncio.wait_for(sub.get(), timeout=2.0)

    assert received.scope == "unresponsive"
    assert received.instance_id == session.instance_id
    assert received.recovering is False


async def test_run_macro_wrapped_timeout_still_publishes(
    monkeypatch: pytest.MonkeyPatch, fake_session_kwargs: dict[str, object]
) -> None:
    """Reproduces review finding F1: ``macros/execution.py``'s per-action
    failure handling re-raises every action failure as
    ``RuntimeError(payload) from exc`` INSIDE the root
    ``session.operation("macro_run")`` frame, so a bare ``isinstance`` check
    on the escaping exception (the pre-fix behaviour) never saw the
    ``SessionCallTimeoutError`` that caused it — the unattended macro/
    scenario replay path published no event at all. This drives the REAL
    ``run_macro`` / ``_run_macro_impl`` control flow (only ``load_macro`` and
    ``_dispatch_one`` are stubbed, the same technique the review's own
    reproduction used) against a REAL ``BrowserSession``, so it proves the
    gate's ``__cause__``-chain walk end to end rather than re-testing the
    isinstance mechanism a hand-raised ``SessionCallTimeoutError`` already
    covers above.
    """
    from octowright.macros import execution as _execution

    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]
    # Stub the diagnostic-bundle build the failure path always does -- not
    # what this test is about, and the fixture's MagicMock page has no
    # working title()/content() to build a real one from.
    session.diagnostic_bundle = AsyncMock(return_value={"url": "https://octowright.com", "title": "t"})  # type: ignore[method-assign]

    monkeypatch.setattr(
        _execution,
        "load_macro",
        lambda name: {"name": name, "actions": [{"action": "evaluate", "expression": "1"}]},
    )

    async def _raise_timeout(*_args: object, **_kwargs: object) -> tuple[int, int]:
        raise SessionCallTimeoutError("browser_evaluate did not answer within 0.2s")

    monkeypatch.setattr(_execution, "_dispatch_one", _raise_timeout)

    async with session_event_bus.subscribe() as sub:
        with pytest.raises(RuntimeError) as excinfo:
            await _execution.run_macro(session, "wedged-macro")
        # Confirm the reproduction actually matches F1's shape: a RuntimeError
        # wrapping the SessionCallTimeoutError via __cause__, not the bare
        # SessionCallTimeoutError itself.
        assert isinstance(excinfo.value.__cause__, SessionCallTimeoutError)

        received = await asyncio.wait_for(sub.get(), timeout=1.0)

    assert received.scope == "unresponsive"
    assert received.instance_id == session.instance_id
    assert received.recovering is False


# ─── _call_timeout_cause: the __cause__-chain walk itself (review finding F1) ──


def test_call_timeout_cause_finds_the_top_level_exception() -> None:
    """The un-wrapped case: the escaping exception IS the timeout."""
    error = SessionCallTimeoutError("x did not answer within 1.0s")
    assert _call_timeout_cause(error) is error


def test_call_timeout_cause_walks_one_wrap() -> None:
    """The macros/execution.py shape: RuntimeError(payload) from exc."""
    timeout = SessionCallTimeoutError("x did not answer within 1.0s")
    wrapped = RuntimeError({"failed_action": "evaluate"})
    wrapped.__cause__ = timeout
    assert _call_timeout_cause(wrapped) is timeout


def test_call_timeout_cause_walks_multiple_wraps_within_bound() -> None:
    """Several layers deep, still within max_hops."""
    timeout = SessionCallTimeoutError("x did not answer within 1.0s")
    layer1 = RuntimeError("layer1")
    layer1.__cause__ = timeout
    layer2 = RuntimeError("layer2")
    layer2.__cause__ = layer1
    assert _call_timeout_cause(layer2, max_hops=4) is timeout


def test_call_timeout_cause_none_when_no_such_cause() -> None:
    """A plain exception, or one wrapping something else, finds nothing."""
    assert _call_timeout_cause(RuntimeError("boom")) is None
    assert _call_timeout_cause(None) is None

    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = ValueError("bad selector")
    assert _call_timeout_cause(wrapped) is None


def test_call_timeout_cause_respects_the_hop_bound() -> None:
    """A SessionCallTimeoutError beyond max_hops is not found -- the bound is
    a real ceiling, not just documentation, and this also proves a cyclic
    __cause__ chain (a self-referencing exception) cannot spin the walk
    forever."""
    timeout = SessionCallTimeoutError("x did not answer within 1.0s")
    layer1 = RuntimeError("layer1")
    layer1.__cause__ = timeout
    layer2 = RuntimeError("layer2")
    layer2.__cause__ = layer1
    layer3 = RuntimeError("layer3")
    layer3.__cause__ = layer2

    # layer3 -> layer2 -> layer1 -> timeout is 3 hops past layer3 itself (4
    # nodes total); max_hops=1 only inspects layer3 itself.
    assert _call_timeout_cause(layer3, max_hops=1) is None
    # A cyclic chain would spin an unbounded walk forever; bounding it means
    # this returns (rather than hangs) even though the cycle never reaches
    # SessionCallTimeoutError.
    cyclic = RuntimeError("cyclic")
    cyclic.__cause__ = cyclic
    assert _call_timeout_cause(cyclic, max_hops=4) is None


# ─── octowright_status() pull surface (Task 2 review round 2, F3) ─────────


async def test_unresponsive_target_is_retrievable_from_status(fake_session_kwargs: dict[str, object]) -> None:
    """A push notification is best-effort and OTel counters are noop unless
    ``PROVIDE_METRICS_ENABLED`` is set (off by default), so a timeout must
    also leave a retrievable record on the PULL surface
    (``octowright_status()``) in the common configuration -- and it must
    land under its own key, never bleeding into the renderer-crash
    ``"recent"`` key, since an unresponsive target has no crash report to
    correlate."""
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]

    with pytest.raises(SessionCallTimeoutError):
        async with session.operation("browser_evaluate"):
            raise SessionCallTimeoutError("browser_evaluate did not answer within 30.0s")

    snap = octowright_status()
    crash = snap["crash"]

    unresponsive = crash["unresponsive_recent"]
    assert len(unresponsive) == 1
    record = unresponsive[0]
    assert record["instance_id"] == session.instance_id
    assert record["kind"] == session.kind
    assert record["operation"] == "browser_evaluate"
    assert "ts" in record
    # No exception message is recorded -- the operation name is the
    # diagnostic signal, and a message could carry a URL/path.
    assert "error" not in record
    assert "message" not in record

    # The renderer-crash key must not see this record -- different scope,
    # different category, no bleed between them.
    assert all(entry.get("instance_id") != session.instance_id for entry in crash["recent"])


# ─── R1 (review round 3): shapes that swallow inside their OWN root lease ──


async def test_run_sequence_stop_on_failure_false_still_publishes(
    monkeypatch: pytest.MonkeyPatch, fake_session_kwargs: dict[str, object]
) -> None:
    """``run_sequence(stop_on_failure=False)`` catches each step's exception
    and continues WITHOUT re-raising, all inside its own root
    ``session.operation("macro_run_sequence")`` lease -- so nothing ever
    escapes that root frame for a root-only check to see. Before the R1 fix
    (publish from the innermost lease, not the root), this shape published
    no event at all -- driving the REAL ``run_sequence`` here (only
    ``load_macro``/``_dispatch_one`` are stubbed) proves it now does.
    """
    from octowright.macros import execution as _execution

    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]
    session.diagnostic_bundle = AsyncMock(return_value={"url": "https://octowright.com", "title": "t"})  # type: ignore[method-assign]

    monkeypatch.setattr(
        _execution,
        "load_macro",
        lambda name: {"name": name, "actions": [{"action": "evaluate", "expression": "1"}]},
    )

    async def _raise_timeout(*_args: object, **_kwargs: object) -> tuple[int, int]:
        raise SessionCallTimeoutError("browser_evaluate did not answer within 0.2s")

    monkeypatch.setattr(_execution, "_dispatch_one", _raise_timeout)

    async with session_event_bus.subscribe() as sub:
        result = await _execution.run_sequence(session=session, names=["wedged-macro"], stop_on_failure=False)

        received = await asyncio.wait_for(sub.get(), timeout=1.0)
        await _assert_nothing_else_arrives(sub)

    # run_sequence must not have raised -- stop_on_failure=False -- and must
    # report the step as failed rather than silently succeeding.
    assert result["ok"] is False
    assert len(result["steps"]) == 1
    assert result["steps"][0]["ok"] is False

    assert received.scope == "unresponsive"
    assert received.instance_id == session.instance_id
    assert received.recovering is False


async def test_macro_artifact_run_swallowed_timeout_still_publishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_session_kwargs: dict[str, object]
) -> None:
    """``macros/artifacts.py``'s ``run_macro_artifact`` catches whatever its
    nested ``run_macro()``/``"macro_run"`` lease raises and does NOT
    re-raise -- the exception never escapes its OWN root
    ``session.operation("macro_artifact_run")`` lease either. Before the R1
    fix this shape (root-only publish) published no event at all, because
    nothing ever reached a root frame's own except/finally. Drives the REAL
    ``run_macro_artifact`` end to end (``load_macro``/``_dispatch_one``
    stubbed, ``capture=False``/``verify=False`` to skip page/critical-point
    work unrelated to this) against a real artifact store under ``tmp_path``.
    """
    from octowright.macros import execution as _execution
    from tests._macro_artifact_fixtures import _reload as _reload_artifact_modules
    from tests._macro_artifact_fixtures import restore_reloaded_defaults

    _storage, macro_artifacts = _reload_artifact_modules(monkeypatch, tmp_path)
    macro_def = {"name": "wedged-macro", "actions": [{"action": "evaluate", "expression": "1"}]}
    monkeypatch.setattr(_execution, "load_macro", lambda name: macro_def)
    monkeypatch.setattr(macro_artifacts, "load_macro", lambda name: macro_def)

    async def _raise_timeout(*_args: object, **_kwargs: object) -> tuple[int, int]:
        raise SessionCallTimeoutError("browser_evaluate did not answer within 0.2s")

    monkeypatch.setattr(_execution, "_dispatch_one", _raise_timeout)

    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]

    try:
        async with session_event_bus.subscribe() as sub:
            result = await macro_artifacts.run_macro_artifact(session, "wedged-macro", capture=False, verify=False)

            received = await asyncio.wait_for(sub.get(), timeout=1.0)
            await _assert_nothing_else_arrives(sub)
    finally:
        restore_reloaded_defaults()

    # run_macro_artifact must not have raised -- it returns paths even when
    # replay fails -- and must report the run as failed.
    assert result["ok"] is False

    assert received.scope == "unresponsive"
    assert received.instance_id == session.instance_id
    assert received.recovering is False
