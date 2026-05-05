# Dashboard, Macro Repair, And Runner Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded test-suite parallelism, dashboard invalidation streaming with polling fallback, and macro repair preview without adding SQLite state storage.

**Architecture:** Keep Playwright/browser handles process-local. Add a small in-process dashboard event bus exposed as Server-Sent Events for invalidation only; the frontend still fetches canonical state from existing REST endpoints. Macro repair preview remains advisory and returns a proposed patch without mutating saved macros. Runner parallelism preserves isolation by launching a fresh browser per test and only runs multiple tests concurrently when explicitly requested.

**Tech Stack:** Python 3.13, Starlette, FastMCP, pytest/pytest-asyncio, TypeScript/Vite/Vitest frontend.

---

### Task 1: Bounded Parallel Test Runner

**Files:**
- Modify: `src/octowright/runner.py`
- Modify: `src/octowright/server/macros.py`
- Modify: `src/octowright/cli/test_cmd.py`
- Test: existing runner/server/CLI tests, likely `tests/test_runner.py`, `tests/test_server_macros_tools.py`, and/or `tests/test_cli*.py`

- [ ] **Step 1: Write failing tests**

Add tests proving:
- `run_suite(..., max_parallel=1)` preserves sequential behavior.
- `run_suite(..., max_parallel=2)` starts multiple tests before the first finishes, while still launching one fresh browser per test.
- every launched browser is closed even when one macro fails.
- MCP `run_test_suite` and CLI `octowright test` forward `max_parallel`.

- [ ] **Step 2: Run red tests**

Run the targeted runner/server/CLI tests with `--no-cov`. Expected: failures for missing `max_parallel` support.

- [ ] **Step 3: Implement runner concurrency**

Add `max_parallel: int = 1` to `runner.run_suite`. Validate `max_parallel >= 1`. Use `asyncio.Semaphore(max_parallel)` around a per-test coroutine that launches, runs, closes, and returns one result. Preserve output shape and JUnit writing.

- [ ] **Step 4: Wire MCP and CLI**

Add optional `max_parallel` argument to `src/octowright/server/macros.py::run_test_suite` and a CLI option in `src/octowright/cli/test_cmd.py`. Default to `1` for conservative pre-release behavior.

- [ ] **Step 5: Verify and commit**

Run targeted tests and focused ruff/mypy. Commit as `feat: add bounded macro test parallelism`.

---

### Task 2: Dashboard Invalidation Stream

**Files:**
- Create or modify: `src/octowright/http/dashboard_events.py`
- Modify: `src/octowright/http/routes/events.py` or a focused route module
- Modify: route modules that mutate sessions/scenarios/personas/macros where practical
- Modify: `packages/octowright-frontend/src/api.ts`
- Modify: `packages/octowright-frontend/src/dashboard.ts`
- Test: HTTP exposure/server tests and frontend dashboard/API tests

- [ ] **Step 1: Write failing backend tests**

Add tests proving `/api/dashboard/events` is an SSE endpoint that:
- sends `event: hello` or equivalent initial frame;
- receives an invalidation event when the bus publishes `sessions`;
- remains guarded by the dashboard exposure policy.

- [ ] **Step 2: Write failing frontend tests**

Add Vitest coverage proving `bootDashboard` opens the dashboard event stream when `EventSource` exists, refreshes immediately on an invalidation message, and keeps the existing 5s polling fallback when EventSource errors or is missing.

- [ ] **Step 3: Implement event bus and SSE route**

Implement a small in-process publish/subscribe bus for invalidation events. Use `asyncio.Queue` subscribers and an async streaming response with `text/event-stream`. Keep payloads small, e.g. `{ "scope": "sessions" }`.

- [ ] **Step 4: Publish invalidations**

Publish events after session launch/close/relaunch/delete, scenario start/stop/macro actions, persona updates, and macro save/delete where existing route/tool boundaries make this straightforward. Do not try to stream full state; stream invalidations only.

- [ ] **Step 5: Implement frontend subscription**

Add `dashboardEventsUrl()` or direct URL usage in `api.ts`. In `dashboard.ts`, subscribe via `EventSource`, call the existing `tick()` refresh on messages, and retain interval polling. On `error`, close the stream and rely on polling.

- [ ] **Step 6: Verify and commit**

Run backend targeted tests, frontend `npm run test --workspace=packages/octowright-frontend`, and focused lint/type checks. Commit as `feat: stream dashboard invalidations`.

---

### Task 3: Macro Repair Preview

**Files:**
- Modify: `src/octowright/macros.py`
- Modify: `src/octowright/server/macros.py`
- Modify: `src/octowright/types.py` or docs only if needed
- Test: `tests/test_macro_healing.py`, `tests/test_server_macros_tools.py`, macro docs tests if present
- Docs: `README.md` and/or `docs/macros.md`

- [ ] **Step 1: Write failing tests**

Add tests proving a repair preview:
- accepts a macro name and failed action index;
- uses the current session snapshot/A11y tree path to generate a suggested replacement;
- returns a preview object with original action, proposed action or suggestion text, and `apply: false` semantics;
- does not mutate the saved macro.

- [ ] **Step 2: Run red tests**

Run macro healing/server macro tests with `--no-cov`. Expected: missing preview API/tool failures.

- [ ] **Step 3: Implement preview helper**

Add a helper such as `preview_macro_repair(session, name, step_index, args=None)` that loads/substitutes the macro, selects the action, calls existing `_suggest_fix`, and returns a structured preview. Keep the existing failure-path `_suggest_fix` behavior.

- [ ] **Step 4: Expose MCP tool**

Add a FastMCP tool, e.g. `macro_repair_preview(instance_id, name, step_index, args=None)`, returning the structured preview. Do not add an auto-apply tool in this pass.

- [ ] **Step 5: Document and verify**

Document that repair preview is advisory and must be manually saved with `macro_save`. Run targeted tests and focused ruff/mypy. Commit as `feat: add macro repair preview`.

---

### Task 4: Final Integration Verification

**Files:**
- All changed files

- [ ] **Step 1: Run Python tests**

Run `uv run pytest -q tests/`. Expected: pass and coverage remains above gate.

- [ ] **Step 2: Run quality gate**

Run `make lint`. Expected: ruff, format, mypy, ty, bandit, codespell, SPDX pass.

- [ ] **Step 3: Run frontend tests**

Run `npm run test --workspace=packages/octowright-frontend`. Expected: Vitest passes.

- [ ] **Step 4: Inspect final status**

Run `git status --short` and `git diff --stat`. Expected: clean after commits.

---

## Plan Self-Review

- Spec coverage: the plan covers all approved items and explicitly excludes SQLite/external store work.
- Placeholder scan: no placeholder implementation steps remain; each task has concrete files, behavior, verification, and commit messages.
- Type consistency: names are intentionally tentative only where the worker must match existing code patterns; exposed concepts are stable: `max_parallel`, `/api/dashboard/events`, and macro repair preview.
