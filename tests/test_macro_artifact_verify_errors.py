# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_artifact_verify``'s refusal paths, and best-effort screenshot evidence.

`macro_artifact_verify` is an @mcp.tool with five distinct ways to decline, and
every one of them returns `{"ok": False, "error": ...}`. Only the traversal
guard was ever asserted, so on the other four the `False` could be flipped to
`True` with the suite green -- handing an agent a success envelope with an
error message inside it, which is worse than either an error or a success
because it reads as neither.

`_capture_screenshot` is best-effort by design: it must never let a failed
screenshot hide a macro result. That intent is only real if the failure path
actually records something, and nothing had ever driven a session whose
screenshot raises.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._macro_artifact_fixtures import (
    _CapturingSession,
    _FakeSession,
    _passing_critical_point,
    _reload,
    _stub_replay,
    _write_macro,
    restore_reloaded_defaults,
)


@pytest.fixture(autouse=True)
def _restore_reloaded_defaults():
    yield
    restore_reloaded_defaults()


async def _planned_run(macro_artifacts, tmp_path: Path) -> str:
    """Plan, configure a critical point, and complete one run."""
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())
    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=False
    )
    return str(result["run_id"])


def _run_dir(macro_artifacts, run_id: str) -> Path:
    from octowright.artifacts.paths import ArtifactStore

    store = ArtifactStore()
    return store.macro_dir("login") / "runs" / run_id


# ---------------------------------------------------------------------------
# The five refusals
# ---------------------------------------------------------------------------


def test_verify_refuses_a_macro_with_no_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    result = macro_artifacts.macro_artifact_verify("never-planned")

    assert result["ok"] is False
    assert result["error"] == "Manifest not found."
    assert "status" not in result


def test_verify_refuses_an_artifact_with_no_critical_points(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Nothing to verify is a refusal, not a vacuous pass."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = macro_artifacts.macro_artifact_verify("login")

    assert result["ok"] is False
    assert result["error"] == "No critical points configured."


def test_verify_refuses_when_no_run_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Critical points configured, but nothing has been run against them yet."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())

    result = macro_artifacts.macro_artifact_verify("login")

    assert result["ok"] is False
    assert result["error"] == "No runs exist to verify."


def test_verify_names_an_unusable_run_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The error must carry the reason, not `str(None)`.

    This path re-raises `existing_run_dir`'s ValueError text, which is the only
    thing telling a caller *why* their run_id was rejected.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())

    result = macro_artifacts.macro_artifact_verify("login", run_id="../../etc")

    assert result["ok"] is False
    assert "../../etc" in result["error"]
    assert result["error"] != "None"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["result.json", "evidence.json"])
async def test_verify_refuses_a_bundle_missing_either_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    """`or`, not `and` -- either file alone being absent is incomplete.

    Swapped to `and`, a bundle missing exactly one of the two passes the check
    and fails later inside the read with a FileNotFoundError surfaced as
    "Failed reading bundle" -- a different, less actionable message for a
    condition this branch exists to name precisely.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    run_id = await _planned_run(macro_artifacts, tmp_path)

    (_run_dir(macro_artifacts, run_id) / missing).unlink()

    result = macro_artifacts.macro_artifact_verify("login", run_id=run_id)

    assert result["ok"] is False
    assert result["error"] == "Run bundle incomplete."


@pytest.mark.asyncio
async def test_verify_refuses_an_unreadable_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Malformed JSON is reported as a read failure, still with ok False."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    run_id = await _planned_run(macro_artifacts, tmp_path)

    (_run_dir(macro_artifacts, run_id) / "result.json").write_text("{not json", encoding="utf-8")

    result = macro_artifacts.macro_artifact_verify("login", run_id=run_id)

    assert result["ok"] is False
    assert result["error"].startswith("Failed reading bundle:")


@pytest.mark.asyncio
async def test_the_verification_file_is_valid_json_on_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """verification.json is read back by reports.py, so it must parse."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    run_id = await _planned_run(macro_artifacts, tmp_path)

    verified = macro_artifacts.macro_artifact_verify("login", run_id=run_id)

    payload = json.loads(Path(verified["paths"]["verification"]).read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["critical_points"]


# ---------------------------------------------------------------------------
# _capture_screenshot -- best-effort means the failure path must do something
# ---------------------------------------------------------------------------


class _FailingScreenshotSession(_CapturingSession):
    """A session whose screenshot always raises, as a locked screen might."""

    async def screenshot(self, path: Path) -> None:
        raise RuntimeError("display unavailable")


@pytest.mark.asyncio
async def test_a_failed_screenshot_is_recorded_rather_than_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run still succeeds, and the failure leaves a trace.

    "Best-effort evidence must not hide macro results" is only half the
    contract -- the other half is that the attempt is visible afterwards.
    Nothing had driven a raising screenshot, so the whole except branch,
    including every argument to log_excerpt, was unobserved.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = await macro_artifacts.run_macro_artifact(
        session=_FailingScreenshotSession(tmp_path), name="login", args={}, capture=True, verify=False
    )

    assert result["ok"] is True  # the macro result is not hidden
    records = json.loads((_run_dir(macro_artifacts, result["run_id"]) / "evidence.json").read_text(encoding="utf-8"))[
        "records"
    ]
    excerpts = [r for r in records if r.get("type") == "log_excerpt"]
    assert excerpts, "the failed screenshot left no evidence"
    assert "RuntimeError" in str(excerpts[0].get("preview"))
    assert "display unavailable" in str(excerpts[0].get("preview"))
    assert str(excerpts[0].get("path", "")).endswith(".png")


@pytest.mark.asyncio
async def test_a_successful_screenshot_records_its_own_path_and_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both screenshots are recorded, each against the file it actually wrote.

    Asserting only that *a* screenshot record exists left `path=None` and a
    swapped label indistinguishable -- and the label is how `screenshot_exists`
    checks find them.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = await macro_artifacts.run_macro_artifact(
        session=_CapturingSession(tmp_path), name="login", args={}, capture=True, verify=False
    )

    records = json.loads((_run_dir(macro_artifacts, result["run_id"]) / "evidence.json").read_text(encoding="utf-8"))[
        "records"
    ]
    shots = {r["label"]: r for r in records if r.get("type") == "screenshot"}

    assert set(shots) == {"before", "after"}
    for label, record in shots.items():
        assert Path(record["path"]).name == f"{label}.png"
        assert Path(record["path"]).exists()
