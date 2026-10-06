# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Macro execution: what a run reports, pushes to the page, loads, and classifies in a called macro."""

from __future__ import annotations

import asyncio
import copy
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog

from octowright.macros import execution
from octowright.macros.privacy_ledger import PrivacyLedger, admit_redacted_input
from tests.test_macro_credential_fill_origin import SECRET, _session

pytestmark = pytest.mark.anyio

LAUNCH = "https://app.example.test/"


def _install(monkeypatch: pytest.MonkeyPatch, macros: dict[str, dict[str, Any]]) -> list[str]:
    loads: list[str] = []

    def _load(name: str) -> dict[str, Any]:
        loads.append(name)
        return copy.deepcopy({"name": name, **macros[name]})

    monkeypatch.setattr(execution, "load_macro", _load)
    return loads


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"action": "macro_call", "name": name, "args": args}


async def test_a_caller_credential_keeps_the_callee_declaration_from_unmarking_it(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    _install(
        monkeypatch,
        {
            "outer": {"actions": [_call("child", note="{{password}}")]},
            "child": {"parameter_specs": {"note": {"sensitive": False}}, "actions": []},
        },
    )

    result = await execution.run_macro(session, "outer", {"password": SECRET})

    assert result["warnings"] == [
        "macro 'child': parameter_specs marks 'note' not sensitive, but its value was passed as a "
        'credential ({"credential": ...}), so it stays credential-tier'
    ]


async def test_each_macro_of_a_nested_run_is_loaded_once(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    loads = _install(
        monkeypatch,
        {
            "outer": {"actions": [_call("middle"), _call("middle")]},
            "middle": {"actions": [_call("leaf"), {"action": "fill", "selector": "#q", "value": "x"}]},
            "leaf": {"actions": [{"action": "fill", "selector": "#r", "value": "y"}]},
        },
    )

    await execution.run_macro(session, "outer", {})

    assert sorted(loads) == ["leaf", "middle", "outer"]


async def test_the_pill_text_names_the_chain_and_never_a_session_value(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    admit_redacted_input(session, "typed-pass9")
    _install(
        monkeypatch,
        {
            "outer": {"actions": [_call("child")]},
            "child": {"actions": [{"action": "click", "selector": "#typed-pass9 , .x typed-pass9"}]},
        },
    )
    session.click = AsyncMock()  # type: ignore[method-assign]

    await execution.run_macro(session, "outer", {})

    texts = [
        call.args[1].get("text")
        for call in session.page.evaluate.await_args_list
        if call.args and call.args[0] == execution._STATUS_PUSH_JS
    ]
    step = [text for text in texts if text and "click" in text]
    assert step, texts
    assert all("typed-pass9" not in text for text in texts if text)
    assert step[0].startswith("outer > child | ")


async def test_a_nested_call_outside_a_macro_context_is_refused() -> None:
    with pytest.raises(RuntimeError) as excinfo:
        await execution._dispatch_nested_call(
            object(),  # type: ignore[arg-type]
            _call("child"),
            invocation_stack=None,
            max_depth=3,
            slowmo_ms=0,
            run_ledger=None,
            macros=execution.RunMacros(lambda name: {}),
        )

    assert str(excinfo.value) == "macro_call can only execute in a macro context with an invocation stack"


async def test_elapsed_seconds_are_rounded_to_milliseconds(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    _install(monkeypatch, {"m": {"actions": [{"action": "fill", "selector": "#q", "value": "x"}]}})

    async def _slow_fill(**kwargs: Any) -> None:
        await asyncio.sleep(0.0123)

    session.fill = AsyncMock(side_effect=_slow_fill)  # type: ignore[method-assign]

    result = await execution.run_macro(session, "m", {})

    assert result["elapsed_s"] > 0
    assert round(result["elapsed_s"], 3) == result["elapsed_s"]


async def test_a_sequence_credential_typed_offsite_is_recorded_in_warn_mode(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS", "warn")
    session = _session(tmp_path, launch=LAUNCH, current="https://evil.example/login")
    _install(monkeypatch, {"m": {"actions": [{"action": "fill", "selector": "#q", "value": "{{note}}"}]}})

    result = await execution.run_macro(session, "m", {"note": SECRET}, credential_args=frozenset({"note"}))

    assert result["credential_fill_offsite"] == [{"step": 0, "action": "fill", "origin": "https://evil.example"}]


def test_the_label_overflow_count_counts_each_collapse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execution, "METRICS_MACRO_LABEL_CAP", 1)
    execution.reset_macro_label_seen()

    assert execution._macro_label("a") == "a"
    assert execution._macro_label("a") == "a"
    assert execution._macro_label("b") == "(overflow)"
    assert execution._macro_label("c") == "(overflow)"
    assert execution._MACRO_LABEL_OVERFLOW_COUNT == 2

    assert execution.reset_macro_label_seen() == 1
    assert execution._MACRO_LABEL_OVERFLOW_COUNT == 0


class _PillSession:
    instance_id = "pill"

    def __init__(self, page: Any) -> None:
        self.page = page

    def operation(self, name: str) -> Any:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _lease():
            yield

        return _lease()


async def test_the_pill_push_sends_its_payload_to_the_page() -> None:
    page = AsyncMock()

    await execution._push_status(_PillSession(page), text="m | starting", start=True)  # type: ignore[arg-type]

    page.evaluate.assert_awaited_once_with(
        execution._STATUS_PUSH_JS, {"visible": True, "text": "m | starting", "start": True}
    )


def test_repair_apply_rewrites_and_saves_through_the_module_store(monkeypatch: pytest.MonkeyPatch) -> None:
    macro = {"name": "demo", "actions": [{"action": "click", "selector": "#submit", "label": "Save"}]}
    written: list[tuple[str, dict[str, Any]]] = []

    def _write(*, name: str, macro: dict[str, Any]) -> str:
        written.append((name, copy.deepcopy(macro)))
        return f"/tmp/{name}.json"

    monkeypatch.setattr(execution, "load_macro", lambda name: copy.deepcopy(macro))
    monkeypatch.setattr(execution, "write_macro", _write)

    result = execution.repair_apply("demo", 0)

    assert result["applied"] is True
    assert result["macro"] == "demo"
    assert result["action_index"] == 0
    assert result["replacement_action"] == {"action": "click_by", "label": "Save"}
    assert written == [("demo", {"name": "demo", "actions": [{"action": "click_by", "label": "Save"}]})]


async def test_page_code_is_refused_up_front_for_a_credential_passed_by_origin(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    _install(
        monkeypatch,
        {
            "m": {
                "actions": [
                    {"action": "fill", "selector": "#q", "value": "{{note}}"},
                    {"action": "evaluate", "expression": "1"},
                ]
            }
        },
    )

    with pytest.raises((RuntimeError, ValueError), match=r"runs page code .* types credential arg \{\{note\}\}"):
        await execution.run_macro(session, "m", {"note": SECRET}, credential_args=frozenset({"note"}))

    session.fill.assert_not_awaited()


async def test_a_call_inside_a_conditional_reuses_the_runs_macros(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    tried = {"action": "try", "actions": [_call("leaf")]}
    loads = _install(
        monkeypatch,
        {
            "outer": {"actions": [tried, copy.deepcopy(tried)]},
            "leaf": {"actions": [{"action": "fill", "selector": "#r", "value": "y"}]},
        },
    )

    await execution.run_macro(session, "outer", {})

    assert sorted(loads) == ["leaf", "outer"]


@pytest.mark.parametrize("depth", ["top", "nested"])
async def test_a_suppressed_call_is_logged_with_the_callees_classification(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, depth: str
) -> None:
    caplog.set_level(logging.DEBUG)
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    session.fill.side_effect = TimeoutError("fill timed out")
    guarded = {"action": "try", "actions": [_call("leaf", note="literal-s3cret-x")]}
    macros: dict[str, dict[str, Any]] = {
        "leaf": {
            "parameter_specs": {"note": {"sensitive": True}},
            "actions": [{"action": "fill", "selector": "#r", "value": "{{note}}"}],
        },
    }
    if depth == "top":
        macros["outer"] = {"actions": [guarded]}
    else:
        macros["outer"] = {"actions": [_call("mid")]}
        macros["mid"] = {"actions": [guarded]}
    _install(monkeypatch, macros)

    await execution.run_macro(session, "outer", {})

    assert "suppress" in caplog.text, caplog.text
    assert "literal-s3cret-x" not in caplog.text


class _Ctx:
    def __init__(self) -> None:
        self.reports: list[tuple[float, float, str | None]] = []

    async def report_progress(self, progress: float, *, total: float, message: str | None) -> None:
        self.reports.append((progress, total, message))


async def test_a_sequence_passes_slowmo_and_progress_to_each_step(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    _install(monkeypatch, {"a": {"actions": [{"action": "fill", "selector": "#q", "value": "x"}]}})
    ctx = _Ctx()

    result = await execution.run_sequence(session=session, names=["a", "a"], slowmo_ms=7, ctx=ctx)

    assert [step["slowmo_ms"] for step in result["steps"]] == [7, 7]
    assert ctx.reports == [(1, 1, "fill"), (1, 1, "fill")]


async def test_a_macro_without_actions_runs_nothing(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name})

    result = await execution.run_macro(session, "empty", {})

    assert (result["executed"], result["skipped"]) == (0, 0)


async def test_the_pill_text_is_scrubbed_of_the_run_ledger_it_is_handed(tmp_path: Any) -> None:
    """The run's own values, not only the session's: the text is handed to page JavaScript."""
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    session.click = AsyncMock()  # type: ignore[method-assign]

    counts = await execution._dispatch_one(
        session,
        {"action": "click", "selector": "#acct-Zq9wv7Lm"},
        invocation_stack=["m"],
        run_ledger=PrivacyLedger(["Zq9wv7Lm"]),
        macros=execution.RunMacros(lambda name: {}),
    )

    assert counts == (1, 0)
    session.click.assert_awaited_once_with(selector="#acct-Zq9wv7Lm")
    session.page.evaluate.assert_awaited_once_with(
        execution._STATUS_PUSH_JS, {"visible": True, "text": "m | click selector=#acct-<redacted>"}
    )


async def test_the_run_log_line_reports_elapsed_seconds_rounded_to_milliseconds(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch=LAUNCH, current=LAUNCH)
    monkeypatch.setattr(execution, "time", SimpleNamespace(monotonic=lambda: 100.1234567))

    with structlog.testing.capture_logs() as logs:
        elapsed = await execution._finish_macro_run(
            session, name="m", completed_ok=True, macro_started=100.0, executed=2, skipped=1, resolved_slowmo=0
        )

    assert elapsed == pytest.approx(0.1234567)
    assert [entry for entry in logs if entry["event"] == "octowright.macro.run"] == [
        {
            "event": "octowright.macro.run",
            "name": "m",
            "instance_id": "test",
            "executed": 2,
            "skipped": 1,
            "slowmo_ms": 0,
            "status": "ok",
            "elapsed_s": 0.123,
            "log_level": "info",
        }
    ]
