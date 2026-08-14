# Browser Session Operation Gate — Design Spec

**Date:** 2026-08-13  
**Status:** Approved in brainstorming; pending written-spec review  
**Topic:** Serialize Playwright access within one browser session while preserving parallelism across sessions.

## 1. Summary

Octowright needs one session-owned operation gate before it can safely add compound browser primitives such as accessible keyboard drag-and-drop. Today, independent MCP requests, macro replay, background capture, recovery, and dashboard-related work can reach the same `BrowserSession` without one shared operation boundary. A multi-stage primitive could therefore be interleaved by another call halfway through its state machine.

The gate provides FIFO, per-browser serialization. It is task-reentrant so a compound operation can call existing session helpers without deadlocking, but a spawned child task cannot silently inherit ownership. One macro invocation holds one lease for its entire run. Different browser sessions remain fully parallel.

This is a browser-session infrastructure project, not the accessible drag/drop implementation. The project order is:

1. Design, plan, and implement this browser session operation gate.
2. Perform the separately scoped repository-wide code- and feature-DRY audit, then handle its resulting subprojects.
3. Replace the current `browser_a11y_dragdrop` design with a separate revised spec based on those foundations.
4. Plan and implement accessible drag/drop from that revised spec.

The existing mouse-based `browser_drag` remains unchanged. The current unimplemented accessible drag/drop spec must not be used as an implementation plan.

## 2. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Ownership | One gate owned by each `BrowserSession`; no global or per-feature locks |
| 2 | Admission | FIFO waiting for ordinary operations, bounded by a configurable queue timeout |
| 3 | Reentrancy | Only the exact owning `asyncio.Task` may re-enter |
| 4 | Coverage | Serialize all Octowright-owned Playwright operations and mutable target state; cached/pool-only reads remain concurrent |
| 5 | Compound work | A macro, macro sequence, artifact replay, or other compound helper holds one lease for its entire logical invocation |
| 6 | Close | Normal close establishes a cutoff, drains work already queued, then closes; it does not preempt active work |
| 7 | Protection race | Protection changes and close reservation are linearized by one short control-plane mutex; whichever commits first wins |
| 8 | Failure containment | Gate errors fail one tool/session, never restart or disconnect the MCP server and never affect another browser |
| 9 | Future interactive control | Reserve a clean admission seam for a separate expiring **control lease** and user-facing **Take control** workflow; do not implement pause/step/resume here |

## 3. Goals and scope

### In scope

- Deterministic, FIFO serialization within one live browser session.
- Exact-task reentrancy for nested session helpers.
- A bounded queue wait for ordinary work, independently configurable by environment or `BrowserPool` construction.
- Durable, coalesced close coordination.
- Whole-invocation leases for macros and other compound operations.
- Coverage of foreground MCP calls, direct Python callers, macro replay, and Octowright-owned background Playwright work.
- Sanitized operation-state observability for MCP status, HTTP/dashboard consumers, logs, and metrics.
- An architecture check that makes newly introduced Playwright access fail CI until it is gated or explicitly classified.
- Targeted extraction of the existing `expect_*` implementation so gate annotations do not push `core_page_mixin.py` through the repository LOC ceiling.

### Out of scope

- Accessible keyboard drag/drop.
- The repository-wide DRY audit or unrelated deduplication.
- Terminal-session serialization.
- Cross-session or process-global serialization.
- Automatic action retries.
- User-visible lock tokens.
- Per-tool queue-timeout parameters.
- Control leases, Take control, macro pause, macro step, or macro resume.

### Guarantee boundary

The gate serializes work initiated by Octowright. It cannot freeze JavaScript timers, network-driven DOM changes, page-initiated navigation, a human operating a headed browser, renderer crashes, or browser/OS closure. Those external events may still change or invalidate the page during an admitted operation. The gate guarantees that a second Octowright operation did not cause the interleaving, and that external closure is contained to the affected session.

## 4. Existing-code fit

`BrowserSession` is already the object shared by the session mixins, and it is the narrowest layer common to MCP tools, macros, pool lifecycle, and background session work. The gate therefore belongs to the session rather than an MCP wrapper.

An MCP-wrapper-only design is insufficient because several server modules call `Page`/`Frame` directly, while markdown capture and crash recovery run outside normal browser tool wrappers. A Playwright proxy is also rejected: it would hide logical operation boundaries and could not infer whether a nested call, event callback, or compound workflow should share ownership.

Macro replay already loops over the substituted actions inside a single `run_macro` coroutine. An outer macro lease can therefore cover the existing dispatch loop, including status-pill updates, slow-motion delays, failure diagnostics, and final status, without changing macro input or output schemas.

## 5. Components

### 5.1 `SessionOperationGate`

Add `src/octowright/session/operation_gate.py` containing:

- `SessionOperationGate`
- `OperationGateState`: `open`, `closing`, `closed`, `broken`
- `SessionBusyTimeoutError`
- `SessionClosingError`
- `SessionClosedError`
- `OperationGateInvariantError`
- the single configuration resolver for the environment default

The gate owns:

- a FIFO deque of waiter records;
- the exact owning `asyncio.Task`, root operation name, acquisition time, and reentrancy depth;
- the current state;
- a short admission/control mutex;
- the ordinary-operation queue timeout;
- a close reservation/shared outcome reference;
- monotonic timestamps needed for sanitized diagnostics and metrics.

The gate does not import Playwright, the MCP server, macro modules, the recorder, or `BrowserPool`.

The FIFO and control mutex are event-loop-owned scheduling primitives. The sanitized status view is published as an immutable snapshot behind a small thread-safe diagnostics guard so existing synchronous status callers can read it without touching the asyncio queue. That diagnostics guard never grants operation ownership or participates in admission.

### 5.2 `BrowserSession` integration

`BrowserSession` owns exactly one gate and exposes thin internal methods:

```python
async with session.operation("browser_click"):
    ...

session.operation_snapshot()
```

Session methods that touch Playwright or mutate the active page/frame/dialog targeting state use an explicit `@gated_operation("fixed_name")` decorator from the same module. Operation names are fixed identifiers and never contain selectors, URLs, macro arguments, credentials, or arbitrary user text.

The decorator is defensive for direct Python callers. A compound caller first acquires an outer lease; decorated methods invoked by that same task re-enter it. The root operation remains the observable active operation.

### 5.3 Pool lifecycle coordination

`BrowserPool` owns the durable close coordinator because it owns session lookup, identity-aware eviction, recently-evicted diagnostics, manifest cleanup, and close notifications.

While an accepted close is waiting for earlier tickets, the session remains resolvable in the active registry and its gate reports `closing`. When close obtains its ticket, the pool moves the identity into a private closing registry before awaiting `session.close()`, preserving the existing requirement that synchronous Playwright close listeners not treat an explicit close as an external eviction. Duplicate close requests consult both registries and await the same coordinator outcome.

Pool shutdown and external last-page cleanup reuse the same teardown implementation; they do not create another operation lock.

### 5.4 Expectation mixin extraction

`core_page_mixin.py` is already at the repository LOC ceiling boundary. Move `_poll_until` and the `expect_url`, `expect_text`, `expect_selector`, and `expect_js` methods, without behavior changes, to `core_expect_mixin.py`. Compose `SessionExpectMixin` into `BrowserSession` beside the existing mixins. This extraction is limited to making the gate work within the existing LOC policy.

## 6. Admission and execution semantics

### 6.1 Ordinary operation

1. Resolve the session.
2. Perform only pure argument validation that does not inspect or mutate browser/session targeting state.
3. Request a FIFO ticket.
4. Await admission, bounded by the effective queue timeout.
5. Start any Playwright/action-specific timeout only after admission.
6. Execute the complete logical operation, including browser-derived response construction, behavioral recording, and failure diagnostics.
7. Release in `finally`.

If the same owning task enters again, the gate increments a depth counter without enqueuing. Only the outermost exit releases ownership and wakes the next waiter. A spawned child task has a different identity, receives its own ticket, and cannot inherit the parent lease through a context variable.

An operation exception propagates unchanged and does not poison the gate. The gate does not retry any browser operation.

### 6.2 Queue timeout and cancellation

The default ordinary queue timeout is 300 seconds. Queue time does not consume a Playwright action, navigation, wait, or verification timeout.

If a ticket expires:

- remove it atomically from the queue;
- raise `SessionBusyTimeoutError`;
- execute no operation body;
- append no behavioral recording;
- wake the next eligible waiter if necessary.

Cancellation of an ordinary waiter has the same no-side-effect removal behavior. Cancellation of an active direct call releases ownership in `finally`.

For keyed MCP calls, the existing idempotency producer intentionally continues across a transport/request cancellation. A reconnect with the same key therefore observes the same producer and the same gate ticket. The finite queue ceiling prevents an abandoned ordinary ticket from waiting forever and unexpectedly executing arbitrarily later.

### 6.3 Macro and compound operations

The following hold one root lease for their complete logical invocation:

- one macro, including nested `macro_call` actions;
- one `macro_run_sequence`, including every member macro;
- macro artifact replay and its evidence capture;
- capture-and-close;
- handoff with `close_original=True`, or fluid relaunch, of the source session;
- any future accessible drag/drop attempt.

A non-closing handoff uses an ordinary root lease and does not transition the source gate to `closing`.

Failure diagnostics execute reentrantly before the root lease is released, so another caller cannot alter the page between the failed action and its evidence capture.

### 6.4 Session-level parallelism

The gate is per `BrowserSession`. Operations against different instance IDs never wait on one another because of this feature. Pool registry locks remain short metadata/lifecycle locks and are not held while awaiting a session operation.

An orchestrator that touches multiple sessions must not hold one session lease while waiting to acquire another. `close_all` first establishes independent close reservations, then awaits their coordinators without nested cross-session ownership.

## 7. Close and protection state machine

### 7.1 Protection versus close

`set_protected` and close reservation use the same short admission/control mutex:

- If protection commits first, an unforced close is refused and the gate remains `open`.
- If close reservation commits first, the gate becomes `closing`; subsequent protection changes receive `SessionClosingError`.
- `force=True` bypasses the protection refusal but not FIFO draining.

There is no delayed protection recheck and no temporary reopen path.

### 7.2 Accepted close

An accepted close:

1. atomically changes `open` to `closing`;
2. inserts one close ticket behind every ticket already admitted or queued;
3. rejects later ordinary work with `SessionClosingError` rather than queueing it;
4. runs session close and lifecycle cleanup when its ticket reaches the front;
5. transitions to `closed` and wakes all remaining exceptional waiters;
6. stores one outcome for callers that duplicate the close while its reservation/coordinator is retained.

The coordinator is a pool/session-owned task, not the requesting MCP task. Canceling a close caller does not revoke an accepted close or strand the session in `closing`. All close callers that arrive while the reservation/coordinator is retained observe the same success or failure. Once cleanup removes the closing-registry entry, a later close uses the existing "no such instance" behavior rather than retaining close results indefinitely.

If external closure wins after close was accepted, the coordinator completes cleanup rather than trying to close twice and returns the normal close result with `closed=True`; artifact paths remain nullable as they are today. If teardown reports an error, every coalesced caller receives that same error; the unusable session is still evicted and the gate remains `closed`.

### 7.3 External closure and broken state

Playwright-originated page/context/browser closure cannot acquire the gate before it happens. Its synchronous listener:

- transitions the gate to `closed`;
- fails queued tickets with `SessionClosedError`;
- lets the active Playwright call receive its normal page/context/driver-closed failure;
- schedules teardown-only cleanup that may run after gate closure.

An internal ownership/state invariant failure transitions only that session gate to `broken`, wakes its waiters with `OperationGateInvariantError`, and requires that browser session to be closed. Explicit close is routed through the teardown-only path when the gate is broken; it does not need to acquire ordinary ownership. A broken gate must not restart the daemon, close other sessions, or terminate the MCP transport.

## 8. Operation coverage policy

### Gated user operations

All Octowright-owned calls through `Page`, `Frame`, `BrowserContext`, locators, keyboard, or page screencast lifecycle are gated. Mutable active-target operations such as page/frame switch and dialog policy changes are also gated. Page/frame listing helpers become async internally so they can return a coherent snapshot under the gate; their MCP names, arguments, and results do not change.

This is an internal Python API migration: direct callers of `BrowserSession.list_pages()`, `list_frames()`, or `set_dialog_policy()` must await them after this change. Update all in-repository callers and document the change for embedders. Do not add a synchronous shim that blocks an active event loop.

Server functions that currently build feature-specific results through direct `Page`/`Frame` access wrap their complete browser-derived workflow in `session.operation(...)`. This includes discovery, inspection, assertions, captures, goldens, and capture-and-close—not just tools in `server/browser/input.py`.

### Gated background/system operations

- Markdown capture queues normally and uses the ordinary timeout.
- Screencast start, rebind, and stop queue normally. Delivery of frames from an already-started producer does not acquire per frame.
- Crash recovery is a durable system operation with no ordinary queue timeout. It queues behind the operation that encountered the crash and is invalidated by session closure.

### Event-critical callbacks

Dialog accept/dismiss and route fulfill/continue callbacks are allowed to execute while a root operation is active because the root Playwright call may be waiting for those callbacks. They are not independent user operations, do not acquire a second lease, and must contain only the callback response and its passive recording/error handling.

### Concurrent cached/control reads

Reads that use only Octowright-owned cached or pool metadata remain concurrent. Examples include pool/session listing metadata, recording paths, buffered console/network events, and saved-download metadata. They must not dereference a live `Page`/`Frame` or mutate active targeting state.

### Launch and teardown classifications

Launch-time Playwright work performed before a `BrowserSession` is published is classified `launch-time-before-session-publication`. External-close and shutdown cleanup that must run after the gate is closed is classified `teardown-only`.

## 9. Architecture enforcement

Add a repository architecture test/scanner for common Playwright access paths, including `page`, `context`, `_target()`, locators, keyboard, and screencast operations. Every production-code hit must be inside a gated boundary or have one narrow, reason-bearing classification:

- `event-critical`
- `teardown-only`
- `cached-property-only`
- `launch-time-before-session-publication`

The scanner fails CI for new unclassified access. Classifications identify why an access may bypass admission; they never implement scheduling and are not a second source of gate behavior.

## 10. Configuration and observability

### Configuration

- Environment: `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS`
- Default: `300`
- Valid value: positive, finite seconds
- Embedder override: `BrowserPool(operation_queue_timeout_seconds=...)`
- Precedence: explicit pool argument, then environment, then default

Invalid values fail clearly while the pool/configuration is initialized. A server configuration whose queue timeout is at or beyond the effective heartbeat ceiling is allowed but emits a warning explaining that a waiting caller may lose transport visibility first. Close coordinators and crash recovery do not use the ordinary timeout.

The timeout is intentionally not copied into every MCP tool schema.

### Sanitized snapshot

The gate exposes one snapshot shape:

```python
{
    "state": "open" | "closing" | "closed" | "broken",
    "active_operation": str | None,
    "active_for_ms": int | None,
    "queue_depth": int,
    "oldest_wait_ms": int | None,
    "queue_timeout_seconds": float,
}
```

`BrowserPool.list_sessions()` adds this as an optional browser-session field. The existing MCP browser-list and HTTP/dashboard session data flows reuse that same snapshot; no parallel status computation is introduced. Terminal session rows may omit it because terminal gating is out of scope.

The frontend consumes the optional field through its existing session model. A compact status indicator shows `busy` with the fixed operation name and queue depth, `closing`, or `broken`; it does not add a second polling endpoint or expose operation arguments. The normal idle state needs no additional visual noise.

Operation names are fixed identifiers. Logs and metrics include state, operation name, wait duration, queue depth, outcome, and browser kind, but never arguments, selectors, URLs, credentials, task identities, or control-lease identities.

Use one metric family with fixed low-cardinality operation names: queue-wait and active-duration histograms, queue-timeout and rejected-operation counters, and current queue depth. Rejection reason and browser kind are bounded attributes; instance ID is reserved for logs and status rather than metric attributes.

Gate acquisition/release/timeout is scheduling metadata and is never written to browser JSONL. Macro replay and script export continue to consume only behavioral actions.

## 11. Error contract

| Error | Meaning | Browser side effects from rejected operation |
|---|---|---|
| `SessionBusyTimeoutError` | FIFO ticket expired before admission | None |
| `SessionClosingError` | Call arrived after the close cutoff | None |
| `SessionClosedError` | External/completed closure invalidated the session | None from the rejected call |
| `OperationGateInvariantError` | Internal ownership/state corruption | Unknown for the active operation; none for queued operations |

All four are distinct `RuntimeError` subclasses, not aliases of built-in or Playwright timeout classes. Messages include only the instance ID, fixed operation name, gate state, and relevant duration/depth values.

Exceptions raised by an admitted operation propagate through the existing tool boundary unchanged and release the gate. They do not close the browser or poison the gate unless the gate's own ownership invariant is broken.

Argument validation may run before admission only when it is pure. Any validation that reads `Page`, `Frame`, context, active target, dialog policy, page collection, or other mutable session targeting state runs inside the lease.

A gate error is a tool/session error. It never requests daemon restart, closes the MCP transport, or closes an unrelated browser.

## 12. File layout and integration points

```text
src/octowright/session/operation_gate.py       # gate, decorator, errors, env resolver
src/octowright/session/core.py                 # one gate per BrowserSession; thin accessors
src/octowright/session/core_expect_mixin.py    # extracted polling + expect_* methods
src/octowright/session/core_*_mixin.py         # explicit gated_operation annotations
src/octowright/session/core_io_mixin.py        # gated markdown capture
src/octowright/session/screencast.py            # gated lifecycle changes

src/octowright/browser_pool/pool.py             # per-pool timeout, closing registry
src/octowright/browser_pool/lifecycle.py        # close reservation/coordinator
src/octowright/browser_pool/crash_recovery.py   # durable system operation
src/octowright/browser_pool/listeners.py        # external-close transition

src/octowright/server/browser/*.py              # complete direct Playwright workflows gated
src/octowright/server/captures.py               # complete capture workflow gated
src/octowright/server/goldens.py                # complete golden workflow gated
src/octowright/server/macros.py                 # MCP delegation unchanged
src/octowright/macros/execution.py              # whole macro/sequence lease
src/octowright/macros/artifacts.py              # whole replay/evidence lease

packages/octowright-frontend/src/types.ts       # optional operation snapshot
packages/octowright-frontend/src/*.ts           # compact busy/closing/broken indicator

tests/session/test_operation_gate.py            # state machine and concurrency unit tests
tests/test_operation_gate_integration.py        # session/pool/macro/background integration
tests/test_operation_gate_architecture.py       # Playwright-access classification scan
tests/test_operation_gate_live.py               # focused local Chromium proof
```

Exact existing test files may receive focused regression cases instead of duplicating their fixtures in the new integration file. No MCP tool registration, macro action, JSONL schema, replay map, or exporter entry is added for the gate.

## 13. Testing plan

### Browser-free state-machine tests

- FIFO order for multiple waiters.
- Exact-task reentrancy and depth accounting.
- A spawned child task does not inherit ownership.
- Waiting cancellation removes the ticket.
- Active cancellation and arbitrary exceptions release ownership.
- Queue expiry never enters the body or records an action.
- Different session gates execute concurrently.
- Close cutoff preserves earlier tickets and rejects later tickets.
- Duplicate close calls share one task and outcome.
- Canceling a close caller does not cancel the coordinator.
- Protection/close admission races have one deterministic winner.
- External closure wakes all waiters and leaves another session usable.
- Broken-state behavior is isolated to one session.
- Snapshots and telemetry contain no arguments or selectors.
- Configuration default, environment parsing, invalid values, and per-pool override.

Use events and a fake monotonic clock instead of wall-clock sleeps. Assert that no test leaves pending tasks.

### Browser-free integration tests

- Queue time does not consume the inner Playwright timeout.
- Nested session calls and nested macros do not deadlock.
- One macro/sequence cannot be interleaved by a manual action.
- Failure diagnostics run before lease release.
- Markdown capture queues after its triggering navigation.
- Crash recovery queues after the failed owner and remains durable.
- Screencast start/rebind/stop serialize without gating frame delivery.
- Dialog and route callbacks unblock an operation that holds the gate.
- Capture-and-close, handoff, relaunch, and close-all have no lock inversion.
- Direct server discovery/inspection/capture/golden paths use one complete boundary.
- Recorder output contains only behavioral actions in execution order.
- Idempotent reconnect resumes the same producer/ticket rather than enqueueing twice.
- Queue timeout is returned as a tool error while the MCP server and another browser remain usable.
- An expired or canceled ticket cannot execute later.
- The architecture scan fails on a synthetic unclassified Playwright access.
- Direct-Python async API migrations are exercised and documented.
- The session-list snapshot is safe when read through existing synchronous status paths.
- The frontend renders busy/closing/broken from the shared optional snapshot and remains quiet for idle/terminal rows.

### Live-browser coverage

Add one focused `live_browser` Chromium/local-playground test covering:

- macro-versus-manual ordering;
- close cutoff behavior;
- cross-session parallelism;
- continued server/session usability after a rejected queued operation.

Extend the existing opt-in stability chaos suite with concurrent gated calls, cancellation, and external page closure. Do not multiply the same scheduling assertions across all three engines; the gate contains no engine-specific behavior, and the existing engine matrix remains the compatibility signal.

### Verification commands

- Focused operation-gate unit and integration tests.
- Full non-live `make test` suite.
- `make typecheck`.
- `make lint`, including LOC and architecture coverage checks.
- Frontend unit tests for the optional session-operation indicator.
- The focused live-browser test when a Chromium installation is available.

## 14. Acceptance criteria

The project is ready to hand back to brainstorming for the DRY audit only when:

- every Octowright-owned Playwright access is gated or explicitly classified;
- same-session operations have deterministic FIFO behavior;
- different sessions retain parallel execution;
- whole macro and compound operations cannot be interleaved;
- queue timeout produces a tool error before the heartbeat ceiling under defaults;
- close and protection races are deterministic and close is durable/idempotent;
- failures affect only the relevant tool/browser session and do not disconnect MCP;
- scheduling produces no JSONL/macro/export noise;
- focused, full non-live, type, lint, LOC, and applicable live tests pass;
- no pending asyncio tasks or closing-registry entries remain after tests.
