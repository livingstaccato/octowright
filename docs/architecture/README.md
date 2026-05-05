# Architecture diagrams

PlantUML sources here; SVGs rendered alongside via `scripts/render_diagrams.sh`
(or `make diagrams` from the repo root). Both `.puml` and `.svg` are committed
so GitHub renders the diagrams without needing PlantUML installed by readers.

| Diagram | Source | Rendered |
|---|---|---|
| Module layout | [`component.puml`](component.puml) | [`component.svg`](component.svg) |
| `scenario_start` lifecycle | [`scenario-lifecycle.puml`](scenario-lifecycle.puml) | [`scenario-lifecycle.svg`](scenario-lifecycle.svg) |
| Persona on-disk layout | [`persona-layout.puml`](persona-layout.puml) | [`persona-layout.svg`](persona-layout.svg) |
| Macro record → save → replay | [`macro-lifecycle.puml`](macro-lifecycle.puml) | [`macro-lifecycle.svg`](macro-lifecycle.svg) |
| Artifact flow | [`artifact-flow.puml`](artifact-flow.puml) | [`artifact-flow.svg`](artifact-flow.svg) |
| MCP tool inventory | [`mcp-tool-surface.puml`](mcp-tool-surface.puml) | [`mcp-tool-surface.svg`](mcp-tool-surface.svg) |

## API contract

[MCP-SHARED-CONTRACT.md](MCP-SHARED-CONTRACT.md) — HTTP/WebSocket wire contract between the Python Starlette server and the TypeScript frontend SPA.

## Regenerating

```bash
make diagrams
# or, equivalently:
bash scripts/render_diagrams.sh docs/architecture
```

Requires `plantuml` on PATH (`brew install plantuml` on macOS; PlantUML in turn
needs Java 8+).

## Conventions

- One `@startuml <name>` per file; filename and the name argument match.
- SPDX header at the top using PlantUML comment syntax (`'`).
- Repo-wide skinparam palette: blue arrows (`#2563eb`), grey component
  borders (`#d1d5db`), pastel package backgrounds for grouping.

## Tracing and metrics notes

- `artifact-flow.puml` documents **Playwright trace artifacts** (`*.trace.zip`)
  written into recordings and viewed with `npx playwright show-trace`.
- This is distinct from **telemetry traces** (OpenTelemetry spans exported via
  OTLP when telemetry tracing is enabled).
- The Starlette app also exposes `GET /api/metrics` (Prometheus text) with
  process-local request and latency counters; this complements OTLP export.
