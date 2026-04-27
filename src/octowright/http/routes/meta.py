# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Meta endpoints: personas / macros listings + persona YAML management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml as _yaml
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...defaults import PROFILES_DIR, SUPPORTED_KINDS
from .. import state
from ._common import _read_json_body


async def list_personas_endpoint(_request: Request) -> JSONResponse:
    rows = state._personas.list_personas()
    out = [
        {
            "name": r["name"],
            "display_name": r.get("display_name"),
            "engines": r.get("engines", []),
            "last_used": r.get("last_used"),
        }
        for r in rows
    ]
    return JSONResponse(out)


async def list_macros_endpoint(_request: Request) -> JSONResponse:
    rows = state._macros.list_macros()
    out = [
        {
            "name": r["name"],
            "description": r.get("description"),
            "parameters": r.get("parameters", []),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
    ]
    return JSONResponse(out)


async def persona_sizes_endpoint(_request: Request) -> JSONResponse:
    """GET /api/personas/sizes — bulk disk-size scan via du."""
    if not PROFILES_DIR.exists():
        return JSONResponse({})
    entries = [e for e in PROFILES_DIR.iterdir() if e.is_dir()]
    if not entries:
        return JSONResponse({})
    try:
        result = state.subprocess.run(
            ["du", "-sk"] + [str(e) for e in entries],
            capture_output=True,
            text=True,
            timeout=15,
        )
        sizes: dict[str, Any] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                try:
                    sizes[Path(parts[1]).name] = int(parts[0]) * 1024
                except (ValueError, OSError):
                    pass
        return JSONResponse(sizes)
    except Exception as e:
        state.log.warning("persona_sizes.failed", error=str(e))
        return JSONResponse({})


async def persona_detail_endpoint(request: Request) -> JSONResponse:
    """GET /api/personas/{name} — YAML content + per-engine disk usage."""
    name = request.path_params["name"]
    p_dir = PROFILES_DIR / name
    yaml_path = p_dir / "profile.yaml"
    if not yaml_path.exists():
        return JSONResponse({"error": f"persona {name!r} not found"}, status_code=404)

    yaml_text = yaml_path.read_text()

    engine_bytes: dict[str, int] = {}
    for kind in SUPPORTED_KINDS:
        kind_dir = p_dir / kind
        if kind_dir.exists():
            try:
                engine_bytes[kind] = sum(f.stat().st_size for f in kind_dir.rglob("*") if f.is_file())
            except OSError:
                pass

    profile_bytes = yaml_path.stat().st_size
    total_bytes = profile_bytes + sum(engine_bytes.values())

    return JSONResponse(
        {
            "name": name,
            "yaml": yaml_text,
            "path": str(yaml_path),
            "disk_bytes": total_bytes,
            "engine_bytes": engine_bytes,
        }
    )


async def persona_update_endpoint(request: Request) -> JSONResponse:
    """PUT /api/personas/{name} — update persona YAML."""
    name = request.path_params["name"]
    p_dir = PROFILES_DIR / name
    yaml_path = p_dir / "profile.yaml"
    if not yaml_path.exists():
        return JSONResponse({"error": f"persona {name!r} not found"}, status_code=404)

    payload, err = await _read_json_body(request)
    if err is not None:
        return err

    yaml_text = payload.get("yaml", "")
    if not isinstance(yaml_text, str):
        return JSONResponse({"error": "'yaml' must be a string"}, status_code=400)

    try:
        _yaml.safe_load(yaml_text)
    except _yaml.YAMLError as e:
        return JSONResponse({"error": f"invalid YAML: {e}"}, status_code=400)

    yaml_path.write_text(yaml_text)
    return JSONResponse({"ok": True, "name": name})


def routes() -> list[Route]:
    return [
        Route("/api/personas", list_personas_endpoint, methods=["GET"]),
        Route("/api/personas/sizes", persona_sizes_endpoint, methods=["GET"]),
        Route("/api/personas/{name}", persona_detail_endpoint, methods=["GET"]),
        Route("/api/personas/{name}", persona_update_endpoint, methods=["PUT"]),
        Route("/api/macros", list_macros_endpoint, methods=["GET"]),
    ]
