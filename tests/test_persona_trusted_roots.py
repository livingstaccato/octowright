# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A persona can name the roots its browsers trust, and nothing else does."""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright import personas


def _write(root: Path, name: str, doc: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "profile.yaml").write_text(doc, encoding="utf-8")


def test_trusted_roots_are_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    _write(tmp_path, "lab", "name: lab\ntrusted_roots:\n  - /etc/lab/root.pem\n")
    assert personas.load_persona("lab").trusted_roots == ["/etc/lab/root.pem"]


def test_a_persona_without_trusted_roots_has_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    _write(tmp_path, "plain", "name: plain\n")
    assert personas.load_persona("plain").trusted_roots == []


@pytest.mark.parametrize(
    "value",
    ["/etc/lab/root.pem", "[1]", "['']", "{a: b}"],
)
def test_trusted_roots_must_be_a_list_of_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    _write(tmp_path, "bad", f"name: bad\ntrusted_roots: {value}\n")
    with pytest.raises(ValueError, match="trusted_roots"):
        personas.load_persona("bad")
