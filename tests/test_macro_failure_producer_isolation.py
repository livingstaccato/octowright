# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""One failing diagnostic producer must not cost the caller the other two.

``_build_failure_payload`` asks three independently-fallible producers for
evidence about a macro step that already failed: the diagnostic bundle, the
healing suggestion, and the failed-request tail. Each is wrapped separately so
its own failure is recorded IN the payload rather than raised over the dispatch
failure the payload exists to explain -- otherwise a flaky producer replaces a
precise "timed out waiting for #foo" with its own unrelated traceback.

Nothing exercised any of those three ``except`` arms. Mutation testing found it:
the arms are reachable, and every mutant of them survived -- including replacing
a whole recovered bundle with ``None``. That is the same thing as saying the
error-handling could be deleted and the suite would stay green.

``_finish_macro_run``'s telemetry had the same shape of gap. Its docstring calls
the metric and the structured log load-bearing on BOTH the ok and failed paths,
and its own comment records why (outside the ``finally`` a raised RuntimeError
skips them entirely, so the histogram only ever measures successful runs) -- but
nothing asserted on a single field of either, so every log kwarg could be
replaced with ``None`` undetected.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution as _execution
from octowright.macros.execution import run_macro
from tests._operation_gate_fakes import OperationAwareFake

BOOM = "A4-PRODUCER-EXPLODED"


class _FakeSession(OperationAwareFake):
    instance_id = "fake-instance"
    kind = "chromium"

    def __init__(self) -> None:
        super().__init__()
        self.page = MagicMock()
        self.page.evaluate = AsyncMock()
        self.diagnostic_bundle = AsyncMock(return_value={"url": "https://x"})


@pytest.fixture
def session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def failing_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """A one-action macro whose only action raises."""
    monkeypatch.setattr(_execution, "load_macro", lambda _n: {"actions": [{"action": "click", "selector": "#a"}]})
    monkeypatch.setattr(_execution, "substitute", lambda actions, _args: actions)

    async def _boom(*_a: Any, **_kw: Any) -> tuple[int, int]:
        raise RuntimeError("step failed")

    monkeypatch.setattr(_execution, "_dispatch_one", _boom)
    monkeypatch.setattr(_execution, "_suggest_fix", AsyncMock(return_value="try harder"))
    monkeypatch.setattr(_execution, "_failed_requests_tail", lambda _s: [{"status": 409}])


async def _payload(session: _FakeSession) -> dict[str, Any]:
    with pytest.raises(RuntimeError) as excinfo:
        await run_macro(session, "m", {})
    payload = excinfo.value.args[0]
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
@pytest.mark.usefixtures("failing_step")
async def test_a_failing_bundle_producer_is_reported_and_the_others_still_land(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.diagnostic_bundle = AsyncMock(side_effect=RuntimeError(BOOM))

    payload = await _payload(session)

    assert BOOM in payload["bundle"]["diagnostic_error"]
    # The other two producers are unaffected.
    assert payload["healing_suggestion"] == "try harder"
    assert payload["failed_requests"] == [{"status": 409}]
    # And the original dispatch failure is still what the payload is about.
    assert "step failed" in payload["original"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("failing_step")
async def test_a_failing_healer_is_reported_into_the_bundle_and_suppresses_the_suggestion(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_execution, "_suggest_fix", AsyncMock(side_effect=RuntimeError(BOOM)))

    payload = await _payload(session)

    assert BOOM in payload["bundle"]["healing_error"]
    # A healer that raised produced no suggestion, so the key is absent rather
    # than present and empty.
    assert "healing_suggestion" not in payload
    assert payload["bundle"]["url"] == "https://x"
    assert payload["failed_requests"] == [{"status": 409}]


@pytest.mark.asyncio
@pytest.mark.usefixtures("failing_step")
async def test_a_failing_network_tail_is_reported_and_degrades_to_an_empty_list(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_s: Any) -> list[dict[str, Any]]:
        raise RuntimeError(BOOM)

    monkeypatch.setattr(_execution, "_failed_requests_tail", _boom)

    payload = await _payload(session)

    assert BOOM in payload["bundle"]["network_error"]
    # An empty list, not a missing key: the field's absence would read as "this
    # producer was not asked", which is a different claim.
    assert payload["failed_requests"] == []
    assert payload["healing_suggestion"] == "try harder"


@pytest.mark.asyncio
@pytest.mark.usefixtures("failing_step")
async def test_a_classified_macro_suppresses_the_bundle_rather_than_producing_one(
    session: _FakeSession,
) -> None:
    """The generic producer persists raw HTML and a raw screenshot."""
    payload = await _payload(session)  # unclassified first, for contrast
    assert payload["bundle"]["url"] == "https://x"

    session.diagnostic_bundle.reset_mock()
    with pytest.raises(RuntimeError) as excinfo:
        await run_macro(session, "m", {"password": "A4-CLASSIFIED-CANARY"})  # pragma: allowlist secret

    classified = excinfo.value.args[0]
    assert classified["bundle"] == {"diagnostic_suppressed": "classified macro arguments"}
    session.diagnostic_bundle.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_run_outcome_log_and_metric_carry_the_run_on_both_paths(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status, counts and slowmo must reach the operator-visible sinks."""
    monkeypatch.setattr(_execution, "load_macro", lambda _n: {"actions": [{"action": "click", "selector": "#a"}]})
    monkeypatch.setattr(_execution, "substitute", lambda actions, _args: actions)

    logged: list[tuple[str, dict[str, Any]]] = []

    class _Capture:
        def info(self, event: str, **kw: Any) -> None:
            logged.append((event, kw))

        def __getattr__(self, _name: str) -> Any:
            return lambda *_a, **_kw: None

    monkeypatch.setattr(_execution, "log", _Capture())
    added: list[tuple[int, dict[str, Any]]] = []
    monkeypatch.setattr(_execution._MACRO_RUN, "add", lambda n, attributes: added.append((n, attributes)))

    async def _ok(*_a: Any, **_kw: Any) -> tuple[int, int]:
        return (3, 2)

    monkeypatch.setattr(_execution, "_dispatch_one", _ok)
    await run_macro(session, "happy", {}, slowmo_ms=40)

    event, fields = logged[-1]
    assert event == "octowright.macro.run"
    assert fields["name"] == "happy"
    assert fields["instance_id"] == "fake-instance"
    assert fields["executed"] == 3
    assert fields["skipped"] == 2
    assert fields["slowmo_ms"] == 40
    assert fields["status"] == "ok"
    assert added[-1] == (1, {"macro": "happy", "status": "ok"})

    async def _boom(*_a: Any, **_kw: Any) -> tuple[int, int]:
        raise RuntimeError("step failed")

    monkeypatch.setattr(_execution, "_dispatch_one", _boom)
    monkeypatch.setattr(_execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(_execution, "_failed_requests_tail", lambda _s: [])
    with pytest.raises(RuntimeError):
        await run_macro(session, "sad", {})

    # The failed datapoint must land too -- that is the whole reason this runs
    # from a `finally` rather than after the try.
    assert logged[-1][1]["status"] == "failed"
    assert added[-1] == (1, {"macro": "sad", "status": "failed"})


# ── _dispatch_one: the arguments that carry authority and bound recursion ────
#
# Twenty mutants survived here. Most are the log/string noise category, but
# three clusters are not, and each is load-bearing:
#
#   - The composition-root authority hook could have `action=` or
#     `invocation_stack=` dropped entirely and nothing noticed. That hook is
#     the synchronous permission check for browser actions; a check that
#     cannot see WHICH action it is authorizing is not a check.
#   - `max_depth if max_depth is not None else MAX_MACRO_CALL_DEPTH` could be
#     inverted, because no test ever passed an explicit depth.
#   - The conditional-branch recursion could lose `invocation_stack`,
#     `max_depth` or `slowmo_ms`, which is how macro_call depth limiting stops
#     working inside an `if`.


class _BoundarySession(_FakeSession):
    """Records what the authority hook was handed."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[dict[str, Any]] = []
        self._octowright_before_macro_action = lambda **kw: self.seen.append(kw)


@pytest.mark.asyncio
async def test_the_authority_hook_is_told_which_action_and_which_macro_stack() -> None:
    from octowright.macros.execution import _dispatch_one

    session = _BoundarySession()
    action = {"action": "click", "selector": "#pay"}

    with pytest.raises(Exception):
        await _dispatch_one(session, action, invocation_stack=["outer", "inner"])

    assert session.seen == [{"action": action, "invocation_stack": ("outer", "inner")}]


@pytest.mark.asyncio
async def test_an_explicit_max_depth_is_used_rather_than_the_module_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ternary reads `is not None`; inverted, an explicit depth is discarded."""
    from octowright.macros.execution import _dispatch_one

    seen: list[int] = []

    async def _capture(*_a: Any, **kw: Any) -> tuple[int, int]:
        seen.append(kw["max_depth"])
        return (1, 0)

    monkeypatch.setattr(_execution, "dispatch_macro_call", _capture)

    session = _FakeSession()
    await _dispatch_one(
        session,
        {"action": "macro_call", "name": "child"},
        invocation_stack=["outer"],
        max_depth=2,
    )

    assert seen == [2], "an explicit max_depth was replaced by the module default"


@pytest.mark.asyncio
async def test_the_conditional_branch_carries_stack_depth_and_slowmo_into_recursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing any of the three silently disables depth limiting inside an `if`.

    Observed through a nested ``macro_call``, which is the action that actually
    consumes the stack and the depth -- so this asserts the values arrived,
    not merely that the recursion happened.
    """
    import octowright.conditional as conditional
    from octowright.macros.execution import _dispatch_one

    nested = {"action": "macro_call", "name": "child"}
    captured: dict[str, Any] = {}
    slept: list[float] = []

    async def _capture_call(*_a: Any, **kw: Any) -> tuple[int, int]:
        captured.update(kw)
        return (1, 0)

    async def _fake_conditional(session: Any, _action: dict[str, Any], recurse: Any) -> tuple[int, int]:
        return await recurse(session, nested)

    async def _no_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(_execution, "dispatch_macro_call", _capture_call)
    monkeypatch.setattr(conditional, "dispatch_conditional", _fake_conditional)
    monkeypatch.setattr(conditional, "CONDITIONAL_ACTIONS", {"if_visible"})
    monkeypatch.setattr(_execution.asyncio, "sleep", _no_sleep)

    session = _FakeSession()
    await _dispatch_one(
        session,
        {"action": "if_visible", "selector": "#gate"},
        invocation_stack=["outer"],
        max_depth=4,
        slowmo_ms=250,
    )

    assert captured["invocation_stack"] == ["outer"], "the macro stack was lost crossing the conditional"
    assert captured["max_depth"] == 4, "depth limiting was reset inside the conditional"
    # Only the OUTER action reaches the slowmo sleep here: a macro_call returns
    # before it. Slowmo propagation is asserted separately, through an action
    # that actually gets there.
    assert slept == [0.25]


@pytest.mark.asyncio
async def test_slowmo_survives_the_conditional_into_the_recursed_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain action DOES reach the sleep, so the recursed one must sleep too.

    Asserting this through a nested ``macro_call`` looks equivalent and is not:
    ``_dispatch_one`` returns for a macro_call before the slowmo sleep, so the
    recursed call never sleeps whether or not the budget was forwarded, and the
    mutant that drops ``slowmo_ms=`` from the closure survives a green test.
    """
    import octowright.conditional as conditional
    from octowright.macros.execution import _dispatch_one

    slept: list[float] = []

    async def _no_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def _plain(*_a: Any, **_kw: Any) -> tuple[int, int]:
        return (1, 0)

    async def _fake_conditional(session: Any, _action: dict[str, Any], recurse: Any) -> tuple[int, int]:
        return await recurse(session, {"action": "click", "selector": "#inner"})

    monkeypatch.setattr(_execution, "dispatch_plain_action", _plain)
    monkeypatch.setattr(conditional, "dispatch_conditional", _fake_conditional)
    monkeypatch.setattr(conditional, "CONDITIONAL_ACTIONS", {"if_visible"})
    monkeypatch.setattr(_execution.asyncio, "sleep", _no_sleep)

    await _dispatch_one(
        _FakeSession(),
        {"action": "if_visible", "selector": "#gate"},
        invocation_stack=["outer"],
        slowmo_ms=250,
    )

    assert slept == [0.25, 0.25], f"the recursed action lost its slowmo budget: {slept}"


@pytest.mark.asyncio
async def test_slowmo_defaults_to_no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero default would insert a sleep into every un-slowmoed action.

    Asserted behaviourally rather than by reading the signature: a signature
    check inspects whatever wrapper is bound to the name, so it cannot see the
    default actually used at the call site and never fails.
    """
    from octowright.macros.execution import _dispatch_one

    slept: list[float] = []

    async def _no_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def _plain(*_a: Any, **_kw: Any) -> tuple[int, int]:
        return (1, 0)

    monkeypatch.setattr(_execution, "dispatch_plain_action", _plain)
    monkeypatch.setattr(_execution.asyncio, "sleep", _no_sleep)

    await _dispatch_one(_FakeSession(), {"action": "click", "selector": "#x"})

    assert slept == [], f"an un-slowmoed action slept: {slept}"


@pytest.mark.asyncio
async def test_a_nested_macro_call_inherits_the_slowmo_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping it makes a nested macro's children run at full speed silently."""
    from octowright.macros.execution import _dispatch_one

    forwarded: dict[str, Any] = {}

    async def _capture(*_a: Any, **kw: Any) -> tuple[int, int]:
        # dispatch_macro_call hands each child action back through this.
        await kw["dispatch_one"](_FakeSession(), {"action": "click", "selector": "#c"})
        return (1, 0)

    slept: list[float] = []

    async def _no_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def _plain(*_a: Any, **_kw: Any) -> tuple[int, int]:
        return (1, 0)

    monkeypatch.setattr(_execution, "dispatch_macro_call", _capture)
    monkeypatch.setattr(_execution, "dispatch_plain_action", _plain)
    monkeypatch.setattr(_execution.asyncio, "sleep", _no_sleep)

    await _dispatch_one(
        _FakeSession(),
        {"action": "macro_call", "name": "child"},
        invocation_stack=["outer"],
        slowmo_ms=250,
    )

    assert slept == [0.25], f"a nested macro's child lost the slowmo budget: {slept}"
    assert forwarded == {}


@pytest.mark.asyncio
async def test_a_macro_call_without_an_invocation_stack_says_why() -> None:
    """The message is the whole diagnostic; RuntimeError(None) survived without it."""
    from octowright.macros.execution import _dispatch_one

    with pytest.raises(RuntimeError) as excinfo:
        await _dispatch_one(_FakeSession(), {"action": "macro_call", "name": "child"})

    assert "macro_call can only execute in a macro context" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_failing_pill_push_is_logged_rather_than_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_push_status` swallows by design, so the log line is the only signal.

    Every mutant of this function's body survives -- the swallow makes it
    structurally unkillable from the outside. That is the repo's own
    silent-swallow policy: a best-effort path may swallow, but it must say so.
    """
    from octowright.macros.execution import _push_status

    debugged: list[tuple[str, dict[str, Any]]] = []

    class _Capture:
        def debug(self, event: str, **kw: Any) -> None:
            debugged.append((event, kw))

        def __getattr__(self, _name: str) -> Any:
            return lambda *_a, **_kw: None

    monkeypatch.setattr(_execution, "log", _Capture())

    session = _FakeSession()
    session.page.evaluate = AsyncMock(side_effect=RuntimeError("A4-PILL-EXPLODED"))

    await _push_status(session, text="anything")  # must not raise

    assert debugged, "a failing pill push produced no diagnostic at all"
    event, fields = debugged[-1]
    assert event == "octowright.macro.pill_push_failed"
    assert "A4-PILL-EXPLODED" in fields["error"]
