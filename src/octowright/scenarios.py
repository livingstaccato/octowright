# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from provide.telemetry import get_logger

from .defaults import SCENARIO_TEMPLATES_DIR, SCENARIOS_DIR, SUPPORTED_KINDS

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
        return load_yaml_scenario(yaml_path.read_text(), name)
    raise FileNotFoundError(
        f"no scenario named {name!r} in {SCENARIOS_DIR}; list available with `scenario_list` "
        f"or drop a {name}.yaml file in that directory"
    )


def load_scenario_template(name: str, args: dict[str, Any]) -> Scenario:
    path = SCENARIO_TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no scenario template named {name!r} in {SCENARIO_TEMPLATES_DIR}")
    content = path.read_text()
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
    from . import personas as _p

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
    from . import personas as _p

    if p.startup_macros is not None:
        return list(p.startup_macros)
    try:
        persona = _p.load_persona(p.persona)
    except FileNotFoundError:
        return []
    return list(persona.default_macros or [])


@dataclass
class LiveScenario:
    scenario_id: str
    name: str
    spec: Scenario
    participants: list[dict[str, Any]]  # [{instance_id, persona, kind, role, ...}]


class ScenarioPool:
    """Tracks scenarios the process has started. Keyed by scenario_id."""

    def __init__(self) -> None:
        self._live: dict[str, LiveScenario] = {}

    def get(self, scenario_id: str) -> LiveScenario:
        if scenario_id not in self._live:
            known = list(self._live)
            hint = (
                "no scenarios are running — start one with `scenario_start name=<name>`"
                if not known
                else f"call `scenario_status` to see live ids; known: {known}"
            )
            raise KeyError(f"no live scenario with id={scenario_id!r}; {hint}")
        return self._live[scenario_id]

    def list_live(self) -> list[dict[str, Any]]:
        return [
            {
                "scenario_id": ls.scenario_id,
                "name": ls.name,
                "participants": ls.participants,
            }
            for ls in self._live.values()
        ]

    async def start(
        self,
        *,
        name: str | None = None,
        browser_pool: Any,
        spec: Scenario | None = None,
    ) -> LiveScenario:
        if spec is None:
            if name is None:
                raise ValueError("either 'name' or 'spec' must be provided to start a scenario")
            spec = load_scenario(name)

        effective_name = name or spec.name
        if not spec.participants:
            raise RuntimeError(f"scenario {effective_name!r} has no participants")
        scenario_id = _uuid.uuid4().hex[:12]
        # Build launch kwargs from participant resolution.
        launch_specs = [resolve_launch_kwargs(p) for p in spec.participants]
        result = await browser_pool.spawn_roster(launch_specs)
        if result["errors"]:
            # Partial launch — close any that came up before raising.
            for launched in result["launched"]:
                try:
                    await browser_pool.close(launched["instance_id"])
                except Exception:
                    pass
            raise RuntimeError(
                f"scenario {effective_name!r}: {len(result['errors'])} participant(s) failed to launch: {result['errors']}"
            )

        participants: list[dict[str, Any]] = []
        for participant_spec, launched in zip(
            spec.participants,
            result["launched"],
            strict=True,
        ):
            entry = dict(launched)
            entry["persona"] = participant_spec.persona
            entry["role"] = participant_spec.role
            participants.append(entry)

        live = LiveScenario(
            scenario_id=scenario_id,
            name=effective_name,
            spec=spec,
            participants=participants,
        )
        self._live[scenario_id] = live

        # Apply fixtures per participant.
        await _apply_fixtures(browser_pool, live, spec.fixtures)
        # Run startup_macros per participant.
        await _run_startup_macros(browser_pool, live)

        log.info(
            "octowright.scenario.started",
            scenario_id=scenario_id,
            name=effective_name,
            participants=[p["persona"] for p in participants],
        )
        return live

    async def stop(self, *, scenario_id: str, browser_pool: Any) -> dict[str, Any]:
        live = self.get(scenario_id)
        summary: dict[str, Any] = {
            "scenario_id": scenario_id,
            "teardown_errors": [],
            "closed": [],
        }
        # Teardown macro per participant.
        if live.spec.teardown_macro:
            from . import macros as _macros

            for p in live.participants:
                try:
                    session = browser_pool.get(p["instance_id"])
                    await _macros.run_macro(
                        session=session,
                        name=live.spec.teardown_macro,
                        args={},
                    )
                except Exception as e:
                    summary["teardown_errors"].append(
                        {"instance_id": p["instance_id"], "error": repr(e)},
                    )
        # Close every participant browser.
        for p in live.participants:
            try:
                await browser_pool.close(p["instance_id"])
                summary["closed"].append(p["instance_id"])
            except Exception as e:
                summary["teardown_errors"].append(
                    {"instance_id": p["instance_id"], "error": repr(e)},
                )
        del self._live[scenario_id]
        log.info(
            "octowright.scenario.stopped",
            scenario_id=scenario_id,
            errors=len(summary["teardown_errors"]),
        )
        return summary

    def tail(
        self,
        *,
        scenario_id: str,
        since_cursors: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Return JSONL events appended to each participant's log since the
        cursors in ``since_cursors`` (keyed by instance_id, value = byte offset).

        Missing instance_ids in ``since_cursors`` default to offset 0 (read
        from the beginning).

        Returns:
            {
              "scenario_id": ...,
              "events": [
                  {"instance_id": ..., "persona": ..., "role": ..., "ts": ..., "action": ..., ...attrs},
                  ...  # ordered by (instance_id, file order)
              ],
              "cursors": {instance_id: new_byte_offset, ...},
            }
        """
        from .recorder import tail_log

        live = self.get(scenario_id)
        cursors = dict(since_cursors or {})
        events: list[dict[str, Any]] = []
        new_cursors: dict[str, int] = {}
        for p in live.participants:
            iid = p["instance_id"]
            log_path = Path(p["log_path"])
            prev = cursors.get(iid, 0)

            p_events, next_cursor, _ = tail_log(log_path, prev)

            new_cursors[iid] = next_cursor
            for entry in p_events:
                entry["instance_id"] = iid
                entry["persona"] = p["persona"]
                entry["role"] = p["role"]
                events.append(entry)

        return {
            "scenario_id": scenario_id,
            "events": events,
            "cursors": new_cursors,
        }

    async def run_macro(
        self,
        *,
        scenario_id: str,
        macro: str,
        browser_pool: Any,
        role: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import asyncio as _asyncio

        from . import macros as _macros

        live = self.get(scenario_id)
        targets = [p for p in live.participants if role is None or p["role"] == role]

        async def _run(p: dict[str, Any]) -> dict[str, Any]:
            session = browser_pool.get(p["instance_id"])
            try:
                await _macros.run_macro(session=session, name=macro, args=args or {})
                return {"instance_id": p["instance_id"], "ok": True}
            except Exception as e:
                return {"instance_id": p["instance_id"], "ok": False, "error": repr(e)}

        results = await _asyncio.gather(*(_run(p) for p in targets))
        return {
            "scenario_id": scenario_id,
            "macro": macro,
            "role": role,
            "targeted": len(targets),
            "results": list(results),
        }

    async def wait_for_sync(
        self,
        *,
        scenario_id: str,
        browser_pool: Any,
        role: str | None = None,
        selector: str | None = None,
        text: str | None = None,
        url: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Block until all participants matching `role` satisfy the condition.

        Condition options:
        - selector: all targets must see this selector.
        - text: all targets must see this text.
        - url: all targets must be at this URL (regex).

        If multiple are given, they are checked in priority: selector > text > url.
        If none are given, it waits for 'networkidle' on all targets.
        """
        import asyncio as _asyncio

        live = self.get(scenario_id)
        targets = [p for p in live.participants if role is None or p["role"] == role]

        async def _wait(p: dict[str, Any]) -> dict[str, Any]:
            session = browser_pool.get(p["instance_id"])
            try:
                if selector or text:
                    await session.wait_for(selector=selector, text=text, timeout_ms=timeout_ms)
                elif url:
                    import re as _re

                    if not _re.search(url, session.page.url):
                        await session.page.wait_for_url(url, timeout=timeout_ms or 30000)
                else:
                    await session.wait_for(selector=None, text=None, timeout_ms=timeout_ms)
                return {"instance_id": p["instance_id"], "ok": True}
            except Exception as e:
                return {"instance_id": p["instance_id"], "ok": False, "error": repr(e)}

        results = await _asyncio.gather(*(_wait(p) for p in targets))
        return {
            "scenario_id": scenario_id,
            "role": role,
            "selector": selector,
            "text": text,
            "url": url,
            "targeted": len(targets),
            "results": list(results),
        }


async def _apply_fixtures(
    browser_pool: Any,
    live: LiveScenario,
    fixtures: dict[str, Any],
) -> None:
    dialog_policy = fixtures.get("dialog_policy")
    mock_routes = fixtures.get("mock_routes") or []
    for p in live.participants:
        session = browser_pool.get(p["instance_id"])
        if dialog_policy:
            session.set_dialog_policy(dialog_policy)
        for mr in mock_routes:
            await session.mock_route(
                mr["pattern"],
                status=mr.get("status", 200),
                body=mr.get("body"),
                content_type=mr.get("content_type", "application/json"),
                headers=mr.get("headers"),
            )


async def _run_startup_macros(browser_pool: Any, live: LiveScenario) -> None:
    from . import macros as _macros

    for participant_dict, participant_spec in zip(
        live.participants,
        live.spec.participants,
        strict=True,
    ):
        for macro_name in resolve_startup_macros(participant_spec):
            session = browser_pool.get(participant_dict["instance_id"])
            try:
                await _macros.run_macro(session=session, name=macro_name, args={})
            except Exception as e:
                log.warning(
                    "scenario.startup_macro_failed",
                    scenario_id=live.scenario_id,
                    persona=participant_dict["persona"],
                    macro=macro_name,
                    error=repr(e),
                )
