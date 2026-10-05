# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a replacement carries of the original's post-launch routes and headers.

Handoff, fluid relaunch and the driver-death / process-crash reopen open a NEW
context, so ``browser_inject_headers`` (context routes), ``browser_mock_route``
(page routes) and ``browser_set_extra_http_headers`` (page headers) were all
lost there, and crash recovery's new page in the same context lost the page
routes and page headers. Nothing said so. These pin the capture, the replay
and its warnings, and the driver-relaunch path that carries them; the live
tests (``test_route_carry_live.py``) pin what the replacement actually sends.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.browser_pool import driver_relaunch, incidents, replacement
from octowright.browser_pool.options import LaunchOptions
from octowright.session.route_carry import MockSpec, RouteCarry, replay_onto_session


class _Recorder:
    """A session stand-in whose route methods only record the call order."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail = fail

    async def inject_headers(self, url_pattern: str, headers: dict[str, str]) -> dict[str, Any]:
        self._maybe_fail("inject_headers", url_pattern)
        self.calls.append(("inject_headers", (url_pattern, headers)))
        return {"ok": True}

    async def mock_route(self, url_pattern: str, **kwargs: Any) -> dict[str, Any]:
        self._maybe_fail("mock_route", url_pattern)
        self.calls.append(("mock_route", (url_pattern, kwargs)))
        return {"ok": True}

    async def set_extra_http_headers(self, headers: dict[str, str]) -> dict[str, Any]:
        self._maybe_fail("set_extra_http_headers", "")
        self.calls.append(("set_extra_http_headers", headers))
        return {"ok": True}

    def _maybe_fail(self, kind: str, pattern: str) -> None:
        if self.fail == f"{kind}:{pattern}":
            raise RuntimeError("Fixture-Not-A-Real-Secret-in-the-error")


def _spec(page: Any, **over: Any) -> MockSpec:
    base: dict[str, Any] = {"status": 200, "body": "{}", "content_type": "application/json", "headers": {}}
    base.update(over)
    return MockSpec(page=page, **base)


def _original(**over: Any) -> SimpleNamespace:
    page = object()
    base: dict[str, Any] = {
        "page": page,
        "_injected_headers": {},
        "_active_routes": {},
        "_mock_specs": {},
        "_page_extra_headers": None,
        "_page_extra_headers_page": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_capture_keeps_registration_order() -> None:
    session = _original()
    session._injected_headers = {"**/b/**": {"X-B": "2"}, "**/a/**": {"X-A": "1"}}
    session._mock_specs = {"**/z": _spec(session.page, status=201), "**/y": _spec(session.page)}
    session._active_routes = dict.fromkeys(session._mock_specs, object())

    carry = RouteCarry.of(session)

    assert [pattern for pattern, _ in carry.injected] == ["**/b/**", "**/a/**"]
    assert [pattern for pattern, _ in carry.mocks] == ["**/z", "**/y"]
    assert carry.not_carried == ()


def test_capture_copies_the_mappings() -> None:
    """The original keeps changing after capture -- or is torn down."""
    session = _original(_injected_headers={"**/a": {"X-A": "1"}})

    carry = RouteCarry.of(session)
    session._injected_headers["**/a"]["X-A"] = "changed"

    assert carry.injected == (("**/a", {"X-A": "1"}),)


def test_page_headers_are_carried_only_from_the_page_the_replacement_reopens() -> None:
    session = _original(_page_extra_headers={"X-Page": "1"})
    session._page_extra_headers_page = session.page
    assert RouteCarry.of(session).page_headers == {"X-Page": "1"}

    session._page_extra_headers_page = object()  # set on a popup, since switched away from
    carry = RouteCarry.of(session)
    assert carry.page_headers is None
    assert carry.not_carried and "page-level" in carry.not_carried[0]


def test_a_mock_on_another_page_is_named_not_dropped() -> None:
    session = _original()
    session._mock_specs = {"**/popup-only": _spec(object())}
    session._active_routes = {"**/popup-only": object()}

    carry = RouteCarry.of(session)

    assert carry.mocks == ()
    assert len(carry.not_carried) == 1 and "**/popup-only" in carry.not_carried[0]


def test_a_mock_whose_response_was_never_kept_is_named_not_dropped() -> None:
    """A handler with no spec cannot be rebuilt in another context."""
    session = _original(_active_routes={"**/opaque": object()})

    carry = RouteCarry.of(session)

    assert carry.mocks == ()
    assert len(carry.not_carried) == 1 and "**/opaque" in carry.not_carried[0]


def test_a_session_with_nothing_set_carries_nothing() -> None:
    carry = RouteCarry.of(_original())

    assert not carry
    assert RouteCarry.of(SimpleNamespace()) == RouteCarry()


async def test_replay_goes_through_the_session_methods_in_order() -> None:
    session = _original(_injected_headers={"**/b": {"X-B": "2"}, "**/a": {"X-A": "1"}})
    session._mock_specs = {"**/m": _spec(session.page, status=418, body="tea")}
    session._active_routes = {"**/m": object()}
    session._page_extra_headers = {"X-Page": "p"}
    session._page_extra_headers_page = session.page
    target = _Recorder()

    warnings = await replay_onto_session(target, RouteCarry.of(session))

    assert warnings == []
    assert target.calls == [
        ("inject_headers", ("**/b", {"X-B": "2"})),
        ("inject_headers", ("**/a", {"X-A": "1"})),
        (
            "mock_route",
            ("**/m", {"status": 418, "body": "tea", "content_type": "application/json", "headers": {}}),
        ),
        ("set_extra_http_headers", {"X-Page": "p"}),
    ]


async def test_a_replay_that_fails_is_a_warning_and_the_rest_still_replays() -> None:
    session = _original(_injected_headers={"**/bad": {"X-B": "2"}, "**/good": {"X-A": "1"}})
    target = _Recorder(fail="inject_headers:**/bad")

    warnings = await replay_onto_session(target, RouteCarry.of(session))

    assert target.calls == [("inject_headers", ("**/good", {"X-A": "1"}))]
    assert len(warnings) == 1 and "**/bad" in warnings[0]
    # The error's text is not echoed: header values must not reach status.
    assert "Fixture-Not-A-Real-Secret" not in warnings[0]


async def test_not_carried_entries_lead_the_warnings() -> None:
    session = _original(_active_routes={"**/opaque": object()})

    warnings = await replay_onto_session(_Recorder(), RouteCarry.of(session))

    assert len(warnings) == 1 and "**/opaque" in warnings[0]


async def test_launch_replacement_exposes_the_routes_only_while_it_launches() -> None:
    carry = RouteCarry(injected=(("**/a", {"X-A": "1"}),))
    seen: list[Any] = []

    async def _launch(**_kw: Any) -> dict[str, Any]:
        seen.append(replacement.pending_route_carry())
        return {"instance_id": "new"}

    await replacement.launch_replacement(_launch, {"kind": "chromium"}, routes=carry)

    assert seen == [carry]
    assert replacement.pending_route_carry() is None


async def test_the_channel_retry_carries_the_routes_too() -> None:
    carry = RouteCarry(injected=(("**/a", {"X-A": "1"}),))
    seen: list[Any] = []

    async def _launch(**kw: Any) -> dict[str, Any]:
        seen.append(replacement.pending_route_carry())
        if kw.get("channel"):
            raise RuntimeError("Chromium distribution 'msedge' is not found at /nowhere")
        return {"instance_id": "new"}

    _, dropped = await replacement.launch_replacement(_launch, {"channel": "msedge"}, routes=carry)

    assert dropped == "msedge" and seen == [carry, carry]
    assert replacement.pending_route_carry() is None


def test_the_replacement_source_captures_the_routes() -> None:
    session = _original(_injected_headers={"**/a": {"X-A": "1"}})
    session.launch_options = LaunchOptions(kind="chromium", headed=False, ephemeral=True)

    source = replacement.ReplacementSource.of(session)

    assert source.routes.injected == (("**/a", {"X-A": "1"}),)


class TestDriverRelaunch:
    """The driver-death / process-crash reopen hands the routes to the launch
    and reports what the replacement could not carry on the lost record."""

    @pytest.fixture(autouse=True)
    def _reset(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright import session_manifest

        driver_relaunch.reset()
        incidents.reset()
        monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", tmp_path / "session-manifest.json")
        monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "new-id")

    def _pool(self, session: SimpleNamespace, *, warnings: list[str] | None = None) -> Any:
        class _Pool:
            def __init__(self) -> None:
                self._sessions = {session.instance_id: session}
                self._sessions_lock = asyncio.Lock()
                self._recently_evicted: dict[str, bool] = {}
                self._driver_restarts = 1
                self.carried: list[Any] = []

            def iter_sessions(self) -> tuple[Any, ...]:
                return tuple(self._sessions.values())

            def maybe_get(self, instance_id: str) -> Any:
                return self._sessions.get(instance_id)

            def _accept_external_close_nowait(self, instance_id: str, **_kw: Any) -> None:
                self._sessions.pop(instance_id, None)

            async def launch(self, **_kw: Any) -> dict[str, Any]:
                self.carried.append(replacement.pending_route_carry())
                self._sessions["new1"] = SimpleNamespace(instance_id="new1")
                result: dict[str, Any] = {"instance_id": "new1"}
                if warnings:
                    result["route_warnings"] = list(warnings)
                return result

        return _Pool()

    def _lost(self) -> SimpleNamespace:
        session = _original(_injected_headers={"**/a": {"X-A": "1"}})
        session.__dict__.update(
            instance_id="old",
            kind="chromium",
            label=None,
            profile=None,
            url="https://example.com/old",
            user_data_dir=None,
            launch_options=LaunchOptions(kind="chromium", headed=False, ephemeral=True),
        )
        return session

    def test_driver_death_reopen_carries_the_routes(self) -> None:
        pool = self._pool(self._lost())

        async def _run() -> None:
            task = driver_relaunch.on_driver_reset(pool, reason="driver died")
            assert task is not None
            await task

        asyncio.run(_run())

        (carry,) = pool.carried
        assert carry.injected == (("**/a", {"X-A": "1"}),)
        (record,) = driver_relaunch.recent_lost()
        assert record["relaunched_to"] == "new1" and "route_warnings" not in record

    def test_what_the_reopen_could_not_carry_is_on_the_lost_record(self) -> None:
        pool = self._pool(self._lost(), warnings=["mock_route '**/x' was not carried"])

        async def _run() -> None:
            task = driver_relaunch.on_driver_reset(pool, reason="driver died")
            assert task is not None
            await task

        asyncio.run(_run())

        (record,) = driver_relaunch.recent_lost()
        assert record["route_warnings"] == ["mock_route '**/x' was not carried"]

    def test_a_process_crash_reopen_puts_them_on_its_incident_too(self) -> None:
        session = self._lost()
        session.log_path = "/tmp/fixture.jsonl"
        session._process_crash_incident = {"ts": 1.0, "outcome": "relaunching"}
        pool = self._pool(session, warnings=["mock_route '**/x' was not carried"])

        async def _run() -> None:
            task = driver_relaunch.on_browser_process_crash(pool, session, None)
            assert task is not None
            await task

        asyncio.run(_run())

        assert pool.carried[0].injected == (("**/a", {"X-A": "1"}),)
        assert session._process_crash_incident["route_warnings"] == ["mock_route '**/x' was not carried"]
        (record,) = driver_relaunch.recent_lost()
        assert record["route_warnings"] == ["mock_route '**/x' was not carried"]
