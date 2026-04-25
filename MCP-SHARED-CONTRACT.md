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
WS   /api/sessions/{id}/tail                   → server pushes {"events": [...], "cursor": int, "complete": bool} every ~1s for live sessions; sends one final message + closes for closed sessions
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
```

## Closed sessions

For v1, octowright doesn't persist a session registry — sessions live in
`pool._sessions` and disappear on close. The HTTP server treats "closed
sessions" as JSONL files in `RECORDINGS_DIR/` whose `instance_id` is not in
`pool._sessions`. Filename layout: `<stamp>-<kind>-<instance_id>[-<label>].jsonl`.
The first `launch` event in the recording supplies `started_at` / `kind` /
`profile` / `label` / `url`.

## WebSocket `/tail` semantics

- Live session: `_tail_jsonl(log_path, cursor)` is polled every ~1s; server
  pushes `{"events": [...], "cursor": int, "complete": false}` on each tick
  (even when `events` is empty) so the frontend can show liveness.
- Session goes from live to closed mid-connection: send one final message with
  `complete: true` and close the socket.
- Session was never live (or already closed) at connect time: send one snapshot
  `{"events": [...], "cursor": int, "complete": true}` and close.

## `/api/sessions/{id}/frame` caching

Extracted frames are cached at `RECORDINGS_DIR/.frame-cache/{id}/{t:.3f}.png`
so repeated requests for the same timestamp don't re-shell out to ffmpeg.
