# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Windows parsing and matching of persona credential cmds.

``shlex.split`` applies POSIX escaping, so on Windows an absolute helper path
such as ``C:\\bin\\op.exe`` lost its backslashes and reached the allowlist and
``subprocess.run`` as ``C:binop.exe``. The allowlist and the shell-interpreter
gate also compared ``Path(argv[0]).name`` exactly, so ``op.exe`` never matched
``op`` and ``bash.exe -c`` slipped past the shell gate into the arbitrary-cmd
gate. Everything here is driven through the ``windows=`` seam, so it runs on
every OS and never starts a real executable.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


@pytest.fixture
def personas(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS", raising=False)
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS", raising=False)
    from octowright import defaults

    importlib.reload(defaults)
    from octowright import personas as module

    importlib.reload(module)
    return module


# --- the splitter -----------------------------------------------------------

_SPLIT_TABLE = [
    # whitespace separates, runs of it collapse
    ("op item get x", ["op", "item", "get", "x"]),
    ("  op \t item  ", ["op", "item"]),
    # backslashes are literal when no quote follows
    (r"C:\bin\op.exe item get x", [r"C:\bin\op.exe", "item", "get", "x"]),
    (r"\\server\share\op.exe", [r"\\server\share\op.exe"]),
    # double quotes group, and are removed
    (
        r'"C:\Program Files\1Password CLI\op.exe" item get x',
        [r"C:\Program Files\1Password CLI\op.exe", "item", "get", "x"],
    ),
    (r'C:\\"Program Files"\op.exe', [r"C:\Program Files\op.exe"]),
    ('a"b c"d', ["ab cd"]),
    # an empty quoted pair is an empty argument
    ('op ""', ["op", ""]),
    ('op "" x', ["op", "", "x"]),
    # 2n+1 backslashes + quote -> n backslashes and a literal quote
    (r"a\"b", ['a"b']),
    (r"a\\\"b", ['a\\"b']),
    (r'"say \"hi\""', ['say "hi"']),
    # 2n backslashes + quote -> n backslashes, and the quote toggles grouping
    (r'a\\"b c"', ["a\\b c"]),
    (r'"C:\dir\\" next', ["C:\\dir\\", "next"]),
    (r'"trail\\\\"', ["trail\\\\"]),
    # backslashes not followed by a quote stay as they are, even at the end
    ("a\\\\ b\\", ["a\\\\", "b\\"]),
    # shell metacharacters inside quotes are part of the argument
    ('bash -c "op read x | head -1"', ["bash", "-c", "op read x | head -1"]),
    # single quotes group as on POSIX (Windows itself does not), so one persona
    # file splits the same everywhere; inside them every character is literal
    ("bash -c 'op read x | head -1'", ["bash", "-c", "op read x | head -1"]),
    ("printf 'a|b'", ["printf", "a|b"]),
    (r"'C:\Program Files\op.exe' item", [r"C:\Program Files\op.exe", "item"]),
    (r"""'say "hi"' x""", ['say "hi"', "x"]),
    (r"'a\\\"b'", [r"a\\\"b"]),
    ("op '' x", ["op", "", "x"]),
    ("x'y z'w", ["xy zw"]),
    # inside double quotes a single quote is literal, and vice versa
    (""""it's" ok""", ["it's", "ok"]),
    # '#' is an ordinary character, not a comment
    ("op read vault#item", ["op", "read", "vault#item"]),
    # the existing \" form keeps working (it did under shlex)
    (r'python -c "print(\"ok\")"', ["python", "-c", 'print("ok")']),
    ("", []),
    ("   ", []),
]


@pytest.mark.parametrize(("cmd", "expected"), _SPLIT_TABLE)
def test_windows_splitter_rules(personas, cmd, expected):
    assert personas._split_windows_cmdline(cmd) == expected


@pytest.mark.parametrize("cmd", ['op "unterminated', r'op "x\"', '"', "op 'unterminated", "'"])
def test_windows_splitter_refuses_an_unterminated_quote(personas, cmd):
    with pytest.raises(ValueError, match="No closing quotation"):
        personas._split_windows_cmdline(cmd)


def test_windows_argv_keeps_backslashes_in_absolute_paths(personas):
    argv = personas._credential_cmd_argv(r"C:\bin\op.exe item get x", "p", "token", windows=True)
    assert argv == [r"C:\bin\op.exe", "item", "get", "x"]


def test_windows_argv_keeps_a_quoted_path_with_spaces(personas):
    cmd = r'"C:\Program Files\1Password CLI\op.exe" item get x'
    argv = personas._credential_cmd_argv(cmd, "p", "token", windows=True)
    assert argv == [r"C:\Program Files\1Password CLI\op.exe", "item", "get", "x"]


def test_windows_unterminated_quote_is_a_parse_failure(personas):
    with pytest.raises(personas.MissingCredential, match=r"cmd parse failure: No closing quotation"):
        personas._credential_cmd_argv('op "item', "p", "token", windows=True)


@pytest.mark.parametrize("cmd", ["op read x | head", "op read x && whoami", "op read x > out.txt"])
def test_windows_still_refuses_bare_shell_operators(personas, cmd):
    with pytest.raises(personas.MissingCredential, match="cmd uses shell semantics"):
        personas._credential_cmd_argv(cmd, "p", "token", windows=True)


@pytest.mark.parametrize("cmd", ["", "   ", "\t"])
def test_windows_empty_cmd_is_refused(personas, cmd):
    with pytest.raises(personas.MissingCredential, match="cmd is empty after parsing"):
        personas._credential_cmd_argv(cmd, "p", "token", windows=True)


# --- POSIX is unchanged -------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        r"C:\bin\op.exe item get x",
        r'"C:\Program Files\op.exe" item',
        r"op read a\ b",
        "op read 'single quoted' \"double\"",
        r"a\"b",
    ],
)
def test_posix_splitting_is_still_shlex(personas, cmd):
    import shlex

    assert personas._credential_cmd_argv(cmd, "p", "token", windows=False) == shlex.split(cmd)


def test_posix_unterminated_quote_message_is_unchanged(personas):
    with pytest.raises(personas.MissingCredential, match=r"cmd parse failure: No closing quotation"):
        personas._credential_cmd_argv('op "item', "p", "token", windows=False)


@pytest.mark.parametrize("argv0", ["op.exe", "OP", "/usr/bin/OP", "/usr/bin/op.exe"])
def test_posix_allowlist_match_is_still_exact(personas, argv0):
    with pytest.raises(personas.MissingCredential, match="not on the credential-helper allowlist"):
        personas._enforce_credential_cmd_policy([argv0, "item"], "p", "token", windows=False)


@pytest.mark.parametrize("argv0", ["op", "/usr/local/bin/op", "/opt/homebrew/bin/vault"])
def test_posix_allowlist_still_accepts_basenames(personas, argv0):
    personas._enforce_credential_cmd_policy([argv0, "item"], "p", "token", windows=False)


def test_posix_bash_exe_is_not_treated_as_a_shell(personas):
    """On POSIX ``bash.exe`` is just an unknown binary: refused by the allowlist gate."""
    with pytest.raises(personas.MissingCredential, match="not on the credential-helper allowlist"):
        personas._enforce_credential_cmd_policy(["bash.exe", "-c", "x"], "p", "token", windows=False)


# --- Windows executable matching ---------------------------------------------


@pytest.mark.parametrize(
    "argv0",
    [
        r"C:\bin\op.exe",
        r"C:\Program Files\1Password CLI\op.exe",
        r"C:\BIN\OP.EXE",
        "op.exe",
        "op",
        "Op.Exe",
        "C:/bin/op.exe",
        r"C:\tools\vault.com",
        # Win32 drops trailing dots and spaces from the last path component.
        r"C:\bin\op.exe.",
    ],
)
def test_windows_allowlist_matches_basename_without_suffix_or_case(personas, argv0):
    personas._enforce_credential_cmd_policy([argv0, "item", "get", "x"], "p", "token", windows=True)


@pytest.mark.parametrize(
    "argv0",
    [
        r"C:\bin\opx.exe",
        r"C:\bin\op.exe.bak",
        r"C:\bin\op.ps1",
        r"C:\bin\op.py",
        r"C:\op\evil.exe",
        # A batch file runs through cmd.exe, which re-reads its arguments with
        # its own metacharacters (``&``, ``|``, ``%``...), so an allowlisted name
        # as .cmd/.bat would let a persona's arguments run anything.
        r"C:\Users\me\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
        r"C:\tools\bw.bat",
        "OP.CMD",
    ],
)
def test_windows_allowlist_still_refuses_other_executables(personas, argv0):
    with pytest.raises(personas.MissingCredential, match="not on the credential-helper allowlist"):
        personas._enforce_credential_cmd_policy([argv0, "item"], "p", "token", windows=True)


@pytest.mark.parametrize("argv0", [r"C:\tools\gcloud.cmd", r"C:\tools\bw.bat"])
def test_windows_batch_helpers_run_with_the_arbitrary_cmd_opt_in(personas, monkeypatch, argv0):
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS", "1")
    personas._enforce_credential_cmd_policy([argv0, "auth", "print-access-token"], "p", "token", windows=True)


@pytest.mark.parametrize(
    "argv0",
    [
        "bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Windows\System32\BASH.EXE",
        "sh.exe",
        r"C:\msys64\usr\bin\zsh.EXE",
        r"C:\bin\bash.exe.",
    ],
)
def test_windows_shell_gate_catches_exe_interpreters(personas, argv0):
    with pytest.raises(personas.MissingCredential, match="with -c which is arbitrary shell execution"):
        personas._enforce_credential_cmd_policy([argv0, "-c", "op read x | head -1"], "p", "token", windows=True)


def test_windows_shell_gate_holds_even_with_arbitrary_opt_in(personas, monkeypatch):
    """The arbitrary-cmd opt-in must not open ``bash.exe -c``: that needs the shell opt-in."""
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS", "1")
    with pytest.raises(personas.MissingCredential, match="with -c which is arbitrary shell execution"):
        personas._enforce_credential_cmd_policy([r"C:\Git\bin\bash.exe", "-c", "x"], "p", "token", windows=True)


@pytest.mark.parametrize(
    "argv",
    [
        ["cmd.exe", "/c", "type secret.txt"],
        [r"C:\Windows\System32\cmd.exe", "/C", "type x"],
        ["powershell", "-Command", "Get-Content x"],
        [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-Command", "x"],
        ["pwsh.exe", "-c", "x"],
    ],
)
def test_windows_native_shells_stay_refused_without_opt_in(personas, argv):
    with pytest.raises(personas.MissingCredential, match="not on the credential-helper allowlist"):
        personas._enforce_credential_cmd_policy(argv, "p", "token", windows=True)


# --- end to end through resolve_credential ------------------------------------


def _write_persona(root: Path, name: str, doc: dict) -> None:
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "profile.yaml").write_text(yaml.safe_dump(doc))


def test_windows_resolve_runs_the_full_backslash_path(tmp_path, personas, monkeypatch):
    captured: dict[str, object] = {}

    def _spy_run(*args, **kwargs):
        captured["argv"] = args[0]
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    import subprocess

    monkeypatch.setattr(subprocess, "run", _spy_run)
    monkeypatch.setattr(personas, "_is_windows", lambda: True)
    _write_persona(
        tmp_path,
        "u",
        {"name": "u", "credentials": {"token_cmd": r'"C:\Program Files\1Password CLI\op.exe" item get x'}},
    )
    p = personas.load_persona("u")
    assert personas.resolve_credential(p, "token") == "ok"
    assert captured["argv"] == [r"C:\Program Files\1Password CLI\op.exe", "item", "get", "x"]


def test_default_seam_follows_sys_platform(personas, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    assert personas._is_windows() is True
    assert personas._credential_cmd_argv(r"C:\bin\op.exe x", "p", "t") == [r"C:\bin\op.exe", "x"]
    monkeypatch.setattr(sys, "platform", "linux")
    assert personas._is_windows() is False
    assert personas._credential_cmd_argv(r"C:\bin\op.exe x", "p", "t") == ["C:binop.exe", "x"]
