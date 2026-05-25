# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def fresh_personas(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    # These tests predate the credential-helper allowlist and exercise the
    # downstream error paths (missing binary, timeout, shell semantics) that
    # the allowlist now short-circuits. Opt in to the arbitrary-cmd bypass
    # so the original behaviour under test still runs; the allowlist itself
    # has its own dedicated test module with the bypass disabled.
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS", "1")
    from octowright import defaults

    importlib.reload(defaults)
    from octowright import personas

    importlib.reload(personas)
    from octowright import engine_profiles

    importlib.reload(engine_profiles)
    return personas


def _write_persona(root: Path, name: str, doc: dict) -> None:
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "profile.yaml").write_text(yaml.safe_dump(doc))


def test_load_persona_round_trip(tmp_path, fresh_personas):
    personas = fresh_personas
    _write_persona(
        tmp_path,
        "dante",
        {
            "name": "dante",
            "display_name": "Dante",
            "default_url": "https://example.com",
            "default_macros": ["login"],
            "credentials": {"email_env": "DANTE_EMAIL"},
            "app": {"discord_user_id": "1234", "role": "player"},
        },
    )
    p = personas.load_persona("dante")
    assert p.name == "dante"
    assert p.display_name == "Dante"
    assert p.default_url == "https://example.com"
    assert p.default_macros == ["login"]
    assert p.credentials == {"email_env": "DANTE_EMAIL"}
    assert p.app == {"discord_user_id": "1234", "role": "player"}


def test_load_persona_missing_raises(fresh_personas):
    with pytest.raises(FileNotFoundError):
        fresh_personas.load_persona("ghost")


def test_load_persona_minimal(tmp_path, fresh_personas):
    _write_persona(tmp_path, "bare", {"name": "bare"})
    p = fresh_personas.load_persona("bare")
    assert p.name == "bare"
    assert p.display_name is None
    assert p.default_url is None
    assert p.default_macros == []
    assert p.credentials == {}
    assert p.app == {}


def test_resolve_env_credential(tmp_path, fresh_personas, monkeypatch):
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"email_env": "TEST_EMAIL"},
        },
    )
    monkeypatch.setenv("TEST_EMAIL", "me@example.com")
    p = fresh_personas.load_persona("u")
    assert fresh_personas.resolve_credential(p, "email") == "me@example.com"


def test_resolve_env_missing_raises(tmp_path, fresh_personas, monkeypatch):
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"email_env": "TEST_EMAIL"},
        },
    )
    monkeypatch.delenv("TEST_EMAIL", raising=False)
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential, match="TEST_EMAIL is unset"):
        fresh_personas.resolve_credential(p, "email")


def test_resolve_cmd_credential(tmp_path, fresh_personas):
    command = f'"{sys.executable}" -c "print(\\"hunter2\\")"'
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": command},
        },
    )
    p = fresh_personas.load_persona("u")
    assert fresh_personas.resolve_credential(p, "token") == "hunter2"


def test_resolve_no_references_raises(tmp_path, fresh_personas):
    _write_persona(tmp_path, "u", {"name": "u"})
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential, match="no email_env or email_cmd"):
        fresh_personas.resolve_credential(p, "email")


def test_resolve_cmd_nonzero_exit_raises(tmp_path, fresh_personas):
    command = f'"{sys.executable}" -c "raise SystemExit(7)"'
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": command},
        },
    )
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential, match="cmd exited"):
        fresh_personas.resolve_credential(p, "token")


def test_resolve_cmd_refuses_shell_metachars(tmp_path, fresh_personas):
    """Pipe / redirect / subshell metachars are unconditionally refused.

    Pipelines must be expressed as `bash -c "..."` so the shell invocation
    is an explicit, signed-off-by-the-cmd-author argv token.
    """
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "echo secret | base64"},
        },
    )
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential, match="shell semantics"):
        fresh_personas.resolve_credential(p, "token")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="bash isn't on PATH on Windows runners; the escape hatch on Windows is `pwsh -c ...` or `cmd /c ...`, exercised by other argv-form tests",
)
def test_resolve_cmd_supports_explicit_bash_pipeline(tmp_path, fresh_personas, monkeypatch):
    """`bash -c "..."` is the opt-in escape hatch (gated behind
    OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS) — bash is a normal argv token, the
    pipeline is its own argument."""
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", "1")
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": 'bash -c "echo hunter2 | tr h H"'},
        },
    )
    p = fresh_personas.load_persona("u")
    assert fresh_personas.resolve_credential(p, "token") == "Hunter2"


def test_resolve_cmd_uses_argv_form_for_simple_commands(tmp_path, fresh_personas, monkeypatch):
    """A cmd with no shell metachars is exec'd directly — never via /bin/sh."""
    captured: dict[str, object] = {}

    def _spy_run(*args, **kwargs):
        captured["argv"] = args[0]
        captured["shell"] = kwargs.get("shell")
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    import subprocess as _subprocess

    monkeypatch.setattr(_subprocess, "run", _spy_run)
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "op read op://Personal/Github/token"},
        },
    )
    p = fresh_personas.load_persona("u")
    assert fresh_personas.resolve_credential(p, "token") == "ok"
    assert captured["argv"] == ["op", "read", "op://Personal/Github/token"]
    assert captured["shell"] is None


def test_resolve_cmd_missing_binary_raises_friendly(tmp_path, fresh_personas):
    """A nonexistent binary on the argv path raises MissingCredential, not OSError."""
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "no-such-binary-exists-anywhere"},
        },
    )
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential, match="cmd not found on PATH"):
        fresh_personas.resolve_credential(p, "token")


def test_resolve_cmd_timeout_raises(tmp_path, fresh_personas, monkeypatch):
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "sleep 60"},
        },
    )
    p = fresh_personas.load_persona("u")
    # Patch subprocess.run to raise TimeoutExpired without actually sleeping.
    import subprocess

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 30))

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with pytest.raises(fresh_personas.MissingCredential, match="cmd timed out"):
        fresh_personas.resolve_credential(p, "token")
