# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The session scrub set's cap, and the saturation it marks (#248, "Ledger limits").

`SessionPrivacyLedger` is append-only and every durable write is scrubbed
against it, so a session admitting many distinct credentials paid without bound.
`OCTOWRIGHT_MACRO_SCRUB_MAX_VALUES` bounds it -- but never by dropping a value:
privacy wins over cost, every value that reaches the ledger is still scrubbed.
Reaching the cap marks the session ``scrub_saturated`` for its lifetime, and the
only enforcement is refusing a macro run that would ADD a persistent value,
before it runs or writes anything.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution, privacy, privacy_ledger, scrub_capacity
from octowright.request_errors import InvalidRequestError
from tests._macro_artifact_fixtures import _FakeSession, _reload, restore_reloaded_defaults

HELD = "held-credential-1"  # pragma: allowlist secret
HELD_2 = "held-credential-2"  # pragma: allowlist secret
FRESH = "fresh-credential-3"  # pragma: allowlist secret
FRESH_2 = "fresh-credential-4"  # pragma: allowlist secret
ENV = scrub_capacity.SCRUB_MAX_VALUES_ENV


@pytest.fixture(autouse=True)
def _restore() -> Any:
    yield
    restore_reloaded_defaults()


def _session() -> MagicMock:
    session = MagicMock()
    session.instance_id = "instance-safe"
    session.kind = "chromium"
    session.diagnostic_bundle = AsyncMock(return_value={})
    return session


def _saturate(session: Any, monkeypatch: pytest.MonkeyPatch, *values: str) -> privacy.SessionPrivacyLedger:
    monkeypatch.setenv(ENV, str(len(values)))
    return privacy.install_sensitive_recorder(session, values)


def _install_macros(monkeypatch: pytest.MonkeyPatch, macros: dict[str, dict[str, Any]]) -> list[Any]:
    dispatched: list[Any] = []

    async def record_dispatch(_session: Any, action: Any, *_args: Any, **_kwargs: Any) -> tuple[int, int]:
        dispatched.append(action)
        return 1, 0

    monkeypatch.setattr(execution, "load_macro", lambda name: macros[name])
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "dispatch_plain_action", record_dispatch)
    # The fill guard checks the live element the value lands in; these runs have no page.
    monkeypatch.setattr(execution, "credential_fill_guard", lambda *_args: contextlib.nullcontext())
    return dispatched


LOGIN = {"actions": [{"action": "fill", "selector": "#pw", "value": "{{password}}"}]}


# -- the setting --------------------------------------------------------------


def test_the_cap_defaults_to_256() -> None:
    assert scrub_capacity.scrub_max_values({}) == 256
    assert scrub_capacity.scrub_max_values({ENV: " "}) == 256


def test_zero_disables_the_cap_and_an_integer_sets_it() -> None:
    assert scrub_capacity.scrub_max_values({ENV: "0"}) == 0
    assert scrub_capacity.scrub_max_values({ENV: " 12 "}) == 12


@pytest.mark.parametrize("raw", ["many", "-1", "2.5", "١٢"])
def test_an_unparsable_cap_keeps_the_default_and_warns(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    warned = MagicMock()
    monkeypatch.setattr(scrub_capacity, "log", warned)
    monkeypatch.setattr(scrub_capacity, "_WARNED", set())

    assert scrub_capacity.scrub_max_values({ENV: raw}) == 256
    assert scrub_capacity.scrub_max_values({ENV: raw}) == 256

    warned.warning.assert_called_once()
    assert warned.warning.call_args.args[0] == "octowright.macro.scrub_max_values_invalid"


# -- the ledger: never drops, saturates once ---------------------------------


def test_the_cap_never_drops_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "2")
    session = _session()
    for value in (HELD, HELD_2, FRESH, FRESH_2):
        privacy.admit_redacted_input(session, value)

    ledger = privacy.session_privacy_ledger(session)
    assert set(ledger.values) == {HELD, HELD_2, FRESH, FRESH_2}
    assert ledger.persistent_count == 4
    assert ledger.saturated


def test_saturation_is_logged_once_with_counts_never_values(monkeypatch: pytest.MonkeyPatch) -> None:
    warned = MagicMock()
    monkeypatch.setattr(privacy_ledger, "log", warned)
    monkeypatch.setenv(ENV, "2")
    ledger = privacy.SessionPrivacyLedger()

    ledger.add([HELD])
    assert not ledger.saturated
    ledger.add([HELD_2])
    ledger.add([FRESH])

    assert ledger.saturated
    warned.warning.assert_called_once()
    assert warned.warning.call_args.args[0] == "octowright.macro.scrub_saturated"
    assert warned.warning.call_args.kwargs == {"count": 2, "cap": 2}
    assert HELD not in repr(warned.mock_calls)


def test_run_scoped_values_are_not_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "1")
    ledger = privacy.SessionPrivacyLedger()
    token = ledger.open_run_scope()
    ledger.add_run_scoped(token, [HELD, HELD_2])

    assert ledger.persistent_count == 0
    assert not ledger.saturated


def test_saturation_is_sticky_and_zero_never_saturates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "0")
    unbounded = privacy.SessionPrivacyLedger([f"value-{index:04d}" for index in range(300)])
    assert not unbounded.saturated

    monkeypatch.setenv(ENV, "1")
    ledger = privacy.SessionPrivacyLedger([HELD])
    assert ledger.saturated
    monkeypatch.setenv(ENV, "0")
    ledger.add([FRESH])
    assert ledger.saturated


# -- refusal: a run that would add a value -----------------------------------


@pytest.mark.asyncio
async def test_a_run_adding_a_value_to_a_saturated_session_is_refused_before_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    ledger = _saturate(session, monkeypatch, HELD)
    dispatched = _install_macros(monkeypatch, {"login": LOGIN})

    with pytest.raises(InvalidRequestError) as caught:
        await execution.run_macro(session, "login", {"password": FRESH})

    message = str(caught.value)
    assert "1 of 1" in message
    assert "relaunch the browser" in message.lower()
    assert ENV in message
    assert FRESH not in message
    assert dispatched == []
    execution._push_status.assert_not_called()
    assert FRESH not in ledger.values


@pytest.mark.asyncio
async def test_an_admission_taking_the_session_over_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    monkeypatch.setenv(ENV, "2")
    ledger = privacy.install_sensitive_recorder(session, [HELD])
    macro = {"actions": [{"action": "fill", "selector": "#a", "value": "{{password}}{{api_token}}"}]}
    dispatched = _install_macros(monkeypatch, {"two": macro})

    with pytest.raises(InvalidRequestError, match="1 of 2"):
        await execution.run_macro(session, "two", {"password": FRESH, "api_token": FRESH_2})

    assert dispatched == []
    assert not ledger.saturated
    assert ledger.persistent_count == 1


@pytest.mark.asyncio
async def test_a_run_reaching_the_cap_exactly_runs_and_reports_saturation(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    monkeypatch.setenv(ENV, "2")
    privacy.install_sensitive_recorder(session, [HELD])
    dispatched = _install_macros(monkeypatch, {"login": LOGIN})

    result = await execution.run_macro(session, "login", {"password": FRESH})

    assert len(dispatched) == 1
    assert result["scrub_saturated"] is True


@pytest.mark.asyncio
async def test_a_run_whose_values_are_all_held_runs_on_a_saturated_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    _saturate(session, monkeypatch, HELD)
    dispatched = _install_macros(monkeypatch, {"login": LOGIN})

    result = await execution.run_macro(session, "login", {"password": HELD})

    assert len(dispatched) == 1
    assert result["scrub_saturated"] is True


@pytest.mark.asyncio
async def test_an_unsaturated_run_result_carries_no_saturation_field(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    _install_macros(monkeypatch, {"login": LOGIN})

    result = await execution.run_macro(session, "login", {"password": FRESH})

    assert "scrub_saturated" not in result


@pytest.mark.asyncio
async def test_run_scoped_identity_values_never_cause_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY", "all")
    session = _session()
    _saturate(session, monkeypatch, HELD)
    macro = {"actions": [{"action": "fill", "selector": "#email", "value": "{{email}}"}]}
    dispatched = _install_macros(monkeypatch, {"profile": macro})

    await execution.run_macro(session, "profile", {"email": "someone.new@example.test"})

    assert len(dispatched) == 1


def test_a_direct_password_fill_is_never_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    ledger = _saturate(session, monkeypatch, HELD)

    privacy.admit_redacted_input(session, FRESH)

    assert FRESH in ledger.values


@pytest.mark.asyncio
async def test_the_failure_payload_of_a_saturated_session_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    _saturate(session, monkeypatch, HELD)

    async def fail(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise ValueError("click failed")

    _install_macros(monkeypatch, {"login": LOGIN})
    monkeypatch.setattr(execution, "dispatch_plain_action", fail)

    with pytest.raises(RuntimeError) as caught:
        await execution.run_macro(session, "login", {"password": HELD})

    assert caught.value.args[0]["scrub_saturated"] is True


# -- nested calls, sequences, artifacts ---------------------------------------


@pytest.mark.asyncio
async def test_a_refused_nested_call_is_a_step_failure_that_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    inner = session.recorder
    ledger = _saturate(session, monkeypatch, HELD)
    macros = {
        "outer": {"actions": [{"action": "macro_call", "name": "child", "args": {"password": FRESH}}]},
        "child": LOGIN,
    }
    dispatched = _install_macros(monkeypatch, macros)

    with pytest.raises(RuntimeError) as caught:
        await execution.run_macro(session, "outer", {})

    payload = caught.value.args[0]
    assert "scrub set is full" in str(payload)
    assert payload["scrub_saturated"] is True
    assert FRESH not in repr(caught.value.args)
    assert dispatched == []
    assert FRESH not in ledger.values
    assert FRESH not in repr(inner.mock_calls)


@pytest.mark.asyncio
async def test_a_refused_sequence_step_is_a_failed_step(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    _saturate(session, monkeypatch, HELD)
    dispatched = _install_macros(monkeypatch, {"login": LOGIN})

    result = await execution.run_sequence(
        session=session,
        names=["login", "login"],
        args_list=[{"password": HELD}, {"password": FRESH}],
        stop_on_failure=False,
    )

    assert [step["ok"] for step in result["steps"]] == [True, False]
    assert "scrub set is full" in result["steps"][1]["error"]
    assert FRESH not in repr(result)
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_an_artifact_run_is_refused_before_its_run_dir_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(name="login", macro={"name": "login", "parameters": ["password"], **LOGIN})
    session = _FakeSession(tmp_path)
    _saturate(session, monkeypatch, HELD)
    replay = AsyncMock()
    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", replay)

    with pytest.raises(InvalidRequestError, match="scrub set is full"):
        await macro_artifacts.run_macro_artifact(session, "login", {"password": FRESH}, capture=False)

    replay.assert_not_called()
    assert not (tmp_path / "recordings" / "artifacts").exists()


@pytest.mark.asyncio
async def test_an_artifact_run_on_a_saturated_session_reports_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(name="login", macro={"name": "login", "parameters": ["password"], **LOGIN})
    session = _FakeSession(tmp_path)
    _saturate(session, monkeypatch, HELD)

    async def fake_run_macro(
        *, session: Any, name: str, args: Any, slowmo_ms: Any = None, **_private: Any
    ) -> dict[str, Any]:
        return {"macro": name, "executed": 1, "skipped": 0}

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)

    result = await macro_artifacts.run_macro_artifact(session, "login", {"password": HELD}, capture=False)

    assert result["scrub_saturated"] is True
