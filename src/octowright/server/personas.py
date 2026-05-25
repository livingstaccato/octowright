# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Persona + profile + migration tools."""

from __future__ import annotations

from typing import Any

from octowright import engine_profiles as profile_mod
from octowright import personas as persona_mod
from octowright.dashboard_events import publish_dashboard_invalidation_nowait
from octowright.server._state import log, mcp, pool
from octowright.types import CredentialCheckReport, PersonaListEntry


@mcp.tool(structured_output=False, description="List saved browser profiles. Pass kind to filter to one engine.")
def profile_list(kind: str | None = None) -> list[dict[str, Any]]:
    return profile_mod.list_profiles(kind)


@mcp.tool(
    structured_output=False,
    description=(
        "Delete a saved browser profile (wipes all cookies, localStorage, IndexedDB, and "
        "saved logins for that profile). Refuses if a live instance is using it."
    ),
)
def profile_delete(kind: str, name: str) -> dict[str, Any]:
    if pool.profile_in_use(kind, name):
        live_ids = [s["instance_id"] for s in pool.list_sessions() if s["kind"] == kind and s["profile"] == name]
        log.warning("octowright.profile.delete_refused", kind=kind, profile=name, reason="in_use")
        raise RuntimeError(
            f"profile {kind}/{name} is in use by live browser(s) {live_ids}; "
            f"close with `browser_close instance_id={live_ids[0]!r}` (or browser_close_all) first"
        )
    path = profile_mod.delete_profile(kind, name)
    log.info("octowright.profile.deleted", kind=kind, profile=name, path=str(path))
    publish_dashboard_invalidation_nowait("personas")
    return {"deleted": True, "path": str(path)}


@mcp.tool(
    structured_output=False,
    description=(
        "List all personas, each with their known engines, display name, and last-used timestamp. "
        "A persona is a named identity (e.g. 'dante') that owns engine-specific browser profiles."
    ),
)
def persona_list() -> list[PersonaListEntry]:
    return persona_mod.list_personas()


@mcp.tool(
    structured_output=False,
    description=(
        "Return the full profile.yaml for a persona. Credentials are returned as their "
        "reference entries (e.g. {'email_env': 'DANTE_EMAIL'}), not resolved secrets. "
        "Raises if the persona doesn't exist."
    ),
)
def persona_get(name: str) -> dict[str, Any]:
    p = persona_mod.load_persona(name)
    return {
        "name": p.name,
        "display_name": p.display_name,
        "default_url": p.default_url,
        "default_macros": p.default_macros,
        "credentials": p.credentials,
        "app": p.app,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Scaffold a new persona directory with a stub profile.yaml. Does nothing engine-specific; "
        "browser profiles are created on first browser_launch with this persona."
    ),
)
def persona_create(
    name: str,
    display_name: str | None = None,
    default_url: str | None = None,
) -> dict[str, Any]:
    try:
        pdir = persona_mod.create_persona(
            name,
            display_name=display_name,
            default_url=default_url,
        )
    except FileExistsError as e:
        raise RuntimeError(str(e)) from e
    publish_dashboard_invalidation_nowait("personas")
    return {"created": True, "name": name, "path": str(pdir)}


@mcp.tool(
    structured_output=False,
    description=(
        "Delete an entire persona (metadata + all engine profiles). Refuses if any engine "
        "profile is currently in use by a live browser."
    ),
)
def persona_delete(name: str) -> dict[str, Any]:
    for s in pool.list_sessions():
        if s["profile"] == name:
            raise RuntimeError(
                f"persona {name!r} is in use by live instance {s['instance_id']}; "
                f"close with `browser_close instance_id={s['instance_id']!r}` first"
            )
    path = profile_mod.delete_persona(name)
    log.info("octowright.persona.deleted", name=name, path=str(path))
    publish_dashboard_invalidation_nowait("personas")
    return {"deleted": True, "name": name, "path": str(path)}


@mcp.tool(
    structured_output=False,
    description=(
        "Pre-flight check that every credential reference in a persona's profile.yaml can "
        "actually be resolved — before you launch a browser and discover the secret is "
        "missing mid-flow. Returns {persona, checked, ok, summary}: `checked` is one entry "
        "per declared credential with {name, source ('env'|'cmd'), reference, ok, error}; "
        "the resolved secret value is NEVER included. Use this before any scenario whose "
        "startup_macros need credentials (e.g. a discord-login macro)."
    ),
)
async def persona_credentials_check(name: str) -> CredentialCheckReport:
    # check_credentials shells out to each persona's credential helper
    # (e.g. `op read ...`). FastMCP runs sync tools directly on the event
    # loop, so a 30s `op` call would stall every live browser, every WS
    # heartbeat, and every JSONL write. Push the synchronous helper to a
    # worker thread to keep the loop responsive.
    import asyncio

    persona = persona_mod.load_persona(name)
    return await asyncio.to_thread(persona_mod.check_credentials, persona)
