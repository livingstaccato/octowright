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

The session now keeps the options it was launched with
(``BrowserSession.launch_options``, written by ``recorded_launch_options``),
and the replacement is DERIVED from them: every caller-settable field is
carried unless it is named here, with its reason. A new ``LaunchOptions``
field is therefore carried by construction, and
``tests/test_relaunch_carries_launch_options.py`` fails on a field that is
neither carried nor named.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from octowright.browser_pool.options import LaunchOptions

#: Options a replacement deliberately does NOT take from the original.
NOT_CARRIED: Final[dict[str, str]] = {
    # LaunchOptions.channel: browser selection is launch-time only, since a
    # carried setting cannot notice the named channel became unavailable.
    "channel": "browser selection is launch-time only",
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

    @property
    def stateful(self) -> bool:
        return self.options.profile is not None or self.options.session

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
