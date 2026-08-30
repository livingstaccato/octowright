# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright doctor`` — the checks, not the browsers.

The engine probe's whole job is to launch a real browser, so it is exercised
here through its subprocess boundary (a fake child that prints a canned result)
rather than by launching three engines in a unit test. What that boundary
buys is testability as well as isolation: the probe's contract is "run this
source, read the last JSON line", and both halves of that are checkable
without a browser anywhere.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from octowright import doctor as _doctor


class TestProbeOutputParsing:
    def test_reads_the_last_json_line_not_the_first(self) -> None:
        """Engines write their own noise to stdout; the result is always last."""
        out = b'{"ok": false, "steps": []}\nsome engine chatter\n{"ok": true, "steps": [{"step": "close"}]}\n'
        assert _doctor._parse_probe_output(out) == {"ok": True, "steps": [{"step": "close"}]}

    def test_ignores_unparsable_json_lines(self) -> None:
        assert _doctor._parse_probe_output(b'{not json\n{"ok": true}\n') == {"ok": True}

    def test_no_output_is_none_rather_than_an_error(self) -> None:
        assert _doctor._parse_probe_output(b"") is None
        assert _doctor._parse_probe_output(None) is None

    def test_a_bare_json_list_is_not_mistaken_for_a_result(self) -> None:
        """Only a mapping is a result; a list would break every .get() below."""
        assert _doctor._parse_probe_output(b"[1, 2, 3]\n") is None


class TestFailingStepAttribution:
    def test_names_the_first_step_that_did_not_complete(self) -> None:
        """The whole diagnostic value: 'goto', not 'something went wrong'."""
        steps = [{"step": "driver_start"}, {"step": "launch"}, {"step": "new_context"}, {"step": "new_page"}]
        assert _doctor._next_step_after(steps) == "goto"

    def test_nothing_completed_means_the_driver_never_started(self) -> None:
        assert _doctor._next_step_after([]) == "driver_start"

    def test_everything_completed_reports_unknown(self) -> None:
        done = [
            {"step": s}
            for s in (
                "driver_start",
                "launch",
                "new_context",
                "new_page",
                "goto",
                "evaluate",
                "add_init_script",
                "close",
            )
        ]
        assert _doctor._next_step_after(done) == "unknown"


class TestEngineProbe:
    """Drive the real subprocess path with a source that needs no browser."""

    async def test_a_healthy_probe_reports_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _doctor,
            "_engine_probe_source",
            lambda kind: 'import json; print(json.dumps({"ok": True, "steps": [{"step": "close"}]}))',
        )
        check = await _doctor.probe_engine("chromium", timeout=30)
        assert check.status == "ok"
        assert check.name == "engine:chromium"

    async def test_a_failing_probe_names_the_step(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = '{"ok": false, "steps": [{"step": "driver_start"}, {"step": "launch"}], "error": "boom"}'
        monkeypatch.setattr(_doctor, "_engine_probe_source", lambda kind: f"print({payload!r})")
        check = await _doctor.probe_engine("webkit", timeout=30)
        assert check.status == "fail"
        # 'new_context' is the first step missing from the two that completed.
        assert "new_context" in check.detail
        assert "boom" in check.detail

    async def test_a_hung_probe_is_killed_and_reported_as_hung(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The case the command exists for: no result, ever, until we stop waiting."""
        monkeypatch.setattr(_doctor, "_engine_probe_source", lambda kind: "import time; time.sleep(120)")
        check = await _doctor.probe_engine("webkit", timeout=1.0)
        assert check.status == "fail"
        assert check.data["hung"] is True
        assert "HUNG" in check.detail

    async def test_a_silent_probe_is_a_failure_not_a_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A child that exits printing nothing must never read as healthy."""
        monkeypatch.setattr(_doctor, "_engine_probe_source", lambda kind: "import sys; sys.exit(3)")
        check = await _doctor.probe_engine("firefox", timeout=30)
        assert check.status == "fail"


class TestStrayDrivers:
    def _table(self, rows: list[tuple[int, int, str]]) -> Any:
        return rows

    def test_a_driver_whose_parent_is_alive_is_not_stray(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A running daemon's own driver must never be reported or reaped."""
        monkeypatch.setattr(
            _doctor,
            "_process_table",
            lambda: [(100, 1, "python daemon"), (200, 100, "/x/playwright/driver/node /y")],
        )
        assert _doctor.stray_driver_pids() == []

    def test_a_reparented_driver_is_stray(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_doctor, "_process_table", lambda: [(200, 1, "/x/playwright/driver/node /y")])
        assert _doctor.stray_driver_pids() == [200]

    def test_a_driver_whose_parent_pid_is_gone_is_stray(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_doctor, "_process_table", lambda: [(200, 999, "/x/playwright/driver/node /y")])
        assert _doctor.stray_driver_pids() == [200]

    def test_non_driver_processes_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_doctor, "_process_table", lambda: [(200, 1, "/usr/bin/node server.js")])
        assert _doctor.stray_driver_pids() == []

    def test_windows_backslash_paths_still_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The path separator differs per platform; the check must not."""
        monkeypatch.setattr(_doctor, "_process_table", lambda: [(200, 1, r"C:\x\playwright\driver\node.exe C:\y")])
        assert _doctor.stray_driver_pids() == [200]


class TestBrowserInstallCheck:
    def test_present_builds_pass(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        for name in ("chromium-1234", "firefox-1538", "webkit-2336"):
            (tmp_path / name).mkdir()
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        assert _doctor.check_browser_installs().status == "ok"

    def test_missing_builds_warn_and_name_them(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        (tmp_path / "chromium-1234").mkdir()
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        check = _doctor.check_browser_installs()
        assert check.status == "warn"
        assert set(check.data["missing"]) == {"firefox", "webkit"}

    def test_a_missing_root_is_a_warning_not_a_crash(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "nope"))
        assert _doctor.check_browser_installs().status == "warn"


class TestStorageCheck:
    def test_group_readable_profiles_are_flagged(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Profiles hold live session cookies; 0755 is a real finding."""
        recordings, profiles, macros = tmp_path / "rec", tmp_path / "prof", tmp_path / "mac"
        for path in (recordings, profiles, macros):
            path.mkdir()
        os.chmod(profiles, 0o755)
        os.chmod(recordings, 0o700)
        monkeypatch.setattr("octowright.defaults.RECORDINGS_DIR", recordings)
        monkeypatch.setattr("octowright.defaults.PROFILES_DIR", profiles)
        monkeypatch.setattr("octowright.defaults.MACROS_DIR", macros)
        check = _doctor.check_storage()
        assert check.status == "warn"
        assert "profiles" in check.detail

    def test_owner_only_roots_pass(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        recordings, profiles, macros = tmp_path / "rec", tmp_path / "prof", tmp_path / "mac"
        for path in (recordings, profiles, macros):
            path.mkdir()
            os.chmod(path, 0o700)
        monkeypatch.setattr("octowright.defaults.RECORDINGS_DIR", recordings)
        monkeypatch.setattr("octowright.defaults.PROFILES_DIR", profiles)
        monkeypatch.setattr("octowright.defaults.MACROS_DIR", macros)
        assert _doctor.check_storage().status == "ok"

    def test_a_macros_dir_that_is_group_readable_is_not_flagged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Only the roots that hold credentials are held to 0700."""
        recordings, profiles, macros = tmp_path / "rec", tmp_path / "prof", tmp_path / "mac"
        for path in (recordings, profiles, macros):
            path.mkdir()
            os.chmod(path, 0o700)
        os.chmod(macros, 0o755)
        monkeypatch.setattr("octowright.defaults.RECORDINGS_DIR", recordings)
        monkeypatch.setattr("octowright.defaults.PROFILES_DIR", profiles)
        monkeypatch.setattr("octowright.defaults.MACROS_DIR", macros)
        assert _doctor.check_storage().status == "ok"


class TestWorstStatus:
    def test_fail_outranks_warn(self) -> None:
        checks = [_doctor.Check("a", "warn", ""), _doctor.Check("b", "fail", "")]
        assert _doctor.worst_status(checks) == "fail"

    def test_warn_outranks_ok(self) -> None:
        checks = [_doctor.Check("a", "ok", ""), _doctor.Check("b", "warn", "")]
        assert _doctor.worst_status(checks) == "warn"

    def test_all_skipped_is_skip(self) -> None:
        assert _doctor.worst_status([_doctor.Check("a", "skip", "")]) == "skip"


class TestReapSafety:
    def test_dry_run_signals_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`doctor` without --fix must never kill anything."""
        killed: list[int] = []
        monkeypatch.setattr(_doctor, "stray_driver_pids", lambda: [4242])
        monkeypatch.setattr(_doctor.os, "kill", lambda pid, sig: killed.append(pid))
        monkeypatch.setattr(
            "octowright.process_reaper.reap_orphan_browsers",
            lambda scope, **kw: {"killed": [], "still_alive": [], "errors": []},
        )
        result = _doctor.reap(dry_run=True)
        assert killed == []
        assert result["drivers"] == [4242]

    def test_fix_signals_each_stray_driver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        killed: list[int] = []
        monkeypatch.setattr(_doctor, "stray_driver_pids", lambda: [1, 2])
        monkeypatch.setattr(_doctor.os, "kill", lambda pid, sig: killed.append(pid))
        monkeypatch.setattr(
            "octowright.process_reaper.reap_orphan_browsers",
            lambda scope, **kw: {"killed": [], "still_alive": [], "errors": []},
        )
        _doctor.reap(dry_run=False)
        assert killed == [1, 2]

    def test_one_unkillable_pid_does_not_abort_the_rest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A pid that died between scan and signal is normal, not fatal."""
        killed: list[int] = []

        def _kill(pid: int, sig: int) -> None:
            if pid == 1:
                raise ProcessLookupError
            killed.append(pid)

        monkeypatch.setattr(_doctor, "stray_driver_pids", lambda: [1, 2])
        monkeypatch.setattr(_doctor.os, "kill", _kill)
        monkeypatch.setattr(
            "octowright.process_reaper.reap_orphan_browsers",
            lambda scope, **kw: {"killed": [], "still_alive": [], "errors": []},
        )
        _doctor.reap(dry_run=False)
        assert killed == [2]


class TestCli:
    def test_skip_engines_runs_without_launching_anything(self) -> None:
        result = CliRunner().invoke(_cli(), ["doctor", "--skip-engines"])
        assert result.exit_code == 0, result.output
        assert "SKIP" in result.output

    def test_json_output_is_machine_readable(self) -> None:
        import json

        result = CliRunner().invoke(_cli(), ["doctor", "--skip-engines", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["status"] in {"ok", "warn"}
        assert any(c["name"] == "daemon" for c in payload["checks"])

    def test_a_failed_check_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CI gates on the exit code, so a FAIL must not exit 0."""

        async def _fake(**kwargs: Any) -> list[_doctor.Check]:
            return [_doctor.Check("engine:webkit", "fail", "broken")]

        monkeypatch.setattr(_doctor, "run_checks", _fake)
        result = CliRunner().invoke(_cli(), ["doctor"])
        assert result.exit_code == 1


def _cli() -> Any:
    from octowright.cli import cli

    return cli
