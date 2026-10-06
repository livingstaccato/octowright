# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What one ``macro_artifact_run`` writes and returns, field for field.

The replay itself is stubbed at ``macros.run_macro`` -- the boundary the
artifact run hands its session to -- so these pin the artifact layer: the
manifest it writes before and after the replay, the run bundle, the evidence
it records when the replay or a screenshot fails, and its telemetry.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import pytest

from tests._macro_artifact_fixtures import _CapturingSession, _FakeSession, _reload, restore_reloaded_defaults
from tests.mutation_kills._artifact_helpers import artifact_dir, read_json, stable


@pytest.fixture(autouse=True)
def _restore_defaults() -> Any:
    yield
    restore_reloaded_defaults()


def _macro(storage: Any) -> None:
    storage.write_macro(
        name="checkout",
        macro={
            "name": "checkout",
            "description": "Checkout flow",
            "parameters": ["item", "password"],
            "actions": [{"action": "navigate", "url": "https://example.test/cart"}],
        },
    )


def _replay(monkeypatch: pytest.MonkeyPatch, macro_artifacts: Any, behaviour: Any) -> list[dict[str, Any]]:
    """Stub the replay with *behaviour*; returns the keyword arguments each call received."""
    calls: list[dict[str, Any]] = []

    async def fake_run_macro(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return await behaviour(**kwargs)

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)
    return calls


def _manifest(tmp_path: Path) -> dict[str, Any]:
    return stable(read_json(artifact_dir(tmp_path, "checkout") / "artifact.json"), tmp_path)


@pytest.mark.asyncio
async def test_a_successful_run_writes_and_returns_exact_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _macro(storage)
    seen_during_replay: list[dict[str, Any]] = []

    async def replay(**_kwargs: Any) -> dict[str, Any]:
        seen_during_replay.append(read_json(artifact_dir(tmp_path, "checkout") / "artifact.json")["parameters"])
        return {"executed": 2, "skipped": 3}

    calls = _replay(monkeypatch, macro_artifacts, replay)
    session = _FakeSession(tmp_path)

    result = await macro_artifacts.run_macro_artifact(
        session, "checkout", {"item": "socks", "password": _SECRET}, capture=False, slowmo_ms=25
    )

    # Every value is withheld while the replay runs; a called macro may classify any of them.
    assert seen_during_replay == [{"item": "<redacted>", "password": "<redacted>"}]
    assert calls[0]["slowmo_ms"] == 25
    run_dir = "<tmp>/recordings/artifacts/macros/checkout/runs/run_0001"
    assert stable(result, tmp_path) == {
        "ok": True,
        "macro": "checkout",
        "run_id": "run_0001",
        "summary": "Ran macro checkout: status=ok, executed=2, skipped=3.",
        "verification_status": "not_configured",
        "paths": {
            "run_dir": run_dir,
            "manifest": "<tmp>/recordings/artifacts/macros/checkout/artifact.json",
            "summary": f"{run_dir}/summary.md",
            "evidence": f"{run_dir}/evidence.json",
            "result": f"{run_dir}/result.json",
        },
    }
    root = "<tmp>/recordings/artifacts/macros/checkout"
    assert _manifest(tmp_path) == {
        "artifact_version": 1,
        "artifact_type": "macro",
        "name": "checkout",
        "source": {"type": "macro", "path": "<tmp>/macros/checkout.json"},
        "parameters": {"item": "socks", "password": "<redacted>"},
        "latest_run": {"run_id": "run_0001", "path": run_dir},
        "exports": [],
        "critical_points": [],
        "metadata": {
            "description": "Checkout flow",
            "action_count": 1,
            "missing_args": [],
            "paths": {"artifact_dir": root, "runs_dir": f"{root}/runs", "exports_dir": f"{root}/exports"},
            "ready": True,
        },
    }
    assert read_json(result["paths"]["result"])["skipped"] == 3


@pytest.mark.asyncio
async def test_a_replay_reporting_no_counts_records_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _macro(storage)

    async def replay(**_kwargs: Any) -> dict[str, Any]:
        return {}

    _replay(monkeypatch, macro_artifacts, replay)
    session = _FakeSession(tmp_path)
    session.__class__ = _Anonymous

    result = await macro_artifacts.run_macro_artifact(session, "checkout", {"item": "socks"}, capture=False)

    bundle = stable(read_json(result["paths"]["result"]), tmp_path)
    assert (bundle["status"], bundle["executed"], bundle["skipped"], bundle["instance_id"]) == ("ok", 0, 0, "")


class _Anonymous(_FakeSession):
    """A session with no ``instance_id`` at all; swapped in after the gate is built."""

    @property
    def instance_id(self) -> str:  # type: ignore[override]
        raise AttributeError("instance_id")


def _deep_failure(depth: int) -> None:
    # Alternates with _deeper so no two consecutive frames are identical:
    # traceback folds a run of identical frames into one "repeated" line.
    if depth:
        _deeper(depth - 1)
        return
    raise RuntimeError("card declined")


def _deeper(depth: int) -> None:
    _deep_failure(depth)


@pytest.mark.asyncio
async def test_a_failed_replay_records_the_recording_excerpt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _macro(storage)

    async def replay(**_kwargs: Any) -> dict[str, Any]:
        _deep_failure(12)
        return {}

    _replay(monkeypatch, macro_artifacts, replay)
    session = _FakeSession(tmp_path)

    result = await macro_artifacts.run_macro_artifact(session, "checkout", {"item": "socks"}, capture=False)

    records = read_json(result["paths"]["evidence"])["records"]
    assert [(record["type"], record["path"], record["offset"]) for record in records] == [
        ("log_excerpt", str(session.log_path), 0)
    ]
    # The excerpt is the traceback cut to its first eight frames.
    assert records[0]["preview"].count('  File "') == 8
    assert records[0]["preview"].startswith("Traceback (most recent call last):\n")


@pytest.mark.asyncio
async def test_a_failed_replay_without_a_recording_points_at_the_run_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _macro(storage)

    async def replay(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("card declined")

    _replay(monkeypatch, macro_artifacts, replay)
    session = _FakeSession(tmp_path)
    del session.log_path

    result = await macro_artifacts.run_macro_artifact(session, "checkout", {"item": "socks"}, capture=False)

    records = read_json(result["paths"]["evidence"])["records"]
    assert [record["path"] for record in records] == [f"{result['paths']['run_dir']}/replay.jsonl"]


@pytest.mark.asyncio
async def test_a_failure_message_holding_a_credential_is_scrubbed_in_the_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _macro(storage)
    secret = "pw-9f8e7d6c5b"  # pragma: allowlist secret

    async def replay(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(f"login rejected {secret}")

    _replay(monkeypatch, macro_artifacts, replay)

    result = await macro_artifacts.run_macro_artifact(
        _FakeSession(tmp_path), "checkout", {"item": "socks", "password": secret}, capture=False
    )

    bundle = read_json(result["paths"]["result"])
    assert bundle["error"] == "RuntimeError: login rejected <redacted>"
    # Scrubbed as it was written, so the bundle's last-line tripwire never had to fire.
    assert "privacy_tripwire" not in bundle
    assert "privacy_tripwire" not in result


@pytest.mark.asyncio
async def test_screenshots_land_under_the_run_and_are_recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _macro(storage)

    async def replay(**_kwargs: Any) -> dict[str, Any]:
        return {"executed": 1}

    _replay(monkeypatch, macro_artifacts, replay)

    result = await macro_artifacts.run_macro_artifact(_CapturingSession(tmp_path), "checkout", {"item": "socks"})

    run_dir = result["paths"]["run_dir"]
    records = read_json(result["paths"]["evidence"])["records"]
    assert [(record["type"], record.get("path"), record.get("label")) for record in records] == [
        ("screenshot", f"{run_dir}/screenshots/before.png", "before"),
        ("screenshot", f"{run_dir}/screenshots/after.png", "after"),
    ]


class _BrokenCamera(_FakeSession):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.page = object()

    async def screenshot(self, path: Path) -> None:
        raise OSError(f"disk full at {Path(path).name}")


class _NoCamera(_FakeSession):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.page = object()


@pytest.mark.asyncio
async def test_a_failed_screenshot_is_recorded_as_an_excerpt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    from octowright.artifacts.evidence import EvidenceBuilder

    evidence = EvidenceBuilder()
    await macro_artifacts._capture_screenshot(
        session=_BrokenCamera(tmp_path), run_dir=tmp_path / "run", evidence=evidence, label="before", enabled=True
    )

    assert stable(evidence.records, tmp_path) == [
        {
            "id": "ev_001",
            "type": "log_excerpt",
            "path": "<tmp>/run/screenshots/before.png",
            "offset": 0,
            "length": len("OSError: disk full at before.png"),
            "preview": "OSError: disk full at before.png",
        }
    ]


@pytest.mark.asyncio
async def test_a_session_that_cannot_screenshot_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    from octowright.artifacts.evidence import EvidenceBuilder

    evidence = EvidenceBuilder()
    await macro_artifacts._capture_screenshot(
        session=_NoCamera(tmp_path), run_dir=tmp_path / "run", evidence=evidence, label="before", enabled=True
    )

    assert evidence.records == []


def test_the_deep_failure_helper_is_deeper_than_the_cut() -> None:
    # Guards the excerpt test above: with fewer frames than the cut, a longer cut would look the same.
    try:
        _deep_failure(12)
    except RuntimeError:
        assert traceback.format_exc().count('  File "') > 9


# --- classified-run screenshots -------------------------------------------------

_SECRET = "pw-classified-4c2e"  # pragma: allowlist secret


def _classified_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, opt_in: bool) -> tuple[Any, Any]:
    from octowright import defaults
    from octowright.macros import safe_screenshot
    from tests.test_macro_redacted_screenshot import FakePage, _session

    # The run directory sits outside the recordings root, so the screenshot is
    # written only because the artifact run names its own run dir as the root.
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path / "recordings")
    monkeypatch.delenv(safe_screenshot.POLICY_ENV, raising=False)
    page = FakePage()
    session = _session(page)
    if opt_in:
        safe_screenshot.enable_redacted_screenshots(session)
    return page, session


@pytest.mark.asyncio
async def test_a_classified_screenshot_is_redacted_into_the_run_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from octowright.artifacts.evidence import EvidenceBuilder
    from octowright.macros import artifacts
    from tests.test_macro_redacted_screenshot import TAKEN

    page, session = _classified_session(monkeypatch, tmp_path, opt_in=True)
    evidence = EvidenceBuilder()

    await artifacts._capture_screenshot(
        session=session,
        run_dir=tmp_path / "run",
        evidence=evidence,
        label="after",
        enabled=True,
        sensitive_values=(_SECRET,),
    )

    assert page.calls == TAKEN
    assert stable(evidence.records, tmp_path) == [
        {"id": "ev_001", "type": "screenshot", "path": "<tmp>/run/screenshots/after.png", "label": "after"}
    ]


@pytest.mark.asyncio
async def test_a_refused_classified_screenshot_is_recorded_as_an_excerpt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from octowright.artifacts.evidence import EvidenceBuilder
    from octowright.macros import artifacts

    _page, session = _classified_session(monkeypatch, tmp_path, opt_in=True)
    session.page = object()  # no DevTools session to redact through
    evidence = EvidenceBuilder()

    await artifacts._capture_screenshot(
        session=session,
        run_dir=tmp_path / "run",
        evidence=evidence,
        label="before",
        enabled=True,
        sensitive_values=(_SECRET,),
    )

    assert stable(evidence.records, tmp_path) == [
        {
            "id": "ev_001",
            "type": "log_excerpt",
            "path": "<tmp>/run/screenshots/before.png",
            "offset": 0,
            "length": len("ScreenshotRefused"),
            "preview": "ScreenshotRefused",
        }
    ]


@pytest.mark.asyncio
async def test_a_suppressed_classified_screenshot_names_its_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from octowright.artifacts.evidence import EvidenceBuilder
    from octowright.macros import artifacts

    page, session = _classified_session(monkeypatch, tmp_path, opt_in=False)
    evidence = EvidenceBuilder()

    await artifacts._capture_screenshot(
        session=session,
        run_dir=tmp_path / "run",
        evidence=evidence,
        label="before",
        enabled=True,
        sensitive_values=(_SECRET,),
    )

    assert page.calls == []
    assert stable(evidence.records, tmp_path) == [{"id": "ev_001", "type": "screenshot_suppressed", "label": "before"}]


# --- telemetry ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_emits_its_documented_span_and_counters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests.test_telemetry_fixes import _collect_counter_points, _setup_metric_reader, _setup_span_exporter

    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _macro(storage)

    async def replay(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("card declined")

    _replay(monkeypatch, macro_artifacts, replay)
    reader = _setup_metric_reader(monkeypatch)
    exporter = _setup_span_exporter(monkeypatch)
    checks = [{"type": "result_status", "status": "ok"}]
    macro_artifacts.macro_artifact_critical_points_set("checkout", [{"checks": checks}])

    await macro_artifacts.run_macro_artifact(_FakeSession(tmp_path), "checkout", {"item": "socks"}, capture=False)

    assert _collect_counter_points(reader, "octowright_macro_artifact_run_total") == [
        ({"macro": "checkout", "status": "failed", "verified": "True"}, 1)
    ]
    assert _collect_counter_points(reader, "octowright_artifact_verify_total") == [
        ({"artifact_type": "macro", "status": "failed"}, 1)
    ]
    spans = {span.name: dict(span.attributes or {}) for span in exporter.get_finished_spans()}
    assert spans["octowright.macro.artifact.run"] == {"macro": "checkout", "run_id": "run_0001", "verify": True}
