# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Refused launches are counted in aggregate, and only in aggregate.

`BrowserPool.launch` records nothing in ``engine_health`` for a refused request
(issue #214) -- correct, and it left an operator with less than they had, since
``octowright_launch_refused_total`` is a noop unless ``PROVIDE_METRICS_ENABLED``
is set. These pin the replacement: that refusals are counted, attributed to the
guard that raised without any guard tagging itself, bounded, and that the
offending url/path never enters the surface.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.browser_pool import refusals as _refusals
from octowright.browser_pool.pool import BrowserPool
from octowright.request_errors import InvalidRequestError


async def test_a_refused_launch_is_counted_and_attributed() -> None:
    """Attribution comes from the traceback, so no guard tags itself.

    The two cases raise from different modules -- ``_reject_unsafe_url`` for
    the target, ``LaunchOptions.validate`` for the options -- which is the
    distinction an operator needs to tell "clients are sending bad URLs" from
    "clients are sending bad options".
    """
    pool = BrowserPool()
    assert pool.refusals() == {"total": 0, "by_guard": {}, "last_at": None}

    for _ in range(3):
        with pytest.raises(InvalidRequestError):
            await pool.launch(kind="chromium", url="file:///etc/passwd")
    with pytest.raises(InvalidRequestError):
        await pool.launch(kind="not-an-engine")

    snapshot = pool.refusals()
    assert snapshot["total"] == 4
    assert snapshot["by_guard"] == {"session.core_page_mixin": 3, "browser_pool.options": 1}
    assert snapshot["last_at"] is not None
    # The whole point of the change this belongs to: still no engine fault.
    assert pool.engine_health() == {}


async def test_the_refused_value_never_enters_the_surface() -> None:
    """A refusal message carries the caller's url/path; the surface must not.

    ``engine_health`` keeps class names and never messages, and
    ``_metrics.launch_span`` stops the message reaching the OTLP backend, both
    because that string reliably holds a filesystem path or profile name.
    A pull surface that kept it would undo both.
    """
    pool = BrowserPool()
    # Named for what it is -- a caller-supplied path that happens to be
    # sensitive -- rather than `secret`, which trips detect-secrets on the
    # identifier rather than the value and would need a pragma to say "this
    # fixture is the point".
    revealing_url = "file:///Users/tanuki-tim/private/vault.env"

    with pytest.raises(InvalidRequestError):
        await pool.launch(kind="chromium", url=revealing_url)

    assert "tanuki-tim" not in repr(pool.refusals())
    assert "vault" not in repr(pool.refusals())


def test_guard_keys_are_bounded() -> None:
    """Bounded even though the key is code-derived and already bounded.

    Same belt-and-braces as the ``kind`` clamp: this dict is never evicted and
    is echoed into every ``octowright_status()``, so a future refactor that
    multiplied raising modules must not be able to grow it without limit.
    """
    tracker = _refusals.RefusalTracker()

    for i in range(_refusals.GUARD_KEY_CAP + 5):
        tracker.record(_raised_from_module(f"octowright.fake_guard_{i}"))

    snapshot = tracker.snapshot()
    assert snapshot["total"] == _refusals.GUARD_KEY_CAP + 5
    assert len(snapshot["by_guard"]) == _refusals.GUARD_KEY_CAP + 1
    assert snapshot["by_guard"][_refusals.OTHER_GUARD_KEY] == 5


def test_an_exception_with_no_traceback_is_named_rather_than_crashing() -> None:
    """A constructed-but-never-raised error has no frame to read."""
    assert _refusals.guard_of(InvalidRequestError("never raised")) == _refusals.UNKNOWN_GUARD_KEY


def test_snapshot_is_a_copy() -> None:
    """Mutating a returned snapshot must not corrupt the tracker's own state."""
    tracker = _refusals.RefusalTracker()
    tracker.record(_raised_from_module("octowright.ssrf"))

    snapshot = tracker.snapshot()
    snapshot["by_guard"]["ssrf"] = 999
    snapshot["by_guard"]["injected"] = 1

    assert tracker.snapshot()["by_guard"] == {"ssrf": 1}


def test_status_surfaces_refusals() -> None:
    """End-to-end wiring into octowright_status()["pool"]."""
    from octowright.server.meta import octowright_status

    block = octowright_status()["pool"]["refusals"]
    assert set(block) == {"total", "by_guard", "last_at"}
    assert isinstance(block["total"], int)


def _raised_from_module(module: str) -> InvalidRequestError:
    """Raise an ``InvalidRequestError`` from a frame whose module is ``module``.

    ``guard_of`` reads the traceback's last frame's ``__name__``, so a fake
    guard needs a real frame with a planted global -- not a hand-built
    traceback, which would test the assembly rather than the read.
    """
    namespace: dict[str, Any] = {"__name__": module, "InvalidRequestError": InvalidRequestError}
    exec("def _guard():\n    raise InvalidRequestError('refused')", namespace)
    try:
        namespace["_guard"]()
    except InvalidRequestError as exc:
        return exc
    raise AssertionError("unreachable")
