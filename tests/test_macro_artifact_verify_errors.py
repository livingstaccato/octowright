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


# ---------------------------------------------------------------------------
# run_macro_artifact -- the run record, and the gate in front of verification
#
# Every field of result.json comes from a separate expression, and the suite
# only ever read `ok`, `run_id` and `verification_status`. The rest -- the
# replay counts, the instance, the recording path, the error slot -- could each
# be replaced with `None` unnoticed, which matters because result.json is what
# `result_status` checks are evaluated against and what a human reads after a
# failed run.
# ---------------------------------------------------------------------------


def _result_json(macro_artifacts, run_id: str) -> dict:
    return json.loads((_run_dir(macro_artifacts, run_id) / "result.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_capture_is_on_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`capture: bool = True` is the documented production default.

    Every existing call passed `capture=` explicitly, so flipping the default
    to False changed nothing the suite could see -- and an artifact bundle
    without screenshots is most of the point of an artifact bundle.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})

    session = _CapturingSession(tmp_path)
    await macro_artifacts.run_macro_artifact(session=session, name="login", args={})

    assert [p.name for p in session.shots] == ["before.png", "after.png"]


@pytest.mark.asyncio
async def test_the_run_record_carries_the_replay_counts_and_session_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """result.json is the artifact of record, so each field is asserted by value.

    `executed` and `skipped` are read out of the replay's own return value and
    coerced with `int(...)`; the default in `replay.get("executed", 0)` is only
    observable when a replay omits the key.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})

    session = _FakeSession(tmp_path)
    result = await macro_artifacts.run_macro_artifact(session=session, name="login", args={}, capture=False)
    record = _result_json(macro_artifacts, result["run_id"])

    assert record["status"] == "ok"
    assert record["error"] is None  # not "" -- the slot means "nothing went wrong"
    assert record["executed"] == 1
    assert record["skipped"] == 0
    assert record["macro"] == "login"
    assert record["instance_id"] == session.instance_id
    assert record["recording_path"] == str(session.log_path)


@pytest.mark.asyncio
async def test_a_replay_that_reports_no_counts_records_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The `, 0)` defaults, reachable only from a replay that omits the keys."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    async def bare_replay(*, session, name, args, slowmo_ms=None):
        return {"macro": name}

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", bare_replay)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False
    )
    record = _result_json(macro_artifacts, result["run_id"])

    assert record["executed"] == 0
    assert record["skipped"] == 0


@pytest.mark.asyncio
async def test_a_failed_replay_is_recorded_with_its_traceback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The except branch: status failed, the error named, a log excerpt kept.

    The run still returns rather than raising -- an artifact of a failed run is
    exactly what a caller needs -- so nothing forces the failure to be
    *recorded*, and every argument to the evidence call could go `None`.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    async def exploding_replay(*, session, name, args, slowmo_ms=None):
        raise RuntimeError("selector never appeared")

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", exploding_replay)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False
    )
    record = _result_json(macro_artifacts, result["run_id"])

    assert record["status"] == "failed"
    assert record["error"] == "RuntimeError: selector never appeared"
    # The counters keep their pre-try initialisers: a replay that raised before
    # returning executed nothing, and `= 1` is as plausible-looking as `= 0`.
    assert record["executed"] == 0
    assert record["skipped"] == 0

    records = json.loads((_run_dir(macro_artifacts, result["run_id"]) / "evidence.json").read_text(encoding="utf-8"))[
        "records"
    ]
    excerpts = [r for r in records if r.get("type") == "log_excerpt"]
    assert excerpts, "a failed replay left no log excerpt"
    assert "selector never appeared" in str(excerpts[0]["preview"])


# --- the gate in front of verification -------------------------------------


@pytest.mark.asyncio
async def test_verify_false_skips_verification_even_with_critical_points(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`if verify AND critical_points` -- swapped to `or`, the flag stops working.

    Every other test in the suite passes `verify=False` on an artifact with no
    critical points, or `verify=True` on one that has them, and `or` gives the
    same answer for both. Only this combination separates them.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=False
    )

    assert result["verification_status"] == "not_configured"
    assert not (_run_dir(macro_artifacts, result["run_id"]) / "verification.json").exists()


@pytest.mark.asyncio
async def test_a_run_with_no_critical_points_reports_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half of the `and`: asking to verify nothing is not a failure."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=True
    )

    assert result["verification_status"] == "not_configured"


@pytest.mark.asyncio
async def test_a_verified_run_reports_its_status_and_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Both fields are lifted out of the verify result with defaults behind them."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=True
    )

    assert result["verification_status"] == "passed"
    verification = Path(result["paths"]["verification"])
    assert verification.exists()
    assert verification.parent.name == result["run_id"]


@pytest.mark.asyncio
async def test_running_an_unplanned_macro_builds_its_own_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`run_macro_artifact` plans for itself when no artifact exists yet.

    All thirty existing call sites call `plan_macro_artifact` first, so the
    `if not manifest:` branch -- and the whole `_manifest_for_plan` call inside
    it, every keyword -- was never executed by the suite. Running a macro
    directly is the documented one-step path, and it must produce the same
    manifest a plan would.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(
        name="login",
        macro={
            "name": "login",
            "description": "Login flow",
            "parameters": ["user", "password"],
            "actions": [{"action": "navigate", "url": "https://example.test/login"}],
        },
    )
    _stub_replay(monkeypatch, macro_artifacts)

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={"user": "tanuki"}, capture=False, verify=False
    )
    assert result["ok"] is True

    listed = macro_artifacts.list_macro_artifacts(name="login")["artifacts"][0]
    meta = listed["metadata"]

    assert listed["parameters"] == {"user": "tanuki"}
    assert meta["missing_args"] == ["password"]
    assert meta["ready"] is False  # a required argument was not supplied
    assert meta["description"] == "Login flow"
    assert meta["action_count"] == 1
    assert len({meta["paths"][k] for k in ("artifact_dir", "runs_dir", "exports_dir")}) == 3
    assert meta["paths"]["runs_dir"].endswith("runs")
    assert meta["paths"]["exports_dir"].endswith("exports")


# ---------------------------------------------------------------------------
# summary.md must carry the verdict
#
# `write_run_bundle` necessarily runs before verification -- verification reads
# the result.json and evidence.json that call writes -- so the bundle's summary
# was always rendered with `verification=None`. Nothing re-rendered it
# afterwards, which made the whole "Verification and Critical Points" section
# unreachable in production: every artifact run with critical points produced a
# human-readable report that said nothing about whether the claims held, while
# verification.json sat beside it holding the answer.
# ---------------------------------------------------------------------------


def _summary(macro_artifacts, run_id: str) -> str:
    return (_run_dir(macro_artifacts, run_id) / "summary.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_verified_run_reports_its_critical_points_in_the_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The verdict, each claim, and the per-check message all reach the report."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set(
        "login",
        [
            {
                "id": "cp1",
                "description": "Login form submits successfully",
                "checks": [{"type": "result_status", "status": "ok"}],
            }
        ],
    )

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=True
    )
    md = _summary(macro_artifacts, result["run_id"])

    assert "## Verification and Critical Points" in md
    assert "**Verification Status**: `passed`" in md
    assert "### cp1: Login form submits successfully" in md
    assert "- Status: `passed`" in md
    assert "`result_status`: `passed`" in md


@pytest.mark.asyncio
async def test_the_summary_says_so_when_a_claim_does_not_hold(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A report that can only say "passed" is worse than no report."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set(
        "login",
        [
            {
                "id": "cp1",
                "description": "A claim that does not hold",
                "checks": [{"type": "result_status", "status": "definitely-not-ok"}],
            }
        ],
    )

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=True
    )
    md = _summary(macro_artifacts, result["run_id"])

    assert "**Verification Status**: `failed`" in md
    assert "### cp1: A claim that does not hold" in md
    assert "Expected status definitely-not-ok, got ok" in md


@pytest.mark.asyncio
async def test_a_standalone_verify_also_refreshes_the_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`macro_artifact_verify` is an @mcp.tool callable on its own.

    An agent that runs with verify=False and verifies later must get the same
    report as one that verified inline, and the returned `paths` must name the
    summary it rewrote.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=False
    )
    assert "Verification and Critical Points" not in _summary(macro_artifacts, result["run_id"])

    verified = macro_artifacts.macro_artifact_verify("login", run_id=result["run_id"])

    assert Path(verified["paths"]["summary"]).name == "summary.md"
    md = _summary(macro_artifacts, result["run_id"])
    assert "**Verification Status**: `passed`" in md


@pytest.mark.asyncio
async def test_the_summary_keeps_its_prose_after_being_re_rendered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-rendering must not lose the human line the run wrote.

    The summary text is only known to `run_macro_artifact`, so the bundle
    persists it -- otherwise the refresh would silently blank the one sentence
    describing what the run did.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path),
        name="login",
        args={},
        capture=False,
        verify=True,
        notes="Nightly smoke run",
    )
    md = _summary(macro_artifacts, result["run_id"])

    assert "Nightly smoke run" in md
    assert "## Verification and Critical Points" in md
