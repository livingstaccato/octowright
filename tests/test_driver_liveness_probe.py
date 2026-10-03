# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A launch failure that merely *reads* like driver death must not reset the driver.

``driver_health.is_driver_dead_error`` matches text, and one of its markers
("Target page, context or browser has been closed") is also what Playwright
raises when ONE browser process exits during launch. Resetting on that text
stopped the shared driver and evicted every live browser for one browser's
problem. The pool now confirms the verdict with ``driver_health.driver_is_alive``
before resetting. These tests drive the real probe against a stand-in for the
Playwright internals it reads; the real-driver measurements are in
``test_launch_death_keeps_driver_live.py``.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from playwright._impl._errors import Error as PlaywrightError
from playwright._impl._errors import TargetClosedError

from octowright.browser_pool import driver_health, driver_relaunch
from octowright.browser_pool.pool import BrowserPool

#: What Playwright raises when one chromium exits during launch (measured).
LAUNCH_DEATH = "BrowserType.launch: Target page, context or browser has been closed\nBrowser logs:\n\n<launching> ..."


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Channel:
    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    async def send(self, method: str, timeout_calculator: Any, params: dict[str, Any]) -> None:
        self.calls.append((method, timeout_calculator, params))
        if self.behaviour == "ok":
            return None
        if self.behaviour == "transport_closed":
            raise Exception("Channel.send: Connection closed while reading from the driver")
        if self.behaviour == "protocol_error":
            raise PlaywrightError("localUtils.traceDiscarded: some protocol-level refusal")
        if self.behaviour == "hang_and_resist_cancel":
            # Playwright's own behaviour on a hung driver: cancelling a channel
            # send runs Connection._abort, which waits for the driver to answer
            # the abort. A probe that awaits the cancellation hangs with it.
            # (Finite here only so event-loop teardown, which cancels once,
            # can finish.)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(2.0)
                raise
        raise AssertionError(self.behaviour)


def _fake_pw(
    behaviour: str = "ok",
    *,
    closed_error: Exception | None = None,
    returncode: int | None = None,
    transport_error: bool = False,
) -> tuple[Any, _Channel]:
    loop = asyncio.get_running_loop()
    on_error_future: asyncio.Future[None] = loop.create_future()
    if transport_error:
        on_error_future.set_exception(Exception("Connection closed while reading from the driver"))
        on_error_future.exception()  # consumed; mirrors Playwright having observed it
    channel = _Channel(behaviour)
    transport = SimpleNamespace(on_error_future=on_error_future, _proc=SimpleNamespace(returncode=returncode))
    connection = SimpleNamespace(
        _closed_error=closed_error,
        _transport=transport,
        local_utils=SimpleNamespace(_channel=channel),
    )
    pw = SimpleNamespace(_impl_obj=SimpleNamespace(_connection=connection), stop=_noop)
    return pw, channel


async def _noop() -> None:
    return None


# ─── the probe itself ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_probe_reports_a_responsive_driver_alive() -> None:
    pw, channel = _fake_pw("ok")
    assert await driver_health.driver_is_alive(pw) is True
    # The no-op round trip: traceDiscarded with an empty id returns before any
    # lookup in the driver, so it can never touch a real tracing session.
    assert channel.calls == [("traceDiscarded", None, {"stacksId": ""})]


@pytest.mark.anyio
async def test_probe_reports_a_closed_transport_dead() -> None:
    pw, _ = _fake_pw("transport_closed")
    assert await driver_health.driver_is_alive(pw) is False


@pytest.mark.anyio
async def test_probe_counts_a_protocol_error_reply_as_alive() -> None:
    """A driver that answered -- even with an error -- is alive."""
    pw, _ = _fake_pw("protocol_error")
    assert await driver_health.driver_is_alive(pw) is True


@pytest.mark.anyio
async def test_probe_reports_target_closed_from_the_send_dead() -> None:
    pw, channel = _fake_pw("ok")

    async def closed(*_a: Any) -> None:
        raise TargetClosedError()

    channel.send = closed  # type: ignore[method-assign]
    assert await driver_health.driver_is_alive(pw) is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "flags",
    [
        {"closed_error": TargetClosedError()},
        {"returncode": -9},
        {"transport_error": True},
    ],
    ids=["stopped", "process_exited", "transport_error"],
)
async def test_probe_trusts_authoritative_flags_without_a_round_trip(flags: dict[str, Any]) -> None:
    pw, channel = _fake_pw("ok", **flags)
    assert await driver_health.driver_is_alive(pw) is False
    assert channel.calls == []


@pytest.mark.anyio
async def test_probe_timeout_is_dead_and_bounded_even_when_cancel_hangs() -> None:
    """A hung driver is dead, and the probe must return on its own bound --
    ``asyncio.wait_for`` would await Playwright's cancellation, which waits for
    the very driver that is not answering."""
    pw, _ = _fake_pw("hang_and_resist_cancel")
    started = time.monotonic()
    assert await driver_health.driver_is_alive(pw, timeout=0.05) is False
    assert time.monotonic() - started < 1.0


@pytest.mark.anyio
async def test_probe_without_a_driver_is_dead() -> None:
    assert await driver_health.driver_is_alive(None) is False


@pytest.mark.anyio
async def test_probe_falls_back_to_dead_when_internals_are_missing() -> None:
    """A Playwright upgrade that moves these internals must degrade to the old
    behaviour (reset on the text verdict), not to never resetting a dead driver."""
    assert await driver_health.driver_is_alive(SimpleNamespace()) is False


# ─── the pool's use of it ────────────────────────────────────────────────────


def _pool_with_failing_launch(
    monkeypatch: pytest.MonkeyPatch, pw: Any, error: BaseException
) -> tuple[BrowserPool, dict[str, int]]:
    pool = BrowserPool()
    pool._pw = pw
    # A live browser that a reset would evict.
    pool._sessions["bystander"] = SimpleNamespace(instance_id="bystander")  # type: ignore[assignment]
    calls = {"n": 0, "evictions": 0}

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise error
        return {"instance_id": "retried"}

    def _on_driver_reset(p: BrowserPool, *, reason: str | None = None) -> None:
        # Stands in for the eviction of every live session (tested in
        # test_driver_relaunch.py); here only whether it happens matters.
        calls["evictions"] += 1
        p._sessions.clear()

    monkeypatch.setattr(pool, "_launch_impl", _impl)
    monkeypatch.setattr(driver_relaunch, "on_driver_reset", _on_driver_reset)
    return pool, calls


@pytest.mark.anyio
async def test_launch_death_with_a_live_driver_does_not_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    pw, _ = _fake_pw("ok")
    error = TargetClosedError(LAUNCH_DEATH)
    pool, calls = _pool_with_failing_launch(monkeypatch, pw, error)

    with pytest.raises(TargetClosedError) as excinfo:
        await pool.launch(kind="chromium")

    assert excinfo.value is error  # the original, unchanged
    assert calls["n"] == 1  # no retry
    assert calls["evictions"] == 0
    assert pool.driver_restart_count() == 0
    assert pool._pw is pw
    assert "bystander" in pool._sessions
    assert pool.engine_health()["chromium"] == {
        "outcome": "error",
        "at": pool.engine_health()["chromium"]["at"],
        "error": "TargetClosedError",
    }


@pytest.mark.anyio
async def test_launch_death_with_a_dead_driver_resets_and_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    pw, _ = _fake_pw("transport_closed")
    pool, calls = _pool_with_failing_launch(monkeypatch, pw, TargetClosedError(LAUNCH_DEATH))

    out = await pool.launch(kind="chromium")

    assert out == {"instance_id": "retried"}
    assert calls["n"] == 2
    assert calls["evictions"] == 1
    assert pool.driver_restart_count() == 1
    assert pool._pw is None


@pytest.mark.anyio
async def test_launch_death_with_a_hung_driver_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    pw, _ = _fake_pw("hang_and_resist_cancel")
    monkeypatch.setattr(driver_health, "DRIVER_PROBE_TIMEOUT_SECONDS", 0.05)
    pool, calls = _pool_with_failing_launch(monkeypatch, pw, RuntimeError("BrowserType.launch: Connection closed"))

    out = await pool.launch(kind="chromium")

    assert out == {"instance_id": "retried"}
    assert calls["n"] == 2
    assert pool.driver_restart_count() == 1
