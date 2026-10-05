# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A macro's privacy is resolved once, from the dict that executes (#248, "Resolve once").

`run_macro_artifact` loaded the macro and built its own privacy view, then
called `run_macro`, which loaded the file again and admitted the same arguments
into a second run ledger. So the artifact's redaction could describe a
different file than the one that ran, and -- the part that leaked -- the
values a nested ``macro_call`` admitted during the replay lived only in
`run_macro`'s ledger: the artifact's "after" screenshot, summary and run bundle
scrubbed against the pre-run tuple, which never held them.

`run_sequence` had the same shape in miniature: any exception while
classifying a failed step's macro fell back to name-only redaction, which is
right when the macro never loaded (nothing was substituted) and wrong when it
did.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution, parameter_specs, privacy
from tests._macro_artifact_fixtures import _CapturingSession, _reload, restore_reloaded_defaults

NESTED = "nested-credential-9f3k"  # pragma: allowlist secret
TOP = "top-credential-2m7q"  # pragma: allowlist secret
VISIBLE = "plain-note-value"


@pytest.fixture(autouse=True)
def _restore() -> Any:
    yield
    restore_reloaded_defaults()


def _quiet_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Dispatch that records the action instead of driving a page."""
    dispatched: list[dict[str, Any]] = []

    async def record(_session: Any, action: Any, *_args: Any, **_kwargs: Any) -> tuple[int, int]:
        dispatched.append(action)
        return 1, 0

    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "dispatch_plain_action", record)
    monkeypatch.setattr(execution, "credential_fill_guard", lambda *_args: contextlib.nullcontext())
    return dispatched


def _write_nested(storage: Any) -> None:
    """``outer`` takes no arguments; the credential arrives in its nested call."""
    storage.write_macro(
        name="outer",
        macro={
            "name": "outer",
            "parameters": [],
            "actions": [{"action": "macro_call", "name": "inner", "args": {"password": NESTED}}],
        },
    )
    storage.write_macro(
        name="inner",
        macro={
            "name": "inner",
            "parameters": ["password"],
            "actions": [{"action": "fill", "selector": "#pw", "value": "{{password}}"}],
        },
    )


def _tree_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*")
        if path.is_file() and path.suffix != ".png"
    )


# -- the artifact scrubs what the replay admitted (issue 2) ----------------------


@pytest.mark.asyncio
async def test_the_after_screenshot_hides_a_value_a_nested_call_admitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_nested(storage)
    dispatched = _quiet_dispatch(monkeypatch)
    seen: dict[str, tuple[str, ...]] = {}
    real_capture = macro_artifacts._capture_screenshot

    async def spy(**kwargs: Any) -> None:
        seen[kwargs["label"]] = tuple(kwargs.get("sensitive_values", ()))
        await real_capture(**kwargs)

    monkeypatch.setattr(macro_artifacts, "_capture_screenshot", spy)
    session = _CapturingSession(tmp_path)

    result = await macro_artifacts.run_macro_artifact(session, "outer", {}, capture=True, verify=False)

    assert result["ok"] is True
    assert dispatched and dispatched[0]["value"] == NESTED
    assert seen["before"] == ()
    assert NESTED in seen["after"]
    # The "before" shot is the only raw one: the "after" one is redacted or suppressed.
    assert [shot.name for shot in session.shots] == ["before.png"]


@pytest.mark.asyncio
async def test_the_summary_and_bundle_scrub_a_value_a_nested_call_admitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_nested(storage)
    _quiet_dispatch(monkeypatch)
    session = _CapturingSession(tmp_path)

    result = await macro_artifacts.run_macro_artifact(
        session, "outer", {}, capture=False, verify=False, notes=f"typed {NESTED} into #pw"
    )

    assert NESTED not in result["summary"]
    assert NESTED not in _tree_text(tmp_path / "recordings")


@pytest.mark.asyncio
async def test_a_run_scoped_nested_value_stays_scrubbed_until_the_artifact_run_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An identity value is admitted for its run only (#247); the "run" an
    # artifact's after screenshot belongs to is the artifact run.
    monkeypatch.setenv("OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY", "all")
    email = "someone.nested@example.test"
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(
        name="outer",
        macro={
            "name": "outer",
            "parameters": [],
            "actions": [{"action": "macro_call", "name": "inner", "args": {"email": email}}],
        },
    )
    storage.write_macro(
        name="inner",
        macro={
            "name": "inner",
            "parameters": ["email"],
            "actions": [{"action": "fill", "selector": "#e", "value": "{{email}}"}],
        },
    )
    _quiet_dispatch(monkeypatch)
    session = _CapturingSession(tmp_path)
    during: list[tuple[str, ...]] = []
    real_capture = macro_artifacts._capture_screenshot

    async def spy(**kwargs: Any) -> None:
        if kwargs["label"] == "after":
            during.append(privacy.session_privacy_ledger(session).values)
        await real_capture(**kwargs)

    monkeypatch.setattr(macro_artifacts, "_capture_screenshot", spy)

    await macro_artifacts.run_macro_artifact(session, "outer", {}, capture=True, verify=False)

    assert email in during[0]
    assert email not in privacy.session_privacy_ledger(session).values


# -- one load, one ledger (issues 1 and 3) ---------------------------------------


@pytest.mark.asyncio
async def test_an_artifact_run_loads_each_macro_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_nested(storage)
    _quiet_dispatch(monkeypatch)
    loads: list[str] = []
    real_load = storage.load_macro

    def counting(name: str) -> dict[str, Any]:
        loads.append(name)
        return real_load(name)

    monkeypatch.setattr(macro_artifacts, "load_macro", counting)
    monkeypatch.setattr(execution, "load_macro", counting)

    await macro_artifacts.run_macro_artifact(_CapturingSession(tmp_path), "outer", {}, capture=False, verify=False)

    assert sorted(loads) == ["inner", "outer"]


@pytest.mark.asyncio
async def test_the_artifact_redacts_from_the_dict_that_executed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second read seeing an edited file cannot split the view from the run."""
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    as_assertion = {"name": "m", "parameters": ["card"], "actions": [{"action": "expect_no_text", "text": "{{card}}"}]}
    as_plain = {"name": "m", "parameters": ["card"], "actions": [{"action": "click", "selector": "#{{card}}"}]}
    versions = iter([as_assertion, as_plain])
    dispatched = _quiet_dispatch(monkeypatch)

    def edited_between_reads(_name: str) -> dict[str, Any]:
        return next(versions)

    monkeypatch.setattr(macro_artifacts, "load_macro", edited_between_reads)
    monkeypatch.setattr(execution, "load_macro", edited_between_reads)

    result = await macro_artifacts.run_macro_artifact(
        _CapturingSession(tmp_path), "m", {"card": "4111"}, capture=False, verify=False
    )

    run_result = json.loads(Path(result["paths"]["result"]).read_text(encoding="utf-8"))
    # The redaction says "assertion arg", so the run must have been the assertion version.
    assert run_result["args_used"]["card"] != "4111"
    assert all(action.get("action") != "click" for action in dispatched)


@pytest.mark.asyncio
async def test_run_macro_uses_a_supplied_ledger_without_admitting_twice_or_closing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _quiet_dispatch(monkeypatch)
    macro = {"actions": [{"action": "fill", "selector": "#pw", "value": "{{password}}"}]}
    monkeypatch.setattr(execution, "load_macro", lambda _name: macro)
    session = MagicMock()
    session.instance_id = "instance-safe"
    session.kind = "chromium"
    session.durable_text_scrubber = None
    session.recorder = None
    macros = execution.RunMacros(execution.load_macro)
    admissions: list[str] = []

    with privacy.run_privacy_ledger(session) as ledger:
        real_admit = ledger.admit

        def counting_admit(name: str, admission: Any) -> None:
            admissions.append(name)
            real_admit(name, admission)

        ledger.admit = counting_admit  # type: ignore[method-assign]
        ledger.admit("login", privacy.MacroArgPrivacy.for_macro(macro["actions"]).admission({"password": TOP}))
        result = await execution.run_macro(session, "login", {"password": TOP}, _macros=macros, _run_ledger=ledger)
        still_open = ledger._scope is not None

    assert admissions == ["login"]
    assert still_open
    assert result["args_used"]["password"] != TOP


# -- run_sequence: a step that loaded fails closed -------------------------------


@pytest.mark.asyncio
async def test_a_step_whose_classification_fails_redacts_every_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    _quiet_dispatch(monkeypatch)
    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": [{"action": "click", "selector": "#go"}]})

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise TypeError("an input the classifier cannot read")

    # Every view -- the run's and the failed step's -- resolves through here.
    monkeypatch.setattr(parameter_specs, "assertion_text_args", broken)
    session = MagicMock()
    session.instance_id = "instance-safe"
    session.kind = "chromium"

    result = await execution.run_sequence(session=session, names=["go"], args_list=[{"note": VISIBLE}])

    step = result["steps"][0]
    assert step["ok"] is False
    assert VISIBLE not in repr(step["args_used"])
    assert set(step["args_used"]) == {"note"}


@pytest.mark.asyncio
async def test_a_step_whose_macro_never_loaded_keeps_name_only_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pins existing behaviour: nothing was substituted, so a plain value is shown."""

    def missing(name: str) -> dict[str, Any]:
        raise FileNotFoundError(name)

    monkeypatch.setattr(execution, "load_macro", missing)
    session = MagicMock()
    session.instance_id = "instance-safe"
    session.kind = "chromium"

    result = await execution.run_sequence(
        session=session, names=["gone"], args_list=[{"note": VISIBLE, "password": TOP}]
    )

    step = result["steps"][0]
    assert step["ok"] is False
    assert step["args_used"]["note"] == VISIBLE
    assert step["args_used"]["password"] != TOP
