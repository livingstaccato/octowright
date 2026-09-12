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
