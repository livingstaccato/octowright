# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A lookup that fails AFTER an unresponsive target must say so.

An unresponsive target is not a dead one -- ``_notify_call_timeout``
deliberately neither sets ``_crashed`` nor tears the session down, because the
page may still be executing. But if that browser is later evicted for any
reason, the eviction ledger used to record only crashed/not-crashed, so the
lookup fell into the generic "ended unexpectedly ... relaunch it" branch. That
is the wrong advice: the browser is usually still alive, and relaunching
discards a live session to fix something that needed a smaller batch.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from octowright.browser_pool import lifecycle
from octowright.browser_pool.pool import BrowserPool


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _pool() -> BrowserPool:
    return BrowserPool()


def _session(*, crashed: bool = False, unresponsive: str | None = None) -> MagicMock:
    session = MagicMock()
    session._crashed = crashed
    session._unresponsive_operation = unresponsive
    return session


class TestEvictionLedgerRecordsWhy:
    def test_unresponsive_session_is_recorded_as_unresponsive(self) -> None:
        pool = _pool()
        lifecycle._record_recently_evicted(pool, "b1", _session(unresponsive="browser_evaluate"))
        assert pool._recently_evicted["b1"] == "unresponsive"

    def test_crashed_session_is_recorded_as_crashed(self) -> None:
        pool = _pool()
        lifecycle._record_recently_evicted(pool, "b1", _session(crashed=True))
        assert pool._recently_evicted["b1"] == "crashed"

    def test_ordinary_eviction_is_recorded_as_external(self) -> None:
        pool = _pool()
        lifecycle._record_recently_evicted(pool, "b1", _session())
        assert pool._recently_evicted["b1"] == "external"

    def test_a_crash_after_going_quiet_reports_as_a_crash(self) -> None:
        """Both markers set: the crash is the more specific answer, and
        'relaunch' is right advice for a browser whose process died."""
        pool = _pool()
        lifecycle._record_recently_evicted(pool, "b1", _session(crashed=True, unresponsive="browser_evaluate"))
        assert pool._recently_evicted["b1"] == "crashed"

    def test_ledger_stays_bounded(self) -> None:
        """It is written from a Playwright close callback on a long-lived
        daemon, so it must not grow without limit."""
        pool = _pool()
        for i in range(pool._RECENTLY_EVICTED_CAP + 25):
            lifecycle._record_recently_evicted(pool, f"b{i}", _session())
        assert len(pool._recently_evicted) <= pool._RECENTLY_EVICTED_CAP


class TestMissingSessionMessage:
    def test_unresponsive_lookup_does_not_tell_the_caller_to_relaunch(self) -> None:
        """The whole complaint: relaunching throws away a browser that is
        usually still running."""
        pool = _pool()
        pool._recently_evicted["b1"] = "unresponsive"
        message = pool._missing_session_message("b1")
        # The crashed/external branches both end in "relaunch it with
        # browser_launch". That instruction must not be the answer here.
        assert "browser_launch" not in message

    def test_unresponsive_lookup_names_the_recovery_path(self) -> None:
        pool = _pool()
        pool._recently_evicted["b1"] = "unresponsive"
        message = pool._missing_session_message("b1")
        assert "unresponsive" in message
        assert "browser_list" in message
        assert "browser_downloads" in message
        assert "smaller batch" in message

    def test_unresponsive_lookup_is_distinguishable_from_a_crash(self) -> None:
        """Both were the same generic dead end before; they must not read alike."""
        pool = _pool()
        pool._recently_evicted["quiet"] = "unresponsive"
        pool._recently_evicted["dead"] = "crashed"
        assert pool._missing_session_message("quiet") != pool._missing_session_message("dead")

    def test_crashed_lookup_still_says_relaunch(self) -> None:
        pool = _pool()
        pool._recently_evicted["b1"] = "crashed"
        message = pool._missing_session_message("b1")
        assert "crashed" in message
        assert "browser_launch" in message

    def test_external_lookup_message_is_unchanged(self) -> None:
        pool = _pool()
        pool._recently_evicted["b1"] = "external"
        message = pool._missing_session_message("b1")
        assert "ended unexpectedly" in message
        assert "browser_launch" in message

    def test_unknown_id_still_falls_through_to_the_live_listing_hint(self) -> None:
        """An id never in the ledger is a typo or a never-launched browser,
        and must not be described as unresponsive."""
        pool = _pool()
        message = pool._missing_session_message("never-existed")
        assert "unresponsive" not in message
        assert "no browsers are live" in message

    def test_pool_get_raises_the_unresponsive_message(self) -> None:
        """The message is only worth anything if it reaches the caller."""
        pool = _pool()
        pool._recently_evicted["b1"] = "unresponsive"
        with pytest.raises(KeyError, match="stopped answering"):
            pool.get("b1")


class TestSessionMarker:
    @pytest.mark.anyio
    async def test_call_timeout_hook_marks_the_session_without_crashing_it(self) -> None:
        """_notify_call_timeout must set the unresponsive marker and must NOT
        set _crashed -- the target may still be executing, and conflating the
        two would put it back in the 'relaunch' branch."""
        from octowright.session.core import BrowserSession
        from octowright.session.timeouts import SessionCallTimeoutError

        session = MagicMock(spec=BrowserSession)
        session._unresponsive_operation = None
        # spec= auto-creates a truthy mock for any declared attribute, so
        # start it at the real default and prove the hook leaves it alone.
        session._crashed = False
        session.instance_id = "b1"
        session.kind = "chromium"
        session.label = None
        session.profile = None
        session.url = "https://octowright.com"
        session.log_path = "/tmp/rec.jsonl"
        BrowserSession._notify_call_timeout(session, "browser_evaluate", SessionCallTimeoutError("did not answer"))
        assert session._unresponsive_operation == "browser_evaluate"
        assert not getattr(session, "_crashed", False)
