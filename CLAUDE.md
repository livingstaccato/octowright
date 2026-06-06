# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

## What This Project Is

**Octowright** is an MCP (Model Context Protocol) server that lets agentic coding clients drive multiple parallel Playwright browsers (Chromium, Firefox, WebKit) simultaneously. It records every browser action to JSONL, supports persistent browser profiles with saved login state, and includes a web dashboard for debugging/monitoring.

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
uv run octowright restart        # stop the daemon, reap orphans, start a fresh one
uv run octowright selftest       # list MCP tools without a client
uv run octowright scenario list  # list loaded scenarios
uv run octowright persona list   # list saved personas (also: persona create/show/delete)
uv run octowright cleanup        # prune stale recordings + abandoned profiles
uv run octowright init           # scaffold a starter octowright project tree
uv run octowright skill          # install/inspect the octowright agent skill
uv run octowright takeover       # detect + disable competing Playwright MCP plugins
uv run octowright test           # run the JSONL-driven test suite (CI-friendly)
```

## Architecture

### Five Concepts

1. **Browser** — One Playwright instance (one engine, one window). Has `instance_id`, records to JSONL.
2. **Profile** — Persistent on-disk state (`~/.config/octowright/profiles/<persona>/<kind>/`). Survives close/relaunch.
3. **Persona** — Named identity (display name, default URL, credentials). Owns profiles across engines.
4. **Scenario** — Pre-declared group of personas launched together with roles, fixtures, and verify macros for testing. Canonical roles are `player`/`monitor`/`spectator`; additional domain-specific roles are also in use (`main-site`, `recorder`, `replayer`, `form`, `counter`, `arithmetic` — see `examples/scenarios/` and `demo/bundles/`). `scenarios._validate_scenario` logs `scenario.unknown_role` on any role outside the canonical set so typos surface in logs without blocking custom role vocabularies.
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

**Follower bridge reliability**: `proxy_bridge.run_proxy(..., health_url=...)` delegates to a supervised bridge. The local stdio follower stays alive while the remote HTTP-MCP leader session is disposable. If the leader stream closes, hangs, or times out, in-flight calls get explicit JSON-RPC bridge errors and later calls reconnect to the current lockfile leader URL. Bridge health snapshots are written to `OCTOWRIGHT_BRIDGE_STATE` and included in `octowright_status()["bridge"]`. `resolve_leader_url` rejects any leader URL whose host is not loopback — any same-user process can overwrite the lockfile, so without this check a hostile local process could redirect MCP traffic (including persona credentials substituted into tool args) to an attacker URL. Opt out with `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1`, the same flag the HTTP layer gates non-loopback binds on.

**Disk-write containment**: every path the daemon writes that flows from an LLM-supplied or recording-supplied string is anchored under `defaults.RECORDINGS_DIR`. `browser_export_script`'s `out_path`, `browser_screenshot`'s output path, and the HAR path recovered by `LaunchOptions.from_launch_record` are all resolved-and-contained against `RECORDINGS_DIR` (symlinks resolved before the prefix check); a poisoned JSONL launch record can't redirect HAR writes anywhere on disk, and an LLM can't escape the recordings root via `..` or symlinks. `recorder.new_log_path` likewise sanitizes the operator-supplied label before it joins the base dir.

**Transport recovery**: If an Octowright MCP call returns `Transport closed` or times out, first check daemon health with `curl http://127.0.0.1:6286/api/health`. If health is good, retry one Octowright MCP call; the follower bridge should fail fast and reconnect for the next call. If the same client handle still fails, run `uv run --active python scripts/bridge_reconnect_smoke.py` to distinguish a broken client handle from a broken daemon. Do not run `octowright restart` unless daemon health fails or the user explicitly asks for a restart.

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
| `src/octowright/macros/` (package) | Record → save → replay pipeline; `execution.py` runs macros, `storage.py` reads/writes JSON, `runtime.py` dispatches actions, `semantic.py` summarizes recordings into human-readable digests (pure helpers, no MCP-tool registry dep — the `@mcp.tool macro_explain` wrapper lives in `server/macros.py`) |
| `src/octowright/dashboard_events.py` | Pure in-process pub/sub for dashboard SSE/WS fanout; lives at the package root so `server/` MCP-tool modules don't have to reach up into the `http/` layer |
| `src/octowright/scenarios.py` | `Scenario`/`Participant` models + YAML/Python loaders |
| `src/octowright/personas.py` | Persona metadata + credential resolution |
| `src/octowright/resolve.py` | `suggest_for_url()` — persona ranking by URL |
| `src/octowright/defaults.py` | All env-var-driven defaults (port, paths, timeouts). `get_default_url()` resolves the actual bound port at runtime; `get_default_label()` derives username/repo from CWD + git. |
| `src/octowright/http/routes/new_tab.py` | `GET /new-tab` — default landing page for `browser_launch` with no URL. Serves Otto logo, wordmark, live status strip (version, commit, uptime, browser count). Time-based background tint. `GET /otto.svg`. |
| `.octowright/config.yaml` | Per-project config file (project root or any parent). Supports `label:`, `persona:`, `profile:`. Read by `get_default_label()` / `browser_launch` at daemon startup. `octowright init` scaffolds a starter copy. |
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

The full MCP tool surface is 113 tools. When the LLM only needs a subset, set `OCTOWRIGHT_PROFILE` (or pass `--profile=...` to `octowright serve`) to one or more comma-separated profile names from `src/octowright/server/profiles.py`. Tools not listed in any active profile are skipped at `@mcp.tool` decoration time, so the LLM-visible schema shrinks accordingly. Profile names available today: `core` (minimal browser-driving surface), `advanced` (inspection + cached captures + assertions + viewport controls + ARIA-locator interactions), `macros`, `scenarios`, `goldens` (accessibility-tree snapshot save/diff/verify), `personas`. Unset / `all` keeps every tool (default, back-compat). The six named profiles together cover the profile-scoped tools plus 7 always-on meta/Advisor tools — the remaining tools (snapshots, a handful of less-common views, etc.) only register when no filter is set, so `--profile=core,advanced,macros,scenarios,goldens,personas` is **not** equivalent to no filter. Authoritative tool counts live in `src/octowright/server/profiles.py`.

**Always-on meta and Advisor tools.** Seven diagnostic/guidance tools are exempt from the profile filter and register under any profile (or no profile): `octowright_status`, `octowright_storage_report`, `octowright_dashboard_url`, `octowright_check_takeover`, `octowright_advisor_status`, `octowright_advisor_set_preference`, and `octowright_advisor_record_macro_observation`. These give the LLM a way to inspect the active profile, inspect storage paths, find the dashboard URL, detect competing MCP plugins, and surface local Advisor guidance regardless of filter. The list is `ALWAYS_ON_TOOLS` in `src/octowright/server/profiles.py`.

### Protected close behavior

`protected=True` marks a browser as user-owned. Close-capable tools must refuse protected browsers unless the caller explicitly passes `force=True`. This applies to `browser_close`, `browser_close_all`, and `browser_capture_and_close`; the capture-and-close tool checks protection before taking screenshots or snapshots so a refused call has no capture side effects. Internal rollback/teardown paths that are recovering from errors use `force=True` intentionally.

### Octowright Advisor

Octowright Advisor is local and deterministic. It records bounded MCP tool-usage summaries and explicit repeated-workflow observations, then returns suggestions in `octowright_status` and `octowright_advisor_status`. Agents should inspect the `advisor` block after first-touch status. When an agent notices the same manual workflow repeating, call `octowright_advisor_record_macro_observation(source="llm", signature=..., summary=...)`; two matching signatures produce a `macro_candidate` suggestion. Advisor never auto-saves macros — macro candidates remain prompt-only even when the preference is `automatic`. Use `octowright_advisor_set_preference` to persist `yes` / `no` / `automatic` preferences for `macro_candidate` and `profile_change`.

## Env Var Configuration

All defaults are in `src/octowright/defaults.py`. Key vars:
- `OCTOWRIGHT_HTTP_PORT` — HTTP dashboard port (default 6286, auto-bumps if busy)
- `OCTOWRIGHT_HTTP_HOST` — HTTP dashboard bind host (default 127.0.0.1)
- `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD` — set to `1` to allow non-loopback access to sensitive dashboard/MCP endpoints. **Warning:** there is no auth layer; combining a non-loopback `OCTOWRIGHT_HTTP_HOST` with this flag exposes RCE-equivalent surface (the MCP transport drives browsers) to the network. Use only behind your own auth gateway.
- `OCTOWRIGHT_DEFAULT_URL` — override the URL opened on `browser_launch` with no `url` argument (default resolves from the bound port at runtime; always points at `/new-tab` on the local daemon)
- `OCTOWRIGHT_DEFAULT_LABEL` — override the auto-detected default browser label (see `.octowright/config.yaml` for per-project configuration)
- `OCTOWRIGHT_BADGE_OPACITY` — corner badge opacity (float 0.0–1.0, default 0.35). Lower = more translucent.
- `OCTOWRIGHT_HEADLESS` — force headless mode
- `OCTOWRIGHT_IDLE_GRACE` — seconds before auto-exit (default 300)
- `OCTOWRIGHT_PROFILES_DIR` — override profile storage root
- `OCTOWRIGHT_MACROS_DIR` — override macro JSON storage root
- `OCTOWRIGHT_ADVISOR_STATE` — override the local Advisor state JSON path (preferences, bounded tool usage, macro observations)
- `OCTOWRIGHT_MACRO_SLOWMO_MS` — default per-action delay during macro replay (0 disables)
- `OCTOWRIGHT_PROFILE` — comma-separated capability-profile names to slim the LLM tool surface; unset or `all` registers everything (see "Capability Profiles" above)
- `OCTOWRIGHT_TAIL_POLL_SECONDS` / `OCTOWRIGHT_TAIL_HEARTBEAT_SECONDS` — WS `/tail` poll interval and quiet-stream keepalive cadence (defaults 1.0 / 15.0)
- `OCTOWRIGHT_DASHBOARD_DISCONNECT_POLL_SECONDS` / `OCTOWRIGHT_DASHBOARD_HEARTBEAT_SECONDS` — SSE `/api/dashboard/events` disconnect-detection cadence and keepalive interval (defaults 0.05 / 15.0)
- `OCTOWRIGHT_REDACT_INPUTS` — record-time scrubbing of user-typed values (`type_text` / `fill`) in the per-session JSONL stream. `off` records the literal value (legacy, leaks secrets to anyone reading `/api/sessions/{id}/events`), `passwords` (DEFAULT) replaces values typed into `<input type="password">` — *and* `<input type="text">` carrying `autocomplete=current-password`, `new-password`, or `one-time-code` (the SPA-custom-password-input case) — with `<redacted:password>` while the page still receives the real value, `all` redacts every typed/filled value regardless of element type. This is the record-time companion to the save-time `macros/lint.py` credential check — the linter only fires when an operator saves a recording as a macro, so unless this is set the JSONL on disk still contains the cleartext password.

## Telemetry (OpenTelemetry)

Tracing and metrics are emitted via `provide.telemetry`. Logs are always structured; spans and metrics are emitted ONLY when explicitly enabled — the noop tracer/meter is the default so there's no cost when not in use. Exports use OTLP, so any OTel-compatible backend works: an OTel Collector that fans out to LGTM/Tempo, OpenObserve, Honeycomb, Datadog (OTLP), Jaeger, Grafana Cloud, SigNoz, etc. The codebase does not name a specific backend.

### Spans

Span names follow the `octowright.<area>.<verb>` convention. The list below is alphabetized for stability — order has no semantic meaning. Per-span attributes vary; only the attributes actually set at the span call site are listed (callers may add more via `set_attrs` mid-span).

| Span | Attributes | Emitted by |
|------|------------|------------|
| `octowright.bridge.forward_rpc` | `method`, `request_id` | `proxy_supervisor.forward_rpc` (follower leg) |
| `octowright.browser.handoff` | `old_instance_id`, `kind`, `headed`, `close_original`, `accept_stateless` | `browser_pool/lifecycle.handoff_browser` |
| `octowright.browser.launch` | `kind` | `browser_pool/_metrics.launch_span` (wraps `pool.launch`) |
| `octowright.browser.relaunch_fluid` | `instance_id`, `kind` | `browser_pool/pool.relaunch_fluid` |
| `octowright.browser.spawn_roster` | `roster_size` | `browser_pool/roster.browser_spawn_roster` |
| `octowright.macro.action` | `action`, `instance_id` | `macros/runtime.dispatch_simple` |
| `octowright.macro.run` | `macro`, `instance_id`, `kind` | `macros/execution.run_macro` |
| `octowright.macro.run_sequence` | `names_count`, `stop_on_failure` | `macros/execution.run_sequence` |
| `octowright.mcp.request` | `method`, `path` | `_trace_propagation.TraceContextExtractionMiddleware` (leader leg, ends on `http.response.start`) |
| `octowright.scenario.run_macro` | `scenario_id`, `macro`, `role`, `targeted` | `scenarios_pool.ScenarioPool.run_macro` |
| `octowright.scenario.start` | `scenario_id`, `scenario_name`, `participants` | `scenarios_pool.ScenarioPool.start` |
| `octowright.session.close` | `instance_id`, `kind` | `session/core_ops_mixin.SessionOpsMixin.close` |
| `octowright.session.navigate` | `instance_id`, `kind`, `url` | `session/core_page_mixin.SessionPageMixin.navigate` |

`macro.action` spans nest under their `macro.run` parent, which (when invoked from `macro_run_sequence`) nests under `macro.run_sequence`, so a multi-step macro run renders as a clean tree.

### Trace context propagation across the bridge

The follower→leader chain is glued together by the W3C `traceparent` header. On the follower side, `proxy_supervisor.forward_rpc` opens its `octowright.bridge.forward_rpc` span and hands the underlying MCP `streamablehttp_client` an httpx factory from `_trace_propagation.tracing_httpx_client_factory`; that factory registers a per-request hook (`_inject_traceparent_hook`) that calls the OTel propagator to inject `traceparent` (and `tracestate`) into every outgoing HTTP request. On the leader side, `_trace_propagation.TraceContextExtractionMiddleware` runs as ASGI middleware in front of the HTTP-MCP app: it extracts the propagated context from request headers, attaches it via `opentelemetry.context.attach`, then opens the per-request `octowright.mcp.request` span. Any spans started while the leader handles the request — including spans inside `@mcp.tool` handlers like `browser.launch` or `macro.run` — chain under the follower's `bridge.forward_rpc` span. The `mcp.request` span ends as soon as `http.response.start` is sent (not on body completion) to avoid filling the OTel batch-exporter buffer with long-lived SSE streams.

### Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `octowright_browser_launched_total` | counter | `kind` | Browsers launched (recorded after registration). |
| `octowright_browser_closed_total` | counter | `kind` | Browser sessions closed cleanly via `session.close()`. |
| `octowright_browser_launch_failed_total` | counter | `kind`, `error` | Failed launches. `error` is the exception class name. |
| `octowright_browser_evicted_total` | counter | `kind` | Browsers removed from the pool by an external close signal (not `pool.close`). |
| `octowright_macro_run_total` | counter | `macro`, `status` | Macro runs (`status` is `ok`/`failed`). |
| `octowright_bridge_reconnect_total` | counter | `reason` | Times the follower bridge reconnected to the leader. |
| `octowright_bridge_rpc_total` | counter | `method` | JSON-RPC messages forwarded local→remote. |
| `octowright_browser_launch_duration_seconds` | histogram (s) | `kind` | Time from `pool.launch()` entry to registered session. |
| `octowright_macro_run_duration_seconds` | histogram (s) | `macro` | `run_macro` elapsed time including nested actions. |
| `octowright_session_navigate_duration_seconds` | histogram (s) | `kind` | Duration of `session.navigate()` including `page.goto`. |
| `octowright_bridge_rpc_duration_seconds` | histogram (s) | `method`, `outcome` | End-to-end follower→leader→follower RPC latency. |

The `macro` label is capped at `OCTOWRIGHT_METRICS_MACRO_LABEL_CAP` distinct values (default 256); beyond the cap, names land in an `(overflow)` bucket so long-lived deployments don't unbound their time-series count. The `error` and `method` labels are intrinsically bounded by code paths; `kind` is bounded to the three browser engines plus `unknown`. `octowright_status()["metrics"]` surfaces `macro_labels_seen` and `macro_label_overflow_count` so an operator can see when dynamic macro names (e.g. `migrate-table-{uuid}`) have saturated the cap. The recovery escape hatch is `octowright.macros.execution.reset_macro_label_seen()` — in-process only (not exposed as an MCP tool, by design) for tests or operator process access.

There is intentionally no counter for the ws-cache batched flush — the flush is purely a transport optimization and its frequency is not a useful operational signal.

### Session log context

Spans are the canonical way to attach session identity to telemetry: span attributes (`instance_id`, `kind`, etc.) are recorded on the span object itself and travel with it regardless of which asyncio task started the span. Anything that needs to chain across tool calls — traces, metrics with `kind=` labels, propagated context across the bridge — relies on the span path, not on log context.

For structured logs, every tool-handler log call passes `instance_id=` explicitly as a keyword (`log.info("session.navigate", instance_id=..., url=...)` style). There is no global contextvar binding that fills it in for you; if a new log site wants the per-session identifiers, it must pass them as kwargs.

### Enabling export

Two env vars turn things on; the OTLP endpoint vars are the standard OpenTelemetry ones (`OTEL_EXPORTER_OTLP_*`), so any backend that speaks OTLP is wired the same way:

```bash
# Required: turn on tracing + metrics. Both default off.
export PROVIDE_TRACE_ENABLED=true
export PROVIDE_METRICS_ENABLED=true

# Optional — service name defaults to "octowright".
# export PROVIDE_TELEMETRY_SERVICE_NAME=octowright-dev

# Point at your backend. Either set the per-signal vars explicitly:
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://<host>/v1/traces
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://<host>/v1/metrics
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=https://<host>/v1/logs
# …or set one root and let the SDK append /v1/<signal>:
# export OTEL_EXPORTER_OTLP_ENDPOINT=https://<host>

# Auth (if your backend requires it):
# export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64-user:pass>"
# or, vendor-specific:
# export OTEL_EXPORTER_OTLP_HEADERS="api-key=<token>"

uv run octowright serve
```

The OTel SDK is pulled in as an extra (`provide-telemetry[otel]`); without it (or without `PROVIDE_TRACE_ENABLED=true`), the tracer/meter are noops and the cost is one cached attribute lookup per span entry — safe to leave the instrumentation in place.

#### Backend-specific notes

**Local OTel Collector (gRPC 4317 / HTTP 4318)** — most LGTM stacks (Loki + Grafana + Tempo + Mimir/Prometheus + Pyroscope) and any "agent-in-the-middle" deployment land here. The collector fans out to whatever backends it's configured with; from octowright's perspective it's the only URL you care about:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

**OpenObserve (direct ingestion)** — exposes per-stream paths under `/api/<org>/v1/<signal>`:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:5080/api/default/v1/traces
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://localhost:5080/api/default/v1/metrics
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://localhost:5080/api/default/v1/logs
```

**Honeycomb / Grafana Cloud / SigNoz / similar SaaS** — same `OTEL_EXPORTER_OTLP_*` vars; auth goes in `OTEL_EXPORTER_OTLP_HEADERS`.

#### Smoke-test recipe

End-to-end verification (replace the URL with your backend):

```bash
PROVIDE_TRACE_ENABLED=true PROVIDE_METRICS_ENABLED=true \
PROVIDE_TELEMETRY_SERVICE_NAME=octowright-smoketest \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
uv run --active python -c "
from provide.telemetry import setup_telemetry, shutdown_telemetry
from octowright._tracing import span, counter
setup_telemetry()
with span('octowright.browser.launch', kind='chromium'):
    with span('octowright.macro.run', macro='login'):
        pass
counter('octowright_smoketest_total').add(1, attributes={'kind': 'chromium'})
shutdown_telemetry()
print('emitted')
"
```

Then query your backend for `service.name=octowright-smoketest`. The expected span tree is `browser.launch → macro.run`. The counter shows up as `octowright_smoketest_total{service_name="octowright-smoketest", kind="chromium"} = 1`.
