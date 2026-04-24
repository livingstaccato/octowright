from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from provide.telemetry import get_logger

from .defaults import SCENARIOS_DIR, SUPPORTED_KINDS

log = get_logger(__name__)


@dataclass
class Participant:
    persona: str
    kind: str
    role: str
    url: str | None = None
    startup_macros: list[str] | None = None
    viewport_w: int | None = None
    viewport_h: int | None = None
    stabilize: bool | None = None
    record_video: bool | None = None
    trace: bool | None = None


@dataclass
class Scenario:
    name: str
    participants: list[Participant]
    description: str | None = None
    fixtures: dict[str, Any] = field(default_factory=dict)
    teardown_macro: str | None = None
    verify: dict[str, str] = field(default_factory=dict)


def _validate_scenario(s: Scenario) -> None:
    seen: set[tuple[str, str]] = set()
    for p in s.participants:
        if p.kind not in SUPPORTED_KINDS:
            raise ValueError(
                f"scenario {s.name!r}: participant has unsupported kind {p.kind!r}"
            )
        key = (p.persona, p.kind)
        if key in seen:
            raise ValueError(
                f"scenario {s.name!r}: duplicate (persona, kind) pair {key}"
            )
        seen.add(key)


def load_yaml_scenario(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raw = {}
    participants = [
        Participant(
            persona=p["persona"],
            kind=p["kind"],
            role=p.get("role", "participant"),
            url=p.get("url"),
            startup_macros=p.get("startup_macros"),
            viewport_w=p.get("viewport_w"),
            viewport_h=p.get("viewport_h"),
            stabilize=p.get("stabilize"),
            record_video=p.get("record_video"),
            trace=p.get("trace"),
        )
        for p in raw.get("participants", [])
    ]
    teardown_raw = raw.get("teardown") or {}
    scenario = Scenario(
        name=raw.get("name", path.stem),
        participants=participants,
        description=raw.get("description"),
        fixtures=dict(raw.get("fixtures") or {}),
        teardown_macro=(teardown_raw.get("macro") if isinstance(teardown_raw, dict) else None),
        verify=dict(raw.get("verify") or {}),
    )
    _validate_scenario(scenario)
    return scenario


def load_scenario(name: str) -> Scenario:
    yaml_path = SCENARIOS_DIR / f"{name}.yaml"
    py_path = SCENARIOS_DIR / f"{name}.py"
    if py_path.exists():
        raise NotImplementedError(
            "Python scenario loader is added in the next task (C3); "
            "only YAML scenarios are supported in this commit."
        )
    if yaml_path.exists():
        return load_yaml_scenario(yaml_path)
    raise FileNotFoundError(f"no scenario named {name!r} in {SCENARIOS_DIR}")


def list_scenarios() -> list[dict[str, Any]]:
    if not SCENARIOS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in sorted(SCENARIOS_DIR.iterdir()):
        if entry.suffix not in (".yaml", ".py"):
            continue
        name = entry.stem
        if name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "path": str(entry),
            "form": "python" if entry.suffix == ".py" else "yaml",
            "mtime": entry.stat().st_mtime,
        })
    return out
