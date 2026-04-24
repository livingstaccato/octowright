from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octowright.runner import _default_report_path, _is_test, _write_junit, run_suite

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
        report = tmp_path / "report.xml"
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
                macros_dir=None,
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "out.xml"),
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
                macros_dir=None,
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "out.xml"),
                pool=fake_pool,
            )

        assert result["total"] == 1
        assert result["passed"] == 1
        assert result["failed"] == 0
        assert result["results"][0]["ok"] is True
        fake_pool.launch.assert_called_once()
        fake_pool.close.assert_called_once_with("abc123")

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
                macros_dir=None,
                kind="webkit",
                tag=None,
                out_path=str(tmp_path / "out.xml"),
                pool=fake_pool,
            )

        assert result["total"] == 1
        assert result["passed"] == 0
        assert result["failed"] == 1
        assert result["results"][0]["ok"] is False
        assert "RuntimeError" in result["results"][0]["error"]
        # close must still be called even on failure
        fake_pool.close.assert_called_once_with("abc123")

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
                macros_dir=None,
                kind="webkit",
                tag="smoke",
                out_path=str(tmp_path / "out.xml"),
                pool=fake_pool,
            )

        assert result["total"] == 1
        assert result["results"][0]["name"] == "smoke-test"

    @pytest.mark.asyncio
    async def test_report_written(self, fake_pool: MagicMock, tmp_path: Path) -> None:
        report_path = tmp_path / "report.xml"
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
                macros_dir=None,
                kind="webkit",
                tag=None,
                out_path=str(report_path),
                pool=fake_pool,
            )

        assert result["report_path"] == str(report_path)
        assert report_path.exists()
        tree = ET.parse(report_path)
        assert tree.getroot().tag == "testsuite"
