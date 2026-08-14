# octowright debugger — shared API contract

The Python `http_server.py` and the TypeScript `packages/octowright-frontend/`
implement the two sides of this contract. Both subagents must keep the wire
format here in lockstep.

## Mounts

- Static SPA: `/`
- API: `/api/*`
- WebSocket: `/api/sessions/{id}/tail`
- WebSocket: `/api/sessions/{id}/screencast`

## Endpoints

```
GET    /                                         → static index.html (dashboard)
GET    /sessions/{id}                            → static session.html (rewritten by frontend router)
GET    /api/sessions                             → {"live": [SessionSummary, ...], "closed": [SessionSummary, ...]}
POST   /api/sessions                             → SessionSummary (201) — launch a new browser session
GET    /api/sessions/{id}                        → SessionDetail
DELETE /api/sessions/{id}                        → SessionCloseResponse (200); 404 if not in live pool.  
  SessionCloseResponse: {"closed": true, "instance_id": str, "log_path": str, "video_path": str|null, "trace_path": str|null, "cache": CacheReport}
DELETE /api/sessions/{id}/recording              → {"deleted": true, "session_id": str, "files_removed": int} (200); 404 if no recording on disk; 409 if the session is still live
POST   /api/sessions/{id}/navigate               → {"ok": true, "url": str} (200); 400 if url missing/empty; 404 if not live; 409 if the session's operation gate is closing/closed; 503 if the gate is busy past OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS (this route does not use the shorter dashboard timeout)
POST   /api/sessions/{id}/selector/validate      → {"ok": true, "count": int} (200) — CSS selector match count against the live page, bounded by OCTOWRIGHT_DASHBOARD_OPERATION_TIMEOUT_SECONDS. 400 if selector missing/empty; 404 if not live; 409 if the session's operation gate is closing/closed; 503 if the gate is busy past the dashboard timeout
POST   /api/sessions/{id}/relaunch                → SessionSummary (201) for a NEW instance_id launched with the same kind/profile/label/url/viewport as the original. 404 if no recording on disk; 409 if the session is still live; 422 if the JSONL has no parseable launch record.
GET    /api/sessions/{id}/events?since=N         → {"events": [...], "cursor": int, "total_bytes": int, "complete": bool}
GET    /api/sessions/{id}/console?level=L&since=N → {"messages": [ConsoleMessage, ...], "cursor": int, "total": int}
GET    /api/sessions/{id}/downloads?since=N      → {"downloads": [DownloadRecord, ...], "cursor": int, "total": int}
WS     /api/sessions/{id}/tail                   → server pushes {"events": [...], "cursor": int, "complete": bool} every ~1s for LIVE sessions; closed/unknown sessions are rejected at connect time (see WS semantics below)
WS     /api/sessions/{id}/screencast?fps=N       → binary JPEG frames for LIVE browser sessions. Requested fps is clamped to the configured backend cap; closed/unknown sessions are rejected at connect time (see WS semantics below)
GET    /api/sessions/{id}/frame?t=<seconds>      → image/png bytes (extracted at the requested timestamp). 404 if no video for this session.
GET    /api/sessions/{id}/video                  → video bytes (HTTP range supported via FileResponse). 404 if missing.
GET    /api/sessions/{id}/trace                  → application/zip download. 404 if missing.
GET    /api/sessions/{id}/markdown                → text/markdown bytes (the cached markdown rendering of the page). For LIVE sessions, transparently triggers `capture_markdown()` on first request if the cache is missing. 404 if no live session and no cached markdown on disk; 500 if generation fails.
GET    /api/sessions/{id}/screenshots            → {"screenshots": [{"path": str, "filename": str, "ts": float, "size_bytes": int}, ...]}
GET    /api/sessions/{id}/screenshots/{filename} → image/png bytes
GET    /api/sessions/{id}/screenshot/now?format=png|jpeg&quality=N&full_page=bool → image/png|jpeg bytes (live page only). Defaults: format=png, quality=80 (jpeg only), full_page=false. Cache-Control: no-store. 404 closed/unknown; 409 if the session's operation gate is closing/closed; 503 if the gate is busy past OCTOWRIGHT_DASHBOARD_OPERATION_TIMEOUT_SECONDS or page.screenshot() raises.
GET    /api/scenarios                            → {"live": [LiveScenario, ...]}
POST   /api/scenarios/{name}/start               → {"scenario_id": str, "name": str, "participants": [...]} (201). 404 if scenario not on disk; 400 validation; 500 if any participant fails to launch.
DELETE /api/scenarios/{id}                       → {"scenario_id": str, "teardown_errors": [...], "closed": [...]} (200). 404 if no live scenario with that id.
POST   /api/scenarios/{id}/run_macro             → {"scenario_id": str, "macro": str, "role": str|null, "targeted": int, "results": [...]} (200). 400 if macro missing; 404 if no live scenario.
GET    /api/personas                             → [PersonaSummary, ...]
GET    /api/personas/sizes                       → {<persona_name>: <bytes>, ...} — bulk `du -sk` over PROFILES_DIR/*; missing entries / scan failures yield `{}`
GET    /api/personas/{name}                      → PersonaDetail; 404 if no `profile.yaml` for that persona
PUT    /api/personas/{name}                      → {"ok": true, "name": str} (200); 400 if `yaml` field missing/non-string or fails `yaml.safe_load`; 404 if persona not found
GET    /api/macros                               → [MacroSummary, ...]
GET    /api/macros/{name:path}                   → MacroDetail (the full macro JSON: name, description, parameters, actions, created_at, updated_at). 404 if not found.
PUT    /api/macros/{name:path}                   → {"ok": true, "name": str} (200) on save. 400 if `macro` field missing/non-object or fails validation (response includes the validation issue list); 404 if not found.
GET    /api/macros/{name:path}/repair_preview    → {"original": [...], "repaired": [...], "diff": [...]} preview of auto-repair suggestions without applying them. 404 if not found.
POST   /api/macros/{name:path}/validate          → {"error_count": int, "warning_count": int, "issues": [LintIssue, ...]} for the supplied macro body. 400 if `macro` field missing/non-object.
POST   /api/sessions/{id}/trace/open             → {"pid": int, "trace_path": str}
GET    /api/health                               → {"ok": true, "version": str}
```

HTTP request metrics are not exposed as a scrape endpoint: they are recorded
through `provide.telemetry`'s `TelemetryMiddleware` (RED metrics
`http.requests.total` / `http.errors.total` / `http.request.duration_ms`,
attributed by route/method/status_code) and exported via OTLP. The
`OCTOWRIGHT_HTTP_METRICS` toggle (default on; `0`/`false`/`no`/`off` to disable;
tests patch `defaults.HTTP_METRICS_ENABLED`) gates metric recording only —
context propagation and log correlation stay on.

## MCP server-push notifications

Beyond request/response, the leader pushes JSON-RPC notifications to the connected
MCP client (forwarded through the follower bridge unchanged). They have no `id`
and require no reply.

```
notifications/octowright/session_closed   — a session left the pool
  params: { instance_id, kind, label, profile, reason, log_path }
  reason: "agent_close" | "user_close" | "external_disconnect" | "crashed" | "shutdown"

notifications/octowright/browser_crashed  — a crash was observed (page.on("crash"));
                                            the session may still be alive
  params: { instance_id, kind, label, profile, scope, log_path, hint }
  scope:  "renderer" | "process"
  hint:   actionable text ("reload it, or relaunch with browser_launch")
```

`browser_crashed` fires proactively the moment the crash is seen, so a client
learns the page died immediately rather than only on its next failing tool call;
the subsequent `session_closed` (if the session is evicted) carries
`reason="crashed"`.

## Write-endpoint request bodies

```
POST /api/sessions
  Content-Type: application/json
  {
    "kind": "chromium" | "firefox" | "webkit",  // required
    "url": str | null,                           // default: DEFAULT_URL
    "label": str | null,
    "profile": str | null,
    "viewport_w": int | null,
    "viewport_h": int | null,
    "headed": bool,                              // default: true
    "stabilize": bool,                           // default: false
    "record_video": bool,                        // default: false
    "trace": bool                                // default: false
  }
  → 201 + SessionSummary (identical shape to GET /api/sessions live[] entries)
  → 400 if `kind` is missing/invalid or body is malformed JSON (valid Content-Type, unparsable bytes)
  → 415 if Content-Type is not `application/json` (per RFC 7231)
  → 500 if `pool.launch()` raises an unexpected error

POST /api/sessions/{id}/navigate
  { "url": str }                                  // required, non-empty
  → 200 {"ok": true, "url": str}

POST /api/scenarios/{name}/start
  (no body required)
  → 201 {"scenario_id": str, "name": str, "participants": [...]}

POST /api/scenarios/{id}/run_macro
  { "macro": str, "role": str | null, "args": object | null }
  → 200 with the per-participant results dict from `scenario_pool.run_macro`

PUT /api/personas/{name}
  Content-Type: application/json
  { "yaml": str }                                  // required; full new contents of profile.yaml
  → 200 {"ok": true, "name": str}
  → 400 if "yaml" missing / not a string / not parsable by yaml.safe_load
  → 404 if no profile.yaml exists for that persona
```

All write endpoints accept an empty body as `{}`. Request body conventions:

- **Missing/wrong Content-Type for a non-empty body**: 415 Unsupported Media Type +
  `{"error": "content-type must be application/json for JSON request bodies"}`.
  A request with a non-empty body must declare `Content-Type: application/json`
  (per RFC 7231). Empty bodies bypass the Content-Type check and decode to `{}`.
- **Malformed JSON** (valid Content-Type, unparsable body): 400 Bad Request +
  `{"error": "invalid JSON body: ..."}`.

## Type shapes

```python
SessionSummary = {
    "id": str,
    "kind": str,  # "chromium" | "firefox" | "webkit"
    "label": str | None,
    "profile": str | None,
    "url": str | None,
    "started_at": str,  # ISO8601 UTC
    "live": bool,
    "protected": bool,  # true if browser_close/close_all refuse this session without force=True
    "log_path": str,
    "operation_gate"?: OperationGateSnapshot,  # browser sessions only; absent for terminal sessions
}

OperationGateSnapshot = {
    "state": "open" | "closing" | "closed" | "broken",
    "active_operation": str | None,
    "active_for_ms": int | None,
    "queue_depth": int,
    "oldest_wait_ms": int | None,
    "queue_timeout_seconds": float,
}
```

The MCP `browser_launch` tool result (and `BrowserPool.launch()`'s return
dict generally, built by `build_launch_result`) additionally includes
`"protected_reason": str` beside `"protected"` — the reason the effective
`protected` value was chosen: `"explicit"` (caller passed `protected=`),
`"all_default"` (`OCTOWRIGHT_PROTECT_BROWSERS`), `"headed_default"` (headed +
non-ephemeral auto-protect), or `"unprotected"`. This reason is not currently
threaded through the HTTP `SessionSummary` shape above — only the plain
`protected` bool is.

```python
SessionDetail = SessionSummary + {
    "video_path": str | None,
    "trace_path": str | None,
    "markdown_path": str | None,
    "websocket_path": str | None,
    "event_count": int,
    "action_count": int,
    "console_count": int,
    "download_count": int,
    "page_count": int,
    "screencast"?: {
        "fps": int,
        "quality": int,
        "fullscreen_mode": "native" | "panel",
    },
    "cache": CacheReport,
    # Live browser sessions include "screencast". Live sessions also include
    # an "aria" ARIA-tree snapshot (str) and may include "macro_intent" (str).
    # Closed sessions may include "url" (str).
}

CacheReport = {
    "total_bytes": int,
    "total_human": str,
    "components": {
        "jsonl": CacheComponent,
        "markdown": CacheComponent,
        "trace": CacheComponent,
        "video": CacheComponent,
        "websocket": CacheComponent,
        "screenshots": CacheComponentList,
    },
    "recommendations": [str, ...],
}

CacheComponent = {
    "size_bytes": int,
    "size_human": str,
    "path": str | None,
    "exists": bool,
}

CacheComponentList = {
    "size_bytes": int,
    "size_human": str,
    "count": int,
    "paths": [str, ...],
}

LiveScenario = {
    "scenario_id": str,
    "name": str,
    "participants": [{"role": str, "persona": str, "kind": str, "instance_id": str}, ...],
}

PersonaSummary = {
    "name": str,
    "display_name": str | None,
    "engines": [str, ...],
    "last_used": str,
}

PersonaDetail = {
    "name": str,
    "yaml": str,                # raw profile.yaml contents
    "path": str,                # absolute path to profile.yaml
    "disk_bytes": int,          # profile.yaml + sum(engine_bytes)
    "engine_bytes": {str: int}, # per-engine on-disk byte count (only engines that exist on disk)
}

MacroSummary = {
    "name": str,
    "description": str | None,
    "parameters": [str, ...],
    "updated_at": str | None,
}

ConsoleMessage = {
    "level": str,           # "log" | "warn" | "error" | "info" | "debug" | …
    "text": str,
    "page_index": int | None,  # set on popup pages, None for the main page
}

DownloadRecord = {
    "url": str,
    "suggested_filename": str,
    "path": str,            # absolute path the file was saved to
    "timestamp": str,       # compact UTC stamp e.g. "20260101T000000Z"
    "path_exists": bool,    # server-checked at request time
}
```

## `/console` and `/downloads` cursor semantics

Both endpoints share the same shape: ``{<plural>: [...], "cursor": int, "total": int}``.

- ``since`` is an optional 0-based index into the messages/downloads list.
  Items at index ``>= since`` are returned. Out-of-range values are clamped
  into ``[0, total]``.
- ``cursor`` returned is always the new ``total`` so callers can pass it back
  on the next poll without tracking offsets manually.
- ``/console`` accepts an optional ``level=`` filter (case-sensitive match
  against ``ConsoleMessage.level``) — the filter applies BEFORE the ``since``
  slice, so the ``cursor``/``total`` values reflect only the filtered view.
- For LIVE sessions the data is read directly off the in-memory session
  (``BrowserSession.console`` and ``BrowserSession.list_downloads()``).
- For CLOSED sessions the data is reconstructed by scanning the JSONL
  recording. Console rows are stored as ``action: "console"`` entries and
  downloads are stored as ``action: "download_saved"`` entries.
- 404 is returned when the id matches neither a live session nor a recording
  on disk.

## Closed sessions

Live handles still live in `pool._sessions` and disappear on close. The HTTP
server treats "closed sessions" as JSONL files in `RECORDINGS_DIR/` whose
`instance_id` is not in the live pool. Filename layout:
`<stamp>-<kind>-<instance_id>[-<label>].jsonl`. The first `launch` event in
the recording supplies `started_at` / `kind` / `profile` / `label` / `url`.

Octowright also writes a lightweight `session-manifest.json` for crash
diagnostics. Graceful closes and external browser evictions remove entries.
If the daemon dies before cleanup, `octowright_status()` reports manifest
entries that are no longer present in the live pool as `stale_manifest_sessions`.
The manifest is diagnostic only; it is not a browser reattach registry.

## WebSocket `/tail` semantics

`/tail` is for LIVE sessions only.

- **Live session**: `_tail_jsonl(log_path, cursor)` is polled every
  ``TAIL_POLL_SECONDS`` (~1s). To avoid steady empty-frame churn across N
  idle dashboards, the server pushes
  `{"events": [...], "cursor": int, "complete": false}` only when
  `events` is non-empty OR a heartbeat tick fires. Heartbeat cadence is
  bounded by ``TAIL_HEARTBEAT_SECONDS`` (default 15s) so a quiet stream
  still produces an empty keepalive frame the client can use to detect a
  dead connection.
- **Live → closed mid-connection**: send one final message with
  `complete: true` and close the socket cleanly.
- **`?since=` query param**: byte cursor into the JSONL log. Non-int and
  negative values are silently coerced to `0` (the WS handshake doesn't
  expose 4xx; the REST `/events` sibling 400s on non-int but identically
  clamps negatives to `0`).
- **Closed at connect time** (the recording exists on disk but the session is
  not in `pool._sessions`): the WebSocket is closed IMMEDIATELY with code
  `1003` (unsupported) and reason
  `"closed sessions don't support tail; use GET /api/sessions/{id}/events instead"`.
  No payload is sent — clients should fall back to the REST `/events` endpoint.
- **Unknown at connect time** (no live session, no recording): close with code
  `1008` (policy violation) and reason `"no session with id <id>"`.
- **Payload asymmetry vs REST `/events`**: the WS frame is
  `{events, cursor, complete}` — it deliberately omits `total_bytes` (which
  REST returns) because the WS receiver is consuming a continuous stream
  and the file-size figure has no client-side use during the connection.
  Clients that need it should call REST `/events` once, then upgrade to WS
  using the returned cursor.

## WebSocket `/screencast` semantics

`/screencast` is for LIVE browser sessions only. It sends each frame as a
binary WebSocket message containing JPEG bytes; no JSON envelope or heartbeat
frame is emitted. The optional `?fps=N` request is clamped to `1..configured_cap`
(`OCTOWRIGHT_LIVE_SCREENCAST_FPS`, default `10`), and JPEG quality comes from
`OCTOWRIGHT_LIVE_SCREENCAST_QUALITY` (default `70`, clamped to `1..100`).

- **Live session**: the endpoint starts or joins that session's Playwright
  screencast producer and fans out JPEG frames until the client disconnects.
- **Closed or unknown at connect time**: close with code `1008` and reason
  `"no live session with id <id>"`.
- **Host/Origin policy failure**: close with code `1008`; reasons are
  `"remote dashboard access is disabled"` or
  `"cross-origin websocket handshake is blocked"`.
- **Screencast acquisition failure**: close with code `1011` and reason
  `"screencast unavailable; use fallback"`.

## `/api/sessions/{id}/frame` caching

Extracted frames are cached at `RECORDINGS_DIR/.frame-cache/{id}/{t:.3f}.png`
so repeated requests for the same timestamp don't re-shell out to ffmpeg.
