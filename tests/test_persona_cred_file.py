# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential can come from a private file, and only a private one."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from octowright import personas
from octowright.personas import Persona

SECRET = "s3cret-value"  # pragma: allowlist secret


# Other persona suites reload octowright.personas in place, which replaces
# MissingCredential; look it up on the module at raise time, never at import.


def persona(path: Path) -> Persona:
    return Persona(name="lab", credentials={"password_file": str(path)})


def private(path: Path, text: str = SECRET + "\n") -> Path:
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_reads_a_private_file_without_its_trailing_newline(tmp_path: Path) -> None:
    assert personas.resolve_credential(persona(private(tmp_path / "p")), "password") == SECRET


def test_group_or_other_permissions_are_refused(tmp_path: Path) -> None:
    path = private(tmp_path / "p")
    path.chmod(0o640)
    with pytest.raises(personas.MissingCredential, match="readable by others") as err:
        personas.resolve_credential(persona(path), "password")
    assert SECRET not in str(err.value)


def test_a_symlink_is_refused(tmp_path: Path) -> None:
    target = private(tmp_path / "p")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(personas.MissingCredential, match="symlink"):
        personas.resolve_credential(persona(link), "password")


def test_a_hardlink_is_refused(tmp_path: Path) -> None:
    target = private(tmp_path / "p")
    os.link(target, tmp_path / "second")
    with pytest.raises(personas.MissingCredential, match="hard link"):
        personas.resolve_credential(persona(target), "password")


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(personas.MissingCredential, match="empty"):
        personas.resolve_credential(persona(private(tmp_path / "p", "")), "password")


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(personas.MissingCredential, match="not found"):
        personas.resolve_credential(persona(tmp_path / "absent"), "password")


def test_a_file_owned_by_someone_else_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = private(tmp_path / "p")
    monkeypatch.setattr(personas.os, "getuid", lambda: os.stat(path).st_uid + 1)
    with pytest.raises(personas.MissingCredential, match="owned by"):
        personas.resolve_credential(persona(path), "password")


def test_cmd_still_wins_over_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    both = Persona(name="lab", credentials={"password_cmd": "op read x", "password_file": str(tmp_path / "p")})
    monkeypatch.setattr(personas, "_exec_credential_cmd", lambda cmd, name, field: "from-cmd")
    assert personas.resolve_credential(both, "password") == "from-cmd"


def test_file_keys_are_valid_in_persona_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "profile.yaml").write_text("name: lab\ncredentials:\n  password_file: /run/x\n")
    loaded = personas.load_persona("lab")
    assert loaded.credentials == {"password_file": "/run/x"}
    assert personas._credential_names(loaded) == ["password"]


def test_the_credential_check_reports_a_file_source(tmp_path: Path) -> None:
    report = personas.check_credentials(persona(private(tmp_path / "p")))
    assert report["checked"] == [
        {"name": "password", "source": "file", "reference": str(tmp_path / "p"), "ok": True, "error": None}
    ]
    assert SECRET not in str(report)
