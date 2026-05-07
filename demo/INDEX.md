# Octowright Demo Catalog

## Hero Demos

### Cross-Engine Trio

- ID: `cross-engine-trio`
- Summary: Launch Chromium, Firefox, and WebKit against the same deterministic offline target to compare engine behavior at a glance.
- Tags: `hero`, `engines`, `scenarios`, `smoke`
- Audiences: `evaluators`, `users`, `contributors`
- Regen: `uv run python scripts/demos/record_demo.py cross-engine-trio`
- Artifacts: replay 1/1, video 2/2
- Generation status: generated
- Last generated: 2026-05-07 17:36:58 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`; declared `artifacts/replay.jsonl`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### First Run Session

- ID: `first-run-session`
- Summary: Launch one browser, perform a visible offline interaction, and inspect the resulting replay and video artifacts.
- Tags: `hero`, `onboarding`, `recording`, `offline`
- Audiences: `evaluators`, `users`, `contributors`
- Regen: `uv run python scripts/demos/record_demo.py first-run-session`
- Artifacts: replay 1/1, video 2/2
- Generation status: generated
- Last generated: 2026-05-07 17:37:04 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`; declared `artifacts/replay.jsonl`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### Fixture Lab

- ID: `fixture-lab`
- Summary: Demonstrate shared mock routes and dialog policy with deterministic local fixture responses wired across multiple participants.
- Tags: `hero`, `fixtures`, `mock-routes`, `offline`
- Audiences: `evaluators`, `contributors`, `testers`
- Regen: `uv run python scripts/demos/record_demo.py fixture-lab`
- Artifacts: replay 2/2, video 2/2
- Generation status: generated
- Last generated: 2026-05-07 17:37:10 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`, `artifacts/mock-routes.json`; declared `artifacts/replay.jsonl`, `artifacts/mock-routes.json`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### Macro Replay Loop

- ID: `macro-replay-loop`
- Summary: Capture a deterministic interaction once, replay it against the same local shape, and inspect the stable loop artifacts.
- Tags: `hero`, `macros`, `replay`, `offline`
- Audiences: `evaluators`, `users`, `contributors`
- Regen: `uv run python scripts/demos/record_demo.py macro-replay-loop`
- Artifacts: replay 2/2, video 2/2
- Generation status: generated
- Last generated: 2026-05-07 17:37:16 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`, `artifacts/replay-roundtrip.jsonl`; declared `artifacts/replay.jsonl`, `artifacts/replay-roundtrip.jsonl`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### Role-Based Duo

- ID: `role-based-duo`
- Summary: Start two coordinated participants and show role-filtered actions against a simple offline scenario.
- Tags: `hero`, `roles`, `scenarios`, `broadcast`
- Audiences: `evaluators`, `users`, `contributors`
- Regen: `uv run python scripts/demos/record_demo.py role-based-duo`
- Artifacts: replay 1/1, video 2/2
- Generation status: generated
- Last generated: 2026-05-07 17:37:27 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`; declared `artifacts/replay.jsonl`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### Seven Mix Orchestration

- ID: `seven-mix-orchestration`
- Summary: Showcase the flagship mixed-engine multi-window scenario with seven players, an operator, and a spectator in one deterministic offline bundle.
- Tags: `hero`, `orchestration`, `multi-window`, `mixed-engine`
- Audiences: `evaluators`, `users`, `contributors`
- Regen: `uv run python scripts/demos/record_demo.py seven-mix-orchestration`
- Artifacts: replay 2/2, video 2/2
- Generation status: generated
- Last generated: 2026-05-07 17:37:45 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`, `artifacts/participant-roster.json`; declared `artifacts/replay.jsonl`, `artifacts/participant-roster.json`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### Verify Suite

- ID: `verify-suite`
- Summary: Run role-specific verification macros as a compact offline scenario suite and surface the replay plus test-report story together.
- Tags: `hero`, `verify`, `testing`, `junit`
- Audiences: `evaluators`, `contributors`, `testers`
- Regen: `uv run python scripts/demos/record_demo.py verify-suite`
- Artifacts: replay 2/2, video 2/2
- Generation status: generated
- Last generated: 2026-05-07 17:37:52 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`, `artifacts/report.xml`; declared `artifacts/replay.jsonl`, `artifacts/report.xml`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`


## Full Library

Supporting demos appear here for the complete non-hero catalog; hero demos are featured above.

No supporting demos yet.

