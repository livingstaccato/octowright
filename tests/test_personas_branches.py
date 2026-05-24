# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch and edge-case coverage for ``octowright.personas`` aimed at killing
mutmut survivors. Each test asserts on a specific behaviour (exact error
message, log emit, default fallback, branch direction) so that mutating the
underlying code produces an observable test failure.
"""

from __future__ import annotations

import importlib
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml


@pytest.fixture
def fresh_personas(tmp_path, monkeypatch):
    """Same isolated PROFILES_DIR pattern used by tests/test_personas.py."""
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    from octowright import defaults

    importlib.reload(defaults)
    from octowright import personas

    importlib.reload(personas)
    from octowright import profiles

    importlib.reload(profiles)
    return personas


def _write_persona(root: Path, name: str, doc: dict) -> None:
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "profile.yaml").write_text(yaml.safe_dump(doc))


# ---------------------------------------------------------------------------
# _slug
# ---------------------------------------------------------------------------


def test_slug_replaces_runs_of_bad_chars_with_single_dash(fresh_personas):
    """Mutation: change re.sub replacement string would alter slug formatting."""
    assert fresh_personas._slug("a   b") == "a-b"
    assert fresh_personas._slug("a/!@#b") == "a-b"


def test_slug_strips_leading_and_trailing_dashes_and_dots(fresh_personas):
    """Mutation: removing the .strip("-.") would leave decorations."""
    assert fresh_personas._slug("--cosmo--") == "cosmo"
    assert fresh_personas._slug("...ziggy...") == "ziggy"
    assert fresh_personas._slug(".-mix-.") == "mix"


def test_slug_strips_outer_whitespace_before_substitution(fresh_personas):
    """Mutation: removing the .strip() before sub would emit dashes for spaces."""
    assert fresh_personas._slug("  cosmo  ") == "cosmo"


def test_slug_empty_input_raises_with_exact_message(fresh_personas):
    """Mutation: changing the error string or skipping the raise would survive."""
    with pytest.raises(ValueError, match="produced an empty slug"):
        fresh_personas._slug("---")
    with pytest.raises(ValueError, match="produced an empty slug"):
        fresh_personas._slug("...")
    with pytest.raises(ValueError, match="produced an empty slug"):
        fresh_personas._slug("")


def test_slug_keeps_underscores_dots_dashes(fresh_personas):
    """Allowed chars [A-Za-z0-9._-] survive untouched (mid-string)."""
    assert fresh_personas._slug("alice_v2.1-beta") == "alice_v2.1-beta"


# ---------------------------------------------------------------------------
# engine_profile_dir
# ---------------------------------------------------------------------------


def test_engine_profile_dir_unsupported_kind_raises(fresh_personas):
    """Mutation: removing the kind validation would let invalid engines through."""
    with pytest.raises(ValueError, match="kind must be one of"):
        fresh_personas.engine_profile_dir("cosmo", "safari")


def test_engine_profile_dir_each_supported_kind_resolves(fresh_personas, tmp_path):
    """Mutation: changing the path composition would produce wrong directories."""
    for kind in ("chromium", "firefox", "webkit"):
        path = fresh_personas.engine_profile_dir("cosmo", kind)
        assert path == tmp_path / "cosmo" / kind


def test_engine_profile_dir_kind_check_is_strict_string_match(fresh_personas):
    """Empty / whitespace / case-mismatched kinds are rejected."""
    for bad in ("", "Chromium", "CHROMIUM", "firefoxx"):
        with pytest.raises(ValueError, match="kind must be one of"):
            fresh_personas.engine_profile_dir("cosmo", bad)


# ---------------------------------------------------------------------------
# _credential_cmd_argv — shell-operator rejection coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_cmd",
    [
        "echo a || echo b",
        "echo a && echo b",
        "echo a ; echo b",
        "echo a & sleep 1",
        "echo a > out",
        "echo a >> out",
        "cat < in",
        "cat << EOF",
        "cat <<< text",
        "echo $(date)",
        "( echo a )",
    ],
)
def test_credential_cmd_argv_rejects_each_shell_operator(fresh_personas, bad_cmd):
    """Mutation: shrinking _SHELL_OPERATOR_TOKENS would let some operator slip through.

    Note: backtick command-substitution (``echo `date```) isn't shlex-tokenised
    as a standalone backtick, so it's not asserted here; the ``$(...)`` form is
    the recommended way to express command substitution and is covered above.
    """
    with pytest.raises(fresh_personas.MissingCredential, match="shell semantics"):
        fresh_personas._credential_cmd_argv(bad_cmd, "cosmo", "token")


def test_credential_cmd_argv_rejects_dollar_paren_prefix(fresh_personas):
    """`$(foo)` produces a token starting with `$(` — covered by startswith branch."""
    with pytest.raises(fresh_personas.MissingCredential, match="shell semantics"):
        fresh_personas._credential_cmd_argv("echo $(whoami)", "cosmo", "token")


def test_credential_cmd_argv_empty_after_split_raises(fresh_personas):
    """Mutation: removing the ``if not argv`` guard would return an empty argv list."""
    with pytest.raises(fresh_personas.MissingCredential, match="cmd is empty after parsing"):
        fresh_personas._credential_cmd_argv("   ", "cosmo", "token")
    with pytest.raises(fresh_personas.MissingCredential, match="cmd is empty after parsing"):
        fresh_personas._credential_cmd_argv("", "cosmo", "token")


def test_credential_cmd_argv_parse_failure_raises_friendly(fresh_personas):
    """An unclosed quote raises shlex.ValueError -> MissingCredential."""
    with pytest.raises(fresh_personas.MissingCredential, match="cmd parse failure"):
        fresh_personas._credential_cmd_argv("echo 'unterminated", "cosmo", "token")


def test_credential_cmd_argv_error_includes_persona_and_field_names(fresh_personas):
    """Mutation: dropping persona/cred from the message would survive without this."""
    with pytest.raises(fresh_personas.MissingCredential) as exc:
        fresh_personas._credential_cmd_argv("echo a | b", "dante", "password")
    msg = str(exc.value)
    assert "dante" in msg
    assert "password" in msg


def test_credential_cmd_argv_returns_argv_for_valid_cmd(fresh_personas):
    """The happy path returns a real argv list — mutating to ``[]`` would fail this."""
    argv = fresh_personas._credential_cmd_argv("op read op://Vault/Item", "cosmo", "token")
    assert argv == ["op", "read", "op://Vault/Item"]


def test_credential_cmd_argv_quoted_metachar_is_allowed(fresh_personas):
    """Quoted shell metachars are folded into one argv token — must NOT raise."""
    argv = fresh_personas._credential_cmd_argv("printf 'a|b'", "cosmo", "token")
    assert argv == ["printf", "a|b"]


# ---------------------------------------------------------------------------
# MissingCredential
# ---------------------------------------------------------------------------


def test_missing_credential_is_runtime_error_subclass(fresh_personas):
    """Mutation: changing the base class of MissingCredential would surface here."""
    assert issubclass(fresh_personas.MissingCredential, RuntimeError)


# ---------------------------------------------------------------------------
# resolve_credential branches
# ---------------------------------------------------------------------------


def test_resolve_credential_both_set_emits_warning(monkeypatch, caplog):
    """When both _env and _cmd exist, the cmd path runs but a warning fires.
    Mutation: dropping the log.warning would still pass other tests but not this."""
    from octowright import personas as _p

    persona = _p.Persona(
        name="dante",
        credentials={"token_env": "X", "token_cmd": "echo ok"},
    )

    def _fake_run(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with caplog.at_level(logging.WARNING):
        result = _p.resolve_credential(persona, "token")
    assert result == "ok"
    assert any("persona.cred.both_set" in rec.message for rec in caplog.records)


def test_resolve_credential_no_refs_message_mentions_persona_yaml_path(monkeypatch):
    """The NotFound error guides the user to add a ref under credentials."""
    from octowright import personas as _p

    persona = _p.Persona(name="dante")
    with pytest.raises(_p.MissingCredential) as exc:
        _p.resolve_credential(persona, "email")
    msg = str(exc.value)
    assert "email_env" in msg
    assert "email_cmd" in msg
    assert "profile.yaml" in msg
    # Suggests the env-var name uppercase too.
    assert "EMAIL_VAR" in msg


# ---------------------------------------------------------------------------
# _exec_credential_cmd
# ---------------------------------------------------------------------------


def test_exec_credential_cmd_truncates_stderr_at_200_chars(monkeypatch):
    """Mutation: removing or growing the [:200] slice would change error length."""
    from octowright import personas as _p

    long_stderr = "X" * 500

    def _fake(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=2, stdout="", stderr=long_stderr)

    monkeypatch.setattr(subprocess, "run", _fake)
    with pytest.raises(_p.MissingCredential) as exc:
        _p._exec_credential_cmd("op read op://x", "dante", "token")
    msg = str(exc.value)
    # Truncated to 200 X's, not the full 500.
    assert "X" * 200 in msg
    assert "X" * 201 not in msg


def test_exec_credential_cmd_strips_stdout(monkeypatch):
    """Mutation: dropping ``.strip()`` would leak trailing whitespace into the secret."""
    from octowright import personas as _p

    def _fake(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="  hunter2  \n", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake)
    assert _p._exec_credential_cmd("echo hunter2", "dante", "password") == "hunter2"


def test_exec_credential_cmd_nonzero_message_includes_returncode(monkeypatch):
    """Exact ``cmd exited <code>`` substring."""
    from octowright import personas as _p

    def _fake(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=42, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", _fake)
    with pytest.raises(_p.MissingCredential, match="cmd exited 42"):
        _p._exec_credential_cmd("op read op://x", "dante", "token")


def test_exec_credential_cmd_filenotfound_message_includes_filename(monkeypatch):
    """The friendly error names the missing binary, not just 'cmd not found'."""
    from octowright import personas as _p

    def boom(*a, **kw):
        raise FileNotFoundError(2, "No such file or directory", "no-such-bin")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(_p.MissingCredential) as exc:
        _p._exec_credential_cmd("no-such-bin", "dante", "token")
    msg = str(exc.value)
    assert "cmd not found on PATH" in msg
    assert "no-such-bin" in msg


def test_exec_credential_cmd_uses_argv_form_no_shell(monkeypatch):
    """Mutation: flipping ``shell=False`` (default) to True would survive without
    asserting on the call kwargs."""
    from octowright import personas as _p

    captured: dict[str, Any] = {}

    def fake(*a, **kw):
        captured["argv"] = a[0]
        captured["shell"] = kw.get("shell")
        captured["timeout"] = kw.get("timeout")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake)
    _p._exec_credential_cmd("op read op://x", "dante", "token")
    assert captured["argv"] == ["op", "read", "op://x"]
    # subprocess.run default is shell=False; we must NOT have passed shell=True.
    assert captured["shell"] is None or captured["shell"] is False
    assert captured["timeout"] == 30


# ---------------------------------------------------------------------------
# persona_dir
# ---------------------------------------------------------------------------


def test_persona_dir_uses_slug_under_profiles_dir(tmp_path, fresh_personas):
    """Mutation: dropping the ``_slug(name)`` call would expose raw user input."""
    assert fresh_personas.persona_dir("Cosmo Smith") == tmp_path / "Cosmo-Smith"


# ---------------------------------------------------------------------------
# bash-c trust-boundary warning — basename match (PR #18 follow-up)
# ---------------------------------------------------------------------------


def test_credential_cmd_bare_bash_dash_c_warns(monkeypatch, caplog):
    """Bare interpreter name (`bash -c '...'`) trips the warning when the
    opt-in env var is set. Pin the existing behaviour."""
    from octowright import personas as _p

    def _fake(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", "1")
    monkeypatch.setattr(subprocess, "run", _fake)
    with caplog.at_level(logging.WARNING):
        _p._exec_credential_cmd("bash -c 'echo hi | tr h H'", "dante", "token")
    assert any("personas.credential_cmd_executes_shell_pipeline" in rec.message for rec in caplog.records)


def test_credential_cmd_absolute_path_bash_dash_c_warns(monkeypatch, caplog):
    """Absolute-path interpreter (`/bin/bash -c '...'`) ALSO trips the warning
    when the opt-in env var is set. The PR #18 implementation matched the bare
    token only, so a YAML author using an absolute path quietly evaded the
    trust-boundary signal — `shell=False` still kept execution safe, but the
    operator no longer saw the heads-up. Pin the basename-match fix."""
    from octowright import personas as _p

    def _fake(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", "1")
    monkeypatch.setattr(subprocess, "run", _fake)
    with caplog.at_level(logging.WARNING):
        _p._exec_credential_cmd("/bin/bash -c 'echo hi | tr h H'", "dante", "token")
    assert any("personas.credential_cmd_executes_shell_pipeline" in rec.message for rec in caplog.records)


def test_credential_cmd_bash_dash_c_rejected_by_default(monkeypatch):
    """Default (env var unset) refuses ``bash -c`` style cmds with an error
    pointing at the gating env var."""
    from octowright import personas as _p

    monkeypatch.delenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", raising=False)
    with pytest.raises(_p.MissingCredential, match="OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS"):
        _p._exec_credential_cmd('bash -c "echo x"', "dante", "token")


def test_credential_cmd_argv_form_allowed_by_default(monkeypatch):
    """Argv-form helpers (``op read op://…``) must run regardless of the
    shell-cred-cmds gate."""
    from octowright import personas as _p

    monkeypatch.delenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", raising=False)

    def _fake(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="secret\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake)
    assert _p._exec_credential_cmd("op read op://vault/item/token", "dante", "token") == "secret"


def test_credential_cmd_each_shell_interpreter_rejected_by_default(monkeypatch):
    """Every interpreter basename in the default-deny set trips the gate."""
    from octowright import personas as _p

    monkeypatch.delenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", raising=False)
    for interp in ("bash", "sh", "zsh", "fish", "dash", "ksh"):
        with pytest.raises(_p.MissingCredential, match="OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS"):
            _p._exec_credential_cmd(f"{interp} -c 'echo x'", "dante", "token")


def test_credential_cmd_bash_dash_c_allowed_when_env_set(monkeypatch):
    """Opt-in env var permits ``bash -c`` to reach subprocess."""
    from octowright import personas as _p

    monkeypatch.setenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", "1")

    def _fake(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="from-pipeline\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake)
    assert _p._exec_credential_cmd('bash -c "echo from-pipeline"', "dante", "token") == "from-pipeline"


def test_credential_cmd_non_shell_does_not_warn(monkeypatch, caplog):
    """A non-shell interpreter (`op read ...`) must NOT trip the warning."""
    from octowright import personas as _p

    def _fake(*_a: Any, **_kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake)
    with caplog.at_level(logging.WARNING):
        _p._exec_credential_cmd("op read op://vault/item/password", "dante", "password")
    assert not any("personas.credential_cmd_executes_shell_pipeline" in rec.message for rec in caplog.records)


def test_persona_dir_empty_slug_propagates_error(fresh_personas):
    """If the name slugs to empty, persona_dir should raise (not return PROFILES_DIR)."""
    with pytest.raises(ValueError, match="produced an empty slug"):
        fresh_personas.persona_dir("---")
