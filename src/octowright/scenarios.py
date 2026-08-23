# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from provide.telemetry import get_logger

from octowright import defaults
from octowright._paths import reject_unsafe_path
from octowright.defaults import (
    SCENARIO_TEMPLATES_DIR,
    SCENARIOS_DIR,
    SUPPORTED_KINDS,
    SUPPORTED_TERMINAL_KINDS,
)

# ``LiveScenario`` and ``ScenarioPool`` are the runtime/registry classes —
# their canonical home is ``octowright.scenarios_pool``. They are NOT re-
# exported here so there's exactly one stable import path; this module is
# the static data model (Scenario / Participant / loaders / resolvers).

log = get_logger(__name__)

# Roles consumed by ``ScenarioPool.run_macro(role=...)`` — a typo here
# silently fans out to zero targets, so warn on unknown roles at load time.
# Custom roles (recorder, replayer, main-site, form, …) are legitimate;
# the warning catches actual typos without breaking domain-specific role
# vocabularies used by existing scenarios and demo bundles.
_KNOWN_ROLES = frozenset({"player", "monitor", "spectator"})
_FIXTURE_KEYS = frozenset({"dialog_policy", "mock_routes"})
_DIALOG_POLICIES = frozenset({"accept", "dismiss", "manual"})


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
    # Kind-specific settings, passed through opaquely and validated by whichever
    # kind owns the participant. This replaced ten terminal-only fields that a
    # browser participant could never use and that no plugin could extend
    # without a core change -- the dataclass is public plugin API, so every
    # field on it is a compatibility commitment.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    participants: list[Participant]
    description: str | None = None
    fixtures: dict[str, Any] = field(default_factory=dict)
    teardown_macro: str | None = None
    verify: dict[str, str] = field(default_factory=dict)


def _validate_participant_kind(s: Scenario, p: Participant) -> None:
    """Validate a participant's kind. Terminal participants (kind == "terminal")
    carry a connector_type (pty/ssh, default pty) and cannot use browser macros;
    browser participants must name a supported engine. SUPPORTED_KINDS stays
    browser-only so this never widens browser_launch / session_launch validation.
    """
    if p.kind == "terminal":
        connector_type = p.options.get("connector_type") or "pty"
        if connector_type not in SUPPORTED_TERMINAL_KINDS:
            raise ValueError(
                f"scenario {s.name!r}: terminal participant has unsupported connector_type {connector_type!r} "
                f"(expected one of {list(SUPPORTED_TERMINAL_KINDS)})"
            )
        if p.startup_macros:
            raise ValueError(
                f"scenario {s.name!r}: terminal participant {p.persona!r} cannot declare startup_macros "
                "(Playwright macros don't apply to terminals)"
            )
    elif p.kind not in SUPPORTED_KINDS:
        raise ValueError(f"scenario {s.name!r}: participant has unsupported kind {p.kind!r}")


def _validate_scenario(s: Scenario) -> None:
    s.fixtures = _validate_fixtures(s.fixtures, scenario_name=s.name)
    seen: set[tuple[str, str]] = set()
    for p in s.participants:
        _validate_participant_kind(s, p)
        if p.role not in _KNOWN_ROLES:
            log.warning(
                "scenario.unknown_role",
                scenario=s.name,
                persona=p.persona,
                role=p.role,
                known=sorted(_KNOWN_ROLES),
            )
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
        _load_yaml_participant(p, index=i, scenario_name=name) for i, p in enumerate(raw.get("participants", []))
    ]
    teardown_raw = raw.get("teardown") or {}
    scenario = Scenario(
        name=raw.get("name", name),
        participants=participants,
        description=raw.get("description"),
        fixtures=_validate_fixtures(raw.get("fixtures"), scenario_name=name),
        teardown_macro=(teardown_raw.get("macro") if isinstance(teardown_raw, dict) else None),
        verify=dict(raw.get("verify") or {}),
    )
    _validate_scenario(scenario)
    return scenario


def _load_yaml_participant(raw: Any, *, index: int, scenario_name: str) -> Participant:
    if not isinstance(raw, dict):
        raise ValueError(
            f"scenario {scenario_name!r}: participants[{index}] must be a mapping, got {type(raw).__name__}"
        )
    _validate_required_participant_strings(raw, index=index, scenario_name=scenario_name)
    startup_macros = _validate_startup_macros(raw.get("startup_macros"), index=index, scenario_name=scenario_name)
    _validate_optional_ints(raw, ("viewport_w", "viewport_h"), index=index, scenario_name=scenario_name)
    _validate_optional_bools(
        raw,
        ("stabilize", "record_video", "trace"),
        index=index,
        scenario_name=scenario_name,
    )
    raw_options = raw.get("options")
    if raw_options is None:
        options: dict[str, Any] = {}
    elif isinstance(raw_options, dict):
        options = dict(raw_options)
    else:
        raise ValueError(f"scenario participant {raw.get('persona')!r}: options must be a mapping")
    return Participant(
        persona=raw["persona"],
        kind=raw["kind"],
        role=raw.get("role", "player"),
        url=raw.get("url"),
        startup_macros=startup_macros,
        viewport_w=raw.get("viewport_w"),
        viewport_h=raw.get("viewport_h"),
        stabilize=raw.get("stabilize"),
        record_video=raw.get("record_video"),
        trace=raw.get("trace"),
        options=options,
    )


def _validate_required_participant_strings(raw: dict[str, Any], *, index: int, scenario_name: str) -> None:
    for field_name in ("persona", "kind"):
        if not isinstance(raw.get(field_name), str) or not raw[field_name]:
            raise ValueError(f"scenario {scenario_name!r}: participants[{index}] missing required {field_name!r}")


def _validate_fixtures(value: Any, *, scenario_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"scenario {scenario_name!r}: fixtures must be a mapping")
    unknown = sorted(set(value) - _FIXTURE_KEYS)
    if unknown:
        raise ValueError(f"scenario {scenario_name!r}: fixtures contain unknown keys: {unknown}")

    fixtures: dict[str, Any] = {}
    if "dialog_policy" in value:
        dialog_policy = value["dialog_policy"]
        if dialog_policy not in _DIALOG_POLICIES:
            raise ValueError(
                f"scenario {scenario_name!r}: fixtures.dialog_policy must be one of accept, dismiss, manual"
            )
        fixtures["dialog_policy"] = dialog_policy
    if "mock_routes" in value:
        fixtures["mock_routes"] = _validate_mock_routes(value["mock_routes"], scenario_name=scenario_name)
    return fixtures


def _validate_mock_routes(value: Any, *, scenario_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"scenario {scenario_name!r}: fixtures.mock_routes must be a list")
    routes: list[dict[str, Any]] = []
    for index, route in enumerate(value):
        routes.append(_validate_mock_route(route, index=index, scenario_name=scenario_name))
    return routes


def _validate_mock_route(route: Any, *, index: int, scenario_name: str) -> dict[str, Any]:
    if not isinstance(route, dict):
        raise ValueError(f"scenario {scenario_name!r}: fixtures.mock_routes[{index}] must be a mapping")
    route_spec = cast(dict[str, Any], route)
    pattern = _validate_mock_route_pattern(route_spec, index=index, scenario_name=scenario_name)
    normalized: dict[str, Any] = {"pattern": pattern}
    _copy_optional_mock_route_fields(route_spec, normalized, index=index, scenario_name=scenario_name)
    extra = sorted(set(route_spec) - {"pattern", "status", "body", "content_type", "headers"})
    if extra:
        raise ValueError(f"scenario {scenario_name!r}: fixtures.mock_routes[{index}] unknown keys: {extra}")
    return normalized


def _validate_mock_route_pattern(route: dict[str, Any], *, index: int, scenario_name: str) -> str:
    pattern = route.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError(
            f"scenario {scenario_name!r}: fixtures.mock_routes[{index}].pattern must be a non-empty string"
        )
    return pattern


def _copy_optional_mock_route_fields(
    route: dict[str, Any],
    normalized: dict[str, Any],
    *,
    index: int,
    scenario_name: str,
) -> None:
    if "status" in route:
        normalized["status"] = _validate_mock_route_status(route["status"], index=index, scenario_name=scenario_name)
    if "body" in route:
        normalized["body"] = _validate_mock_route_body(route["body"], index=index, scenario_name=scenario_name)
    if "content_type" in route:
        normalized["content_type"] = _validate_mock_route_content_type(
            route["content_type"], index=index, scenario_name=scenario_name
        )
    if "headers" in route:
        normalized["headers"] = _validate_mock_route_headers(route["headers"], index=index, scenario_name=scenario_name)


def _validate_mock_route_status(value: Any, *, index: int, scenario_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 100 <= value <= 599:
        raise ValueError(
            f"scenario {scenario_name!r}: fixtures.mock_routes[{index}].status must be an integer from 100 to 599"
        )
    return value


def _validate_mock_route_body(value: Any, *, index: int, scenario_name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"scenario {scenario_name!r}: fixtures.mock_routes[{index}].body must be a string or null")
    return value


def _validate_mock_route_content_type(value: Any, *, index: int, scenario_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"scenario {scenario_name!r}: fixtures.mock_routes[{index}].content_type must be a non-empty string"
        )
    return value


def _validate_mock_route_headers(value: Any, *, index: int, scenario_name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError(
            f"scenario {scenario_name!r}: fixtures.mock_routes[{index}].headers must be a mapping of strings"
        )
    return dict(value)


def _validate_startup_macros(value: Any, *, index: int, scenario_name: str) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, list) or not all(isinstance(macro, str) for macro in value):
        raise ValueError(
            f"scenario {scenario_name!r}: participants[{index}] 'startup_macros' must be a list of strings"
        )
    return value


def _validate_optional_ints(
    raw: dict[str, Any],
    fields: tuple[str, ...],
    *,
    index: int,
    scenario_name: str,
) -> None:
    for field_name in fields:
        value = raw.get(field_name)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"scenario {scenario_name!r}: participants[{index}] {field_name!r} must be an integer")


def _validate_optional_bools(
    raw: dict[str, Any],
    fields: tuple[str, ...],
    *,
    index: int,
    scenario_name: str,
) -> None:
    for field_name in fields:
        value = raw.get(field_name)
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"scenario {scenario_name!r}: participants[{index}] {field_name!r} must be a boolean")


def load_python_scenario(path: Path) -> Scenario:
    import importlib.util
    import sys

    # `*.py` scenarios execute arbitrary Python at module import — anything
    # at top level runs with the daemon's privileges. Default-deny so a
    # scenarios dir on shared storage / CI checkout can't be a code-exec
    # vector. Operators who deliberately ship Python scenarios opt in via
    # OCTOWRIGHT_ALLOW_PY_SCENARIOS.
    if not defaults.allow_py_scenarios():
        raise RuntimeError(
            f"Python scenario {path} is gated behind "
            f"{defaults.ALLOW_PY_SCENARIOS_ENV}=1; .py scenarios execute "
            f"arbitrary code at import. Either convert to .yaml or set "
            f"{defaults.ALLOW_PY_SCENARIOS_ENV}=1 to opt in."
        )
    # Opt-in path: keep the existing runtime warning so the trust boundary
    # is explicit and audit-able.
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
    # Containment check on both candidate paths: ``name`` comes from an MCP
    # client (untrusted) and the slug-style join is not enough — anything
    # with ``..`` or an absolute path would otherwise escape SCENARIOS_DIR.
    yaml_path = reject_unsafe_path(SCENARIOS_DIR / f"{name}.yaml", SCENARIOS_DIR, label=f"scenario name {name!r}")
    py_path = reject_unsafe_path(SCENARIOS_DIR / f"{name}.py", SCENARIOS_DIR, label=f"scenario name {name!r}")
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
    # Same containment guard as load_scenario — the name is MCP-supplied.
    path = reject_unsafe_path(
        SCENARIO_TEMPLATES_DIR / f"{name}.yaml",
        SCENARIO_TEMPLATES_DIR,
        label=f"scenario template name {name!r}",
    )
    if not path.exists():
        raise FileNotFoundError(f"no scenario template named {name!r} in {SCENARIO_TEMPLATES_DIR}")
    content = path.read_text(encoding="utf-8")
    # Reject arg values that contain CR/LF before raw substitution into YAML:
    # a newline in a value lets the caller inject arbitrary YAML structure
    # (extra keys, list items) once the {{placeholder}} is replaced and the
    # result is fed to yaml.safe_load.
    for k, v in args.items():
        sv = str(v)
        if "\n" in sv or "\r" in sv:
            raise ValueError(
                f"scenario template arg {k!r} contains a newline; "
                "templates substitute raw into YAML and newlines would inject structure"
            )
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


def _load_persona_or_none(name: str) -> Any:
    from octowright import personas as _p

    try:
        return _p.load_persona(name)
    except FileNotFoundError:
        return None


def resolve_terminal_launch(p: Participant) -> dict[str, Any]:
    """Return kwargs for ``terminal_pool.launch(**kwargs)`` from a terminal Participant.

    Note ``terminal_pool.launch``'s ``kind`` is the *connector* type (pty/ssh);
    the session's own kind is always ``"terminal"``. SSH fields resolve
    participant-override → persona ``app['ssh']`` default → omit. No password is
    read from the scenario (scenarios are persisted): key-based / known_hosts auth
    only — the pure builders live in ``octowright.terminal.connector_config`` so
    this stays importable on a core install.
    """
    from octowright.terminal.connector_config import (
        SSH_DEFAULT_PORT,
        pty_connector_config,
        ssh_connector_config,
    )

    opts = p.options
    connector_type = opts.get("connector_type") or "pty"
    if connector_type == "ssh":
        persona = _load_persona_or_none(p.persona)
        ssh = (getattr(persona, "app", {}) or {}).get("ssh", {}) or {}

        def _pick(key: str) -> Any:
            value = opts.get(key)
            return value if value is not None else ssh.get(key)

        port_opt = opts.get("port")
        port = int(port_opt) if port_opt is not None else int(ssh.get("port", SSH_DEFAULT_PORT))
        insecure_opt = opts.get("insecure_no_host_check")
        insecure = bool(insecure_opt) if insecure_opt is not None else bool(ssh.get("insecure_no_host_check", False))
        cfg = ssh_connector_config(
            host=_pick("host"),
            port=port,
            user=_pick("user"),
            key_path=_pick("key_path"),
            password=None,
            known_hosts=_pick("known_hosts"),
            insecure_no_host_check=insecure,
        )
    else:
        cfg = pty_connector_config(command=opts.get("command"), cols=opts.get("cols"), rows=opts.get("rows"))
    return {"kind": connector_type, "connector_config": cfg, "label": None, "profile": p.persona, "protected": False}


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
