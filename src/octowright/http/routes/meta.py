# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Meta endpoints: personas / macros listings + persona YAML management."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, cast

import yaml as _yaml
from starlette.requests import Request
from starlette.routing import Route

import octowright.http.state as state
from octowright._paths import atomic_write_text
from octowright.dashboard_events import publish_dashboard_invalidation
from octowright.defaults import PROFILES_DIR, SUPPORTED_KINDS
from octowright.http.exposure import guard_sensitive_http
from octowright.http.json_response import SafeJSONResponse
from octowright.http.routes._common import _read_json_body
from octowright.macros.lint import lint_macro
from octowright.personas import _slug as _persona_slug
from octowright.personas import _validate_persona_yaml_doc


def _resolve_persona_dir(name: str) -> Path | SafeJSONResponse:
    """Map a path-param name to its on-disk profile dir, with containment.

    The route's ``{name}`` is URL-decoded by Starlette and may carry traversal
    payloads like ``%2E%2E``. Apply the slug regex to reject empty/dotted
    names, then verify the resolved candidate path stays inside the
    module-level ``PROFILES_DIR`` so a symlink can't escape the tree.

    Returns the resolved persona directory on success, or a ready-to-return
    ``SafeJSONResponse`` describing the rejection.
    """
    try:
        slug = _persona_slug(name)
    except ValueError:
        return SafeJSONResponse({"error": f"invalid persona name {name!r}"}, status_code=400)
    candidate = PROFILES_DIR / slug
    resolved = candidate.resolve()
    root = PROFILES_DIR.resolve()
    if resolved != root and root not in resolved.parents:
        return SafeJSONResponse({"error": f"invalid persona name {name!r}"}, status_code=400)
    return candidate


async def list_personas_endpoint(_request: Request) -> SafeJSONResponse:
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
    return SafeJSONResponse(out)


async def list_macros_endpoint(_request: Request) -> SafeJSONResponse:
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
    return SafeJSONResponse(out)


async def macro_repair_preview_endpoint(request: Request) -> SafeJSONResponse:
    name = request.path_params["name"]
    try:
        preview = state._macros.repair_preview(name)
    except FileNotFoundError:
        return SafeJSONResponse({"error": f"macro {name!r} not found"}, status_code=404)
    return SafeJSONResponse(preview)


def _issue_payload(macro: dict[str, Any], previous: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [asdict(issue) for issue in lint_macro(macro, previous=previous)]


def _saved_version(name: str) -> dict[str, Any] | None:
    """The macro *name* on disk, for the sensitivity-shrink check; ``None`` if there is none to compare."""
    try:
        saved = state._macros.load_macro(name)
    except (FileNotFoundError, ValueError, OSError):
        return None
    return saved if isinstance(saved, dict) else None


def _validation_body(macro: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    issues = _issue_payload(macro, previous)
    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    # Non-error issues are surfaced separately so the dashboard can render
    # warnings without recomputing the split client-side; the MCP-SHARED
    # contract documents warning_count as a required field on this response.
    warning_count = sum(1 for issue in issues if issue["severity"] != "error")
    return {
        "ok": error_count == 0,
        "valid": error_count == 0,
        "issues": issues,
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
    }


def _macro_error(exc: Exception) -> SafeJSONResponse:
    """A macro read or write the caller can act on, instead of a bare 500.

    ``TimeoutError`` rather than ``MacroWriteLockTimeout`` (its subclass) so a
    test that reloads the storage module does not change what is caught.
    """
    status = 503 if isinstance(exc, TimeoutError) else 400
    return SafeJSONResponse({"error": str(exc)}, status_code=status)


async def macro_detail_endpoint(request: Request) -> SafeJSONResponse:
    name = request.path_params["name"]
    try:
        macro = state._macros.load_macro(name)
    except FileNotFoundError:
        return SafeJSONResponse({"error": f"macro {name!r} not found"}, status_code=404)
    except ValueError as exc:  # a name outside the macros dir, an unreadable file
        return _macro_error(exc)
    return SafeJSONResponse(macro)


async def macro_validate_endpoint(request: Request) -> SafeJSONResponse:
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    macro = payload.get("macro") if isinstance(payload, dict) else None
    if not isinstance(macro, dict):
        return SafeJSONResponse({"error": "'macro' must be a JSON object"}, status_code=400)
    previous = await asyncio.to_thread(_saved_version, request.path_params["name"])
    return SafeJSONResponse(_validation_body(macro, previous))


async def macro_update_endpoint(request: Request) -> SafeJSONResponse:
    name = request.path_params["name"]
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    macro = payload.get("macro") if isinstance(payload, dict) else None
    if not isinstance(macro, dict):
        return SafeJSONResponse({"error": "'macro' must be a JSON object"}, status_code=400)

    previous = await asyncio.to_thread(_saved_version, name)
    validation = _validation_body(macro, previous)
    if validation["error_count"]:
        return SafeJSONResponse({"error": "macro validation failed", **validation}, status_code=400)

    # Off the event loop: the write waits on the macro write lock and does file I/O.
    try:
        path = await asyncio.to_thread(state._macros.write_macro, name=name, macro=macro)
        saved = await asyncio.to_thread(state._macros.load_macro, name)
    except (ValueError, TimeoutError) as exc:  # a refused name or collision; the write lock held too long
        return _macro_error(exc)
    await publish_dashboard_invalidation("macros")
    return SafeJSONResponse({"ok": True, "name": name, "path": str(path), "macro": saved})


async def persona_sizes_endpoint(_request: Request) -> SafeJSONResponse:
    """GET /api/personas/sizes — bulk disk-size scan via du.

    ``du`` can take several seconds on a populous profile root, so the call
    runs on a worker thread to keep the event loop responsive — otherwise
    every concurrent HTTP request, WebSocket JSONL tail, and SSE dashboard
    event stalls for the duration of the scan (up to the 15 s timeout).
    """
    if not PROFILES_DIR.exists():
        return SafeJSONResponse({})
    entries = [e for e in PROFILES_DIR.iterdir() if e.is_dir()]
    if not entries:
        return SafeJSONResponse({})
    try:
        # text=True makes subprocess.run return CompletedProcess[str], but
        # asyncio.to_thread can't propagate that overload narrowing through
        # the call wrapper — cast back to keep the str-typed CompletedProcess.
        result = cast(
            "CompletedProcess[str]",
            await asyncio.to_thread(
                state.subprocess.run,
                ["du", "-sk", "--", *(str(e) for e in entries)],
                capture_output=True,
                text=True,
                timeout=15,
            ),
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
        return SafeJSONResponse(sizes)
    except Exception as e:
        state.log.warning("persona_sizes.failed", error=str(e))
        return SafeJSONResponse({})


def _persona_engine_bytes(persona_dir: Path) -> dict[str, int]:
    """Bytes on disk per engine profile under ``persona_dir``. Blocking."""
    engine_bytes: dict[str, int] = {}
    for kind in SUPPORTED_KINDS:
        kind_dir = persona_dir / kind
        if kind_dir.exists():
            try:
                engine_bytes[kind] = sum(f.stat().st_size for f in kind_dir.rglob("*") if f.is_file())
            except OSError:
                pass  # a file vanished mid-walk (a live browser's cache); size is best-effort
    return engine_bytes


async def persona_detail_endpoint(request: Request) -> SafeJSONResponse:
    """GET /api/personas/{name} — YAML content + per-engine disk usage."""
    name = request.path_params["name"]
    resolved = _resolve_persona_dir(name)
    if isinstance(resolved, SafeJSONResponse):
        return resolved
    yaml_path = resolved / "profile.yaml"
    if not yaml_path.exists():
        return SafeJSONResponse({"error": f"persona {name!r} not found"}, status_code=404)

    yaml_text = yaml_path.read_text(encoding="utf-8")

    # A persistent profile can hold tens of thousands of cache files: walk it
    # in a worker thread, as persona_sizes_endpoint runs du, or every MCP call
    # and dashboard stream on the leader stalls for the length of the walk.
    engine_bytes = await asyncio.to_thread(_persona_engine_bytes, resolved)

    profile_bytes = yaml_path.stat().st_size
    total_bytes = profile_bytes + sum(engine_bytes.values())

    return SafeJSONResponse(
        {
            "name": name,
            "yaml": yaml_text,
            "path": str(yaml_path),
            "disk_bytes": total_bytes,
            "engine_bytes": engine_bytes,
        }
    )


async def persona_update_endpoint(request: Request) -> SafeJSONResponse:
    """PUT /api/personas/{name} — update persona YAML."""
    name = request.path_params["name"]
    resolved = _resolve_persona_dir(name)
    if isinstance(resolved, SafeJSONResponse):
        return resolved
    yaml_path = resolved / "profile.yaml"
    if not yaml_path.exists():
        return SafeJSONResponse({"error": f"persona {name!r} not found"}, status_code=404)

    payload, err = await _read_json_body(request)
    if err is not None:
        return err

    yaml_text = payload.get("yaml", "")
    if not isinstance(yaml_text, str):
        return SafeJSONResponse({"error": "'yaml' must be a string"}, status_code=400)

    try:
        parsed = _yaml.safe_load(yaml_text)
    except _yaml.YAMLError as e:
        return SafeJSONResponse({"error": f"invalid YAML: {e}"}, status_code=400)

    # Validate the document against the Persona schema BEFORE writing. When
    # OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1, any HTTP client can hit this
    # endpoint, and persona YAML drives credential-resolver argv. Catching
    # unknown keys / wrong types here keeps the on-disk persona files
    # well-formed and refuses junk that resolve_credential would later
    # mis-handle.
    try:
        _validate_persona_yaml_doc(parsed)
    except ValueError as e:
        return SafeJSONResponse({"error": f"invalid persona YAML: {e}"}, status_code=400)

    atomic_write_text(yaml_path, yaml_text, encoding="utf-8")
    await publish_dashboard_invalidation("personas")
    return SafeJSONResponse({"ok": True, "name": name})


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
