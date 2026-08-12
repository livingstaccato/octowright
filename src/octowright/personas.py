# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from provide.telemetry import get_logger

from octowright import defaults
from octowright.defaults import PROFILES_DIR, SUPPORTED_KINDS
from octowright.types import CredentialCheckEntry, CredentialCheckReport, PersonaListEntry

log = get_logger(__name__)

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Tokens that shlex.split surfaces as standalone — i.e. the cmd is asking
# for shell semantics (pipes, redirection, subshells, command separators,
# logical chains). Quoted occurrences of the same characters are folded
# into argv tokens by shlex and don't count. Cred cmds may not use these
# directly; if a pipeline is needed, invoke a shell explicitly via
# `bash -c "..."` so the trust boundary is in the cmd author's hands.
_SHELL_OPERATOR_TOKENS = frozenset(
    {"|", "||", "&", "&&", ";", ";;", "<", ">", ">>", "<<", "<<<", "<>", "(", ")", "$(", "`"}
)

# Interpreter basenames that fall under the ``-c`` shell-pipeline default
# deny. argv[0]'s basename is checked so absolute paths
# (``/bin/bash``, ``/usr/local/bin/zsh``) trip the same gate.
_SHELL_INTERPRETER_BASENAMES = frozenset({"bash", "sh", "zsh", "fish", "dash", "ksh"})

# Well-known credential-helper executable basenames that may run without an
# env-var opt-in. argv[0]'s basename is matched, so absolute paths like
# ``/usr/local/bin/op`` or ``/opt/homebrew/bin/op`` still resolve.
#
# Trust model: a same-user attacker who can write to a persona YAML on this
# host can ALREADY exec arbitrary code via the shell-form escape hatch when
# OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS=1, and can write to ~/.bashrc, $PATH dirs,
# etc. The allowlist is NOT a defense against a determined local attacker —
# it's a guardrail against accidental/sloppy YAML (a copy-pasted example, a
# CI-checked-in config that drifts) silently invoking an unexpected binary.
# To run a non-allowlisted cmd, opt in with
# OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS=1.
_CREDENTIAL_HELPER_ALLOWLIST = frozenset(
    {
        "op",  # 1Password CLI
        "vault",  # HashiCorp Vault CLI
        "gpg",  # GnuPG (pass, gpg-agent, etc.)
        "gpg2",
        "pass",  # password-store
        "aws",  # AWS CLI (sts get-session-token, secretsmanager, etc.)
        "gcloud",  # Google Cloud CLI
        "security",  # macOS Keychain CLI
        "secret-tool",  # libsecret (GNOME Keyring / KWallet bridge)
        "bw",  # Bitwarden CLI
        "kwallet-query",  # KDE KWallet CLI
    }
)


def _credential_cmd_argv(cmd: str, persona_name: str, cred_name: str) -> list[str]:
    """Parse `cmd` into argv form; raise MissingCredential if it cannot be
    safely represented without invoking /bin/sh.

    A persona YAML can come from any source the caller has configured, so
    we never invoke a shell on its behalf. `bash -c "..."` is the explicit
    escape hatch for pipeline-style credential helpers — the bash invocation
    is itself a normal argv token, and the pipeline lives inside its `-c`
    argument where the cmd author has signed off on it.
    """
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        raise MissingCredential(f"persona {persona_name!r} field {cred_name!r}: cmd parse failure: {exc}") from exc
    bad = [tok for tok in argv if tok in _SHELL_OPERATOR_TOKENS or tok.startswith("$(")]
    if bad:
        raise MissingCredential(
            f"persona {persona_name!r} field {cred_name!r}: cmd uses shell semantics "
            f'({bad!r}); wrap explicitly as `bash -c "..."` if a pipeline is required'
        )
    if not argv:
        raise MissingCredential(f"persona {persona_name!r} field {cred_name!r}: cmd is empty after parsing")
    return argv


def _slug(name: str) -> str:
    cleaned = _SLUG_RE.sub("-", name.strip()).strip("-.")
    if not cleaned:
        raise ValueError(f"persona name {name!r} produced an empty slug")
    return cleaned


@dataclass
class Persona:
    name: str
    display_name: str | None = None
    default_url: str | None = None
    default_macros: list[str] = field(default_factory=list)
    credentials: dict[str, str] = field(default_factory=dict)
    app: dict[str, Any] = field(default_factory=dict)
    # Optional override for the auto-picked persona emoji shown in window
    # title prefix and corner badge. When None, the launcher hash-picks from
    # a curated pool keyed off the persona name (deterministic).
    emoji: str | None = None


# Allowed top-level keys in a persona YAML document. Mirrors the Persona
# dataclass fields. Update both together.
_PERSONA_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "display_name",
        "default_url",
        "default_macros",
        "credentials",
        "app",
        "emoji",
    }
)

# Suffixes valid on credential keys. resolve_credential() only consults
# ``<name>_env`` / ``<name>_cmd`` pairs, so any other suffix is a typo or
# spec drift that should fail loudly rather than silently no-op.
_CREDENTIAL_KEY_SUFFIXES: tuple[str, ...] = ("_env", "_cmd")


def _validate_scalar_str_fields(doc: dict[str, Any]) -> None:
    if "name" in doc and not isinstance(doc["name"], str):
        raise ValueError(f"persona YAML field 'name' must be a string, got {type(doc['name']).__name__}")
    for str_field in ("display_name", "default_url", "emoji"):
        v = doc.get(str_field)
        if str_field in doc and v is not None and not isinstance(v, str):
            raise ValueError(f"persona YAML field {str_field!r} must be a string or null, got {type(v).__name__}")


def _validate_default_macros(doc: dict[str, Any]) -> None:
    macros = doc.get("default_macros")
    if "default_macros" not in doc or macros is None:
        return
    if not isinstance(macros, list):
        raise ValueError(f"persona YAML field 'default_macros' must be a list of strings, got {type(macros).__name__}")
    for i, item in enumerate(macros):
        if not isinstance(item, str):
            raise ValueError(f"persona YAML field 'default_macros[{i}]' must be a string, got {type(item).__name__}")


def _validate_credentials(doc: dict[str, Any]) -> None:
    creds = doc.get("credentials")
    if "credentials" not in doc or creds is None:
        return
    if not isinstance(creds, dict):
        raise ValueError(f"persona YAML field 'credentials' must be a mapping, got {type(creds).__name__}")
    for key, value in creds.items():
        if not isinstance(key, str):
            raise ValueError(f"persona YAML 'credentials' keys must be strings, got {type(key).__name__}")
        if not key.endswith(_CREDENTIAL_KEY_SUFFIXES):
            raise ValueError(
                f"persona YAML 'credentials' key {key!r} must end with one of "
                f"{list(_CREDENTIAL_KEY_SUFFIXES)!r} (e.g. 'email_env' or 'token_cmd')"
            )
        if not isinstance(value, str):
            raise ValueError(
                f"persona YAML 'credentials.{key}' must be a string (env var name or cmd), got {type(value).__name__}"
            )


def _validate_persona_yaml_doc(doc: Any) -> None:
    """Validate a parsed persona YAML document against the Persona schema.

    Raises ``ValueError`` for: non-dict top-level, unknown top-level keys,
    type mismatches against the dataclass fields, and malformed
    ``credentials`` shapes (must be a flat ``{name_env|name_cmd: str}`` map).

    The validator only inspects shape — it does NOT check that env vars or
    cmds resolve, or that referenced macros exist. Those checks belong to
    runtime (``resolve_credential``, macro execution) which already raises
    informative errors.
    """
    if not isinstance(doc, dict):
        raise ValueError(f"persona YAML must be a mapping at the top level, got {type(doc).__name__}")
    unknown = sorted(set(doc) - _PERSONA_ALLOWED_KEYS)
    if unknown:
        raise ValueError(
            f"persona YAML has unknown top-level key(s): {unknown!r}; "
            f"allowed keys are {sorted(_PERSONA_ALLOWED_KEYS)!r}"
        )
    _validate_scalar_str_fields(doc)
    _validate_default_macros(doc)
    _validate_credentials(doc)
    if "app" in doc and doc["app"] is not None and not isinstance(doc["app"], dict):
        raise ValueError(f"persona YAML field 'app' must be a mapping, got {type(doc['app']).__name__}")


def persona_dir(name: str) -> Path:
    return PROFILES_DIR / _slug(name)


def engine_profile_dir(persona: str, kind: str) -> Path:
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {kind!r}")
    return persona_dir(persona) / kind


def load_persona(name: str) -> Persona:
    p = persona_dir(name) / "profile.yaml"
    if not p.exists():
        raise FileNotFoundError(
            f"no persona named {name!r} at {p}; list with `persona_list` or create with `persona_create name={name!r}`"
        )
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    try:
        _validate_persona_yaml_doc(raw)
    except ValueError as exc:
        raise ValueError(f"invalid persona file {p}: {exc}") from exc
    return Persona(
        name=raw.get("name", _slug(name)),
        display_name=raw.get("display_name"),
        default_url=raw.get("default_url"),
        default_macros=list(raw.get("default_macros") or []),
        credentials=dict(raw.get("credentials") or {}),
        app=dict(raw.get("app") or {}),
        emoji=raw.get("emoji"),
    )


def list_personas() -> list[PersonaListEntry]:
    """Return [{name, display_name, engines, path, mtime, last_used}, ...]
    sorted with pinned first-party personas first, then most-recent-mtime.
    Empty list if PROFILES_DIR missing.

    Only directories with a ``profile.yaml`` are reported — orphan profile
    folders left behind by tests or interrupted launches are skipped, since
    every consumer (dashboard editor, resolve.suggest_for_url) needs the
    yaml to function.
    """
    if not PROFILES_DIR.exists():
        return []
    out: list[PersonaListEntry] = []
    for entry in PROFILES_DIR.iterdir():
        if not entry.is_dir():
            continue
        yaml_path = entry / "profile.yaml"
        if not yaml_path.exists():
            continue
        display_name = None
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            display_name = raw.get("display_name")
        except Exception:
            log.warning("persona.yaml_parse_failed", path=str(yaml_path))
        engines = sorted(sub.name for sub in entry.iterdir() if sub.is_dir() and sub.name in SUPPORTED_KINDS)
        stat = entry.stat()
        out.append(
            {
                "name": entry.name,
                "display_name": display_name,
                "engines": engines,
                "path": str(entry),
                "mtime": stat.st_mtime,
                "last_used": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat().replace("+00:00", "Z"),
            }
        )
    out.sort(key=lambda p: -float(p["mtime"]))
    return out


def create_persona(
    name: str,
    *,
    display_name: str | None = None,
    default_url: str | None = None,
) -> Path:
    """Scaffold a new persona directory + stub profile.yaml.
    Raises FileExistsError if profile.yaml already exists.
    Returns the persona directory path.
    """
    pdir = persona_dir(name)
    pdir.mkdir(parents=True, exist_ok=True)
    yaml_path = pdir / "profile.yaml"
    if yaml_path.exists():
        raise FileExistsError(f"persona {name!r} already has profile.yaml at {yaml_path}")
    doc: dict[str, Any] = {"name": _slug(name)}
    if display_name:
        doc["display_name"] = display_name
    if default_url:
        doc["default_url"] = default_url
    # Validate so future changes to the scaffold can't drift away from the
    # schema unnoticed. Today this is a no-op for the constructed doc.
    _validate_persona_yaml_doc(doc)
    yaml_path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return pdir


class MissingCredential(RuntimeError):
    pass


def _enforce_credential_cmd_policy(argv: list[str], persona_name: str, cred_name: str) -> None:
    """Apply the trust-boundary gates that must pass before we exec ``argv``.

    Two gates, mutually exclusive on the argv shape:

    1. **Shell form** (``bash -c "..."`` / ``zsh -c ...``): arbitrary code
       execution from persona YAML. Default-deny unless
       ``OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS=1``. Match the interpreter by
       basename so absolute paths trip the same gate.

    2. **Argv form**: gate any executable that isn't on the well-known
       credential-helper allowlist. ``OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS=1``
       is the documented escape hatch.

    Both opt-in paths emit a structured ``log.warning`` at the call site so
    the trust boundary stays audit-able.
    """
    interpreter_name = Path(argv[0]).name if argv else ""
    is_shell_form = interpreter_name in _SHELL_INTERPRETER_BASENAMES and len(argv) >= 2 and argv[1] == "-c"
    if is_shell_form:
        if not defaults.allow_shell_cred_cmds():
            raise MissingCredential(
                f"persona {persona_name!r} field {cred_name!r}: cmd invokes "
                f"{argv[0]!r} with -c which is arbitrary shell execution. "
                f"Rewrite as an argv-form helper (e.g. `op read op://…`) or "
                f"set {defaults.ALLOW_SHELL_CRED_CMDS_ENV}=1 to opt in."
            )
        log.warning(
            "personas.credential_cmd_executes_shell_pipeline",
            persona=persona_name,
            field=cred_name,
            interpreter=argv[0],
            hint="treat persona YAML as trusted; bash -c is arbitrary code execution",
        )
        return
    if interpreter_name in _CREDENTIAL_HELPER_ALLOWLIST:
        return
    if not defaults.allow_arbitrary_cred_cmds():
        raise MissingCredential(
            f"persona {persona_name!r} field {cred_name!r}: cmd "
            f"executable {argv[0]!r} is not on the credential-helper "
            f"allowlist ({sorted(_CREDENTIAL_HELPER_ALLOWLIST)!r}). "
            f"Use an allowlisted helper, or set "
            f"{defaults.ALLOW_ARBITRARY_CRED_CMDS_ENV}=1 to opt in."
        )
    log.warning(
        "personas.credential_cmd_executes_arbitrary_binary",
        persona=persona_name,
        field=cred_name,
        executable=argv[0],
        hint="treat persona YAML as trusted; arbitrary cred cmd opt-in is enabled",
    )


def _exec_credential_cmd(cmd_str: str, persona_name: str, cred_name: str) -> str:
    """Execute a credential cmd and return stdout.

    Cmds always run in argv form (shell=False). For pipelines, write the
    cmd as `bash -c "..."` — bash is then a normal argv token whose -c
    argument carries the shell logic the cmd author has signed off on.
    """
    argv = _credential_cmd_argv(cmd_str, persona_name, cred_name)
    _enforce_credential_cmd_policy(argv, persona_name, cred_name)
    try:
        # List-arg form (no shell). argv is validated above; persona YAML is trusted.
        result = subprocess.run(  # nosec B603
            argv, capture_output=True, text=True, check=False, timeout=30
        )
    except FileNotFoundError as e:
        raise MissingCredential(
            f"persona {persona_name!r} field {cred_name!r}: cmd not found on PATH ({e.filename!r})"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise MissingCredential(f"persona {persona_name!r} field {cred_name!r}: cmd timed out after 30s") from e
    if result.returncode != 0:
        # Do NOT surface stderr content to the caller: a credential helper can
        # print a secret there, and this message flows into structured MCP
        # errors or logs. Credential helpers are allowed to emit secret values
        # on stderr, so even DEBUG telemetry records only its length.
        log.debug(
            "persona.cred.cmd_failed",
            persona=persona_name,
            field=cred_name,
            returncode=result.returncode,
            stderr_len=len(result.stderr),
        )
        raise MissingCredential(
            f"persona {persona_name!r} field {cred_name!r}: cmd exited {result.returncode} "
            "(stderr suppressed; see debug log for length)"
        )
    return result.stdout.strip()


def resolve_credential(persona: Persona, cred_name: str) -> str:
    """Resolve a credential like 'email' via _env or _cmd references in
    persona.credentials. *_cmd wins if both are set."""
    creds = persona.credentials
    cmd_key = f"{cred_name}_cmd"
    env_key = f"{cred_name}_env"
    if cmd_key in creds:
        if env_key in creds:
            log.warning("persona.cred.both_set", persona=persona.name, cred_name=cred_name)
        return _exec_credential_cmd(creds[cmd_key], persona.name, cred_name)
    if env_key in creds:
        env_name = creds[env_key]
        value = os.environ.get(env_name)
        if value is None:
            raise MissingCredential(f"persona {persona.name!r} field {cred_name!r}: env var {env_name} is unset")
        return value
    raise MissingCredential(
        f"persona {persona.name!r} field {cred_name!r}: no {cred_name}_env or {cred_name}_cmd in credentials. "
        f"Add one to {persona_dir(persona.name) / 'profile.yaml'} under `credentials:` "
        f"(e.g. {cred_name}_env: {cred_name.upper()}_VAR or {cred_name}_cmd: 'op read op://…')."
    )


def _credential_names(persona: Persona) -> list[str]:
    """Return the sorted list of credential names declared by a persona.

    A name is any ``<name>_env`` or ``<name>_cmd`` key in credentials — the
    same inference `resolve_credential` uses. Duplicates (both forms present
    for one name) collapse to a single entry.
    """
    names: set[str] = set()
    for key in persona.credentials:
        if key.endswith("_env"):
            names.add(key[: -len("_env")])
        elif key.endswith("_cmd"):
            names.add(key[: -len("_cmd")])
    return sorted(names)


def check_credentials(persona: Persona) -> CredentialCheckReport:
    """Try to resolve every declared credential reference WITHOUT raising.

    Returns a structured report per field — success/failure + the reference
    type (env or cmd) and its literal reference (env var name or the shell
    command). Never includes the resolved secret value.

    Shape:
        {
          "persona": str,
          "checked": [
              {"name": str, "source": "env"|"cmd", "reference": str, "ok": bool,
               "error": str | None},
              ...
          ],
          "ok": bool,        # True iff every field resolved
          "summary": str,    # "N/M credentials resolved; ..."
        }

    Fields with both ``_env`` and ``_cmd`` are checked as ``cmd`` only (matching
    ``resolve_credential`` precedence) — the ``_env`` value is ignored.
    """
    names = _credential_names(persona)
    checked: list[CredentialCheckEntry] = []
    for name in names:
        cmd_key = f"{name}_cmd"
        env_key = f"{name}_env"
        if cmd_key in persona.credentials:
            source = "cmd"
            reference = persona.credentials[cmd_key]
        else:
            source = "env"
            reference = persona.credentials[env_key]
        try:
            resolve_credential(persona, name)
            checked.append({"name": name, "source": source, "reference": reference, "ok": True, "error": None})
        except MissingCredential as e:
            checked.append({"name": name, "source": source, "reference": reference, "ok": False, "error": str(e)})

    total = len(checked)
    passed = sum(1 for c in checked if c["ok"])
    if total == 0:
        summary = f"persona {persona.name!r} declares no credentials"
    else:
        failing = [c["name"] for c in checked if not c["ok"]]
        if not failing:
            summary = f"{passed}/{total} credentials resolved"
        else:
            summary = f"{passed}/{total} credentials resolved; failing: {', '.join(failing)}"
    return {
        "persona": persona.name,
        "checked": checked,
        # A persona with zero declared credentials is not broken — it just
        # has nothing to verify. Treat that as ok so callers can use the flag
        # as "no missing creds" rather than "creds exist AND resolve".
        "ok": total == 0 or passed == total,
        "summary": summary,
    }
