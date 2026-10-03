# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Native Wayland: the launch-time fallback, persistence and status surfacing.

No browser is launched. The fallback is driven through
``launch_execution.open_with_wayland_fallback`` with a scripted opener, and
end to end through ``BrowserPool.launch`` with ``_open_browser_context`` and
``post_context_setup`` replaced.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.browser_pool import launch_execution, wayland
from octowright.browser_pool.launch_helpers import _record_launch_event
from octowright.browser_pool.options import LaunchOptions
from octowright.browser_pool.pool import BrowserPool
from octowright.browser_pool.relaunch import _launch_from_snapshot, _relaunch_snapshot_from_session
from octowright.recorder import Recorder
from octowright.request_errors import InvalidRequestError

#: The real failure text, captured from chromium-1243 launched with
#: --ozone-platform=wayland against a missing socket (trimmed). Note the first
#: line also matches driver_health's driver-dead markers, which is why the pool
#: confirms a dead driver with a liveness probe before resetting it.
WAYLAND_FAILURE = (
    "BrowserType.launch: Target page, context or browser has been closed\n"
    "Browser logs:\n"
    "\n"
    "<launching> /cache/chromium-1243/chrome-linux64/chrome --disable-field-trial-config "
    "--ozone-platform=wayland --enable-features=WaylandWindowDecorations --remote-debugging-pipe\n"
    "<launched> pid=1\n"
    "[pid=1][err] [1:1:1003/123020.174417:ERROR:ui/ozone/platform/wayland/host/wayland_connection.cc:206] "
    "Failed to connect to Wayland display: No such file or directory (2)\n"
    "[pid=1][err] [1:1:1003/123020.174657:ERROR:ui/aura/env.cc:246] The platform failed to initialize.  Exiting.\n"
)

ON_ARGS = {
    "args": ["--disable-dev-shm-usage", "--ozone-platform=wayland", "--enable-features=WaylandWindowDecorations"]
}
OFF_ARGS = {"args": ["--disable-dev-shm-usage"]}
#: What the X11 retry of ON_ARGS launches with: X11 forced, not merely Wayland dropped.
RETRY_ARGS = {"args": ["--disable-dev-shm-usage", "--ozone-platform=x11"]}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _decision(source: str = "auto", requested: bool | None = None) -> wayland.WaylandDecision:
    return wayland.WaylandDecision(requested=requested, source=source, effective=True, reason="wayland_session")  # type: ignore[arg-type]


class _Opener:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, launch_kwargs: dict[str, Any]) -> Any:
        self.calls.append(launch_kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


# ─── open_with_wayland_fallback ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_auto_failure_retries_once_without_the_flags() -> None:
    opener = _Opener(RuntimeError(WAYLAND_FAILURE), "opened")
    cleanup = AsyncMock()

    opened, decision = await launch_execution.open_with_wayland_fallback(
        decision=_decision(), launch_kwargs=ON_ARGS, open_context=opener, cleanup=cleanup
    )

    assert opened == "opened"
    assert len(opener.calls) == 2
    assert opener.calls[0] == ON_ARGS
    assert opener.calls[1] == RETRY_ARGS
    cleanup.assert_awaited_once()
    assert decision.effective is False
    assert decision.reason == "fallback_x11"
    assert decision.fallback_reason is not None
    assert decision.fallback_reason.startswith("ERROR:ui/ozone/platform/wayland/host/wayland_connection.cc")
    assert "Failed to connect to Wayland display" in decision.fallback_reason
    report = decision.report()
    assert report["fallback_reason"] == decision.fallback_reason
    assert report["effective"] is False


@pytest.mark.anyio
async def test_auto_does_not_fall_back_when_the_browser_did_not_blame_wayland() -> None:
    """A failure with no Wayland complaint (a locked profile, a missing
    library) would fail identically on X11: retrying doubled the launch time
    and reported an unrelated error as the Wayland reason."""
    unrelated = RuntimeError(
        "BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
        "Browser logs:\n<launching> /cache/chrome --ozone-platform=wayland --user-data-dir=/p\n"
        "[pid=1][err] The profile appears to be in use by another Chromium process\n"
    )
    opener = _Opener(unrelated, "opened")
    cleanup = AsyncMock()

    with pytest.raises(RuntimeError) as excinfo:
        await launch_execution.open_with_wayland_fallback(
            decision=_decision(), launch_kwargs=ON_ARGS, open_context=opener, cleanup=cleanup
        )
    assert excinfo.value is unrelated
    assert len(opener.calls) == 1
    cleanup.assert_awaited_once()


@pytest.mark.anyio
async def test_a_second_failure_raises_the_x11_error() -> None:
    second = RuntimeError("x11 also broken")
    opener = _Opener(RuntimeError(WAYLAND_FAILURE), second)
    cleanup = AsyncMock()

    with pytest.raises(RuntimeError, match="x11 also broken"):
        await launch_execution.open_with_wayland_fallback(
            decision=_decision(), launch_kwargs=ON_ARGS, open_context=opener, cleanup=cleanup
        )
    assert len(opener.calls) == 2
    assert cleanup.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["argument", "env"])
async def test_explicit_failure_does_not_fall_back(source: str) -> None:
    opener = _Opener(RuntimeError(WAYLAND_FAILURE))

    with pytest.raises(wayland.WaylandLaunchError) as excinfo:
        await launch_execution.open_with_wayland_fallback(
            decision=_decision(source=source, requested=True if source == "argument" else None),
            launch_kwargs=ON_ARGS,
            open_context=opener,
            cleanup=AsyncMock(),
        )
    assert len(opener.calls) == 1
    message = str(excinfo.value)
    assert "Failed to connect to Wayland display" in message
    assert "wayland_native=False" in message


@pytest.mark.anyio
async def test_explicit_failure_reports_the_browsers_line_and_chains_the_original() -> None:
    """The raw text matches driver_health's markers. The message no longer has
    to hide that line from them -- the pool confirms a dead driver with a
    liveness probe before resetting (tests/test_driver_liveness_probe.py) --
    but it still leads with the browser's own complaint, the useful part."""
    from octowright.browser_pool import driver_health

    assert driver_health.is_driver_dead_error(RuntimeError(WAYLAND_FAILURE)) is True
    original = RuntimeError(WAYLAND_FAILURE)
    opener = _Opener(original)
    with pytest.raises(wayland.WaylandLaunchError) as excinfo:
        await launch_execution.open_with_wayland_fallback(
            decision=_decision(source="argument", requested=True),
            launch_kwargs=ON_ARGS,
            open_context=opener,
            cleanup=AsyncMock(),
        )
    assert "Chromium reported: ERROR:ui/ozone/platform/wayland/host/wayland_connection.cc" in str(excinfo.value)
    assert excinfo.value.__cause__ is original


@pytest.mark.anyio
async def test_explicit_failure_without_wayland_evidence_is_raised_untouched() -> None:
    # The argv echo names --ozone-platform=wayland on every Wayland launch; it
    # is not evidence that Wayland is what failed.
    original = RuntimeError(
        "BrowserType.launch_persistent_context: profile is locked\n"
        "Call log:\n"
        "  - <launching> /cache/chrome --ozone-platform=wayland --enable-features=WaylandWindowDecorations\n"
    )
    opener = _Opener(original)
    with pytest.raises(RuntimeError) as excinfo:
        await launch_execution.open_with_wayland_fallback(
            decision=_decision(source="argument", requested=True),
            launch_kwargs=ON_ARGS,
            open_context=opener,
            cleanup=AsyncMock(),
        )
    assert excinfo.value is original


@pytest.mark.anyio
async def test_a_refused_request_is_not_retried() -> None:
    opener = _Opener(InvalidRequestError("har_path escapes"))
    with pytest.raises(InvalidRequestError):
        await launch_execution.open_with_wayland_fallback(
            decision=_decision(), launch_kwargs=ON_ARGS, open_context=opener, cleanup=AsyncMock()
        )
    assert len(opener.calls) == 1


@pytest.mark.anyio
async def test_off_decision_launches_once_as_given() -> None:
    opener = _Opener(RuntimeError("boom"))
    off = wayland.WaylandDecision(requested=None, source="auto", effective=False, reason="no_wayland_display")
    with pytest.raises(RuntimeError, match="boom"):
        await launch_execution.open_with_wayland_fallback(
            decision=off, launch_kwargs=OFF_ARGS, open_context=opener, cleanup=AsyncMock()
        )
    assert opener.calls == [OFF_ARGS]


# ─── end to end through BrowserPool.launch ───────────────────────────────────


@pytest.fixture
def wayland_desktop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(wayland, "host_platform", lambda: "linux")
    monkeypatch.delenv(wayland.WAYLAND_NATIVE_ENV, raising=False)
    (tmp_path / "wayland-0").touch()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")


def _pool_with_scripted_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *outcomes: Any
) -> tuple[BrowserPool, list]:
    pool = BrowserPool(recordings_dir=tmp_path / "rec")
    calls: list[dict[str, Any]] = []
    remaining = list(outcomes)

    async def fake_open(**kwargs: Any) -> Any:
        calls.append(kwargs["launch_kwargs"])
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def fake_post_context_setup(_pool: Any, **kwargs: Any) -> dict[str, Any]:
        return {"instance_id": kwargs["instance_id"], "kind": kwargs["kind"]}

    monkeypatch.setattr(launch_execution, "_open_browser_context", fake_open)
    monkeypatch.setattr(launch_execution, "post_context_setup", fake_post_context_setup)
    monkeypatch.setattr(pool, "_ensure_pw", AsyncMock(return_value=SimpleNamespace(chromium=object())))
    monkeypatch.setattr(pool, "_headed_chromium_args", lambda: [])
    return pool, calls


@pytest.mark.anyio
@pytest.mark.usefixtures("wayland_desktop")
async def test_pool_launch_reports_wayland_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pool, calls = _pool_with_scripted_open(monkeypatch, tmp_path, (None, object(), object(), None))

    result = await pool.launch(kind="chromium", headed=True, ephemeral=True, url="http://127.0.0.1:9/")

    assert "--ozone-platform=wayland" in calls[0]["args"]
    assert result["wayland_native"] == {
        "requested": None,
        "source": "auto",
        "effective": True,
        "reason": "wayland_session",
    }
    assert "wayland_warning" not in result


@pytest.mark.anyio
@pytest.mark.usefixtures("wayland_desktop")
async def test_pool_launch_falls_back_and_counts_engine_health_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pool, calls = _pool_with_scripted_open(
        monkeypatch, tmp_path, RuntimeError(WAYLAND_FAILURE), (None, object(), object(), None)
    )
    reset = AsyncMock()
    monkeypatch.setattr(pool, "_reset_driver", reset)

    result = await pool.launch(kind="chromium", headed=True, ephemeral=True, url="http://127.0.0.1:9/")

    assert len(calls) == 2
    assert "--ozone-platform=wayland" in calls[0]["args"]
    assert "--ozone-platform=wayland" not in calls[1]["args"]
    assert "--ozone-platform=x11" in calls[1]["args"]
    assert result["wayland_native"]["effective"] is False
    assert result["wayland_native"]["reason"] == "fallback_x11"
    assert "Wayland" in result["wayland_warning"]
    # The engine worked (on X11): one healthy outcome, no driver reset.
    assert pool.engine_health()["chromium"]["outcome"] == "ok"
    reset.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.usefixtures("wayland_desktop")
async def test_pool_launch_omits_the_report_for_firefox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pool, calls = _pool_with_scripted_open(monkeypatch, tmp_path, (None, object(), object(), None))
    monkeypatch.setattr(pool, "_ensure_pw", AsyncMock(return_value=SimpleNamespace(firefox=object())))

    result = await pool.launch(kind="firefox", headed=True, ephemeral=True, url="http://127.0.0.1:9/")

    assert "wayland_native" not in result
    assert "args" not in calls[0]


# ─── persistence ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [True, False, None])
def test_to_pool_kwargs_round_trip_is_lossless(monkeypatch: pytest.MonkeyPatch, value: bool | None) -> None:
    monkeypatch.setattr(wayland, "host_platform", lambda: "linux")
    opts = LaunchOptions(wayland_native=value)
    assert LaunchOptions.from_mapping(opts.to_pool_kwargs()) == opts


@pytest.mark.parametrize("value", [True, False, None])
def test_launch_record_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: bool | None) -> None:
    monkeypatch.setattr(wayland, "host_platform", lambda: "linux")
    log_path = tmp_path / "s.jsonl"
    recorder = Recorder(log_path)
    _record_launch_event(
        recorder,
        instance_id="i1",
        kind="chromium",
        label=None,
        profile=None,
        user_data_dir=None,
        target_url="https://x.test/",
        headless=False,
        log_viewport=None,
        stabilize=False,
        record_video=False,
        video_dir=None,
        trace=False,
        har_path=None,
        har_mode="minimal",
        har_url_filter=None,
        har_content=None,
        badge=True,
        badge_position="bottom-right",
        tile=False,
        ephemeral=False,
        session=False,
        disable_automation_controlled=False,
        wayland_native=value,
    )
    recorder.close()
    row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["wayland_native"] is value
    assert LaunchOptions.from_launch_record(row).wayland_native is value


def test_old_record_defaults_to_auto() -> None:
    assert LaunchOptions.from_launch_record({"kind": "chromium"}).wayland_native is None


@pytest.mark.parametrize("value", ["true", 1, [], {}])
def test_poisoned_record_value_is_refused(value: object) -> None:
    with pytest.raises(InvalidRequestError, match="wayland_native must be a boolean"):
        LaunchOptions.from_launch_record({"kind": "chromium", "wayland_native": value})


@pytest.mark.anyio
@pytest.mark.parametrize("value", [True, False, None])
async def test_handoff_and_relaunch_carry_the_request(value: bool | None) -> None:
    """The REQUEST is carried, so auto stays auto and re-detects. It comes
    from the session's stored launch options, which the launch validated
    strictly -- a duck-typed session attribute is never read."""
    session = SimpleNamespace(
        launch_options=LaunchOptions(kind="chromium", label="lab", profile="lab", wayland_native=value),
        har_path=None,
        protected=False,
        protected_reason="explicit",
        user_data_dir=None,
        wayland_native=object(),
        page=SimpleNamespace(url="https://x.test/now"),
        url="https://x.test/",
        launch_url="https://x.test/",
    )
    snapshot = _relaunch_snapshot_from_session(session)  # type: ignore[arg-type]

    pool = SimpleNamespace(launch=AsyncMock(return_value={"instance_id": "new"}))
    await _launch_from_snapshot(pool, snapshot, headed=True)  # type: ignore[arg-type]
    assert pool.launch.call_args.kwargs["wayland_native"] is value


# ─── status ──────────────────────────────────────────────────────────────────


def test_status_defaults_surface_the_wayland_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server.meta import octowright_status

    monkeypatch.setattr(wayland, "host_platform", lambda: "darwin")
    monkeypatch.delenv(wayland.WAYLAND_NATIVE_ENV, raising=False)
    snap = octowright_status()
    assert snap["defaults"]["wayland_native"] == {
        "setting": "auto",
        "effective_for_headed_chromium": False,
        "reason": "not_linux",
    }
