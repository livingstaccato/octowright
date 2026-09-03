# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Argv-form allowlist for persona credential cmds.

The shell-form (``bash -c "..."``) gate is exercised by ``test_personas.py``.
These tests cover the parallel allowlist for argv-form cmds: well-known
credential helpers (``op``, ``vault``, ``gpg``, …) run unconditionally,
anything else is default-denied with a clear bypass hint.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


@pytest.fixture
def fresh_personas(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS", raising=False)
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", raising=False)
    from octowright import defaults

    importlib.reload(defaults)
    from octowright import personas

    importlib.reload(personas)
    return personas


def _write_persona(root: Path, name: str, doc: dict) -> None:
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "profile.yaml").write_text(yaml.safe_dump(doc))


def _spy_run_factory(captured: dict[str, object]):
    def _spy_run(*args, **kwargs):
        captured["argv"] = args[0]
        captured["shell"] = kwargs.get("shell")
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    return _spy_run


def test_allowlisted_cmd_runs_without_opt_in(tmp_path, fresh_personas, monkeypatch):
    """A bare ``op`` invocation is on the static allowlist and needs no env opt-in."""
    captured: dict[str, object] = {}
    import subprocess

    monkeypatch.setattr(subprocess, "run", _spy_run_factory(captured))
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "op read op://vault/x"},
        },
    )
    p = fresh_personas.load_persona("u")
    assert fresh_personas.resolve_credential(p, "token") == "ok"
    assert captured["argv"] == ["op", "read", "op://vault/x"]
    assert captured["shell"] is None


def test_non_allowlisted_cmd_raises_with_env_hint(tmp_path, fresh_personas, monkeypatch):
    """``/tmp/evil.sh`` is not on the allowlist; the error must name the env var."""
    import subprocess

    # Should never be called — the gate trips first. Make any call explode loudly.
    def _explode(*args, **kwargs):
        raise AssertionError(f"subprocess.run should not be reached; got {args!r}")

    monkeypatch.setattr(subprocess, "run", _explode)
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "/tmp/evil.sh"},
        },
    )
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential) as excinfo:
        fresh_personas.resolve_credential(p, "token")
    msg = str(excinfo.value)
    assert "/tmp/evil.sh" in msg
    assert "OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS" in msg
    assert "allowlist" in msg


def test_non_allowlisted_cmd_runs_with_opt_in(tmp_path, fresh_personas, monkeypatch):
    """With OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS=1 the gate is bypassed."""
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS", "1")
    captured: dict[str, object] = {}
    import subprocess

    monkeypatch.setattr(subprocess, "run", _spy_run_factory(captured))
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "/usr/local/bin/custom-cred-helper --field token"},
        },
    )
    p = fresh_personas.load_persona("u")
    assert fresh_personas.resolve_credential(p, "token") == "ok"
    assert captured["argv"] == ["/usr/local/bin/custom-cred-helper", "--field", "token"]
    assert captured["shell"] is None


def test_path_prefixed_allowlisted_cmd_resolves_via_basename(tmp_path, fresh_personas, monkeypatch):
    """``/opt/homebrew/bin/op`` is allowlisted because the basename is ``op``."""
    captured: dict[str, object] = {}
    import subprocess

    monkeypatch.setattr(subprocess, "run", _spy_run_factory(captured))
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "/opt/homebrew/bin/op read op://vault/x"},
        },
    )
    p = fresh_personas.load_persona("u")
    assert fresh_personas.resolve_credential(p, "token") == "ok"
    assert captured["argv"] == ["/opt/homebrew/bin/op", "read", "op://vault/x"]


def test_failed_cmd_error_does_not_leak_stderr_content(tmp_path, fresh_personas, monkeypatch):
    """A credential helper that exits non-zero must not have its stderr content
    surfaced in the caller-facing error — stderr can carry a secret. The message
    keeps the exit code (diagnostic) but not the stderr text."""
    import subprocess

    def _fail_run(*args, **kwargs):
        return SimpleNamespace(returncode=7, stdout="", stderr="SECRET_LEAK_TOKEN=hunter2")

    monkeypatch.setattr(subprocess, "run", _fail_run)
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "op read op://vault/x"},
        },
    )
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential) as excinfo:
        fresh_personas.resolve_credential(p, "token")
    msg = str(excinfo.value)
    assert "SECRET_LEAK_TOKEN" not in msg
    assert "hunter2" not in msg
    assert "7" in msg  # exit code retained for diagnosis


def test_empty_argv_raises_validation_error_not_permission_error(tmp_path, fresh_personas, monkeypatch):
    """An empty/whitespace-only cmd must surface as a parse/validation error,
    not as a misleading "not on the allowlist" permission failure.

    MissingCredential is the codebase's RuntimeError subclass that wraps both
    validation and runtime failures; the assertion here is that we get the
    "empty after parsing" message, NOT the allowlist-bypass message.
    """
    import subprocess

    def _explode(*args, **kwargs):
        raise AssertionError("subprocess.run should not be reached for empty argv")

    monkeypatch.setattr(subprocess, "run", _explode)
    _write_persona(
        tmp_path,
        "u",
        {
            "name": "u",
            "credentials": {"token_cmd": "   "},
        },
    )
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential) as excinfo:
        fresh_personas.resolve_credential(p, "token")
    msg = str(excinfo.value)
    assert "empty" in msg.lower()
    assert "OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS" not in msg


# ---------------------------------------------------------------------------
# The two gates are mutually exclusive, and the audit trail is a contract.
#
# Everything below kills a mutant that survived the 2026-08-27 run. The gate
# itself was well covered on the argv side; what nothing asserted was how a
# command gets CLASSIFIED between the two gates, and whether the warning the
# docstring calls "audit-able" actually identifies anything.
# ---------------------------------------------------------------------------


def test_bash_with_no_script_is_still_classified_as_shell_form(tmp_path, fresh_personas, monkeypatch):
    """``bash -c`` — exactly two tokens — is the boundary of the shell check.

    ``len(argv) >= 2`` is what makes a two-token ``bash -c`` read as shell
    form. Relax it to ``> 2`` and the classification silently flips: the
    command stops being shell form and falls through to the argv allowlist
    instead. Both still refuse here, so a test asserting only "it raised"
    passes either way -- which is why this asserts WHICH gate refused.

    It matters because the two errors tell an operator to do different things
    (set the shell opt-in vs. use an allowlisted helper), and because under
    ``OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS=1`` the reclassified command is no
    longer refused at all: the arbitrary opt-in would grant an interpreter the
    shell opt-in was deliberately withheld from. A two-token ``bash -c``
    carries no payload, so this is a gate-decision defect rather than a live
    exploit -- but the decision is the thing the gate exists to make.
    """
    import subprocess

    def _explode(*args, **kwargs):
        raise AssertionError(f"subprocess.run should not be reached; got {args!r}")

    monkeypatch.setattr(subprocess, "run", _explode)
    # The arbitrary-form opt-in is ON and the shell opt-in is OFF: the exact
    # configuration where a misclassification stops being just a wrong message.
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS", "1")
    _write_persona(tmp_path, "u", {"name": "u", "credentials": {"token_cmd": "bash -c"}})

    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential) as excinfo:
        fresh_personas.resolve_credential(p, "token")

    msg = str(excinfo.value)
    assert "arbitrary shell execution" in msg
    assert "OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS" in msg
    assert "allowlist" not in msg


def test_shell_form_refusal_names_the_interpreter_not_the_flag(tmp_path, fresh_personas, monkeypatch):
    """The refusal must quote ``argv[0]``, the interpreter, not ``argv[1]``.

    Both indices exist on every shell-form argv, so an off-by-one prints
    ``'-c'`` where the operator expects ``'/bin/bash'`` -- a message naming the
    flag it already knows about instead of the binary it refused to run. The
    original assertions only looked for the env-var name, which both spellings
    contain.
    """
    import subprocess

    def _explode(*args, **kwargs):
        raise AssertionError("subprocess.run should not be reached")

    monkeypatch.setattr(subprocess, "run", _explode)
    _write_persona(tmp_path, "u", {"name": "u", "credentials": {"token_cmd": "/bin/zsh -c 'echo hi'"}})

    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential) as excinfo:
        fresh_personas.resolve_credential(p, "token")

    msg = str(excinfo.value)
    assert "/bin/zsh" in msg
    assert "'-c' with -c" not in msg


def test_shell_opt_in_warning_identifies_the_persona_and_field(tmp_path, fresh_personas, monkeypatch, caplog):
    """Opting in downgrades a refusal to a warning, so the warning IS the record.

    ``_enforce_credential_cmd_policy``'s docstring says both opt-in paths log
    "so the trust boundary stays audit-able". Nothing checked that. Every
    existing assertion matches the event NAME only, and the event name is the
    one part of the line that says nothing about which persona ran what --
    drop ``persona=``, ``field=`` or ``interpreter=`` and the audit line
    survives every test while becoming useless to the person reading it after
    the fact.
    """
    import logging
    import subprocess

    monkeypatch.setenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", "1")
    captured: dict[str, object] = {}
    monkeypatch.setattr(subprocess, "run", _spy_run_factory(captured))
    _write_persona(tmp_path, "dante", {"name": "dante", "credentials": {"token_cmd": "/bin/bash -c 'echo hi'"}})

    p = fresh_personas.load_persona("dante")
    with caplog.at_level(logging.WARNING):
        assert fresh_personas.resolve_credential(p, "token") == "ok"

    line = next(r.getMessage() for r in caplog.records if "credential_cmd_executes_shell_pipeline" in r.getMessage())
    assert "persona=dante" in line
    assert "field=token" in line
    assert "interpreter=/bin/bash" in line


def test_arbitrary_opt_in_warning_identifies_the_persona_and_field(tmp_path, fresh_personas, monkeypatch, caplog):
    """The same contract on the other opt-in path, which has its own log call.

    Two call sites means two chances to lose the identifying fields, and a
    test written against one proves nothing about the other. This one also
    pins the event name itself: replacing it with ``None`` left an unnamed
    warning that no log query could ever select.
    """
    import logging
    import subprocess

    monkeypatch.setenv("OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS", "1")
    captured: dict[str, object] = {}
    monkeypatch.setattr(subprocess, "run", _spy_run_factory(captured))
    _write_persona(tmp_path, "ziggy", {"name": "ziggy", "credentials": {"api_key_cmd": "/tmp/helper.sh"}})

    p = fresh_personas.load_persona("ziggy")
    with caplog.at_level(logging.WARNING):
        assert fresh_personas.resolve_credential(p, "api_key") == "ok"

    line = next(r.getMessage() for r in caplog.records if "credential_cmd_executes_arbitrary_binary" in r.getMessage())
    assert "persona=ziggy" in line
    assert "field=api_key" in line
