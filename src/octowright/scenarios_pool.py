# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

log = get_logger(__name__)


@dataclass
class LiveScenario:
    scenario_id: str
    name: str
    spec: Any
    participants: list[dict[str, Any]]


class ScenarioPool:
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
            {"scenario_id": ls.scenario_id, "name": ls.name, "participants": ls.participants}
            for ls in self._live.values()
        ]

    def remap_participant(
        self, *, scenario_id: str, old_instance_id: str, new_instance_id: str, role: str | None = None
    ) -> dict[str, Any]:
        live = self.get(scenario_id)
        matches = [p for p in live.participants if p.get("instance_id") == old_instance_id]
        if role is not None:
            matches = [p for p in matches if p.get("role") == role]
        if not matches:
            suffix = f" with role={role!r}" if role is not None else ""
            raise ValueError(
                f"scenario {scenario_id!r} has no participant bound to instance_id={old_instance_id!r}{suffix}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"scenario {scenario_id!r} has multiple participants for instance_id={old_instance_id!r}; pass role=... to disambiguate"
            )
        target = matches[0]
        target["instance_id"] = new_instance_id
        return {
            "scenario_id": scenario_id,
            "role": target.get("role"),
            "persona": target.get("persona"),
            "old_instance_id": old_instance_id,
            "new_instance_id": new_instance_id,
        }

    def remap_participants(self, *, scenario_id: str, remaps: list[dict[str, Any]]) -> dict[str, Any]:
        applied: list[dict[str, Any]] = []
        for item in remaps:
            old_instance_id = item.get("old_instance_id")
            new_instance_id = item.get("new_instance_id")
            role = item.get("role")
            if not isinstance(old_instance_id, str) or not old_instance_id:
                raise ValueError("each remap requires non-empty old_instance_id")
            if not isinstance(new_instance_id, str) or not new_instance_id:
                raise ValueError("each remap requires non-empty new_instance_id")
            if role is not None and not isinstance(role, str):
                raise ValueError("remap role must be a string when provided")
            applied.append(
                self.remap_participant(
                    scenario_id=scenario_id, old_instance_id=old_instance_id, new_instance_id=new_instance_id, role=role
                )
            )
        return {"scenario_id": scenario_id, "applied": applied, "count": len(applied)}

    async def start(self, *, name: str | None = None, browser_pool: Any, spec: Any | None = None) -> LiveScenario:
        from .scenarios import load_scenario, resolve_launch_kwargs

        if spec is None:
            if name is None:
                raise ValueError("either 'name' or 'spec' must be provided to start a scenario")
            spec = load_scenario(name)
        effective_name = name or spec.name
        if not spec.participants:
            raise RuntimeError(f"scenario {effective_name!r} has no participants")
        scenario_id = _uuid.uuid4().hex[:12]
        result = await browser_pool.spawn_roster([resolve_launch_kwargs(p) for p in spec.participants])
        if result["errors"]:
            for launched in result["launched"]:
                try:
                    await browser_pool.close(launched["instance_id"])
                except Exception:
                    pass
            raise RuntimeError(
                f"scenario {effective_name!r}: {len(result['errors'])} participant(s) failed to launch: {result['errors']}"
            )
        participants: list[dict[str, Any]] = []
        for participant_spec, launched in zip(spec.participants, result["launched"], strict=True):
            entry = dict(launched)
            entry["persona"] = participant_spec.persona
            entry["role"] = participant_spec.role
            participants.append(entry)
        live = LiveScenario(scenario_id=scenario_id, name=effective_name, spec=spec, participants=participants)
        self._live[scenario_id] = live
        await _apply_fixtures(browser_pool, live, spec.fixtures)
        await _run_startup_macros(browser_pool, live)
        return live

    async def stop(self, *, scenario_id: str, browser_pool: Any) -> dict[str, Any]:
        live = self.get(scenario_id)
        summary: dict[str, Any] = {"scenario_id": scenario_id, "teardown_errors": [], "closed": []}
        if live.spec.teardown_macro:
            from . import macros as _macros

            for p in live.participants:
                try:
                    session = browser_pool.get(p["instance_id"])
                    await _macros.run_macro(session=session, name=live.spec.teardown_macro, args={})
                except Exception as e:
                    summary["teardown_errors"].append({"instance_id": p["instance_id"], "error": repr(e)})
        for p in live.participants:
            try:
                await browser_pool.close(p["instance_id"])
                summary["closed"].append(p["instance_id"])
            except Exception as e:
                summary["teardown_errors"].append({"instance_id": p["instance_id"], "error": repr(e)})
        del self._live[scenario_id]
        return summary

    def tail(self, *, scenario_id: str, since_cursors: dict[str, int] | None = None) -> dict[str, Any]:
        from .recorder import tail_log

        live = self.get(scenario_id)
        cursors = dict(since_cursors or {})
        events: list[dict[str, Any]] = []
        new_cursors: dict[str, int] = {}
        for p in live.participants:
            iid = p["instance_id"]
            p_events, next_cursor, _ = tail_log(Path(p["log_path"]), cursors.get(iid, 0))
            new_cursors[iid] = next_cursor
            for entry in p_events:
                entry["instance_id"] = iid
                entry["persona"] = p["persona"]
                entry["role"] = p["role"]
                events.append(entry)
        return {"scenario_id": scenario_id, "events": events, "cursors": new_cursors}

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
        import asyncio as _asyncio
        import re as _re

        live = self.get(scenario_id)
        targets = [p for p in live.participants if role is None or p["role"] == role]

        async def _wait(p: dict[str, Any]) -> dict[str, Any]:
            session = browser_pool.get(p["instance_id"])
            try:
                if selector or text:
                    await session.wait_for(selector=selector, text=text, timeout_ms=timeout_ms)
                elif url:
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


async def _apply_fixtures(browser_pool: Any, live: LiveScenario, fixtures: dict[str, Any]) -> None:
    import asyncio as _asyncio

    dialog_policy = fixtures.get("dialog_policy")
    mock_routes = fixtures.get("mock_routes") or []

    async def _apply(p: dict[str, Any]) -> None:
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

    await _asyncio.gather(*(_apply(p) for p in live.participants))


async def _run_startup_macros(browser_pool: Any, live: LiveScenario) -> None:
    import asyncio as _asyncio

    from . import macros as _macros
    from .scenarios import resolve_startup_macros

    async def _run_for_participant(participant_dict: dict[str, Any], participant_spec: Any) -> None:
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

    await _asyncio.gather(
        *(_run_for_participant(pd, ps) for pd, ps in zip(live.participants, live.spec.participants, strict=True))
    )
