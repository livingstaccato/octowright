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
| MCP tool inventory (high-level by capability profile) | [`mcp-tool-surface.puml`](mcp-tool-surface.puml) | [`mcp-tool-surface.svg`](mcp-tool-surface.svg) |

The full per-tool inventory — every tool by profile, with counts that match `uv run octowright selftest` — lives in [`mcp-tool-inventory.md`](mcp-tool-inventory.md). Treat selftest as the source of truth; the diagram and the markdown table both lag if they drift.

## API contract

[MCP-SHARED-CONTRACT.md](MCP-SHARED-CONTRACT.md) — HTTP/WebSocket wire contract between the Python Starlette server and the TypeScript frontend SPA.

## Regenerating

```bash
make diagrams       # SVG only (committed; cheap; what CI runs)
make diagrams-png   # also rasterises each SVG to a 2400-px PNG in
                    # /tmp/octowright-diagrams/ for offline review.
                    # Bump width via OCTOWRIGHT_DIAGRAM_PNG_WIDTH=3600.
# or, equivalently:
bash scripts/render_diagrams.sh docs/architecture
bash scripts/render_diagrams.sh docs/architecture --png
```

`make diagrams` requires `plantuml` on PATH (`brew install plantuml`;
PlantUML needs Java 8+). `make diagrams-png` also requires `rsvg-convert`
(`brew install librsvg`).

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
- The Starlette app records HTTP RED metrics via `provide.telemetry`'s
  `TelemetryMiddleware` (`http.requests/errors/duration`), exported over OTLP
  with the rest of octowright's telemetry — there is no separate Prometheus
  scrape endpoint.
