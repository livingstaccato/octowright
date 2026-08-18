# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright restart``'s browser sweep must run against a pid set it can see.

Narrowing the sweep from ``scope="all"`` to ``scope="orphaned"`` fixed the blast
radius and broke the sweep: ``_stop_leader`` only signals the leader *python*
pids, and returns the moment they exit. The Playwright **node driver** they own
is still alive at that instant (the kernel has just reparented it to init), so
every browser's ppid is still a live pid and ``_is_orphaned_browser`` answers
False for all of them. ``find_browser_pids("orphaned")`` came back empty and the
command printed ``orphan browsers: killed=0`` while leaving every window up —
with ``--no-start`` there is no boot sweep behind it either.

The fix is to stop depending on that race: snapshot the browsers descended from
the daemons we are about to stop BEFORE stopping them, and reap that snapshot
(unioned with anything genuinely orphaned by an earlier generation). The
snapshot is strictly narrower than ``scope="all"`` — it can only ever contain
browsers this daemon owned — so the blast-radius fix is preserved.
"""

from __future__ import annotations

import pytest

from octowright import process_reaper
from octowright.cli import restart as restart_mod


def test_owned_browser_pids_snapshots_descendants_of_each_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    # leader 100 -> node driver 200 -> browsers 300/301; leader 400 -> 500 -> 600
    table = [
        (1, 0, "/sbin/launchd"),
        (100, 1, "python octowright serve"),
        (200, 100, "node .../playwright/cli.js run-driver"),
        (300, 200, "/x/ms-playwright/chromium-1234/chrome"),
        (301, 200, "/x/ms-playwright/firefox-1/firefox"),
        (400, 1, "python octowright serve"),
        (500, 400, "node .../playwright/cli.js run-driver"),
        (600, 500, "/x/ms-playwright/webkit-9/webkit"),
        (700, 1, "/x/ms-playwright/chromium-1234/chrome"),  # someone else's
    ]
    monkeypatch.setattr(process_reaper, "_list_processes", lambda: table)

    assert process_reaper.browser_pids_owned_by({100, 400}) == [300, 301, 600]


def test_owned_snapshot_survives_a_driver_that_has_not_exited_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact race: post-stop the driver is alive, so nothing reads as orphaned."""
    post_stop = [
        (1, 0, "/sbin/launchd"),
        (200, 1, "node .../playwright/cli.js run-driver"),  # reparented, still alive
        (300, 200, "/x/ms-playwright/chromium-1234/chrome"),
    ]
    monkeypatch.setattr(process_reaper, "_list_processes", lambda: post_stop)

    # The orphan heuristic alone finds nothing — this is the bug.
    assert process_reaper.find_browser_pids("orphaned") == []
    # The pre-kill snapshot still names the browser.
    assert process_reaper.browser_pids_owned_by({200}) == [300]


BROWSER = "/x/ms-playwright/chromium-1234/chrome"
INIT = (1, 0, "/sbin/launchd")
DRIVER = (200, 100, "node .../playwright/cli.js run-driver")


def _tables(monkeypatch: pytest.MonkeyPatch, *tables: list[tuple[int, int, str]]) -> None:
    """Feed successive process-table reads; the last table repeats forever.

    Stubbing `_list_processes` rather than `find_browser_pids` keeps the real
    browser-command filter and orphan heuristic in the loop, so these tests
    exercise the selection logic instead of asserting against a stubbed selector.
    """
    seq = list(tables)
    scans = iter(seq[:-1])
    monkeypatch.setattr(process_reaper, "_list_processes", lambda: next(scans, seq[-1]))


def _capture_signals(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    signalled: list[tuple[int, str]] = []

    def _fake_signal(pids: list[int], signum: int, stage: str) -> list[dict[str, str]]:
        signalled.extend((pid, stage) for pid in pids)
        return []

    monkeypatch.setattr(process_reaper, "_signal_pids", _fake_signal)
    monkeypatch.setattr(process_reaper.time, "sleep", lambda _s: None)
    return signalled


def test_reap_signals_the_verified_pids_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both snapshot pids are still live browsers, so both are signalled."""
    signalled = _capture_signals(monkeypatch)
    live = [INIT, (100, 1, "python octowright serve"), DRIVER, (300, 200, BROWSER), (301, 200, BROWSER)]
    _tables(monkeypatch, live, [INIT])  # verified live, then gone

    summary = process_reaper.reap_daemon_browsers([300, 301])

    assert [pid for pid, stage in signalled if stage == "sigterm"] == [300, 301]
    assert summary["killed"] == [300, 301]


def test_reap_unions_the_owned_snapshot_with_orphans(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot browsers plus anything an earlier generation orphaned."""
    signalled = _capture_signals(monkeypatch)
    live = [
        INIT,
        (100, 1, "python octowright serve"),
        DRIVER,
        (300, 200, BROWSER),
        (301, 200, BROWSER),
        (900, 1, BROWSER),  # orphaned by a previous generation
    ]
    _tables(monkeypatch, live, [INIT])

    process_reaper.reap_daemon_browsers([300, 301])

    assert sorted({pid for pid, stage in signalled if stage == "sigterm"}) == [300, 301, 900]


def test_signalled_pids_are_re_verified_as_browsers_at_kill_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """A snapshotted pid must be re-checked before it is signalled.

    The snapshot is taken BEFORE the daemon is stopped, and stopping it can take
    seconds (SIGTERM, wait out --timeout, escalate to SIGKILL, wait again). A
    browser in that snapshot can exit during the window and have its pid recycled
    by the OS to an unrelated process — so signalling the raw snapshot can SIGTERM
    a foreign pid. This repo already established the rule for exactly this hazard
    (`restart._locked_pid_is_octowright` verifies a lockfile pid's command line
    before killing it, because "a recorded pid can be recycled by the OS to an
    unrelated process after the daemon dies"), and the previous `scope="orphaned"`
    sweep upheld it implicitly by re-deriving its pid list from a live `ps` scan
    at kill time.

    So ownership comes from the snapshot; identity must come from a fresh scan.
    """
    signalled = _capture_signals(monkeypatch)
    # 300 is still a browser; 301 exited and its pid now belongs to something else.
    _tables(monkeypatch, [INIT, DRIVER, (300, 200, BROWSER), (301, 1, "/usr/bin/unrelated-daemon")])

    summary = process_reaper.reap_daemon_browsers([300, 301])

    assert 301 not in [pid for pid, _stage in signalled], "signalled a pid that is no longer a browser"
    assert 300 in [pid for pid, _stage in signalled]
    assert 301 not in summary["killed"], "reported killing a pid it must not have signalled"


def test_a_recycled_pid_is_not_reported_as_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never claim credit for a pid that was skipped as unverifiable."""
    signalled = _capture_signals(monkeypatch)
    _tables(monkeypatch, [INIT])  # no browsers at all

    summary = process_reaper.reap_daemon_browsers([300, 301])

    assert signalled == []
    assert summary["killed"] == []
    assert summary["still_alive"] == []


def test_stop_leader_snapshots_browsers_before_signalling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordering is the whole fix: the snapshot must precede the first signal."""
    order: list[str] = []

    monkeypatch.setattr(restart_mod, "_collect_target_pids", lambda *a, **kw: {100})
    monkeypatch.setattr(
        restart_mod,
        "browser_pids_owned_by",
        lambda pids: order.append("snapshot") or [300],  # type: ignore[func-returns-value]
    )
    monkeypatch.setattr(restart_mod, "_send_signal", lambda pid, sig: order.append("signal"))
    monkeypatch.setattr(restart_mod, "_escalate_survivors", lambda pids, timeout: [])
    monkeypatch.setattr(restart_mod.singleton, "remove_lock", lambda: None)

    stopped, killed, owned = restart_mod._stop_leader(1.0)

    assert order == ["snapshot", "signal"]
    assert (stopped, killed, owned) == (1, 0, [300])


def test_escalation_re_verifies_before_the_stronger_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The grace window is a SECOND chance for a pid to be recycled.

    SIGTERM goes out, then the sweep waits `grace_seconds` for the browser to
    exit cleanly before escalating. A pid that dies in that window and gets
    reused belongs to someone else by the time SIGKILL would land — so the
    escalation re-checks identity too, not just the first stage.

    Verified by mutation: neutering `_still_browser_pids` to `return list(pids)`
    left every other test in this file passing, which is how this gap was found.
    """
    signalled = _capture_signals(monkeypatch)
    live = [INIT, (100, 1, "python octowright serve"), DRIVER, (300, 200, BROWSER)]
    # Still a browser for the pre-signal check; by escalation time the pid has
    # been recycled to an unrelated process.
    recycled = [INIT, DRIVER, (300, 1, "/usr/bin/unrelated-daemon")]
    _tables(monkeypatch, live, recycled)

    process_reaper.reap_daemon_browsers([300])

    stages = [stage for _pid, stage in signalled]
    assert "sigterm" in stages, "should have signalled while it was still a browser"
    assert "sigkill" not in stages, "escalated to SIGKILL against a recycled pid"
