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
from unittest.mock import MagicMock

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
