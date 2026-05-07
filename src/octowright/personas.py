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

from octowright.defaults import PROFILES_DIR, SUPPORTED_KINDS

log = get_logger(__name__)

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Tokens that shlex.split surfaces as standalone — i.e. the user is asking
# for shell semantics (pipes, redirection, subshells, command separators,
# logical chains). Quoted occurrences of the same characters are folded
# into argv tokens by shlex and don't count.
_SHELL_OPERATOR_TOKENS = frozenset(
    {
        "|",
        "||",
        "&",
        "&&",
        ";",
        ";;",
        "<",
        ">",
        ">>",
        "<<",
        "<<<",
        "<>",
        "(",
        ")",
        "$(",
        "`",
    }
)

# Opt-in env gate for shell-form credential commands. Without this set,
# credential cmds are parsed with shlex.split and run with shell=False —
# safe for the common case (`op read op://...`, `pass show foo/bar`).
# Setting OCTOWRIGHT_ALLOW_CRED_SHELL=1 re-enables the legacy shell=True
# path for users who actually need pipelines, but flags the trust boundary
# they're crossing in audit logs.
_ALLOW_CRED_SHELL_ENV = "OCTOWRIGHT_ALLOW_CRED_SHELL"


def _credential_cmd_needs_shell(cmd: str) -> tuple[bool, list[str]]:
    """Try to parse `cmd` as a list-form invocation.

    Returns (needs_shell, argv_or_offending_token_list). If shlex.split
    fails or any resulting token is a shell-only operator, needs_shell is
    True and the second element lists the offending token(s); the caller
    should refuse to exec without the shell-allow opt-in.
    """
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        # Unbalanced quotes / unfinished escape — can't safely list-form.
        return True, [str(exc)]
    bad = [tok for tok in argv if tok in _SHELL_OPERATOR_TOKENS or tok.startswith("$(")]
    if bad:
        return True, bad
    return False, argv


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
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict):
        raw = {}
    return Persona(
        name=raw.get("name", _slug(name)),
        display_name=raw.get("display_name"),
        default_url=raw.get("default_url"),
        default_macros=list(raw.get("default_macros") or []),
        credentials=dict(raw.get("credentials") or {}),
        app=dict(raw.get("app") or {}),
        emoji=raw.get("emoji"),
    )


def list_personas() -> list[dict[str, Any]]:
    """Return [{name, display_name, engines, path, mtime, last_used}, ...]
    sorted most-recent-mtime first. Empty list if PROFILES_DIR missing.

    Only directories with a ``profile.yaml`` are reported — orphan profile
    folders left behind by tests or interrupted launches are skipped, since
    every consumer (dashboard editor, resolve.suggest_for_url) needs the
    yaml to function.
    """
    if not PROFILES_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for entry in PROFILES_DIR.iterdir():
        if not entry.is_dir():
            continue
        yaml_path = entry / "profile.yaml"
        if not yaml_path.exists():
            continue
        display_name = None
        try:
            raw = yaml.safe_load(yaml_path.read_text()) or {}
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
    out.sort(key=lambda p: p["mtime"], reverse=True)
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
    yaml_path.write_text(yaml.safe_dump(doc))
    return pdir


class MissingCredential(RuntimeError):
    pass


def _exec_credential_cmd(cmd_str: str, persona_name: str, cred_name: str) -> str:
    """Execute a credential cmd and return stdout. Default-deny shell mode.

    Refuses commands containing shell operator tokens unless
    OCTOWRIGHT_ALLOW_CRED_SHELL=1, since persona YAML may come from
    untrusted sources (downloaded packs, shared scenarios). The list-form
    (shell=False) path covers `op read op://...`, `pass show foo/bar`,
    `bw get password ...` and similar.
    """
    needs_shell, parsed = _credential_cmd_needs_shell(cmd_str)
    shell_allowed = os.environ.get(_ALLOW_CRED_SHELL_ENV, "").lower() in ("1", "true", "yes")

    if needs_shell and not shell_allowed:
        raise MissingCredential(
            f"persona {persona_name!r} field {cred_name!r}: cmd uses shell semantics "
            f"({parsed!r}). Refusing to invoke /bin/sh by default. "
            f"Set {_ALLOW_CRED_SHELL_ENV}=1 to opt in, or rewrite the cmd as a list-form invocation."
        )

    try:
        if needs_shell:
            log.warning(
                "persona.cred.shell_invoked",
                persona=persona_name,
                cred_name=cred_name,
                note="OCTOWRIGHT_ALLOW_CRED_SHELL=1 — running cmd through /bin/sh",
            )
            result = subprocess.run(  # nosec B602 — opt-in via env var
                cmd_str, capture_output=True, text=True, check=False, shell=True, timeout=30
            )
        else:
            argv = parsed  # already a validated argv from _credential_cmd_needs_shell
            if not argv:
                raise MissingCredential(f"persona {persona_name!r} field {cred_name!r}: cmd is empty after parsing")
            result = subprocess.run(  # nosec B603 B607 — list-arg form, PATH-resolved
                argv, capture_output=True, text=True, check=False, timeout=30
            )
    except FileNotFoundError as e:
        raise MissingCredential(
            f"persona {persona_name!r} field {cred_name!r}: cmd not found on PATH ({e.filename!r})"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise MissingCredential(f"persona {persona_name!r} field {cred_name!r}: cmd timed out after 30s") from e
    if result.returncode != 0:
        raise MissingCredential(
            f"persona {persona_name!r} field {cred_name!r}: "
            f"cmd exited {result.returncode}; stderr: {result.stderr[:200]}"
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


def check_credentials(persona: Persona) -> dict[str, Any]:
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
    checked: list[dict[str, Any]] = []
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
        "ok": passed == total and total > 0,
        "summary": summary,
    }
