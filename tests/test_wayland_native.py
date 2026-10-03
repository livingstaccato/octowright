# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Native Wayland for headed Chromium: resolution, validation and argv.

No browser is launched here. Platform is faked through
``wayland.host_platform`` and the compositor socket is a plain file under
``tmp_path`` -- auto-detection checks that the socket path exists, not that a
compositor answers on it (a dead one is what the launch fallback is for).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from octowright.browser_pool import wayland
from octowright.browser_pool.options import LaunchOptions
from octowright.browser_pool.pool import BrowserPool
from octowright.request_errors import InvalidRequestError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _fake_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wayland, "host_platform", lambda: "linux")


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (wayland.WAYLAND_NATIVE_ENV, "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "PLAYWRIGHT_LEGACY_SCREENSHOT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_linux(monkeypatch)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)


@pytest.fixture
def wayland_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A Linux desktop with a live-looking Wayland socket."""
    _fake_linux(monkeypatch)
    _clear_env(monkeypatch)
    socket = tmp_path / "wayland-0"
    socket.touch()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    return socket


def _resolve(
    explicit: bool | None = None, *, kind: str = "chromium", headless: bool = False
) -> wayland.WaylandDecision:
    return wayland.resolve_wayland_native(explicit, kind=kind, headless=headless)


# ─── auto-detection matrix ───────────────────────────────────────────────────


@pytest.mark.usefixtures("wayland_session")
def test_auto_turns_on_for_headed_chromium_in_a_wayland_session() -> None:
    decision = _resolve()
    assert decision.effective is True
    assert decision.source == "auto"
    assert decision.reason == "wayland_session"


def test_auto_stays_off_when_the_socket_is_missing(wayland_session: Path) -> None:
    wayland_session.unlink()
    decision = _resolve()
    assert decision.effective is False
    assert decision.reason == "wayland_socket_missing"


@pytest.mark.usefixtures("linux", "clean_env")
def test_auto_stays_off_on_x11_only_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":0")
    decision = _resolve()
    assert decision.effective is False
    assert decision.reason == "no_wayland_display"


@pytest.mark.usefixtures("wayland_session")
def test_auto_stays_off_without_xdg_runtime_dir_for_a_relative_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    decision = _resolve()
    assert decision.effective is False
    assert decision.reason == "no_xdg_runtime_dir"


@pytest.mark.usefixtures("linux", "clean_env")
def test_auto_accepts_an_absolute_wayland_display_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    socket = tmp_path / "elsewhere" / "wl-sock"
    socket.parent.mkdir()
    socket.touch()
    monkeypatch.setenv("WAYLAND_DISPLAY", str(socket))
    assert wayland.wayland_socket_path() == socket
    assert _resolve().effective is True


@pytest.mark.usefixtures("wayland_session")
@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_auto_is_off_off_linux_even_with_a_socket(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    monkeypatch.setattr(wayland, "host_platform", lambda: platform)
    decision = _resolve()
    assert decision.effective is False
    assert decision.reason == "not_linux"


@pytest.mark.usefixtures("wayland_session")
def test_auto_is_off_for_headless() -> None:
    decision = _resolve(headless=True)
    assert decision.effective is False
    assert decision.reason == "headless"


@pytest.mark.usefixtures("wayland_session")
@pytest.mark.parametrize("kind", ["firefox", "webkit"])
def test_auto_is_off_for_non_chromium(kind: str) -> None:
    decision = _resolve(kind=kind)
    assert decision.effective is False
    assert decision.reason == "not_chromium"


# ─── env override + precedence ───────────────────────────────────────────────


@pytest.mark.usefixtures("linux", "clean_env")
@pytest.mark.parametrize("token", ["1", "true", "YES", " on "])
def test_env_on_forces_it_without_a_socket(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv(wayland.WAYLAND_NATIVE_ENV, token)
    decision = _resolve()
    assert decision.effective is True
    assert decision.source == "env"


@pytest.mark.usefixtures("wayland_session")
@pytest.mark.parametrize("token", ["0", "false", "no", "off", "OFF"])
def test_env_off_forces_it_off_in_a_wayland_session(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv(wayland.WAYLAND_NATIVE_ENV, token)
    decision = _resolve()
    assert decision.effective is False
    assert decision.source == "env"
    assert decision.reason == "disabled"


@pytest.mark.usefixtures("wayland_session")
@pytest.mark.parametrize("token", ["", "auto", "AUTO"])
def test_env_empty_or_auto_means_auto(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv(wayland.WAYLAND_NATIVE_ENV, token)
    assert wayland.env_setting() == "auto"
    assert _resolve().source == "auto"


@pytest.mark.usefixtures("wayland_session")
def test_env_junk_warns_and_is_treated_as_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_log = MagicMock()
    monkeypatch.setattr(wayland, "log", fake_log)
    monkeypatch.setattr(wayland, "_ENV_WARNED", set())
    monkeypatch.setenv(wayland.WAYLAND_NATIVE_ENV, "enabled-ish")
    assert wayland.env_setting() == "auto"
    assert wayland.env_setting() == "auto"
    fake_log.warning.assert_called_once()
    assert fake_log.warning.call_args.args[0] == "octowright.wayland.env_unrecognized"
    assert _resolve().source == "auto"


@pytest.mark.usefixtures("wayland_session")
def test_explicit_false_beats_env_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(wayland.WAYLAND_NATIVE_ENV, "1")
    decision = _resolve(False)
    assert decision.effective is False
    assert decision.source == "argument"


@pytest.mark.usefixtures("linux", "clean_env")
def test_explicit_true_beats_env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(wayland.WAYLAND_NATIVE_ENV, "0")
    decision = _resolve(True)
    assert decision.effective is True
    assert decision.source == "argument"


@pytest.mark.usefixtures("wayland_session")
def test_explicit_true_on_headless_is_reported_not_applied() -> None:
    """Headed-ness is resolved late (env default, handoff may flip it), so a
    headless launch carrying True is reported rather than refused."""
    decision = _resolve(True, headless=True)
    assert decision.effective is False
    assert decision.reason == "headless"


# ─── strict validation ───────────────────────────────────────────────────────


def test_option_defaults_to_auto() -> None:
    assert LaunchOptions().wayland_native is None


@pytest.mark.parametrize("value", ["true", "false", 1, 0, [], {}, "auto"])
def test_non_boolean_is_rejected_on_the_mapping_path(value: object) -> None:
    with pytest.raises(InvalidRequestError, match="wayland_native must be a boolean"):
        LaunchOptions.from_mapping({"kind": "chromium", "wayland_native": value})


@pytest.mark.parametrize("value", ["true", 1])
def test_non_boolean_is_rejected_on_the_direct_mcp_path(value: object) -> None:
    with pytest.raises(InvalidRequestError, match="wayland_native must be a boolean"):
        LaunchOptions(wayland_native=value).to_pool_kwargs()  # type: ignore[arg-type]


@pytest.mark.usefixtures("linux")
@pytest.mark.parametrize("kind", ["firefox", "webkit"])
def test_explicit_true_is_rejected_for_non_chromium(kind: str) -> None:
    with pytest.raises(InvalidRequestError, match="only supported for kind='chromium'"):
        LaunchOptions.from_mapping({"kind": kind, "wayland_native": True})
    with pytest.raises(InvalidRequestError, match="only supported for kind='chromium'"):
        LaunchOptions(kind=kind, wayland_native=True).to_pool_kwargs()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_explicit_true_is_rejected_off_linux(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    monkeypatch.setattr(wayland, "host_platform", lambda: platform)
    with pytest.raises(InvalidRequestError, match="only supported on Linux"):
        LaunchOptions.from_mapping({"kind": "chromium", "wayland_native": True})


@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
@pytest.mark.parametrize("value", [None, False])
def test_false_and_none_are_valid_everywhere(monkeypatch: pytest.MonkeyPatch, kind: str, value: bool | None) -> None:
    monkeypatch.setattr(wayland, "host_platform", lambda: "darwin")
    LaunchOptions(kind=kind, wayland_native=value).validate()


@pytest.mark.usefixtures("linux")
def test_fixed_option_needs_no_arbitrary_argv_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_EXECUTABLE_PATH", raising=False)
    assert LaunchOptions(wayland_native=True).to_pool_kwargs()["wayland_native"] is True


# ─── argv ────────────────────────────────────────────────────────────────────


def test_flag_set_is_the_fixed_tuple() -> None:
    assert wayland.WAYLAND_NATIVE_ARGS == ("--ozone-platform=wayland", "--enable-features=WaylandWindowDecorations")
    assert isinstance(wayland.WAYLAND_NATIVE_ARGS, tuple)


def _feature_switches(args: list[str]) -> list[str]:
    return [a for a in args if a.startswith("--enable-features=")]


@pytest.mark.anyio
@pytest.mark.usefixtures("clean_env")
async def test_enabled_adds_ozone_and_one_merged_feature_switch() -> None:
    kwargs = await BrowserPool()._build_launch_kwargs(tile=False, kind="chromium", headless=True, wayland_native=True)
    args = kwargs["args"]
    assert args.count("--ozone-platform=wayland") == 1
    # Playwright passes --enable-features=CDPScreenshotNewSurface itself and
    # Chromium honours only the LAST --enable-features, so ours carries it.
    assert _feature_switches(args) == ["--enable-features=CDPScreenshotNewSurface,WaylandWindowDecorations"]


@pytest.mark.anyio
@pytest.mark.usefixtures("clean_env")
async def test_legacy_screenshot_env_drops_playwrights_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_LEGACY_SCREENSHOT", "1")
    kwargs = await BrowserPool()._build_launch_kwargs(tile=False, kind="chromium", headless=True, wayland_native=True)
    assert _feature_switches(kwargs["args"]) == ["--enable-features=WaylandWindowDecorations"]


@pytest.mark.anyio
@pytest.mark.usefixtures("clean_env")
async def test_caller_feature_switch_is_merged_not_clobbered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_EXECUTABLE_PATH", "1")
    kwargs = await BrowserPool()._build_launch_kwargs(
        tile=False,
        kind="chromium",
        headless=True,
        wayland_native=True,
        launch_args=["--user-flag", "--enable-features=Foo,WaylandWindowDecorations"],
    )
    args = kwargs["args"]
    assert _feature_switches(args) == ["--enable-features=CDPScreenshotNewSurface,WaylandWindowDecorations,Foo"]
    # Caller argv still comes after octowright's own flags.
    assert args.index("--ozone-platform=wayland") < args.index("--user-flag")


@pytest.mark.anyio
@pytest.mark.usefixtures("clean_env")
async def test_disabled_leaves_existing_args_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_EXECUTABLE_PATH", "1")
    pool = BrowserPool()
    caller = ["--enable-features=A", "--enable-features=B"]
    kwargs = await pool._build_launch_kwargs(tile=False, kind="chromium", headless=True, launch_args=caller)
    assert "--ozone-platform=wayland" not in kwargs["args"]
    # Without Wayland nothing is merged: the caller's argv is passed as given.
    assert kwargs["args"][-2:] == caller


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["firefox", "webkit"])
async def test_non_chromium_never_gets_the_flags(kind: str) -> None:
    kwargs = await BrowserPool()._build_launch_kwargs(tile=False, kind=kind, headless=False, wayland_native=True)
    assert "args" not in kwargs


@pytest.mark.usefixtures("clean_env")
def test_without_wayland_args_strips_exactly_what_was_added() -> None:
    launch_kwargs = {
        "args": [
            "--disable-dev-shm-usage",
            "--ozone-platform=wayland",
            "--user-flag",
            "--enable-features=CDPScreenshotNewSurface,WaylandWindowDecorations,Foo",
        ],
        "channel": "chrome",
    }
    stripped = wayland.without_wayland_args(launch_kwargs)
    assert stripped == {
        "args": ["--disable-dev-shm-usage", "--user-flag", "--enable-features=CDPScreenshotNewSurface,Foo"],
        "channel": "chrome",
    }
    # Pure: the input is untouched.
    assert "--ozone-platform=wayland" in launch_kwargs["args"]


def test_without_wayland_args_drops_a_switch_left_empty() -> None:
    stripped = wayland.without_wayland_args(
        {"args": ["--ozone-platform=wayland", "--enable-features=WaylandWindowDecorations"]}
    )
    assert stripped == {}


# ─── report shape ────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("wayland_session")
def test_report_shape() -> None:
    assert _resolve().report() == {
        "requested": None,
        "source": "auto",
        "effective": True,
        "reason": "wayland_session",
    }


@pytest.mark.usefixtures("wayland_session")
def test_status_default_describes_a_headed_chromium_launch() -> None:
    status = wayland.status_default()
    assert status == {
        "setting": "auto",
        "effective_for_headed_chromium": True,
        "reason": "wayland_session",
    }
