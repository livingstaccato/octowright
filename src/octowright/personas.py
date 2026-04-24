from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from provide.telemetry import get_logger

from .defaults import PROFILES_DIR, SUPPORTED_KINDS

log = get_logger(__name__)

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


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


def persona_dir(name: str) -> Path:
    return PROFILES_DIR / _slug(name)


def engine_profile_dir(persona: str, kind: str) -> Path:
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {kind!r}")
    return persona_dir(persona) / kind


def load_persona(name: str) -> Persona:
    p = persona_dir(name) / "profile.yaml"
    if not p.exists():
        raise FileNotFoundError(f"no persona at {p}")
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
    )


def list_personas() -> list[dict[str, Any]]:
    """Return [{name, display_name, engines, path, mtime, last_used}, ...]
    sorted most-recent-mtime first. Empty list if PROFILES_DIR missing."""
    if not PROFILES_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for entry in PROFILES_DIR.iterdir():
        if not entry.is_dir():
            continue
        yaml_path = entry / "profile.yaml"
        display_name = None
        if yaml_path.exists():
            try:
                raw = yaml.safe_load(yaml_path.read_text()) or {}
                display_name = raw.get("display_name")
            except Exception:  # noqa: BLE001 — surface but don't crash listing
                log.warning("persona.yaml_parse_failed", path=str(yaml_path))
        engines = sorted(
            sub.name for sub in entry.iterdir()
            if sub.is_dir() and sub.name in SUPPORTED_KINDS
        )
        stat = entry.stat()
        out.append({
            "name": entry.name,
            "display_name": display_name,
            "engines": engines,
            "path": str(entry),
            "mtime": stat.st_mtime,
            "last_used": datetime.fromtimestamp(stat.st_mtime, UTC)
                .isoformat().replace("+00:00", "Z"),
        })
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return out


def migrate_legacy_layout() -> dict[str, Any]:
    """One-shot migration from profiles/<kind>/<name>/ to profiles/<name>/<kind>/.
    Idempotent. Returns {moved: N, personas: M}."""
    if not PROFILES_DIR.exists():
        return {"moved": 0, "personas": 0}

    moved = 0
    touched_personas: set[str] = set()

    for kind_dir in list(PROFILES_DIR.iterdir()):
        if not kind_dir.is_dir() or kind_dir.name not in SUPPORTED_KINDS:
            continue
        # Every subdir of a kind-dir is a legacy (kind, name) tuple.
        for legacy_engine in list(kind_dir.iterdir()):
            if not legacy_engine.is_dir():
                continue
            name = legacy_engine.name
            new_engine = PROFILES_DIR / name / kind_dir.name
            new_engine.parent.mkdir(parents=True, exist_ok=True)
            if new_engine.exists():
                log.warning("migrate.target_exists_skipping",
                            source=str(legacy_engine), target=str(new_engine))
                continue
            legacy_engine.rename(new_engine)
            moved += 1
            touched_personas.add(name)

        # Remove the now-empty kind directory (if it is empty).
        try:
            kind_dir.rmdir()
        except OSError:
            pass  # non-empty (shouldn't happen) — leave it

    # Create stub profile.yaml for each touched persona if missing.
    for name in touched_personas:
        yaml_path = PROFILES_DIR / name / "profile.yaml"
        if not yaml_path.exists():
            yaml_path.write_text(yaml.safe_dump({"name": name}))

    log.info("personas.migrated", moved=moved, personas=len(touched_personas))
    return {"moved": moved, "personas": len(touched_personas)}


class MissingCredential(RuntimeError):
    pass


def resolve_credential(persona: Persona, cred_name: str) -> str:
    """Resolve a credential like 'email' via _env or _cmd references in
    persona.credentials. *_cmd wins if both are set."""
    creds = persona.credentials
    cmd_key = f"{cred_name}_cmd"
    env_key = f"{cred_name}_env"
    if cmd_key in creds:
        if env_key in creds:
            log.warning("persona.cred.both_set", persona=persona.name, cred_name=cred_name)
        try:
            result = subprocess.run(  # noqa: S602 — shell usage is a documented feature
                creds[cmd_key], shell=True, capture_output=True, text=True,
                check=False, timeout=30,
            )
        except subprocess.TimeoutExpired as e:
            raise MissingCredential(
                f"persona {persona.name!r} field {cred_name!r}: "
                f"cmd timed out after 30s"
            ) from e
        if result.returncode != 0:
            raise MissingCredential(
                f"persona {persona.name!r} field {cred_name!r}: "
                f"cmd exited {result.returncode}; stderr: {result.stderr[:200]}"
            )
        return result.stdout.strip()
    if env_key in creds:
        env_name = creds[env_key]
        value = os.environ.get(env_name)
        if value is None:
            raise MissingCredential(
                f"persona {persona.name!r} field {cred_name!r}: "
                f"env var {env_name} is unset"
            )
        return value
    raise MissingCredential(
        f"persona {persona.name!r} field {cred_name!r}: "
        f"no {cred_name}_env or {cred_name}_cmd in credentials"
    )
