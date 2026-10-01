# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential can come from a private file, and only a private one."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from octowright import personas
from octowright.personas import Persona

SECRET = "s3cret-value"  # pragma: allowlist secret

#: The rules a credential file is held to are POSIX permission bits, owner and
#: link count. Windows has none of them, so there a ``*_file`` credential is
#: refused outright (see ``test_file_credentials_are_refused_on_windows``).
posix_only = pytest.mark.skipif(os.name == "nt", reason="file credentials need POSIX permissions")


# Other persona suites reload octowright.personas in place, which replaces
# MissingCredential; look it up on the module at raise time, never at import.


def persona(path: Path) -> Persona:
    return Persona(name="lab", credentials={"password_file": str(path)})


def private(path: Path, text: str = SECRET + "\n") -> Path:
    path.write_text(text)
    path.chmod(0o600)
    return path


@posix_only
def test_reads_a_private_file_without_its_trailing_newline(tmp_path: Path) -> None:
    assert personas.resolve_credential(persona(private(tmp_path / "p")), "password") == SECRET


@posix_only
def test_group_or_other_permissions_are_refused(tmp_path: Path) -> None:
    path = private(tmp_path / "p")
    path.chmod(0o640)
    with pytest.raises(personas.MissingCredential, match="readable by others") as err:
        personas.resolve_credential(persona(path), "password")
    assert SECRET not in str(err.value)


@posix_only
def test_a_symlink_is_refused(tmp_path: Path) -> None:
    target = private(tmp_path / "p")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(personas.MissingCredential, match="symlink"):
        personas.resolve_credential(persona(link), "password")


@posix_only
def test_a_hardlink_is_refused(tmp_path: Path) -> None:
    target = private(tmp_path / "p")
    os.link(target, tmp_path / "second")
    with pytest.raises(personas.MissingCredential, match="hard link"):
        personas.resolve_credential(persona(target), "password")


@posix_only
def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(personas.MissingCredential, match="empty"):
        personas.resolve_credential(persona(private(tmp_path / "p", "")), "password")


@posix_only
def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(personas.MissingCredential, match="not found"):
        personas.resolve_credential(persona(tmp_path / "absent"), "password")


@posix_only
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


@posix_only
def test_the_credential_check_reports_a_file_source(tmp_path: Path) -> None:
    report = personas.check_credentials(persona(private(tmp_path / "p")))
    assert report["checked"] == [
        {"name": "password", "source": "file", "reference": str(tmp_path / "p"), "ok": True, "error": None}
    ]
    assert SECRET not in str(report)


@posix_only
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_a_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    """A FIFO open blocks until a writer appears; the read must not wait for one."""
    fifo = tmp_path / "p"
    os.mkfifo(fifo, 0o600)
    outcome: list[BaseException] = []

    def attempt() -> None:
        try:
            personas.resolve_credential(persona(fifo), "password")
        except BaseException as exc:  # handed back to the test thread
            outcome.append(exc)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout=5)
    if worker.is_alive():
        # Unblock the stuck open so the daemon thread can finish.
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        pytest.fail("reading a FIFO credential blocked")
    assert len(outcome) == 1
    assert isinstance(outcome[0], personas.MissingCredential)
    assert "not a regular file" in str(outcome[0])


@posix_only
def test_a_file_that_is_not_utf8_is_refused_without_its_bytes(tmp_path: Path) -> None:
    path = tmp_path / "p"
    path.write_bytes(b"abc\xff\xfedef")
    path.chmod(0o600)
    with pytest.raises(personas.MissingCredential, match="not valid UTF-8") as err:
        personas.resolve_credential(persona(path), "password")
    message = str(err.value)
    assert "xff" not in message
    assert "position" not in message
    assert err.value.__cause__ is None
    assert err.value.__suppress_context__


@posix_only
def test_the_credential_check_reports_an_undecodable_file_per_field(tmp_path: Path) -> None:
    path = tmp_path / "p"
    path.write_bytes(b"\xff")
    path.chmod(0o600)
    report = personas.check_credentials(persona(path))
    assert report["ok"] is False
    assert "not valid UTF-8" in str(report["checked"][0]["error"])


def test_file_credentials_are_refused_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No POSIX owner/mode bits to check, so fail closed rather than read."""
    monkeypatch.setattr(personas, "_posix_file_permissions", lambda: False)
    path = tmp_path / "p"
    path.write_text(SECRET)
    with pytest.raises(personas.MissingCredential, match="POSIX") as err:
        personas.resolve_credential(persona(path), "password")
    assert "'password'" in str(err.value)
    assert "password_env" in str(err.value)
    report = personas.check_credentials(persona(path))
    assert report["ok"] is False
    assert report["checked"][0]["source"] == "file"
    assert "POSIX" in str(report["checked"][0]["error"])
    assert SECRET not in str(report)


def test_the_posix_check_matches_the_host() -> None:
    assert personas._posix_file_permissions() is (os.name != "nt")
    if sys.platform == "win32":
        assert not hasattr(os, "O_NOFOLLOW")
