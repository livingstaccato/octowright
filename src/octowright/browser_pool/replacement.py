# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a replacement launch carries from the browser it replaces.

Handoff, fluid relaunch and the driver-death / process-crash relaunch each
open a new browser in place of an old one. Each used to rebuild the options
by hand from session attributes, and each copy list had drifted from
``LaunchOptions``: launch headers and their URL scoping, an explicit
``base_url``, ``disable_gpu``, the HAR mode/filter/content, the viewport and
the badge were lost everywhere, and the driver path also lost ``protected``,
``disable_automation_controlled``, ``wayland_native`` and ``headed``.

The browser ``channel`` is carried too. It used to be named below as
launch-time only, because a carried channel cannot notice that the named build
became unavailable; that case is handled where it happens instead
(`launch_replacement`): a replacement whose channel cannot be found relaunches
on Playwright's bundled build and says so. The channel comes only from the
live session's own options, never from a JSONL recording
(``LaunchOptions.from_launch_record`` still drops it).

The session now keeps the options it was launched with
(``BrowserSession.launch_options``, written by ``recorded_launch_options``),
and the replacement is DERIVED from them: every caller-settable field is
carried unless it is named here, with its reason. A new ``LaunchOptions``
field is therefore carried by construction, and
``tests/test_relaunch_carries_launch_options.py`` fails on a field that is
neither carried nor named.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from provide.telemetry import get_logger

from octowright.browser_pool.options import LaunchOptions

log = get_logger(__name__)

#: Options a replacement deliberately does NOT take from the original.
NOT_CARRIED: Final[dict[str, str]] = {
    # Arbitrary binary and argv: a code-execution opt-in the caller makes per
    # launch (OCTOWRIGHT_ALLOW_EXECUTABLE_PATH), never implied by a relaunch.
    "executable_path": "an arbitrary local binary is chosen per launch, never implied",
    "launch_args": "arbitrary argv is chosen per launch, never implied",
}

#: Options the replacement sets itself rather than copying.
SET_BY_REPLACEMENT: Final[dict[str, str]] = {
    "url": "the replacement opens where the page is now",
    "trusted_launch_url": "it keeps trusting where the operator launched the original",
    "protected": "the original's CURRENT state, which browser_set_protected may have changed",
    "har": "on exactly when the original wrote a HAR",
    "har_path": "a fresh sibling of the original's HAR, which is not overwritten",
}


#: A fluid relaunch's deliberate departures: it exists to reopen the browser as
#: a headed window whose viewport follows the OS window (``no_viewport``), so
#: a fixed size the original pinned is exactly what it drops.
FLUID_OVERRIDES: Final[dict[str, Any]] = {"headed": True, "viewport_w": None, "viewport_h": None}


def recorded_launch_options(
    launch_options: LaunchOptions, *, instance_id: str, profile: str | None, headless: bool
) -> LaunchOptions:
    """The options a session keeps: the request, with the decisions a
    replacement must not re-make folded in -- the resolved headedness (an
    auto ``headed=None`` would re-resolve, not repeat), the promoted profile,
    and the directory key of a ``session=True`` launch (an anonymous one is
    keyed by THIS instance_id, which its replacement does not share)."""
    session_key = launch_options.session_name(instance_id) if launch_options.session else None
    return replace(launch_options, headed=not headless, profile=profile, session_key=session_key)


@dataclass(frozen=True)
class ReplacementSource:
    """Everything a replacement is built from, read off the original session."""

    options: LaunchOptions
    launch_url: str | None
    protected: bool
    protected_reason: str
    har_path: Path | None = None

    @classmethod
    def of(cls, session: Any) -> ReplacementSource:
        options = session.launch_options
        if not isinstance(options, LaunchOptions):
            raise TypeError(f"session {getattr(session, 'instance_id', '?')!r} has no launch_options to relaunch from")
        har_path = getattr(session, "har_path", None)
        return cls(
            options=options,
            launch_url=getattr(session, "launch_url", None),
            protected=bool(getattr(session, "protected", False)),
            protected_reason=getattr(session, "protected_reason", "explicit"),
            har_path=Path(har_path) if har_path else None,
        )

    @property
    def kind(self) -> str:
        return self.options.kind

    @property
    def profile(self) -> str | None:
        return self.options.profile

    def launch_kwargs(
        self, *, url: str, headed: bool | None = None, overrides: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """``pool.launch`` kwargs for the replacement. ``headed`` overrides the
        original's (``None`` keeps it); ``overrides`` names any other
        deliberate departure, such as ``FLUID_OVERRIDES``."""
        defaults = LaunchOptions()
        changes: dict[str, Any] = {name: getattr(defaults, name) for name in NOT_CARRIED}
        changes.update(
            url=url,
            trusted_launch_url=self.launch_url,
            protected=self.protected,
            har=self.har_path is not None,
            har_path=str(self.har_path) if self.har_path is not None else None,
        )
        if headed is not None:
            changes["headed"] = headed
        changes.update(overrides or {})
        return replace(self.options, **changes).with_har_rotated().to_pool_kwargs()


#: Playwright's refusal of a channel it cannot find on this host (its own
#: wording, from ``_createChromiumChannel``): "Chromium distribution 'msedge'
#: is not found at ..." or "... is not supported on linux".
_CHANNEL_UNAVAILABLE = re.compile(r"distribution '([^']+)' is not (?:found|supported)")

#: How far down ``__cause__``/``__context__`` a launch failure is searched.
_CAUSE_DEPTH = 8


def channel_unavailable(exc: BaseException, channel: str) -> bool:
    """Whether *exc* is Playwright refusing *channel* itself as not installed here.

    Only that channel: a failure naming any other distribution, or none, is
    a real launch failure and is not retried.
    """
    seen: BaseException | None = exc
    for _ in range(_CAUSE_DEPTH):
        if seen is None:
            return False
        if any(match == channel for match in _CHANNEL_UNAVAILABLE.findall(str(seen))):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def channel_dropped_warning(channel: str, kind: str) -> str:
    return (
        f"browser channel {channel!r} is not available on this host any more, so the replacement "
        f"launched on Playwright's bundled {kind} build instead"
    )


async def launch_replacement(
    launch: Callable[..., Awaitable[dict[str, Any]]], kwargs: Mapping[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Launch a replacement with *kwargs*; returns the result and the channel it dropped, if any.

    The original's ``channel`` is carried, and the one way that goes wrong is
    the named build having been uninstalled since. Then, and only then, the
    replacement is launched again with no channel, on the bundled build, and
    the dropped channel is returned so the caller can say so. Any other
    failure propagates untouched.
    """
    channel = kwargs.get("channel")
    try:
        return await launch(**kwargs), None
    except Exception as exc:
        if not isinstance(channel, str) or not channel or not channel_unavailable(exc, channel):
            raise
    log.warning("octowright.browser.replacement.channel_dropped", channel=channel, kind=kwargs.get("kind"))
    return await launch(**{**kwargs, "channel": None}), channel
