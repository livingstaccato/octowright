# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A ``{{placeholder}}`` action name is resolved before the step is classified.

The guard decided what a step was from its ``action`` field AS WRITTEN, then
expanded that field like any other. ``{"action": "{{kind}}", "value":
"{{password}}"}`` with ``kind=fill`` was therefore judged as an unknown action:
no credential marker, so no origin check, and the password was typed into
whatever page the macro had navigated to. ``"{{call}}"`` resolving to
``macro_call`` dropped the call's credential taint the same way. The name is
now expanded first and the step judged as what it will dispatch as; a
credential-tier arg is refused as an action name, since a dispatch error naming
an unknown action would carry the value.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.credential_sinks import CREDENTIAL_CALL_MARKER, CREDENTIAL_FILL_MARKER
from octowright.macros.substitution import substitute
from tests.test_macro_credential_fill_origin import SECRET, _session

pytestmark = pytest.mark.anyio

ARGS = {"password": SECRET, "kind": "fill", "call": "macro_call"}


async def _run(monkeypatch: pytest.MonkeyPatch, session: Any, macros: dict[str, list[dict[str, Any]]]) -> Any:
    from octowright.macros import execution

    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})
    return await execution.run_macro(session, "outer", dict(ARGS))


def _assert_absent(text: str) -> None:
    for spelling in (SECRET, SECRET.upper(), SECRET.lower(), SECRET.swapcase()):
        assert spelling not in text


def test_a_parameterized_action_name_is_judged_as_what_it_resolves_to() -> None:
    (step,) = substitute([{"action": "{{kind}}", "selector": "#pw", "value": "{{password}}"}], dict(ARGS))
    assert step["action"] == "fill"
    assert step[CREDENTIAL_FILL_MARKER] == ["password"]


def test_a_parameterized_nested_action_name_is_judged_too() -> None:
    (step,) = substitute(
        [{"action": "try", "actions": [{"action": "{{kind}}", "selector": "#pw", "value": "{{password}}"}]}],
        dict(ARGS),
    )
    assert step["actions"][0][CREDENTIAL_FILL_MARKER] == ["password"]


def test_a_parameterized_macro_call_keeps_its_credential_taint() -> None:
    (step,) = substitute([{"action": "{{call}}", "name": "inner", "args": {"q": "{{password}}"}}], dict(ARGS))
    assert step["action"] == "macro_call"
    assert step[CREDENTIAL_CALL_MARKER] == ["q"]


def test_a_credential_is_refused_as_an_action_name() -> None:
    with pytest.raises(ValueError, match=r"\{\{password\}\}") as caught:
        substitute([{"action": "{{password}}"}], dict(ARGS))
    _assert_absent(str(caught.value))


async def test_a_parameterized_fill_onto_a_foreign_origin_is_refused(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/login")
    with pytest.raises(RuntimeError, match=r"https://evil\.example") as caught:
        await _run(
            monkeypatch, session, {"outer": [{"action": "{{kind}}", "selector": "#pw", "value": "{{password}}"}]}
        )
    _assert_absent(str(caught.value))
    _assert_absent(repr(session.recorder.mock_calls))
    session.fill.assert_not_awaited()


async def test_a_parameterized_call_cannot_launder_a_credential_into_a_foreign_fill(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/login")
    macros = {
        "outer": [{"action": "{{call}}", "name": "inner", "args": {"q": "{{password}}"}}],
        "inner": [{"action": "fill", "selector": "#pw", "value": "{{q}}"}],
    }
    with pytest.raises(RuntimeError, match=r"https://evil\.example") as caught:
        await _run(monkeypatch, session, macros)
    _assert_absent(str(caught.value))
    _assert_absent(repr(session.recorder.mock_calls))
    session.fill.assert_not_awaited()
