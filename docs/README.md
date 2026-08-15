# Octowright Docs

Reference documentation for Octowright users and contributors. For a quick introduction
see the [top-level README](../README.md); come here for deep-dives on specific features.

## Start Here

- [getting-started.md](getting-started.md) — install, register an MCP client, first successful run
- [engines.md](engines.md) — browser engine install, status, reinstall, and launch-mode behavior

## Core Features

- [personas.md](personas.md) — named identity model, profile layout, `profile.yaml` spec, credential pre-flight
- [macros.md](macros.md) — record/replay, parameterization, conditional actions, linting, test runner
- [scenarios.md](scenarios.md) — YAML spec, participant roles, fixtures, lifecycle, JUnit output
- [goldens.md](goldens.md) — accessibility-tree baseline capture, verify policy, CI vs local rules
- [dashboard.md](dashboard.md) — web UI: live sessions, per-session debugger, trace deep-dive

## Operations & Quality

- [ci-quality.md](ci-quality.md) — local quality gates (`make ci`), coverage floor, `act` subset parity
- [troubleshooting.md](troubleshooting.md) — common failure modes and fast diagnosis
- [telemetry.md](telemetry.md) — structured logging, OTLP export, HTTP metrics

## Architecture & Internals

- [architecture/](architecture/) — PlantUML component and lifecycle diagrams
- [architecture/MCP-SHARED-CONTRACT.md](architecture/MCP-SHARED-CONTRACT.md) — HTTP/WebSocket wire contract between Python server and TypeScript frontend
- [images/README.md](images/README.md) — branding/image asset workflow and regeneration
