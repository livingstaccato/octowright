# Dashboard

`octowright serve` boots two things in one process: the MCP stdio server (what
your client talks to) and a Starlette HTTP server on `http://127.0.0.1:6286/` (what
*you* look at). One stable URL, pinned in your browser, replaces the old dance
of copying log paths and shelling out to `npx playwright show-trace` by hand.

Ask your MCP client `"give me the octowright dashboard URL"` (it'll call the
`octowright_dashboard_url` MCP tool), or just open the URL directly.

## What you get

- **Top-level dashboard** — every live browser, every live scenario, recent
  closed sessions, all your personas, all your saved macros. Auto-refreshes
  every 5 seconds.
- **Persona management** — each persona card shows engine list, last-used
  time, and on-disk size (chromium + firefox + webkit + yaml). Hover the
  card and click the edit (✎) icon to open an in-page YAML editor; save
  writes back to `<persona>/profile.yaml` via `PUT /api/personas/{name}`.
  Disk sizes are loaded lazily after first paint via
  `GET /api/personas/sizes` (a single directory-size scan over Octowright's
  profile config dir).
- **Closed-session cleanup** — closed-session rows expose an `⊗` delete
  button on hover; clicking removes the JSONL recording, video, trace, and
  screenshots from disk via `DELETE /api/sessions/{id}/recording`. Live
  sessions reject the call with 409 (close them first).
- **Per-session debugger** — click any session for a two-column page with the
  live browser preview or embedded session video on the left, action timeline
  on the right. Live previews stream JPEG frames over one WebSocket at
  `/api/sessions/{id}/screencast`, with bounded screenshot polling as a
  compatibility fallback.
  Controls include pause/resume plus fullscreen in panel or native browser
  mode. Click any action in the timeline to seek the video to that moment.
  Tabs underneath the timeline switch between **console messages** (filtered
  by level), **downloads** (with a "missing" badge if the file was moved),
  **markdown export**, and **screenshots** (lazy-loaded thumbnail grid).
- **Live updates** — for currently-running sessions, the page opens a
  WebSocket to `/api/sessions/{id}/tail` and appends new events as they
  arrive (no manual refresh). WebSocket frame payloads that are binary are
  intentionally hidden in the UI preview as `[binary payload hidden]`. Full frames
  are still cached to the websocket cache using base64 for safe replay and
  debugging.
- **Trace deep-dive** — a button on each session page spawns
  `npx playwright show-trace` against that session's `.zip` trace, opening
  the official Playwright trace viewer for full per-action inspection
  (network, snapshots, source links). Requires `npx` on PATH.

The markdown tab uses the `GET /api/sessions/{id}/markdown` endpoint; the
server captures cached markdown on page load and user navigation, and generates
it on demand if a live session hasn't populated the cache yet.

## Implementation

The dashboard is a TypeScript SPA built into `packages/octowright-frontend/`
(Vite + strict tsc + Biome + vitest). It uses `@provide-io/telemetry` for structured
logging so frontend log lines are correlated with the Python server's
`provide.telemetry` calls (see [telemetry.md](telemetry.md)). The compiled bundle ships inside the wheel; the
frontend has zero runtime dependency on Node — Node is only needed at build
time and for the optional `npx playwright show-trace` deep-dive.

## Port and remote access

If port 6286 is taken, the server walks up to 5 higher ports and picks the
first free one (or logs a warning and continues without the HTTP layer if all
are busy — MCP keeps running). Override the default with `OCTOWRIGHT_HTTP_PORT`
or bind to a different host with `OCTOWRIGHT_HTTP_HOST` (default `127.0.0.1`).
Binding to `0.0.0.0` only exposes health/static assets by default; sensitive
dashboard, API, and MCP access from another machine also requires
`OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1`. Only enable that opt-in on trusted
networks because it exposes live browser state, recordings, traces, downloads,
and the MCP tool surface.

## Related

- [getting-started.md](getting-started.md) — install and first run.
- [../README.md#capability-profiles](../README.md#capability-profiles) — slimming the MCP tool surface doesn't affect the dashboard.
- [troubleshooting.md](troubleshooting.md) — common failure modes.
