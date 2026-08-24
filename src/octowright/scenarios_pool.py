# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import anyio
from provide.telemetry import get_logger

from octowright._tracing import span
from octowright.browser_pool.roster import shielded_rollback_close
from octowright.defaults import SUPPORTED_KINDS
from octowright.mcp_types import (
    ScenarioParticipantOutcome,
    ScenarioRemapEntry,
    ScenarioRemapResult,
    ScenarioRunMacroResult,
    ScenarioStopResult,
    ScenarioTailEntry,
    ScenarioTailResult,
    ScenarioWaitForSyncResult,
)
from octowright.plugins.contract import SupportsDialogPolicy, SupportsMacros, SupportsMockRoutes, SupportsSync

log = get_logger(__name__)


@dataclass
class LiveScenario:
    scenario_id: str
    name: str
    spec: Any
    participants: list[dict[str, Any]]


class ScenarioRoleNotFoundError(ValueError):
    """Raised when an explicit role filter matches no live participant."""


class ScenarioPool:
    def __init__(self) -> None:
        self._live: dict[str, LiveScenario] = {}
        self._live_lock = asyncio.Lock()

    def get(self, scenario_id: str) -> LiveScenario:
        if scenario_id not in self._live:
            raise KeyError(self._missing_scenario_message(scenario_id))
        return self._live[scenario_id]

    def _missing_scenario_message(self, scenario_id: str) -> str:
        known = list(self._live)
        hint = (
            "no scenarios are running — start one with `scenario_start name=<name>`"
            if not known
            else f"call `scenario_status` to see live ids; known: {known}"
        )
        return f"no live scenario with id={scenario_id!r}; {hint}"

    def maybe_get(self, scenario_id: str) -> LiveScenario | None:
        return self._live.get(scenario_id)

    def has_live(self, scenario_id: str) -> bool:
        return scenario_id in self._live

    def list_live(self) -> list[dict[str, Any]]:  # rendered shape has fields beyond ScenarioParticipant; left as dict
        return [
            {"scenario_id": ls.scenario_id, "name": ls.name, "participants": ls.participants}
            for ls in self._live.values()
        ]

    def _participants_for_role(self, live: LiveScenario, role: str | None) -> list[dict[str, Any]]:
        targets = [p for p in live.participants if role is None or p["role"] == role]
        if role is not None and not targets:
            raise ScenarioRoleNotFoundError(f"scenario {live.scenario_id!r} has no participants with role {role!r}")
        return targets

    def remap_participant(
        self,
        *,
        scenario_id: str,
        old_instance_id: str,
        new_instance_id: str,
        role: str | None = None,
        browser_pool: Any | None = None,
        terminal_pool: Any | None = None,
    ) -> ScenarioRemapEntry:
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
        if browser_pool is None:
            raise ValueError("browser_pool is required for remap validation")
        # A terminal participant's replacement must come from the terminal pool.
        lookup_pool = self._pool_for(target, browser_pool, terminal_pool)
        replacement = lookup_pool.maybe_get(new_instance_id)
        if replacement is None:
            raise ValueError(f"replacement instance_id={new_instance_id!r} is not live")
        expected_kind = target.get("kind")
        actual_kind = getattr(replacement, "kind", None)
        if expected_kind is not None and actual_kind != expected_kind:
            raise ValueError(
                f"replacement instance_id={new_instance_id!r} has kind={actual_kind!r}, expected {expected_kind!r}"
            )
        expected_profile = target.get("profile") or target.get("persona")
        actual_profile = getattr(replacement, "profile", None)
        if expected_profile is not None and actual_profile != expected_profile:
            raise ValueError(
                f"replacement instance_id={new_instance_id!r} has profile={actual_profile!r}, expected {expected_profile!r}"
            )
        target["instance_id"] = new_instance_id
        return {
            "scenario_id": scenario_id,
            "role": target.get("role"),
            "persona": target.get("persona"),
            "old_instance_id": old_instance_id,
            "new_instance_id": new_instance_id,
        }

    def remap_participants(
        self,
        *,
        scenario_id: str,
        remaps: list[dict[str, Any]],
        browser_pool: Any | None = None,
        terminal_pool: Any | None = None,
    ) -> ScenarioRemapResult:
        applied: list[ScenarioRemapEntry] = []
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
                    scenario_id=scenario_id,
                    old_instance_id=old_instance_id,
                    new_instance_id=new_instance_id,
                    role=role,
                    browser_pool=browser_pool,
                    terminal_pool=terminal_pool,
                )
            )
        return {"scenario_id": scenario_id, "applied": applied, "count": len(applied)}

    async def start(
        self,
        *,
        name: str | None = None,
        browser_pool: Any,
        spec: Any | None = None,
        terminal_pool: Any | None = None,
    ) -> LiveScenario:
        if spec is None:
            if name is None:
                raise ValueError("either 'name' or 'spec' must be provided to start a scenario")
            from octowright.scenarios import load_scenario

            spec = load_scenario(name)
        effective_name = name or spec.name
        if not spec.participants:
            raise RuntimeError(f"scenario {effective_name!r} has no participants")
        # Group by kind rather than splitting browser/terminal: browsers keep
        # the roster (it batches window creation), terminals keep their
        # individual launches, and a plugin kind launches through its own pool.
        from octowright.scenario_kinds import TERMINAL_KIND

        terminal_specs = [(i, p) for i, p in enumerate(spec.participants) if p.kind == TERMINAL_KIND]
        if terminal_specs and terminal_pool is None:
            raise RuntimeError(
                f"scenario {effective_name!r} has terminal participant(s) but the octowright[terminal] "
                "extra is not installed (terminal_pool is unavailable)"
            )
        browser_specs = [(i, p) for i, p in enumerate(spec.participants) if p.kind in SUPPORTED_KINDS]
        plugin_specs = [
            (i, p) for i, p in enumerate(spec.participants) if p.kind != TERMINAL_KIND and p.kind not in SUPPORTED_KINDS
        ]
        scenario_id = _uuid.uuid4().hex[:12]
        # Wrap the whole multi-session spawn + fixture + startup-macro sequence
        # under a single span so per-participant launches and macro runs nest
        # cleanly underneath. Count (not the full list) keeps cardinality bounded.
        with span(
            "octowright.scenario.start",
            scenario_id=scenario_id,
            scenario_name=effective_name,
            participants=len(spec.participants),
        ):
            launched_by_index, browser_ids, terminal_ids, plugin_ids_by_kind = await self._launch_participants(
                browser_pool, terminal_pool, browser_specs, terminal_specs, plugin_specs, effective_name
            )
            # Reassemble in original spec order so roles/personas line up.
            participants: list[dict[str, Any]] = []
            for i, participant_spec in enumerate(spec.participants):
                entry = dict(launched_by_index[i])
                entry["persona"] = participant_spec.persona
                entry["role"] = participant_spec.role
                participants.append(entry)
            live = LiveScenario(scenario_id=scenario_id, name=effective_name, spec=spec, participants=participants)
            async with self._live_lock:
                self._live[scenario_id] = live
            try:
                await _apply_fixtures(browser_pool, live, spec.fixtures)
                await _run_startup_macros(browser_pool, live)
            except BaseException:
                # CancelledError is a BaseException, so catch it here too — but the
                # rollback must *complete* before we re-raise, so run it shielded.
                await self._rollback_start(
                    scenario_id, browser_pool, browser_ids, terminal_pool, terminal_ids, plugin_ids_by_kind
                )
                raise
            return live

    async def _launch_participants(
        self,
        browser_pool: Any,
        terminal_pool: Any | None,
        browser_specs: list[tuple[int, Any]],
        terminal_specs: list[tuple[int, Any]],
        plugin_specs: list[tuple[int, Any]],
        effective_name: str,
    ) -> tuple[dict[int, dict[str, Any]], list[str], list[str], dict[str, list[str]]]:
        """Launch browser participants via the roster, plugin participants
        through their own pools, and terminal participants individually --
        keyed back to their original participant index. Plugins launch
        *before* terminals so ``_launch_terminals``' errors-so-far early-out
        (which exists to stop it opening further, possibly remote, sessions
        once anything has already failed) also covers a plugin launch
        failure, not just a browser-roster one. On any failure, close
        everything launched in every group and raise."""
        from octowright.scenarios import resolve_launch_kwargs

        launched_by_index: dict[int, dict[str, Any]] = {}
        browser_ids: list[str] = []
        errors: list[Any] = []

        if browser_specs:
            roster = await browser_pool.spawn_roster([resolve_launch_kwargs(p) for _, p in browser_specs])
            browser_ids = [launched["instance_id"] for launched in roster["launched"]]
            errors.extend(roster["errors"])
            if not roster["errors"]:
                for (i, _p), launched in zip(browser_specs, roster["launched"], strict=True):
                    launched_by_index[i] = launched

        plugin_ids_by_kind = await self._launch_plugin_group(plugin_specs, launched_by_index, errors)

        terminal_ids = await self._launch_terminals(terminal_pool, terminal_specs, launched_by_index, errors)

        if errors:
            await self._close_launched(browser_pool, browser_ids, terminal_pool, terminal_ids, plugin_ids_by_kind)
            raise RuntimeError(f"scenario {effective_name!r}: {len(errors)} participant(s) failed to launch: {errors}")
        return launched_by_index, browser_ids, terminal_ids, plugin_ids_by_kind

    @staticmethod
    async def _launch_plugin_group(
        plugin_specs: list[tuple[int, Any]],
        launched_by_index: dict[int, dict[str, Any]],
        errors: list[Any],
    ) -> dict[str, list[str]]:
        """Launch every plugin participant and report what actually landed,
        grouped by kind for rollback.

        Wraps ``_launch_plugin_participants``: a pool/adapter lookup failure
        (unregistered kind) raises rather than appending to ``errors``, so it
        is folded in here -- the shared close-everything-and-raise path in
        ``_launch_participants`` still runs, and a kind that resolves to no
        pool reports the same failure shape as any other launch failure.
        Reconstructing from ``launched_by_index`` rather than trusting the
        return value alone matters for the same reason: an exception raised
        partway through the loop still leaves earlier successful launches
        recorded there (it is mutated in place), and those must still be
        closed on rollback.
        """
        if not plugin_specs:
            return {}
        try:
            await ScenarioPool._launch_plugin_participants(plugin_specs, launched_by_index, errors)
        except Exception as exc:
            errors.append(f"plugin participant launch aborted: {exc!r}")
        plugin_ids_by_kind: dict[str, list[str]] = {}
        for i, p in plugin_specs:
            entry = launched_by_index.get(i)
            if entry is not None:
                plugin_ids_by_kind.setdefault(p.kind, []).append(entry["instance_id"])
        return plugin_ids_by_kind

    @staticmethod
    async def _launch_plugin_participants(
        plugin_specs: list[tuple[int, Any]],
        launched_by_index: dict[int, dict[str, Any]],
        errors: list[Any],
    ) -> list[str]:
        """Launch each plugin participant through the pool its kind registered.

        Sequential rather than gathered, matching ``_launch_terminals``: a
        plugin pool makes no concurrency promise, and a scenario roster is
        small enough that the wall-clock cost is not worth the first
        cross-plugin race report. Also matches ``_launch_terminals``' early-out:
        stops launching once ``errors`` is already non-empty (a failed browser
        roster, or an earlier plugin participant in this same loop), so a
        failed launch never goes on to open further -- possibly remote --
        plugin sessions before rollback.
        """
        from octowright.scenario_kinds import adapter_for, pool_for_kind
        from octowright.scenarios import _load_persona_or_none

        launched_ids: list[str] = []
        for index, p in plugin_specs:
            if errors:
                break
            pool = pool_for_kind(p.kind, browser_pool=None, terminal_pool=None)
            adapter = adapter_for(p.kind, browser_pool=None)
            if adapter is None:
                errors.append(f"participant kind {p.kind!r} has no scenario adapter")
                continue
            try:
                launch_kwargs = adapter.resolve_participant(p, _load_persona_or_none(p.persona))
                launched = await pool.launch(**launch_kwargs)
            except Exception as e:
                errors.append(f"participant {p.persona!r} ({p.kind}) failed to launch: {e!r}")
                continue
            entry = dict(launched)
            entry.setdefault("kind", p.kind)
            launched_by_index[index] = entry
            launched_ids.append(entry["instance_id"])
        return launched_ids

    @staticmethod
    async def _launch_terminals(
        terminal_pool: Any | None,
        terminal_specs: list[tuple[int, Any]],
        launched_by_index: dict[int, dict[str, Any]],
        errors: list[Any],
    ) -> list[str]:
        """Launch each terminal participant, recording its launched dict by index.
        Stops early if ``errors`` is already non-empty (the browser roster failed),
        so a failed browser launch never opens further (esp. remote SSH) sessions."""
        from octowright.scenarios import resolve_terminal_launch
        from octowright.terminal.errors import TerminalPoolUnavailableError

        terminal_ids: list[str] = []
        for i, p in terminal_specs:
            if errors:
                break
            # start() guarantees a terminal_pool whenever terminal_specs is non-empty.
            # Explicit raise (not assert) so a broken invariant fails loudly even
            # under `python -O`, rather than crashing on `None.launch`.
            if terminal_pool is None:
                raise TerminalPoolUnavailableError(
                    "terminal participants present but terminal_pool is None — start() invariant violated"
                )
            try:
                launched = await terminal_pool.launch(**resolve_terminal_launch(p))
            except Exception as exc:
                errors.append({"persona": p.persona, "error": repr(exc)})
                continue
            terminal_ids.append(launched["instance_id"])
            launched_by_index[i] = launched
        return terminal_ids

    @staticmethod
    def _plugin_pool_groups(plugin_ids_by_kind: dict[str, list[str]]) -> list[tuple[Any, list[str]]]:
        """Resolve (pool, ids) for each plugin kind's launched sessions, for
        teardown. A kind whose pool can no longer be resolved (e.g. the
        registry changed mid-run) is logged and skipped rather than raised --
        teardown must still close every OTHER already-launched session."""
        from octowright.scenario_kinds import pool_for_kind

        groups: list[tuple[Any, list[str]]] = []
        for kind, ids in plugin_ids_by_kind.items():
            try:
                pool = pool_for_kind(kind, browser_pool=None, terminal_pool=None)
            except Exception as exc:
                log.warning("scenario.rollback.pool_lookup_failed", kind=kind, error=repr(exc))
                continue
            groups.append((pool, ids))
        return groups

    @staticmethod
    async def _close_launched(
        browser_pool: Any,
        browser_ids: list[str],
        terminal_pool: Any | None,
        terminal_ids: list[str],
        plugin_ids_by_kind: dict[str, list[str]] | None = None,
    ) -> None:
        """Best-effort close of partially-launched sessions across every pool."""
        groups: list[tuple[Any, list[str]]] = [(browser_pool, browser_ids), (terminal_pool, terminal_ids)]
        if plugin_ids_by_kind:
            groups.extend(ScenarioPool._plugin_pool_groups(plugin_ids_by_kind))
        for pool, ids in groups:
            if pool is None:
                continue
            for iid in ids:
                try:
                    await pool.close(iid, force=True)
                except Exception as exc:
                    # Cleanup-after-error path: surface so a leaked session is auditable.
                    log.warning("scenario.rollback.close_failed", instance_id=iid, error=repr(exc))

    async def _rollback_start(
        self,
        scenario_id: str,
        browser_pool: Any,
        browser_ids: list[str],
        terminal_pool: Any | None,
        terminal_ids: list[str],
        plugin_ids_by_kind: dict[str, list[str]] | None = None,
    ) -> None:
        """Shielded teardown for a scenario that failed or was cancelled during
        fixture application / startup macros: drop bookkeeping and close every
        launched session (browser + terminal + plugin) before the original
        exception re-propagates."""
        with anyio.CancelScope(shield=True):
            async with self._live_lock:
                self._live.pop(scenario_id, None)
            await shielded_rollback_close(browser_pool, browser_ids, logger=log, event="scenario.rollback.close_failed")
            if terminal_pool is not None and terminal_ids:
                await shielded_rollback_close(
                    terminal_pool, terminal_ids, logger=log, event="scenario.rollback.close_failed"
                )
            if plugin_ids_by_kind:
                for pool, ids in self._plugin_pool_groups(plugin_ids_by_kind):
                    await shielded_rollback_close(pool, ids, logger=log, event="scenario.rollback.close_failed")

    @staticmethod
    def _pool_for(p: dict[str, Any], browser_pool: Any, terminal_pool: Any | None) -> Any:
        """The pool that owns participant ``p``, resolved by its recorded ``kind``.

        A participant dict with no recorded ``kind`` defaults to the browser
        pool -- the pre-plugin default every hand-built ``LiveScenario`` in the
        test suite (and any caller that never set the field) relies on. Every
        production launch path (browser roster, terminal launch, and a plugin's
        own ``entry.setdefault("kind", p.kind)``) stamps ``kind``, so this
        branch is only ever reached by a test fixture today -- but a future
        adapter that forgets to stamp it would otherwise silently misroute to
        the browser pool, so the fallback is logged rather than silent.
        """
        from octowright.scenario_kinds import pool_for_kind

        kind = p.get("kind")
        if not kind:
            log.debug(
                "scenario.pool_for.kind_missing_defaulting_to_browser",
                instance_id=p.get("instance_id"),
                persona=p.get("persona"),
            )
            return browser_pool
        return pool_for_kind(kind, browser_pool=browser_pool, terminal_pool=terminal_pool)

    async def stop(
        self, *, scenario_id: str, browser_pool: Any, terminal_pool: Any | None = None
    ) -> ScenarioStopResult:
        # Shield the whole teardown: the scenario is popped from the registry
        # up front, so a cancel mid-teardown would strand live participants with
        # no scenario_id left to retry. Matching _rollback_start, the pop + macro
        # + closes run to completion even under cancellation.
        with anyio.CancelScope(shield=True):
            async with self._live_lock:
                live = self._live.pop(scenario_id, None)
            if live is None:
                raise KeyError(self._missing_scenario_message(scenario_id))
            summary: ScenarioStopResult = {"scenario_id": scenario_id, "teardown_errors": [], "closed": []}
            if live.spec.teardown_macro:
                from octowright.scenario_kinds import adapter_for

                for p in live.participants:
                    adapter = adapter_for(p.get("kind") or "", browser_pool=browser_pool)
                    if not isinstance(adapter, SupportsMacros):
                        continue  # a kind with no run_macro has no teardown macro to run
                    try:
                        await adapter.run_macro(p["instance_id"], name=live.spec.teardown_macro, args={})
                    except Exception as e:
                        summary["teardown_errors"].append({"instance_id": p["instance_id"], "error": repr(e)})
            for p in live.participants:
                try:
                    await self._pool_for(p, browser_pool, terminal_pool).close(p["instance_id"], force=True)
                    summary["closed"].append(p["instance_id"])
                except Exception as e:
                    summary["teardown_errors"].append({"instance_id": p["instance_id"], "error": repr(e)})
        return summary

    def tail(self, *, scenario_id: str, since_cursors: dict[str, int] | None = None) -> ScenarioTailResult:
        from octowright.recorder import tail_log

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
                entry["scenario_role"] = p["role"]  # NOT "role" — that is the ARIA locator key
                events.append(entry)
        return {
            "scenario_id": scenario_id,
            "events": cast("list[ScenarioTailEntry]", events),
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
    ) -> ScenarioRunMacroResult:
        import asyncio as _asyncio

        live = self.get(scenario_id)
        targets = self._participants_for_role(live, role)
        # Wrap the fan-out so the per-participant macro.run spans nest under a
        # single scenario-scoped parent. ``targeted`` records whether the role
        # filter narrowed the fan-out at all (None role = fan to every
        # participant); ``role`` is the literal filter value the operator
        # supplied, propagated for filtering in the trace UI.
        with span(
            "octowright.scenario.run_macro",
            scenario_id=scenario_id,
            macro=macro,
            role=role,
            targeted=role is not None,
        ):

            async def _run(p: dict[str, Any]) -> ScenarioParticipantOutcome:
                from octowright.scenario_kinds import adapter_for

                kind = p.get("kind") or ""
                adapter = adapter_for(kind, browser_pool=browser_pool)
                if not isinstance(adapter, SupportsMacros):
                    return {
                        "instance_id": p["instance_id"],
                        "ok": False,
                        "error": f"kind {kind!r} does not support macros (its adapter provides no run_macro)",
                    }
                try:
                    await adapter.run_macro(p["instance_id"], name=macro, args=args or {})
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
    ) -> ScenarioWaitForSyncResult:
        import asyncio as _asyncio

        live = self.get(scenario_id)
        targets = self._participants_for_role(live, role)

        async def _wait(p: dict[str, Any]) -> ScenarioParticipantOutcome:
            from octowright.scenario_kinds import adapter_for

            kind = p.get("kind") or ""
            adapter = adapter_for(kind, browser_pool=browser_pool)
            if not isinstance(adapter, SupportsSync):
                return {
                    "instance_id": p["instance_id"],
                    "ok": False,
                    "error": f"kind {kind!r} does not support sync (its adapter provides no wait_for_sync)",
                }
            try:
                await adapter.wait_for_sync(
                    p["instance_id"], selector=selector, text=text, url=url, timeout_ms=timeout_ms
                )
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
    if not dialog_policy and not mock_routes:
        # Nothing to configure -- skip touching any pool at all. The common
        # case (a scenario that declares no fixtures) must not force a
        # session lookup for every participant, browser or plugin, that has
        # nothing to receive. Per-kind adapter dispatch for the fixtures that
        # ARE set is a separate concern (routing dialog_policy/mock_routes to
        # a plugin's own adapter rather than the browser pool).
        return

    async def _apply(p: dict[str, Any]) -> None:
        from octowright.scenario_kinds import adapter_for

        adapter = adapter_for(p.get("kind") or "", browser_pool=browser_pool)
        # Two capabilities, not one: _validate_fixtures accepts exactly these
        # two keys and this function does nothing but dispatch to them, so a
        # single "fixtures" capability would need an undefined precedence
        # against its own constituents. A kind that supports one and not the
        # other gets the one it supports.
        if dialog_policy and isinstance(adapter, SupportsDialogPolicy):
            await adapter.set_dialog_policy(p["instance_id"], dialog_policy)
        if mock_routes and isinstance(adapter, SupportsMockRoutes):
            await adapter.install_mock_routes(p["instance_id"], list(mock_routes))

    await _asyncio.gather(*(_apply(p) for p in live.participants))


async def _run_startup_macros(browser_pool: Any, live: LiveScenario) -> None:
    import asyncio as _asyncio

    from octowright.scenario_kinds import adapter_for
    from octowright.scenarios import resolve_startup_macros

    failures: list[dict[str, str]] = []

    async def _run_for_participant(participant_dict: dict[str, Any], participant_spec: Any) -> None:
        adapter = adapter_for(participant_dict.get("kind") or "", browser_pool=browser_pool)
        if not isinstance(adapter, SupportsMacros):
            return  # a kind with no run_macro has no startup macros to run (validation also forbids declaring them)
        for macro_name in resolve_startup_macros(participant_spec):
            try:
                await adapter.run_macro(participant_dict["instance_id"], name=macro_name, args={})
            except Exception as e:
                log.warning(
                    "scenario.startup_macro_failed",
                    scenario_id=live.scenario_id,
                    persona=participant_dict["persona"],
                    macro=macro_name,
                    error=repr(e),
                )
                failures.append(
                    {
                        "instance_id": participant_dict["instance_id"],
                        "persona": str(participant_dict["persona"]),
                        "macro": macro_name,
                        "error": repr(e),
                    }
                )

    await _asyncio.gather(
        *(_run_for_participant(pd, ps) for pd, ps in zip(live.participants, live.spec.participants, strict=True))
    )
    if failures:
        raise RuntimeError(f"startup macro failures: {failures}")
