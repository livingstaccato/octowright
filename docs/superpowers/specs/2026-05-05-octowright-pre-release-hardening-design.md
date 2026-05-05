# Octowright Pre-Release Hardening Design

Date: 2026-05-05

## Context

The code review found several pre-release issues that should be fixed as a final architecture, not as an interim compatibility layer:

- Python tests fail during collection because test imports expect helper symbols from `octowright.pool` that are no longer exported there.
- Two divergent `BrowserPool` implementations exist: `octowright.pool.BrowserPool`, used by the server, and `octowright.browser_pool.runtime.BrowserPool`, imported by at least one test.
- `session=True` launch state is not stored on the active `BrowserSession`, so handoff can treat session-scoped profiles as stateless.
- Browser launch can leak a created context/browser if initialization or initial navigation fails before the session is registered.
- Runtime modules import `httpx`, but `httpx` is not declared as a project dependency.
- Dashboard HTTP routes expose sensitive data and mutating operations if the server is bound beyond localhost.
- Network request capture is unbounded.
- Fire-and-forget session background tasks can outlive recorder closure.

Because Octowright is pre-release, the fix should remove split-brain internal states directly instead of preserving migration shims.

## Goals

1. Make `octowright.pool.BrowserPool` the only browser-pool implementation.
2. Remove the duplicate `octowright.browser_pool.runtime.BrowserPool` implementation and update tests to target the canonical pool.
3. Replace HTTP and server code's direct private-state reads with public pool and scenario APIs.
4. Make launch failure cleanup deterministic.
5. Make `session=True` handoff work as a first-class mode.
6. Make dashboard exposure policy explicit and deny sensitive operations when bound to non-loopback hosts unless the user explicitly opts in.
7. Bound in-memory session event growth and close background tasks before closing the recorder.
8. Restore a passing Python and frontend test suite.

## Non-Goals

- Preserve `octowright.browser_pool.runtime.BrowserPool` as a compatibility import.
- Add a general authentication system for the dashboard.
- Redesign the frontend UI.
- Change the public MCP tool names or basic request/response shapes unless needed for the hardening behavior.

## Architecture

`src/octowright/pool.py` remains the canonical pool module. The server singleton already imports it, most tests already target it, and it is the simplest final public path.

Reusable helper code can stay in focused modules, but there must be only one `BrowserPool` class. Visual helpers should live in `src/octowright/browser_pool/visuals.py`; the duplicate runtime module should not remain. The import policy is:

- Tests and internal implementation import visual helpers from `octowright.browser_pool.visuals`.
- `octowright.pool` exports `BrowserPool` and pool lifecycle behavior, not visual-helper test internals.
- There must be no second runtime implementation that can diverge from server behavior.

`BrowserPool` should expose a small public state API so HTTP and MCP code do not read `_sessions` directly:

- `get(instance_id) -> BrowserSession`
- `maybe_get(instance_id) -> BrowserSession | None`
- `has_session(instance_id) -> bool`
- `iter_sessions() -> Iterable[BrowserSession]`
- `list_sessions() -> list[dict[str, Any]]`
- `active_count() -> int`
- `profile_in_use(kind, profile) -> bool`
- existing lifecycle methods such as `launch`, `close`, `close_all`, `handoff`, and `spawn_roster`

`ScenarioPool` should expose matching public APIs for live scenario access:

- `get(scenario_id) -> LiveScenario`
- `maybe_get(scenario_id) -> LiveScenario | None`
- `has_live(scenario_id) -> bool`
- `list_live() -> list[dict[str, Any]]`

HTTP discovery and route handlers should use these public APIs. Direct access to `_sessions` and `_live` should be restricted to pool internals and narrowly scoped tests that construct fake state.

## Launch Lifecycle

Launch should be fail-safe after any Playwright object is created. If browser/context/page creation succeeds and later initialization fails, the pool should close whatever was created before re-raising:

- init-script injection failures
- tracing start failures
- initial `page.goto()` failures
- markdown capture scheduling failures if they become synchronous errors

Cleanup must close context and browser as applicable, close the recorder if it was opened, and avoid registering the failed session as live.

Playwright errors should use the existing engine sanity-hint behavior from `browser_pool/errors.py`, folded into the canonical pool path. Launch and initial navigation failures should surface useful messages without hiding the original exception.

## Session Mode And Handoff

`session=True` uses a daemon-lifetime temporary persistent context directory keyed by `(label or profile or "anon", kind)`. The resulting `BrowserSession` must store the concrete `user_data_dir`.

Handoff should preserve state according to source mode:

- Persistent profile source: close original before launching the replacement against the same profile unless explicitly stateless and accepted.
- Session-scoped tmpdir source: preserve the same tmpdir by relaunching with session-scoped semantics when no explicit target profile is supplied.
- Stateless source: refuse by default unless `accept_stateless=True`.

The active implementation and tests should cover persistent profile handoff, session tmpdir handoff, and stateless refusal.

## Dashboard Exposure Policy

The dashboard remains unauthenticated and convenient on loopback addresses. When the HTTP server is bound to a non-loopback host, sensitive routes must be denied by default unless an explicit opt-in environment variable is set.

Loopback hosts include `127.0.0.1`, `localhost`, and `::1`. Non-loopback includes `0.0.0.0`, `::`, LAN IPs, and public interfaces.

Add the explicit opt-in setting `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1`. Without this setting, non-loopback binding should block:

- session launch, close, navigate, recording delete
- scenario start, stop, macro dispatch
- persona YAML update
- trace open
- live screenshots
- video, trace, markdown, screenshot file serving
- endpoints that reveal raw captured data or local artifact paths

`GET /api/health` can remain public. Low-risk catalogue endpoints may remain readable only if they do not expose sensitive paths or captured content. When in doubt, sensitive-by-default wins.

Credential command support remains local-trusted behavior. Persona `_cmd` credentials can stay because they are part of the persona model, but remote persona YAML writes must not be possible by default.

## Session Memory And Background Tasks

Network request capture should be bounded. Replace the unbounded list with a bounded collection, using `OCTOWRIGHT_NETWORK_EVENT_LIMIT` with a default of 5000 retained events per session. Cursor behavior should continue to work within the retained window and should report retained count plus dropped count clearly.

Session close should drain or cancel background tasks before closing the recorder. Downloads, dialog handlers, markdown capture, and related tasks should get a short best-effort timeout. Late failures should be recorded when possible, but recorder closure should be deterministic.

## Testing

Tests should prove the final architecture rather than preserving old import paths.

Required Python coverage:

- Python suite collection succeeds.
- No tests import `octowright.browser_pool.runtime.BrowserPool`.
- The duplicate runtime implementation is gone.
- Visual helper imports use the chosen single source.
- `session=True` stores `user_data_dir`.
- Handoff preserves session-scoped state, preserves persistent profiles, and refuses stateless sources by default.
- Launch cleanup closes context/browser/recorder after initialization or initial navigation failure.
- Playwright launch/navigation errors include engine sanity hints when available.
- HTTP routes use public pool/scenario APIs where practical.
- Non-loopback HTTP binding blocks sensitive routes unless explicitly opted in.
- Network request capture is bounded.
- Session close drains or cancels background tasks before recorder closure.
- `httpx` is declared as a runtime dependency.

Required verification commands:

- `uv run pytest -q tests/`
- `make lint` or equivalent targeted lint/typecheck commands if the full lint target is too slow during iteration
- `npm run test` from `packages/octowright-frontend`

## Rollout

This is a pre-release cleanup. Implement it as one cohesive architectural hardening change. Do not add compatibility shims for the duplicate pool runtime.

The expected end state is:

- one canonical browser pool
- one live-state access layer
- explicit dashboard exposure policy
- fail-safe launch lifecycle
- first-class session-scoped handoff
- bounded session memory
- deterministic background task shutdown
- passing Python and frontend tests
