# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Host authority, bounded capture and immutable page clocks."""

from __future__ import annotations

import asyncio
import importlib
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.session.operation.gate.core import SessionOperationGate
from tests.test_interactions_storage import row


def backend() -> Any:
    try:
        return importlib.import_module("octowright.interactions")
    except ModuleNotFoundError:
        pytest.fail("interaction controller is not implemented")


class Page:
    url = "about:blank"
    video = None
    frames: list[Any] = []

    def __init__(self) -> None:
        self.main_frame = SimpleNamespace(page=self)
        self.frames = [self.main_frame]
        self.main_frame.evaluate = self.evaluate
        self.listeners: dict[str, Any] = {}

    async def evaluate(self, *args: Any) -> Any:
        if args[0].startswith("({time_origin"):
            return {"time_origin": time.time() * 1000 - 1000, "width": 800, "height": 600, "dpr": 2}
        return True

    def on(self, event: str, callback: Any) -> None:
        self.listeners[event] = callback

    def is_closed(self) -> bool:
        return False


class Context:
    def __init__(self) -> None:
        self.pages = [Page()]
        self.bindings: dict[str, Any] = {}
        self.listeners: dict[str, Any] = {}

    async def expose_binding(self, name: str, callback: Any) -> None:
        self.bindings[name] = callback

    async def add_init_script(self, **kwargs: Any) -> None:
        pass

    def on(self, event: str, callback: Any) -> None:
        self.listeners[event] = callback

    def remove_listener(self, event: str, callback: Any) -> None:
        self.listeners.pop(event, None)


def payload(**kwargs: Any) -> dict[str, Any]:
    value = row()
    for key in ("type", "page_id", "video", "t"):
        value.pop(key)
    return {**value, "at": time.time() * 1000, **kwargs}


async def installed(log: Path, **flags: bool) -> tuple[Any, Context, dict[str, Any]]:
    controller = backend().InteractionController(log, **flags)
    context = Context()
    await controller.install(
        SimpleNamespace(
            context=context, kind="chromium", operation=SessionOperationGate("interactiontest", "chromium").operation
        )
    )
    page = context.pages[0]
    return controller, context, {"page": page, "frame": page.main_frame, "context": context}


@pytest.mark.asyncio
async def test_dormant_flags_clock_and_host_settings_reset(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, show=True)
    callback = context.bindings["__octowrightInteractionEvent"]
    callback(source, payload())
    assert not log.with_suffix(".interactions.jsonl").exists()
    await controller.configure(record=True)
    callback(source, payload())
    await controller.configure(record=False)
    callback(source, payload(phase="up"))
    await controller.close()
    result = backend().read_interactions(log)
    events = result["events"]
    event = next(r for r in events if r["type"] == "interaction")
    assert event["t"] >= 900
    assert events[-1]["type"] == "settings" and events[-1]["reset"] is True
    assert events[-1]["record"] is False
    assert sum(r["type"] == "interaction" for r in events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"x": True},
        {"x": float("nan")},
        {"width": 0},
        {"pointer_id": 1.5},
        {"button": True},
        {"shown": 1},
        {"at": 0},
        {"page_text": "typed"},
    ],
)
async def test_rejects_untrusted_payload_and_source(tmp_path: Path, invalid: dict[str, Any]) -> None:
    controller, context, source = await installed(tmp_path / "x.jsonl", record=True)
    callback = context.bindings["__octowrightInteractionEvent"]
    assert callback(source, payload(**invalid)) is None
    assert callback({**source, "page": Page()}, payload()) is None
    await controller.close()
    assert not any(r["type"] == "interaction" for r in backend().read_interactions(tmp_path / "x.jsonl")["events"])


@pytest.mark.asyncio
async def test_rate_loss_resets_contacts_and_bounds_accepted_events(tmp_path: Path) -> None:
    controller, context, source = await installed(tmp_path / "x.jsonl", record=True)
    callback = context.bindings["__octowrightInteractionEvent"]
    for n in range(200):
        callback(source, payload(phase="wheel", dy=n))
    callback(source, payload(phase="up"))
    await controller.configure(record=False)
    await controller.close()
    result = backend().read_interactions(tmp_path / "x.jsonl")
    assert result["truncated"]
    assert sum(r["type"] == "interaction" for r in result["events"]) <= 120
    assert any(r.get("reset") is True for r in result["events"])


@pytest.mark.asyncio
async def test_page_binding_never_raises_on_host_sink_failure(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    log.with_suffix(".interactions.jsonl").mkdir()
    controller, context, source = await installed(log, record=True)
    assert context.bindings["__octowrightInteractionEvent"](source, payload()) is None
    assert (await controller.configure(show=True))["truncated"] is True
    await controller.close()


@pytest.mark.asyncio
async def test_contact_budget_and_many_successive_closed_pages(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    callback = context.bindings["__octowrightInteractionEvent"]
    for n in range(17):
        callback(source, payload(pointer_id=n))
    assert backend().read_interactions(log)["truncated"]
    # The page bound applies to retained states, never the lifetime number.
    for _ in range(70):
        page = Page()
        context.listeners["page"](page)
        await asyncio.gather(*controller._tasks)
        context.bindings["__octowrightInteractionEvent"](
            {"context": context, "page": page, "frame": page.main_frame}, payload()
        )
        page.listeners["close"]()
    await controller.close()
    rows = backend().read_interactions(log)["events"]
    assert max(r["page_id"] for r in rows if r["type"] == "interaction") > 64


def test_validation_handles_huge_integer_without_host_exception() -> None:
    from octowright.interaction_schema import valid_payload

    assert valid_payload(payload(x=10**1000)) is False


@pytest.mark.asyncio
async def test_rate_is_bounded_across_window_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = backend()
    now = [0.0]
    monkeypatch.setattr(api, "time", SimpleNamespace(time=time.time, monotonic=lambda: now[0]))
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    callback = context.bindings["__octowrightInteractionEvent"]
    now[0] = 0.99
    for _ in range(120):
        callback(source, payload(phase="wheel"))
    now[0] = 1.01
    for _ in range(120):
        callback(source, payload(phase="wheel"))
    await controller.close()
    assert sum(r["type"] == "interaction" for r in api.read_interactions(log)["events"]) <= 120


@pytest.mark.asyncio
async def test_normal_page_close_resets_without_claiming_lost_events(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    context.bindings["__octowrightInteractionEvent"](source, payload())
    context.pages[0].listeners["close"]()
    await controller.close()
    result = backend().read_interactions(log)
    assert not result["truncated"]
    assert any(r.get("reason") == "page_closed" and r.get("page_id") == 1 for r in result["events"])


@pytest.mark.asyncio
async def test_geometry_seed_is_main_frame_only_and_immutable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(*args: Any) -> None:
        raise RuntimeError("document changed during initial probe")

    monkeypatch.setattr(Page, "evaluate", unavailable)
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    callback = context.bindings["__octowrightInteractionConfig"]
    geometry = {"time_origin": time.time() * 1000, "width": 800, "height": 600, "dpr": 2}
    child = {**source, "frame": SimpleNamespace(page=source["page"])}
    callback(child, {**geometry, "width": 111})
    assert [r for r in backend().read_interactions(log)["events"] if r["type"] == "page"][-1]["video_geometry"] is None
    callback(source, geometry)
    callback(source, {**geometry, "width": 160, "height": 120, "dpr": 1})
    await controller.close()
    metadata = [r for r in backend().read_interactions(log)["events"] if r["type"] == "page"]
    assert metadata[-1]["video_geometry"] == {
        "engine": "chromium",
        "viewport_width": 800,
        "viewport_height": 600,
        "device_scale_factor": 2,
    }
    assert all(r["video_geometry"] in (None, metadata[-1]["video_geometry"]) for r in metadata)


@pytest.mark.asyncio
async def test_close_drains_pending_public_video_identity(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    controller, context, _source = await installed(log, record=True)
    started, ready = asyncio.Event(), asyncio.Event()

    class Video:
        async def path(self) -> Path:
            started.set()
            await ready.wait()
            return tmp_path / "page@late.webm"

    page = Page()
    page.video = Video()  # type: ignore[assignment]
    context.listeners["page"](page)
    await started.wait()
    ready.set()
    await controller.close()
    rows = backend().read_interactions(log)["events"]
    assert any(r["type"] == "page" and r["video"] == "page@late.webm" for r in rows)


@pytest.mark.asyncio
async def test_host_control_rows_place_resets_after_a_held_contact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = backend()
    wall = [time.time() * 1000]
    monkeypatch.setattr(api, "time", SimpleNamespace(time=lambda: wall[0] / 1000, monotonic=time.monotonic))
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    context.bindings["__octowrightInteractionEvent"](source, payload(at=wall[0]))
    wall[0] += 3000
    await controller.configure(record=False)
    rows = api.read_interactions(log)["events"]
    down = next(r for r in rows if r["type"] == "interaction")
    assert rows[-1]["at"] == down["at"] + 3000
    await controller.configure(record=True)
    wall[0] += 2000
    context.pages[0].listeners["close"]()
    await controller.close()
    reset = next(r for r in api.read_interactions(log)["events"] if r.get("reason") == "page_closed")
    assert reset["at"] == down["at"] + 5000


@pytest.mark.asyncio
async def test_noop_settings_preserve_an_active_contact(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    callback = context.bindings["__octowrightInteractionEvent"]
    callback(source, payload())
    before = backend().read_interactions(log)["events"]
    await controller.configure(record=True)
    assert backend().read_interactions(log)["events"] == before
    callback(source, payload(phase="up"))
    await controller.close()


@pytest.mark.asyncio
async def test_cancelled_close_releases_pending_work_and_sink(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    controller, context, _source = await installed(log, record=True)
    started = asyncio.Event()

    class Video:
        async def path(self) -> Path:
            started.set()
            await asyncio.Event().wait()
            return tmp_path / "never.webm"

    page = Page()
    page.video = Video()  # type: ignore[assignment]
    context.listeners["page"](page)
    await started.wait()
    closing = asyncio.create_task(controller.close())
    async with asyncio.timeout(2):
        while not controller._closed:
            await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert controller._sink._stream.closed
    assert all(task.done() for task in controller._tasks)


@pytest.mark.asyncio
async def test_late_page_event_cannot_follow_its_close_reset(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    callback = context.bindings["__octowrightInteractionEvent"]
    callback(source, payload())
    context.pages[0].listeners["close"]()
    callback(source, payload(pointer_id=2))
    await controller.close()
    assert sum(r["type"] == "interaction" for r in backend().read_interactions(log)["events"]) == 1


@pytest.mark.asyncio
async def test_contact_budget_counts_pointer_types_independently(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    callback = context.bindings["__octowrightInteractionEvent"]
    for n in range(17):
        callback(source, payload(pointer_type=("mouse", "touch", "pen")[n % 3], pointer_id=n // 3))
    await controller.close()
    result = backend().read_interactions(log)
    assert sum(r["type"] == "interaction" for r in result["events"]) == 16
    assert result["truncated"]


@pytest.mark.asyncio
async def test_same_settings_retry_heals_failed_frames_without_resetting_contacts(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    attempts = 0

    async def retrying_frame(*args: Any) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary frame failure")
        return True

    context.pages[0].main_frame.evaluate = retrying_frame
    first = await controller.configure(show=True)
    assert first["pending_frames"] == 1 and first["applied_frames"] == 0
    context.bindings["__octowrightInteractionEvent"](source, payload())
    before = backend().read_interactions(log)["events"]
    second = await controller.configure(show=True)
    assert second["pending_frames"] == 0 and second["applied_frames"] == 1
    assert backend().read_interactions(log)["events"] == before
    context.bindings["__octowrightInteractionEvent"](source, payload(phase="up"))
    await controller.close()


@pytest.mark.asyncio
async def test_dropped_release_has_timed_reset_and_off_still_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = backend()
    wall = [time.time() * 1000]
    monkeypatch.setattr(api, "time", SimpleNamespace(time=lambda: wall[0] / 1000, monotonic=time.monotonic))
    log = tmp_path / "x.jsonl"
    controller, context, source = await installed(log, record=True)
    callback = context.bindings["__octowrightInteractionEvent"]
    callback(source, payload(at=wall[0]))
    for _ in range(119):
        callback(source, payload(phase="wheel", at=wall[0]))
    wall[0] += 1000
    callback(source, payload(phase="up", at=wall[0]))
    rows = api.read_interactions(log)["events"]
    assert rows[-1]["reason"] == "loss" and rows[-1]["at"] == wall[0]
    wall[0] += 1000
    await controller.configure(record=False)
    rows = api.read_interactions(log)["events"]
    assert rows[-1]["type"] == "settings" and rows[-1]["record"] is False
    assert rows[-1]["at"] == wall[0]
    await controller.close()


@pytest.mark.asyncio
async def test_missing_document_observer_is_pending_instead_of_applied(tmp_path: Path) -> None:
    controller, context, _source = await installed(tmp_path / "x.jsonl")

    async def absent_observer(*args: Any) -> bool:
        return False

    context.pages[0].main_frame.evaluate = absent_observer
    result = await controller.configure(show=True)
    assert result["applied_frames"] == 0 and result["pending_frames"] == 1
    await controller.close()
