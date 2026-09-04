# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Aliasing and default-shape contracts for the artifact record constructors.

``artifacts/models.py`` had no test file of its own, and mutation testing found
every ``copy.deepcopy`` in ``new_manifest`` free to become ``copy.copy``,
``copy.deepcopy(None)``, or to have its ``is not None`` guard inverted -- plus
``evidence or []`` free to become ``evidence and []`` -- with the whole suite
still green.

The unifying reason these went unnoticed is that a manifest is written to disk
immediately after construction, so an equality assertion on the returned dict
passes under every one of those mutations. What they change is what happens
*next*: whether the caller's list is now shared with the manifest it handed to,
and whether an omitted argument yields an empty container or ``None``. Both are
only observable by mutating the input afterwards, or by asking what the default
is -- neither of which anything did.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.artifacts.models import ARTIFACT_VERSION, new_check_result, new_manifest


def _manifest(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"artifact_type": "macro", "name": "login", "source": {"macro": "login"}}
    return new_manifest(**{**base, **overrides})


# ---------------------------------------------------------------------------
# new_manifest -- defensive copying
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["exports", "critical_points"])
def test_a_supplied_list_is_deep_copied_not_aliased(field: str) -> None:
    """Mutating the caller's list -- or anything nested inside it -- must not reach the manifest.

    ``copy.copy`` passes an equality check and still shares every nested dict,
    so the caller keeps a live handle into a manifest that is about to be
    written to disk. A critical point edited after construction would then be
    persisted under a manifest that never declared it.
    """
    supplied = [{"id": "cp1", "detail": {"selector": "#ok"}}]

    manifest = _manifest(**{field: supplied})

    supplied.append({"id": "cp2"})
    supplied[0]["detail"]["selector"] = "#tampered"

    assert manifest[field] == [{"id": "cp1", "detail": {"selector": "#ok"}}]


def test_the_source_mapping_is_deep_copied_too() -> None:
    """``source`` takes no ``is not None`` guard, so only the copy itself protects it."""
    source = {"macro": "login", "args": {"user": "tanuki-tim"}}

    manifest = _manifest(source=source)
    source["args"]["user"] = "someone-else"

    assert manifest["source"] == {"macro": "login", "args": {"user": "tanuki-tim"}}


# ---------------------------------------------------------------------------
# new_manifest -- omitted-argument defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("field", "empty"), [("exports", []), ("critical_points", []), ("metadata", {})])
def test_an_omitted_container_defaults_to_empty_rather_than_null(field: str, empty: Any) -> None:
    """Each guard is ``if x is not None``, and inverting it swaps the two branches.

    The manifest is JSON on disk and every reader indexes these keys directly.
    ``null`` where a list is expected is a ``TypeError`` at read time, in a
    different process from the one that wrote it.
    """
    manifest = _manifest()

    assert manifest[field] == empty


def test_a_supplied_container_is_carried_through_rather_than_defaulted() -> None:
    """The other side of the same guard: supplying a value must not yield the default.

    Inverting ``is not None`` makes every supplied list silently become ``[]``
    -- an artifact that records no exports and no critical points, and so
    verifies vacuously.
    """
    manifest = _manifest(exports=[{"path": "a.py"}], critical_points=[{"id": "cp1"}], metadata={"note": "x"})

    assert manifest["exports"] == [{"path": "a.py"}]
    assert manifest["critical_points"] == [{"id": "cp1"}]
    assert manifest["metadata"] == {"note": "x"}


def test_a_new_manifest_carries_the_current_artifact_version() -> None:
    """Readers dispatch on this; a manifest written without it is unversioned."""
    assert _manifest()["artifact_version"] == ARTIFACT_VERSION


# ---------------------------------------------------------------------------
# new_check_result
# ---------------------------------------------------------------------------


def test_check_result_evidence_defaults_to_an_empty_list() -> None:
    assert new_check_result(check_type="url", status="passed", message="ok")["evidence"] == []


def test_check_result_keeps_the_evidence_it_was_given() -> None:
    """``evidence or []`` against ``evidence and []``.

    Under the ``and`` mutation a truthy list yields ``[]`` -- so every check
    result is recorded with its evidence stripped, while the check still
    reports ``passed`` and nothing raises. The verification report then shows
    a verdict with nothing backing it.
    """
    result = new_check_result(check_type="url", status="failed", message="no", evidence=["ev_001", "ev_002"])

    assert result["evidence"] == ["ev_001", "ev_002"]
