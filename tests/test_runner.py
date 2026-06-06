# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octowright.runner import _default_report_path, _is_test, _write_junit, run_suite


@pytest.fixture(autouse=True)
def _recordings_dir_under_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from octowright import defaults

    rec = tmp_path / "recordings"
    rec.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", rec)


# ---------------------------------------------------------------------------
# _is_test
# ---------------------------------------------------------------------------


class TestIsTest:
    def test_bare_test_tag_no_filter(self) -> None:
        macro = {"description": "[test] does something"}
        assert _is_test(macro, tag=None) is True

    def test_tagged_test_no_filter(self) -> None:
        macro = {"description": "[test:smoke] some smoke test"}
        assert _is_test(macro, tag=None) is True

    def test_tagged_test_matching_filter(self) -> None:
        macro = {"description": "[test:smoke] some smoke test"}
        assert _is_test(macro, tag="smoke") is True

    def test_tagged_test_non_matching_filter(self) -> None:
        macro = {"description": "[test:smoke] some smoke test"}
        assert _is_test(macro, tag="regression") is False

    def test_bare_tag_with_filter_excluded(self) -> None:
        # A bare [test] macro has group(1)==None, which != any string tag
        macro = {"description": "[test] generic"}
        assert _is_test(macro, tag="smoke") is False

    def test_non_test_description(self) -> None:
        macro = {"description": "just a regular macro"}
        assert _is_test(macro, tag=None) is False

    def test_empty_description(self) -> None:
        macro = {"description": ""}
        assert _is_test(macro, tag=None) is False

    def test_none_description(self) -> None:
        macro = {"description": None}
        assert _is_test(macro, tag=None) is False

    def test_missing_description_key(self) -> None:
        macro: dict[str, Any] = {}
        assert _is_test(macro, tag=None) is False

    def test_tag_prefix_not_at_start(self) -> None:
        macro = {"description": "preamble [test] something"}
        assert _is_test(macro, tag=None) is False


# ---------------------------------------------------------------------------
# _write_junit
# ---------------------------------------------------------------------------


class TestWriteJunit:
    def test_all_passing(self, tmp_path: Path) -> None:
        results = [
            {"name": "login", "ok": True, "error": None, "duration": 1.2},
            {"name": "checkout", "ok": True, "error": None, "duration": 0.8},
        ]
        report = tmp_path / "recordings" / "report.xml"
        _write_junit(results, report, kind="webkit")

        assert report.exists()
        tree = ET.parse(report)
        suite = tree.getroot()
        assert suite.tag == "testsuite"
        assert suite.attrib["tests"] == "2"
        assert suite.attrib["failures"] == "0"
        cases = suite.findall("testcase")
        assert len(cases) == 2
        assert cases[0].attrib["name"] == "login"
        assert cases[0].attrib["classname"] == "octowright.webkit"
        # No failure sub-element
        assert cases[0].find("failure") is None

    def test_with_failure(self, tmp_path: Path) -> None:
        results = [
            {"name": "login", "ok": False, "error": "RuntimeError('boom')", "duration": 0.5},
        ]
        report = tmp_path / "fail.xml"
        _write_junit(results, report, kind="chromium")

        tree = ET.parse(report)
        suite = tree.getroot()
        assert suite.attrib["failures"] == "1"
        case = suite.find("testcase")
        assert case is not None
        failure = case.find("failure")
        assert failure is not None
        assert failure.attrib["message"] == "RuntimeError('boom')"
        assert failure.text == "RuntimeError('boom')"

    def test_empty_results(self, tmp_path: Path) -> None:
        report = tmp_path / "empty.xml"
        _write_junit([], report, kind="firefox")
        tree = ET.parse(report)
        suite = tree.getroot()
        assert suite.attrib["tests"] == "0"
        assert suite.attrib["failures"] == "0"

    def test_xml_declaration_present(self, tmp_path: Path) -> None:
        report = tmp_path / "decl.xml"
        _write_junit([], report, kind="webkit")
        raw = report.read_bytes()
        assert raw.startswith(b"<?xml")


# ---------------------------------------------------------------------------
# _default_report_path
# ---------------------------------------------------------------------------


class TestDefaultReportPath:
    def test_returns_path_under_cwd(self) -> None:
        p = _default_report_path()
        assert isinstance(p, Path)
        assert p.parent == Path.cwd()

    def test_filename_contains_stamp(self) -> None:
        p = _default_report_path()
        # e.g. octowright-report-20260424T123456Z.xml
        assert p.name.startswith("octowright-report-")
        assert p.suffix == ".xml"
        # Basic ISO-ish stamp check: 8 digits, T, 6 digits, Z
        import re

        assert re.search(r"\d{8}T\d{6}Z", p.name)


# ---------------------------------------------------------------------------
# run_suite (end-to-end with mocked pool and macro_mod)
# ---------------------------------------------------------------------------


class TestRunSuite:
    @pytest.fixture()
    def fake_pool(self) -> MagicMock:
        pool = MagicMock()
        pool.launch = AsyncMock(return_value={"instance_id": "abc123"})
        pool.get = MagicMock(return_value=MagicMock())
        pool.close = AsyncMock(return_value={"closed": True})
        return pool

    @pytest.mark.asyncio
    async def test_no_test_macros(self, fake_pool: MagicMock, tmp_path: Path) -> None:
        macros_list = [{"name": "not-a-test"}]
        macros_full = {"name": "not-a-test", "description": "plain macro"}

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", return_value=macros_full),
            patch("octowright.runner.macro_mod.run_macro", new_callable=AsyncMock),
        ):
            result = await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "recordings" / "out.xml"),
                pool=fake_pool,
            )

        assert result["total"] == 0
        assert result["passed"] == 0
        assert result["failed"] == 0
        fake_pool.launch.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_passing_test(self, fake_pool: MagicMock, tmp_path: Path) -> None:
        macros_list = [{"name": "login-test"}]
        macros_full = {"name": "login-test", "description": "[test] login flow"}

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", return_value=macros_full),
            patch(
                "octowright.runner.macro_mod.run_macro",
                new_callable=AsyncMock,
                return_value={"macro": "login-test", "executed": 3, "skipped": 0, "args_used": {}},
            ),
        ):
            result = await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "recordings" / "out.xml"),
                pool=fake_pool,
            )

        assert result["total"] == 1
        assert result["passed"] == 1
        assert result["failed"] == 0
        assert result["results"][0]["ok"] is True
        fake_pool.launch.assert_called_once()
        fake_pool.close.assert_called_once_with("abc123", force=True)

    @pytest.mark.asyncio
    async def test_one_failing_test(self, fake_pool: MagicMock, tmp_path: Path) -> None:
        macros_list = [{"name": "bad-test"}]
        macros_full = {"name": "bad-test", "description": "[test] broken flow"}

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", return_value=macros_full),
            patch(
                "octowright.runner.macro_mod.run_macro",
                new_callable=AsyncMock,
                side_effect=RuntimeError("element not found"),
            ),
        ):
            result = await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "recordings" / "out.xml"),
                pool=fake_pool,
            )

        assert result["total"] == 1
        assert result["passed"] == 0
        assert result["failed"] == 1
        assert result["results"][0]["ok"] is False
        assert "RuntimeError" in result["results"][0]["error"]
        # close must still be called even on failure
        fake_pool.close.assert_called_once_with("abc123", force=True)

    @pytest.mark.asyncio
    async def test_tag_filter(self, fake_pool: MagicMock, tmp_path: Path) -> None:
        macros_list = [
            {"name": "smoke-test"},
            {"name": "regression-test"},
        ]

        def _load(name: str) -> dict[str, Any]:
            return {
                "smoke-test": {"name": "smoke-test", "description": "[test:smoke] smoke"},
                "regression-test": {"name": "regression-test", "description": "[test:regression] reg"},
            }[name]

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", side_effect=_load),
            patch(
                "octowright.runner.macro_mod.run_macro",
                new_callable=AsyncMock,
                return_value={"macro": "smoke-test", "executed": 1, "skipped": 0, "args_used": {}},
            ),
        ):
            result = await run_suite(
                kind="webkit",
                tag="smoke",
                out_path=str(tmp_path / "recordings" / "out.xml"),
                pool=fake_pool,
            )

        assert result["total"] == 1
        assert result["results"][0]["name"] == "smoke-test"

    @pytest.mark.asyncio
    async def test_report_written(self, fake_pool: MagicMock, tmp_path: Path) -> None:
        report_path = tmp_path / "recordings" / "report.xml"
        macros_list = [{"name": "t1"}]
        macros_full = {"name": "t1", "description": "[test] simple"}

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", return_value=macros_full),
            patch(
                "octowright.runner.macro_mod.run_macro",
                new_callable=AsyncMock,
                return_value={"macro": "t1", "executed": 1, "skipped": 0, "args_used": {}},
            ),
        ):
            result = await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(report_path),
                pool=fake_pool,
            )

        assert result["report_path"] == str(report_path)
        assert report_path.exists()
        tree = ET.parse(report_path)
        assert tree.getroot().tag == "testsuite"

    @pytest.mark.asyncio
    async def test_max_parallel_one_preserves_sequential_execution(
        self,
        fake_pool: MagicMock,
        tmp_path: Path,
    ) -> None:
        macros_list = [{"name": "t1"}, {"name": "t2"}]

        def _load(name: str) -> dict[str, Any]:
            return {"name": name, "description": "[test] flow"}

        active = 0
        max_active = 0

        async def _run_macro(*, session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return {"macro": name, "executed": 1, "skipped": 0, "args_used": args}

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", side_effect=_load),
            patch("octowright.runner.macro_mod.run_macro", side_effect=_run_macro),
        ):
            result = await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "recordings" / "out.xml"),
                pool=fake_pool,
                max_parallel=1,
            )

        assert result["total"] == 2
        assert result["passed"] == 2
        assert max_active == 1
        assert [r["name"] for r in result["results"]] == ["t1", "t2"]

    @pytest.mark.asyncio
    async def test_max_parallel_two_starts_multiple_tests_before_first_finishes(
        self,
        fake_pool: MagicMock,
        tmp_path: Path,
    ) -> None:
        macros_list = [{"name": "t1"}, {"name": "t2"}, {"name": "t3"}]
        launched_ids = iter(["i-1", "i-2", "i-3"])
        fake_pool.launch.side_effect = lambda **_kwargs: {"instance_id": next(launched_ids)}
        fake_pool.get.side_effect = lambda iid: MagicMock(instance_id=iid)
        second_started = asyncio.Event()
        started: list[str] = []

        def _load(name: str) -> dict[str, Any]:
            return {"name": name, "description": "[test] flow"}

        async def _run_macro(*, session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
            started.append(name)
            if name == "t1":
                await second_started.wait()
            if name == "t2":
                second_started.set()
            return {"macro": name, "executed": 1, "skipped": 0, "args_used": args}

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", side_effect=_load),
            patch("octowright.runner.macro_mod.run_macro", side_effect=_run_macro),
        ):
            result = await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "recordings" / "out.xml"),
                pool=fake_pool,
                max_parallel=2,
            )

        assert result["passed"] == 3
        assert started[:2] == ["t1", "t2"]
        assert fake_pool.launch.await_count == 3
        assert sorted(c.args[0] for c in fake_pool.close.await_args_list) == ["i-1", "i-2", "i-3"]

    @pytest.mark.asyncio
    async def test_closes_every_launched_browser_when_one_parallel_macro_fails(
        self,
        fake_pool: MagicMock,
        tmp_path: Path,
    ) -> None:
        macros_list = [{"name": "ok"}, {"name": "bad"}]
        launched_ids = iter(["i-ok", "i-bad"])
        fake_pool.launch.side_effect = lambda **_kwargs: {"instance_id": next(launched_ids)}
        fake_pool.get.side_effect = lambda iid: MagicMock(instance_id=iid)

        def _load(name: str) -> dict[str, Any]:
            return {"name": name, "description": "[test] flow"}

        async def _run_macro(*, session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "bad":
                raise RuntimeError("boom")
            return {"macro": name, "executed": 1, "skipped": 0, "args_used": args}

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", side_effect=_load),
            patch("octowright.runner.macro_mod.run_macro", side_effect=_run_macro),
        ):
            result = await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "recordings" / "out.xml"),
                pool=fake_pool,
                max_parallel=2,
            )

        assert result["passed"] == 1
        assert result["failed"] == 1
        assert sorted(c.args[0] for c in fake_pool.close.await_args_list) == ["i-bad", "i-ok"]

    @pytest.mark.asyncio
    async def test_launch_failure_returns_failed_result_and_still_writes_report(
        self,
        fake_pool: MagicMock,
        tmp_path: Path,
    ) -> None:
        report_path = tmp_path / "recordings" / "out.xml"
        macros_list = [{"name": "bad-launch"}, {"name": "ok"}]

        async def _launch(**kwargs: Any) -> dict[str, str]:
            if kwargs["label"] == "test-bad-launch":
                raise RuntimeError("launch failed")
            return {"instance_id": "i-ok"}

        def _load(name: str) -> dict[str, Any]:
            return {"name": name, "description": "[test] flow"}

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", side_effect=_load),
            patch(
                "octowright.runner.macro_mod.run_macro",
                new_callable=AsyncMock,
                return_value={"macro": "ok", "executed": 1, "skipped": 0, "args_used": {}},
            ),
        ):
            fake_pool.launch.side_effect = _launch
            result = await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(report_path),
                pool=fake_pool,
                max_parallel=2,
            )

        assert result["passed"] == 1
        assert result["failed"] == 1
        failed = next(r for r in result["results"] if r["name"] == "bad-launch")
        assert failed["ok"] is False
        assert failed["error"] == "RuntimeError('launch failed')"
        assert report_path.exists()
        tree = ET.parse(report_path)
        assert tree.getroot().attrib["failures"] == "1"
        fake_pool.close.assert_called_once_with("i-ok", force=True)

    @pytest.mark.asyncio
    async def test_close_failure_returns_failed_result_and_still_writes_report(
        self,
        fake_pool: MagicMock,
        tmp_path: Path,
    ) -> None:
        report_path = tmp_path / "recordings" / "out.xml"
        macros_list = [{"name": "close-fails"}]
        macros_full = {"name": "close-fails", "description": "[test] flow"}
        fake_pool.close.side_effect = RuntimeError("close failed")

        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
            patch("octowright.runner.macro_mod.load_macro", return_value=macros_full),
            patch(
                "octowright.runner.macro_mod.run_macro",
                new_callable=AsyncMock,
                return_value={"macro": "close-fails", "executed": 1, "skipped": 0, "args_used": {}},
            ),
        ):
            result = await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(report_path),
                pool=fake_pool,
                max_parallel=1,
            )

        # Teardown failure on an otherwise-passing test must NOT fail the
        # test — instead the close error is attached as a teardown_warning
        # and the test stays ok=True.
        assert result["passed"] == 1
        assert result["failed"] == 0
        assert result["results"][0]["ok"] is True
        assert result["results"][0]["error"] is None
        assert result["results"][0]["teardown_warning"] == "RuntimeError('close failed')"
        assert report_path.exists()
        tree = ET.parse(report_path)
        assert tree.getroot().attrib["failures"] == "0"
        fake_pool.close.assert_called_once_with("abc123", force=True)

    @pytest.mark.asyncio
    async def test_rejects_max_parallel_less_than_one(self, fake_pool: MagicMock, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="max_parallel"):
            await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "recordings" / "out.xml"),
                pool=fake_pool,
                max_parallel=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_out_path_outside_recordings(self, fake_pool: MagicMock, tmp_path: Path) -> None:
        with (
            patch("octowright.runner.macro_mod.list_macros", return_value=[]),
            pytest.raises(ValueError, match="suite report path"),
        ):
            await run_suite(
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "outside.xml"),
                pool=fake_pool,
            )
