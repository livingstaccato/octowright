# Octowright Review Findings Hardening Design

Date: 2026-05-05

## Scope

Address the four concrete issues found in the code review while keeping the change set narrow:

1. Scenario startup must fail if required startup macros fail.
2. Scenario participant remaps must reject invalid replacement instances.
3. HTTP session launch must match the MCP/browser-pool launch default semantics.
4. HTTP MCP session tracking must not leak stale global state across app instances.

Additionally, the changes should prepare the code for future dependency injection by reducing hidden reliance on module globals where practical, without attempting a full container refactor in this batch.

## Non-Goals

- No broad rewrite of MCP tool registration.
- No replacement of the module-global server state model across the whole project.
- No changes to public API shapes unless needed to make failures explicit and correct.
- No speculative performance refactor of dashboard recording discovery in this batch.

## Approach Options

### Option 1: Targeted behavioral fixes in existing modules

Patch the affected functions in place, add regression tests, and add small seams that make later dependency injection easier.

Pros:

- Lowest risk.
- Smallest review surface.
- Preserves current public surface.

Cons:

- Leaves the larger singleton architecture in place.

### Option 2: Introduce shared service/helper layers now

Move launch and scenario validation logic behind new shared services and have HTTP and MCP both call them.

Pros:

- Cleaner architecture.
- Better DI story immediately.

Cons:

- Larger refactor than the findings require.
- Higher regression risk in a busy dirty tree.

### Option 3: Full DI-oriented refactor of server/http state

Replace module globals with explicit app/service wiring and thread those dependencies through handlers and tools.

Pros:

- Best long-term architecture.

Cons:

- Far too large for the current scope.
- Would mix correctness fixes with invasive structural work.

## Chosen Design

Use Option 1. Fix the behavior in place, but add small seams that make future DI migration easier:

- pass explicit `browser_pool` references where validation needs runtime checks;
- centralize app-local MCP session manager reset behavior in `http.app`;
- keep tests focused on the intended contract rather than implementation details.

## Detailed Design

### 1. Fatal startup macro failures

`ScenarioPool.start()` currently treats launch and fixture failures as fatal, but startup macro failures are downgraded to warnings. That leaves callers with a live scenario whose bootstrap is incomplete.

Change:

- `_run_startup_macros()` will accumulate per-participant failures.
- If any startup macro fails, it will raise a structured `RuntimeError` summarizing persona, macro, and error.
- `ScenarioPool.start()` will keep its existing cleanup path: remove the live scenario entry and close every launched participant before re-raising.

Behavioral contract:

- A scenario either starts fully, or it is torn down and reported as failed.
- Partial startup success is not considered a valid live scenario.

### 2. Scenario remap validation

`ScenarioPool.remap_participant()` currently mutates scenario state without verifying the replacement instance.

Change:

- Extend remap validation to require a live replacement instance from the provided `browser_pool`.
- Verify that the replacement session kind matches the existing participant kind.
- Verify persona/profile compatibility when possible:
  - if the participant has a persona and the replacement session has a `profile`, they must match;
  - if the replacement is stateless and therefore has no profile, allow it only when there is no contradictory profile data to reject.

Interface change:

- `ScenarioPool.remap_participant()` and `remap_participants()` will take `browser_pool` explicitly.
- `server.scenarios.scenario_remap_participants()` will pass the shared pool through.

DI preparation:

- This avoids reaching into `server._state.pool` from inside `ScenarioPool`, which makes the scenario state object less tightly bound to global runtime state.

### 3. HTTP launch semantics parity

`POST /api/sessions` currently injects `headed=True` when omitted, which overrides the browser pool's environment-sensitive default behavior.

Change:

- Preserve `headed=None` when the client omits it.
- Continue to pass explicit `True` or `False` unchanged when the client provides a value.

Behavioral contract:

- Dashboard launches and MCP launches should resolve headless/headed mode the same way when the caller has not explicitly chosen.

### 4. App-local MCP session manager reset

`http.app._mcp_session_manager` is a module-global cache used by the idle watchdog. It is currently set when building a leader app, but not explicitly cleared when building a non-leader app.

Change:

- `build_app(mcp_leader=False)` will explicitly clear `_mcp_session_manager`.
- `build_app(mcp_leader=True)` will continue to populate it from the mounted MCP transport.

Behavioral contract:

- The active MCP session count reflects the app most recently built for the current process.
- Non-leader app builds must not inherit leader-only state from a prior build.

DI preparation:

- This change defines the cache lifecycle more clearly, which makes later replacement with app-scoped state or an injected session counter more straightforward.

## Error Handling

- Scenario startup failures will surface as explicit `RuntimeError`s with participant-level detail.
- Remap validation failures will raise `ValueError` for bad caller input and `KeyError` only for existing "no such scenario" behavior.
- HTTP launch input validation remains unchanged except for omission semantics of `headed`.

## Testing Strategy

Add focused regression tests first:

1. Scenario start fails and cleans up when a startup macro fails.
2. Scenario remap rejects:
   - unknown replacement instance id;
   - mismatched browser kind;
   - mismatched profile/persona when both are known.
3. HTTP session launch passes `headed=None` when omitted and preserves explicit booleans.
4. Building a non-leader app clears stale `_mcp_session_manager`.

Tests should verify observable behavior, not private implementation details, except where the app-level MCP session cache is the contract being hardened.

## Implementation Notes

- Keep edits limited to `scenarios_pool.py`, `server/scenarios.py`, `http/routes/sessions.py`, `http/app.py`, and their tests unless new failures reveal a narrower helper is needed.
- Do not broaden the change into a general service abstraction yet.
- If a helper is extracted, it should be small and directly motivated by one of the four fixes.

## Risks

- Tightening scenario startup may break tests or user flows that implicitly relied on "warn and continue" behavior. That is an intentional contract correction.
- Persona/profile validation during remap must be careful not to over-reject stateless handoffs that are intentionally allowed elsewhere.

## Success Criteria

- All four findings are closed with regression coverage.
- Existing tests continue to pass.
- The touched code is slightly easier to wire with explicit dependencies later, rather than more dependent on module globals.
