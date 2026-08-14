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
import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool import BrowserPool
from octowright.browser_pool.launch_pipeline import _build_session_object
from octowright.browser_pool.options import LaunchOptions
from octowright.server import _idempotency
from octowright.server import _request_context as _rc
from octowright.session import BrowserSession
from tests._operation_gate_fakes import OperationAwareFake


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
async def test_timeout_waiter_never_enters_body_and_leaves_no_recorder_row(tmp_path: Path) -> None:
    """A ticket that expires waiting for gate admission must never enter the
    click body -- no Playwright call, no JSONL row -- even after the holder
    releases and the gate opens back up. Uses a REAL ``Recorder`` (not a
    mock) so ``action_count`` reflects an actual disk-backed decision, not a
    mock call tally."""
    from octowright.recorder import Recorder
    from octowright.session.operation_gate import SessionBusyTimeoutError

    page = MagicMock()
    page.click = AsyncMock()
    log_path = tmp_path / "timeout-session.jsonl"
    session = BrowserSession(
        instance_id="timeout-session",
        kind="chromium",
        label=None,
        url="https://octowright.com",
        browser=None,
        context=MagicMock(),
        page=page,
        recorder=Recorder(log_path),
        log_path=log_path,
        operation_queue_timeout_seconds=0.05,
    )

    async with session.operation("owner"):
        queued = asyncio.create_task(session.click("#buy"))
        with pytest.raises(SessionBusyTimeoutError):
            await queued

    assert session.recorder.action_count == 0
    page.click.assert_not_awaited()

    # The gate is open again and the session is fully usable.
    await session.click("#buy")
    assert session.recorder.action_count == 1
    page.click.assert_awaited_once()


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


# ─── close_with_preparation (Task 8): preparation runs at the close ticket ──


def _real_pool_session(instance_id: str, tmp_path: Path) -> BrowserSession:
    context = MagicMock()
    context.close = AsyncMock()
    context.tracing = MagicMock()
    context.on = MagicMock()
    page = MagicMock()
    return BrowserSession(
        instance_id=instance_id,
        kind="chromium",
        label=None,
        url="https://octowright.com",
        browser=None,
        context=context,
        page=page,
        recorder=MagicMock(),
        log_path=tmp_path / f"{instance_id}.jsonl",
    )


@pytest.mark.asyncio
async def test_close_with_preparation_keeps_reservation_name_as_observable_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preparation callback re-enters ``session.operation(...)`` under
    the SAME literal name as the close reservation's own ``operation_name``
    -- exact-task reentrancy (Task 2) admits it without queueing, and the
    snapshot's ``active_operation`` stays that reservation's root the whole
    time it runs, proving a direct outside caller could never observe (or
    piggyback on) a different root by racing the ticket."""
    from octowright.browser_pool import close_helpers as _close_helpers
    from octowright.browser_pool.lifecycle import close_with_preparation

    monkeypatch.setattr(_close_helpers, "remove_manifest_session", lambda _id: None)
    pool = BrowserPool()
    session = _real_pool_session("root-check", tmp_path)
    pool._sessions[session.instance_id] = session

    seen_root: list[str | None] = []

    async def _preparation(prepared_session: BrowserSession) -> str:
        async with prepared_session.operation("browser_capture_and_close"):
            seen_root.append(prepared_session.operation_snapshot()["active_operation"])
            return "prepared"

    outcome = await close_with_preparation(
        pool,
        session.instance_id,
        force=True,
        reason="agent_close",
        operation_name="browser_capture_and_close",
        preparation=_preparation,
    )

    assert seen_root == ["browser_capture_and_close"]
    assert outcome.prepared == "prepared"
    assert outcome.response["closed"] is True
    assert session.instance_id not in pool._closing_sessions


@pytest.mark.asyncio
async def test_reserve_close_browser_require_fresh_rejects_shared_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``require_fresh=True`` refuses to coalesce onto a close cutoff
    another caller already accepted -- a compound helper cannot retroactively
    attach its own preparation to a ticket it does not own."""
    from octowright.browser_pool import close_helpers as _close_helpers
    from octowright.browser_pool.lifecycle import reserve_close_browser
    from octowright.session.operation_gate import SessionClosingError

    monkeypatch.setattr(_close_helpers, "remove_manifest_session", lambda _id: None)
    pool = BrowserPool()
    session = _real_pool_session("fresh-check", tmp_path)
    pool._sessions[session.instance_id] = session

    first = await reserve_close_browser(pool, session.instance_id, force=True, reason="agent_close")
    with pytest.raises(SessionClosingError):
        await reserve_close_browser(
            pool,
            session.instance_id,
            force=True,
            reason="agent_close",
            operation_name="browser_capture_and_close",
            require_fresh=True,
        )
    await first.reservation.wait()


def _json_route_request(sid: str, payload: dict[str, Any]) -> SimpleNamespace:
    """Minimal fake Starlette ``Request`` for calling a session route directly.

    ``TestClient`` doesn't mix cleanly with a background task holding a
    session's gate open in the same event loop, so the drain-window test below
    calls the route coroutines by hand (same approach as
    ``tests/test_http_routes_sessions_branches.py``)."""

    async def _body() -> bytes:
        return json.dumps(payload).encode()

    return SimpleNamespace(
        path_params={"id": sid},
        headers={"content-type": "application/json"},
        body=_body,
    )


@pytest.mark.asyncio
async def test_dashboard_routes_answer_409_inside_the_close_drain_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session mid-drain is deliberately visible in BOTH ``_sessions`` and
    ``_closing_sessions``, so ``has_session`` says yes while ``pool.get``
    raises. The dashboard's navigate / selector-validate routes must answer
    that race with a clean 409 instead of letting the gate error escape as an
    uncaught Starlette 500."""
    from octowright.browser_pool import close_helpers as _close_helpers
    from octowright.http.routes import sessions as _session_routes
    from octowright.server import _state as _server_state
    from tests._pool_invariants import wait_until

    monkeypatch.setattr(_close_helpers, "remove_manifest_session", lambda _id: None)
    pool = BrowserPool()
    session = _real_pool_session("drain-window", tmp_path)
    sid = session.instance_id
    pool._sessions[sid] = session
    monkeypatch.setattr(_server_state, "pool", pool)

    release = asyncio.Event()

    async def _hold() -> None:
        async with session.operation("held_work"):
            await release.wait()

    holder = asyncio.create_task(_hold())
    await wait_until(lambda: session.operation_snapshot()["active_operation"] == "held_work")

    closing = asyncio.create_task(pool.close(sid, force=True))
    # The close ticket is accepted (state -> closing, entry registered) but
    # queued behind the held operation: the drain window this test is about.
    await wait_until(lambda: sid in pool._closing_sessions)
    assert pool.has_session(sid)

    navigate = await _session_routes.session_navigate(_json_route_request(sid, {"url": "https://octowright.com/next"}))
    selector = await _session_routes.session_selector_validate(_json_route_request(sid, {"selector": ".btn"}))

    assert navigate.status_code == 409
    assert selector.status_code == 409

    release.set()
    await holder
    await closing


# ─── Task 9: macro replay holds one root lease per logical invocation ──────
#
# run_macro/run_sequence/run_macro_artifact each wrap their ENTIRE body in a
# single outer session.operation(...) lease. Nested session-method calls
# (click, expect_*, _push_status, selector_present, the checks helpers) all
# re-enter that same lease because they run in the SAME asyncio task -- only
# a call from a DIFFERENT task (a "manual" action racing the macro) actually
# queues behind it. These tests prove that queueing, not just that the calls
# don't raise.


class MacroGateFake(OperationAwareFake):
    """Real-gate session fake wired with just enough of the click/diagnostic
    surface for ``run_macro`` to dispatch a tiny macro end-to-end through the
    real dispatch machinery (no ``_dispatch_one`` monkeypatch needed)."""

    def __init__(self) -> None:
        super().__init__()
        self.page = None  # `_push_status` reads `.page`; None short-circuits.
        self.calls: list[str] = []
        self.block_after_first: asyncio.Event | None = None
        self.release_first: asyncio.Event | None = None

    async def click(self, selector: str) -> None:
        async with self.operation("browser_click"):
            if selector == "#manual":
                self.calls.append("manual")
                return
            self.calls.append(f"macro:{selector}")
            if selector == "boom":
                raise RuntimeError("boom click failed")
            if selector == "first" and self.block_after_first is not None:
                self.block_after_first.set()
                assert self.release_first is not None
                await self.release_first.wait()

    async def diagnostic_bundle(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append("diagnostic_bundle")
        return {}


@pytest.fixture
def session() -> MacroGateFake:
    return MacroGateFake()


def _register_macro_gate_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.macros import execution as _execution

    macros = {
        "two-actions": {
            "name": "two-actions",
            "actions": [
                {"action": "click", "selector": "first"},
                {"action": "click", "selector": "second"},
            ],
        },
        "failing-macro": {
            "name": "failing-macro",
            "actions": [{"action": "click", "selector": "boom"}],
        },
    }

    def _fake_load(name: str) -> dict[str, Any]:
        if name not in macros:
            raise FileNotFoundError(name)
        return macros[name]

    monkeypatch.setattr(_execution, "load_macro", _fake_load)


async def _wait_for_root_operation(fake_session: MacroGateFake, name: str) -> None:
    async with asyncio.timeout(1):
        while fake_session.operation_snapshot()["active_operation"] != name:
            await asyncio.sleep(0)


async def run_macro_with_waiting_manual_action(fake_session: MacroGateFake, name: str) -> None:
    """Start ``run_macro`` and, once its root lease is confirmed held, race a
    manual action against it -- proving *any* point during the macro's
    execution (not just a hand-picked gap) rejects interleaving."""
    from octowright.macros.execution import run_macro as _run_macro

    macro_task = asyncio.create_task(_run_macro(fake_session, name))
    await _wait_for_root_operation(fake_session, "macro_run")
    manual_task = asyncio.create_task(fake_session.click("#manual"))
    try:
        await macro_task
    finally:
        await manual_task


@pytest.mark.asyncio
async def test_manual_action_cannot_interleave_macro(session: MacroGateFake, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.macros.execution import run_macro

    _register_macro_gate_fixtures(monkeypatch)
    session.block_after_first = asyncio.Event()
    session.release_first = asyncio.Event()
    macro_task = asyncio.create_task(run_macro(session, "two-actions"))
    await session.block_after_first.wait()
    manual = asyncio.create_task(session.click("#manual"))
    await wait_for_queue_depth(session._test_operation_gate, 1)
    assert session.calls == ["macro:first"]
    session.release_first.set()
    await asyncio.gather(macro_task, manual)
    assert session.calls == ["macro:first", "macro:second", "manual"]


@pytest.mark.asyncio
async def test_failure_bundle_is_captured_before_manual_waiter(
    session: MacroGateFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure diagnostics (diagnostic_bundle) run while the root
    ``macro_run`` lease is still held -- a manual action queued behind it
    can only run AFTER the lease releases, so it always lands last."""
    _register_macro_gate_fixtures(monkeypatch)
    with pytest.raises(RuntimeError):
        await run_macro_with_waiting_manual_action(session, "failing-macro")
    assert session.calls.index("diagnostic_bundle") < session.calls.index("manual")


def test_gate_operation_names_never_enter_the_replay_or_recorder_vocabulary() -> None:
    """Task 9's fixed operation names are pure in-process scheduling labels
    for ``SessionOperationGate.operation(...)`` -- gate acquire/release/
    timeout never touch the session's JSONL recorder, so these names must
    never collide with a real macro action kind, replay rename/drop key, or
    conditional-action name. A collision here would mean gate scheduling
    leaked into the JSONL/replay/export vocabulary, which the design
    explicitly forbids."""
    from octowright.conditional import CONDITIONAL_ACTIONS
    from octowright.macros import runtime as _runtime

    gate_operation_names = {
        "macro_run",
        "macro_run_sequence",
        "macro_artifact_run",
        "macro_status",
        "macro_condition",
        "macro_check",
    }
    replay_and_recorder_vocabulary = (
        set(_runtime._ACTION_MAP)
        | _runtime._REPLAY_SKIP
        | _runtime._REPLAY_PASSIVE
        | set(_runtime._REPLAY_RENAME_KEYS)
        | set(_runtime._REPLAY_DROP_KEYS)
        | CONDITIONAL_ACTIONS
    )
    assert gate_operation_names.isdisjoint(replay_and_recorder_vocabulary)


# ─── Task 10: complete-workflow composites keep ONE observable root op ─────
#
# browser_operation(pool, instance_id, operation_name) defines a boundary
# around a WHOLE MCP-tool call. A composite like browser_click(...,
# response_mode="outline") dispatches a click AND builds an outline
# response; both must run under the SAME root operation (exact-task
# reentrancy, Task 2) rather than two separate gate acquisitions with a
# window between them where a concurrent caller could interleave.


class ToolGateFake(OperationAwareFake):
    """Real-gate session fake wired with just enough of the click/discovery
    surface for the real ``browser_click``/``browser_page_outline`` MCP
    tools to run end-to-end. Every Page/Frame-touching point records the
    gate's current root operation name so a test can assert it never moved."""

    instance_id = "click-outline-inst"

    def __init__(self) -> None:
        super().__init__()
        self.observed_roots: list[str | None] = []
        self.page = SimpleNamespace(title=self._title)

    async def _title(self) -> str:
        self.observed_roots.append(self.operation_snapshot()["active_operation"])
        return "Example"

    async def click(self, selector: str) -> None:
        self.observed_roots.append(self.operation_snapshot()["active_operation"])

    def _target(self) -> _ToolGateTarget:
        return _ToolGateTarget(self)


class _ToolGateTarget:
    url = "https://octowright.com"

    def __init__(self, fake: ToolGateFake) -> None:
        self._fake = fake

    async def evaluate(self, _js: str, _args: dict[str, Any]) -> dict[str, Any]:
        self._fake.observed_roots.append(self._fake.operation_snapshot()["active_operation"])
        return {"headings": [], "landmarks": [], "links": [], "fields": [], "counts": {}}


@pytest.mark.asyncio
async def test_browser_click_outline_is_one_root_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server.browser import discovery as _discovery
    from octowright.server.browser import input as _input
    from octowright.server.browser.input import browser_click

    tool_session = ToolGateFake()
    fake_pool = MagicMock()
    fake_pool.get.return_value = tool_session
    monkeypatch.setattr(_input, "pool", fake_pool)
    monkeypatch.setattr(_discovery, "pool", fake_pool)

    result = await browser_click(tool_session.instance_id, selector="#buy", response_mode="outline")

    assert result["ok"] is True
    assert "outline" in result
    # Click AND the outline's page-side evaluate both ran while "browser_click"
    # was still the gate's root -- one observable operation, not two.
    assert tool_session.observed_roots
    assert all(root == "browser_click" for root in tool_session.observed_roots)


@contextlib.contextmanager
def _keyed_request_context(key: str) -> Iterator[None]:
    """Set the request contextvar carrying an ``octowrightIdempotencyKey``.

    The test above runs with NO request context, so ``_idempotent_dispatch``
    takes its no-key fast path and never exercises production dispatch. The
    follower bridge injects a key into EVERY ``tools/call``, so the keyed path
    below is the real deployment. Mirrors
    ``tests/test_idempotency_cache.py::_request_context``.
    """
    ctx = SimpleNamespace(meta={"octowrightIdempotencyKey": key}, session=object(), request_id="r")
    token = _rc._request_ctx.set(ctx)
    try:
        yield
    finally:
        _rc._request_ctx.reset(token)


@pytest.mark.asyncio
async def test_keyed_composite_tools_do_not_deadlock_on_nested_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A composite tool calling another tool by its module-level (fully
    wrapped) name must not hop that nested call into a second producer task.

    With a key present, ``_idempotent_dispatch`` runs the tool in a detached
    producer task. A nested call has a different storage key, so it would claim
    its own task -- one that does NOT hold the parent's gate lease. The gate's
    exact-task reentrancy rule then (correctly) queues it behind the lease the
    parent is holding while awaiting it: deadlock until the queue timeout. The
    bounded waits below fail fast instead of hanging if that regresses.
    """
    from octowright.server.browser import discovery as _discovery
    from octowright.server.browser import input as _input
    from octowright.server.browser import inspect as _inspect

    tool_session = ToolGateFake()
    fake_pool = MagicMock()
    fake_pool.get.return_value = tool_session
    monkeypatch.setattr(_input, "pool", fake_pool)
    monkeypatch.setattr(_discovery, "pool", fake_pool)
    monkeypatch.setattr(_inspect, "pool", fake_pool)

    _idempotency._reset_for_tests()
    try:
        with _keyed_request_context("observe-key"):
            observed = await asyncio.wait_for(
                _inspect.browser_observe(
                    tool_session.instance_id,
                    include_console=False,
                    include_network=False,
                ),
                timeout=5.0,
            )
        assert "outline" in observed

        with _keyed_request_context("click-outline-key"):
            clicked = await asyncio.wait_for(
                _input.browser_click(tool_session.instance_id, selector="#buy", response_mode="outline"),
                timeout=5.0,
            )
        assert clicked["ok"] is True
        assert "outline" in clicked
    finally:
        _idempotency._reset_for_tests()

    # The nested sub-calls ran inline under their parent's root operation, so
    # the gate never saw a second root -- same one-observable-operation contract
    # the no-key test above pins.
    assert set(tool_session.observed_roots) == {"browser_observe", "browser_click"}


class CaptureGateFake(OperationAwareFake):
    """Real-gate session fake for ``capture_create``: aria content read
    through URL/title metadata must stay under one root."""

    instance_id = "capture-gate-inst"

    def __init__(self) -> None:
        super().__init__()
        self.observed_roots: list[str | None] = []
        self.page = SimpleNamespace(title=self._title)

    async def _title(self) -> str:
        self.observed_roots.append(self.operation_snapshot()["active_operation"])
        return "Example"

    def _target(self) -> _CaptureGateTarget:
        return _CaptureGateTarget(self)


class _CaptureGateTarget:
    url = "https://octowright.com"

    def __init__(self, fake: CaptureGateFake) -> None:
        self._fake = fake

    def locator(self, _selector: str) -> _CaptureGateLocator:
        return _CaptureGateLocator(self._fake)


class _CaptureGateLocator:
    def __init__(self, fake: CaptureGateFake) -> None:
        self._fake = fake

    async def aria_snapshot(self) -> str:
        self._fake.observed_roots.append(self._fake.operation_snapshot()["active_operation"])
        return "- button 'Buy'"


@pytest.mark.asyncio
async def test_capture_create_keeps_content_and_metadata_together(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server import captures as _captures_tools

    session = CaptureGateFake()
    fake_pool = MagicMock()
    fake_pool.get.return_value = session
    monkeypatch.setattr(_captures_tools, "pool", fake_pool)
    monkeypatch.setattr(
        _captures_tools._captures,
        "save_capture",
        lambda **_kw: {"capture_id": "cap_test", "preview": "preview", "truncated": False},
    )

    out = await _captures_tools.capture_create(session.instance_id, source="snapshot")

    assert out["capture_id"] == "cap_test"
    # The aria-snapshot content read and the title/url metadata read both ran
    # under the same "capture_create" root as the eventual save_capture call.
    assert session.observed_roots
    assert all(root == "capture_create" for root in session.observed_roots)
