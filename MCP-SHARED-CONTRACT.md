# octowright debugger — shared API contract

The Python `http_server.py` and the TypeScript `packages/octowright-frontend/`
implement the two sides of this contract. Both subagents must keep the wire
format here in lockstep.

## Mounts

- Static SPA: `/`
- API: `/api/*`
- WebSocket: `/api/sessions/{id}/tail`

## Endpoints

```
GET  /                                         → static index.html (dashboard)
GET  /sessions/{id}                            → static session.html (rewritten by frontend router)
GET  /api/sessions                             → {"live": [SessionSummary, ...], "closed": [SessionSummary, ...]}
GET  /api/sessions/{id}                        → SessionDetail
GET  /api/sessions/{id}/events?since=N         → {"events": [...], "cursor": int, "total_bytes": int, "complete": bool}
GET  /api/sessions/{id}/console?level=L&since=N → {"messages": [ConsoleMessage, ...], "cursor": int, "total": int}
GET  /api/sessions/{id}/downloads?since=N      → {"downloads": [DownloadRecord, ...], "cursor": int, "total": int}
WS   /api/sessions/{id}/tail                   → server pushes {"events": [...], "cursor": int, "complete": bool} every ~1s for LIVE sessions; closed/unknown sessions are rejected at connect time (see WS semantics below)
GET  /api/sessions/{id}/frame?t=<seconds>      → image/png bytes (extracted at the requested timestamp). 404 if no video for this session.
GET  /api/sessions/{id}/video                  → video bytes (HTTP range supported via FileResponse). 404 if missing.
GET  /api/sessions/{id}/trace                  → application/zip download. 404 if missing.
GET  /api/sessions/{id}/screenshots            → {"screenshots": [{"path": str, "filename": str, "ts": float, "size_bytes": int}, ...]}
GET  /api/sessions/{id}/screenshots/{filename} → image/png bytes
GET  /api/scenarios                            → {"live": [LiveScenario, ...]}
GET  /api/personas                             → [PersonaSummary, ...]
GET  /api/macros                               → [MacroSummary, ...]
POST /api/sessions/{id}/trace/open             → {"pid": int, "trace_path": str}
GET  /api/health                               → {"ok": true, "version": str}
```

## Type shapes

```python
SessionSummary = {
    "id": str,
    "kind": str,           # "chromium" | "firefox" | "webkit"
    "label": str | None,
    "profile": str | None,
    "url": str | None,
    "started_at": str,     # ISO8601 UTC
    "live": bool,
    "log_path": str,
}

SessionDetail = SessionSummary + {
    "video_path": str | None,
    "trace_path": str | None,
    "action_count": int,
    "console_count": int,
    "download_count": int,
    "page_count": int,
    "title": str | None,
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
  recording. Today the JSONL log captures ``download_saved`` rows but NOT
  console events — so a closed session's ``/console`` always returns an empty
  list. The endpoint reads ``action: "console"`` rows defensively, so adding
  console persistence later requires no API change.
- 404 is returned when the id matches neither a live session nor a recording
  on disk.

## Closed sessions

For v1, octowright doesn't persist a session registry — sessions live in
`pool._sessions` and disappear on close. The HTTP server treats "closed
sessions" as JSONL files in `RECORDINGS_DIR/` whose `instance_id` is not in
`pool._sessions`. Filename layout: `<stamp>-<kind>-<instance_id>[-<label>].jsonl`.
The first `launch` event in the recording supplies `started_at` / `kind` /
`profile` / `label` / `url`.

## WebSocket `/tail` semantics

`/tail` is for LIVE sessions only.

- **Live session**: `_tail_jsonl(log_path, cursor)` is polled every ~1s; the
  server pushes `{"events": [...], "cursor": int, "complete": false}` on each
  tick (even when `events` is empty) so the frontend can show liveness.
- **Live → closed mid-connection**: send one final message with
  `complete: true` and close the socket cleanly.
- **Closed at connect time** (the recording exists on disk but the session is
  not in `pool._sessions`): the WebSocket is closed IMMEDIATELY with code
  `1003` (unsupported) and reason
  `"closed sessions don't support tail; use GET /api/sessions/{id}/events instead"`.
  No payload is sent — clients should fall back to the REST `/events` endpoint.
- **Unknown at connect time** (no live session, no recording): close with code
  `1008` (policy violation) and reason `"no session with id <id>"`.

## `/api/sessions/{id}/frame` caching

Extracted frames are cached at `RECORDINGS_DIR/.frame-cache/{id}/{t:.3f}.png`
so repeated requests for the same timestamp don't re-shell out to ffmpeg.
