# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""#247: short and common identity/contextual values, and how long they are scrubbed.

Under ``OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY=all`` every classified tier is
blind-scrubbed. A short or common identity/contextual value (``session="1"``,
``user="admin"``) then rewrote ``nth-child(1)``, ``?page=1`` and the word
"admin" in every later row on the session, because the session ledger is never
cleared. Two decided fixes, both configurable:

* a length floor and a common-value list exempt identity/contextual values
  (never credentials) from blind scrubbing, and the run result names the
  exempted argument paths -- never the values;
* identity/contextual values that ARE scrubbed are scrubbed for the run that
  supplied them only; they never join the session-wide ledger.

Credential-tier values are untouched by both: scrubbed at any length,
session-wide.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.macros import execution, privacy, scrub_admission
from octowright.macros.privacy import (
    BLIND_SCRUB_POLICY_ENV,
    MacroArgPrivacy,
    blind_scrub_arg_values,
    scrub_sensitive_values,
)
from octowright.macros.scrub_admission import (
    DEFAULT_SCRUB_COMMON_VALUES,
    DEFAULT_SCRUB_MIN_LENGTH,
    SCRUB_COMMON_VALUES_ENV,
    SCRUB_MIN_LENGTH_ENV,
    SCRUB_RUN_SCOPED_ENV,
)

ISSUE_ROW = {
    "action": "click",
    "selector": "li:nth-child(1) > a",
    "url": "https://shop.test/list?page=1&sort=11",
    "text": "Administrator panel, admin tools, 1 item",
}
EMAIL = "person-a247@example.test"
SHORT_PASSWORD = "1"  # pragma: allowlist secret -- a fixture
COMMON_PASSWORD = "admin"  # pragma: allowlist secret -- a fixture


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (SCRUB_MIN_LENGTH_ENV, SCRUB_COMMON_VALUES_ENV, SCRUB_RUN_SCOPED_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "all")


# --- the issue's own reproduction --------------------------------------------


@pytest.mark.parametrize("args", [{"session": "1"}, {"user": "admin"}, {"email": "ADMIN"}])
def test_issue_247_short_and_common_values_leave_the_row_untouched(args: dict[str, str]) -> None:
    assert blind_scrub_arg_values(args) == ()
    assert scrub_sensitive_values(ISSUE_ROW, blind_scrub_arg_values(args)) == ISSUE_ROW


def test_a_long_uncommon_identity_value_is_still_admitted() -> None:
    assert blind_scrub_arg_values({"email": EMAIL, "session": "1"}) == (EMAIL,)


# --- credentials are never exempt --------------------------------------------


@pytest.mark.parametrize("policy", ["all", "credentials", "reject"])
@pytest.mark.parametrize("value", [SHORT_PASSWORD, COMMON_PASSWORD])
def test_a_short_or_common_credential_is_always_admitted(
    monkeypatch: pytest.MonkeyPatch, policy: str, value: str
) -> None:
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, policy)

    assert blind_scrub_arg_values({"password": value}) == (value,)


def test_a_value_forced_credential_tier_by_position_is_never_exempt() -> None:
    """An ``expect_no_text`` argument is credential-tier whatever its name."""
    view = MacroArgPrivacy.for_macro([{"action": "expect_no_text", "text": "{{user}}"}])

    assert view.blind_scrub({"user": "admin"}) == ("admin",)
    assert view.admission({"user": "admin"}).exempt == ()


def test_a_value_both_credential_and_identity_is_scrubbed_and_not_reported() -> None:
    admission = MacroArgPrivacy().admission({"password": COMMON_PASSWORD, "user": COMMON_PASSWORD})

    assert admission.values == (COMMON_PASSWORD,)
    assert admission.persistent == (COMMON_PASSWORD,)
    assert admission.exempt == ()


# --- the exemption report ------------------------------------------------------


def test_exemptions_name_paths_tiers_and_reasons_never_values() -> None:
    admission = MacroArgPrivacy().admission({"session": "1", "payload": {"user": "guest"}, "email": EMAIL})

    assert [(item.path, item.tier, item.reason) for item in admission.exempt] == [
        ("payload.user", "contextual", "common"),
        ("session", "contextual", "short"),
    ]
    rendered = repr([item.as_dict() for item in admission.exempt])
    assert "guest" not in rendered
    assert "'1'" not in rendered


def test_no_exemptions_are_reported_when_the_policy_admits_no_identity_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under ``credentials`` the floor is moot: nothing of that tier is blind-scrubbed."""
    monkeypatch.setenv(BLIND_SCRUB_POLICY_ENV, "credentials")

    assert MacroArgPrivacy().admission({"session": "1"}).exempt == ()


# --- configuration -------------------------------------------------------------


def test_defaults() -> None:
    assert DEFAULT_SCRUB_MIN_LENGTH == 4
    assert scrub_admission.scrub_min_length() == 4
    assert {"admin", "test", "user", "guest", "root", "demo"} <= DEFAULT_SCRUB_COMMON_VALUES
    assert scrub_admission.scrub_common_values() == DEFAULT_SCRUB_COMMON_VALUES
    assert scrub_admission.scrub_run_scoped() is True


def test_min_length_zero_disables_the_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_MIN_LENGTH_ENV, "0")

    assert blind_scrub_arg_values({"session": "1"}) == ("1",)


def test_min_length_can_be_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_MIN_LENGTH_ENV, " 30 ")

    assert blind_scrub_arg_values({"email": EMAIL}) == ()


@pytest.mark.parametrize("raw", ["four", "-1", "2.5", "nan"])
def test_an_unparsable_min_length_disables_the_floor(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """The safe side is MORE scrubbing: a typo must not exempt values."""
    monkeypatch.setenv(SCRUB_MIN_LENGTH_ENV, raw)

    assert scrub_admission.scrub_min_length() == 0
    assert blind_scrub_arg_values({"session": "1"}) == ("1",)


def test_an_empty_min_length_keeps_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_MIN_LENGTH_ENV, "  ")

    assert scrub_admission.scrub_min_length() == DEFAULT_SCRUB_MIN_LENGTH


def test_the_common_list_is_compared_ignoring_case_and_surrounding_space() -> None:
    assert blind_scrub_arg_values({"user": " Admin "}) == ()


def test_the_common_list_can_be_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_COMMON_VALUES_ENV, "acme-corp, Shop-Demo")

    assert scrub_admission.scrub_common_values() == frozenset({"acme-corp", "shop-demo"})
    assert blind_scrub_arg_values({"user": "admin"}) == ("admin",)
    assert blind_scrub_arg_values({"user": "SHOP-DEMO"}) == ()


def test_the_common_list_can_be_extended(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_COMMON_VALUES_ENV, "+acme-corp")

    assert scrub_admission.scrub_common_values() == DEFAULT_SCRUB_COMMON_VALUES | {"acme-corp"}
    assert blind_scrub_arg_values({"user": "acme-corp"}) == ()
    assert blind_scrub_arg_values({"user": "admin"}) == ()


@pytest.mark.parametrize("raw", ["", " ", ","])
def test_an_empty_common_list_disables_it(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(SCRUB_COMMON_VALUES_ENV, raw)
    monkeypatch.setenv(SCRUB_MIN_LENGTH_ENV, "0")

    assert scrub_admission.scrub_common_values() == frozenset()
    assert blind_scrub_arg_values({"user": "admin"}) == ("admin",)


@pytest.mark.parametrize(
    ("raw", "expected"), [("off", False), ("0", False), ("FALSE", False), ("on", True), ("", True), ("1", True)]
)
def test_run_scoped_tokens(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv(SCRUB_RUN_SCOPED_ENV, raw)

    assert scrub_admission.scrub_run_scoped() is expected


def test_an_unknown_run_scoped_value_means_session_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session-wide scrubs MORE, so it is the side an unparsable value lands on."""
    monkeypatch.setenv(SCRUB_RUN_SCOPED_ENV, "sometimes")

    assert scrub_admission.scrub_run_scoped() is False


# --- run scoping, through a real run -------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def record(self, action: str, **fields: Any) -> None:
        self.rows.append((action, fields))

    def record_control(self, action: str, **fields: Any) -> None:
        self.rows.append((action, fields))


def _session() -> tuple[MagicMock, _Recorder]:
    session = MagicMock()
    session.instance_id = "instance-247"
    session.kind = "chromium"
    session.durable_text_scrubber = None
    session.diagnostic_bundle = AsyncMock(return_value={})
    inner = _Recorder()
    session.recorder = inner
    return session, inner


def _install_macros(monkeypatch: pytest.MonkeyPatch, macros: dict[str, Any], echo: str) -> None:
    """Each plain step records a row echoing *echo*, as a page would."""

    async def echo_dispatch(session: Any, *_args: Any, **_kwargs: Any) -> tuple[int, int]:
        session.recorder.record("console", text=f"page says {echo}")
        return 1, 0

    monkeypatch.setattr(execution, "load_macro", lambda name: macros[name])
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "dispatch_plain_action", echo_dispatch)


def _ledger(session: Any) -> privacy.SessionPrivacyLedger:
    ledger = getattr(session, privacy.SESSION_PRIVACY_LEDGER_ATTR)
    assert isinstance(ledger, privacy.SessionPrivacyLedger)
    return ledger


@pytest.mark.asyncio
async def test_an_identity_value_is_scrubbed_during_its_run_and_not_after(monkeypatch: pytest.MonkeyPatch) -> None:
    session, inner = _session()
    _install_macros(monkeypatch, {"m": {"actions": [{"action": "click", "selector": "#go"}]}}, EMAIL)

    await execution.run_macro(session, "m", {"email": EMAIL})

    assert EMAIL not in repr(inner.rows), "the run's own rows must be scrubbed"
    assert EMAIL not in _ledger(session).values
    session.recorder.record("console", text=f"later {EMAIL}")
    assert inner.rows[-1][1]["text"] == f"later {EMAIL}"


@pytest.mark.asyncio
async def test_a_credential_stays_session_wide_however_short(monkeypatch: pytest.MonkeyPatch) -> None:
    session, inner = _session()
    _install_macros(monkeypatch, {"m": {"actions": [{"action": "click", "selector": "#go"}]}}, "x")

    await execution.run_macro(session, "m", {"password": SHORT_PASSWORD, "email": EMAIL})

    assert _ledger(session).values == (SHORT_PASSWORD,)
    session.recorder.record("console", text="pin=1 then 1 again")
    assert inner.rows[-1][1]["text"] == "pin=<redacted> then <redacted> again"


@pytest.mark.asyncio
async def test_run_scoping_off_restores_session_wide_identity_scrubbing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_RUN_SCOPED_ENV, "off")
    session, inner = _session()
    _install_macros(monkeypatch, {"m": {"actions": [{"action": "click", "selector": "#go"}]}}, EMAIL)

    await execution.run_macro(session, "m", {"email": EMAIL})

    session.recorder.record("console", text=f"later {EMAIL}")
    assert EMAIL not in inner.rows[-1][1]["text"]


@pytest.mark.asyncio
async def test_a_nested_calls_identity_value_lasts_for_the_outer_run_only(monkeypatch: pytest.MonkeyPatch) -> None:
    session, inner = _session()
    macros = {
        "outer": {
            "actions": [
                {"action": "macro_call", "name": "child", "args": {"email": EMAIL}},
                {"action": "click", "selector": "#after"},
            ]
        },
        "child": {"actions": [{"action": "click", "selector": "#go"}]},
    }
    _install_macros(monkeypatch, macros, EMAIL)

    await execution.run_macro(session, "outer", {})

    assert len(inner.rows) == 2
    assert EMAIL not in repr(inner.rows), "the outer run's later step is still inside the run"
    session.recorder.record("console", text=f"later {EMAIL}")
    assert inner.rows[-1][1]["text"] == f"later {EMAIL}"


@pytest.mark.asyncio
async def test_a_failed_run_still_closes_its_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    session, _inner = _session()

    async def fail(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise ValueError("boom")

    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": [{"action": "click", "selector": "#a"}]})
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "dispatch_plain_action", fail)

    with pytest.raises(RuntimeError) as caught:
        await execution.run_macro(session, "m", {"email": EMAIL, "session": "1"})

    assert _ledger(session).values == ()
    payload = caught.value.args[0]
    assert payload["scrub_exempt_args"] == [{"macro": "m", "path": "session", "tier": "contextual", "reason": "short"}]


@pytest.mark.asyncio
async def test_the_run_result_names_exempt_arguments_by_path(monkeypatch: pytest.MonkeyPatch) -> None:
    session, _inner = _session()
    macros = {
        "outer": {"actions": [{"action": "macro_call", "name": "child", "args": {"user": "demo"}}]},
        "child": {"actions": [{"action": "click", "selector": "#go"}]},
    }
    _install_macros(monkeypatch, macros, "x")

    result = await execution.run_macro(session, "outer", {"session": "1", "email": EMAIL})

    assert result["scrub_exempt_args"] == [
        {"macro": "outer", "path": "session", "tier": "contextual", "reason": "short"},
        {"macro": "child", "path": "user", "tier": "contextual", "reason": "common"},
    ]
    assert "demo" not in repr(result["scrub_exempt_args"])


@pytest.mark.asyncio
async def test_a_run_with_nothing_exempt_carries_no_report(monkeypatch: pytest.MonkeyPatch) -> None:
    session, _inner = _session()
    _install_macros(monkeypatch, {"m": {"actions": []}}, "x")

    result = await execution.run_macro(session, "m", {"email": EMAIL})

    assert "scrub_exempt_args" not in result


# --- the exported script applies the same admission ----------------------------


def _exported_module(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    source = render_macro_cli(name="scrub-admission", macro={"actions": []})
    module: dict[str, Any] = {"__name__": "scrub_admission_script"}
    exec(compile(source, "<scrub-admission>", "exec"), module)
    return module


@pytest.mark.parametrize(
    "env",
    [
        {},
        {SCRUB_MIN_LENGTH_ENV: "0"},
        {SCRUB_MIN_LENGTH_ENV: "bogus"},
        {SCRUB_COMMON_VALUES_ENV: "+acme-corp"},
        {SCRUB_COMMON_VALUES_ENV: ""},
    ],
)
def test_the_exported_script_admits_what_live_replay_admits(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    module = _exported_module(monkeypatch)
    args = {
        "session": "1",
        "user": "admin",
        "contact": "acme-corp",
        "email": EMAIL,
        "password": SHORT_PASSWORD,
    }

    assert module["_blind_scrub_arg_values"](args) == list(blind_scrub_arg_values(args))


# --- artifact runs report it too -----------------------------------------------


@pytest.mark.asyncio
async def test_an_artifact_run_names_exempt_arguments(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from tests._macro_artifact_fixtures import _CapturingSession, _reload, restore_reloaded_defaults

    try:
        storage, macro_artifacts = _reload(monkeypatch, tmp_path)
        storage.write_macro(
            name="greet",
            macro={"name": "greet", "parameters": ["user"], "actions": [{"action": "click", "selector": "#go"}]},
        )

        async def fake_run_macro(
            *, session: Any, name: str, args: Any, slowmo_ms: Any = None, **_private: Any
        ) -> dict[str, Any]:
            raise RuntimeError("replay failed before reporting")

        monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)
        result = await macro_artifacts.run_macro_artifact(
            _CapturingSession(tmp_path), "greet", {"user": "admin"}, capture=False, verify=False
        )
    finally:
        restore_reloaded_defaults()

    assert result["scrub_exempt_args"] == [{"macro": "greet", "path": "user", "tier": "contextual", "reason": "common"}]
