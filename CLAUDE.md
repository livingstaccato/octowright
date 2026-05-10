# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

## What This Project Is

**Octowright** is an MCP (Model Context Protocol) server that lets Claude Code drive multiple parallel Playwright browsers (Chromium, Firefox, WebKit) simultaneously. It records every browser action to JSONL, supports persistent browser profiles with saved login state, and includes a web dashboard for debugging/monitoring.

## Commands

```bash
# Install
make install              # uv sync --all-groups

# Test & quality
make test                 # pytest (no live browsers)
make lint                 # ruff/format/mypy/ty/bandit/codespell/SPDX/LOC/vulture/xenon/secrets
make format               # ruff format + ruff --fix
make typecheck            # mypy only
make ci                   # lint + test
make audit                # pip-audit against the dependency tree
make vulture              # dead-code scan (baseline-ratchet)
make xenon                # cyclomatic complexity (baseline-ratchet)
make secrets-scan         # detect-secrets vs .secrets.baseline
make mutmut               # opt-in mutation testing (slow)

# Run a single test
uv run pytest tests/path/to/test_file.py::test_name -v

# Frontend
cd packages/octowright-frontend && npm run build   # compile TypeScript → static files
cd packages/octowright-frontend && npm run test    # vitest

# Playwright browsers
uv run playwright install webkit firefox chromium

# CLI
uv run octowright serve          # start MCP + HTTP dashboard
uv run octowright selftest       # list MCP tools without a client
uv run octowright scenario list  # list loaded scenarios
uv run octowright persona list   # list saved personas (also: persona create/show/delete)
uv run octowright cleanup        # prune stale recordings + abandoned profiles
uv run octowright init           # scaffold a starter octowright project tree
uv run octowright skill          # install/inspect the using-octowright agent skill
uv run octowright takeover       # detect + disable competing Playwright MCP plugins
uv run octowright test           # run the JSONL-driven test suite (CI-friendly)
```

## Architecture

### Five Concepts

1. **Browser** — One Playwright instance (one engine, one window). Has `instance_id`, records to JSONL.
2. **Profile** — Persistent on-disk state (`~/.config/octowright/profiles/<persona>/<kind>/`). Survives close/relaunch.
3. **Persona** — Named identity (display name, default URL, credentials). Owns profiles across engines.
4. **Scenario** — Pre-declared group of personas launched together with roles (`player`/`monitor`/`spectator`), fixtures, and verify macros for testing.
5. **Dashboard** — Starlette web UI showing live browsers, recordings, session debugger with embedded video + action timeline.

### Layer Map

```
CLI (Click)
  └─ serve.py → leader-election via lockfile
      ├─ MCP server (FastMCP, stdio transport)
      │   └─ server/browser/*.py   ← @mcp.tool decorated functions
      │   └─ server/macros.py
      │   └─ server/scenarios.py
      │   └─ server/personas.py
      │   └─ server/meta.py
      └─ HTTP server (Starlette)
          └─ http/routes/*.py      ← JSON/WebSocket endpoints
          └─ frontend/             ← built TypeScript SPA
```

**Singleton leader-election**: first `octowright serve` becomes leader (MCP stdio + HTTP + HTTP-MCP proxy at `/mcp`). Additional instances become followers that bridge stdin/stdout to leader's HTTP endpoint. Override with `--no-singleton`.

**Follower watchdog**: `proxy_bridge.run_proxy(..., health_url=...)` polls `GET /api/health` every 10s (timeout 5s) and tears the bridge down after 3 consecutive failures, so a dead leader can't hang followers indefinitely.

### Key Files

| Path | Role |
|------|------|
| `src/octowright/browser_pool/pool.py` | `BrowserPool` — top-level lifecycle entry points |
| `src/octowright/browser_pool/lifecycle.py` | Per-session launch / close / handoff logic |
| `src/octowright/browser_pool/listeners.py` | External-close eviction (context.close, browser.disconnected, page.close) |
| `src/octowright/browser_pool/options.py` | Launch-kwargs assembly + tile placement |
| `src/octowright/browser_pool/roster.py` | `browser_spawn_roster` parallel launch coordination |
| `src/octowright/browser_pool/launch_helpers.py` | Shared per-launch wiring (recorder, listeners, init scripts) |
| `src/octowright/browser_pool/errors.py` | Pool-specific exception types |
| `src/octowright/browser_pool/visuals.py` | Emoji badges, title injection, macro-status pill helpers |
| `src/octowright/browser_pool/_assets/*.js` | Init scripts injected into every page (title tag, corner badge, macro pill) |
| `src/octowright/session/core.py` | `BrowserSession` dataclass |
| `src/octowright/server/_state.py` | Shared singletons: `pool`, `mcp`, `scenario_pool` |
| `src/octowright/server/browser/lifecycle.py` | MCP tools: `browser_launch`, `browser_close`, `browser_navigate` |
| `src/octowright/cli/serve.py` | Leader-election + server startup |
| `src/octowright/http/app.py` | Starlette app factory |
| `src/octowright/macros/` (package) | Record → save → replay pipeline; `execution.py` runs macros, `storage.py` reads/writes JSON, `runtime.py` dispatches actions |
| `src/octowright/scenarios.py` | `Scenario`/`Participant` models + YAML/Python loaders |
| `src/octowright/personas.py` | Persona metadata + credential resolution |
| `src/octowright/resolve.py` | `suggest_for_url()` — persona ranking by URL |
| `src/octowright/defaults.py` | All env-var-driven defaults (port, paths, timeouts) |
| `tools/octowright_demos/` | **Out-of-wheel** demo-bundle generation (catalog, indexer, runtime, exports). Imported by `scripts/demos/*` and `tests/test_demos_*`; not part of the shipped package. |
| `demo/bundles/` | Source-of-truth demo bundles (`demo.yaml` + recorded artifacts). Tracked in git. Re-recording requires browser sessions. |
| `demo/tutorial-export/` | **Derived; gitignored.** Verbatim mirror of `demo/bundles/.../artifacts/` plus generated JSON manifests, consumed by `site-octowright-com`'s sync workflow. Regenerate with `make export-demos` (no browsers needed — just `shutil.copytree` + JSON writes). |
| `docs/architecture/MCP-SHARED-CONTRACT.md` | HTTP API spec (endpoints, request/response shapes) |
| `docs/architecture/` | PlantUML diagrams (render with `make diagrams`) |

### JSONL Recording

Every browser action is appended as a JSON object `{ts, action, ...fields}` to a `.jsonl` file per session. JSONL is:
- **Streamed live** via WebSocket `/api/sessions/{id}/tail`
- **Exported** to standalone Python/TS scripts via `export.py`
- **Replayed** as a macro via `macros/execution.py`
- **Diffed** as golden accessibility-tree snapshots via `server/goldens.py`

### MCP Tool Registration

Tools are `@mcp.tool`-decorated async functions in `server/browser/`, `server/macros.py`, `server/personas.py`, `server/scenarios.py`, `server/goldens.py`, and `server/meta.py`. The `mcp` singleton lives in `server/_state.py` and is imported by each submodule. Adding a new tool: decorate a function with `@mcp.tool` in the appropriate submodule — no manual registration needed.

### Macro Status Pill

Every page launched by the pool gets a faint translucent overlay at the bottom-center. While `run_macro` is dispatching, the pill shows the per-browser ID chip (matches the corner-badge color), a live elapsed counter, and the current action description. After completion the pill stays visible with `done` / `failed`; the next macro's `start` push resets the counter. Holding **Alt** makes the pill clickable — click opens a themed modal with the full per-push run history. The pill is `pointer-events: none` by default so it never intercepts page clicks.

Pass `slowmo_ms=N` to `macro_run` / `macro_run_sequence` (or set `OCTOWRIGHT_MACRO_SLOWMO_MS`) to insert a per-action delay between status push and dispatch — useful for following execution by eye.

### Silent-swallow policy

Bandit's B110 (`try/except/pass`) and B112 (`try/except/continue`) are blanket-suppressed in `make lint`. Production code uses these patterns only in:

- Process shutdown paths (signal-handler restore, task-cancel await)
- Dir scans skipping orphans (profile cleanup, recording cleanup)
- JSONL/YAML parse-skip on per-line malformed input (recorder, macro list, persona list)
- Best-effort I/O during teardown

Silent swallow in **user-action paths** must `log.warning` or `log.debug` instead of truly swallowing. The bandit suppression assumes the swallow is intentional, not an excuse to hide failures from the user.

### Idle Watchdog

After the pool sits empty for `OCTOWRIGHT_IDLE_GRACE` seconds (default 300s), the daemon auto-exits. Use `--keep-alive` to disable or `--idle-grace <seconds>` to override.

### Frontend

TypeScript SPA in `packages/octowright-frontend/`. Built files land in `src/octowright/server/frontend/`. The dashboard auto-polls `/api/sessions` and uses WebSockets for live event streaming. Types in `src/types.ts` mirror the Python Pydantic/dataclass models.

### Capability Profiles

The full MCP tool surface is ~89 tools. When the LLM only needs a subset, set `OCTOWRIGHT_PROFILE` (or pass `--profile=...` to `octowright serve`) to one or more comma-separated profile names from `src/octowright/server/profiles.py`. Tools not listed in any active profile are skipped at `@mcp.tool` decoration time, so the LLM-visible schema shrinks accordingly. Profile names available today: `core` (minimal browser-driving surface, 13 tools), `advanced` (inspection + assertions + ARIA-locator interactions), `macros`, `scenarios`, `personas`. Unset / `all` keeps every tool (default, back-compat). The five named profiles together cover 55 distinct tools — the remaining 34 (snapshots, a handful of less-common views, etc.) only register when no filter is set, so `--profile=core,advanced,macros,scenarios,personas` is **not** equivalent to no filter. Example: `octowright serve --profile=core,macros` exposes 25 tools instead of 89.

**Always-on meta tools.** Three diagnostic/meta tools are exempt from the profile filter and register under any profile (or no profile): `octowright_status`, `octowright_dashboard_url`, `octowright_check_takeover`. These give the LLM a way to inspect the active profile, find the dashboard URL, and detect competing MCP plugins regardless of filter. The list is `ALWAYS_ON_TOOLS` in `src/octowright/server/profiles.py`.

## Env Var Configuration

All defaults are in `src/octowright/defaults.py`. Key vars:
- `OCTOWRIGHT_HTTP_PORT` — HTTP dashboard port (default 8765, auto-bumps if busy)
- `OCTOWRIGHT_HTTP_HOST` — HTTP dashboard bind host (default 127.0.0.1)
- `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD` — set to `1` to allow non-loopback access to sensitive dashboard/MCP endpoints. **Warning:** there is no auth layer; combining a non-loopback `OCTOWRIGHT_HTTP_HOST` with this flag exposes RCE-equivalent surface (the MCP transport drives browsers) to the network. Use only behind your own auth gateway.
- `OCTOWRIGHT_HEADLESS` — force headless mode
- `OCTOWRIGHT_IDLE_GRACE` — seconds before auto-exit (default 300)
- `OCTOWRIGHT_PROFILES_DIR` — override profile storage root
- `OCTOWRIGHT_MACROS_DIR` — override macro JSON storage root
- `OCTOWRIGHT_MACRO_SLOWMO_MS` — default per-action delay during macro replay (0 disables)
- `OCTOWRIGHT_PROFILE` — comma-separated capability-profile names to slim the LLM tool surface; unset or `all` registers everything (see "Capability Profiles" above)
- `OCTOWRIGHT_TAIL_POLL_SECONDS` / `OCTOWRIGHT_TAIL_HEARTBEAT_SECONDS` — WS `/tail` poll interval and quiet-stream keepalive cadence (defaults 1.0 / 15.0)
- `OCTOWRIGHT_DASHBOARD_DISCONNECT_POLL_SECONDS` / `OCTOWRIGHT_DASHBOARD_HEARTBEAT_SECONDS` — SSE `/api/dashboard/events` disconnect-detection cadence and keepalive interval (defaults 0.05 / 15.0)
