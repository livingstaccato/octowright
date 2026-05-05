# Browser Pool Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move browser pool code into one cohesive `octowright.browser_pool` package, remove old top-level pool modules, and harden concurrent state transitions.

**Architecture:** `octowright.browser_pool` becomes the sole pool implementation package. `BrowserPool` is exported from `octowright.browser_pool`; there is no compatibility `octowright.pool` facade. Pool launch/registry code lives in `browser_pool/pool.py`, close/handoff/shutdown lifecycle helpers in `browser_pool/lifecycle.py`, roster helpers in `browser_pool/roster.py`, visual helpers in `browser_pool/visuals.py`, listener wiring in `browser_pool/listeners.py`, and Playwright error normalization in `browser_pool/errors.py`.

**Tech Stack:** Python 3.13, Playwright async API, pytest, ruff, mypy.

---

### Task 1: Module Move And Import Update

**Files:**
- Create: `src/octowright/browser_pool/pool.py`
- Create: `src/octowright/browser_pool/lifecycle.py`
- Create: `src/octowright/browser_pool/roster.py`
- Modify: `src/octowright/browser_pool/__init__.py`
- Delete: `src/octowright/pool.py`
- Delete: `src/octowright/pool_support.py`
- Delete: `src/octowright/pool_roster.py`
- Modify imports in `src/octowright/**/*.py` and `tests/**/*.py`

- [x] Move `BrowserPool` implementation from `src/octowright/pool.py` to `src/octowright/browser_pool/pool.py`.
- [x] Move `src/octowright/pool_roster.py` to `src/octowright/browser_pool/roster.py`.
- [x] Replace `pool_support` imports with `browser_pool.visuals` and `browser_pool.listeners`.
- [x] Export `BrowserPool`, `close_all`, and `spawn_roster` from `src/octowright/browser_pool/__init__.py`.
- [x] Update every import from `octowright.pool` or relative `.pool` to `octowright.browser_pool`.
- [x] Delete old top-level pool modules.
- [x] Run `rg "octowright\\.pool|from \\.pool|pool_support|pool_roster" src tests` and expect no results except historical prose if any.
- [x] Run focused tests: `uv run pytest --no-cov tests/test_pool_state_api.py tests/test_roster.py tests/test_handoff.py`.
- [ ] Commit with `refactor: consolidate browser pool package`.

### Task 2: Pool Registry Concurrency

**Files:**
- Modify: `src/octowright/browser_pool/pool.py`
- Test: `tests/test_pool_state_api.py` or `tests/test_pool_launch_cleanup.py`

- [x] Add `self._sessions_lock = asyncio.Lock()` to `BrowserPool`.
- [x] Protect registry mutation paths that span awaits: launch registration, close removal, close-all snapshot, external close eviction helper as needed.
- [x] Preserve explicit-close behavior: remove before `session.close()` so external close listeners no-op.
- [x] Add tests for concurrent close of the same session: one close succeeds, the other gets a deterministic missing-session error, and the registry ends empty.
- [ ] Add tests for external evictor operating after explicit close: no duplicate entry and no exception.
- [x] Run focused pool tests.
- [ ] Commit with `fix: harden browser pool registry transitions`.

### Task 3: Scenario Pool Concurrency

**Files:**
- Modify: `src/octowright/scenarios_pool.py`
- Test: `tests/test_scenarios_unit.py`

- [x] Add an async lock to `ScenarioPool`.
- [x] Hold the lock for `_live` registration/removal/remap operations.
- [x] Avoid holding the lock while launching browsers or running teardown macros unless needed for a specific invariant.
- [x] Make `stop()` remove the live scenario exactly once before closing participants so concurrent stop calls cannot double-close.
- [x] Add tests for concurrent stop of the same scenario.
- [x] Run focused scenario tests.
- [ ] Commit with `fix: harden scenario pool transitions`.

### Task 4: Tail Log Regression And Final Verification

**Files:**
- Modify: `tests/test_recording_tail.py` or add focused `tests/test_recorder.py`
- Modify: `src/octowright/recorder.py` only if tests expose a bug.

- [x] Add regression tests that `tail_log()` handles partial UTF-8 lines and large chunks without moving cursor past incomplete JSON.
- [x] Keep implementation if tests pass; optimize only if a test exposes a concrete behavior/performance issue.
- [x] Run final verification:
  - `uv run pytest --no-cov tests/test_pool_state_api.py tests/test_roster.py tests/test_handoff.py tests/test_scenarios_unit.py tests/test_recording_tail.py tests/test_http_server.py tests/test_http_server_writes.py`
  - `uv run mypy src/octowright`
  - `uv run ruff check src tests`
  - `npm run test --workspace=packages/octowright-frontend`
  - `npm run typecheck --workspace=packages/octowright-frontend`
  - `npm run lint --workspace=packages/octowright-frontend`
- [ ] Commit final test/verification cleanup if needed.
