# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Fail a run whose pytest process ends holding sockets it did not start with.

Why it exists: a Windows leg once failed a navigation to a loopback test
server with ``net::ERR_NO_BUFFER_SPACE`` (``WSAENOBUFS``), the error Windows
gives when it runs out of ephemeral ports. Measuring every test that runs
before that point found no leak to explain it (the process never held more than
nine sockets at any test's end) -- but a test server or client left open per
test is exactly how a serial run *would* get there, and on Linux nothing would
notice. This turns that into a red run on the platform that can see it.

How: after each test's whole protocol (setup, call, teardown) the socket
inodes in ``/proc/self/fd`` are read, and each new one is remembered against
the test that was running when it appeared. At session end, after a
``gc.collect()``, any socket still open that was not open at session start is
a leak; past ``LEAK_BOUND`` the run fails and names the tests that opened them.

Fail rather than warn, because a warning in a ~10k-test run is not read. The
bound is what keeps that from being flaky: measured over the whole suite in 53
chunked processes, the excess at session end was 0 in 52 and 2 in one. The only
transient holders found are handler threads still sleeping
inside a deliberately slow response (``time.sleep(20)`` in
``test_macro_network_clean_no_text_live``), each of which pins one listener and
one connection until it returns -- a handful at most, against a bound of 16.

Linux only: it needs ``/proc/self/fd``. Elsewhere every hook is a no-op. The
per-test cost is one ``listdir`` plus a ``readlink`` per fd that is new since
the previous test.
"""

from __future__ import annotations

import gc
import os
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

# Sockets above the session-start set that may remain at session end.
LEAK_BOUND = 16
# How many offending tests the failure names.
WORST_TESTS = 5

_FD_DIR = Path("/proc/self/fd")
_TCP_TABLES = (Path("/proc/net/tcp"), Path("/proc/net/tcp6"))
_TCP_STATES = {"01": "ESTABLISHED", "06": "TIME_WAIT", "08": "CLOSE_WAIT", "0A": "LISTEN"}


def socket_inodes(fd_dir: Path = _FD_DIR) -> set[str]:
    """Return the inode of every socket fd open in this process."""
    inodes: set[str] = set()
    base = str(fd_dir)
    try:
        names = os.listdir(base)
    except OSError:
        return inodes
    for name in names:
        try:
            target = os.readlink(f"{base}/{name}")
        except OSError:
            # Closed between listdir and readlink (the listdir fd itself).
            continue
        if target.startswith("socket:["):
            inodes.add(target[len("socket:[") : -1])
    return inodes


def describe(inodes: set[str]) -> dict[str, str]:
    """Map each inode to ``STATE port`` for TCP sockets, ``non-tcp`` otherwise."""
    found: dict[str, str] = {}
    for table in _TCP_TABLES:
        try:
            lines = table.read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) > 9 and parts[9] in inodes:
                port = int(parts[1].rsplit(":", 1)[1], 16)
                found[parts[9]] = f"{_TCP_STATES.get(parts[3], parts[3])} :{port}"
    return {inode: found.get(inode, "non-tcp") for inode in inodes}


class SocketLeakTripwire:
    """pytest plugin; registered from ``tests/conftest.py``'s ``pytest_configure``."""

    def __init__(self, fd_dir: Path = _FD_DIR, bound: int = LEAK_BOUND) -> None:
        self._fd_dir = fd_dir
        self._bound = bound
        self._enabled = fd_dir.is_dir()
        self._baseline: set[str] = set()
        self._opened_by: dict[str, str] = {}
        # fd name -> socket inode (or "" for a non-socket), from the last scan.
        self._fd_targets: dict[str, str] = {}
        self.report_lines: list[str] = []

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        del session
        if self._enabled:
            self._baseline = socket_inodes(self._fd_dir)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Iterator[None]:
        yield
        if self._enabled:
            self.record(item.nodeid)

    def record(self, nodeid: str) -> None:
        """Attribute sockets that appeared since the last test to *nodeid*.

        Only fds that are new since the previous test are ``readlink``-ed, so a
        test that opens nothing costs one ``listdir`` (a full scan per test was
        measured at ~0.7ms, ~1% of a unit-heavy run). The price is that a fd
        number closed and reused for another socket within one test keeps its
        old target here; that only blurs attribution, never detection, because
        ``leaked`` does a full scan.
        """
        base = str(self._fd_dir)
        try:
            names = os.listdir(base)
        except OSError:
            return
        targets: dict[str, str] = {}
        for name in names:
            known = self._fd_targets.get(name)
            if known is None:
                try:
                    link = os.readlink(f"{base}/{name}")
                except OSError:
                    continue
                known = link[len("socket:[") : -1] if link.startswith("socket:[") else ""
            targets[name] = known
        self._fd_targets = targets
        current = {inode for inode in targets.values() if inode} - self._baseline
        # Forget closed sockets so the map stays the size of what is open.
        self._opened_by = {inode: self._opened_by.get(inode, nodeid) for inode in current}

    def leaked(self) -> dict[str, str]:
        """Inode -> opening test, for every socket open now but not at start."""
        if not self._enabled:
            return {}
        gc.collect()
        current = socket_inodes(self._fd_dir) - self._baseline
        return {inode: self._opened_by.get(inode, "<session-level hook>") for inode in current}

    def check(self) -> bool:
        """Return True when the leak is past the bound; fills ``report_lines``."""
        leaked = self.leaked()
        if len(leaked) <= self._bound:
            return False
        states = describe(set(leaked))
        per_test = Counter(leaked.values())
        self.report_lines = [
            f"socket leak: the pytest process ends holding {len(leaked)} sockets it did not "
            f"start with (bound {self._bound}). Close servers/clients in teardown "
            "(server_close(), context managers). Tests that opened them:",
        ]
        for nodeid, count in per_test.most_common(WORST_TESTS):
            kinds = Counter(states[i].split()[0] for i, n in leaked.items() if n == nodeid)
            self.report_lines.append(f"  {count:4d}  {nodeid}  {dict(kinds)}")
        return True

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if not self.check():
            return
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep("=", "socket leak tripwire", red=True)
            for line in self.report_lines:
                reporter.write_line(line, red=True)
        if exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
