# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A replacement launch keeps the original's browser ``channel``.

Handoff, fluid relaunch and the driver-death relaunch reopened a browser
launched with ``channel="chrome"`` on Playwright's bundled Chromium, because
``channel`` was in ``replacement.NOT_CARRIED``. The stated reason -- the named
channel may have become unavailable since -- is now handled where it happens:
a replacement whose channel cannot be found relaunches on the bundled build
and says so, rather than every replacement silently switching browsers.

The channel is still never read back from a JSONL recording: a recording is
untrusted input, and only the live session's own options are carried.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

from octowright.browser_pool import replacement
from octowright.browser_pool.options import LaunchOptions
from octowright.browser_pool.pool import BrowserPool


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _channel_options(channel: str = "chrome") -> LaunchOptions:
    return LaunchOptions(kind="chromium", headed=False, label="lab", profile="lab", channel=channel)


def _not_found(channel: str) -> PlaywrightError:
    # Playwright's own wording (coreBundle.js, _createChromiumChannel).
    return PlaywrightError(
        f"BrowserType.launch_persistent_context: Chromium distribution '{channel}' is not found at "
        f'/opt/microsoft/{channel}/{channel}\nRun "playwright install {channel}"'
    )


def test_channel_is_not_named_as_not_carried() -> None:
    assert "channel" not in replacement.NOT_CARRIED


def test_the_replacement_kwargs_carry_the_channel() -> None:
    source = replacement.ReplacementSource(
        options=_channel_options(), launch_url=None, protected=False, protected_reason="unprotected"
    )
    assert source.launch_kwargs(url="https://x.test")["channel"] == "chrome"


def test_a_recording_never_supplies_the_channel() -> None:
    """Only the live session's options are carried; a JSONL record is untrusted."""
    record = {"kind": "chromium", "url": "https://x.test/", "headed": False, "channel": "chrome"}
    assert LaunchOptions.from_launch_record(record).channel is None


def _handoff_pool(monkeypatch: pytest.MonkeyPatch, options: LaunchOptions) -> BrowserPool:
    from tests.test_handoff import _fake_source, _pop_manifest_noop

    _pop_manifest_noop(monkeypatch)
    pool = BrowserPool()
    source = _fake_source(instance_id="src", profile="lab", label="lab", user_data_dir="/tmp/lab")
    source.launch_options = options
    pool._sessions["src"] = source
    return pool


async def _replace(pool: BrowserPool, path: str) -> dict[str, Any]:
    if path == "handoff":
        return await pool.handoff("src")
    return await pool.relaunch_fluid("src")


@pytest.mark.parametrize("path", ["handoff", "relaunch_fluid"])
@pytest.mark.anyio
async def test_handoff_and_fluid_relaunch_carry_the_channel(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    pool = _handoff_pool(monkeypatch, _channel_options())
    launched: list[dict[str, Any]] = []

    async def fake_launch(**kwargs: Any) -> dict[str, Any]:
        launched.append(kwargs)
        return {"instance_id": "new"}

    monkeypatch.setattr(pool, "launch", fake_launch)
    result = await _replace(pool, path)

    assert [call["channel"] for call in launched] == ["chrome"]
    assert "warnings" not in result


@pytest.mark.parametrize("path", ["handoff", "relaunch_fluid"])
@pytest.mark.anyio
async def test_an_unavailable_channel_falls_back_to_the_bundled_build_and_says_so(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    pool = _handoff_pool(monkeypatch, _channel_options("msedge"))
    launched: list[dict[str, Any]] = []

    async def fake_launch(**kwargs: Any) -> dict[str, Any]:
        launched.append(kwargs)
        if kwargs.get("channel"):
            raise _not_found(kwargs["channel"])
        return {"instance_id": "new"}

    monkeypatch.setattr(pool, "launch", fake_launch)
    result = await _replace(pool, path)

    assert [call["channel"] for call in launched] == ["msedge", None]
    # Nothing else departs from the original: only the channel is dropped.
    assert {k: v for k, v in launched[0].items() if k != "channel"} == {
        k: v for k, v in launched[1].items() if k != "channel"
    }
    assert result["new_instance_id"] == "new"
    (warning,) = result["warnings"]
    assert "msedge" in warning
    assert "bundled" in warning


@pytest.mark.parametrize("path", ["handoff", "relaunch_fluid"])
@pytest.mark.anyio
async def test_any_other_launch_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    pool = _handoff_pool(monkeypatch, _channel_options())
    launched: list[dict[str, Any]] = []

    async def fake_launch(**kwargs: Any) -> dict[str, Any]:
        launched.append(kwargs)
        raise PlaywrightError("BrowserType.launch_persistent_context: Target page, context or browser has been closed")

    monkeypatch.setattr(pool, "launch", fake_launch)
    with pytest.raises(PlaywrightError, match="has been closed"):
        await _replace(pool, path)
    assert len(launched) == 1


@pytest.mark.anyio
async def test_a_missing_channel_other_than_the_requested_one_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _handoff_pool(monkeypatch, _channel_options("chrome"))
    launched: list[dict[str, Any]] = []

    async def fake_launch(**kwargs: Any) -> dict[str, Any]:
        launched.append(kwargs)
        raise _not_found("msedge")

    monkeypatch.setattr(pool, "launch", fake_launch)
    with pytest.raises(PlaywrightError, match="msedge"):
        await pool.handoff("src")
    assert len(launched) == 1


@pytest.mark.anyio
async def test_the_driver_death_relaunch_carries_the_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.browser_pool import driver_relaunch
    from tests.test_driver_relaunch import _FakePool, _session

    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "new-id")
    lost = _session("old", label="lab", profile="lab", launch_options=_channel_options())
    pool = _FakePool([lost])
    task = driver_relaunch.on_driver_reset(pool, reason="test")
    assert task is not None
    await task

    (kwargs,) = pool.launched
    assert kwargs["channel"] == "chrome"


@pytest.mark.anyio
async def test_the_driver_death_relaunch_falls_back_when_the_channel_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.browser_pool import driver_relaunch
    from tests.test_driver_relaunch import _FakePool, _session

    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "new-id")
    lost = _session("old", label="lab", profile="lab", launch_options=_channel_options("msedge"))
    pool = _FakePool([lost])
    real_launch = pool.launch

    async def launch(**kwargs: Any) -> dict[str, Any]:
        if kwargs.get("channel"):
            pool.launched.append(kwargs)
            raise _not_found(kwargs["channel"])
        return await real_launch(**kwargs)

    monkeypatch.setattr(pool, "launch", launch)
    warned: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def warning(self, event: str, **kw: Any) -> None:
            warned.append((event, kw))

        def __getattr__(self, _name: str) -> Any:
            return lambda *_a, **_kw: None

    monkeypatch.setattr(replacement, "log", _Log())
    task = driver_relaunch.on_driver_reset(pool, reason="test")
    assert task is not None
    await task

    assert [call["channel"] for call in pool.launched] == ["msedge", None]
    (record,) = driver_relaunch.recent_lost()[-1:]
    assert record["relaunched_to"] == "new1"
    assert record["channel_dropped"] == "msedge"
    assert [event for event, _ in warned] == ["octowright.browser.replacement.channel_dropped"]
    assert warned[0][1]["channel"] == "msedge"
