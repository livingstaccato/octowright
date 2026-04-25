# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Scenario endpoints: list / start / stop / run_macro."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...server import _state
from .. import state
from ._common import _read_json_body


async def list_scenarios(_request: Request) -> JSONResponse:
    spool = _state.scenario_pool
    return JSONResponse({"live": spool.list_live()})


async def scenario_start_endpoint(request: Request) -> JSONResponse:
    """POST /api/scenarios/{name}/start — launch a scenario by name.

    Mirrors the ``scenario_start`` MCP tool: returns
    ``{scenario_id, name, participants}`` on success. 404 if the name doesn't
    map to a scenario file on disk; 400 for validation errors; 500 if any
    participant browser fails to launch (with the spawn_roster error list).
    """
    name = request.path_params["name"]
    spool = _state.scenario_pool
    pool = _state.pool
    try:
        live = await spool.start(name=name, browser_pool=pool)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        # spawn_roster reports per-participant errors as "scenario X: N
        # participant(s) failed to launch: [...]" — surface that as 500.
        return JSONResponse({"error": str(e)}, status_code=500)
    except Exception as e:
        state.log.exception("octowright.http.scenario_start_failed", name=name)
        return JSONResponse({"error": f"scenario start failed: {e}"}, status_code=500)

    body = {
        "scenario_id": live.scenario_id,
        "name": live.name,
        "participants": live.participants,
    }
    state.log.info(
        "octowright.http.scenario_started",
        scenario_id=live.scenario_id,
        name=live.name,
        participants=len(live.participants),
    )
    return JSONResponse(body, status_code=201)


async def scenario_stop_endpoint(request: Request) -> JSONResponse:
    """DELETE /api/scenarios/{id} — stop a live scenario."""
    sid = request.path_params["id"]
    spool = _state.scenario_pool
    pool = _state.pool
    if sid not in spool._live:
        return JSONResponse(
            {"error": f"no live scenario with id {sid!r}"},
            status_code=404,
        )
    try:
        result = await spool.stop(scenario_id=sid, browser_pool=pool)
    except Exception as e:
        state.log.exception("octowright.http.scenario_stop_failed", scenario_id=sid)
        return JSONResponse({"error": f"scenario stop failed: {e}"}, status_code=500)
    state.log.info("octowright.http.scenario_stopped", scenario_id=sid)
    return JSONResponse(result)


async def scenario_run_macro_endpoint(request: Request) -> JSONResponse:
    """POST /api/scenarios/{id}/run_macro — broadcast a macro to a scenario."""
    sid = request.path_params["id"]
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    macro = payload.get("macro")
    if not isinstance(macro, str) or not macro.strip():
        return JSONResponse({"error": "macro is required and must be a non-empty string"}, status_code=400)

    role = payload.get("role")
    args = payload.get("args") or {}
    if not isinstance(args, dict):
        return JSONResponse({"error": "args must be a JSON object"}, status_code=400)

    spool = _state.scenario_pool
    pool = _state.pool
    if sid not in spool._live:
        return JSONResponse(
            {"error": f"no live scenario with id {sid!r}"},
            status_code=404,
        )
    try:
        result = await spool.run_macro(
            scenario_id=sid,
            macro=macro,
            browser_pool=pool,
            role=role,
            args=args,
        )
    except Exception as e:
        state.log.exception(
            "octowright.http.scenario_run_macro_failed",
            scenario_id=sid,
            macro=macro,
        )
        return JSONResponse({"error": f"run_macro failed: {e}"}, status_code=500)
    state.log.info(
        "octowright.http.scenario_macro_dispatched",
        scenario_id=sid,
        macro=macro,
        role=role,
    )
    return JSONResponse(result)


def routes() -> list[Route]:
    return [
        Route("/api/scenarios", list_scenarios, methods=["GET"]),
        Route("/api/scenarios/{name}/start", scenario_start_endpoint, methods=["POST"]),
        Route("/api/scenarios/{id}", scenario_stop_endpoint, methods=["DELETE"]),
        Route("/api/scenarios/{id}/run_macro", scenario_run_macro_endpoint, methods=["POST"]),
    ]
