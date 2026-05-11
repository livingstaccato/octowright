# Octowright Demo Catalog

## Hero Demos

### Cross-Engine Trio

- ID: `cross-engine-trio`
- Summary: Three engines run the same Wikipedia search in parallel. Watch where they agree and where timing differs.
- Tags: `hero`, `engines`, `scenarios`, `real-site`
- Audiences: `evaluators`, `users`, `contributors`
- Regen: `uv run python scripts/demos/record_demo.py cross-engine-trio`
- Artifacts: replay 1/1, video 2/2
- Generation status: generated
- Last generated: 2026-05-11 21:59:18 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`; declared `artifacts/replay.jsonl`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### Macro Replay Loop

- ID: `macro-replay-loop`
- Summary: Record a macro on Wikipedia. Replay it cleanly later. Proof that selectors survive.
- Tags: `hero`, `macros`, `replay`, `real-site`
- Audiences: `evaluators`, `users`, `contributors`
- Regen: `uv run python scripts/demos/record_demo.py macro-replay-loop`
- Artifacts: replay 2/2, video 2/2
- Generation status: generated
- Last generated: 2026-05-11 22:00:37 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`, `artifacts/replay-roundtrip.jsonl`; declared `artifacts/replay.jsonl`, `artifacts/replay-roundtrip.jsonl`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### Role-Based Duo

- ID: `role-based-duo`
- Summary: One browser drives a 3-step form. A second browser, in a different role, sees each step appear on a live dashboard.
- Tags: `hero`, `roles`, `scenarios`, `broadcast`
- Audiences: `evaluators`, `users`, `contributors`
- Regen: `uv run python scripts/demos/with_playground.py role-based-duo`
- Artifacts: replay 1/1, video 2/2
- Generation status: generated
- Last generated: 2026-05-11 22:02:41 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`; declared `artifacts/replay.jsonl`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### Seven Mix Orchestration

- ID: `seven-mix-orchestration`
- Summary: Seven players claim tiles on a shared canvas while an operator watches the aggregate live. Nine browsers actually coordinating — not nine empty tabs.
- Tags: `hero`, `orchestration`, `multi-window`, `mixed-engine`
- Audiences: `evaluators`, `users`, `contributors`
- Regen: `uv run python scripts/demos/with_playground.py seven-mix-orchestration`
- Artifacts: replay 2/2, video 2/2
- Generation status: generated
- Last generated: 2026-05-11 22:02:28 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`, `artifacts/participant-roster.json`; declared `artifacts/replay.jsonl`, `artifacts/participant-roster.json`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`

### Verify Suite

- ID: `verify-suite`
- Summary: Three roles each run their own verify macros against the same scenario. Each asserts what its role should see.
- Tags: `hero`, `verify`, `testing`, `junit`
- Audiences: `evaluators`, `contributors`, `testers`
- Regen: `uv run python scripts/demos/record_demo.py verify-suite`
- Artifacts: replay 2/2, video 2/2
- Generation status: generated
- Last generated: 2026-05-07 17:54:50 UTC
- Replay artifacts: existing `artifacts/replay.jsonl`, `artifacts/report.xml`; declared `artifacts/replay.jsonl`, `artifacts/report.xml`
- Video artifacts: existing `artifacts/demo.mp4`, `artifacts/poster.png`; declared `artifacts/demo.mp4`, `artifacts/poster.png`


## Full Library

Supporting demos appear here for the complete non-hero catalog; hero demos are featured above.

No supporting demos yet.

