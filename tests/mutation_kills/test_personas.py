# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Persona loading, listing, scaffolding and credential resolution, pinned message by message.

Every test works under a temporary ``PROFILES_DIR`` and sets the credential opt-in
variables explicitly; nothing reads the real profile or config directories.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from octowright import personas

SHELL_ENV = "OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS"
ARBITRARY_ENV = "OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS"
ALLOWLIST = sorted(personas._CREDENTIAL_HELPER_ALLOWLIST)

#: A ``bash -c`` cmd needs a POSIX bash; on a Windows runner ``bash`` is the WSL launcher, or absent.
needs_posix_bash = pytest.mark.skipif(sys.platform == "win32", reason="runs a POSIX `bash -c` cmd")
#: The helper is a ``#!/bin/sh`` script made executable with ``chmod``, which Windows cannot run.
needs_sh_script = pytest.mark.skipif(sys.platform == "win32", reason="runs an executable #!/bin/sh helper script")
#: File credentials are refused on Windows by design: there are no POSIX permissions to hold the file to.
needs_file_credentials = pytest.mark.skipif(
    sys.platform == "win32", reason="file credentials are refused on Windows by design"
)


@pytest.fixture(autouse=True)
def profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "profiles"
    monkeypatch.setattr(personas, "PROFILES_DIR", root)
    monkeypatch.delenv(SHELL_ENV, raising=False)
    monkeypatch.delenv(ARBITRARY_ENV, raising=False)
    return root


def _write_persona(root: Path, slug: str, doc: object) -> Path:
    pdir = root / slug
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / "profile.yaml"
    path.write_text(doc if isinstance(doc, str) else yaml.safe_dump(doc), encoding="utf-8")
    return path


def _error(exc_type: type[BaseException], func, *args) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(exc_type) as raised:
        func(*args)
    return str(raised.value)


def _cred(**creds: str) -> personas.Persona:
    return personas.Persona(name="alice", credentials=dict(creds))


# --- YAML validation ------------------------------------------------------------------

VALIDATION_CASES = [
    ([1], "persona YAML must be a mapping at the top level, got list"),
    (
        {"name": "a", "zeta": 1, "alpha": 2},
        "persona YAML has unknown top-level key(s): ['alpha', 'zeta']; allowed keys are "
        "['app', 'credentials', 'default_macros', 'default_url', 'display_name', 'emoji', 'name', 'trusted_roots']",
    ),
    ({"name": 3}, "persona YAML field 'name' must be a string, got int"),
    ({"display_name": 3}, "persona YAML field 'display_name' must be a string or null, got int"),
    ({"default_url": ["x"]}, "persona YAML field 'default_url' must be a string or null, got list"),
    ({"emoji": 1.5}, "persona YAML field 'emoji' must be a string or null, got float"),
    ({"default_macros": "m"}, "persona YAML field 'default_macros' must be a list of strings, got str"),
    ({"default_macros": ["a", 2]}, "persona YAML field 'default_macros[1]' must be a string, got int"),
    ({"credentials": ["x"]}, "persona YAML field 'credentials' must be a mapping, got list"),
    ({"credentials": {1: "x"}}, "persona YAML 'credentials' keys must be strings, got int"),
    (
        {"credentials": {"email": "X"}},
        "persona YAML 'credentials' key 'email' must end with one of ['_env', '_cmd', '_file'] "
        "(e.g. 'email_env' or 'token_cmd')",
    ),
    (
        {"credentials": {"email_env": 5}},
        "persona YAML 'credentials.email_env' must be a string (env var name or cmd), got int",
    ),
    ({"trusted_roots": "ca.pem"}, "persona YAML field 'trusted_roots' must be a list of paths, got str"),
    ({"trusted_roots": ["a.pem", ""]}, "persona YAML field 'trusted_roots[1]' must be a non-empty path string"),
    ({"trusted_roots": [3]}, "persona YAML field 'trusted_roots[0]' must be a non-empty path string"),
    ({"app": [1]}, "persona YAML field 'app' must be a mapping, got list"),
]


@pytest.mark.parametrize(("doc", "message"), VALIDATION_CASES)
def test_an_invalid_persona_document_is_refused_with_its_exact_reason(doc: object, message: str) -> None:
    assert _error(ValueError, personas._validate_persona_yaml_doc, doc) == message


@pytest.mark.parametrize(
    "doc",
    [
        {},
        {"display_name": None, "default_url": None, "emoji": None, "default_macros": None},
        {"credentials": None, "trusted_roots": None, "app": None},
        {
            "name": "a",
            "display_name": "A",
            "default_url": "https://a.test",
            "emoji": "x",
            "default_macros": ["m"],
            "credentials": {"email_env": "E", "token_cmd": "op read x", "key_file": "~/k"},
            "trusted_roots": ["ca.pem"],
            "app": {"k": 1},
        },
    ],
)
def test_a_valid_persona_document_passes(doc: dict[str, object]) -> None:
    personas._validate_persona_yaml_doc(doc)


def test_load_persona_reports_an_invalid_file_by_path(profiles: Path) -> None:
    path = _write_persona(profiles, "bad", {"name": 3})
    assert _error(ValueError, personas.load_persona, "bad") == (
        f"invalid persona file {path}: persona YAML field 'name' must be a string, got int"
    )


def test_load_persona_of_a_missing_persona_says_how_to_make_one(profiles: Path) -> None:
    assert _error(FileNotFoundError, personas.load_persona, "ghost") == (
        f"no persona named 'ghost' at {profiles / 'ghost' / 'profile.yaml'}; list with `persona_list` or "
        "create with `persona_create name='ghost'`"
    )


def test_load_persona_reads_every_field(profiles: Path) -> None:
    _write_persona(
        profiles,
        "alice",
        {
            "name": "Alice",
            "display_name": "Alice A",
            "default_url": "https://a.test",
            "default_macros": ["login"],
            "credentials": {"email_env": "ALICE_EMAIL"},
            "app": {"tenant": "t1"},
            "emoji": "x",
            "trusted_roots": ["ca.pem"],
        },
    )
    assert personas.load_persona("alice") == personas.Persona(
        name="Alice",
        display_name="Alice A",
        default_url="https://a.test",
        default_macros=["login"],
        credentials={"email_env": "ALICE_EMAIL"},
        app={"tenant": "t1"},
        emoji="x",
        trusted_roots=["ca.pem"],
    )


def test_an_empty_persona_file_loads_as_the_slug(profiles: Path) -> None:
    _write_persona(profiles, "bob-b", "")
    assert personas.load_persona("bob b") == personas.Persona(name="bob-b")


def test_slug_refuses_a_name_with_nothing_left() -> None:
    assert _error(ValueError, personas._slug, " .-/ ") == "persona name ' .-/ ' produced an empty slug"
    assert personas._slug(" Ann  Lee. ") == "Ann-Lee"


def test_engine_profile_dir_refuses_an_unknown_kind(profiles: Path) -> None:
    assert personas.engine_profile_dir("a b", "firefox") == profiles / "a-b" / "firefox"
    assert _error(ValueError, personas.engine_profile_dir, "a", "edge") == (
        "kind must be one of ('chromium', 'firefox', 'webkit'), got 'edge'"
    )


# --- listing and scaffolding ----------------------------------------------------------


def test_list_personas_without_a_directory_is_empty() -> None:
    assert personas.list_personas() == []


def test_list_personas_reports_every_persona_with_a_profile_newest_first(profiles: Path) -> None:
    _write_persona(profiles, "old", {"display_name": "Old One"})
    (profiles / "old" / "webkit").mkdir()
    (profiles / "old" / "chromium").mkdir()
    (profiles / "old" / "trust-home").mkdir()
    (profiles / "old" / "firefox").write_text("not a dir", encoding="utf-8")
    _write_persona(profiles, "new", "display_name: [unclosed")
    (profiles / "orphan").mkdir()
    (profiles / "stray.yaml").write_text("x: 1", encoding="utf-8")
    os.utime(profiles / "old", (1_000_000_000, 1_000_000_000))
    os.utime(profiles / "new", (1_700_000_000, 1_700_000_000))
    assert personas.list_personas() == [
        {
            "name": "new",
            "display_name": None,
            "engines": [],
            "path": str(profiles / "new"),
            "mtime": 1_700_000_000.0,
            "last_used": "2023-11-14T22:13:20Z",
        },
        {
            "name": "old",
            "display_name": "Old One",
            "engines": ["chromium", "webkit"],
            "path": str(profiles / "old"),
            "mtime": 1_000_000_000.0,
            "last_used": "2001-09-09T01:46:40Z",
        },
    ]


def test_create_persona_writes_the_minimal_profile(profiles: Path) -> None:
    pdir = personas.create_persona("Ann Lee")
    assert pdir == profiles / "Ann-Lee"
    assert yaml.safe_load((pdir / "profile.yaml").read_text(encoding="utf-8")) == {"name": "Ann-Lee"}


def test_create_persona_writes_the_display_name_and_url_and_refuses_a_second_time(profiles: Path) -> None:
    pdir = personas.create_persona("bo", display_name="Bø", default_url="https://b.test")
    text = (pdir / "profile.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(text) == {"name": "bo", "display_name": "Bø", "default_url": "https://b.test"}
    assert "Bø" in text
    assert _error(FileExistsError, personas.create_persona, "bo") == (
        f"persona 'bo' already has profile.yaml at {pdir / 'profile.yaml'}"
    )


def test_create_persona_ignores_empty_display_name_and_url(profiles: Path) -> None:
    pdir = personas.create_persona("cy", display_name="", default_url="")
    assert yaml.safe_load((pdir / "profile.yaml").read_text(encoding="utf-8")) == {"name": "cy"}


# --- credential commands --------------------------------------------------------------


def test_a_cmd_that_does_not_parse_is_refused() -> None:
    assert _error(personas.MissingCredential, personas.resolve_credential, _cred(token_cmd='op read "x'), "token") == (
        "persona 'alice' field 'token': cmd parse failure: No closing quotation"
    )


@pytest.mark.parametrize(
    ("cmd", "bad"),
    [("op read x | cat", ["|"]), ("op $(whoami)", ["$(whoami)"]), ("op a ; b && c", [";", "&&"])],
)
def test_a_cmd_using_shell_semantics_is_refused(cmd: str, bad: list[str]) -> None:
    assert _error(personas.MissingCredential, personas.resolve_credential, _cred(token_cmd=cmd), "token") == (
        f"persona 'alice' field 'token': cmd uses shell semantics ({bad!r}); wrap explicitly as "
        '`bash -c "..."` if a pipeline is required'
    )


def test_an_empty_cmd_is_refused() -> None:
    assert _error(personas.MissingCredential, personas.resolve_credential, _cred(token_cmd="  "), "token") == (
        "persona 'alice' field 'token': cmd is empty after parsing"
    )


@pytest.mark.parametrize("interpreter", ["bash", "/bin/sh", "/usr/local/bin/zsh", "fish", "dash", "ksh"])
def test_a_shell_form_cmd_is_refused_without_the_opt_in(interpreter: str) -> None:
    persona = _cred(token_cmd=f"{interpreter} -c 'echo hi'")
    assert _error(personas.MissingCredential, personas.resolve_credential, persona, "token") == (
        f"persona 'alice' field 'token': cmd invokes {interpreter!r} with -c which is arbitrary shell "
        "execution. Rewrite as an argv-form helper (e.g. `op read op://…`) or set "
        f"{SHELL_ENV}=1 to opt in."
    )


def _arbitrary_refusal(executable: str) -> str:
    return (
        f"persona 'alice' field 'token': cmd executable {executable!r} is not on the credential-helper "
        f"allowlist ({ALLOWLIST!r}). Use an allowlisted helper, or set {ARBITRARY_ENV}=1 to opt in."
    )


@pytest.mark.parametrize("cmd", ["bash", "bash -x 'echo hi'", "/bin/echo hi", "python -c 1"])
def test_a_cmd_that_is_not_shell_form_and_not_allowlisted_is_refused(cmd: str) -> None:
    assert _error(personas.MissingCredential, personas.resolve_credential, _cred(token_cmd=cmd), "token") == (
        _arbitrary_refusal(cmd.split()[0])
    )


@needs_posix_bash
def test_the_shell_opt_in_runs_a_shell_form_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SHELL_ENV, "1")
    assert personas.resolve_credential(_cred(token_cmd="bash -c 'echo \"  s3cret  \"'"), "token") == "s3cret"


def test_the_shell_opt_in_does_not_admit_an_arbitrary_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SHELL_ENV, "1")
    assert _error(personas.MissingCredential, personas.resolve_credential, _cred(token_cmd="echo hi"), "token") == (
        _arbitrary_refusal("echo")
    )


def test_the_arbitrary_opt_in_runs_a_binary_and_strips_its_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ARBITRARY_ENV, "1")
    assert personas.resolve_credential(_cred(token_cmd="printf '\\n tok \\n'"), "token") == "tok"


def test_the_arbitrary_opt_in_does_not_admit_a_shell_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ARBITRARY_ENV, "1")
    with pytest.raises(personas.MissingCredential, match="with -c which is arbitrary shell execution"):
        personas.resolve_credential(_cred(token_cmd="sh -c 'echo hi'"), "token")


@needs_sh_script
def test_an_allowlisted_helper_runs_without_an_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = tmp_path / "bin" / "op"
    helper.parent.mkdir()
    helper.write_text('#!/bin/sh\necho "from-op $1"\n', encoding="utf-8")
    helper.chmod(0o700)
    assert personas.resolve_credential(_cred(token_cmd=f"{helper} read"), "token") == "from-op read"


def test_a_missing_executable_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ARBITRARY_ENV, "1")
    persona = _cred(token_cmd="octowright-no-such-helper-binary read")
    assert _error(personas.MissingCredential, personas.resolve_credential, persona, "token") == (
        "persona 'alice' field 'token': cmd not found on PATH ('octowright-no-such-helper-binary')"
    )


@needs_posix_bash
def test_a_failing_cmd_reports_its_exit_code_and_never_its_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SHELL_ENV, "1")
    persona = _cred(token_cmd="bash -c 'echo leaked-secret >&2; exit 3'")
    message = _error(personas.MissingCredential, personas.resolve_credential, persona, "token")
    assert message == "persona 'alice' field 'token': cmd exited 3 (stderr suppressed; see debug log for length)"


def test_a_cmd_that_runs_too_long_times_out_after_thirty_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def bounded_run(argv: list[str], **kwargs: object) -> object:
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])  # type: ignore[arg-type]

    monkeypatch.setattr(personas.subprocess, "run", bounded_run)
    monkeypatch.setenv(ARBITRARY_ENV, "1")
    assert _error(personas.MissingCredential, personas.resolve_credential, _cred(token_cmd="sleep 60"), "token") == (
        "persona 'alice' field 'token': cmd timed out after 30s"
    )
    assert seen == {"capture_output": True, "text": True, "check": False, "timeout": 30}


# --- credential files -----------------------------------------------------------------

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")


def _secret(tmp_path: Path, content: bytes, mode: int = 0o600) -> Path:
    path = tmp_path / "secret.txt"
    path.write_bytes(content)
    path.chmod(mode)
    return path


def _file_error(path: Path | str) -> str:
    return _error(personas.MissingCredential, personas.resolve_credential, _cred(key_file=str(path)), "key")


WHERE = "persona 'alice' field 'key'"


@posix_only
def test_a_credential_file_is_read_without_one_trailing_newline(tmp_path: Path) -> None:
    path = _secret(tmp_path, b"s3cret\n\n")
    assert personas.resolve_credential(_cred(key_file=str(path)), "key") == "s3cret\n"


@posix_only
def test_a_credential_file_under_home_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _secret(tmp_path, b"homed")
    assert personas.resolve_credential(_cred(key_file="~/secret.txt"), "key") == "homed"


@posix_only
@pytest.mark.parametrize(
    ("content", "mode", "reason"),
    [
        (b"\n", 0o600, "credential file is empty"),
        (b"", 0o600, "credential file is empty"),
        (b"\xff\xfe", 0o600, "credential file is not valid UTF-8"),
        (b"x", 0o640, "credential file is readable by others; chmod 600"),
        (b"x", 0o604, "credential file is readable by others; chmod 600"),
    ],
)
def test_a_credential_file_that_breaks_a_rule_is_refused_by_the_rule(
    tmp_path: Path, content: bytes, mode: int, reason: str
) -> None:
    assert _file_error(_secret(tmp_path, content, mode)) == f"{WHERE}: {reason}"


@posix_only
def test_a_missing_symlinked_linked_or_special_credential_file_is_refused(tmp_path: Path) -> None:
    assert _file_error(tmp_path / "absent") == f"{WHERE}: credential file not found"
    secret = _secret(tmp_path, b"x")
    link = tmp_path / "link"
    link.symlink_to(secret)
    assert _file_error(link) == f"{WHERE}: credential file is a symlink"
    os.link(secret, tmp_path / "second")
    assert _file_error(secret) == f"{WHERE}: credential file has another hard link"
    assert _file_error(tmp_path) == f"{WHERE}: credential file is not a regular file"
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    assert _file_error(fifo) == f"{WHERE}: credential file is not a regular file"
    assert _file_error(tmp_path / "second" / "below") == f"{WHERE}: credential file cannot be opened"


@posix_only
def test_a_credential_file_owned_by_someone_else_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _secret(tmp_path, b"x")
    owner = path.stat().st_uid
    monkeypatch.setattr(personas.os, "getuid", lambda: owner + 1)
    assert _file_error(path) == f"{WHERE}: credential file is owned by another user"


def test_a_host_without_posix_permissions_refuses_file_credentials(tmp_path: Path) -> None:
    assert personas._posix_file_permissions() is (os.name != "nt")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(personas.os, "name", "nt")
        assert personas._posix_file_permissions() is False
        message = _file_error(tmp_path / "any")
    assert message == (
        f"{WHERE}: file credentials need POSIX file permissions and are not supported on this platform; "
        "use key_env or key_cmd"
    )


# --- resolution and the check report --------------------------------------------------


@needs_file_credentials
def test_resolve_credential_prefers_cmd_then_file_then_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALICE_KEY", "from-env")
    monkeypatch.setenv(ARBITRARY_ENV, "1")
    path = _secret(tmp_path, b"from-file")
    assert personas.resolve_credential(_cred(key_env="ALICE_KEY"), "key") == "from-env"
    assert personas.resolve_credential(_cred(key_env="ALICE_KEY", key_file=str(path)), "key") == "from-file"
    both = _cred(key_env="ALICE_KEY", key_file=str(path), key_cmd="echo from-cmd")
    assert personas.resolve_credential(both, "key") == "from-cmd"
    assert personas.resolve_credential(_cred(key_env="ALICE_KEY", key_cmd="echo c"), "key") == "c"


def test_resolve_credential_names_an_unset_env_var_and_a_missing_reference(
    profiles: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OW_UNSET_VAR", raising=False)
    monkeypatch.setenv("OW_EMPTY_VAR", "")
    assert personas.resolve_credential(_cred(key_env="OW_EMPTY_VAR"), "key") == ""
    assert _error(personas.MissingCredential, personas.resolve_credential, _cred(key_env="OW_UNSET_VAR"), "key") == (
        f"{WHERE}: env var OW_UNSET_VAR is unset"
    )
    assert _error(personas.MissingCredential, personas.resolve_credential, _cred(), "key") == (
        f"{WHERE}: no key_env, key_cmd or key_file in credentials. Add one to "
        f"{profiles / 'alice' / 'profile.yaml'} under `credentials:` "
        "(e.g. key_env: KEY_VAR or key_cmd: 'op read op://…')."
    )


def test_check_credentials_reports_each_name_once_by_its_winning_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALICE_EMAIL", "a@x.test")
    monkeypatch.delenv("ALICE_PIN", raising=False)
    path = _secret(tmp_path, b"k")
    persona = personas.Persona(
        name="alice",
        credentials={
            "email_env": "ALICE_EMAIL",
            "pin_env": "ALICE_PIN",
            "key_file": str(path),
            "key_env": "IGNORED",
            "token_cmd": "nope-binary",
            "token_env": "ALICE_EMAIL",
        },
    )
    key_entry: dict[str, object] = {"name": "key", "source": "file", "reference": str(path), "ok": True, "error": None}
    summary = "2/4 credentials resolved; failing: pin, token"
    if sys.platform == "win32":
        # File credentials are refused there by design, so the file source wins and fails.
        key_entry["ok"] = False
        key_entry["error"] = (
            "persona 'alice' field 'key': file credentials need POSIX file permissions and are not "
            "supported on this platform; use key_env or key_cmd"
        )
        summary = "1/4 credentials resolved; failing: key, pin, token"
    report = personas.check_credentials(persona)
    assert report == {
        "persona": "alice",
        "checked": [
            {"name": "email", "source": "env", "reference": "ALICE_EMAIL", "ok": True, "error": None},
            key_entry,
            {
                "name": "pin",
                "source": "env",
                "reference": "ALICE_PIN",
                "ok": False,
                "error": "persona 'alice' field 'pin': env var ALICE_PIN is unset",
            },
            {
                "name": "token",
                "source": "cmd",
                "reference": "nope-binary",
                "ok": False,
                "error": _arbitrary_refusal("nope-binary"),
            },
        ],
        "ok": False,
        "summary": summary,
    }


def test_check_credentials_of_a_persona_with_none_or_all_resolved_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    assert personas.check_credentials(personas.Persona(name="bare")) == {
        "persona": "bare",
        "checked": [],
        "ok": True,
        "summary": "persona 'bare' declares no credentials",
    }
    monkeypatch.setenv("ALICE_EMAIL", "a@x.test")
    report = personas.check_credentials(_cred(email_env="ALICE_EMAIL"))
    assert report["ok"] is True
    assert report["summary"] == "1/1 credentials resolved"


# --- the audit trail of the trust boundary --------------------------------------------


class LogRecorder:
    """Stands in for the module logger: keeps every call as ``(level, event, fields)``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def __getattr__(self, level: str):  # type: ignore[no-untyped-def]
        return lambda event, **fields: self.calls.append((level, event, fields))


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> LogRecorder:
    recorder = LogRecorder()
    monkeypatch.setattr(personas, "log", recorder)
    return recorder


def _ran(monkeypatch: pytest.MonkeyPatch, stdout: str) -> list[list[str]]:
    """Stand in for the exec, so a test of the policy runs on a host without the binary."""
    argvs: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(personas.subprocess, "run", run)
    return argvs


def test_an_opted_in_shell_cmd_is_audited(monkeypatch: pytest.MonkeyPatch, logged: LogRecorder) -> None:
    monkeypatch.setenv(SHELL_ENV, "1")
    argvs = _ran(monkeypatch, "x\n")
    personas.resolve_credential(_cred(token_cmd="/bin/bash -c 'echo x'"), "token")
    assert logged.calls == [
        (
            "warning",
            "personas.credential_cmd_executes_shell_pipeline",
            {
                "persona": "alice",
                "field": "token",
                "interpreter": "/bin/bash",
                "hint": "treat persona YAML as trusted; bash -c is arbitrary code execution",
            },
        )
    ]
    assert argvs == [["/bin/bash", "-c", "echo x"]]


def test_an_opted_in_arbitrary_binary_is_audited(monkeypatch: pytest.MonkeyPatch, logged: LogRecorder) -> None:
    monkeypatch.setenv(ARBITRARY_ENV, "1")
    personas.resolve_credential(_cred(token_cmd="echo x"), "token")
    assert logged.calls == [
        (
            "warning",
            "personas.credential_cmd_executes_arbitrary_binary",
            {
                "persona": "alice",
                "field": "token",
                "executable": "echo",
                "hint": "treat persona YAML as trusted; arbitrary cred cmd opt-in is enabled",
            },
        )
    ]


def test_an_allowlisted_helper_is_not_audited(monkeypatch: pytest.MonkeyPatch, logged: LogRecorder) -> None:
    argvs = _ran(monkeypatch, "ok\n")
    assert personas.resolve_credential(_cred(token_cmd="/opt/bin/gpg --decrypt"), "token") == "ok"
    assert argvs == [["/opt/bin/gpg", "--decrypt"]]
    assert logged.calls == []


@needs_posix_bash
def test_a_failing_cmd_logs_only_the_length_of_its_stderr(monkeypatch: pytest.MonkeyPatch, logged: LogRecorder) -> None:
    monkeypatch.setenv(SHELL_ENV, "1")
    with pytest.raises(personas.MissingCredential):
        personas.resolve_credential(_cred(token_cmd="bash -c 'printf leaked >&2; exit 4'"), "token")
    assert logged.calls[1:] == [
        (
            "debug",
            "persona.cred.cmd_failed",
            {"persona": "alice", "field": "token", "returncode": 4, "stderr_len": 6},
        )
    ]


def test_a_cmd_shadowing_an_env_reference_is_warned_about(monkeypatch: pytest.MonkeyPatch, logged: LogRecorder) -> None:
    monkeypatch.setenv(ARBITRARY_ENV, "1")
    personas.resolve_credential(_cred(token_cmd="echo x", token_env="T"), "token")
    assert logged.calls[0] == ("warning", "persona.cred.both_set", {"persona": "alice", "cred_name": "token"})
    logged.calls.clear()
    personas.resolve_credential(_cred(token_cmd="echo x"), "token")
    assert [event for _, event, _ in logged.calls] == ["personas.credential_cmd_executes_arbitrary_binary"]


def test_an_unparsable_persona_file_is_reported_while_listing(profiles: Path, logged: LogRecorder) -> None:
    path = _write_persona(profiles, "p", "a: [")
    personas.list_personas()
    assert logged.calls == [("warning", "persona.yaml_parse_failed", {"path": str(path)})]
