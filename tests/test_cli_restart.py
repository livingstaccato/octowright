# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for ``octowright restart``.

The command shells out to OS process facilities, so each test pins the
subprocess + signal + http-probe surfaces and asserts the *intent* of the
command rather than running real daemons.
"""

from __future__ import annotations

import signal
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from octowright.cli import restart as _restart_mod
from octowright.cli._root import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block anything that would shell out by default; tests opt back in via stubs."""

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("real subprocess.run not expected in this test")

    monkeypatch.setattr(_restart_mod.subprocess, "run", _boom)


@pytest.fixture
def stub_no_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend nothing is running."""
    monkeypatch.setattr(_restart_mod.singleton, "read_lock", lambda *_a, **_kw: None)
    monkeypatch.setattr(_restart_mod, "_leader_pids_from_pgrep", lambda: [])
    monkeypatch.setattr(_restart_mod.singleton, "remove_lock", lambda *_a, **_kw: None)


def test_help_documents_keep_browsers_and_no_start(runner: CliRunner) -> None:
    """The two operational flags must be discoverable from `--help`."""
    result = runner.invoke(cli, ["restart", "--help"])
    assert result.exit_code == 0, result.output
    assert "--keep-browsers" in result.output
    assert "--no-start" in result.output
    assert "--timeout" in result.output
    assert "--http-port" in result.output


@pytest.mark.usefixtures("stub_no_leader")
def test_no_start_skips_spawn_and_reaps_browsers(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--no-start`` must stop + reap + return without calling _spawn_daemon."""
    reap_calls: list[None] = []
    monkeypatch.setattr(
        _restart_mod,
        "reap_orphan_browsers",
        lambda *_a, **_kw: (reap_calls.append(None), {"killed": [], "still_alive": [], "errors": []})[1],
    )
    monkeypatch.setattr(
        _restart_mod,
        "_spawn_daemon",
        lambda *_a, **_kw: pytest.fail("must not spawn when --no-start is set"),
    )

    result = runner.invoke(cli, ["restart", "--no-start", "--timeout", "1"])

    assert result.exit_code == 0, result.output
    assert "no running octowright daemon found" in result.output
    assert len(reap_calls) == 1
    assert "not starting a new daemon" in result.output


@pytest.mark.usefixtures("stub_no_leader")
def test_keep_browsers_skips_reaper(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--keep-browsers`` must skip the reaper sweep."""
    monkeypatch.setattr(
        _restart_mod,
        "reap_orphan_browsers",
        lambda *_a, **_kw: pytest.fail("must not reap when --keep-browsers is set"),
    )
    monkeypatch.setattr(
        _restart_mod,
        "_spawn_daemon",
        lambda *_a, **_kw: pytest.fail("must not spawn when --no-start is set"),
    )

    result = runner.invoke(cli, ["restart", "--no-start", "--keep-browsers", "--timeout", "1"])

    assert result.exit_code == 0, result.output
    assert "orphan browsers" not in result.output


def test_stop_escalates_to_sigkill_on_holdouts(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a leader doesn't exit after SIGTERM within timeout, SIGKILL is sent."""
    monkeypatch.setattr(_restart_mod.singleton, "read_lock", lambda *_a, **_kw: None)
    monkeypatch.setattr(_restart_mod, "_leader_pids_from_pgrep", lambda: [12345])
    monkeypatch.setattr(_restart_mod.singleton, "remove_lock", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _restart_mod,
        "reap_orphan_browsers",
        lambda *_a, **_kw: {"killed": [], "still_alive": [], "errors": []},
    )

    sent_signals: list[tuple[int, int]] = []

    def _send(pid: int, sig: int) -> bool:
        sent_signals.append((pid, sig))
        return True

    monkeypatch.setattr(_restart_mod, "_send_signal", _send)
    # Process never exits — forces escalation.
    monkeypatch.setattr(_restart_mod.singleton, "pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(_restart_mod.time, "sleep", lambda _s: None)

    # Each call to time.monotonic returns an ever-growing virtual clock so
    # the wait loops always blow past their deadline on the second poll —
    # forces SIGTERM-wait + SIGKILL-wait to fall through quickly.
    _clock = [0.0]

    def _tick() -> float:
        _clock[0] += 5.0
        return _clock[0]

    monkeypatch.setattr(_restart_mod.time, "monotonic", _tick)
    monkeypatch.setattr(
        _restart_mod,
        "_spawn_daemon",
        lambda *_a, **_kw: pytest.fail("must not spawn when --no-start is set"),
    )

    result = runner.invoke(cli, ["restart", "--no-start", "--timeout", "1"])

    assert result.exit_code == 0, result.output
    sigs_sent_to_pid = [sig for pid, sig in sent_signals if pid == 12345]
    assert signal.SIGTERM in sigs_sent_to_pid
    # Windows has no SIGKILL — restart.py falls back to SIGTERM as the
    # "force" signal there, matching process_reaper.KILL_SIGNAL.
    assert _restart_mod._FORCE_KILL in sigs_sent_to_pid
    assert "escalating to SIGKILL" in result.output


def test_process_fallback_skips_bare_follower_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restart must not kill bare ``serve`` followers owned by MCP clients."""

    def fake_run(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(
            stdout="\n".join(
                [
                    "101 /Users/tim/.venv/bin/python /bin/octowright serve",
                    "202 /Users/tim/.venv/bin/python /bin/octowright serve --daemon-mode",
                    "303 uv run octowright serve --http-host 127.0.0.1 --http-port 8765",
                    "404 /Users/tim/.venv/bin/python /bin/octowright restart",
                    "505 /Users/tim/.venv/bin/python /bin/octowright serve --profile core",
                ]
            )
        )

    monkeypatch.setattr(_restart_mod.subprocess, "run", fake_run)

    assert _restart_mod._leader_pids_from_pgrep() == [202, 303]


def test_list_process_commands_windows_parses_powershell_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows path must parse PowerShell ConvertTo-Csv output correctly."""
    monkeypatch.setattr(_restart_mod.sys, "platform", "win32")

    csv_output = "\n".join(
        [
            '"ProcessId","CommandLine"',
            '"101","C:\\venv\\Scripts\\octowright serve"',
            '"202","C:\\venv\\Scripts\\octowright serve --daemon-mode"',
            '"303","C:\\venv\\Scripts\\octowright serve --http-host 127.0.0.1 --http-port 8765"',
            '"404","C:\\venv\\Scripts\\octowright restart"',
            '"505",""',  # process with empty command line
        ]
    )

    monkeypatch.setattr(
        _restart_mod.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(stdout=csv_output),
    )

    rows = _restart_mod._list_process_commands()
    assert rows == [
        (101, "C:\\venv\\Scripts\\octowright serve"),
        (202, "C:\\venv\\Scripts\\octowright serve --daemon-mode"),
        (303, "C:\\venv\\Scripts\\octowright serve --http-host 127.0.0.1 --http-port 8765"),
        (404, "C:\\venv\\Scripts\\octowright restart"),
        (505, ""),
    ]


def test_leader_pids_from_pgrep_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows the leader-pid fallback must use PowerShell and filter correctly."""
    monkeypatch.setattr(_restart_mod.sys, "platform", "win32")

    csv_output = "\n".join(
        [
            '"ProcessId","CommandLine"',
            '"101","C:\\venv\\Scripts\\octowright serve"',
            '"202","C:\\venv\\Scripts\\octowright serve --daemon-mode"',
            '"303","C:\\venv\\Scripts\\octowright serve --http-port 8765"',
        ]
    )

    monkeypatch.setattr(
        _restart_mod.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(stdout=csv_output),
    )

    assert sorted(_restart_mod._leader_pids_from_pgrep()) == [202, 303]


def test_follower_pids_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows ``_follower_pids`` must identify bare serve processes."""
    monkeypatch.setattr(_restart_mod.sys, "platform", "win32")

    csv_output = "\n".join(
        [
            '"ProcessId","CommandLine"',
            '"101","C:\\venv\\Scripts\\octowright serve"',
            '"202","C:\\venv\\Scripts\\octowright serve --daemon-mode"',
            '"303","C:\\venv\\Scripts\\octowright serve --profile core"',
        ]
    )

    monkeypatch.setattr(
        _restart_mod.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(stdout=csv_output),
    )

    assert sorted(_restart_mod._follower_pids()) == [101, 303]


@pytest.mark.usefixtures("stub_no_leader")
def test_spawn_passes_http_host_and_port_through(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The daemon spawn must include the --http-host and --http-port the
    user passed to restart, so the health probe afterwards is checking the
    same endpoint the daemon was asked to bind."""
    popen_calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, argv: list[str], **_kw: Any) -> None:
            popen_calls.append(argv)
            self.pid = 99999

    monkeypatch.setattr(_restart_mod.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(_restart_mod, "_resolve_octowright_entry", lambda: "/fake/octowright")
    monkeypatch.setattr(
        _restart_mod,
        "reap_orphan_browsers",
        lambda *_a, **_kw: {"killed": [], "still_alive": [], "errors": []},
    )
    monkeypatch.setattr(_restart_mod, "_wait_for_health", lambda *_a, **_kw: "http://127.0.0.1:9876/")
    monkeypatch.setattr(_restart_mod, "_wait_for_port_free", lambda *_a, **_kw: True)

    result = runner.invoke(
        cli,
        ["restart", "--http-host", "127.0.0.1", "--http-port", "9876", "--timeout", "1"],
    )

    assert result.exit_code == 0, result.output
    assert popen_calls, "subprocess.Popen must be called to spawn the daemon"
    argv = popen_calls[0]
    assert argv[0] == "/fake/octowright"
    assert argv[1] == "serve"
    assert "--http-host" in argv and "127.0.0.1" in argv
    assert "--http-port" in argv and "9876" in argv
    assert "daemon healthy at http://127.0.0.1:9876/" in result.output


@pytest.mark.usefixtures("stub_no_leader")
def test_health_probe_failure_returns_nonzero(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the spawned daemon never serves HTTP 200, restart exits non-zero."""
    monkeypatch.setattr(
        _restart_mod,
        "reap_orphan_browsers",
        lambda *_a, **_kw: {"killed": [], "still_alive": [], "errors": []},
    )
    monkeypatch.setattr(_restart_mod, "_spawn_daemon", lambda *_a, **_kw: 9999)
    monkeypatch.setattr(_restart_mod, "_wait_for_health", lambda *_a, **_kw: None)
    monkeypatch.setattr(_restart_mod, "_wait_for_port_free", lambda *_a, **_kw: True)

    result = runner.invoke(cli, ["restart", "--timeout", "1"])

    assert result.exit_code == 1
    assert "did not become healthy" in result.output


@pytest.mark.usefixtures("stub_no_leader")
def test_restart_refuses_fallback_port_when_requested_port_busy(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _restart_mod,
        "reap_orphan_browsers",
        lambda *_a, **_kw: {"killed": [], "still_alive": [], "errors": []},
    )
    monkeypatch.setattr(_restart_mod, "_wait_for_port_free", lambda *_a, **_kw: False)
    monkeypatch.setattr(
        _restart_mod,
        "_spawn_daemon",
        lambda *_a, **_kw: pytest.fail("must not spawn on fallback port when requested port is busy"),
    )

    result = runner.invoke(cli, ["restart", "--timeout", "1"])

    assert result.exit_code == 1
    assert "not starting a daemon on a fallback port" in result.output


def test_wait_for_health_follows_lockfile_auto_bumped_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the daemon auto-bumps its port, restart must probe the lockfile URL."""
    calls: list[str] = []

    monkeypatch.setattr(
        _restart_mod.singleton,
        "read_lock",
        lambda *_a, **_kw: SimpleNamespace(pid=123, http_host="127.0.0.1", http_port=8766),
    )
    monkeypatch.setattr(_restart_mod.singleton, "pid_is_alive", lambda _pid: True)

    class _Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    def fake_get(url: str, **_kw: Any) -> _Response:
        calls.append(url)
        return _Response(200 if url == "http://127.0.0.1:8766/api/health" else 503)

    monkeypatch.setattr("httpx.get", fake_get)

    assert _restart_mod._wait_for_health("127.0.0.1", 8765, timeout=1) == "http://127.0.0.1:8766/"
    assert calls[:2] == [
        "http://127.0.0.1:8765/api/health",
        "http://127.0.0.1:8766/api/health",
    ]


def test_resolve_octowright_entry_prefers_venv_neighbour(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Path(sys.executable).parent / 'octowright' must win over PATH discovery."""
    bin_dir = tmp_path / "venv-bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("")
    fake_octowright = bin_dir / "octowright"
    fake_octowright.write_text("")

    monkeypatch.setattr(_restart_mod.sys, "executable", str(fake_python))
    resolved = _restart_mod._resolve_octowright_entry()
    assert resolved == str(fake_octowright)


def test_kill_followers_flag_is_documented_in_help(runner: CliRunner) -> None:
    """``--kill-followers`` must appear in help output."""
    result = runner.invoke(cli, ["restart", "--help"])
    assert result.exit_code == 0, result.output
    assert "--kill-followers" in result.output


def test_looks_like_follower_identifies_bare_serve() -> None:
    """Bare ``octowright serve`` (no daemon flags) must be classified as a follower."""
    assert _restart_mod._looks_like_follower("/venv/bin/python /venv/bin/octowright serve")
    assert _restart_mod._looks_like_follower("uv run octowright serve")
    assert _restart_mod._looks_like_follower("uv run octowright serve --profile core")


def test_looks_like_follower_rejects_daemon_processes() -> None:
    """Processes with daemon flags must NOT be classified as followers."""
    assert not _restart_mod._looks_like_follower("octowright serve --daemon-mode")
    assert not _restart_mod._looks_like_follower("octowright serve --http-host 127.0.0.1")
    assert not _restart_mod._looks_like_follower("octowright serve --http-port 8765")
    assert not _restart_mod._looks_like_follower("octowright restart")
    assert not _restart_mod._looks_like_follower("unrelated process")


def test_follower_pids_returns_bare_serve_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    """_follower_pids must return PIDs for bare serve processes only."""

    def fake_run(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(
            stdout="\n".join(
                [
                    "101 /venv/bin/python /venv/bin/octowright serve",
                    "202 /venv/bin/python /venv/bin/octowright serve --daemon-mode",
                    "303 uv run octowright serve --http-host 127.0.0.1 --http-port 8765",
                    "404 /venv/bin/python /venv/bin/octowright serve --profile core",
                ]
            )
        )

    monkeypatch.setattr(_restart_mod.subprocess, "run", fake_run)
    assert sorted(_restart_mod._follower_pids()) == [101, 404]


@pytest.mark.usefixtures("stub_no_leader")
def test_kill_followers_flag_sweeps_follower_pids(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--kill-followers`` must call SIGTERM on discovered follower PIDs."""
    monkeypatch.setattr(_restart_mod, "_follower_pids", lambda: [777, 888])
    monkeypatch.setattr(_restart_mod.singleton, "pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(
        _restart_mod,
        "reap_orphan_browsers",
        lambda *_a, **_kw: {"killed": [], "still_alive": [], "errors": []},
    )

    sent_signals: list[tuple[int, int]] = []
    monkeypatch.setattr(_restart_mod, "_send_signal", lambda pid, sig: sent_signals.append((pid, sig)) or True)

    result = runner.invoke(cli, ["restart", "--kill-followers", "--no-start", "--timeout", "1"])

    assert result.exit_code == 0, result.output
    killed_pids = {pid for pid, sig in sent_signals if sig == signal.SIGTERM}
    assert 777 in killed_pids
    assert 888 in killed_pids
    assert "follower" in result.output


def test_port_is_free_sets_reuseaddr_so_time_wait_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-flight check must set SO_REUSEADDR so a TIME_WAIT socket from the
    just-stopped daemon reads as free — matching what the new daemon (which also
    sets SO_REUSEADDR/SO_REUSEPORT) can actually bind. Without it, restart sits
    through the full TIME_WAIT timeout for nothing."""
    import socket as _socket

    opts: list[tuple[int, int, int]] = []

    class _FakeSocket:
        def __init__(self, family: int, socktype: int, proto: int) -> None:
            pass

        def setsockopt(self, level: int, optname: int, value: int) -> None:
            opts.append((level, optname, value))

        def bind(self, sockaddr: tuple[object, ...]) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(_restart_mod.socket, "getaddrinfo", lambda *_a, **_k: [(2, 1, 6, "", ("127.0.0.1", 6286))])
    monkeypatch.setattr(_restart_mod.socket, "socket", _FakeSocket)

    assert _restart_mod._port_is_free("127.0.0.1", 6286) is True
    assert (_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1) in opts
