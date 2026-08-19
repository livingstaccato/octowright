# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Session-collection endpoints: list / launch / detail / close / navigate."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import octowright.http.state as state
from octowright.browser_pool.errors import ProtectedBrowserCloseError
from octowright.browser_pool.options import LaunchOptions
from octowright.dashboard_events import publish_dashboard_invalidation
from octowright.defaults import SUPPORTED_KINDS, get_default_url
from octowright.http.artifacts import _build_cache_components
from octowright.http.discovery import (
    _closed_sessions,
    _find_recording_for,
    _live_session_or_none,
    _live_summary,
    _live_summary_from_launch,
    _resolve_artifact_path,
    _summarise_recording,
)
from octowright.http.exposure import guard_sensitive_http
from octowright.http.routes._common import _dashboard_operation_timeout_seconds, _parse_bool, _read_json_body
from octowright.http.session_artifacts import session_artifact_cache
from octowright.session.aria_redaction import aria_snapshot as redacted_aria_snapshot
from octowright.session.operation_gate import SessionBusyTimeoutError, SessionClosedError, SessionClosingError
from octowright.session.screencast_config import screencast_config_block
from octowright.terminal.errors import ProtectedTerminalCloseError


async def list_sessions(_request: Request) -> JSONResponse:
    pool = state.pool
    live = [_live_summary(s) for s in pool.iter_sessions()]
    # Terminal sessions live in a separate pool that only exists when the
    # optional `octowright[terminal]` extra is installed. `_live_summary` is
    # getattr-defensive, so terminal sessions serialize through it cleanly.
    terminal_pool = state.terminal_pool
    if terminal_pool is not None:
        live += [_live_summary(s) for s in terminal_pool.iter_sessions()]
    live_paths = {s["log_path"] for s in live}
    closed = _closed_sessions(state.RECORDINGS_DIR, live_paths)
    return JSONResponse({"live": live, "closed": closed})


def _resolve_live_markdown_path(live: Any) -> str | None:
    live_markdown_path = _resolve_artifact_path(live.instance_id, "markdown_path")
    if live.markdown_path is not None:
        return str(live.markdown_path)
    if live_markdown_path is not None:
        return str(live_markdown_path)
    return None


def _build_live_session_detail(live: Any, markdown_path: str | None) -> dict[str, Any]:
    return {
        **_live_summary(live),
        "video_path": str(live.video_path) if live.video_path else None,
        "trace_path": str(live.trace_path) if live.trace_path else None,
        "markdown_path": markdown_path,
        "websocket_path": str(live.websocket_path) if getattr(live, "websocket_path", None) else None,
        "action_count": -1,  # unknown without re-reading the file
        "event_count": int(getattr(getattr(live, "recorder", None), "event_count", 0)),
        "console_count": int(getattr(live, "console_count", len(live.console))),
        "download_count": int(getattr(live, "download_count", len(live.downloads))),
        "page_count": int(getattr(live, "page_count", len(live.pages))),
        "screencast": screencast_config_block(),
        "cache": _build_cache_components(
            session_id=live.instance_id,
            jsonl_path=Path(live.log_path),
            markdown_path=Path(markdown_path)
            if markdown_path
            else (live.markdown_path if live.markdown_path else None),
            trace_path=Path(live.trace_path) if live.trace_path else None,
            video_path=Path(live.video_path) if live.video_path else None,
            websocket_path=live.websocket_path if getattr(live, "websocket_path", None) else None,
        ),
    }


def _attach_macro_intent(detail: dict[str, Any], log_path: Path) -> None:
    from octowright.macros import load_macro_from_recording
    from octowright.macros.semantic import get_semantic_intent

    try:
        actions = load_macro_from_recording(log_path)
        detail["macro_intent"] = get_semantic_intent(actions)
    except Exception as exc:
        # The intent string is purely informational on the dashboard. If
        # the JSONL is half-written or the semantic resolver hits an
        # unknown action shape, drop the field and surface the reason at
        # debug level instead of failing the request.
        state.log.debug(
            "octowright.http.macro_intent_failed",
            log_path=str(log_path),
            error=repr(exc),
        )


async def _live_session_detail_response(live: Any) -> JSONResponse:
    markdown_path = _resolve_live_markdown_path(live)
    detail = _build_live_session_detail(live, markdown_path)
    log_path = Path(live.log_path)
    detail["action_count"] = int(getattr(getattr(live, "recorder", None), "action_count", 0))
    timeout = _dashboard_operation_timeout_seconds()
    try:
        async with live.operation("dashboard_session_detail", wait_timeout_seconds=timeout):
            detail["aria"] = await redacted_aria_snapshot(live, live.page.locator("html"))
    except Exception as exc:
        state.log.debug(
            "octowright.http.live_aria_snapshot_failed",
            instance_id=getattr(live, "instance_id", None),
            error=repr(exc),
        )
    if log_path.exists():
        _attach_macro_intent(detail, log_path)
    return JSONResponse(detail)


def _closed_session_detail_response(sid: str) -> JSONResponse:
    jsonl = _find_recording_for(sid, state.RECORDINGS_DIR)
    if jsonl is None:
        return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
    summary = _summarise_recording(jsonl)
    if summary is None:
        return JSONResponse({"error": f"could not parse recording for id {sid!r}"}, status_code=404)
    artefacts = session_artifact_cache.scan_artifacts(jsonl)
    detail = {
        **summary,
        "video_path": artefacts["video_path"],
        "trace_path": artefacts["trace_path"],
        "markdown_path": artefacts["markdown_path"],
        "websocket_path": artefacts["websocket_path"],
        "event_count": artefacts["event_count"],
        "action_count": artefacts["action_count"],
        "console_count": artefacts["console_count"],
        "download_count": artefacts["download_count"],
        "page_count": artefacts["page_count"],
        "cache": session_artifact_cache.cache_report(jsonl),
    }
    if artefacts["url"]:
        detail["url"] = artefacts["url"]

    _attach_macro_intent(detail, jsonl)

    return JSONResponse(detail)


def _terminal_session_detail(live: Any) -> dict[str, Any]:
    """Detail payload for a live terminal session.

    Terminal sessions have no page/console/download/video/trace artefacts, so
    we return the summary plus terminal-relevant fields rather than running the
    browser-only ``_build_live_session_detail`` (which reads ``live.page`` etc.).
    """
    return {
        **_live_summary(live),
        "connector_type": getattr(live, "connector_type", None),
        "video_path": None,
        "trace_path": None,
        "markdown_path": None,
        "websocket_path": None,
        "action_count": int(getattr(getattr(live, "recorder", None), "action_count", 0)),
    }


async def session_detail(request: Request) -> JSONResponse:
    sid = request.path_params["id"]
    # Terminal sessions are browser-shaped only in the summary; short-circuit
    # before the browser-only detail builder.
    terminal_pool = state.terminal_pool
    if terminal_pool is not None:
        term = terminal_pool.maybe_get(sid)
        if term is not None:
            return JSONResponse(_terminal_session_detail(term))
    live = _live_session_or_none(sid)
    if live is not None:
        return await _live_session_detail_response(live)
    return _closed_session_detail_response(sid)


# ---------------------------------------------------------------------------
# Write endpoints — sessions (launch / close / navigate)
# ---------------------------------------------------------------------------


async def session_launch(request: Request) -> JSONResponse:
    """POST /api/sessions — launch a new browser session via ``pool.launch(...)``.

    Returns a 201 with the SessionSummary shape used by ``GET /api/sessions``.
    """
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    kind = payload.get("kind")
    if not kind:
        return JSONResponse(
            {"error": "kind is required (one of chromium/firefox/webkit)"},
            status_code=400,
        )
    if kind not in SUPPORTED_KINDS:
        return JSONResponse(
            {"error": f"kind must be one of {list(SUPPORTED_KINDS)}, got {kind!r}"},
            status_code=400,
        )

    # Funnel through LaunchOptions.from_mapping so the HTTP body shape stays
    # in lock-step with the MCP browser_launch surface — a new launch field
    # is one edit in options.py, not three call sites.
    launch_kwargs = LaunchOptions.from_mapping(
        {**payload, "kind": kind, "url": payload.get("url") or get_default_url()}
    ).to_pool_kwargs()

    pool = state.pool
    try:
        result = await pool.launch(**launch_kwargs)
    except ValueError as e:
        # pool.launch validates `kind`; surface that as 400 even though we
        # already pre-checked, so we stay safe if SUPPORTED_KINDS drifts.
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        state.log.exception(
            "octowright.http.session_launch_failed",
            kind=kind,
            url=launch_kwargs["url"],
        )
        return JSONResponse({"error": f"launch failed: {e}"}, status_code=500)

    summary = _live_summary_from_launch(result)
    state.log.info(
        "octowright.http.session_launched",
        instance_id=result["instance_id"],
        kind=result["kind"],
        url=result.get("url"),
        record_video=launch_kwargs["record_video"],
        trace=launch_kwargs["trace"],
    )
    await publish_dashboard_invalidation("sessions")
    return JSONResponse(summary, status_code=201)


async def _maybe_close_terminal(sid: str, *, force: bool) -> JSONResponse | None:
    """Close ``sid`` if it is a live terminal session, else return ``None``.

    Returns ``None`` when terminals are unavailable (core install) or ``sid`` is
    not a terminal, so the caller falls through to the browser-pool path. A
    protected terminal without ``force`` maps to 409, mirroring the browser path.
    """
    terminal_pool = state.terminal_pool
    if terminal_pool is None or terminal_pool.maybe_get(sid) is None:
        return None
    try:
        await terminal_pool.close(sid, force=force)
    except ProtectedTerminalCloseError as e:
        return JSONResponse({"error": str(e).replace("force=True", "force=true")}, status_code=409)
    state.log.info("octowright.http.terminal_session_closed", instance_id=sid)
    await publish_dashboard_invalidation("sessions")
    return JSONResponse({"closed": True, "instance_id": sid})


async def _close_browser_session(sid: str, *, force: bool) -> JSONResponse:
    """Close a live browser session and warm its close-time artefact cache.

    404 if no live browser session holds ``sid`` (closed sessions on disk cannot
    be re-closed); 409 for a protected session without ``force``; 400 on a
    validation error; 500 on unexpected failure.
    """
    pool = state.pool
    try:
        result = await pool.close(sid, force=force)
    except KeyError:
        # A live session with sid may still be draining in pool._closing_sessions
        # (removed from pool._sessions once the coordinator's ticket is admitted,
        # well before teardown finishes) -- pool.close() itself coalesces onto
        # that in-flight coordinator and returns normally. Only a genuinely
        # unknown/fully-closed id reaches this branch.
        return JSONResponse(
            {"error": f"no live session with id {sid!r}; closed sessions cannot be re-closed"},
            status_code=404,
        )
    except ProtectedBrowserCloseError as e:
        return JSONResponse({"error": str(e).replace("force=True", "force=true")}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        state.log.exception("octowright.http.session_close_failed", instance_id=sid)
        return JSONResponse({"error": f"close failed: {e}"}, status_code=500)

    body: dict[str, Any] = {"closed": True, "instance_id": sid, **result}
    log_path = result.get("log_path")
    if isinstance(log_path, str) and log_path:
        try:
            jsonl_path = Path(log_path)
            # Run the JSONL walk + sidecar write in a worker thread so a
            # multi-MB recording doesn't block the event loop (other
            # dashboard requests and SSE/WS streams would otherwise stall).
            # The cache's LRU OrderedDicts are guarded by ``self._lock``
            # inside ``warm_close``, so concurrent event-loop readers
            # touching the same caches won't race with the warmup.
            body["cache"] = await asyncio.to_thread(session_artifact_cache.warm_close, jsonl_path)
        except Exception as e:
            # User-action swallow path: log the cause so the failure mode
            # is diagnosable (AGENTS.md silent-swallow policy).
            state.log.warning(
                "octowright.http.session_close_cache_report_failed",
                instance_id=sid,
                log_path=log_path,
                error=repr(e),
            )

    state.log.info("octowright.http.session_closed", instance_id=sid)
    await publish_dashboard_invalidation("sessions")
    return JSONResponse(body)


async def session_close(request: Request) -> JSONResponse:
    """DELETE /api/sessions/{id} — close a live session (browser or terminal)."""
    sid = request.path_params["id"]
    raw_force = request.query_params.get("force")
    force = False
    if raw_force is not None:
        parsed_force = _parse_bool(raw_force)
        if parsed_force is None:
            return JSONResponse({"error": f"invalid force={raw_force!r}, must be bool"}, status_code=400)
        force = parsed_force
    # Terminal sessions live in a separate (optional) pool. Close them here too
    # so the dashboard's close button works uniformly — without this, DELETE on a
    # visible terminal 404s because its id isn't in the browser pool.
    terminal_close = await _maybe_close_terminal(sid, force=force)
    if terminal_close is not None:
        return terminal_close
    return await _close_browser_session(sid, force=force)


async def session_navigate(request: Request) -> JSONResponse:
    """POST /api/sessions/{id}/navigate — drive the live session's page to ``url``."""
    sid = request.path_params["id"]
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        return JSONResponse({"error": "url is required and must be a non-empty string"}, status_code=400)

    pool = state.pool
    if not pool.has_session(sid):
        return JSONResponse({"error": f"no live session with id {sid!r}"}, status_code=404)
    try:
        # In the try: a mid-drain session passes has_session but pool.get raises -- 409, not 500.
        session = pool.get(sid)
        await session.navigate(url)
    except ValueError as e:
        # Bad input (e.g. disallowed url scheme) — 400, not 500.
        return JSONResponse({"error": str(e)}, status_code=400)
    except (SessionClosingError, SessionClosedError) as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except SessionBusyTimeoutError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        state.log.exception("octowright.http.session_navigate_failed", instance_id=sid, url=url)
        return JSONResponse({"error": f"navigate failed: {e}"}, status_code=500)
    state.log.info("octowright.http.session_navigated", instance_id=sid, url=url)
    return JSONResponse({"ok": True, "url": url})


async def session_selector_validate(request: Request) -> JSONResponse:
    """POST /api/sessions/{id}/selector/validate — check a CSS selector against a live page."""
    sid = request.path_params["id"]
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    selector = payload.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        return JSONResponse({"error": "selector is required and must be a non-empty string"}, status_code=400)

    pool = state.pool
    if not pool.has_session(sid):
        return JSONResponse({"error": f"no live session with id {sid!r}"}, status_code=404)
    timeout = _dashboard_operation_timeout_seconds()
    try:
        session = pool.get(sid)  # in the try for session_navigate's reason: 409, not 500
        async with session.operation("dashboard_selector_validate", wait_timeout_seconds=timeout):
            count = await session.page.locator(selector).count()
    except (SessionClosingError, SessionClosedError) as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except SessionBusyTimeoutError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "selector": selector,
                "found": False,
                "count": 0,
                "error": str(e),
            },
            status_code=400,
        )
    return JSONResponse({"ok": True, "selector": selector, "found": count > 0, "count": count})


def routes() -> list[Route]:
    return [
        Route("/api/sessions", guard_sensitive_http(list_sessions), methods=["GET"]),
        Route("/api/sessions", guard_sensitive_http(session_launch), methods=["POST"]),
        Route(
            "/api/sessions/{id}",
            guard_sensitive_http(session_detail, side_effect_get=True),
            methods=["GET"],
        ),
        Route("/api/sessions/{id}", guard_sensitive_http(session_close), methods=["DELETE"]),
        Route("/api/sessions/{id}/navigate", guard_sensitive_http(session_navigate), methods=["POST"]),
        Route(
            "/api/sessions/{id}/selector/validate",
            guard_sensitive_http(session_selector_validate),
            methods=["POST"],
        ),
    ]
