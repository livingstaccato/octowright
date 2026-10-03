# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A replacement launch carries what the original was launched with.

Handoff, fluid relaunch and the driver-death relaunch each rebuilt the
replacement's options by hand from session attributes, and each list had
drifted: launch extra headers and their URL scoping, an explicit base_url,
disable_gpu, the HAR mode/filter/content, the viewport, the badge, and (on the
driver path) protected, disable_automation_controlled, wayland_native and
headed were silently lost. An anonymous ``session=True`` browser also lost its
whole state, because its tmpdir is keyed by instance_id and the replacement
got a new one.

The session now keeps the ``LaunchOptions`` it was launched with, and
``replacement.ReplacementSource`` derives the replacement from them: every
field is carried unless it is named, with a reason, in ``NOT_CARRIED`` or
``SET_BY_REPLACEMENT``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.browser_pool import replacement
from octowright.browser_pool.options import CALLER_SETTABLE_FIELDS, LaunchOptions
from octowright.browser_pool.pool import BrowserPool


def _existing(path: Path) -> Path:
    path.write_text("{}")
    return path


def _every_field_set(tmp_path: Path) -> LaunchOptions:
    """Every field away from its default, so a dropped one cannot hide behind
    a default that happens to match."""
    exe = tmp_path / "chrome"
    exe.write_text("")
    return LaunchOptions(
        kind="chromium",
        url="https://launched.test/start",
        headed=True,
        label="lab",
        viewport_w=1024,
        viewport_h=768,
        profile=None,
        base_url="https://dev.test",
        stabilize=True,
        record_video=True,
        trace=True,
        har=True,
        har_path=str(_existing(tmp_path / "a.har")),
        har_mode="full",
        har_url_filter="octowright.com",
        har_content="embed",
        badge=False,
        badge_position="top-left",
        tile=True,
        ephemeral=False,
        session=True,
        session_key="anon-key",
        protected=True,
        protected_reason="explicit",
        channel="chrome",
        executable_path=str(exe),
        launch_args=["--foo"],
        extra_http_headers={"X-Env": "staging"},
        extra_http_headers_urls=["**/api/**"],
        disable_gpu=True,
        disable_automation_controlled=True,
        wayland_native=False,
        trusted_launch_url="https://origin.test/",
    )


def test_the_fixture_sets_every_launch_option(tmp_path: Path) -> None:
    """A new LaunchOptions field fails here until the fixture -- and so the
    carry test below -- covers it."""
    original = _every_field_set(tmp_path)
    defaults = LaunchOptions()
    unset = [f.name for f in dataclasses.fields(LaunchOptions) if getattr(original, f.name) == getattr(defaults, f.name)]
    # kind stays chromium (the chromium-only options need it), profile None and
    # ephemeral False (both exclusive with session=True), and protected_reason
    # is an output, not a launch option.
    assert unset == ["kind", "profile", "ephemeral", "protected_reason"]


def test_every_option_is_carried_or_named_as_not_carried(tmp_path: Path) -> None:
    original = _every_field_set(tmp_path)
    source = replacement.ReplacementSource(
        options=original,
        launch_url="https://origin.test/",
        protected=True,
        protected_reason="headed_default",
        har_path=Path(original.har_path or ""),
    )
    kwargs = source.launch_kwargs(url="https://now.test/page")

    assert set(kwargs) == set(CALLER_SETTABLE_FIELDS)
    exempt = set(replacement.NOT_CARRIED) | set(replacement.SET_BY_REPLACEMENT)
    assert exempt <= set(CALLER_SETTABLE_FIELDS), "an exemption names no launch option"
    dropped = sorted(name for name in CALLER_SETTABLE_FIELDS - exempt if kwargs[name] != getattr(original, name))
    assert dropped == []
    for name in replacement.NOT_CARRIED:
        assert kwargs[name] == getattr(LaunchOptions(), name), name
    assert kwargs["url"] == "https://now.test/page"
    assert kwargs["trusted_launch_url"] == "https://origin.test/"
    assert kwargs["har"] is True
    assert kwargs["har_path"] != original.har_path


def test_headed_is_carried_unless_the_caller_overrides_it(tmp_path: Path) -> None:
    source = replacement.ReplacementSource(
        options=LaunchOptions(headed=False), launch_url=None, protected=False, protected_reason="unprotected"
    )
    assert source.launch_kwargs(url="https://x.test")["headed"] is False
    assert source.launch_kwargs(url="https://x.test", headed=True)["headed"] is True


def test_the_kwargs_are_accepted_by_a_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_EXECUTABLE_PATH", "1")
    source = replacement.ReplacementSource.of(_session_from(_every_field_set(tmp_path)))
    LaunchOptions.from_mapping(source.launch_kwargs(url="https://now.test/"))


def _session_from(options: LaunchOptions, **over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "launch_options": options,
        "launch_url": "https://origin.test/",
        "protected": bool(options.protected),
        "protected_reason": "explicit",
        "har_path": Path(options.har_path) if options.har_path else None,
    }
    base.update(over)
    return SimpleNamespace(**base)


# --- the stored options --------------------------------------------------------


async def test_an_anonymous_session_replacement_reuses_the_original_directory() -> None:
    """The tmpdir of an anonymous ``session=True`` browser is keyed by its
    instance_id; the replacement has a new one, so without the original's key
    it opened an empty profile and lost every cookie."""
    pool = BrowserPool()
    original = LaunchOptions(session=True)
    original_dir = await pool._resolve_session_dir(True, original, "old-iid", "chromium")

    stored = replacement.recorded_launch_options(original, instance_id="old-iid", profile=None, headless=True)
    assert stored.session_key == "old-iid"
    assert stored.headed is False

    source = replacement.ReplacementSource.of(_session_from(stored))
    kwargs = source.launch_kwargs(url="https://x.test")
    replacement_dir = await pool._resolve_session_dir(True, LaunchOptions(**kwargs), "new-iid", "chromium")
    assert replacement_dir == original_dir


def test_a_named_session_keeps_its_label_as_the_key() -> None:
    stored = replacement.recorded_launch_options(
        LaunchOptions(session=True, label="shared"), instance_id="iid", profile=None, headless=False
    )
    assert stored.session_key == "shared"
    assert stored.session_name("other") == "shared"


def test_a_non_session_launch_has_no_session_key() -> None:
    stored = replacement.recorded_launch_options(
        LaunchOptions(label="lab"), instance_id="iid", profile="lab", headless=False
    )
    assert stored.session_key is None
    assert stored.profile == "lab"


def test_session_key_requires_session() -> None:
    from octowright.request_errors import InvalidRequestError

    with pytest.raises(InvalidRequestError, match="session_key"):
        LaunchOptions.from_mapping({"session_key": "x"})
    with pytest.raises(InvalidRequestError, match="session_key"):
        LaunchOptions.from_mapping({"session": True, "session_key": 3})


def test_session_key_is_never_read_from_a_recording() -> None:
    record = {"kind": "chromium", "url": "https://x.test", "session": True, "session_key": "victim"}
    assert LaunchOptions.from_launch_record(record).session_key is None


def test_a_roster_spec_may_not_set_session_key() -> None:
    from octowright.browser_pool.roster import roster_launch_kwargs
    from octowright.request_errors import InvalidRequestError

    with pytest.raises(InvalidRequestError, match="session_key"):
        roster_launch_kwargs({"session": True, "session_key": "victim"})


# --- every replacement path builds from the stored options ---------------------


def _rich_options() -> LaunchOptions:
    return LaunchOptions(
        kind="chromium",
        headed=False,
        label="lab",
        profile="lab",
        viewport_w=900,
        viewport_h=700,
        base_url="https://dev.test",
        har_mode="full",
        badge=False,
        protected=True,
        extra_http_headers={"X-Env": "staging"},
        extra_http_headers_urls=["**/api/**"],
        disable_gpu=True,
        disable_automation_controlled=True,
        wayland_native=False,
    )


_CARRIED = {
    "viewport_w": 900,
    "viewport_h": 700,
    "base_url": "https://dev.test",
    "har_mode": "full",
    "badge": False,
    "extra_http_headers": {"X-Env": "staging"},
    "extra_http_headers_urls": ["**/api/**"],
    "disable_gpu": True,
    "disable_automation_controlled": True,
    "wayland_native": False,
}


def _assert_carried(kwargs: dict[str, Any]) -> None:
    assert {name: kwargs.get(name) for name in _CARRIED} == _CARRIED


@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.parametrize("path", ["handoff", "relaunch_fluid"])
@pytest.mark.anyio
async def test_handoff_and_fluid_relaunch_carry_the_launch_options(
    monkeypatch: pytest.MonkeyPatch, anyio_backend: str, path: str
) -> None:
    from tests.test_handoff import _fake_source, _pop_manifest_noop

    _pop_manifest_noop(monkeypatch)
    pool = BrowserPool()
    source = _fake_source(instance_id="src", profile="lab", label="lab", user_data_dir="/tmp/lab")
    source.launch_options = _rich_options()
    pool._sessions["src"] = source
    launched: dict[str, Any] = {}

    async def fake_launch(**kwargs: Any) -> dict[str, Any]:
        launched.update(kwargs)
        return {"instance_id": "new"}

    monkeypatch.setattr(pool, "launch", fake_launch)
    if path == "handoff":
        await pool.handoff("src")
        assert launched["headed"] is False  # kept, not re-defaulted
        _assert_carried(launched)
    else:
        await pool.relaunch_fluid("src")
        # Fluid's deliberate departures: a headed window the viewport follows.
        assert {k: launched[k] for k in replacement.FLUID_OVERRIDES} == replacement.FLUID_OVERRIDES
        _assert_carried({**launched, "viewport_w": 900, "viewport_h": 700})
    assert launched["profile"] == "lab"


@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.anyio
async def test_the_driver_death_relaunch_carries_the_launch_options(
    monkeypatch: pytest.MonkeyPatch, anyio_backend: str
) -> None:
    from octowright.browser_pool import driver_relaunch
    from tests.test_driver_relaunch import _FakePool, _session

    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", "new-id")
    lost = _session("old", label="lab", profile="lab", launch_options=_rich_options(), protected=True)
    pool = _FakePool([lost])
    task = driver_relaunch.on_driver_reset(pool, reason="test")
    assert task is not None
    await task

    (kwargs,) = pool.launched
    _assert_carried(kwargs)
    assert kwargs["headed"] is False
    assert kwargs["protected"] is True
    assert kwargs["url"] == lost.url


@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.anyio
async def test_a_launched_session_keeps_its_launch_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, anyio_backend: str
) -> None:
    """The stored copy is written at launch, with the resolved headedness and
    the session directory's key."""
    from octowright.browser_pool import launch_execution

    captured: dict[str, Any] = {}

    async def fake_post_context_setup(_pool: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"instance_id": kwargs["instance_id"]}

    async def fake_open(**_: Any) -> tuple[Any, Any, Any, str | None]:
        return None, object(), object(), None

    monkeypatch.setattr(launch_execution, "post_context_setup", fake_post_context_setup)
    monkeypatch.setattr(launch_execution, "_open_browser_context", fake_open)
    pool = BrowserPool(recordings_dir=tmp_path)

    class _PW:
        chromium = object()

    async def ensure_pw() -> Any:
        return _PW()

    monkeypatch.setattr(pool, "_ensure_pw", ensure_pw)
    result = await launch_execution.launch_profile_locked(
        pool, LaunchOptions(session=True, headed=None), None, None, "https://x.test"
    )
    stored = captured["launch_options"]
    assert stored.session_key == result["instance_id"]
    assert isinstance(stored.headed, bool)
    assert stored.protected is not None


# --- live: an anonymous session's state survives a handoff ---------------------


@pytest.fixture
def _origin() -> Any:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"<!doctype html><title>state</title><p>state</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.live_browser
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.anyio
async def test_live_an_anonymous_session_keeps_its_state_across_handoff(
    tmp_path: Path, _origin: str, anyio_backend: str
) -> None:
    """Real Chromium, headless. Measured before the fix: the replacement opened
    a fresh tmpdir keyed by its own instance_id, and the value was gone."""
    from octowright.browser_pool.lifecycle import shutdown_pool

    pool = BrowserPool(recordings_dir=tmp_path / "rec")
    try:
        try:
            launched = await pool.launch(kind="chromium", headed=False, session=True, url=_origin)
        except Exception as exc:  # pragma: no cover - host without chromium
            pytest.skip(f"chromium unavailable: {exc}")
        original = pool.get(launched["instance_id"])
        await original.page.evaluate("localStorage.setItem('kept', 'yes')")

        result = await pool.handoff(launched["instance_id"])

        assert result["old_closed"] is True
        fresh = pool.get(result["new_instance_id"])
        assert fresh.user_data_dir == original.user_data_dir
        assert await fresh.page.evaluate("localStorage.getItem('kept')") == "yes"
    finally:
        await shutdown_pool(pool)
