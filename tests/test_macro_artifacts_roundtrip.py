# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Re-entry and success paths for macro artifacts.

``tests/test_macro_artifacts.py`` covers this module well in one dimension and
not at all in another: every test there starts from an empty artifact store and
stops after a single call. The 2026-08-27 mutation run made the cost visible --
562 survivors in ``macros/artifacts.py``, dominated by code that no test
executes rather than by weak assertions.

Three consequences, each fixed by a test here:

* **The verifier is never shown succeeding.** ``macro_artifact_verify`` is an
  MCP tool, and across the whole suite it was called exactly once -- with a
  path-traversal payload, asserting ``ok is False``. Its evaluate-checks,
  write-verification.json and return-success path never ran, so the terminal
  ``"ok": True`` could be flipped to ``False`` with everything still green. A
  verifier that cannot be shown to say *yes* correctly is the worst shape for
  this kind of code, because every consumer trusts it.
* **The manifest merge is never re-entered.** ``_merge_existing_manifest``
  preserves exactly ``created_at`` / ``latest_run`` / ``exports`` /
  ``critical_points`` from an existing manifest, and nothing called a
  manifest-writing function twice on the same macro. Skipping that merge resets
  ``critical_points`` to ``[]``, which makes ``run_macro_artifact``'s
  ``if verify and critical_points:`` false -- verification silently degrades to
  a no-op while still reporting success, and an operator's configured critical
  points are gone.
* **Evidence capture never runs.** Every existing ``run_macro_artifact`` call
  passes ``capture=False`` while the production default is ``capture=True``, so
  the screenshot evidence that is the entire point of an artifact bundle was
  exercised by nothing.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from tests._operation_gate_fakes import OperationAwareFake


@pytest.fixture(autouse=True)
def _restore_reloaded_defaults():
    yield
    import octowright.artifacts.paths as artifact_paths
    import octowright.defaults as defaults
    import octowright.macros.storage as storage

    importlib.reload(defaults)
    importlib.reload(storage)
    importlib.reload(artifact_paths)


def _reload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "recordings"))
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))

    import octowright.artifacts.paths as artifact_paths
    import octowright.defaults as defaults
    import octowright.macros.artifacts as macro_artifacts
    import octowright.macros.storage as storage

    importlib.reload(defaults)
    importlib.reload(storage)
    importlib.reload(artifact_paths)
    importlib.reload(macro_artifacts)
    return storage, macro_artifacts


def _write_macro(storage, *, name: str = "login") -> Path:
    return storage.write_macro(
        name=name,
        macro={
            "name": name,
            "description": "Login flow",
            "parameters": [],
            "actions": [{"action": "navigate", "url": "https://example.test/login"}],
        },
    )


class _FakeSession(OperationAwareFake):
    """Session double with no page, matching the existing capture=False tests."""

    def __init__(self, tmp_path: Path) -> None:
        self.instance_id = "inst-1"
        super().__init__()
        self.log_path = tmp_path / "recording.jsonl"
        self.log_path.write_text('{"action":"click"}\n', encoding="utf-8")
        self.page = None


class _CapturingSession(_FakeSession):
    """Session that can actually take a screenshot, so ``capture=True`` does work.

    ``_capture_screenshot`` returns early unless ``session.page`` is set AND a
    ``screenshot`` attribute exists, which is why the page-less double above
    cannot exercise the capture path however the flag is set.
    """

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.page = object()
        self.shots: list[Path] = []

    async def screenshot(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")
        self.shots.append(Path(path))


def _passing_critical_point() -> list[dict[str, Any]]:
    """A critical point that passes against a successful run.

    ``run_macro_artifact`` writes ``status: "ok"`` into result.json on success,
    and the ``result_status`` check compares against exactly that.
    """
    return [{"id": "cp1", "checks": [{"type": "result_status", "status": "ok"}]}]


def _stub_replay(monkeypatch: pytest.MonkeyPatch, macro_artifacts) -> None:
    async def fake_run_macro(*, session, name, args, slowmo_ms=None):
        return {"macro": name, "executed": 1, "skipped": 0, "args_used": args or {}, "slowmo_ms": slowmo_ms or 0}

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)


# ---------------------------------------------------------------------------
# Manifest re-entry
# ---------------------------------------------------------------------------


def test_critical_points_survive_a_second_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Re-planning a macro must merge with its existing manifest, not replace it.

    This is the single highest-value assertion in the file. If
    ``_safe_existing_manifest_path`` stops finding an existing manifest, the
    merge is skipped and ``critical_points`` silently resets -- which turns
    every later verification into a no-op that still reports success.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    macro_artifacts.plan_macro_artifact("login", args={})
    set_res = macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())
    assert set_res["ok"] is True

    macro_artifacts.plan_macro_artifact("login", args={})

    got = macro_artifacts.macro_artifact_critical_points_get("login")
    assert got["ok"] is True
    assert [cp["id"] for cp in got["critical_points"]] == ["cp1"]


def test_created_at_survives_a_second_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``created_at`` is merge-preserved too, and is the field that proves identity."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    first = macro_artifacts.plan_macro_artifact("login", args={})
    manifest_path = Path(first["paths"]["manifest"])
    created_first = json.loads(manifest_path.read_text(encoding="utf-8"))["created_at"]

    macro_artifacts.plan_macro_artifact("login", args={})
    created_second = json.loads(manifest_path.read_text(encoding="utf-8"))["created_at"]

    assert created_second == created_first


# ---------------------------------------------------------------------------
# Verification success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_verifies_configured_critical_points(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With critical points configured, a run must actually verify them.

    Nothing previously configured critical points before running, so the whole
    verification branch of ``run_macro_artifact`` was dead in every test.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)

    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())

    result = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False
    )

    assert result["ok"] is True
    assert result["verification_status"] == "passed"


@pytest.mark.asyncio
async def test_verify_reports_ok_on_a_passing_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The verifier's success path: ``ok`` True, status passed, file written.

    Previously unreachable — the only caller in the suite fed it a traversal
    payload and asserted rejection, so ``"ok": True`` was never observed.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)

    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())
    # verify=False so this test's explicit call is the FIRST evaluation of the
    # stored critical points, keeping the assertions below about one known
    # evaluation rather than a second one. Repeat evaluation is covered by
    # test_verify_is_idempotent below.
    await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=False
    )

    verified = macro_artifacts.macro_artifact_verify("login")

    assert verified["ok"] is True
    assert verified["status"] == "passed"
    written = Path(verified["paths"]["verification"])
    assert written.exists()
    assert json.loads(written.read_text(encoding="utf-8"))["status"] == "passed"


@pytest.mark.asyncio
async def test_verify_fails_when_a_critical_point_does_not_hold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The companion assertion: the verifier must still be able to say no.

    Without this, a mutant hardcoding ``status: "passed"`` would satisfy the
    success test above. A verifier is only worth anything if both verdicts are
    pinned.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)

    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set(
        "login", [{"id": "cp1", "checks": [{"type": "result_status", "status": "definitely-not-ok"}]}]
    )
    await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=False
    )

    verified = macro_artifacts.macro_artifact_verify("login")

    assert verified["ok"] is True  # the call succeeded...
    assert verified["status"] == "failed"  # ...and the verdict is a failure


@pytest.mark.asyncio
async def test_verify_honours_an_explicit_run_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicit ``run_id`` must override the manifest's latest run.

    ``run_id or (manifest.get("latest_run") or {}).get("run_id")`` -- swap that
    ``or`` for ``and`` and the argument silently stops working, so verify
    reports a verdict about a different run than the caller asked about.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)

    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())

    first = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=False
    )
    second = await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=False
    )
    assert first["run_id"] != second["run_id"]

    verified = macro_artifacts.macro_artifact_verify("login", run_id=first["run_id"])

    assert verified["ok"] is True
    # The verification file must land in the run we named, not the latest one.
    assert first["run_id"] in verified["paths"]["verification"]
    assert second["run_id"] not in verified["paths"]["verification"]


@pytest.mark.asyncio
async def test_verify_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifying the same run twice must give the same verdict.

    Was xfail(strict=True) until the bug it documents was fixed. Verification
    persisted the evaluated form of each critical point back over its stored
    definition, and a `result_status` check declares its expected run status
    under `status` -- the same key the evaluation reports its verdict under. So
    the first verify rewrote the expected "ok" into the verdict "passed", and
    the second compared the unchanged run against "passed" and failed.

    A verifier whose answer depends on how many times it has been asked is
    worse than no verifier, because the first answer looks authoritative.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)

    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())
    await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=False
    )

    first = macro_artifacts.macro_artifact_verify("login")
    second = macro_artifacts.macro_artifact_verify("login")

    assert first["status"] == "passed"
    assert second["status"] == first["status"]


@pytest.mark.asyncio
async def test_verify_leaves_the_stored_declarations_untouched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The manifest stores declarations; only the roll-up may be written back.

    This is the mechanism behind the idempotency test above, asserted directly
    so a regression names its own cause. `checks` must come back byte-identical
    to what was declared -- the verdict belongs on the critical point, and in
    `verification.json`, not smeared over the definition that produced it.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)

    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())
    declared = macro_artifacts.macro_artifact_critical_points_get("login")["critical_points"]
    declared_checks = json.loads(json.dumps([cp["checks"] for cp in declared]))

    await macro_artifacts.run_macro_artifact(
        session=_FakeSession(tmp_path), name="login", args={}, capture=False, verify=False
    )
    verified = macro_artifacts.macro_artifact_verify("login")
    assert verified["status"] == "passed"

    stored = macro_artifacts.macro_artifact_critical_points_get("login")["critical_points"]
    assert [cp["checks"] for cp in stored] == declared_checks
    # The roll-up is the part that IS allowed to change.
    assert stored[0]["status"] == "passed"
    assert stored[0]["last_verified_run"] is not None


@pytest.mark.asyncio
async def test_the_verification_report_reports_verdicts_not_declarations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A check result carries the spec's four fields and nothing merged in.

    Keeping the declaration out of the outcome is what makes the collision
    impossible rather than merely unpersisted: `{**check, "status": verdict}`
    produces a record whose `status` means different things depending on the
    check type, and any future consumer that writes such a record back would
    reintroduce the same defect. `screenshot_exists` declares a `label`, so its
    presence in the report is a direct signal that the merge is back.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)

    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set(
        "login",
        [
            {
                "id": "cp1",
                "checks": [
                    {"type": "result_status", "status": "ok"},
                    {"type": "screenshot_exists", "label": "after"},
                ],
            }
        ],
    )
    await macro_artifacts.run_macro_artifact(
        session=_CapturingSession(tmp_path), name="login", args={}, capture=True, verify=False
    )

    verified = macro_artifacts.macro_artifact_verify("login")
    assert verified["status"] == "passed"

    report = json.loads(Path(verified["paths"]["verification"]).read_text(encoding="utf-8"))
    checks = report["critical_points"][0]["checks"]
    assert [set(c) for c in checks] == [{"type", "status", "message", "evidence"}] * 2
    assert [c["status"] for c in checks] == ["passed", "passed"]
    # The label is not lost to the reader -- the message carries it.
    assert "after" in checks[1]["message"]


# ---------------------------------------------------------------------------
# Evidence capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_true_writes_screenshot_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``capture=True`` is the production default and no test ever used it."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    session = _CapturingSession(tmp_path)

    result = await macro_artifacts.run_macro_artifact(session=session, name="login", args={}, capture=True)

    assert result["ok"] is True
    # before + after
    assert len(session.shots) == 2
    assert all(shot.exists() for shot in session.shots)

    evidence = json.loads(Path(result["paths"]["evidence"]).read_text(encoding="utf-8"))
    labels = {rec.get("label") for rec in evidence["records"]}
    assert {"before", "after"} <= labels


@pytest.mark.asyncio
async def test_capture_false_takes_no_screenshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The negative half: the flag must actually gate, not merely be accepted."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    session = _CapturingSession(tmp_path)

    await macro_artifacts.run_macro_artifact(session=session, name="login", args={}, capture=False)

    assert session.shots == []


# ---------------------------------------------------------------------------
# Listing with more than one artifact
# ---------------------------------------------------------------------------


def test_list_macro_artifacts_sorts_newest_first_and_caps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every existing listing test has exactly one artifact.

    With one item a broken sort key is invisible and a broken comparison never
    executes; with two, a mutant that strips the key raises TypeError comparing
    dicts, and one that inverts the order makes a ``limit``-capped listing drop
    the newest artifact and show a stale one.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    for macro_name in ("alpha", "beta", "gamma"):
        _write_macro(storage, name=macro_name)
        macro_artifacts.plan_macro_artifact(macro_name, args={})

    listing = macro_artifacts.list_macro_artifacts()
    names = [item["name"] for item in listing["artifacts"]]

    assert listing["count"] == 3
    assert sorted(names) == ["alpha", "beta", "gamma"]
    # updated_at descending — the newest plan must lead.
    stamps = [item["updated_at"] for item in listing["artifacts"]]
    assert stamps == sorted(stamps, reverse=True)

    capped = macro_artifacts.list_macro_artifacts(limit=2)
    assert capped["count"] == 2
    assert capped["limit"] == 2
    assert [item["name"] for item in capped["artifacts"]] == names[:2]


# ---------------------------------------------------------------------------
# Export destination
# ---------------------------------------------------------------------------


def test_export_macro_cli_honours_an_explicit_out_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``out_path`` reaches ``resolve_macro_export_path`` and nothing passed it.

    Hardcoding it to ``None`` sends the script to the default location while
    still returning success, so an MCP caller asking for a destination gets a
    file somewhere else and no error. (Containment itself is covered by
    tests/test_artifacts_paths.py — this is about the caller's request being
    honoured, not about escaping the root.)
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    import octowright.defaults as defaults

    requested = Path(defaults.RECORDINGS_DIR) / "exports" / "custom-login.py"

    result = macro_artifacts.export_macro_cli(name="login", out_path=str(requested))

    assert result["ok"] is True
    assert Path(result["path"]) == requested
    assert requested.exists()
