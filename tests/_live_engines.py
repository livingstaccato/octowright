# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""When a skipped live-browser test must fail instead: ``OCTOWRIGHT_REQUIRE_LIVE_ENGINES``.

Every live fixture turns a launch exception into ``pytest.skip``, which is right
on a laptop with one engine missing and wrong on a runner that just installed
all three: there an engine regression -- a launch arg WebKit now rejects, a
dependency lost in a runner image update -- skips every test measuring that
engine, and a skip is green. Set the variable on such a runner and those skips
fail instead. See tests/AGENTS.md.

Judged centrally, from the skip, rather than by editing each fixture: 19
modules carry their own copy of the catch-and-skip, and one that is added later
would otherwise not know to participate.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

ENV_VAR = "OCTOWRIGHT_REQUIRE_LIVE_ENGINES"
ENGINES = frozenset({"chromium", "firefox", "webkit"})
_ALL = {"1", "true", "yes", "on", "all"}
_NONE = {"", "0", "false", "no", "off", "none"}

#: What the daemon-driven tests skip with: they read the launch failure out of a
#: tool result rather than catching it, so there is no exception to look at.
_UNAVAILABLE_REASON = re.compile(r"no usable browser engine", re.IGNORECASE)


def required_engines(raw: str | None) -> frozenset[str]:
    """The engines the switch requires: none, all three, or a comma-separated list."""
    value = (raw or "").strip().lower()
    if value in _NONE:
        return frozenset()
    if value in _ALL:
        return ENGINES
    named = frozenset(part.strip() for part in value.split(",") if part.strip())
    unknown = named - ENGINES
    if unknown:
        # A typo would otherwise require nothing and pass the run it exists to fail.
        raise pytest.UsageError(f"{ENV_VAR}: unknown engine(s) {sorted(unknown)}; expected {sorted(ENGINES)}")
    return named


def _engine(item: Any) -> str:
    """The engine *item* launches: its parametrization, else Chromium, the pool's default."""
    callspec = getattr(item, "callspec", None)
    for value in (getattr(callspec, "params", None) or {}).values():
        if value in ENGINES:
            return str(value)
    for engine in sorted(ENGINES):
        if f"[{engine}" in item.nodeid or f"-{engine}" in item.nodeid:
            return engine
    return "chromium"


def engine_skip_failure(item: Any, skipped: BaseException, required: frozenset[str]) -> str | None:
    """The failure message if this skip hid an engine that was required, else None.

    A skip counts when it was raised while handling an exception -- the
    fixture pattern ``except Exception: pytest.skip(...)`` around a launch --
    or names an unusable engine outright. A test that skips on purpose (a
    surface only Chromium has) raises its skip from nowhere and stays a skip.
    """
    if not required or item.get_closest_marker("live_browser") is None:
        return None
    engine = _engine(item)
    if engine not in required:
        return None
    cause = skipped.__context__
    reason = str(getattr(skipped, "msg", None) or skipped)
    if cause is None and not _UNAVAILABLE_REASON.search(reason):
        return None
    detail = f"{type(cause).__name__}: {cause}" if cause is not None else reason
    return f"{ENV_VAR} requires {engine}, but the test skipped because it could not run it: {detail}"
