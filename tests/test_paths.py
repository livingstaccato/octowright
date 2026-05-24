# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the central path-containment helper.

``reject_unsafe_path`` and ``safe_under`` are the single audited check
between any untrusted (LLM- or operator-supplied) name and a filesystem
operation. Cover every branch so a future change can't quietly weaken the
guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright._paths import reject_unsafe_path, safe_under


def test_safe_under_returns_true_for_inside_path(tmp_path: Path) -> None:
    inside = tmp_path / "child" / "file.txt"
    assert safe_under(inside, tmp_path) is True


def test_safe_under_returns_true_for_root_itself(tmp_path: Path) -> None:
    """A candidate that resolves to root is conventionally inside root."""
    assert safe_under(tmp_path, tmp_path) is True


def test_safe_under_returns_false_for_escaping_path(tmp_path: Path) -> None:
    outside = tmp_path.parent / "sibling" / "file.txt"
    assert safe_under(outside, tmp_path) is False


def test_safe_under_handles_resolve_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If ``.resolve()`` raises (e.g. a too-long path), treat as unsafe."""
    real_resolve = Path.resolve

    def broken_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        raise OSError("simulated resolve failure")

    monkeypatch.setattr(Path, "resolve", broken_resolve)
    try:
        assert safe_under(tmp_path / "x", tmp_path) is False
    finally:
        monkeypatch.setattr(Path, "resolve", real_resolve)


def test_reject_unsafe_path_returns_resolved_candidate(tmp_path: Path) -> None:
    inside = tmp_path / "child" / "file.txt"
    resolved = reject_unsafe_path(inside, tmp_path, label="x")
    assert resolved == inside.resolve()


def test_reject_unsafe_path_raises_for_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "sibling" / "file.txt"
    with pytest.raises(ValueError, match="resolves outside"):
        reject_unsafe_path(outside, tmp_path, label="screenshot path")


def test_reject_unsafe_path_includes_label_in_message(tmp_path: Path) -> None:
    outside = tmp_path.parent / "evil"
    with pytest.raises(ValueError, match="custom-label"):
        reject_unsafe_path(outside, tmp_path, label="custom-label")
