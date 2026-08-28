# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The read-side artifact tools: digest, listing, and critical-point retrieval.

Three MCP-facing entry points whose survivors shared a theme -- the branch
selection and the caps were never observed. `macro_digest` takes either a macro
name or a recording path and nothing had driven the recording arm;
`list_macro_artifacts` clamps its limit with `max(0, ...)` and nothing had
passed a limit low enough to tell that apart from `max(1, ...)`; and
`macro_artifact_critical_points_get` could report `ok: True` on a missing
manifest with the suite still green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._macro_artifact_fixtures import (
    _reload,
    _write_macro,
    restore_reloaded_defaults,
)


@pytest.fixture(autouse=True)
def _restore_reloaded_defaults():
    yield
    restore_reloaded_defaults()


# ---------------------------------------------------------------------------
# macro_digest -- two input modes, one of them never driven
# ---------------------------------------------------------------------------


def test_digest_requires_one_of_its_two_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Neither argument is a caller error, not an empty digest.

    `name is None and recording_path is None` flipped to `is not None` makes
    the guard fire when a recording path *was* supplied -- rejecting the valid
    call and accepting the empty one.
    """
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="provide either name or recording_path"):
        macro_artifacts.macro_digest()


def test_digest_of_a_macro_names_its_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The macro arm: source type, name, and a real path on disk."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    result = macro_artifacts.macro_digest(name="login")

    assert result["source"]["type"] == "macro"
    assert result["source"]["name"] == "login"
    assert Path(result["source"]["path"]).exists()


def test_digest_of_a_recording_names_its_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The recording arm, which no test had entered.

    Everything below the `if name is not None` early return -- containment,
    the read, the digest call and the source block -- was unreachable from the
    suite, so all of it could be replaced with `None` unnoticed.
    """
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    from octowright.artifacts.paths import ArtifactStore

    recording = ArtifactStore().root / "sample.jsonl"
    recording.parent.mkdir(parents=True, exist_ok=True)
    recording.write_text(
        json.dumps({"ts": 1, "action": "navigate", "url": "https://example.test/"}) + "\n",
        encoding="utf-8",
    )

    result = macro_artifacts.macro_digest(recording_path=str(recording))

    assert result["source"] == {"type": "recording", "path": str(recording)}


def test_digest_rejects_a_recording_outside_the_artifact_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`recording_path` is caller-supplied, so containment is load-bearing."""
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        macro_artifacts.macro_digest(recording_path=str(outside))


# ---------------------------------------------------------------------------
# list_macro_artifacts -- the limit clamp
# ---------------------------------------------------------------------------


def test_listing_reports_its_default_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The documented default is 20 and the payload echoes it back."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    assert macro_artifacts.list_macro_artifacts()["limit"] == 20


def test_a_zero_limit_returns_nothing_rather_than_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`max(0, limit)` vs `max(1, limit)` differ only here.

    With the floor at 1, asking for zero artifacts returns one -- a caller
    paginating down to an empty page instead gets a row it already showed.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    listing = macro_artifacts.list_macro_artifacts(limit=0)

    assert listing["artifacts"] == []
    assert listing["count"] == 0
    assert listing["limit"] == 0


def test_a_negative_limit_is_clamped_to_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A negative slice bound would count from the end and return real rows."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    listing = macro_artifacts.list_macro_artifacts(limit=-5)

    assert listing["artifacts"] == []
    assert listing["limit"] == 0


# ---------------------------------------------------------------------------
# macro_artifact_critical_points_get
# ---------------------------------------------------------------------------


def test_get_reports_a_missing_manifest_as_not_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`ok: False` is the contract; `ok: True` with an error beside it is not."""
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    result = macro_artifacts.macro_artifact_critical_points_get("never-planned")

    assert result["ok"] is False
    assert result["error"] == "Manifest not found."
    assert "critical_points" not in result


def test_get_returns_an_empty_list_for_an_artifact_with_none_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default must be iterable -- callers loop over it without checking."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = macro_artifacts.macro_artifact_critical_points_get("login")

    assert result["ok"] is True
    assert result["critical_points"] == []
    assert Path(result["paths"]["manifest"]).name == "artifact.json"


def test_get_round_trips_what_set_stored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", [{"id": "cp1", "description": "Login succeeds"}])

    result = macro_artifacts.macro_artifact_critical_points_get("login")

    assert [cp["id"] for cp in result["critical_points"]] == ["cp1"]
    assert result["critical_points"][0]["description"] == "Login succeeds"
