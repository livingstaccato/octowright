# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A macro declares which parameters are sensitive: ``parameter_specs`` (#248, Part B).

Classification by name alone misses ``display`` holding a national id, and it
cannot let ``username`` -- which the application's header draws on every page
-- be shown, so a redacted screenshot of any signed-in page was refused
(the field case on #248). ``sensitive: true`` makes a parameter
credential-tier; ``sensitive: false`` unmarks an identity/contextual name, with
a floor it cannot go below: credential-like names, credential-origin values and
``expect_no_text`` texts stay credentials, and an ignored unmark is reported.
"""

from __future__ import annotations

import contextlib
import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.macros import execution, privacy
from octowright.macros.parameter_specs import declared_sensitivity, resolve_macro_privacy
from tests._macro_artifact_fixtures import _FakeSession, _reload, restore_reloaded_defaults

DISPLAY = "Natl-Id-7Q4-Display"  # pragma: allowlist secret
USERNAME = "admin-header-user"
PW_FIXTURE = "Fixture-Not-A-Real-Secret-Floor"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _restore() -> Any:
    yield
    restore_reloaded_defaults()


def _variants(value: str) -> tuple[str, ...]:
    return (value, value.lower(), value.upper(), json.dumps(value)[1:-1])


def _absent(value: str, text: str) -> bool:
    return not any(variant in text for variant in _variants(value))


def _macro(specs: Any, actions: list[dict[str, Any]] | None = None, name: str = "m") -> dict[str, Any]:
    return {
        "name": name,
        "parameters": ["display", "username", "password"],
        "parameter_specs": specs,
        "actions": actions if actions is not None else [{"action": "click", "selector": "#go"}],
    }


# -- resolution ------------------------------------------------------------------


def test_sensitive_true_makes_an_unrecognised_name_credential_tier() -> None:
    resolved = resolve_macro_privacy(_macro({"display": {"sensitive": True}}))
    args = {"display": DISPLAY, "note": "plain"}

    assert "display" in resolved.credential_args
    assert resolved.privacy.blind_scrub(args) == (DISPLAY,)
    assert resolved.privacy.redact(args) == {"display": privacy.REDACTED, "note": "plain"}
    assert resolved.warnings == ()


def test_sensitive_false_unmarks_an_identity_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(privacy.BLIND_SCRUB_POLICY_ENV, "all")
    resolved = resolve_macro_privacy(_macro({"username": {"sensitive": False}}))
    args = {"username": USERNAME}

    assert resolved.privacy.classified(args) == ()
    assert resolved.privacy.blind_scrub(args) == ()
    assert resolved.privacy.redact(args) == {"username": USERNAME}
    assert resolved.warnings == ()


def test_without_a_spec_the_identity_name_is_still_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(privacy.BLIND_SCRUB_POLICY_ENV, "all")
    resolved = resolve_macro_privacy(_macro({}))
    assert resolved.privacy.redact({"username": USERNAME}) == {"username": privacy.REDACTED}


@pytest.mark.parametrize("name", ["password", "api_key", "authToken", "PASSWORDS"])
def test_a_credential_like_name_cannot_be_declared_public(name: str) -> None:
    resolved = resolve_macro_privacy(_macro({name: {"sensitive": False}}))
    args = {name: PW_FIXTURE}

    assert resolved.privacy.blind_scrub(args) == (PW_FIXTURE,)
    assert resolved.privacy.redact(args) == {name: privacy.REDACTED}
    (warning,) = resolved.warnings
    assert repr(name) in warning and "reads as a credential" in warning
    assert _absent(PW_FIXTURE, warning)


def test_a_credential_origin_wins_over_a_public_declaration() -> None:
    """The field case: ``username={"credential": "username"}`` stays credential-tier."""
    resolved = resolve_macro_privacy(
        _macro({"username": {"sensitive": False}}), credential_args=frozenset({"username"})
    )
    assert resolved.privacy.blind_scrub({"username": USERNAME}) == (USERNAME,)
    (warning,) = resolved.warnings
    assert "passed as a credential" in warning


def test_the_expect_no_text_text_cannot_be_declared_public() -> None:
    actions = [{"action": "expect_no_text", "selector": "body", "text": "{{display}}"}]
    resolved = resolve_macro_privacy(_macro({"display": {"sensitive": False}}, actions))
    assert resolved.privacy.redact({"display": DISPLAY}) == {"display": privacy.REDACTED}
    (warning,) = resolved.warnings
    assert "expect_no_text" in warning


def test_a_key_nested_under_a_public_parameter_keeps_its_own_name() -> None:
    resolved = resolve_macro_privacy(_macro({"username": {"sensitive": False}}))
    args = {"username": {"login": USERNAME, "password": PW_FIXTURE}}

    assert resolved.privacy.blind_scrub(args) == (PW_FIXTURE,)
    assert resolved.privacy.redact(args) == {"username": {"login": USERNAME, "password": privacy.REDACTED}}


def test_sensitivity_inherits_down_a_declared_branch() -> None:
    resolved = resolve_macro_privacy(_macro({"display": {"sensitive": True}}))
    args = {"display": {"line1": DISPLAY, "parts": ["Part-One-Value"]}}
    assert set(resolved.privacy.blind_scrub(args)) == {DISPLAY, "Part-One-Value"}


@pytest.mark.parametrize(
    ("specs", "fragment"),
    [
        (["display"], "must be an object mapping"),
        ({"display": True}, "must be an object such as"),
        ({"display": {"sensitive": "true"}}, "must be true or false"),
        ({"display": {"sensitive": 1}}, "must be true or false"),
    ],
)
def test_a_malformed_spec_falls_back_to_the_name_heuristic_and_says_so(specs: Any, fragment: str) -> None:
    resolved = resolve_macro_privacy(_macro(specs))
    args = {"display": DISPLAY, "password": PW_FIXTURE}

    assert resolved.privacy.blind_scrub(args) == (PW_FIXTURE,)
    (warning,) = resolved.warnings
    assert fragment in warning
    assert warning.startswith("macro 'm': ")


def test_only_top_level_names_are_declared() -> None:
    declared = declared_sensitivity(_macro({"profile.note": {"sensitive": True}, "x": {}}))
    resolved = resolve_macro_privacy(_macro({"profile.note": {"sensitive": True}}))
    args = {"profile": {"note": "Nested-Not-Declared"}}

    assert declared.sensitive == frozenset({"profile.note"})
    assert resolved.privacy.blind_scrub(args) == ()


# -- a run -----------------------------------------------------------------------


def _session() -> MagicMock:
    session = MagicMock()
    session.instance_id = "specs-1"
    session.kind = "chromium"
    session.durable_text_scrubber = None
    session.diagnostic_bundle = AsyncMock(return_value={})
    session.recorder = MagicMock()
    return session


def _install(monkeypatch: pytest.MonkeyPatch, macros: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    dispatched: list[dict[str, Any]] = []

    async def record(_session: Any, action: Any, *_args: Any, **_kwargs: Any) -> tuple[int, int]:
        dispatched.append(action)
        if action.get("selector") == "#fail":
            raise RuntimeError("no element #fail")
        return 1, 0

    monkeypatch.setattr(execution, "load_macro", lambda name: macros[name])
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "dispatch_plain_action", record)
    monkeypatch.setattr(execution, "credential_fill_guard", lambda *_args: contextlib.nullcontext())
    return dispatched


@pytest.mark.asyncio
async def test_a_run_redacts_and_scrubs_a_declared_sensitive_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {"m": _macro({"display": {"sensitive": True}})})
    session = _session()

    result = await execution.run_macro(session, "m", {"display": DISPLAY, "username": USERNAME})

    assert result["args_used"]["display"] == "<redacted>"
    assert DISPLAY in privacy.session_privacy_ledger(session).values
    assert "warnings" not in result


@pytest.mark.asyncio
async def test_a_declared_sensitive_argument_is_held_to_the_credential_sink_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = [{"action": "navigate", "url": "https://app.example.test/?d={{display}}"}]
    dispatched = _install(monkeypatch, {"m": _macro({"display": {"sensitive": True}}, actions)})

    with pytest.raises(Exception) as caught:
        await execution.run_macro(_session(), "m", {"display": DISPLAY})

    assert dispatched == []
    assert "display" in str(caught.value)
    assert _absent(DISPLAY, str(caught.value))


@pytest.mark.asyncio
async def test_a_public_declaration_shows_the_identity_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(privacy.BLIND_SCRUB_POLICY_ENV, "all")
    _install(monkeypatch, {"m": _macro({"username": {"sensitive": False}})})
    session = _session()

    result = await execution.run_macro(session, "m", {"username": USERNAME})

    assert result["args_used"] == {"username": USERNAME}
    assert USERNAME not in privacy.session_privacy_ledger(session).values


@pytest.mark.asyncio
async def test_an_ignored_unmark_is_reported_in_the_run_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, {"m": _macro({"password": {"sensitive": False}})})

    result = await execution.run_macro(_session(), "m", {"password": PW_FIXTURE})

    assert result["args_used"]["password"] == "<redacted>"
    (warning,) = result["warnings"]
    assert "'password'" in warning
    assert _absent(PW_FIXTURE, json.dumps(result))


@pytest.mark.asyncio
async def test_a_caller_credential_stays_credential_tier_under_a_public_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner's ``{"credential": ...}`` origin wins over the macro's declaration."""
    _install(monkeypatch, {"m": _macro({"username": {"sensitive": False}})})
    session = _session()

    result = await execution.run_macro(session, "m", {"username": USERNAME}, credential_args=frozenset({"username"}))

    assert result["args_used"]["username"] == "<redacted>"
    assert USERNAME in privacy.session_privacy_ledger(session).values
    assert any("passed as a credential" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_a_called_macro_declares_its_own_sensitive_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    outer = {
        "name": "outer",
        "parameters": [],
        "actions": [{"action": "macro_call", "name": "inner", "args": {"display": DISPLAY}}],
    }
    inner = _macro({"display": {"sensitive": True}}, [{"action": "click", "selector": "#x"}], name="inner")
    _install(monkeypatch, {"outer": outer, "inner": inner})
    session = _session()

    await execution.run_macro(session, "outer", {})

    assert DISPLAY in privacy.session_privacy_ledger(session).values


@pytest.mark.asyncio
async def test_a_called_macros_sensitive_parameter_is_held_to_the_sink_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    outer = {
        "name": "outer",
        "parameters": [],
        "actions": [{"action": "macro_call", "name": "inner", "args": {"display": DISPLAY}}],
    }
    inner = _macro(
        {"display": {"sensitive": True}},
        [{"action": "navigate", "url": "https://app.example.test/?d={{display}}"}],
        name="inner",
    )
    dispatched = _install(monkeypatch, {"outer": outer, "inner": inner})

    with pytest.raises(Exception) as caught:
        await execution.run_macro(_session(), "outer", {})

    assert [action.get("action") for action in dispatched] == []
    assert _absent(DISPLAY, str(caught.value))


@pytest.mark.asyncio
async def test_a_called_macros_ignored_unmark_reaches_the_run_result(monkeypatch: pytest.MonkeyPatch) -> None:
    outer = {
        "name": "outer",
        "parameters": [],
        "actions": [{"action": "macro_call", "name": "inner", "args": {"password": PW_FIXTURE}}],
    }
    inner = _macro({"password": {"sensitive": False}}, name="inner")
    _install(monkeypatch, {"outer": outer, "inner": inner})

    result = await execution.run_macro(_session(), "outer", {})

    (warning,) = result["warnings"]
    assert warning.startswith("macro 'inner': ")


# -- a sequence --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sequence_step_carries_its_warnings_and_its_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _macro({"password": {"sensitive": False}}, name="good")
    bad = _macro({"display": {"sensitive": True}}, [{"action": "click", "selector": "#fail"}], name="bad")
    _install(monkeypatch, {"good": good, "bad": bad})

    result = await execution.run_sequence(
        session=_session(),
        names=["good", "bad"],
        args_list=[{"password": PW_FIXTURE}, {"display": DISPLAY}],
    )

    first, second = result["steps"]
    assert first["ok"] is True and len(first["warnings"]) == 1
    assert second["ok"] is False
    assert second["args_used"] == {"display": "<redacted>"}
    assert _absent(DISPLAY, json.dumps(result)) and _absent(PW_FIXTURE, json.dumps(result))


# -- an artifact run -------------------------------------------------------------


def _tree_text(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in root.rglob("*") if path.is_file())


@pytest.mark.asyncio
async def test_an_artifact_run_honours_the_specs_and_reports_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(name="m", macro=_macro({"display": {"sensitive": True}, "password": {"sensitive": False}}))
    _install(monkeypatch, {})
    monkeypatch.setattr(execution, "load_macro", storage.load_macro)

    result = await macro_artifacts.run_macro_artifact(
        _FakeSession(tmp_path), "m", {"display": DISPLAY, "password": PW_FIXTURE}, capture=False, verify=False
    )

    assert result["ok"] is True
    (warning,) = result["warnings"]
    assert "'password'" in warning
    on_disk = _tree_text(tmp_path / "recordings")
    assert _absent(DISPLAY, on_disk) and _absent(PW_FIXTURE, on_disk)


# -- the exported CLI --------------------------------------------------------------


def _exported(monkeypatch: pytest.MonkeyPatch, macro: dict[str, Any]) -> dict[str, Any]:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    module: dict[str, Any] = {"__name__": "specs_cli"}
    exec(compile(render_macro_cli(name="m", macro=macro), "<specs-cli>", "exec"), module)
    return module


def test_the_exported_cli_resolves_the_same_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(privacy.BLIND_SCRUB_POLICY_ENV, "all")
    macro = _macro({"display": {"sensitive": True}, "username": {"sensitive": False}, "password": {"sensitive": False}})
    module = _exported(monkeypatch, macro)
    args = {"display": DISPLAY, "username": USERNAME, "password": PW_FIXTURE}
    live = resolve_macro_privacy(macro).privacy

    assert module["_redact_args"](args) == live.redact(args)
    assert sorted(module["_blind_scrub_arg_values"](args)) == sorted(live.blind_scrub(args))
    assert module["_is_credential_arg"]("display") is True
    assert module["_is_credential_arg"]("username") is False
    assert module["_ARG_PRIVACY_CLASSIFIER_VERSION"] == privacy.ARG_PRIVACY_CLASSIFIER_VERSION == 7


# -- lint --------------------------------------------------------------------------


def _codes(macro: dict[str, Any]) -> list[str]:
    from octowright.macros.lint import lint_macro

    return [issue.code for issue in lint_macro(macro) if issue.action_index is None]


def test_lint_warns_on_an_ignored_unmark_a_malformed_spec_and_an_unknown_name() -> None:
    from octowright.macros.lint import lint_macro

    macro = _macro({"password": {"sensitive": False}, "display": {"sensitive": "yes"}, "ghost": {"sensitive": True}})
    issues = [issue for issue in lint_macro(macro) if issue.action_index is None]

    assert sorted(issue.code for issue in issues) == [
        "bad_parameter_specs",
        "ignored_public_declaration",
        "unknown_parameter_spec",
    ]
    assert {issue.severity for issue in issues} == {"warning"}


def test_lint_is_quiet_about_a_well_formed_spec() -> None:
    assert _codes(_macro({"display": {"sensitive": True}, "username": {"sensitive": False}})) == []


@pytest.mark.asyncio
async def test_an_artifact_run_resolves_its_view_once_and_the_replay_uses_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from octowright.macros import parameter_specs

    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(name="m", macro=_macro({"display": {"sensitive": True}}))
    _install(monkeypatch, {})
    monkeypatch.setattr(execution, "load_macro", storage.load_macro)
    calls: list[str] = []
    real = parameter_specs.resolve_macro_privacy

    def counting(macro: Any, **kwargs: Any) -> Any:
        calls.append(str(macro.get("name")))
        return real(macro, **kwargs)

    monkeypatch.setattr(macro_artifacts, "resolve_macro_privacy", counting)
    monkeypatch.setattr(execution, "resolve_macro_privacy", counting)

    await macro_artifacts.run_macro_artifact(
        _FakeSession(tmp_path), "m", {"display": DISPLAY}, capture=False, verify=False
    )

    assert calls == ["m"]


@pytest.mark.asyncio
async def test_a_sequence_step_with_a_malformed_spec_runs_on_the_name_heuristic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a spec-shape problem falls back to heuristic-only; the step still runs."""
    _install(monkeypatch, {"m": _macro(["display"])})

    result = await execution.run_sequence(
        session=_session(), names=["m"], args_list=[{"password": PW_FIXTURE, "display": DISPLAY}]
    )

    (step,) = result["steps"]
    assert step["ok"] is True
    assert step["args_used"] == {"password": "<redacted>", "display": DISPLAY}
    assert "must be an object mapping" in step["warnings"][0]


@pytest.mark.asyncio
async def test_a_sequence_step_whose_macro_is_not_valid_json_is_a_recorded_failed_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, {"good": _macro({}, name="good")})

    def load(name: str) -> dict[str, Any]:
        if name == "broken":
            raise json.JSONDecodeError("Expecting value", "", 0)
        return _macro({}, name="good")

    monkeypatch.setattr(execution, "load_macro", load)

    result = await execution.run_sequence(
        session=_session(), names=["good", "broken"], args_list=[{}, {"password": PW_FIXTURE}], stop_on_failure=False
    )

    assert [step["ok"] for step in result["steps"]] == [True, False]
    assert result["steps"][1]["args_used"] == {"password": "<redacted>"}
