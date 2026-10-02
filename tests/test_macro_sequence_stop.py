# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A stopped ``run_sequence`` returns its steps instead of raising (#248).

One result shape for both ``stop_on_failure`` modes: ``{sequence, steps, ok,
stopped_at}``. A failing step -- its macro raising, or its macro file missing --
is a step result. What still raises is what means the sequence could not run at
all: malformed arguments, the operation gate, cancellation.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution as _execution
from octowright.macros.execution import run_sequence
from octowright.session.operation.gate import SessionClosedError
from tests._operation_gate_fakes import OperationAwareFake

PASSWORD = "hunter2-correct-horse"  # pragma: allowlist secret


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Session(OperationAwareFake):
    instance_id = "seq-stop"
    kind = "chromium"

    def __init__(self) -> None:
        super().__init__()
        self.page = MagicMock()
        self.page.evaluate = AsyncMock()
        self.diagnostic_bundle = AsyncMock(return_value={"url": "https://x.test/", "title": "t"})


@pytest.fixture
def session() -> _Session:
    return _Session()


@pytest.fixture
def macros(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Saved macros by name, a dispatch log, and per-selector failures."""
    saved: dict[str, list[dict[str, Any]]] = {}
    dispatched: list[dict[str, Any]] = []
    fail_on: dict[str, BaseException] = {}

    def load(name: str) -> dict[str, Any]:
        if name not in saved:
            raise FileNotFoundError(f"no macro named {name!r}")
        return {"name": name, "actions": saved[name]}

    async def dispatch(_session: Any, action: dict[str, Any], **_kwargs: Any) -> tuple[int, int]:
        dispatched.append(action)
        if action.get("selector") in fail_on:
            raise fail_on[action["selector"]]
        return (1, 0)

    monkeypatch.setattr(_execution, "load_macro", load)
    monkeypatch.setattr(_execution, "_dispatch_one", dispatch)
    monkeypatch.setattr(_execution, "_suggest_fix", AsyncMock(return_value=None))
    return {"saved": saved, "dispatched": dispatched, "fail_on": fail_on}


def _three(macros: dict[str, Any]) -> None:
    macros["saved"]["pre"] = [{"action": "click", "selector": "#pre"}]
    macros["saved"]["bad"] = [{"action": "click", "selector": "#ok"}, {"action": "click", "selector": "#bad"}]
    macros["saved"]["post"] = [{"action": "click", "selector": "#post"}]
    macros["fail_on"]["#bad"] = ValueError("no element #bad")


@pytest.mark.anyio
async def test_a_stopped_sequence_returns_the_steps_that_ran(session: _Session, macros: dict[str, Any]) -> None:
    _three(macros)
    result = await run_sequence(session=session, names=["pre", "bad", "post"])

    assert result["ok"] is False
    assert result["stopped_at"] == 1
    assert result["sequence"] == ["pre", "bad", "post"]
    assert [step["macro"] for step in result["steps"]] == ["pre", "bad"]
    assert result["steps"][0]["ok"] is True
    assert result["steps"][1]["ok"] is False
    assert "#bad" in result["steps"][1]["error"]
    # "post" never ran.
    assert {"action": "click", "selector": "#post"} not in macros["dispatched"]


@pytest.mark.anyio
async def test_the_failed_step_carries_the_structured_macro_failure(session: _Session, macros: dict[str, Any]) -> None:
    _three(macros)
    result = await run_sequence(session=session, names=["pre", "bad", "post"])

    failure = result["steps"][1]["failure"]
    assert failure["macro"] == "bad"
    assert failure["failed_at_step"] == 1
    assert failure["executed"] == 1
    assert failure["failed_action"]["selector"] == "#bad"
    assert "bundle" in failure


@pytest.mark.anyio
async def test_a_completed_sequence_reports_no_stop(session: _Session, macros: dict[str, Any]) -> None:
    macros["saved"]["a"] = [{"action": "click", "selector": "#a"}]
    result = await run_sequence(session=session, names=["a", "a"])
    assert result["ok"] is True
    assert result["stopped_at"] is None


@pytest.mark.anyio
async def test_collecting_failures_runs_every_step_and_reports_no_stop(
    session: _Session, macros: dict[str, Any]
) -> None:
    _three(macros)
    result = await run_sequence(session=session, names=["pre", "bad", "post"], stop_on_failure=False)
    assert result["ok"] is False
    assert result["stopped_at"] is None
    assert [step["ok"] for step in result["steps"]] == [True, False, True]
    assert result["steps"][1]["failure"]["failed_at_step"] == 1


@pytest.mark.anyio
async def test_a_missing_macro_is_a_failed_step_naming_it(session: _Session, macros: dict[str, Any]) -> None:
    macros["saved"]["pre"] = [{"action": "click", "selector": "#pre"}]
    macros["saved"]["post"] = [{"action": "click", "selector": "#post"}]
    result = await run_sequence(session=session, names=["pre", "nope", "post"])

    assert result["ok"] is False
    assert result["stopped_at"] == 1
    assert [step["ok"] for step in result["steps"]] == [True, False]
    assert "nope" in result["steps"][1]["error"]
    assert "failure" not in result["steps"][1]  # nothing structured to carry


@pytest.mark.anyio
async def test_a_credential_value_never_appears_in_a_stopped_result(session: _Session, macros: dict[str, Any]) -> None:
    macros["saved"]["login"] = [
        {"action": "fill", "selector": "#pw", "value": "{{password}}"},
        {"action": "click", "selector": "#submit"},
    ]
    macros["fail_on"]["#submit"] = RuntimeError(f"page said: wrong password {PASSWORD}")
    session.diagnostic_bundle = AsyncMock(return_value={"console": [{"text": f"pw={PASSWORD}"}]})

    result = await run_sequence(session=session, names=["login"], args_list=[{"password": PASSWORD}])

    assert result["stopped_at"] == 0
    assert result["steps"][0]["args_used"] == {"password": "<redacted>"}
    assert PASSWORD not in json.dumps(result, default=str)


@pytest.mark.anyio
async def test_cancellation_propagates_and_is_never_a_step(session: _Session, macros: dict[str, Any]) -> None:
    macros["saved"]["a"] = [{"action": "click", "selector": "#a"}]
    macros["fail_on"]["#a"] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await run_sequence(session=session, names=["a"])


@pytest.mark.anyio
async def test_a_gate_refusal_propagates(
    session: _Session, macros: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def closed(**_kwargs: Any) -> Any:
        raise SessionClosedError("session closed")

    macros["saved"]["a"] = []
    monkeypatch.setattr(_execution, "run_macro", closed)
    with pytest.raises(SessionClosedError):
        await run_sequence(session=session, names=["a", "a"])


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("names", "args_list"),
    [
        ("a", None),  # a bare string, not a list of names
        (["a", 3], None),
        (["a"], {"k": 1}),  # a dict, not a list of dicts
        (["a"], ["not-a-dict"]),
    ],
)
async def test_malformed_arguments_raise_before_any_step_runs(
    session: _Session, macros: dict[str, Any], names: Any, args_list: Any
) -> None:
    macros["saved"]["a"] = [{"action": "click", "selector": "#a"}]
    with pytest.raises(ValueError, match=r"names|args_list"):
        await run_sequence(session=session, names=names, args_list=args_list)
    assert macros["dispatched"] == []
