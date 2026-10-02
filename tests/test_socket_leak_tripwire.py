# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The session-end socket-leak tripwire names the test that left a socket open."""

from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._socket_leak_tripwire import SocketLeakTripwire, socket_inodes

pytestmark = pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="reads /proc/self/fd")


def _started(bound: int) -> SocketLeakTripwire:
    wire = SocketLeakTripwire(bound=bound)
    wire.pytest_sessionstart(session=None)  # type: ignore[arg-type]
    return wire


def test_a_socket_left_open_is_reported_against_the_test_that_opened_it() -> None:
    wire = _started(bound=0)
    wire.record("tests/x.py::before")
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        wire.record("tests/x.py::leaker")
        wire.record("tests/x.py::after")
        assert wire.check()
        assert "tests/x.py::leaker" in "\n".join(wire.report_lines)
        assert "tests/x.py::after" not in "\n".join(wire.report_lines)
        assert "LISTEN" in "\n".join(wire.report_lines)
    finally:
        listener.close()


def test_a_closed_socket_is_not_a_leak() -> None:
    wire = _started(bound=0)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        wire.record("tests/x.py::closes")
    assert not wire.check()


def test_the_bound_tolerates_a_few_stragglers() -> None:
    wire = _started(bound=2)
    first, second = socket.socket(), socket.socket()
    try:
        wire.record("tests/x.py::two")
        assert not wire.check()
    finally:
        first.close()
        second.close()


def test_a_socket_already_open_at_session_start_is_not_counted() -> None:
    with socket.socket():
        wire = _started(bound=0)
        wire.record("tests/x.py::anything")
        assert not wire.check()


def test_socket_inodes_sees_this_process_sockets() -> None:
    before = socket_inodes()
    with socket.socket():
        assert len(socket_inodes() - before) == 1


def _session(exitstatus: int) -> SimpleNamespace:
    manager = SimpleNamespace(get_plugin=lambda _name: None)
    return SimpleNamespace(exitstatus=exitstatus, config=SimpleNamespace(pluginmanager=manager))


def test_it_fails_a_green_session_and_leaves_a_red_one_alone() -> None:
    wire = _started(bound=0)
    leaked = socket.socket()
    try:
        wire.record("tests/x.py::leaker")
        green = _session(pytest.ExitCode.OK)
        wire.pytest_sessionfinish(green, pytest.ExitCode.OK)  # type: ignore[arg-type]
        assert green.exitstatus == pytest.ExitCode.TESTS_FAILED
        red = _session(pytest.ExitCode.INTERRUPTED)
        wire.pytest_sessionfinish(red, pytest.ExitCode.INTERRUPTED)  # type: ignore[arg-type]
        assert red.exitstatus == pytest.ExitCode.INTERRUPTED
    finally:
        leaked.close()
