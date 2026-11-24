# Interaction indicators implementation plan

> For agentic workers: use superpowers:subagent-driven-development. Execute the task briefs in order; preserve this plan's ledger across compaction.

**Goal:** Include the user-approved live and recorded interaction indicators in Octowright0.27.0, then finish the existing PR/merge/release/deployment task.

**Architecture:** A context-installed page observer classifies native manual and automated input. Independent show/record settings control page rendering and a bounded JSONL sidecar. A guarded HTTP reader supplies the dashboard's video-synchronized overlay. Keep observed input out of macro action replay.

**Tech stack:** Python3.11+, Playwright1.63+ including1.65, plain injected JavaScript with checkJs, TypeScript/Vitest/Vite frontend, pytest.

## Global Constraints

- Work in an isolated feature checkout on `feat/interaction-indicators`; never modify original dirty worktrees. Preserve the separately reviewed driver compatibility fix during integration.
- User approved the [interaction indicator spec](../specs/2026-11-23-interaction-indicators-design.md) and inclusion in v0.27.0.
- `show_interactions=False` and `record_interactions=False` are independent opt-ins. Dashboard playback has its own display toggle. Drawn video pixels cannot be erased; suppress a second dashboard marker for an event whose live marker was enabled.
- Cover clicks, taps, right/middle clicks, double clicks/taps, holds after500ms, drags, wheel direction, and distinct touch contacts. Indicators use fixed labels/shapes as well as color. Never consume input, move focus, prevent default, or dispatch substitute application events.
- Support all three engines, navigation/new pages and frames. Clean contacts/frames/timers on cancellation, blur/pagehide, disable and destroy. Observations are page-visible and best effort.
- Metadata is coordinates, timings, pointer/contact type, button and gesture phase. No page text/selectors/typed values. Validate finite numeric fields, enums, contact IDs and rates on the host; page data cannot choose a disk path.
- Sidecar file is `log_path.with_suffix('.interactions.jsonl')`, private0600 under the owning log root. Cap at64MiB and120 accepted events/sec/session; line ceiling4096bytes. At most16 active contacts and coalesce drag moves to20Hz/contact. Expose truncation visibly. Add the suffix to discovery/cleanup/label-sanitization contracts.
- HTTP pages return at most1000 events by default, hard max5000, with byte cursor; enforce existing pairing/Host boundaries. Old recordings remain readable without a sidecar.
- No arbitrary sleeps as the primary regression assertion. Show red/green tests before production changes. Keep LOC<=777 and baseline complexity/type/security guards green. Do not push or merge from worker agents. Commit only a task's owned files with normal signed Git/hook flow.

## Shared interfaces

Injected asset path: `src/octowright/browser_pool/_assets/interactions.js`.

```ts
// Fixed host bindings installed before the asset. Source page/frame comes from Playwright.
__octowrightInteractionConfig({time_origin:number}): Promise<{show:boolean;record:boolean}>;
__octowrightInteractionEvent(payload: InteractionPayload): Promise<void>;
// Idempotent per-document controller, configurable without appending competing init scripts.
window.__octowrightInteractions: {
  ready: Promise<void>;
  configure(options:{show:boolean;record:boolean}): void;
  destroy(): void;
};
type InteractionPayload = {
  v:1; phase:'down'|'move'|'up'|'cancel'|'double'|'wheel';
  pointer_type:'mouse'|'touch'|'pen'; pointer_id:number; button:0|1|2;
  x:number; y:number; width:number; height:number; at:number; shown:boolean;
  dx?:number; dy?:number;
};
```

`at` is epoch milliseconds from the observer; bounds-check it. Coordinates supplied to the host refer to the top-level viewport; in-frame live rendering still uses the frame's own coordinates. Relay/map nested frame observations through their embedding viewport, including cross-origin frames, with bounded validated messages; do not give page callbacks host write authority. Resolve remaining geometry details empirically with actual frame tests.

```python
# src/octowright/interactions.py (split helpers if needed)
def interaction_path(log_path: Path) -> Path: ...
class InteractionController:
    # Owns settings, sink/rate limits, page/video identity and stable video time origins.
    async def install(self, session: BrowserSession) -> None: ...
    async def configure(self, *, show: bool | None = None, record: bool | None = None) -> dict[str, Any]: ...
    async def close(self) -> None: ...
def read_interactions(log_path: Path, *, cursor: int = 0, limit: int = 1000) -> dict[str, Any]: ...
```

Keep the row format stable across backend/frontend tasks: event rows contain `type:'interaction'`, `t` (nonnegative video-relative milliseconds), `video` (host-resolved basename or null), validated payload fields, and host-derived page identity. Host-generated settings/reset rows end active contacts at runtime recording changes. File metadata includes video time origin/identity sufficient to avoid mixing tabs and to keep navigation from resetting time. Reader reply is `{available,events,next_cursor,complete,truncated}`. `available:false` for no sidecar. Document the implemented typed row contract in Task2's report before Task4 starts.

MCP control: `browser_set_interaction_visuals(instance_id, show:bool|None=None, record:bool|None=None)`; require at least one setting. Launch/quick-launch/external option mappings use `show_interactions`, `record_interactions`. Session settings survive handoff/relaunch through launch options. All direct page access obeys operation-gate and bounded-call rules; passive binding callbacks must not deadlock behind a wheel/click that triggered them.

HTTP reader: `GET /api/sessions/{id}/interactions?cursor=0&limit=1000`, same session lookup/guard as recording events. Optional video filter may be added to the reader if needed; do not accept arbitrary paths. Register new routes through existing app wiring.

### Task 1: Browser observer and live renderer

**Own files:** new injected interactions.js; new deterministic JS scheduler test utility + pytest launcher; new isolated raw-Playwright live tests for this asset. Update JS typecheck inventory/config only for this asset. No backend control, lifecycle, HTTP, frontend session or driver files.

**Produces:** the fixed asset/controller/bindings above. Metadata gesture phases are raw observed input, not replay actions. Defaults remain inert when both modes are false.

- [ ] Write tests exercising the real asset: down->hold at500ms->up, touch/mouse/button distinction, double de-duplication, drag coalescing, wheel, simultaneous contacts, disable/destroy/blur cleanup, repeat installation, record-only, show-only, malformed relay messages, frame-coordinate mapping. Assert application input event sequence unchanged.
- [ ] Run the tests before implementation and retain the expected missing-feature failure. Use `uv run pytest tests/test_interaction_observer.py --no-cov -q` and the task's raw-engine tests.
- [ ] Implement event observation and non-intercepting isolated DOM markers, bounded contact state, stable labels/shapes and the per-document configuration controller. Avoid new host bindings beyond the two fixed names.
- [ ] Run deterministic tests and raw local-page coverage on Chromium, Firefox and WebKit. Verify a native right-click, touch tap/hold, drag and wheel using genuine Playwright input APIs; tests may use real touch-enabled contexts.
- [ ] Run JS checkJs/typecheck and owned-file formatting. Commit owned files; report exact output and interface details to the controller, then freeze for scoped review.

### Task 2: Bounded capture, sidecar reader and HTTP endpoint

**Own files:** new interactions.py and focused helper modules/tests; new http/routes/interactions.py, app route wiring, recording_sidecars.py, recorder label-tail list, closed-discovery/cleanup tests as necessary. Do not edit observer asset, launch/session API integration, or frontend files.

**Consumes:** Task1 asset and bindings. **Produces:** InteractionController and read_interactions APIs, finalized row contract, authenticated paginated endpoint.

- [ ] Add tests first for independent flags, validation of bool/nonfinite/out-of-range fields, max rates/contacts,64MiB truncation,0600 file privacy, cursor/line bounds, missing/old sidecars, malicious IDs/path containment and paired/unpaired HTTP access.
- [ ] Implement capture using the session-owned log path; validate binding source and payload before writes. Preserve cancellation. Use a stable per-page video origin and video basename; test navigation and multiple pages so timestamps do not reset or mix videos. Settings/reset rows must close prior contacts when recording changes mid-press.
- [ ] Add the guarded HTTP reader. A normal test should be able to do `read_interactions(log_path, cursor=0, limit=1)` and see one event plus a usable next_cursor, then resume without duplication or loss.
- [ ] Wire sidecar suffix into discovery, cleanup and label guards without treating the sidecar as a second recording. Close event queues/tasks cleanly and bound outstanding work.
- [ ] Run focused Python/HTTP tests, mypy/ty/Ruff and sidecar regression tests. Commit owned files and publish the exact frontend row/response contract in the report.

### Task 3: Launch and runtime controls

**Own files:** launch options/mappings/pipeline/session fields; new server/browser interaction-control module and registrations/profile mapping; related launch/result/relaunch/replay/export contracts and tests. Do not edit driver or frontend files.

**Consumes:** Task2 controller APIs. **Produces:** public launch flags and browser_set_interaction_visuals, runtime setting persistence and lifecycle cleanup.

- [ ] Add failing tests for both flags off by default; show-only/record-only independence; runtime configure validation; nav/new-page settings; session handoff/relaunch; close/failed-launch/cancellation cleanup; serialized operations and a reentrant tool call without binding deadlock.
- [ ] Thread the two boolean options through caller mappings, launch recording/result and session state. Install one controller before the browser is returned/initial target input can occur. Do not append competing init scripts on every toggle. Preserve settings in replacement launches and exports, and keep observed event rows out of replay dispatch.
- [ ] Add the MCP control with the existing browser_operation boundary; implement bounded propagation and report requested/effective state. Extend server registration/profile inventories through existing generators rather than manual conflicting counts.
- [ ] Run launch/relaunch/options/recorder/export/gate tests and an actual local-browser control smoke. Commit owned files and report interfaces for Task4/5 review.

### Task 4: Dashboard playback overlay

**Own files:** new frontend interaction-overlay.ts + tests, frontend API/types, session.ts integration and styles. No Python/observer/driver files.

**Consumes:** Task2 finalized HTTP/row contract; Task3 options do not need a second dashboard settings API. **Produces:** independent playback Show interactions control.

- [ ] Write Vitest tests first for no-data legacy videos, paginated loading, paired requests, time synchronization, pause/seek backward/speed changes, viewport scaling/letterboxing, holds/drags/cancellation, multiple contacts and video selection.
- [ ] Implement a modular overlay mounted around the existing video element. Draw only applicable video events within their lifetime; derive hold progress from video time. Suppress markers already baked into video according to recorded state. On seek rebuild state deterministically; on dispose cancel frame loops/listeners/fetches. Avoid fetching or showing browser overlay UI for terminal/plugin renderers.
- [ ] Show an unavailable explanation for old recordings and accessible labelled controls. Use the shared payload semantics; do not turn recorded metadata into page HTML.
- [ ] Run frontend unit/coverage/typecheck/Biome/build and existing session-boot/player tests. Commit owned files and report screenshot/sample paths for final verification.

### Task 5: Integration, examples, release notes and final validation

**Own files:** real browser integration/video tests, documentation, changelog0.27.0, highlights0.27.0, tool/asset/telemetry/mutation inventories as required. Integrate the separately reviewed driver compatibility fix before final gates.

- [ ] Add and run end-to-end tests covering native/manual-style and actual Octowright-driven interactions across all3engines, opt-in modes, frames/new tabs/navigation, live-mode video output and dashboard synchronization. Assert enabling feedback preserves application event counts/order and disabling it leaves no active animation/listener state beyond the dormant configured observer.
- [ ] Produce and inspect a short recorded-video example with click, right-click, tap/hold, drag and wheel feedback. Validate the dashboard with the live overlay off + metadata on, and both on without duplicate overlay markers.
- [ ] Update changelog and0.27.0 highlights with user-facing controls and the driver fix. Update usage/tool docs and packaging/typecheck inventory; run release-note validators and wheel asset checks.
- [ ] Run `make lint`, the broad nonlive/non-memory suite, focused3engine feature/live driver tests, frontend full suite and build. Review the complete branch against the approved spec and source; resolve concrete findings before release.
- [ ] Merge feature into integration checkout without disturbing original worktrees. Push final candidate to PR285. Wait required GitHub checks, merge, wait main CI, signed-tag/push v0.27.0, draft/verify/publish release, wait PyPI, install published wheel and restart/verify local daemon. Keep exact audit/evidence in release-state files and final report.

## Implementation clarifications

The final bootstrap config binding accepts read-only document geometry alongside
its clock: `{time_origin, width, height, dpr}`. Host page metadata records a stable
estimated video origin, engine, initial viewport dimensions and device scale.
These values support best-effort playback mapping; they do not promise frame-exact
synchronization. Missing geometry and Firefox observations after a viewport resize
suppress independent markers. Native markers already captured in video remain.

Standalone Python, TypeScript and macro CLI exports create ordinary Playwright
contexts and omit Octowright interaction feedback/capture. Relevant requested
launch values survive as explicitly labelled comments; application actions remain
executable. There is no standalone interaction recorder or daemon dependency.
Actual Octowright launch-record recovery and replacement retain both flags.
Interaction observations stay in the sidecar and never enter macro action replay.
