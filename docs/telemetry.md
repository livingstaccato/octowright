# Telemetry

Both halves of Octowright use the `provide.telemetry` family for structured
logging:

- **Python server** uses `provide-telemetry>=0.4.8` (structlog under the hood).
  `setup_telemetry()` is called by `octowright serve`; every module gets a
  logger via `get_logger(__name__)`. Logs land on stderr in development,
  JSON in production (auto-detected).
- **TypeScript dashboard** uses `@provide-io/telemetry@^0.4.7` (pino under
  the hood). `setupTelemetry()` runs at the top of each entrypoint;
  `getLogger('octowright.frontend.{api,tail,dashboard,session,global}')` per
  module. Logger names mirror the Python convention so log lines are easy
  to correlate across the stack.

## Log level and format

```bash
# Human-readable local debugging
export PROVIDE_LOG_LEVEL=DEBUG
export PROVIDE_LOG_FORMAT=pretty
uv run octowright serve
```

```bash
# Machine-friendly production logs
export PROVIDE_LOG_LEVEL=INFO
export PROVIDE_LOG_FORMAT=json
uv run octowright serve
```

`octowright serve --log-level DEBUG` is a convenience wrapper that sets
`PROVIDE_LOG_LEVEL` for the process and spawned daemon.

## OTLP export

Telemetry export is opt-in. To send OpenTelemetry signals to an OTLP collector:

```bash
export PROVIDE_TRACE_ENABLED=1
export PROVIDE_METRICS_ENABLED=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
# optional auth/tenant headers
export OTEL_EXPORTER_OTLP_HEADERS="authorization=Bearer%20TOKEN,x-tenant-id=dev"
uv run octowright serve
```

Signals are no-op if telemetry exporters are not configured/available. See
[architecture/](architecture/) for the full span/metric inventory and the
MCP notification taxonomy.

## Playwright traces vs telemetry traces

- **Playwright trace**: per-session browser artifact (`*.trace.zip`) produced
  by Playwright when session tracing is enabled; inspect with
  `npx playwright show-trace`.
- **Telemetry trace**: OpenTelemetry spans emitted by `provide.telemetry`
  (when `PROVIDE_TRACE_ENABLED=1`) and exported to OTLP.

These are separate systems and can be enabled independently.

## HTTP metrics

HTTP request metrics for the debugger/API server are recorded through
`provide.telemetry`'s `TelemetryMiddleware` and exported via OTLP alongside the
rest of octowright's telemetry — RED metrics (`http.requests.total`,
`http.errors.total`, `http.request.duration_ms`) attributed by route, method,
and status code, plus request-id/session-id log correlation and W3C trace
propagation. There is no separate Prometheus scrape endpoint; point an OTLP
collector at the process to consume them. Disable metric recording (propagation
stays on) with:

```bash
export OCTOWRIGHT_HTTP_METRICS=0
```

## Related

- [ci-quality.md](ci-quality.md) — local quality gates.
- [troubleshooting.md](troubleshooting.md) — common failure modes.
- [architecture/](architecture/) — span/metric/notification reference tables.

---

# OpenTelemetry: spans, metrics, and MCP notifications

Extracted from the root `AGENTS.md`. Read this before adding or renaming a
span, metric, or notification -- `scripts/check_telemetry_docs.py` fails
`make lint` when an emitted metric or notification is documented in neither
this file nor `AGENTS.md`.

## Telemetry (OpenTelemetry)

Tracing and metrics are emitted via `provide.telemetry`. Logs are always structured; spans and metrics are emitted ONLY when explicitly enabled — the noop tracer/meter is the default so there's no cost when not in use. Exports use OTLP, so any OTel-compatible backend works: an OTel Collector that fans out to LGTM/Tempo, OpenObserve, Honeycomb, Datadog (OTLP), Jaeger, Grafana Cloud, SigNoz, etc. The codebase does not name a specific backend.

### Spans

Span names follow the `octowright.<area>.<verb>` convention. The list below is alphabetized for stability — order has no semantic meaning. Per-span attributes vary; only the attributes actually set at the span call site are listed (callers may add more via `set_attrs` mid-span).

| Span | Attributes | Emitted by |
|------|------------|------------|
| `octowright.artifact.verify` | `artifact_type`, `name`, `critical_points`, `run_id` | `artifacts/verification.py` |
| `octowright.artifact.verify.check` | `artifact_type`, `check_type` | `artifacts/verification.py` |
| `octowright.bridge.forward_rpc` | `method`, `request_id` | `proxy_supervisor.forward_rpc` (follower leg) |
| `octowright.browser.handoff` | `old_instance_id`, `kind`, `headed`, `close_original`, `accept_stateless` | `browser_pool/lifecycle.handoff_browser` |
| `octowright.browser.launch` | `kind` | `browser_pool/_metrics.launch_span` (wraps `pool.launch`) |
| `octowright.browser.relaunch_fluid` | `instance_id`, `kind` | `browser_pool/pool.relaunch_fluid` |
| `octowright.browser.spawn_roster` | `roster_size` | `browser_pool/roster.browser_spawn_roster` |
| `octowright.macro.action` | `action`, `instance_id` | `macros/runtime.dispatch_simple` |
| `octowright.macro.artifact.run` | `macro`, `run_id`, `verify` | `macros/artifacts.py` |
| `octowright.macro.run` | `macro`, `instance_id`, `kind` | `macros/execution.run_macro` |
| `octowright.macro.run_sequence` | `names_count`, `stop_on_failure` | `macros/execution.run_sequence` |
| `octowright.mcp.request` | `method`, `path` | `_trace_propagation.TraceContextExtractionMiddleware` (leader leg, ends on `http.response.start`) |
| `octowright.scenario.run_macro` | `scenario_id`, `macro`, `role`, `targeted` | `scenarios_pool.ScenarioPool.run_macro` |
| `octowright.scenario.start` | `scenario_id`, `scenario_name`, `participants` | `scenarios_pool.ScenarioPool.start` |
| `octowright.session.close` | `instance_id`, `kind` | `session/core_ops_mixin.SessionOpsMixin.close` |
| `octowright.session.navigate` | `instance_id`, `kind`, `url` | `session/core_page_mixin.SessionPageMixin.navigate` |

The terminal session-kind plugin (`packages/octowright-terminal`, see **Terminal Sessions (plugin)**) emits its own `octowright.terminal.launch` / `.close` / `.send_input` spans, documented in its own README rather than here, since core does not emit them.

`macro.action` spans nest under their `macro.run` parent, which (when invoked from `macro_run_sequence`) nests under `macro.run_sequence`, so a multi-step macro run renders as a clean tree.

The `url` attribute on `octowright.session.navigate` is run through `_sanitize_url_for_span` before it is stamped: it strips the query string *and* any `user:pass@` basic-auth userinfo (preserving `host:port` verbatim by dropping everything up to the last `@` in the netloc), so navigation tokens and cleartext credentials don't reach traces / exporter backends. The full URL still flows to `self.url` and the recorder's `navigate` event — only the span attribute is sanitized.

### Trace context propagation across the bridge

The follower→leader chain is glued together by the W3C `traceparent` header. On the follower side, `proxy_supervisor.forward_rpc` opens its `octowright.bridge.forward_rpc` span and hands the underlying MCP `streamable_http_client` a ready-made `httpx2.AsyncClient` from `_trace_propagation.build_tracing_http_client` — MCP 2.0 takes the client itself, where 1.x took an `httpx_client_factory`. That client carries a per-request hook (`_inject_traceparent_hook`) that calls the OTel propagator to inject `traceparent` (and `tracestate`) into every outgoing HTTP request, and a response hook capturing `mcp-session-id` — 2.0 no longer yields a `get_session_id` callable alongside the streams, and the leader's pid-liveness reaper matches sessions by `(follower_pid, remote_session_id)`, so losing it would silently disable that reaper. On the leader side, `_trace_propagation.TraceContextExtractionMiddleware` runs as ASGI middleware in front of the HTTP-MCP app: it extracts the propagated context from request headers, attaches it via `opentelemetry.context.attach`, then opens the per-request `octowright.mcp.request` span. Any spans started while the leader handles the request — including spans inside `@mcp.tool` handlers like `browser.launch` or `macro.run` — chain under the follower's `bridge.forward_rpc` span. The `mcp.request` span ends as soon as `http.response.start` is sent (not on body completion) to avoid filling the OTel batch-exporter buffer with long-lived SSE streams.

### Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `octowright_browser_launched_total` | counter | `kind` | Browsers launched (recorded after registration). |
| `octowright_browser_closed_total` | counter | `kind` | Browser sessions closed cleanly via `session.close()`. |
| `octowright_browser_launch_failed_total` | counter | `kind`, `error` | Launches an engine attempted and failed. `error` is the exception class name. Machinery only — a request an input guard refused (`InvalidRequestError`) is counted by `octowright_launch_refused_total` instead, so this counter stays a per-engine health signal. |
| `octowright_browser_evicted_total` | counter | `kind` | Browsers removed from the pool by an external close signal (not `pool.close`). |
| `octowright_macro_run_total` | counter | `macro`, `status` | Macro runs (`status` is `ok`/`failed`). |
| `octowright_bridge_reconnect_total` | counter | `reason` | Times the follower bridge reconnected to the leader. |
| `octowright_bridge_rpc_total` | counter | `method` | JSON-RPC messages forwarded local→remote. |
| `octowright_bridge_resume_total` | counter | — | In-flight requests re-sent to the leader after a reconnect (idempotent resume). |
| `octowright_bridge_suspension_total` | counter | — | Follower-process suspensions detected by the deadline watchdog (a client froze the follower, e.g. an MCP-client compaction SIGSTOP). |
| `octowright_browser_crashed_total` | counter | `kind` | Renderer crashes observed (`page.on("crash")`). |
| `octowright_browser_crash_recovered_total` | counter | `kind` | Renderer crashes auto-recovered by replacing the dead page. |
| `octowright_browser_crash_recovery_failed_total` | counter | `kind` | Auto-recovery attempts whose page replacement failed. |
| `octowright_unresponsive_target_total` | counter | `kind` | Targets that stopped answering a Playwright call within its budget (`SessionCallTimeoutError`, `CrashScope="unresponsive"`) — not a `page.on("crash")` event, so kept separate from `octowright_browser_crashed_total`. |
| `octowright_driver_restart_total` | counter | — | Shared Playwright driver deaths rebuilt mid-run (the SPOF signal). |
| `octowright_driver_lost_total` | counter | `outcome`, `kind` | Sessions lost when the shared driver died (`outcome` = `surfaced`/`relaunched`). |
| `octowright_launch_refused_total` | counter | `reason` | Launches refused (`reason` = `cap`/`memory`/`invalid_request`). `cap`/`memory` are capacity pressure. `invalid_request` means an input guard rejected the request — either what a caller sent, or stored configuration a policy now refuses (a saved persona's `default_url` under `OCTOWRIGHT_SSRF_POLICY=block-private` raises it with no caller mistake at all). Either way it is not a machine fault, which is why it is not on `octowright_browser_launch_failed_total`. |
| `octowright_orphan_reaped_total` | counter | `scope` | Orphaned (dead-driver) browser processes killed by the reaper. |
| `octowright_follower_session_reaped_total` | counter | — | Leader MCP sessions terminated by the housekeeping pid-liveness reaper (job 3) because their follower's OS process was found dead. Process-lifetime running total also readable in-process via `octowright_status()["bridge"]["follower_sessions_reaped"]`. |
| `octowright_mcp_new_session_throttled_total` | counter | — | Session-creating `/mcp` requests rejected with `429` by the leader-side per-source new-session rate limit (`OCTOWRIGHT_MCP_NEW_SESSION_MAX`). A high value means a follower is storming — reconnecting/creating sessions far faster than legit use. |
| `octowright_mcp_session_evicted_total` | counter | — | Leader MCP sessions evicted by housekeeping because the live table exceeded `OCTOWRIGHT_MCP_MAX_SESSIONS` (the version-agnostic memory bound against a session storm). |
| `octowright_bridge_leader_recovery_total` | counter | `outcome` | Leader-down gaps (`outcome` = `recovered`/`exhausted`) — how often a leader restart is survived vs. drops the client. |
| `octowright_artifact_verify_total` | counter | — | Macro-artifact verification runs. |
| `octowright_artifact_verify_check_total` | counter | — | Per-check results within a macro-artifact verification. |
| `octowright_macro_artifact_run_total` | counter | — | Macro-artifact replay runs. |
| `octowright_process_rss_bytes` | histogram (By) | `scope` | Resident memory of the leader + its browsers, sampled each housekeeping cycle (`scope` = `leader`/`browsers`/`total`) — the continuous multi-day leak signal. |
| `octowright_browser_launch_duration_seconds` | histogram (s) | `kind` | Time from `pool.launch()` entry to registered session. |
| `octowright_macro_run_duration_seconds` | histogram (s) | `macro` | `run_macro` elapsed time including nested actions. |
| `octowright_session_navigate_duration_seconds` | histogram (s) | `kind` | Duration of `session.navigate()` including `page.goto`. |
| `octowright_bridge_rpc_duration_seconds` | histogram (s) | `method`, `outcome` | End-to-end follower→leader→follower RPC latency. |
| `octowright_operation_queue_wait_seconds` | histogram (s) | `operation`, `kind`, `outcome` | Time an operation spent in the per-session FIFO queue before admission (`outcome` = `admitted`/`timeout`/`cancelled`). See **Browser Session Operation Gate**. |
| `octowright_operation_active_duration_seconds` | histogram (s) | `operation`, `kind`, `outcome` | Time an admitted operation held the gate (`outcome` = `ok`/`error`/`cancelled`). |
| `octowright_operation_queue_timeout_total` | counter | `operation`, `kind` | FIFO tickets that expired before admission (`SessionBusyTimeoutError`). |
| `octowright_operation_active_timeout_total` | counter | `operation`, `kind` | Active-duration ceiling breaches (`OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS`, off by default) — a session's root operation ran longer than the ceiling, so its owning task was cancelled and the gate driven to `broken`. Incremented once per breach; a gate already `broken` short-circuits before re-incrementing. |
| `octowright_operation_rejected_total` | counter | `operation`, `kind`, `reason` | Operations rejected outright because the gate was not open (`reason` is the gate state or close/invariant cause, e.g. `closing`/`closed`/`broken`/`external_close`/`session_closed`). |
| `octowright_operation_queue_depth` | gauge (1) | `kind` | Current FIFO queue depth, aggregated per browser `kind` (not per session or per operation). |

The `macro` label is capped at `OCTOWRIGHT_METRICS_MACRO_LABEL_CAP` distinct values (default 256); beyond the cap, names land in an `(overflow)` bucket so long-lived deployments don't unbound their time-series count. The `error` and `method` labels are intrinsically bounded by code paths; `kind` is bounded to the three browser engines plus `unknown`. `octowright_status()["metrics"]` surfaces `macro_labels_seen` and `macro_label_overflow_count` so an operator can see when dynamic macro names (e.g. `migrate-table-{uuid}`) have saturated the cap. The recovery escape hatch is `octowright.macros.execution.reset_macro_label_seen()` — in-process only (not exposed as an MCP tool, by design) for tests or operator process access.

There is intentionally no counter for the ws-cache batched flush — the flush is purely a transport optimization and its frequency is not a useful operational signal.

### MCP notifications (proactive, LLM-facing)

Octowright builds JSON-RPC notifications for exceptional situations from `browser_pool` session-event-bus events (`server/mcp_notifications.notification_payload` / `_build_notification`) and delivers them over TWO paths so a client gets them regardless of transport: (1) **stdio** — the emitter (`run_with_notifications`) writes to the stdio server, used when the leader runs inline (`--no-singleton`); (2) **follower bridge** — the leader streams the event bus over the `GET /api/mcp-events` SSE endpoint (`http/routes/mcp_events.py`), and the follower's `proxy_runtime.consume_leader_notifications` re-injects each frame (rebuilt via `payload_to_message`) into the local stdio client write. Path (2) closes the daemon-mode gap: the HTTP-MCP transport the detached-daemon leader serves has no server-initiated-notification path of its own, so without it a stdio-client-through-follower (the normal deployment) would never see crash/driver/close notifications. The leader's own stdio emitter writes to the detached daemon's clientless stdout, so there is no double-delivery. A **direct** HTTP-MCP client that bypasses the follower still gets no push (SDK limitation) — so the LLM should still treat `octowright_status()` (health / crash.recent / pool.lost_sessions) as the authoritative check and notifications as best-effort. Covered end-to-end by `tests/test_mcp_events_daemon_live.py` (via-follower delivery) and `tests/test_mcp_notifications_daemon_live.py` (direct-client boundary).

| Method | Fires when | Key params |
|--------|-----------|------------|
| `notifications/octowright/browser_crashed` | a renderer crash is observed (`page.on("crash")`), OR a target stops answering within its call budget (`scope="unresponsive"`) | `recovering` (auto-recovery scheduled → WAIT for `browser_recovered`, don't relaunch; always `false` for `scope="unresponsive"`), `scope` (`renderer`/`process`/`unresponsive`), `hint` |
| `notifications/octowright/browser_recovered` | a renderer-crash recovery resolved | `outcome` (`recovered` = usable again, continue / `failed` / `exhausted` = relaunch), `attempts`, `hint` |
| `notifications/octowright/driver_died` | the shared driver died and sessions were lost | `lost_instance_ids`, `relaunch_mode`, `restart_count`, `hint` (points at `octowright_status().pool.lost_sessions`) |
| `notifications/octowright/session_closed` | a session left the pool | `reason` (`agent_close`/`user_close`/`external_disconnect`/`crashed`/`shutdown`) |

A third `CrashScope` (`browser_pool/events.py`) exists alongside `renderer`/`process`: `unresponsive`, for a target that is alive but stopped answering a Playwright call within its budget (`session/timeouts.py`'s `bounded()`, raising `SessionCallTimeoutError` — the 2026-08-29 incident this closes, where a wedged WebKit target hung a test run for 12.6 hours with `page.on("crash")` never firing because a merely-unresponsive target never crashes). No Playwright event reports this case, so it is *raised* by the call budget rather than *observed* like a real crash.

**The rule** (`SessionOperationGate.operation()`, `session/operation/gate/core.py`): the INNERMOST gated operation that sees a `SessionCallTimeoutError` escape it — reachable via the exception's explicit `__cause__` chain (`_call_timeout_cause`, bounded to a few hops against a pathological chain), not just the top-level type — publishes exactly once, and marks that `SessionCallTimeoutError` instance (`_mark_call_timeout_published`) so any ancestor frame the exception continues propagating through (still escaping, or reachable via its own `__cause__` walk) finds the mark and stays silent. That holds regardless of what an outer caller does with the exception afterward — re-raise it, wrap it again, or swallow it inside its own lease — because the publish already happened at the point of first escape, before the outer caller ever got a chance to touch it. This is deliberately NOT "the root lease publishes": an earlier version of this fix gated on `_LeaseToken.is_root` (root-only), reasoned from call-graph shape that every caller "goes through `run_macro`/`run_sequence`" and would therefore see it — which review round 3 proved false by driving `macros/artifacts.py`'s `run_macro_artifact` and `run_sequence(stop_on_failure=False)`: both catch the wrapped timeout inside their OWN root lease and never re-raise it, so nothing ever escaped a root frame for a root-only check to see, and neither published anything. The innermost-lease rule needs no per-caller enumeration and no update when a new caller is added, because it does not depend on knowing what any particular caller does with the exception. `BrowserSession.__post_init__` wires the gate's `on_call_timeout` hook to `BrowserSession._notify_call_timeout`, which publishes `SessionCrashedEvent(scope="unresponsive", recovering=False)` and counts `octowright_unresponsive_target_total{kind}`, using THIS frame's own operation name — for a nested wedge, the specific action that stalled, not an outer umbrella name.

**Deliberately does not auto-recover**: renderer-crash recovery replaces the dead page, which is right for an actual crash and wrong here — the target may still be executing, and force-replacing it can thrash a browser that is only slow. Surface + notify; let the caller (agent or operator) decide whether to wait, retry, or relaunch with `browser_launch`. The counter and the notification are not enough on their own to keep this scope visible on the PULL surface in the common configuration: a push notification is best-effort (a direct HTTP-MCP client gets no push at all — SDK limitation), and an OTel counter is a noop unless `PROVIDE_METRICS_ENABLED` is set (off by default). So `_notify_call_timeout` also records an `incidents.CATEGORY_UNRESPONSIVE_TARGET` incident (`instance_id`, `kind`, `url`, `operation` — the gated operation name that timed out — and a timestamp; deliberately no exception message, since the operation name is the diagnostic signal and a message could carry a URL/path), surfaced at `octowright_status()["crash"]["unresponsive_recent"]`. That is a key SEPARATE from `"recent"` (the renderer-crash records), not folded into it: `"recent"` runs through `crash_reports.enrich`, which correlates macOS `.ips` SIGSEGV signatures written a beat after a real crash, and an unresponsive target never crashed — it just stopped replying — so it has no crash report to correlate and folding the two would invite a lookup that can never hit.

**A lookup that fails after an unresponsive target says so.** The hook above deliberately neither sets `_crashed` nor tears the session down, so an unresponsive target usually stays live and the next call simply works. But when that browser is *later* evicted for any reason (an external close, a dead driver), `lifecycle._record_recently_evicted` used to store a crashed/not-crashed **bool** — and since the unresponsive path never sets `_crashed`, the lookup fell into the generic `ended unexpectedly (closed or crashed externally) — relaunch it with browser_launch` branch. That is the wrong advice in this exact case: the browser process is usually still running, so relaunching discards a live session and its profile state to fix something that only needed a smaller batch, and it says nothing about downloads the timed-out call may already have landed. The ledger is now three states (`crashed` / `unresponsive` / `external`), read from a `_unresponsive_operation` marker the hook sets, and `pool._missing_session_message` has a third branch naming the recovery path (`browser_list` before relaunching, `browser_downloads`, retry smaller) instead of `browser_launch`. A crash **wins** over unresponsiveness when both are set: a target that went quiet and then actually died is a crash, and `relaunch` is right for it. The marker deliberately lives on the session rather than being read back out of `incidents` — that ring is 25 entries **shared across all categories**, and its own docstring notes a repeatedly-unresponsive target evicts its own history, which is fine for the `octowright_status` surface it was built for and unreliable as a correctness input. The `SessionCallTimeoutError` raised while the session is still live already carried the right advice and keeps it, now also naming the smaller-batch retry and the `browser_downloads` check.

The MCP server `instructions` string (`server/_state.py`) summarizes this taxonomy so the LLM knows the signals exist; refused launches surface in-band as actionable tool errors (cap / memory floor), not notifications.

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

