# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A green bundle must not mean "policy never arrived" (#248, Part 0).

An artifact run's bundle (result.json, evidence.json, summary.md) is scrubbed
of what the run's privacy view holds. Two ways that can be quietly wrong:

- the view never resolved, or was not finished (unsealed), or the session's
  scrub set is saturated: the bundle is still written -- nothing raises after
  the run directory exists -- with key-level redaction on top, and says
  ``privacy_unresolved: true``;
- a value is still there after the scrub -- one the session holds but the
  bundle's own scrub was not given, or a spelling the scrub does not cover:
  the tripwire finds it, in any case, removes it, and says
  ``privacy_tripwire: true``.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.artifacts.bundle_privacy import BundlePrivacy, guard_documents
from octowright.artifacts.reports import write_run_bundle
from octowright.macros import execution, privacy
from tests._macro_artifact_fixtures import _FakeSession, _reload, restore_reloaded_defaults

SECRET = "Bundle-Secret-4Wz"  # pragma: allowlist secret
RUN_VALUE = "Run-Own-Value-1"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _restore() -> Any:
    yield
    restore_reloaded_defaults()


def _spellings(value: str) -> tuple[str, ...]:
    return (value, value.lower(), value.upper(), value.swapcase(), json.dumps(value)[1:-1])


def _clean(text: str) -> bool:
    return not any(spelling in text for spelling in _spellings(SECRET))


def _tree(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in root.rglob("*") if path.is_file())


# -- the guard ---------------------------------------------------------------------


@pytest.mark.parametrize("spelling", [SECRET.upper(), SECRET.lower(), SECRET.swapcase()])
def test_the_tripwire_finds_and_removes_a_spelling_the_scrub_missed(spelling: str) -> None:
    docs = {"result": {"error": f"page said {spelling} twice: {spelling}"}, "summary": f"see {spelling}"}

    cleaned, flags = guard_documents(docs, BundlePrivacy(values=(SECRET,)))

    assert flags == {"privacy_tripwire": True}
    assert _clean(json.dumps(cleaned))
    assert cleaned["result"]["error"].startswith("page said <redacted>")


def test_the_tripwire_is_quiet_on_a_clean_bundle() -> None:
    docs = {"result": {"error": "page said <redacted>"}}
    cleaned, flags = guard_documents(docs, BundlePrivacy(values=(SECRET,)))
    assert flags == {}
    assert cleaned == docs


def test_a_typed_password_trips_only_as_a_whole_identifier() -> None:
    """A word-bounded ledger value (a typed ``admin``) must not fire on ``administrator``."""
    guard = BundlePrivacy(bounded=frozenset({"admin"}))

    _, quiet = guard_documents({"summary": "the administrator menu #admin-menu"}, guard)
    cleaned, fired = guard_documents({"summary": "password was ADMIN"}, guard)

    assert quiet == {}
    assert fired == {"privacy_tripwire": True}
    assert "ADMIN" not in cleaned["summary"]


def test_an_unresolved_view_adds_key_level_redaction_and_says_so() -> None:
    docs = {"result": {"args_used": {"password": SECRET}}, "evidence": [{"headers": {"authorization": "Bearer x"}}]}

    cleaned, flags = guard_documents(docs, BundlePrivacy(unresolved=True))

    assert flags == {"privacy_unresolved": True}
    assert cleaned["result"]["args_used"]["password"] == "<redacted>"
    assert cleaned["evidence"][0]["headers"]["authorization"] == "<redacted>"


def test_write_run_bundle_writes_the_flags_into_result_json(tmp_path: Path) -> None:
    guard = BundlePrivacy(values=(SECRET,), unresolved=True)
    paths = write_run_bundle(
        run_dir=tmp_path,
        result={"status": "failed", "error": f"boom {SECRET.upper()}", "args_used": {"note": "x"}},
        evidence=[{"type": "log_excerpt", "preview": SECRET.lower()}],
        summary=f"ran {SECRET.swapcase()}",
        # The bundle's own scrub was not given the value; only the guard holds it.
        sensitive_values=(),
        privacy=guard,
    )

    result = json.loads(paths["result"].read_text(encoding="utf-8"))
    assert result["privacy_tripwire"] is True
    assert result["privacy_unresolved"] is True
    assert guard.flags == {"privacy_tripwire": True, "privacy_unresolved": True}
    assert _clean(_tree(tmp_path))


# -- the run view ---------------------------------------------------------------


def test_a_run_view_records_its_resolved_sites_and_seals(tmp_path: Path) -> None:
    session = _FakeSession(tmp_path)
    ledger = privacy.RunPrivacyLedger(session)
    view = privacy.MacroArgPrivacy()
    assert ledger.resolved_sites == [] and ledger.sealed is False

    ledger.admit("outer", view.admission({}))
    ledger.admit("inner", view.admission({}))
    ledger.admit("inner", view.admission({}))
    ledger.seal()

    assert ledger.resolved_sites == ["outer", "inner"]
    assert ledger.sealed is True


# -- an artifact run ---------------------------------------------------------------


def _install(monkeypatch: pytest.MonkeyPatch, storage: Any, error: str | None = None) -> None:
    async def dispatch(*_a: Any, **_kw: Any) -> tuple[int, int]:
        if error is not None:
            raise RuntimeError(error)
        return 1, 0

    monkeypatch.setattr(execution, "load_macro", storage.load_macro)
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "dispatch_plain_action", dispatch)
    monkeypatch.setattr(execution, "credential_fill_guard", lambda *_a: contextlib.nullcontext())


def _write(storage: Any) -> None:
    storage.write_macro(
        name="m",
        macro={"name": "m", "parameters": ["password"], "actions": [{"action": "click", "selector": "#go"}]},
    )


@pytest.mark.asyncio
async def test_an_artifact_run_trips_on_a_value_the_scrub_missed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write(storage)
    _install(monkeypatch, storage, error="step failed")
    session = _FakeSession(tmp_path)
    # Held by the session from earlier, not by this run: the bundle's scrub
    # uses the run's values, so only the tripwire stands between the operator's
    # notes and the summary.
    privacy.install_sensitive_recorder(session, [SECRET])

    result = await macro_artifacts.run_macro_artifact(
        session, "m", {"password": RUN_VALUE}, capture=False, verify=False, notes=f"saw {SECRET.lower()}"
    )

    assert result["ok"] is False
    assert result["privacy_tripwire"] is True
    assert _clean(json.dumps(result))
    assert "privacy_unresolved" not in result
    assert _clean(_tree(tmp_path / "recordings"))
    assert json.loads(Path(result["paths"]["result"]).read_text())["privacy_tripwire"] is True


@pytest.mark.asyncio
async def test_a_clean_artifact_run_carries_neither_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write(storage)
    _install(monkeypatch, storage)

    result = await macro_artifacts.run_macro_artifact(
        _FakeSession(tmp_path), "m", {"password": SECRET}, capture=False, verify=False
    )

    assert result["ok"] is True
    assert "privacy_tripwire" not in result and "privacy_unresolved" not in result
    written = json.loads(Path(result["paths"]["result"]).read_text())
    assert "privacy_tripwire" not in written and "privacy_unresolved" not in written


@pytest.mark.asyncio
async def test_an_unsealed_view_still_writes_the_bundle_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write(storage)
    _install(monkeypatch, storage)
    monkeypatch.setattr(privacy.RunPrivacyLedger, "seal", lambda _self: None)

    result = await macro_artifacts.run_macro_artifact(
        _FakeSession(tmp_path), "m", {"password": SECRET}, capture=False, verify=False
    )

    assert result["privacy_unresolved"] is True
    written = json.loads(Path(result["paths"]["result"]).read_text())
    assert written["privacy_unresolved"] is True
    assert written["args_used"]["password"] == "<redacted>"
    assert _clean(_tree(tmp_path / "recordings"))


@pytest.mark.asyncio
async def test_a_saturated_session_marks_the_bundle_unresolved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write(storage)
    _install(monkeypatch, storage)
    session = _FakeSession(tmp_path)
    monkeypatch.setenv("OCTOWRIGHT_MACRO_SCRUB_MAX_VALUES", "1")
    privacy.install_sensitive_recorder(session, [SECRET])  # held already, so the run is not refused

    result = await macro_artifacts.run_macro_artifact(session, "m", {"password": SECRET}, capture=False, verify=False)

    assert result["scrub_saturated"] is True
    assert result["privacy_unresolved"] is True
