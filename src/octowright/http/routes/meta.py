# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Meta endpoints: personas / macros listings + persona YAML management."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml as _yaml
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import octowright.http.state as state
from octowright.defaults import PROFILES_DIR, SUPPORTED_KINDS
from octowright.http.dashboard_events import publish_dashboard_invalidation
from octowright.http.exposure import guard_sensitive_http
from octowright.http.routes._common import _read_json_body
from octowright.macros.lint import lint_macro
from octowright.personas import _slug as _persona_slug


def _resolve_persona_dir(name: str) -> Path | JSONResponse:
    """Map a path-param name to its on-disk profile dir, with containment.

    The route's ``{name}`` is URL-decoded by Starlette and may carry traversal
    payloads like ``%2E%2E``. Apply the slug regex to reject empty/dotted
    names, then verify the resolved candidate path stays inside the
    module-level ``PROFILES_DIR`` so a symlink can't escape the tree.

    Returns the resolved persona directory on success, or a ready-to-return
    ``JSONResponse`` describing the rejection.
    """
    try:
        slug = _persona_slug(name)
    except ValueError:
        return JSONResponse({"error": f"invalid persona name {name!r}"}, status_code=400)
    candidate = PROFILES_DIR / slug
    resolved = candidate.resolve()
    root = PROFILES_DIR.resolve()
    if resolved != root and root not in resolved.parents:
        return JSONResponse({"error": f"invalid persona name {name!r}"}, status_code=400)
    return candidate


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


async def macro_repair_preview_endpoint(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    try:
        preview = state._macros.repair_preview(name)
    except FileNotFoundError:
        return JSONResponse({"error": f"macro {name!r} not found"}, status_code=404)
    return JSONResponse(preview)


def _issue_payload(macro: dict[str, Any]) -> list[dict[str, Any]]:
    return [asdict(issue) for issue in lint_macro(macro)]


def _validation_body(macro: dict[str, Any]) -> dict[str, Any]:
    issues = _issue_payload(macro)
    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    return {
        "ok": error_count == 0,
        "issues": issues,
        "issue_count": len(issues),
        "error_count": error_count,
    }


async def macro_detail_endpoint(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    try:
        macro = state._macros.load_macro(name)
    except FileNotFoundError:
        return JSONResponse({"error": f"macro {name!r} not found"}, status_code=404)
    return JSONResponse(macro)


async def macro_validate_endpoint(request: Request) -> JSONResponse:
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    macro = payload.get("macro") if isinstance(payload, dict) else None
    if not isinstance(macro, dict):
        return JSONResponse({"error": "'macro' must be a JSON object"}, status_code=400)
    return JSONResponse(_validation_body(macro))


async def macro_update_endpoint(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    macro = payload.get("macro") if isinstance(payload, dict) else None
    if not isinstance(macro, dict):
        return JSONResponse({"error": "'macro' must be a JSON object"}, status_code=400)

    validation = _validation_body(macro)
    if validation["error_count"]:
        return JSONResponse({"error": "macro validation failed", **validation}, status_code=400)

    path = state._macros.write_macro(name=name, macro=macro)
    saved = state._macros.load_macro(name)
    await publish_dashboard_invalidation("macros")
    return JSONResponse({"ok": True, "name": name, "path": str(path), "macro": saved})


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
    resolved = _resolve_persona_dir(name)
    if isinstance(resolved, JSONResponse):
        return resolved
    yaml_path = resolved / "profile.yaml"
    if not yaml_path.exists():
        return JSONResponse({"error": f"persona {name!r} not found"}, status_code=404)

    yaml_text = yaml_path.read_text(encoding="utf-8")

    engine_bytes: dict[str, int] = {}
    for kind in SUPPORTED_KINDS:
        kind_dir = resolved / kind
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
    resolved = _resolve_persona_dir(name)
    if isinstance(resolved, JSONResponse):
        return resolved
    yaml_path = resolved / "profile.yaml"
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

    yaml_path.write_text(yaml_text, encoding="utf-8")
    await publish_dashboard_invalidation("personas")
    return JSONResponse({"ok": True, "name": name})


def routes() -> list[Route]:
    return [
        Route("/api/personas", guard_sensitive_http(list_personas_endpoint), methods=["GET"]),
        Route("/api/personas/sizes", guard_sensitive_http(persona_sizes_endpoint), methods=["GET"]),
        Route("/api/personas/{name}", guard_sensitive_http(persona_detail_endpoint), methods=["GET"]),
        Route("/api/personas/{name}", guard_sensitive_http(persona_update_endpoint), methods=["PUT"]),
        Route("/api/macros", guard_sensitive_http(list_macros_endpoint), methods=["GET"]),
        Route(
            "/api/macros/{name:path}/repair_preview",
            guard_sensitive_http(macro_repair_preview_endpoint),
            methods=["GET"],
        ),
        Route("/api/macros/{name:path}/validate", guard_sensitive_http(macro_validate_endpoint), methods=["POST"]),
        Route("/api/macros/{name:path}", guard_sensitive_http(macro_detail_endpoint), methods=["GET"]),
        Route("/api/macros/{name:path}", guard_sensitive_http(macro_update_endpoint), methods=["PUT"]),
    ]
