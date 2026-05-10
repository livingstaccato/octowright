# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from provide.telemetry import get_logger

from octowright.defaults import SCENARIO_TEMPLATES_DIR, SCENARIOS_DIR, SUPPORTED_KINDS
from octowright.scenarios_pool import LiveScenario, ScenarioPool

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
            raise ValueError(f"scenario {s.name!r}: participant has unsupported kind {p.kind!r}")
        key = (p.persona, p.kind)
        if key in seen:
            raise ValueError(f"scenario {s.name!r}: duplicate (persona, kind) pair {key}")
        seen.add(key)


def load_yaml_scenario(content: str, name: str) -> Scenario:
    raw = yaml.safe_load(content)
    if not isinstance(raw, dict):
        # Scenario YAML must be a mapping; a list or scalar at top level is
        # almost certainly a hand-edit mistake. Reset to {} so the caller
        # gets a "no participants" error rather than an AttributeError, but
        # warn so the operator sees what actually happened.
        log.warning(
            "scenarios.yaml_not_mapping",
            name=name,
            got=type(raw).__name__,
        )
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
        name=raw.get("name", name),
        participants=participants,
        description=raw.get("description"),
        fixtures=dict(raw.get("fixtures") or {}),
        teardown_macro=(teardown_raw.get("macro") if isinstance(teardown_raw, dict) else None),
        verify=dict(raw.get("verify") or {}),
    )
    _validate_scenario(scenario)
    return scenario


def load_python_scenario(path: Path) -> Scenario:
    import importlib.util
    import sys

    # `*.py` scenarios execute arbitrary Python at module import — anything
    # at top level runs with the daemon's privileges. Treat the scenarios
    # dir like trusted local config (your own files, not random downloads
    # or shared-repo contributions). The warning makes the trust boundary
    # explicit at runtime so an operator who didn't realize the scenarios
    # dir was on shared storage notices.
    log.warning(
        "scenarios.python_load_executes_arbitrary_code",
        path=str(path),
        hint="treat scenarios dir as trusted local config",
    )
    spec = importlib.util.spec_from_file_location(
        f"octowright._scenario_{path.stem}",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load Python scenario from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    if not hasattr(mod, "build"):
        raise RuntimeError(f"Python scenario {path} must define a top-level build() -> Scenario")
    s = mod.build()
    if not isinstance(s, Scenario):
        raise TypeError(f"{path}:build() returned {type(s).__name__}, expected Scenario")
    _validate_scenario(s)
    return s


def load_scenario(name: str) -> Scenario:
    yaml_path = SCENARIOS_DIR / f"{name}.yaml"
    py_path = SCENARIOS_DIR / f"{name}.py"
    if py_path.exists():
        if yaml_path.exists():
            log.warning("scenarios.both_forms_present_py_wins", name=name)
        return load_python_scenario(py_path)
    if yaml_path.exists():
        return load_yaml_scenario(yaml_path.read_text(encoding="utf-8"), name)
    raise FileNotFoundError(
        f"no scenario named {name!r} in {SCENARIOS_DIR}; list available with `scenario_list` "
        f"or drop a {name}.yaml file in that directory"
    )


def load_scenario_template(name: str, args: dict[str, Any]) -> Scenario:
    path = SCENARIO_TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no scenario template named {name!r} in {SCENARIO_TEMPLATES_DIR}")
    content = path.read_text(encoding="utf-8")
    # Simple jinja-style substitution if args are provided.
    for k, v in args.items():
        content = content.replace(f"{{{{{k}}}}}", str(v))
    return load_yaml_scenario(content, name)


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
        out.append(
            {
                "name": name,
                "path": str(entry),
                "form": "python" if entry.suffix == ".py" else "yaml",
                "mtime": entry.stat().st_mtime,
            }
        )
    return out


def resolve_launch_kwargs(p: Participant) -> dict[str, Any]:
    """Return kwargs suitable for pool.launch(**kwargs) from a Participant,
    applying the participant override → persona default → fallback resolution
    order for each field."""
    from octowright import personas as _p

    try:
        persona = _p.load_persona(p.persona)
    except FileNotFoundError:
        persona = None

    def _from_persona(attr: str, default: Any = None) -> Any:
        if persona is None:
            return default
        return getattr(persona, attr, None) or default

    return {
        "kind": p.kind,
        "profile": p.persona,
        "url": p.url if p.url is not None else _from_persona("default_url"),
        "label": None,
        "viewport_w": p.viewport_w,
        "viewport_h": p.viewport_h,
        "stabilize": p.stabilize if p.stabilize is not None else False,
        "record_video": p.record_video if p.record_video is not None else False,
        "trace": p.trace if p.trace is not None else False,
    }


def resolve_startup_macros(p: Participant) -> list[str]:
    """participant override → persona default_macros → []."""
    from octowright import personas as _p

    if p.startup_macros is not None:
        return list(p.startup_macros)
    try:
        persona = _p.load_persona(p.persona)
    except FileNotFoundError:
        return []
    return list(persona.default_macros or [])


# Keep runtime/pool classes in this module's API surface.
_SCENARIO_RUNTIME_EXPORTS = (LiveScenario, ScenarioPool)
