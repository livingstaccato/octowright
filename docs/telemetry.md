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
