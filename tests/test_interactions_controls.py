# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Public interaction settings survive launch mappings, replacement and cancellation."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.options import LaunchOptions
from octowright.browser_pool.replacement import ReplacementSource
from octowright.recorder import Recorder
from octowright.session import BrowserSession
from tests.test_interactions_controller import Context, payload


@pytest.mark.parametrize("show,record", [(False, False), (True, False), (False, True), (True, True)])
def test_options_preserve_independent_interaction_flags(show: bool, record: bool) -> None:
    options = LaunchOptions.from_external_mapping(
        {"show_interactions": show, "record_interactions": record}, source="test"
    )
    assert options.show_interactions is show
    assert options.record_interactions is record
    assert (
        LaunchOptions.from_launch_record({"show_interactions": show, "record_interactions": record}).show_interactions
        is show
    )
    assert (
        LaunchOptions.from_launch_record({"show_interactions": show, "record_interactions": record}).record_interactions
        is record
    )
    source = ReplacementSource(options=options, launch_url=None, protected=False, protected_reason="explicit")
    kwargs = source.launch_kwargs(url="https://example.test")
    assert (kwargs["show_interactions"], kwargs["record_interactions"]) == (show, record)


def test_old_options_default_off() -> None:
    assert LaunchOptions().show_interactions is False
    assert LaunchOptions.from_launch_record({}).record_interactions is False


@pytest.mark.parametrize("key", ["show_interactions", "record_interactions"])
@pytest.mark.parametrize("value", [None, "false", 0, 1])
def test_record_flags_reject_nonbool(key: str, value: Any) -> None:
    with pytest.raises(ValueError, match=key):
        LaunchOptions.from_launch_record({key: value})


def session_with_controller(tmp_path: Path) -> BrowserSession:
    from octowright.interactions import InteractionController

    context = Context()
    session = BrowserSession(
        instance_id="controls",
        kind="chromium",
        label=None,
        url="about:blank",
        browser=None,
        context=context,
        page=context.pages[0],
        recorder=Recorder(tmp_path / "controls.jsonl"),
        log_path=tmp_path / "controls.jsonl",
        launch_options=LaunchOptions(),
    )
    session.interaction_controller = InteractionController(session.log_path)
    return session


async def test_runtime_control_is_reentrant_and_bindings_do_not_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("octowright.server.browser.interactions")
    session = session_with_controller(tmp_path)
    controller = session.interaction_controller
    await controller.install(session)
    monkeypatch.setattr(module.pool, "get", lambda _: session)
    frame = session.page.main_frame
    source = {"page": session.page, "frame": frame, "context": session.context}

    async def evaluate(*args: Any) -> bool:
        # Simulate an observer calling its host binding while configure owns the gate.
        session.context.bindings["__octowrightInteractionEvent"](source, payload())
        return True

    frame.evaluate = evaluate
    try:
        async with asyncio.timeout(2):
            async with session.operation("test_composite"):
                result = await module.browser_set_interaction_visuals("controls", show=True, record=True)
        assert result["show"] is True and result["record"] is True
        assert result["applied_frames"] == 1 and result["pending_frames"] == 0
        assert session.launch_options.show_interactions is True
        assert session.launch_options.record_interactions is True
    finally:
        await controller.close()
        session.recorder.close()


async def test_runtime_cancellation_preserves_host_requested_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("octowright.server.browser.interactions")
    session = session_with_controller(tmp_path)
    controller = session.interaction_controller
    await controller.install(session)
    monkeypatch.setattr(module.pool, "get", lambda _: session)
    entered = asyncio.Event()

    async def evaluate(*_: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    session.page.main_frame.evaluate = evaluate
    task = asyncio.create_task(module.browser_set_interaction_visuals("controls", record=True))
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert controller.record is True
        assert session.launch_options.record_interactions is True
    finally:
        await controller.close()
        session.recorder.close()


@pytest.mark.parametrize("kwargs", [{}, {"show": 1}, {"record": "false"}])
async def test_runtime_control_rejects_invalid_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]
) -> None:
    module = importlib.import_module("octowright.server.browser.interactions")
    session = session_with_controller(tmp_path)
    await session.interaction_controller.install(session)
    monkeypatch.setattr(module.pool, "get", lambda _: session)
    try:
        with pytest.raises(ValueError):
            await module.browser_set_interaction_visuals("controls", **kwargs)
        assert session.launch_options == LaunchOptions()
    finally:
        await session.interaction_controller.close()
        session.recorder.close()


@pytest.mark.parametrize("key", ["show_interactions", "record_interactions"])
@pytest.mark.parametrize("value", ["false", 1, 0])
async def test_launch_mcp_refuses_nonboolean_flags(monkeypatch: pytest.MonkeyPatch, key: str, value: Any) -> None:
    from tests.test_browser_launch_mcp_validation import _call_browser_launch

    result, launch = await _call_browser_launch(monkeypatch, value, field=key)
    assert result.is_error
    launch.assert_not_awaited()


@pytest.mark.parametrize("tool", ["browser_launch", "browser_quick_launch"])
async def test_launch_tools_forward_both_flags(monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    from unittest.mock import AsyncMock

    from octowright.server.browser import lifecycle

    launch = AsyncMock(return_value={"instance_id": "flags"})
    monkeypatch.setattr(lifecycle, "_pool_launch_with_deadline", launch)
    await getattr(lifecycle, tool)(
        url="https://example.test", ephemeral=True, show_interactions=True, record_interactions=True
    )
    assert launch.call_args.kwargs["show_interactions"] is True
    assert launch.call_args.kwargs["record_interactions"] is True


def test_scenario_flags_reach_browser_options() -> None:
    from octowright.scenarios import load_yaml_scenario, resolve_launch_kwargs

    scenario = load_yaml_scenario(
        "participants:\n- persona: flags\n  kind: chromium\n  show_interactions: true\n  record_interactions: true\n",
        "flags",
    )
    kwargs = resolve_launch_kwargs(scenario.participants[0])
    assert kwargs["show_interactions"] is True
    assert kwargs["record_interactions"] is True


async def test_runtime_control_queues_before_mutating_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("octowright.server.browser.interactions")
    session = session_with_controller(tmp_path)
    await session.interaction_controller.install(session)
    monkeypatch.setattr(module.pool, "get", lambda _: session)
    try:
        async with session.operation("test_blocking_input"):
            task = asyncio.create_task(module.browser_set_interaction_visuals("controls", show=True))
            # Gate state, rather than elapsed time, proves a competing task queued.
            async with asyncio.timeout(2):
                while session.operation_snapshot()["queue_depth"] != 1:
                    await asyncio.sleep(0)
            assert session.launch_options.show_interactions is False
            assert session.interaction_controller.show is False
        result = await task
        assert result["show"] is True
        source = ReplacementSource.of(session)
        assert source.launch_kwargs(url="https://example.test")["show_interactions"] is True
    finally:
        await session.interaction_controller.close()
        session.recorder.close()


def test_scenario_new_settings_preserve_existing_positional_fields() -> None:
    from octowright.scenarios import Participant

    participant = Participant("flags", "chromium", "player", None, None, None, None, None, None, True, {"legacy": 1})
    assert participant.trace is True
    assert participant.options == {"legacy": 1}
    assert participant.show_interactions is None and participant.record_interactions is None


def test_new_options_preserve_existing_positional_fields() -> None:
    options = LaunchOptions("chromium", None, None, None, None, None, None, None, False, False, True)
    assert options.trace is True
    assert options.show_interactions is False and options.record_interactions is False


@pytest.mark.parametrize("tool", ["browser_launch", "browser_quick_launch"])
async def test_launch_new_flags_preserve_existing_positional_trace(monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    from unittest.mock import AsyncMock

    from octowright.server.browser import lifecycle

    launch = AsyncMock(return_value={"instance_id": "positional"})
    monkeypatch.setattr(lifecycle, "_pool_launch_with_deadline", launch)
    first = ("chromium", "https://example.test") if tool == "browser_launch" else ("https://example.test", "chromium")
    await getattr(lifecycle, tool)(*first, False, None, None, None, None, False, False, True, ephemeral=True)
    assert launch.call_args.kwargs["trace"] is True
    assert launch.call_args.kwargs["show_interactions"] is False
