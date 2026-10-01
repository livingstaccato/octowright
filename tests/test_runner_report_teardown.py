# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""`octowright test`: the report is always written, where it is allowed, and a
failed browser close neither loses it nor leaks exception text into it."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octowright import defaults, runner

PLANTED = "close-planted-secret"  # pragma: allowlist secret


@pytest.fixture
def recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", rec)
    return rec


def fake_pool(close_exc: BaseException | None = None) -> MagicMock:
    pool = MagicMock()
    pool.launch = AsyncMock(return_value={"instance_id": "abc123"})
    pool.get = MagicMock(return_value=MagicMock())
    pool.close = AsyncMock(side_effect=close_exc) if close_exc else AsyncMock(return_value={"closed": True})
    return pool


def suite_patches(run_macro: Any) -> Any:
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("octowright.runner.macro_mod.list_macros", return_value=[{"name": "t1"}]))
    stack.enter_context(
        patch(
            "octowright.runner.macro_mod.load_macro",
            side_effect=lambda n: {"name": n, "description": "[test] t", "actions": []},
        )
    )
    stack.enter_context(patch("octowright.runner.macro_mod.run_macro", side_effect=run_macro))
    return stack


async def passing(*_: Any, **__: Any) -> dict[str, Any]:
    return {"executed": 1}


async def failing(*_: Any, **__: Any) -> dict[str, Any]:
    raise RuntimeError(f"boom {PLANTED}")


def sequence_file(tmp_path: Path, steps: list[dict[str, Any]] | None = None) -> Path:
    path = tmp_path / "smoke.json"
    path.write_text(json.dumps(steps or [{"macro": "m1"}, {"macro": "m2"}]))
    return path


def run_seq(tmp_path: Path, pool: Any, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "kind": "chromium",
        "persona": None,
        "artifacts": None,
        "redact_errors": False,
        "out_path": None,
        "pool": pool,
    }
    kwargs.update(overrides)
    if "sequence" not in kwargs:
        kwargs["sequence"] = sequence_file(tmp_path)
    return asyncio.run(runner.run_sequence_file(**kwargs))


# --- 1. --redact-errors covers a close failure too ---------------------------


@pytest.mark.parametrize("run_macro", [passing, failing], ids=["test-passed", "test-failed"])
def test_redacted_suite_keeps_close_error_text_out_of_everything(recordings: Path, run_macro: Any) -> None:
    report = recordings / "s.xml"
    with suite_patches(run_macro), patch.object(runner, "log") as log:
        result = asyncio.run(
            runner.run_suite(
                kind="chromium",
                pool=fake_pool(RuntimeError(f"close {PLANTED}")),
                out_path=str(report),
                redact_errors=True,
            )
        )
    assert PLANTED not in report.read_text()
    assert PLANTED not in json.dumps(result)
    assert PLANTED not in repr(log.mock_calls)
    case = result["results"][0]
    if case["ok"]:
        assert case["teardown_warning"] == "RuntimeError"
    else:
        assert case["error"] == "RuntimeError; close failed: RuntimeError"


def test_unredacted_suite_still_reports_the_close_error(recordings: Path) -> None:
    with suite_patches(passing):
        result = asyncio.run(
            runner.run_suite(
                kind="chromium", pool=fake_pool(RuntimeError("close x")), out_path=str(recordings / "s.xml")
            )
        )
    assert result["results"][0]["teardown_warning"] == "RuntimeError('close x')"


# --- 2. a sequence whose close fails still writes its report -----------------


def test_sequence_close_failure_is_a_warning_and_the_report_is_written(tmp_path: Path, recordings: Path) -> None:
    report = recordings / "r.xml"
    with patch("octowright.runner.macro_mod.run_macro", side_effect=passing):
        result = run_seq(tmp_path, fake_pool(RuntimeError(f"close {PLANTED}")), out_path=str(report))
    assert report.is_file()
    assert (result["passed"], result["failed"]) == (2, 0)
    assert "teardown_warning" not in result["results"][0]
    assert result["results"][-1]["teardown_warning"] == f"RuntimeError('close {PLANTED}')"


def test_sequence_close_failure_joins_the_failing_step_redacted(tmp_path: Path, recordings: Path) -> None:
    report = recordings / "r.xml"

    async def second_fails(*_: Any, name: str, **__: Any) -> dict[str, Any]:
        if name == "m2":
            raise RuntimeError(f"boom {PLANTED}")
        return {}

    steps = [{"macro": "m1"}, {"macro": "m2"}, {"macro": "m3"}]
    with patch("octowright.runner.macro_mod.run_macro", side_effect=second_fails):
        result = run_seq(
            tmp_path,
            fake_pool(RuntimeError(f"close {PLANTED}")),
            sequence=sequence_file(tmp_path, steps),
            out_path=str(report),
            redact_errors=True,
        )
    assert PLANTED not in report.read_text()
    assert PLANTED not in json.dumps(result)
    assert result["results"][1]["error"] == "RuntimeError; close failed: RuntimeError"
    assert result["results"][2].get("skipped") is True


# --- 3. the report's directory is created ------------------------------------


def test_suite_creates_the_report_directory(recordings: Path) -> None:
    report = recordings / "smoke" / "octowright-report.xml"
    with suite_patches(passing):
        asyncio.run(runner.run_suite(kind="chromium", pool=fake_pool(), out_path=str(report)))
    assert report.is_file()


# --- 4. a refused report path fails before any browser launches -------------


def test_suite_refuses_an_outside_report_path_before_launching(tmp_path: Path, recordings: Path) -> None:
    pool = fake_pool()
    with suite_patches(passing), pytest.raises(ValueError, match="suite report path"):
        asyncio.run(runner.run_suite(kind="chromium", pool=pool, out_path=str(tmp_path / "dist" / "x.xml")))
    pool.launch.assert_not_called()


def test_sequence_refuses_an_outside_report_path_before_launching(tmp_path: Path, recordings: Path) -> None:
    pool = fake_pool()
    with (
        patch("octowright.runner.macro_mod.run_macro", side_effect=passing),
        pytest.raises(ValueError, match="suite report path"),
    ):
        run_seq(tmp_path, pool, out_path=str(tmp_path / "dist" / "x.xml"))
    pool.launch.assert_not_called()


def test_sequence_default_report_lands_under_the_recordings_root(
    tmp_path: Path, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # outside the recordings root, as a CI checkout is
    with patch("octowright.runner.macro_mod.run_macro", side_effect=passing):
        result = run_seq(tmp_path, fake_pool())
    report = Path(result["report_path"])
    assert report.is_file()
    assert report.parent == recordings.resolve()
    assert report.name.startswith("octowright-report-")


def test_suite_default_report_lands_under_the_recordings_root(
    tmp_path: Path, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with suite_patches(passing):
        result = asyncio.run(runner.run_suite(kind="chromium", pool=fake_pool()))
    report = Path(result["report_path"])
    assert report.is_file()
    assert report.parent == recordings.resolve()


# --- 7. one persona profile cannot be opened by parallel browsers ------------


def test_suite_refuses_a_persona_with_parallel_tests(recordings: Path) -> None:
    pool = fake_pool()
    with suite_patches(passing), pytest.raises(ValueError, match="persona"):
        asyncio.run(
            runner.run_suite(
                kind="chromium", pool=pool, persona="lab", max_parallel=2, out_path=str(recordings / "s.xml")
            )
        )
    pool.launch.assert_not_called()
