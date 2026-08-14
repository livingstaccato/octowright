# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Integration tests pinning the one-gate-per-session wiring added in Task 4:

- ``BrowserSession.__post_init__`` constructs exactly one ``SessionOperationGate``.
- ``operation_snapshot()`` is a stable, repeatable read of that gate.
- ``BrowserPool``'s resolved ``operation_queue_timeout_seconds`` reaches every
  session built through ``_build_session_object``.
- ``BrowserSession.set_protected_state`` routes through the gate's
  ``control_update`` and mutates ``protected``/``protected_reason``.
- ``core_page_mixin.py`` stays under the repository's 550-line LOC ceiling
  after the expectation-mixin extraction.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool import BrowserPool
from octowright.browser_pool.launch_pipeline import _build_session_object
from octowright.browser_pool.options import LaunchOptions
from octowright.session import BrowserSession


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_session_kwargs(tmp_path: Path) -> dict[str, object]:
    return {
        "instance_id": "fake-session",
        "kind": "chromium",
        "label": None,
        "url": "https://octowright.com",
        "browser": None,
        "context": MagicMock(),
        "page": MagicMock(),
        "recorder": MagicMock(),
        "log_path": tmp_path / "fake.jsonl",
    }


def test_browser_session_constructs_exactly_one_gate(fake_session_kwargs: dict[str, object]) -> None:
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]
    first = session.operation_snapshot()
    second = session.operation_snapshot()
    assert first == second
    assert first == {
        "state": "open",
        "active_operation": None,
        "active_for_ms": None,
        "queue_depth": 0,
        "oldest_wait_ms": None,
        "queue_timeout_seconds": 300.0,
    }


def test_browser_session_gate_is_a_single_persistent_instance(fake_session_kwargs: dict[str, object]) -> None:
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]
    gate_one = session._operation_gate
    gate_two = session._operation_gate
    assert gate_one is gate_two


def test_browser_session_explicit_timeout_overrides_env_default(
    monkeypatch: pytest.MonkeyPatch, fake_session_kwargs: dict[str, object]
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", "99")
    session = BrowserSession(operation_queue_timeout_seconds=12.5, **fake_session_kwargs)  # type: ignore[arg-type]
    assert session.operation_snapshot()["queue_timeout_seconds"] == 12.5


@pytest.mark.asyncio
async def test_set_protected_state_updates_fields_and_routes_through_gate(
    fake_session_kwargs: dict[str, object],
) -> None:
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]
    assert session.protected is False
    result = await session.set_protected_state(True, reason="user_pin")
    assert session.protected is True
    assert session.protected_reason == "user_pin"
    assert result == {"instance_id": "fake-session", "protected": True}


@pytest.mark.asyncio
async def test_set_protected_state_runs_while_an_operation_is_active(
    fake_session_kwargs: dict[str, object],
) -> None:
    """control_update only takes the gate's short admission lock (mutual
    exclusion against a concurrent reserve_close preflight reading the same
    field), not the FIFO operation slot — so it must complete promptly even
    while an unrelated operation is in flight, not queue behind it."""
    session = BrowserSession(**fake_session_kwargs)  # type: ignore[arg-type]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _hold_operation() -> None:
        async with session.operation("browser_click"):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(_hold_operation())
    await entered.wait()

    result = await asyncio.wait_for(session.set_protected_state(True), timeout=1.0)
    assert result == {"instance_id": "fake-session", "protected": True}
    assert session.protected is True

    release.set()
    await holder


def test_core_page_mixin_stays_below_loc_ceiling() -> None:
    with Path("src/octowright/session/core_page_mixin.py").open() as handle:
        assert sum(1 for _ in handle) <= 550


def test_core_expect_mixin_module_exists_and_is_importable() -> None:
    from octowright.session.core_expect_mixin import SessionExpectMixin

    assert hasattr(SessionExpectMixin, "expect_url")
    assert hasattr(SessionExpectMixin, "expect_text")
    assert hasattr(SessionExpectMixin, "expect_selector")
    assert hasattr(SessionExpectMixin, "expect_js")
    assert hasattr(SessionExpectMixin, "_poll_until")


# ─── gated_operation: direct-call serialization + reentrant timeout boundary ──


def blocking_call(started: asyncio.Event, release: asyncio.Event) -> Any:
    async def _side_effect(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        await release.wait()

    return _side_effect


async def wait_for_queue_depth(gate: Any, depth: int) -> None:
    async with asyncio.timeout(1):
        while gate.snapshot()["queue_depth"] != depth:
            await asyncio.sleep(0)


@pytest.fixture
def fake_browser_session(fake_session_kwargs: dict[str, object]) -> BrowserSession:
    page = MagicMock()
    page.url = "https://octowright.com"
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="Example")
    page.evaluate = AsyncMock(return_value=None)
    page.wait_for_selector = AsyncMock()
    kwargs = {**fake_session_kwargs, "page": page}
    session = BrowserSession(**kwargs)  # type: ignore[arg-type]
    # Not exercised by these gate-serialization tests; stub out so navigate()
    # doesn't spawn a real markdown-capture background task against a
    # MagicMock page.
    session._schedule_markdown_capture = MagicMock()  # type: ignore[method-assign]
    return session


@pytest.mark.asyncio
async def test_direct_session_actions_serialize(fake_browser_session: BrowserSession) -> None:
    """Two decorated methods called directly (no MCP layer) still serialize
    through the same gate: the second call queues behind the first and its
    Playwright call does not fire until the first releases."""
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    fake_browser_session.page.goto.side_effect = blocking_call(first_started, release_first)
    first = asyncio.create_task(fake_browser_session.navigate("https://one.test"))
    await first_started.wait()
    second = asyncio.create_task(fake_browser_session.evaluate("document.title"))
    await wait_for_queue_depth(fake_browser_session._operation_gate, 1)
    fake_browser_session.page.evaluate.assert_not_awaited()
    release_first.set()
    await asyncio.gather(first, second)
    fake_browser_session.page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_inner_timeout_begins_after_gate_admission(fake_browser_session: BrowserSession) -> None:
    """A queued call's own internal timeout must not start ticking while it
    waits for the gate -- it starts only once admitted."""
    async with fake_browser_session.operation("owner"):
        queued = asyncio.create_task(fake_browser_session.expect_selector("#ready", timeout_ms=25))
        await wait_for_queue_depth(fake_browser_session._operation_gate, 1)
        fake_browser_session.page.wait_for_selector.assert_not_awaited()
    await queued
    fake_browser_session.page.wait_for_selector.assert_awaited_once_with("#ready", timeout=25)


@pytest.mark.asyncio
async def test_list_pages_is_coherent_and_async(fake_browser_session: BrowserSession) -> None:
    result = await fake_browser_session.list_pages()
    assert result[0]["is_active"] is True


@pytest.mark.asyncio
async def test_list_frames_is_coherent_and_async(fake_browser_session: BrowserSession) -> None:
    fake_browser_session.page.frames = []
    result = await fake_browser_session.list_frames()
    assert result == []


@pytest.mark.asyncio
async def test_set_dialog_policy_is_coherent_and_async(fake_browser_session: BrowserSession) -> None:
    result = await fake_browser_session.set_dialog_policy("accept")
    assert result == {"ok": True, "policy": "accept", "prompt_text": None}


# ─── pool → session timeout propagation ────────────────────────────────────


@dataclass
class FakeLaunchParts:
    """Minimal set of ``_build_session_object`` inputs for a synthetic launch."""

    instance_id: str = "fake-launch"
    kind: str = "chromium"
    label: str | None = None
    target_url: str = "https://octowright.com"
    browser: Any = None
    context: Any = field(default_factory=MagicMock)
    page: Any = field(default_factory=MagicMock)
    recorder: Any = field(default_factory=MagicMock)
    log_path: Path = field(default_factory=lambda: Path("/tmp/fake-launch.jsonl"))
    user_data_dir: str | None = None
    profile: str | None = None
    launch_options: LaunchOptions = field(default_factory=lambda: LaunchOptions(protected=False))
    har_path: Path | None = None
    viewport_info: Any = field(
        default_factory=lambda: MagicMock(mode=MagicMock(value="unknown"), width=None, height=None)
    )


@pytest.fixture
def fake_launch_parts(tmp_path: Path) -> FakeLaunchParts:
    page = MagicMock()
    page.video = None
    return FakeLaunchParts(log_path=tmp_path / "fake-launch.jsonl", page=page)


def build_session_for_test(pool: BrowserPool, parts: FakeLaunchParts) -> BrowserSession:
    return _build_session_object(
        pool=pool,
        instance_id=parts.instance_id,
        kind=parts.kind,
        label=parts.label,
        target_url=parts.target_url,
        browser=parts.browser,
        context=parts.context,
        page=parts.page,
        recorder=parts.recorder,
        log_path=parts.log_path,
        user_data_dir=parts.user_data_dir,
        profile=parts.profile,
        launch_options=parts.launch_options,
        har_path=parts.har_path,
        viewport_info=parts.viewport_info,
        operation_queue_timeout_seconds=pool.operation_queue_timeout_seconds,
    )


def test_pool_explicit_operation_timeout_reaches_new_session(
    monkeypatch: pytest.MonkeyPatch,
    fake_launch_parts: FakeLaunchParts,
) -> None:
    pool = BrowserPool(operation_queue_timeout_seconds=17.0)
    session = build_session_for_test(pool, fake_launch_parts)
    assert session.operation_snapshot()["queue_timeout_seconds"] == 17.0


def test_pool_default_operation_timeout_resolves_from_env(
    monkeypatch: pytest.MonkeyPatch,
    fake_launch_parts: FakeLaunchParts,
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", "42")
    pool = BrowserPool()
    assert pool.operation_queue_timeout_seconds == 42.0
    session = build_session_for_test(pool, fake_launch_parts)
    assert session.operation_snapshot()["queue_timeout_seconds"] == 42.0
