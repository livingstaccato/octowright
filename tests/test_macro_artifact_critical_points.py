# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Normalization performed by ``macro_artifact_critical_points_set``.

Roughly 30 mutants survived in this one function. Two blind spots explain
nearly all of them: every test called it on an artifact that *already
existed*, so the branch that builds a manifest from scratch never ran, and
every test passed critical points that already carried an ``id``, so the
generator never ran either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._macro_artifact_fixtures import (
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


def test_critical_points_can_be_set_before_the_artifact_is_planned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The manifest-from-scratch branch, which no test had entered.

    `if not manifest:` builds one via `_manifest_for_plan` from the macro
    itself. Nothing exercised it, so the whole construction -- every keyword,
    and the `parent / "runs"` path joins -- was unobserved.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    result = macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())

    assert result["ok"] is True
    status = macro_artifacts.macro_artifact_status("login")
    assert status["ok"] is True
    assert status["counts"]["total"] == 1


def test_ids_are_generated_in_order_when_omitted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CP1, CP2, CP3 -- one-based and in the order given.

    `f"CP{i + 1}"` reads as arbitrary until something pins it: `i - 1` and
    `i + 2` both produce plausible-looking ids, and a caller that stored `CP1`
    to reference a claim would silently address a different one.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = macro_artifacts.macro_artifact_critical_points_set(
        "login", [{"description": "first"}, {"description": "second"}, {"description": "third"}]
    )

    assert [cp["id"] for cp in result["critical_points"]] == ["CP1", "CP2", "CP3"]


def test_an_empty_id_is_replaced_rather_than_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`"id" not in cp OR not cp["id"]` -- the second arm is the one at risk.

    Swap that `or` for `and` and a present-but-empty id survives normalization,
    so the critical point is stored with `id: ""` and can never be addressed.
    Only a caller who supplies the key with a falsy value reaches it.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = macro_artifacts.macro_artifact_critical_points_set("login", [{"id": "", "description": "blank id"}])

    assert result["critical_points"][0]["id"] == "CP1"


def test_normalization_fills_defaults_without_overwriting_what_was_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each default is applied only when the key is absent.

    Flipping `if "status" not in normalized` to `in` inverts exactly this: a
    caller-supplied status gets clobbered back to "unknown" while an absent one
    is left unset. The defaults are also asserted by value, since `[]` and
    `None` are both falsy and only one is iterable.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = macro_artifacts.macro_artifact_critical_points_set(
        "login",
        [
            {"id": "given", "status": "passed", "checks": [{"type": "result_status", "status": "ok"}]},
            {"id": "bare"},
        ],
    )
    given, bare = result["critical_points"]

    assert given["status"] == "passed"  # supplied value survives
    assert given["checks"] == [{"type": "result_status", "status": "ok"}]
    assert bare["status"] == "unknown"  # absent value is filled
    assert bare["checks"] == []


def test_setting_critical_points_preserves_existing_run_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`if not manifest:` must not fire when a manifest is already there.

    Inverted, an artifact with runs behind it gets rebuilt from the macro and
    loses `latest_run` -- so a later verify has nothing to verify against.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)

    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())
    before = macro_artifacts.macro_artifact_status("login")

    macro_artifacts.macro_artifact_critical_points_set("login", _passing_critical_point())
    after = macro_artifacts.macro_artifact_status("login")

    assert after["latest_run"] == before["latest_run"]


def test_a_stored_critical_point_has_the_canonical_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All seven fields the design doc calls canonical, not three.

    Only `id`, `status` and `checks` were filled, so the rest were absent
    rather than empty and a reader could not tell "not set" from "not yet
    verified". `description` was the visible one: the run report renders
    `cp.get("description", "Unknown")`, so a claim without one came back as
    `### CP1: Unknown` -- naming a critical point while saying nothing about
    what it asserts.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = macro_artifacts.macro_artifact_critical_points_set(
        "login", [{"checks": [{"type": "result_status", "status": "ok"}]}]
    )
    stored = result["critical_points"][0]

    assert set(stored) == {
        "id",
        "description",
        "status",
        "checks",
        "evidence",
        "last_verified_run",
        "notes",
    }
    assert stored["id"] == "CP1"
    assert stored["description"] == "Unknown claim"
    assert stored["status"] == "unknown"
    assert stored["evidence"] == []
    assert stored["last_verified_run"] is None
    assert stored["notes"] is None


def test_every_supplied_field_survives_normalization(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Normalization fills gaps; it never overwrites what the caller said."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    supplied = {
        "id": "login-works",
        "description": "Login succeeds",
        "status": "passed",
        "checks": [{"type": "result_status", "status": "ok"}],
        "evidence": ["ev_001"],
        "last_verified_run": "run_0003",
        "notes": "flaky on staging",
    }
    stored = macro_artifacts.macro_artifact_critical_points_set("login", [dict(supplied)])["critical_points"][0]

    assert stored == supplied


def test_a_blank_description_is_replaced_like_a_missing_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An empty string is not a description, for the same reason `""` is not an id."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    stored = macro_artifacts.macro_artifact_critical_points_set(
        "login", [{"id": "cp1", "description": "", "checks": []}]
    )["critical_points"][0]

    assert stored["description"] == "Unknown claim"
