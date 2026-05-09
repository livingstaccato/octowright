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
