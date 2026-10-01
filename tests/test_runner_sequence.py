# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""`octowright test --sequence` runs one persona browser through a sequence."""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octowright import defaults, personas, runner, sequences

PLANTED = "planted-secret-value"  # pragma: allowlist secret


@pytest.fixture
def recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", rec)
    return rec


@pytest.fixture
def lab_persona(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    profiles = tmp_path / "profiles"
    (profiles / "lab").mkdir(parents=True)
    (profiles / "lab" / "profile.yaml").write_text("name: lab\ncredentials:\n  password_env: LAB_PW\n")
    monkeypatch.setattr(personas, "PROFILES_DIR", profiles)
    monkeypatch.setenv("LAB_PW", PLANTED)
    return "lab"


def fake_pool() -> MagicMock:
    pool = MagicMock()
    pool.launch = AsyncMock(return_value={"instance_id": "abc123"})
    pool.get = MagicMock(return_value=MagicMock())
    pool.close = AsyncMock(return_value={"closed": True})
    return pool


def sequence_file(tmp_path: Path, steps: list[dict[str, Any]]) -> Path:
    path = tmp_path / "sequence.json"
    path.write_text(json.dumps(steps))
    return path


THREE_STEPS = [
    {"macro": "m1", "args": {"password": {"credential": "password"}}},
    {"macro": "m2", "args": {"password": {"credential": "password"}}},
    {"macro": "m3", "args": {}},
]


def failing_second(calls: list[tuple[str, dict[str, Any]]]):
    async def run_macro(session: Any, name: str, args: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        calls.append((name, dict(args or {})))
        if name == "m2":
            raise RuntimeError(
                {"macro": "m2", "failed_at_step": 1, "failed_action": {"action": "fill"}, "detail": PLANTED}
            )
        return {"macro": name, "executed": 1, "skipped": 0}

    return run_macro


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_redact_error_keeps_only_macro_step_and_action() -> None:
    payload = {
        "macro": "bmf-login",
        "failed_at_step": 3,
        "failed_action": {"action": "fill", "value": PLANTED},
        "bundle": {"console": PLANTED},
    }
    assert runner.redact_error(RuntimeError(payload)) == "macro bmf-login failed at step 3 (fill)"


def test_redact_error_drops_any_other_message() -> None:
    assert runner.redact_error(ValueError(f"boom {PLANTED}")) == "ValueError"


def test_sequence_launches_one_browser_with_the_persona(tmp_path: Path, recordings: Path, lab_persona: str) -> None:
    pool, calls = fake_pool(), []
    with patch("octowright.runner.macro_mod.run_macro", side_effect=failing_second(calls)):
        run(
            runner.run_sequence_file(
                sequence=sequence_file(tmp_path, THREE_STEPS[:1]),
                kind="chromium",
                persona=lab_persona,
                artifacts=None,
                redact_errors=False,
                out_path=str(recordings / "r.xml"),
                pool=pool,
            )
        )
    pool.launch.assert_awaited_once()
    kwargs = pool.launch.await_args.kwargs
    assert kwargs["profile"] == "lab"
    assert kwargs["headed"] is False
    assert kwargs["url"] == "about:blank"
    pool.close.assert_awaited_once_with("abc123", force=True)


def test_sequence_passes_resolved_args_to_each_macro(tmp_path: Path, recordings: Path, lab_persona: str) -> None:
    pool, calls = fake_pool(), []
    with patch("octowright.runner.macro_mod.run_macro", side_effect=failing_second(calls)):
        run(
            runner.run_sequence_file(
                sequence=sequence_file(tmp_path, THREE_STEPS[:1]),
                kind="chromium",
                persona=lab_persona,
                artifacts=None,
                redact_errors=False,
                out_path=str(recordings / "r.xml"),
                pool=pool,
            )
        )
    assert calls == [("m1", {"password": PLANTED})]


def test_sequence_junit_has_a_case_per_step_and_skips_after_failure(
    tmp_path: Path, recordings: Path, lab_persona: str
) -> None:
    pool, calls = fake_pool(), []
    report = recordings / "r.xml"
    with patch("octowright.runner.macro_mod.run_macro", side_effect=failing_second(calls)):
        result = run(
            runner.run_sequence_file(
                sequence=sequence_file(tmp_path, THREE_STEPS),
                kind="chromium",
                persona=lab_persona,
                artifacts=None,
                redact_errors=False,
                out_path=str(report),
                pool=pool,
            )
        )
    assert [name for name, _ in calls] == ["m1", "m2"]
    assert (result["total"], result["passed"], result["failed"]) == (3, 1, 2)
    suite = ET.parse(report).getroot()
    assert (suite.get("failures"), suite.get("skipped")) == ("1", "1")
    cases = suite.findall("testcase")
    assert [c.get("name") for c in cases] == ["m1", "m2", "m3"]
    assert cases[0].find("failure") is None and cases[0].find("skipped") is None
    assert cases[1].find("failure") is not None
    assert cases[2].find("skipped") is not None


def test_redacted_sequence_leaks_nothing(
    tmp_path: Path, recordings: Path, lab_persona: str, capsys: pytest.CaptureFixture[str]
) -> None:
    pool, calls = fake_pool(), []
    report = recordings / "r.xml"
    with patch("octowright.runner.macro_mod.run_macro", side_effect=failing_second(calls)):
        result = run(
            runner.run_sequence_file(
                sequence=sequence_file(tmp_path, THREE_STEPS),
                kind="chromium",
                persona=lab_persona,
                artifacts=None,
                redact_errors=True,
                out_path=str(report),
                pool=pool,
            )
        )
    assert PLANTED not in report.read_text()
    assert PLANTED not in json.dumps(result)
    captured = capsys.readouterr()
    assert PLANTED not in captured.out + captured.err
    assert result["results"][1]["error"] == "macro m2 failed at step 1 (fill)"


def test_unresolvable_credential_fails_before_launch(tmp_path: Path, recordings: Path, lab_persona: str) -> None:
    pool = fake_pool()
    steps = [{"macro": "m1", "args": {"token": {"credential": "token"}}}]
    with pytest.raises(sequences.SequenceError, match="step 0 argument 'token'"):
        run(
            runner.run_sequence_file(
                sequence=sequence_file(tmp_path, steps),
                kind="chromium",
                persona=lab_persona,
                artifacts=None,
                redact_errors=True,
                out_path=str(recordings / "r.xml"),
                pool=pool,
            )
        )
    pool.launch.assert_not_called()


def test_suite_honours_persona(recordings: Path) -> None:
    pool = fake_pool()
    macros_list = [{"name": "t1"}]
    macros_full = {"name": "t1", "description": "[test] one", "actions": []}
    with (
        patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
        patch("octowright.runner.macro_mod.load_macro", return_value=macros_full),
        patch("octowright.runner.macro_mod.run_macro", new_callable=AsyncMock),
    ):
        run(runner.run_suite(kind="chromium", pool=pool, persona="lab", out_path=str(recordings / "s.xml")))
    assert pool.launch.await_args.kwargs["profile"] == "lab"


def test_suite_redacts_errors_when_asked(recordings: Path) -> None:
    pool = fake_pool()
    macros_list = [{"name": "t1"}]
    macros_full = {"name": "t1", "description": "[test] one", "actions": []}
    report = recordings / "s.xml"
    with (
        patch("octowright.runner.macro_mod.list_macros", return_value=macros_list),
        patch("octowright.runner.macro_mod.load_macro", return_value=macros_full),
        patch("octowright.runner.macro_mod.run_macro", new_callable=AsyncMock, side_effect=ValueError(PLANTED)),
    ):
        result = run(runner.run_suite(kind="chromium", pool=pool, redact_errors=True, out_path=str(report)))
    assert result["results"][0]["error"] == "ValueError"
    assert PLANTED not in report.read_text()


def test_without_out_the_report_lands_in_the_artifacts_directory(
    tmp_path: Path, recordings: Path, lab_persona: str
) -> None:
    pool, calls = fake_pool(), []
    artifacts = recordings / "run"
    with patch("octowright.runner.macro_mod.run_macro", side_effect=failing_second(calls)):
        result = run(
            runner.run_sequence_file(
                sequence=sequence_file(tmp_path, THREE_STEPS[:1]),
                kind="chromium",
                persona=lab_persona,
                artifacts=artifacts,
                redact_errors=False,
                out_path=None,
                pool=pool,
            )
        )
    assert result["report_path"] == str(artifacts / "octowright-report.xml")
    assert (artifacts / "octowright-report.xml").is_file()


def test_a_credential_argument_reaches_run_macro_as_credential_tier(
    tmp_path: Path, recordings: Path, lab_persona: str
) -> None:
    """`{"pin": {"credential": "password"}}` resolves to a plain string; the name
    `pin` would not classify it, so the runner says which args were credentials."""
    seen: list[dict[str, Any]] = []

    async def run_macro(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {}

    steps = [{"macro": "m1", "args": {"pin": {"credential": "password"}, "route": "/x"}}]
    with patch("octowright.runner.macro_mod.run_macro", side_effect=run_macro):
        run(
            runner.run_sequence_file(
                sequence=sequence_file(tmp_path, steps),
                kind="chromium",
                persona=lab_persona,
                artifacts=None,
                redact_errors=False,
                out_path=str(recordings / "r.xml"),
                pool=fake_pool(),
            )
        )
    assert seen[0]["args"] == {"pin": PLANTED, "route": "/x"}
    assert seen[0]["credential_args"] == frozenset({"pin"})
