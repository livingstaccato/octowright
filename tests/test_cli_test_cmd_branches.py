# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.cli.test_cmd."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from octowright.cli._root import cli


def _result(*, passed: int, failed: int, total: int, report_path: str = "/tmp/junit.xml") -> dict[str, Any]:
    """Build a fake TestSuiteResult shaped dict."""
    return {
        "passed": passed,
        "failed": failed,
        "total": total,
        "report_path": report_path,
        "results": [],
    }


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_value: dict[str, Any],
    capture: dict[str, Any] | None = None,
) -> AsyncMock:
    """Patch runner.run_suite to return the canned result; capture kwargs."""
    from octowright import runner as _runner

    async def fake_run_suite(**kwargs: Any) -> dict[str, Any]:
        if capture is not None:
            capture.update(kwargs)
        return return_value

    monkeypatch.setattr(_runner, "run_suite", fake_run_suite)
    # Also stub BrowserPool to avoid spinning up real Playwright.
    from octowright import browser_pool as _bp

    pool_stub = MagicMock()
    pool_stub.shutdown = AsyncMock()
    monkeypatch.setattr(_bp, "BrowserPool", lambda *_a, **_kw: pool_stub)
    return pool_stub


class TestTestCmdDefaults:
    def test_passes_kind_default_webkit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No --kind → defaults to 'webkit'."""
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=1, failed=0, total=1), capture=captured)
        result = CliRunner().invoke(cli, ["test"])
        assert result.exit_code == 0
        assert captured["kind"] == "webkit"

    def test_passes_tag_none_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No --tag → tag=None passed through."""
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0), capture=captured)
        CliRunner().invoke(cli, ["test"])
        assert captured["tag"] is None

    def test_passes_out_path_none_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No --out → out_path=None."""
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0), capture=captured)
        CliRunner().invoke(cli, ["test"])
        assert captured["out_path"] is None

    def test_passes_max_parallel_default_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No --max-parallel → 1 (sequential)."""
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0), capture=captured)
        CliRunner().invoke(cli, ["test"])
        assert captured["max_parallel"] == 1


class TestTestCmdOptions:
    def test_kind_option_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--kind firefox → captured."""
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0), capture=captured)
        CliRunner().invoke(cli, ["test", "--kind", "firefox"])
        assert captured["kind"] == "firefox"

    def test_tag_option_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--tag smoke → captured."""
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0), capture=captured)
        CliRunner().invoke(cli, ["test", "--tag", "smoke"])
        assert captured["tag"] == "smoke"

    def test_out_option_passes_through(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        """--out /path/to/junit.xml → captured."""
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0), capture=captured)
        out = str(tmp_path / "junit.xml")
        CliRunner().invoke(cli, ["test", "--out", out])
        assert captured["out_path"] == out

    def test_max_parallel_option_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--max-parallel 4 → captured."""
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0), capture=captured)
        CliRunner().invoke(cli, ["test", "--max-parallel", "4"])
        assert captured["max_parallel"] == 4

    def test_max_parallel_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--max-parallel 0 fails IntRange(min=1)."""
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0))
        result = CliRunner().invoke(cli, ["test", "--max-parallel", "0"])
        assert result.exit_code != 0


class TestTestCmdExitCode:
    def test_all_passing_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """failed=0 → SystemExit(0)."""
        _patch_runner(monkeypatch, return_value=_result(passed=3, failed=0, total=3))
        result = CliRunner().invoke(cli, ["test"])
        assert result.exit_code == 0

    def test_any_failure_exits_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """failed > 0 → SystemExit(1)."""
        _patch_runner(monkeypatch, return_value=_result(passed=2, failed=1, total=3))
        result = CliRunner().invoke(cli, ["test"])
        assert result.exit_code == 1

    def test_zero_total_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No tests at all → still passes (failed=0)."""
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0))
        result = CliRunner().invoke(cli, ["test"])
        assert result.exit_code == 0


class TestTestCmdOutput:
    def test_summary_line_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'<passed>/<total> passed' echoed."""
        _patch_runner(monkeypatch, return_value=_result(passed=2, failed=1, total=3))
        result = CliRunner().invoke(cli, ["test"])
        assert "2/3 passed" in result.output

    def test_report_path_echoed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'report: <path>' echoed."""
        _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0, report_path="/tmp/abc.xml"))
        result = CliRunner().invoke(cli, ["test"])
        assert "report: /tmp/abc.xml" in result.output


class TestTestCmdShutdown:
    def test_pool_shutdown_called_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pool.shutdown awaited even on success path (finally clause)."""
        pool = _patch_runner(monkeypatch, return_value=_result(passed=0, failed=0, total=0))
        CliRunner().invoke(cli, ["test"])
        pool.shutdown.assert_awaited()

    def test_pool_shutdown_called_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pool.shutdown still called when runner raises."""
        from octowright import browser_pool as _bp
        from octowright import runner as _runner

        async def boom(**_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("nope")

        monkeypatch.setattr(_runner, "run_suite", boom)
        pool_stub = MagicMock()
        pool_stub.shutdown = AsyncMock()
        monkeypatch.setattr(_bp, "BrowserPool", lambda *_a, **_kw: pool_stub)
        result = CliRunner().invoke(cli, ["test"])
        assert result.exit_code != 0
        pool_stub.shutdown.assert_awaited()


PLANTED = "cli-planted-secret"  # pragma: allowlist secret


def _patch_sequence(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_value: dict[str, Any] | None = None,
    raises: BaseException | None = None,
    capture: dict[str, Any] | None = None,
) -> None:
    from octowright import runner as _runner

    async def fake_run_sequence_file(**kwargs: Any) -> dict[str, Any]:
        if capture is not None:
            capture.update(kwargs)
        if raises is not None:
            raise raises
        assert return_value is not None
        return return_value

    async def no_suite(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("run_suite must not run for --sequence")

    monkeypatch.setattr(_runner, "run_sequence_file", fake_run_sequence_file)
    monkeypatch.setattr(_runner, "run_suite", no_suite)
    from octowright import browser_pool as _bp

    pool_stub = MagicMock()
    pool_stub.shutdown = AsyncMock()
    monkeypatch.setattr(_bp, "BrowserPool", lambda *_a, **_kw: pool_stub)


class TestTestCmdSequence:
    def test_sequence_runs_as_the_persona_with_artifacts(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sequence = tmp_path / "j.json"
        sequence.write_text("[]")
        captured: dict[str, Any] = {}
        _patch_sequence(monkeypatch, return_value=_result(passed=2, failed=0, total=2), capture=captured)
        result = CliRunner().invoke(
            cli,
            ["test", "--kind", "chromium", "--persona", "lab", "--sequence", str(sequence),
             "--artifacts", str(tmp_path / "ev"), "--redact-errors"],
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        assert captured["persona"] == "lab"
        assert captured["kind"] == "chromium"
        assert str(captured["sequence"]) == str(sequence)
        assert str(captured["artifacts"]) == str(tmp_path / "ev")
        assert captured["redact_errors"] is True
        assert captured["out_path"] == str(tmp_path / "ev" / "octowright-report.xml")

    def test_sequence_and_tag_are_exclusive(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sequence = tmp_path / "j.json"
        sequence.write_text("[]")
        _patch_sequence(monkeypatch, return_value=_result(passed=0, failed=0, total=0))
        result = CliRunner().invoke(cli, ["test", "--sequence", str(sequence), "--tag", "smoke"])
        assert result.exit_code == 2
        assert "exclusive" in result.output

    def test_a_failed_sequence_exits_one(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sequence = tmp_path / "j.json"
        sequence.write_text("[]")
        _patch_sequence(monkeypatch, return_value=_result(passed=1, failed=1, total=2))
        result = CliRunner().invoke(cli, ["test", "--sequence", str(sequence)])
        assert result.exit_code == 1

    def test_a_sequence_that_cannot_resolve_says_why_and_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        from octowright.sequences import SequenceError

        sequence = tmp_path / "j.json"
        sequence.write_text("[]")
        _patch_sequence(monkeypatch, raises=SequenceError("step 0 argument 'p': a credential argument needs --persona"))
        result = CliRunner().invoke(cli, ["test", "--sequence", str(sequence)])
        assert result.exit_code == 1
        assert "needs --persona" in result.output

    def test_redacted_run_prints_no_exception_text(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sequence = tmp_path / "j.json"
        sequence.write_text("[]")
        _patch_sequence(monkeypatch, raises=OSError(f"cannot open {PLANTED}"))
        result = CliRunner().invoke(cli, ["test", "--sequence", str(sequence), "--redact-errors"])
        assert result.exit_code == 1
        assert PLANTED not in result.output
        assert "OSError" in result.output

    def test_suite_receives_persona_and_redaction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=1, failed=0, total=1), capture=captured)
        result = CliRunner().invoke(cli, ["test", "--persona", "lab", "--redact-errors"])
        assert result.exit_code == 0, result.output
        assert captured["persona"] == "lab"
        assert captured["redact_errors"] is True


def _patch_recording_sequence(
    monkeypatch: pytest.MonkeyPatch,
    video: str,
    *,
    capture: dict[str, Any],
    raises: BaseException | None = None,
    failed: int = 0,
) -> None:
    """A run_sequence_file that 'records' one video the way the runner does:
    appended to the caller's list, before any exception escapes."""
    from pathlib import Path

    from octowright import runner as _runner

    async def fake_run_sequence_file(**kwargs: Any) -> dict[str, Any]:
        capture.update(kwargs)
        if kwargs.get("videos") is not None:
            kwargs["videos"].append(Path(video))
        if raises is not None:
            raise raises
        return _result(passed=2 - failed, failed=failed, total=2)

    monkeypatch.setattr(_runner, "run_sequence_file", fake_run_sequence_file)
    from octowright import browser_pool as _bp

    pool_stub = MagicMock()
    pool_stub.shutdown = AsyncMock()
    monkeypatch.setattr(_bp, "BrowserPool", lambda *_a, **_kw: pool_stub)


#: How the CLI prints the fake video path on this platform (backslashes on Windows).
_VIDEO = __import__("pathlib").Path("/rec/ev/repair.webm")


class TestTestCmdRecordVideo:
    def test_the_flag_asks_the_runner_for_videos(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sequence = tmp_path / "repair.json"
        sequence.write_text("[]")
        captured: dict[str, Any] = {}
        _patch_recording_sequence(monkeypatch, "/rec/ev/repair.webm", capture=captured)
        result = CliRunner().invoke(cli, ["test", "--sequence", str(sequence), "--record-video"])
        assert result.exit_code == 0, result.output
        assert captured["videos"] == [__import__("pathlib").Path("/rec/ev/repair.webm")]

    def test_the_video_line_follows_the_report_line(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sequence = tmp_path / "repair.json"
        sequence.write_text("[]")
        _patch_recording_sequence(monkeypatch, "/rec/ev/repair.webm", capture={})
        result = CliRunner().invoke(cli, ["test", "--sequence", str(sequence), "--record-video"])
        assert result.stdout == f"2/2 passed\nreport: /tmp/junit.xml\nvideo: {_VIDEO}\n"

    def test_without_the_flag_nothing_changes(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sequence = tmp_path / "repair.json"
        sequence.write_text("[]")
        captured: dict[str, Any] = {}
        _patch_recording_sequence(monkeypatch, "/rec/ev/repair.webm", capture=captured)
        result = CliRunner().invoke(cli, ["test", "--sequence", str(sequence)])
        assert captured["videos"] is None
        assert result.stdout == "2/2 passed\nreport: /tmp/junit.xml\n"

    def test_a_failed_run_still_prints_its_video(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sequence = tmp_path / "repair.json"
        sequence.write_text("[]")
        _patch_recording_sequence(monkeypatch, "/rec/ev/repair.webm", capture={}, failed=1)
        result = CliRunner().invoke(cli, ["test", "--sequence", str(sequence), "--record-video", "--redact-errors"])
        assert result.exit_code == 1
        assert result.stdout.endswith(f"report: /tmp/junit.xml\nvideo: {_VIDEO}\n")

    def test_a_run_that_raises_still_prints_its_video(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sequence = tmp_path / "repair.json"
        sequence.write_text("[]")
        _patch_recording_sequence(
            monkeypatch, "/rec/ev/repair.webm", capture={}, raises=OSError(f"cannot open {PLANTED}")
        )
        result = CliRunner().invoke(cli, ["test", "--sequence", str(sequence), "--record-video", "--redact-errors"])
        assert result.exit_code == 1
        assert f"video: {_VIDEO}\n" in result.output
        assert "test run failed: OSError" in result.output
        assert PLANTED not in result.output

    def test_suite_receives_videos_and_artifacts(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=1, failed=0, total=1), capture=captured)
        result = CliRunner().invoke(cli, ["test", "--record-video", "--artifacts", str(tmp_path / "ev")])
        assert result.exit_code == 0, result.output
        assert captured["videos"] == []
        assert str(captured["artifacts"]) == str(tmp_path / "ev")


class TestTestCmdRefusals:
    def test_a_persona_with_parallel_tests_is_refused_up_front(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}
        _patch_runner(monkeypatch, return_value=_result(passed=1, failed=0, total=1), capture=captured)
        result = CliRunner().invoke(cli, ["test", "--persona", "lab", "--max-parallel", "2"])
        assert result.exit_code == 2
        assert "--persona" in result.output and "--max-parallel" in result.output
        assert captured == {}

    def test_a_report_path_outside_the_recordings_root_fails_before_any_browser(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        from octowright import browser_pool as _bp
        from octowright import defaults

        monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path / "recordings")
        pool_stub = MagicMock()
        pool_stub.launch = AsyncMock()
        pool_stub.shutdown = AsyncMock()
        monkeypatch.setattr(_bp, "BrowserPool", lambda *_a, **_kw: pool_stub)
        result = CliRunner().invoke(cli, ["test", "--out", str(tmp_path / "dist" / "x.xml")])
        assert result.exit_code == 1
        assert "test run refused: suite report path" in result.output
        assert "Traceback" not in result.output
        pool_stub.launch.assert_not_called()


def test_an_interrupted_run_still_prints_its_videos(capsys: pytest.CaptureFixture[str]) -> None:
    from pathlib import Path

    from octowright.cli.test_cmd import _run_and_report

    videos = [Path("/rec/ev/smoke.webm")]

    async def interrupted() -> Any:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run_and_report(interrupted, redact_errors=False, videos=videos)
    assert f"video: {videos[0]}" in capsys.readouterr().out
