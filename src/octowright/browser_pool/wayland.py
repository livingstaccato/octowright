# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Native Wayland for headed Chromium on Linux.

Without an ozone flag, headed Chromium on a Wayland desktop runs as an X11
client under XWayland, and XWayland drops touchpad pinch gestures: a page-side
logger saw zero wheel/touch/pointer events while pinching in an octowright
window, while the user's own Chrome on the same machine pinch-zoomed fine.
``--ozone-platform=wayland`` makes Chromium a native Wayland client.

Deliberately a BOOLEAN over a fixed flag set rather than more ``launch_args``,
for the reason ``OCTOWRIGHT_DISABLE_GPU`` is (see ``options.py``): launch_args
is arbitrary argv, gated behind ``OCTOWRIGHT_ALLOW_EXECUTABLE_PATH`` because it
is a code-execution primitive. A boolean selecting two constant flags grants no
new power, so it needs no gate. Caller input never reaches this argv.

Resolution, most specific first: the per-launch ``wayland_native`` argument;
else ``OCTOWRIGHT_WAYLAND_NATIVE`` (on / off / unset = auto); else AUTO, which
turns it on only for headed Chromium on Linux whose ``WAYLAND_DISPLAY`` names a
socket that exists. Whatever the source, it is never applied to a headless
launch, a non-Chromium engine, or a non-Linux host.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Literal

from provide.telemetry import get_logger

log = get_logger(__name__)

WAYLAND_NATIVE_ENV: Final = "OCTOWRIGHT_WAYLAND_NATIVE"

#: The complete set of flags this feature adds. A module constant, never
#: extended from caller input.
WAYLAND_NATIVE_ARGS: Final = ("--ozone-platform=wayland", "--enable-features=WaylandWindowDecorations")
_OZONE_ARG: Final = WAYLAND_NATIVE_ARGS[0]
#: What the auto fallback puts in ``_OZONE_ARG``'s place (see
#: ``x11_retry_kwargs``). A constant, never caller input.
X11_RETRY_OZONE_ARG: Final = "--ozone-platform=x11"
_FEATURES_PREFIX: Final = "--enable-features="
_WAYLAND_FEATURES: Final = tuple(WAYLAND_NATIVE_ARGS[1].removeprefix(_FEATURES_PREFIX).split(","))

#: Features Playwright itself enables (``chromiumSwitches``: it passes
#: ``--enable-features=CDPScreenshotNewSurface`` unless
#: ``PLAYWRIGHT_LEGACY_SCREENSHOT`` is set). Chromium honours only the LAST
#: ``--enable-features`` switch, and our args come after Playwright's, so ours
#: must carry Playwright's too or silently switch it off.
PLAYWRIGHT_DEFAULT_ENABLED_FEATURES: Final = ("CDPScreenshotNewSurface",)
_PLAYWRIGHT_LEGACY_SCREENSHOT_ENV: Final = "PLAYWRIGHT_LEGACY_SCREENSHOT"

_ENV_ON: Final = frozenset({"1", "true", "yes", "on"})
_ENV_OFF: Final = frozenset({"0", "false", "no", "off", "never", "none", "disabled"})
_ENV_AUTO: Final = frozenset({"", "auto"})
_ENV_WARNED: set[str] = set()

#: Longest ``fallback_reason`` reported. The text is the browser's own log
#: line, which is useful and unbounded.
_REASON_MAX_CHARS: Final = 300

Source = Literal["argument", "env", "auto"]
EnvSetting = Literal["on", "off", "auto"]


class WaylandLaunchError(RuntimeError):
    """Chromium could not start natively on Wayland, and Wayland was asked for
    explicitly (argument or env), so octowright did not fall back to X11.

    Its message quotes the browser's own complaint about Wayland rather than
    Playwright's first line; the full original stays on ``__cause__``. It is
    never read as a dead shared driver, whatever its text
    (``driver_health.is_driver_dead_error`` excludes this type), so one
    browser's display problem cannot stop the driver -- not even when the
    liveness probe cannot read Playwright's internals and falls back to "dead".
    """


def host_platform() -> str:
    """``sys.platform``, behind a seam so tests can fake the host."""
    return sys.platform


def env_setting() -> EnvSetting:
    """Read ``OCTOWRIGHT_WAYLAND_NATIVE`` at call time.

    An unrecognised value means AUTO (the default), with one warning per value.
    That is the repository's rule for every knob -- a typo falls back to the
    default rather than being guessed at -- and here the default is auto
    rather than off. Auto is also the safe reading: it applies Wayland only
    where a compositor socket exists and falls back to X11 if that launch fails.
    """
    token = os.environ.get(WAYLAND_NATIVE_ENV, "").strip().lower()
    if token in _ENV_ON:
        return "on"
    if token in _ENV_OFF:
        return "off"
    if token not in _ENV_AUTO and token not in _ENV_WARNED:
        _ENV_WARNED.add(token)
        log.warning(
            "octowright.wayland.env_unrecognized",
            name=WAYLAND_NATIVE_ENV,
            hint="not recognized; using auto (expected on/off/auto)",
        )
    return "auto"


def wayland_socket_path() -> Path | None:
    """Where ``WAYLAND_DISPLAY`` points, as libwayland resolves it: an absolute
    value is the socket itself, a relative one lives in ``XDG_RUNTIME_DIR``.
    ``None`` when there is no display, or a relative one with no runtime dir."""
    display = os.environ.get("WAYLAND_DISPLAY", "").strip()
    if not display:
        return None
    if os.path.isabs(display):
        return Path(display)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not runtime_dir:
        return None
    return Path(runtime_dir) / display


def _auto_reason() -> tuple[bool, str]:
    if not os.environ.get("WAYLAND_DISPLAY", "").strip():
        return False, "no_wayland_display"
    socket = wayland_socket_path()
    if socket is None:
        return False, "no_xdg_runtime_dir"
    if not socket.exists():
        return False, "wayland_socket_missing"
    return True, "wayland_session"


@dataclass(frozen=True)
class WaylandDecision:
    """What was asked for, where the answer came from, and what happened."""

    requested: bool | None
    source: Source
    effective: bool
    reason: str
    fallback_reason: str | None = None

    def report(self) -> dict[str, Any]:
        """The launch result's ``wayland_native`` block."""
        out: dict[str, Any] = {
            "requested": self.requested,
            "source": self.source,
            "effective": self.effective,
            "reason": self.reason,
        }
        if self.fallback_reason is not None:
            out["fallback_reason"] = self.fallback_reason
        return out

    def fell_back(self, exc: BaseException) -> WaylandDecision:
        return replace(self, effective=False, reason="fallback_x11", fallback_reason=failure_summary(exc))


def resolve_wayland_native(explicit: bool | None, *, kind: str, headless: bool) -> WaylandDecision:
    """Decide whether this launch runs Chromium as a native Wayland client.

    Headless is reported, not refused, even for an explicit ``True``: headed-ness
    is resolved late (``OCTOWRIGHT_HEADLESS``/CI detection) and a handoff can
    flip it, so the same persisted request must survive a headless relaunch.
    """
    setting = env_setting()
    if explicit is not None:
        source: Source = "argument"
        wanted: bool | None = explicit
    elif setting != "auto":
        source, wanted = "env", setting == "on"
    else:
        source, wanted = "auto", None

    def decided(effective: bool, reason: str) -> WaylandDecision:
        return WaylandDecision(requested=explicit, source=source, effective=effective, reason=reason)

    if kind != "chromium":
        return decided(False, "not_chromium")
    if not host_platform().startswith("linux"):
        return decided(False, "not_linux")
    if headless:
        return decided(False, "headless")
    if wanted is not None:
        return decided(wanted, "requested" if wanted else "disabled")
    return decided(*_auto_reason())


def status_default() -> dict[str, Any]:
    """``octowright_status()["defaults"]["wayland_native"]``: the setting, and
    what it would mean for a headed Chromium launched right now."""
    decision = resolve_wayland_native(None, kind="chromium", headless=False)
    return {
        "setting": env_setting(),
        "effective_for_headed_chromium": decision.effective,
        "reason": decision.reason,
    }


def _features_of(arg: str) -> list[str]:
    return [f for f in arg.removeprefix(_FEATURES_PREFIX).split(",") if f]


def merge_enable_features(args: list[str]) -> list[str]:
    """Collapse every ``--enable-features`` switch into one, keeping Playwright's.

    Chromium reads only the last occurrence, so two switches mean the first is
    silently ignored. The merged switch takes the place of the last one, with
    Playwright's own default features first and duplicates dropped.
    """
    switches = [i for i, a in enumerate(args) if a.startswith(_FEATURES_PREFIX)]
    if not switches:
        return list(args)
    merged: list[str] = []
    if not os.environ.get(_PLAYWRIGHT_LEGACY_SCREENSHOT_ENV):
        merged.extend(PLAYWRIGHT_DEFAULT_ENABLED_FEATURES)
    for i in switches:
        merged.extend(f for f in _features_of(args[i]) if f not in merged)
    last = switches[-1]
    out = [a for i, a in enumerate(args) if i not in switches[:-1]]
    out[out.index(args[last])] = _FEATURES_PREFIX + ",".join(merged)
    return out


def _without_wayland_features(arg: str) -> str | None:
    """``arg`` minus this module's features; ``None`` for a switch left empty."""
    if not arg.startswith(_FEATURES_PREFIX):
        return arg
    kept = [f for f in _features_of(arg) if f not in _WAYLAND_FEATURES]
    return _FEATURES_PREFIX + ",".join(kept) if kept else None


def x11_retry_kwargs(launch_kwargs: dict[str, Any]) -> dict[str, Any]:
    """The same launch kwargs with X11 forced in place of what this module added.

    Dropping the Wayland flag is not enough. With no ``--ozone-platform`` switch
    Chromium picks the platform itself, from ``XDG_SESSION_TYPE``, and on a
    Wayland desktop that picks Wayland again: the retry died exactly like the
    first attempt. So the switch this module added is REPLACED, in place, by the
    fixed ``X11_RETRY_OZONE_ARG``. Measured on chromium-1243 under ``xvfb-run``
    with ``XDG_SESSION_TYPE=wayland`` and a dead ``WAYLAND_DISPLAY``: no switch
    fails, ``--ozone-platform=x11`` launches, and Chromium honours the LAST
    ``--ozone-platform`` given. No environment variable overrides the switch
    (``OZONE_PLATFORM=wayland`` made no difference; the binary carries no
    ``--ozone-platform-hint`` either).

    Only the first ``--ozone-platform=wayland`` is touched -- the one
    ``_chromium_args`` put before any caller ``launch_args``. A caller's own
    switch comes later, is left as given, and still wins, which is the ordering
    rule ``_build_launch_kwargs`` documents. Wayland features are dropped from
    the merged ``--enable-features`` switch.

    Rewriting rather than rebuilding keeps a fallback from consuming a second
    window-tiling slot (``_chromium_args`` advances the tile counter)."""
    args = list(launch_kwargs.get("args", []))
    if _OZONE_ARG in args:
        args[args.index(_OZONE_ARG)] = X11_RETRY_OZONE_ARG
    args = [kept for kept in map(_without_wayland_features, args) if kept is not None]
    out = {k: v for k, v in launch_kwargs.items() if k != "args"}
    if args:
        out["args"] = args
    return out


def _wayland_evidence(exc: BaseException) -> list[str]:
    """Lines of a launch failure in which the BROWSER complains about Wayland.

    Playwright's error echoes the full argv (``<launching> ... --ozone-platform=
    wayland ...``, once under "Browser logs" and again under "Call log"), so a
    plain substring test says "wayland" for EVERY failed Wayland launch -- a
    locked profile included. Found by the live fallback test, which reported
    the argv line as the reason. Argv echoes are skipped.
    """
    lines = [line.strip() for line in str(exc).splitlines()]
    return [line for line in lines if "wayland" in line.lower() and "<launching>" not in line and "--" not in line]


def failure_summary(exc: BaseException) -> str:
    """The most telling line of a launch failure: the browser's own complaint
    about Wayland when it logged one, else the first line. Bounded."""
    evidence = _wayland_evidence(exc)
    if evidence:
        chosen = evidence[0]
        marker = chosen.find("ERROR:")
        if marker != -1:
            chosen = chosen[marker:]
    else:
        lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
        chosen = lines[0] if lines else type(exc).__name__
    return chosen[:_REASON_MAX_CHARS]


def mentions_wayland(exc: BaseException) -> bool:
    """Whether the browser itself blamed Wayland (argv echoes excluded)."""
    return bool(_wayland_evidence(exc))


def explicit_failure(decision: WaylandDecision, exc: BaseException) -> WaylandLaunchError:
    """The error for a native-Wayland launch that was asked for explicitly."""
    asked = "wayland_native=True" if decision.source == "argument" else f"{WAYLAND_NATIVE_ENV}=on"
    reported = failure_summary(exc)
    return WaylandLaunchError(
        f"Chromium could not start as a native Wayland client ({asked}; flags {' '.join(WAYLAND_NATIVE_ARGS)}). "
        f"Chromium reported: {reported}. "
        f"Check that a Wayland compositor is running and that WAYLAND_DISPLAY/XDG_RUNTIME_DIR reach the daemon, "
        f"or launch with wayland_native=False (X11/XWayland), or leave it unset with {WAYLAND_NATIVE_ENV} "
        "unset for auto, which falls back to X11 when the Wayland launch fails."
    )
