# Browser Session Operation Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, FIFO, per-browser serialization for every Octowright-owned Playwright operation, including durable close coordination and whole-invocation leases for macros and compound helpers, without reducing parallelism across browser sessions.

**Architecture:** Each `BrowserSession` owns one exact-`asyncio.Task`-reentrant `SessionOperationGate`; session methods are defensively decorated, while compound MCP/HTTP/macro workflows take one outer lease. `BrowserPool` owns a durable close coordinator and closing registry, and a static architecture scanner prevents new Playwright access from bypassing the gate unless it has one of the four approved, reason-bearing classifications. Scheduling remains invisible to JSONL, macro replay, and export formats.

**Tech Stack:** Python 3.11+, `asyncio`, Playwright async API, MCPServer, pytest/pytest-asyncio, provide.telemetry, TypeScript, Vitest.

---

## Scope guard

This plan implements only `docs/superpowers/specs/2026-08-13-browser-session-operation-gate-design.md`. It deliberately does not implement accessible keyboard drag/drop, a control lease/Take control workflow, terminal-session gating, automatic retries, or the repository-wide code/feature DRY audit. After this plan lands and passes its acceptance suite, return to brainstorming for the separately scoped DRY audit, then replace the old accessible drag/drop design before implementing drag/drop.

## File map

### New focused modules

- `src/octowright/session/operation_gate.py` — gate state machine, FIFO waiter queue, exact-task reentrancy, close reservation, fixed-name decorator, errors, configuration resolver, snapshots, logs, and metrics.
- `src/octowright/session/core_expect_mixin.py` — `_poll_until` and the four existing `expect_*` methods extracted without behavioral changes.
- `scripts/check_operation_gate_architecture.py` — AST scanner plus the narrow, reason-bearing bypass inventory.
- `tests/session/test_operation_gate.py` — browser-free state-machine, configuration, cancellation, diagnostics, and telemetry tests.
- `tests/test_operation_gate_integration.py` — browser-free session/pool/macro/background/server integration and failure-containment tests.
- `tests/test_operation_gate_architecture.py` — scanner contract and synthetic violation tests.
- `tests/test_operation_gate_live.py` — one opt-in Chromium/local-playground proof.
- `tests/_operation_gate_fakes.py` — reusable operation-aware fake/probe used by macro and integration tests.

### Existing Python modules changed

- `src/octowright/_tracing.py` — re-export provide.telemetry's gauge helper.
- `src/octowright/session/core.py`, `_protocols.py`, and `__init__.py` — construct/export one gate and expose thin session APIs.
- `src/octowright/session/core_page_mixin.py`, `core_expect_mixin.py`, `core_ops_mixin.py`, `core_locator_mixin.py`, `core_interaction_mixin.py`, `core_io_mixin.py`, `frames.py`, and `downloads.py` — gate live Playwright/mutable-target operations and migrate page/frame/dialog listing setters to async.
- `src/octowright/session/screencast.py` — serialize producer start/rebind/ordinary stop while leaving frame delivery ungated.
- `src/octowright/browser_pool/pool.py`, `launch_pipeline.py`, `lifecycle.py`, `listeners.py`, `roster.py`, and `crash_recovery.py` — propagate configuration, coordinate durable close, handle external close, serialize recovery, and protect compound lifecycle operations.
- `src/octowright/macros/execution.py` and `artifacts.py` — whole-macro, sequence, and artifact leases.
- `src/octowright/conditional.py` and `src/octowright/macros/checks.py` — keep direct macro predicates inside the macro root lease for direct callers too.
- `src/octowright/server/browser/discovery.py`, `discovery_links.py`, `input.py`, `inspect.py`, `inspect_assertions.py`, `lifecycle.py`, `network.py`, and `views.py` — wrap complete browser-derived tool workflows and update async migrations.
- `src/octowright/server/captures.py`, `goldens.py`, and `src/octowright/scenarios_pool.py` — wrap complete capture/golden/scenario browser workflows.
- `src/octowright/http/discovery.py`, `routes/sessions.py`, and `routes/media.py` — reuse the shared snapshot and gate live ARIA, selector, and screenshot work.
- `src/octowright/server/meta.py` — surface the existing pool snapshot through status without a second scheduler view.
- `Makefile` — run the architecture scanner in `make lint`.

### Existing tests and documentation changed

- Focused regressions in `tests/test_session_*`, `tests/test_browser_pool_*`, `tests/test_pool_disconnect.py`, `tests/test_crash_recovery.py`, `tests/session/test_screencast_*`, `tests/test_macros.py`, `tests/test_macro_*`, `tests/test_server_browser_*`, `tests/test_server_captures_tools.py`, `tests/test_goldens.py`, `tests/test_http_server.py`, `tests/test_http_routes_sessions_branches.py`, `tests/test_idempotency_cache.py`, `tests/test_progress_heartbeat.py`, and `tests/test_stability_chaos_live.py`.
- `packages/octowright-frontend/src/types.ts`, `session-table.ts`, `session-table.test.ts`, and the relevant stylesheet — optional operation status with no idle/terminal noise.
- `README.md`, `docs/troubleshooting.md`, `CHANGELOG.md`, `AGENTS.md`, and `CLAUDE.md` — embedder API migration, timeout/heartbeat interaction, telemetry, and operational semantics. `AGENTS.md` remains canonical and is copied byte-for-byte to `CLAUDE.md`.

## Locked implementation interfaces

Use these names consistently throughout the implementation:

| Interface | Exact contract |
|---|---|
| `OperationGateState` | `StrEnum` with `OPEN="open"`, `CLOSING="closing"`, `CLOSED="closed"`, and `BROKEN="broken"` |
| `OperationGateSnapshot` | `TypedDict` containing `state`, `active_operation`, `active_for_ms`, `queue_depth`, `oldest_wait_ms`, and `queue_timeout_seconds` with the types shown in the approved spec |
| `UseDefault` / `USE_DEFAULT` | private enum sentinel used only to distinguish the configured ordinary timeout from explicit `None` |
| `SessionOperationGate.operation` | `(operation_name: LiteralString, *, wait_timeout_seconds: float | None | UseDefault = USE_DEFAULT) -> AbstractAsyncContextManager[None]` |
| `SessionOperationGate.control_update` | async fixed-name control-plane mutation serialized by the admission mutex |
| `SessionOperationGate.reserve_close` | async protection-preflight plus one FIFO close reservation |
| `SessionOperationGate.reserve_external_teardown` | synchronous teardown-only reservation after an external close has set `closed` |
| `SessionOperationGate.close_operation` | async context manager that binds a granted reservation to the durable coordinator task |
| `SessionOperationGate.complete_close` / `fail_close` | resolve the retained close outcome exactly once and leave the state `closed` |
| `SessionOperationGate.mark_closed_external` | synchronous event-loop callback transition that closes admission and fails queued tickets |
| `SessionOperationGate.snapshot` | synchronous fresh-copy read from the thread-safe diagnostics publication |

`USE_DEFAULT` means the configured ordinary queue timeout. Passing `None` means a durable system wait with no ordinary queue deadline; only crash recovery and accepted close use it. `CloseReservation` owns one FIFO-or-teardown close ticket and one shared result future; it is intentionally not a public MCP type. Every in-repository `operation(...)`, `control_update(...)`, and `@gated_operation(...)` name is a source-code string literal matching `^[a-z][a-z0-9_.-]{0,63}$`.

### Task 1: Add configuration, error, snapshot, and metric contracts

**Files:**
- Create: `src/octowright/session/operation_gate.py`
- Modify: `src/octowright/_tracing.py`
- Create: `tests/session/test_operation_gate.py`
- Modify: `tests/test_tracing.py`

- [ ] **Step 1: Write failing configuration and public-contract tests**

Add tests that pin the four distinct `RuntimeError` subclasses, enum values, default/env/explicit precedence, rejection of zero/negative/NaN/infinite values, fixed-name validation, and gauge availability:

```python
def test_operation_timeout_resolution_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", "41.5")
    assert resolve_operation_queue_timeout_seconds(None) == 41.5
    assert resolve_operation_queue_timeout_seconds(7.0) == 7.0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "nope"])
def test_operation_timeout_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS"):
        resolve_operation_queue_timeout_seconds(None)


def test_gate_errors_are_distinct_runtime_errors() -> None:
    errors = {
        SessionBusyTimeoutError,
        SessionClosingError,
        SessionClosedError,
        OperationGateInvariantError,
    }
    assert len(errors) == 4
    assert all(issubclass(error, RuntimeError) for error in errors)


def test_operation_names_are_fixed_identifiers() -> None:
    assert validate_operation_name("browser_click") == "browser_click"
    for unsafe in ("#password", "https://secret.test", "user supplied", "", "a" * 65):
        with pytest.raises(ValueError, match="fixed identifier"):
            validate_operation_name(unsafe)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run --active pytest tests/session/test_operation_gate.py tests/test_tracing.py -q --no-cov`

Expected: collection fails because `octowright.session.operation_gate` and `_tracing.gauge` do not exist.

- [ ] **Step 3: Implement the contract layer**

Create the module with SPDX headers and these concrete definitions before adding scheduling behavior:

```python
DEFAULT_OPERATION_QUEUE_TIMEOUT_SECONDS = 300.0
_OPERATION_TIMEOUT_ENV = "OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS"
_OPERATION_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class OperationGateState(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    BROKEN = "broken"


class SessionBusyTimeoutError(RuntimeError):
    """The operation's FIFO ticket expired before it owned the session."""


class SessionClosingError(RuntimeError):
    """The operation arrived after the session close cutoff."""


class SessionClosedError(RuntimeError):
    """The underlying browser session is already closed."""


class OperationGateInvariantError(RuntimeError):
    """The gate's ownership/state invariants were violated."""


class OperationGateSnapshot(TypedDict):
    state: Literal["open", "closing", "closed", "broken"]
    active_operation: str | None
    active_for_ms: int | None
    queue_depth: int
    oldest_wait_ms: int | None
    queue_timeout_seconds: float


def _positive_finite_seconds(value: object, *, source: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} must be positive finite seconds, got {value!r}") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{source} must be positive finite seconds, got {value!r}")
    return parsed


def resolve_operation_queue_timeout_seconds(
    explicit: float | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> float:
    if explicit is not None:
        return _positive_finite_seconds(explicit, source="operation_queue_timeout_seconds")
    source = os.environ if environ is None else environ
    raw = source.get(_OPERATION_TIMEOUT_ENV, str(DEFAULT_OPERATION_QUEUE_TIMEOUT_SECONDS))
    return _positive_finite_seconds(raw, source=_OPERATION_TIMEOUT_ENV)


def validate_operation_name(name: str) -> str:
    if not _OPERATION_NAME_RE.fullmatch(name):
        raise ValueError(f"operation name must be a fixed identifier, got {name!r}")
    return name
```

Also import and re-export `gauge` beside `counter` and `histogram` in `src/octowright/_tracing.py`. Define the five bounded instruments in `operation_gate.py`:

```python
_QUEUE_WAIT = histogram("octowright_operation_queue_wait_seconds", unit="s")
_ACTIVE_DURATION = histogram("octowright_operation_active_duration_seconds", unit="s")
_QUEUE_TIMEOUT = counter("octowright_operation_queue_timeout_total")
_REJECTED = counter("octowright_operation_rejected_total")
_QUEUE_DEPTH = gauge("octowright_operation_queue_depth", unit="1")
```

Use only `operation`, `kind`, `outcome`, and bounded `reason` attributes. Implement current queue depth as aggregate deltas with `_QUEUE_DEPTH.add(+1/-1, {"kind": kind})`; do not label metrics with instance IDs. Instance IDs are allowed only in sanitized logs and error messages.

- [ ] **Step 4: Run the contract tests and verify GREEN**

Run: `uv run --active pytest tests/session/test_operation_gate.py tests/test_tracing.py -q --no-cov`

Expected: all new contract tests pass.

- [ ] **Step 5: Commit the contract layer**

```bash
git add src/octowright/session/operation_gate.py src/octowright/_tracing.py tests/session/test_operation_gate.py tests/test_tracing.py
git commit -m "feat(session): define operation gate contracts"
```

### Task 2: Implement FIFO admission and exact-task reentrancy

**Files:**
- Modify: `src/octowright/session/operation_gate.py`
- Modify: `tests/session/test_operation_gate.py`

- [ ] **Step 1: Write failing scheduler tests using events**

Add tests for FIFO order, same-task depth, child-task non-inheritance, cross-gate parallelism, ordinary waiter cancellation, active cancellation, arbitrary exception release, and timeout-before-body. The ordering test must control progress with events instead of sleeps:

```python
@pytest.mark.asyncio
async def test_fifo_waiters_run_in_arrival_order() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    owner_entered = asyncio.Event()
    release_owner = asyncio.Event()
    order: list[str] = []

    async def owner() -> None:
        async with gate.operation("owner"):
            owner_entered.set()
            await release_owner.wait()

    async def waiter(name: Literal["first", "second"]) -> None:
        async with gate.operation(name):
            order.append(name)

    owner_task = asyncio.create_task(owner())
    await owner_entered.wait()
    first = asyncio.create_task(waiter("first"))
    await wait_for_queue_depth(gate, 1)
    second = asyncio.create_task(waiter("second"))
    await wait_for_queue_depth(gate, 2)
    release_owner.set()
    await asyncio.gather(owner_task, first, second)
    assert order == ["first", "second"]


@pytest.mark.asyncio
async def test_spawned_child_does_not_inherit_owner() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    child_entered = asyncio.Event()

    async with gate.operation("parent"):
        async with gate.operation("nested"):
            assert gate.snapshot()["active_operation"] == "parent"
        child = asyncio.create_task(enter_and_signal(gate, "child", child_entered))
        await wait_for_queue_depth(gate, 1)
        assert not child_entered.is_set()

    await child
    assert child_entered.is_set()


@pytest.mark.asyncio
async def test_expired_waiter_never_enters_body() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=0.01)
    entered = False

    async def blocked() -> None:
        nonlocal entered
        async with gate.operation("blocked"):
            entered = True

    async with gate.operation("owner"):
        blocked_task = asyncio.create_task(blocked())
        await wait_for_queue_depth(gate, 1)
        with pytest.raises(SessionBusyTimeoutError, match="one.*blocked"):
            await blocked_task
    assert entered is False
    assert gate.snapshot()["queue_depth"] == 0
```

Add `wait_for_queue_depth` as a test-only yield loop bounded by `asyncio.timeout(1)`; it may call `await asyncio.sleep(0)` to yield but must not use wall-clock sleeps.

- [ ] **Step 2: Run scheduler tests and verify RED**

Run: `uv run --active pytest tests/session/test_operation_gate.py -q --no-cov`

Expected: tests fail because `SessionOperationGate.operation()` has no scheduler implementation.

- [ ] **Step 3: Implement the explicit waiter queue**

Use a deque of records containing the requesting task, fixed operation name, enqueue monotonic time, future, and grant flag. The essential acquisition/release algorithm is:

```python
@dataclass(slots=True)
class _Waiter:
    task: asyncio.Task[object]
    operation_name: str
    queued_at: float
    ready: asyncio.Future[None]
    granted: bool = False


@dataclass(frozen=True, slots=True)
class _LeaseToken:
    owner_task: asyncio.Task[object]
    operation_name: str


async def _acquire(
    self,
    operation_name: str,
    wait_timeout_seconds: float | None | UseDefault,
) -> _LeaseToken:
    name = validate_operation_name(operation_name)
    task = self._current_task()
    async with self._admission_lock:
        if self._owner_task is task:
            self._depth += 1
            return _LeaseToken(task, name)
        self._raise_if_not_open(name)
        waiter = _Waiter(task, name, self._clock(), asyncio.get_running_loop().create_future())
        self._waiters.append(waiter)
        self._queue_depth_delta(+1)
        self._publish_diagnostics_locked()
        self._grant_next_locked()

    timeout = self.queue_timeout_seconds if wait_timeout_seconds is USE_DEFAULT else wait_timeout_seconds
    try:
        if timeout is None:
            await asyncio.shield(waiter.ready)
        else:
            await asyncio.wait_for(asyncio.shield(waiter.ready), timeout=timeout)
    except TimeoutError:
        await self._remove_or_release_waiter(waiter)
        _QUEUE_TIMEOUT.add(1, attributes={"operation": name, "kind": self.kind})
        raise SessionBusyTimeoutError(self._busy_timeout_message(waiter)) from None
    except asyncio.CancelledError:
        await self._remove_or_release_waiter(waiter)
        raise
    return _LeaseToken(task, name)


def _grant_next_locked(self) -> None:
    if self._owner_task is not None or not self._waiters:
        return
    waiter = self._waiters.popleft()
    self._queue_depth_delta(-1)
    waiter.granted = True
    self._owner_task = waiter.task
    self._root_operation = waiter.operation_name
    self._active_since = self._clock()
    self._depth = 1
    _QUEUE_WAIT.record(
        self._active_since - waiter.queued_at,
        attributes={"operation": waiter.operation_name, "kind": self.kind, "outcome": "admitted"},
    )
    self._publish_diagnostics_locked()
    waiter.ready.set_result(None)


async def _release(self, lease: _LeaseToken, outcome: Literal["ok", "error", "cancelled"]) -> None:
    async with self._admission_lock:
        if self._owner_task is not lease.owner_task:
            self._break_locked("operation released by a task that does not own the gate")
            raise OperationGateInvariantError(self._invariant_message())
        self._depth -= 1
        if self._depth:
            return
        self._record_active_duration_locked(outcome)
        self._owner_task = None
        self._root_operation = None
        self._active_since = None
        self._grant_next_locked()
        self._publish_diagnostics_locked()


@asynccontextmanager
async def operation(
    self,
    operation_name: LiteralString,
    *,
    wait_timeout_seconds: float | None | UseDefault = USE_DEFAULT,
) -> AsyncIterator[None]:
    lease = await self._acquire(operation_name, wait_timeout_seconds)
    outcome: Literal["ok", "error", "cancelled"] = "ok"
    try:
        yield
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except BaseException:
        outcome = "error"
        raise
    finally:
        release_task = asyncio.create_task(self._release(lease, outcome))
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            await wait_task_after_cancellation(release_task)
            raise
```

Use the existing `session_manifest.wait_task_after_cancellation` behavior as the model, but keep the small cancellation-join helper local to `operation_gate.py` so the session layer does not import manifest code. Passing `_LeaseToken` into the detached release is essential: `asyncio.shield(coroutine)` creates another task, so checking `asyncio.current_task()` from inside release would falsely report an ownership invariant violation on every cancellation-safe exit.

`_remove_or_release_waiter` must remove an ungranted waiter atomically and decrement the aggregate gauge; if timeout/cancellation races with grant, it must use the captured lease owner to release the newly granted ownership before propagating. `_raise_if_not_open` maps `closing`, `closed`, and `broken` to the three distinct gate errors and increments `_REJECTED` with a bounded reason. Ordinary operation exceptions pass through unchanged and never change the state.

Record queue-wait duration for `admitted`, `timeout`, and `cancelled` outcomes and root active duration for `ok`, `error`, and `cancelled`; nested re-entry does not create a second duration. Emit sanitized structured logs `octowright.operation.queued`, `.admitted`, `.released`, `.timeout`, `.rejected`, `.state_changed`, and `.invariant_broken` at debug/info/warning levels appropriate to the outcome. Fields are limited to instance ID, browser kind, fixed operation name, state, queue depth, durations, and bounded outcome/reason—never operation arguments or task identities. Add assertions using a selector-like rejected name to prove it cannot reach logs or metrics.

- [ ] **Step 4: Add immutable, thread-safe diagnostics**

Store only scheduler-derived scalar fields under a `threading.Lock`. `snapshot()` takes that lock, uses the injected monotonic clock to derive `active_for_ms` and `oldest_wait_ms`, and returns a fresh `OperationGateSnapshot` dict. It must never inspect an `asyncio.Future`, task object, selector, URL, argument, or recorder.

- [ ] **Step 5: Run scheduler tests and verify GREEN**

Run: `uv run --active pytest tests/session/test_operation_gate.py -q --no-cov`

Expected: FIFO, reentrancy, cancellation, exception, timeout, cross-gate, and snapshot tests all pass with no pending-task warnings.

- [ ] **Step 6: Commit FIFO admission**

```bash
git add src/octowright/session/operation_gate.py tests/session/test_operation_gate.py
git commit -m "feat(session): serialize operations with FIFO admission"
```

### Task 3: Add close cutoff, external-close, broken-state, and control-update semantics

**Files:**
- Modify: `src/octowright/session/operation_gate.py`
- Modify: `tests/session/test_operation_gate.py`

- [ ] **Step 1: Write failing state-machine tests**

Cover all transitions and exact error types:

```python
@pytest.mark.asyncio
async def test_close_cutoff_drains_earlier_waiters_and_rejects_later_work() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    release_owner = asyncio.Event()
    sequence: list[str] = []

    async def owner() -> None:
        async with gate.operation("owner"):
            await release_owner.wait()
            sequence.append("owner")

    owner_task = asyncio.create_task(owner())
    await wait_for_active(gate, "owner")
    earlier = asyncio.create_task(run_recorded(gate, "earlier", sequence))
    await wait_for_queue_depth(gate, 1)
    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
    assert gate.snapshot()["state"] == "closing"
    with pytest.raises(SessionClosingError):
        async with gate.operation("later"):
            raise AssertionError("later work must not enter")

    close_task = asyncio.create_task(run_close_reservation(gate, reservation, sequence))
    release_owner.set()
    await asyncio.gather(owner_task, earlier, close_task)
    assert sequence == ["owner", "earlier", "close"]


@pytest.mark.asyncio
async def test_external_close_fails_waiters_but_not_another_gate() -> None:
    first = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    second = SessionOperationGate("two", "firefox", queue_timeout_seconds=30)
    release = asyncio.Event()
    async with hold_gate(first, "owner", release):
        waiter = asyncio.create_task(enter_once(first, "queued"))
        await wait_for_queue_depth(first, 1)
        first.mark_closed_external()
        with pytest.raises(SessionClosedError):
            await waiter
        async with second.operation("healthy"):
            assert second.snapshot()["active_operation"] == "healthy"
        release.set()


@pytest.mark.asyncio
async def test_control_update_and_close_preflight_have_one_winner() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    protected = False

    def protect() -> None:
        nonlocal protected
        protected = True

    await gate.control_update("browser_set_protected", protect)
    with pytest.raises(ProtectedForTest):
        await gate.reserve_close("browser_close", preflight=lambda: raise_protected(protected))
    assert gate.snapshot()["state"] == "open"
```

Also test duplicate `reserve_close` returns the identical `CloseReservation`, all duplicate waiters observe the same result/error, cancellation of one waiter does not cancel the shared future, a failed outcome with no remaining waiter is internally observed instead of producing an unhandled-future warning, external close invalidates a pending close ticket, external close after ticket grant but before coordinator binding prevents preparation, `reserve_external_teardown` is idempotent after an external transition, and invariant corruption changes only that gate to `broken`.

- [ ] **Step 2: Run state-machine tests and verify RED**

Run: `uv run --active pytest tests/session/test_operation_gate.py -q --no-cov`

Expected: close reservation and control-update tests fail because those APIs are not implemented.

- [ ] **Step 3: Implement one reserved close ticket and shared outcome**

Add an internal close waiter behind all already queued ordinary waiters. `reserve_close` executes the supplied synchronous `preflight` while holding `_admission_lock`; a preflight exception leaves the state `open` and inserts no ticket. On success, set `closing` before releasing the mutex so later ordinary calls reject immediately. A repeated reservation returns the retained object:

```python
@dataclass(slots=True)
class CloseReservation:
    operation_name: str
    waiter: _Waiter
    outcome: asyncio.Future[object]
    teardown_only: bool = False

    async def wait(self) -> object:
        return await asyncio.shield(self.outcome)


async def reserve_close(
    self,
    operation_name: LiteralString,
    *,
    preflight: Callable[[], None],
) -> CloseReservation:
    name = validate_operation_name(operation_name)
    async with self._admission_lock:
        if self._close_reservation is not None:
            return self._close_reservation
        preflight()
        if self._state is OperationGateState.BROKEN:
            reservation = self._new_teardown_only_reservation(name)
            self._close_reservation = reservation
            return reservation
        self._raise_if_not_open(name)
        reservation = self._new_fifo_close_reservation(name)
        self._close_reservation = reservation
        self._state = OperationGateState.CLOSING
        self._waiters.append(reservation.waiter)
        self._queue_depth_delta(+1)
        self._publish_diagnostics_locked()
        self._grant_next_locked()
        return reservation
```

The close waiter is durable and uses no ordinary timeout. In this task, widen `_Waiter.task` to `asyncio.Task[object] | None` for close waiters and add a separate `_granted_close_reservation: CloseReservation | None` field. Update `_grant_next_locked` so a gate is free only when both `_owner_task` and `_granted_close_reservation` are `None`. A close waiter is created with `task=None`; when it reaches the front, validate that it is the retained reservation's waiter and store that reservation in `_granted_close_reservation` instead of assigning `None` to `_owner_task`. Resolve `waiter.ready` only after this sentinel is installed.

`close_operation(reservation)` awaits `waiter.ready`, then under `_admission_lock` verifies `_granted_close_reservation is reservation`. If `mark_closed_external()` changed the state to `closed` after ticket grant but before this bind, clear the sentinel and raise `SessionClosedError`; this routes the coordinator to teardown-only cleanup and guarantees that a compound preparation never touches the dead page. Otherwise clear the sentinel, bind `_owner_task` to the coordinator's exact current task, and set depth one. Its exit clears ordinary ownership without granting new work. This explicit sentinel prevents the gap between ticket grant and coordinator scheduling from looking like an unowned gate. No ordinary work can slip through because state already equals `closing`. A broken gate returns a teardown-only reservation whose context does not claim ordinary ownership, but its protection preflight still runs; broken state does not silently bypass a user's protection choice.

Add synchronous `reserve_external_teardown(operation_name)`. It is legal only after `mark_closed_external()` set state `closed`; it returns an existing retained reservation when present or creates a completed, teardown-only reservation and outcome for the pool's external cleanup coordinator. `complete_close`/`fail_close` always leave the state `closed`, fail any remaining exceptional waiters, and set the single outcome exactly once.

Attach a small done callback to every internal close-outcome future that calls `future.exception()` when the future was not canceled. This is exception observation only: awaiting that same future later must still raise the identical stored exception object. It prevents a canceled last caller or an unobserved external-teardown failure from generating an event-loop “Future exception was never retrieved” warning.

- [ ] **Step 4: Implement external close and invariant isolation**

`mark_closed_external()` is synchronous because Playwright invokes close listeners synchronously on the owning event loop. It must:

```python
def mark_closed_external(self) -> None:
    if self._state is OperationGateState.CLOSED:
        return
    self._state = OperationGateState.CLOSED
    self._fail_queued_locked(SessionClosedError, reason="external_close")
    self._publish_diagnostics_locked()
```

This synchronous method is permitted only on the owning asyncio event-loop thread; its body contains no await, so it cannot interleave with an admission-lock critical section. Do not cancel or replace the active owner; its Playwright await receives the normal page/context/driver-closed exception. If an accepted FIFO close ticket exists, fail its admission future but retain its shared outcome so the already-created coordinator can switch to teardown-only cleanup. `_break_locked` sets only this gate to `broken`, wakes queued waiters with `OperationGateInvariantError`, logs sanitized state, and allows a later teardown-only close reservation. It must not import or call daemon, MCP transport, pool, or driver-restart code.

- [ ] **Step 5: Run the full state-machine file and verify GREEN**

Run: `uv run --active pytest tests/session/test_operation_gate.py -q --no-cov`

Expected: all configuration, FIFO, cancellation, close, external-close, broken-state, snapshot, and metric tests pass with no pending tasks.

- [ ] **Step 6: Commit the completed gate state machine**

```bash
git add src/octowright/session/operation_gate.py tests/session/test_operation_gate.py
git commit -m "feat(session): add durable operation gate close cutoff"
```

### Task 4: Integrate one gate into `BrowserSession` and extract the expectation mixin

**Files:**
- Create: `src/octowright/session/core_expect_mixin.py`
- Modify: `src/octowright/session/core.py:20-154`
- Modify: `src/octowright/session/core_page_mixin.py:6-548`
- Modify: `src/octowright/session/_protocols.py:6-82`
- Modify: `src/octowright/session/__init__.py`
- Modify: `src/octowright/browser_pool/pool.py:40-80`
- Modify: `src/octowright/browser_pool/launch_pipeline.py:244-298`
- Modify: `src/octowright/server/_state.py:20-42`
- Modify: `tests/test_session_page_mixin_branches.py`
- Modify: `tests/test_browser_pool_branches.py`
- Create: `tests/test_operation_gate_integration.py`
- Create: `tests/_operation_gate_fakes.py`

- [ ] **Step 1: Write failing session-construction and mixin-regression tests**

Pin one gate per object, per-pool timeout propagation, direct-construction env resolution, the unchanged expectation results/recordings, and the LOC ceiling:

```python
def test_browser_session_constructs_exactly_one_gate(fake_session_kwargs: dict[str, object]) -> None:
    session = BrowserSession(**fake_session_kwargs)
    first = session.operation_snapshot()
    second = session.operation_snapshot()
    assert first == second
    assert first == {
        "state": "open",
        "active_operation": None,
        "active_for_ms": None,
        "queue_depth": 0,
        "oldest_wait_ms": None,
        "queue_timeout_seconds": 300.0,
    }


def test_pool_explicit_operation_timeout_reaches_new_session(
    monkeypatch: pytest.MonkeyPatch,
    fake_launch_parts: FakeLaunchParts,
) -> None:
    pool = BrowserPool(operation_queue_timeout_seconds=17.0)
    session = build_session_for_test(pool, fake_launch_parts)
    assert session.operation_snapshot()["queue_timeout_seconds"] == 17.0


def test_core_page_mixin_stays_below_loc_ceiling() -> None:
    assert sum(1 for _ in Path("src/octowright/session/core_page_mixin.py").open()) <= 550
```

Retain the existing `expect_url`, `expect_text`, `expect_selector`, `expect_js`, and polling tests unchanged; add an assertion that each action emits the same recorder row it did before extraction.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run --active pytest tests/session/test_operation_gate.py tests/test_operation_gate_integration.py tests/test_session_page_mixin_branches.py tests/test_browser_pool_branches.py -q --no-cov`

Expected: the session has no gate/snapshot and `core_expect_mixin.py` does not exist.

- [ ] **Step 3: Extract expectations without changing behavior**

Move `_poll_until`, `expect_url`, `expect_text`, `expect_selector`, and `expect_js` verbatim from `core_page_mixin.py` into `SessionExpectMixin` in the new file. Keep `_WAIT_FOR_POLL_SECONDS`, `asyncio`, `re`, `time`, `DEFAULT_ACTION_TIMEOUT_MS`, and `SessionLike` imports in the new file. Leave `_body_contains_text`, `_evaluate_truthy`, and `wait_for` in `core_page_mixin.py`; `wait_for` calls the inherited `self._poll_until`.

Compose the mixin explicitly:

```python
class BrowserSession(
    SessionIOMixin,
    SessionExpectMixin,
    SessionPageMixin,
    SessionOpsMixin,
    SessionNetworkMixin,
    SessionInteractionMixin,
    SessionLocatorMixin,
):
```

Do not change expectation timeout, error, recorder, or return behavior in this step.

- [ ] **Step 4: Construct and expose the session-owned gate**

Add these fields and methods to `BrowserSession`:

```python
operation_queue_timeout_seconds: float | None = field(default=None, repr=False)
_operation_gate: SessionOperationGate = field(init=False, repr=False)

def __post_init__(self) -> None:
    self._operation_gate = SessionOperationGate(
        self.instance_id,
        self.kind,
        queue_timeout_seconds=resolve_operation_queue_timeout_seconds(self.operation_queue_timeout_seconds),
    )
    # Preserve the existing browser/page/start-time initialization below this block.

def operation(
    self,
    operation_name: LiteralString,
    *,
    wait_timeout_seconds: float | None | UseDefault = USE_DEFAULT,
) -> AbstractAsyncContextManager[None]:
    return self._operation_gate.operation(operation_name, wait_timeout_seconds=wait_timeout_seconds)

def operation_snapshot(self) -> OperationGateSnapshot:
    return self._operation_gate.snapshot()

async def set_protected_state(
    self,
    protected: bool,
    *,
    reason: str = "explicit",
) -> dict[str, object]:
    def _commit() -> dict[str, object]:
        self.protected = protected
        self.protected_reason = reason
        return {"instance_id": self.instance_id, "protected": protected}

    return await self._operation_gate.control_update("browser_set_protected", _commit)
```

Add `operation(...)`, `operation_snapshot()`, and the async list/setter signatures to `SessionLike`. Export the gate errors, state, and snapshot from `octowright.session`; keep `BrowserSession` and `DEFAULT_PREVIEW_CHARS` exports intact.

Create `tests._operation_gate_fakes.OperationAwareFake` with a real `SessionOperationGate("fake-session", "chromium", queue_timeout_seconds=30)`, an `operation(...)` delegator, and `operation_snapshot()`. Later mixin/server/macro fakes inherit it; production code gets no fallback for objects that lack a gate.

- [ ] **Step 5: Resolve the timeout once per pool and pass it to every launched session**

Change the pool signature and launch construction:

```python
def __init__(
    self,
    *,
    recordings_dir: Path | None = None,
    operation_queue_timeout_seconds: float | None = None,
) -> None:
    self._operation_queue_timeout_seconds = resolve_operation_queue_timeout_seconds(
        operation_queue_timeout_seconds
    )
```

Pass `pool._operation_queue_timeout_seconds` through `_build_session_object` to `BrowserSession(operation_queue_timeout_seconds=...)`. Add a read-only `operation_queue_timeout_seconds` property beside `recordings_dir` so embedders and tests can inspect the effective value.

After `server/_state.py` constructs the global `BrowserPool`, compare its resolved timeout with the already-imported `_heartbeat.HEARTBEAT_MAX_SECONDS`. Warn once when the queue timeout is greater than or equal to that ceiling; state both values and that transport visibility may expire before admission. Do not import `octowright.server` from the session or pool layer: importing a server submodule executes `server/__init__.py` and would create a layer/cycle hazard during pool construction. Library-created pools still expose their effective timeout; the warning is specifically for the MCP server configuration described by the spec.

- [ ] **Step 6: Run focused tests and LOC check**

Run: `uv run --active pytest tests/test_operation_gate_integration.py tests/test_session_page_mixin_branches.py tests/test_browser_pool_branches.py -q --no-cov`

Run: `uv run --active python scripts/check_max_loc.py`

Expected: tests pass and every Python file remains at or below 550 lines.

- [ ] **Step 7: Commit session integration and extraction**

```bash
git add src/octowright/session/core_expect_mixin.py src/octowright/session/core.py src/octowright/session/core_page_mixin.py src/octowright/session/_protocols.py src/octowright/session/__init__.py src/octowright/browser_pool/pool.py src/octowright/browser_pool/launch_pipeline.py src/octowright/server/_state.py tests/_operation_gate_fakes.py tests/test_operation_gate_integration.py tests/test_session_page_mixin_branches.py tests/test_browser_pool_branches.py
git commit -m "refactor(session): add operation gate and extract expectations"
```

### Task 5: Gate every public session action and migrate coherent snapshots to async

**Files:**
- Modify: `src/octowright/session/operation_gate.py`
- Modify: `src/octowright/session/core_page_mixin.py`
- Modify: `src/octowright/session/core_expect_mixin.py`
- Modify: `src/octowright/session/core_ops_mixin.py`
- Modify: `src/octowright/session/core_locator_mixin.py`
- Modify: `src/octowright/session/locators.py`
- Modify: `src/octowright/session/core_interaction_mixin.py`
- Modify: `src/octowright/session/frames.py`
- Modify: `src/octowright/scenarios_pool.py:489-510`
- Modify: `src/octowright/server/browser/lifecycle.py:401-415`
- Modify: `tests/test_session_page_mixin_branches.py`
- Modify: `tests/test_session_ops_mixin_actions.py`
- Modify: `tests/test_session_ops_mixin_diagnostic.py`
- Modify: `tests/test_session_interaction_mixin_branches.py`
- Modify: `tests/test_interception.py`
- Modify: `tests/test_popup_listeners.py`
- Modify: `tests/test_session_helpers_branches.py`
- Modify: `tests/test_multitab.py`
- Modify: `tests/test_iframes.py`
- Modify: `tests/test_default_dialog_policy.py`
- Modify: `tests/test_recorder_redaction_edge_cases.py`
- Modify: `tests/test_security_regressions.py`
- Modify: `tests/test_telemetry_fixes.py`
- Modify: `tests/test_scenarios_pool.py`
- Modify: `tests/test_scenarios_unit.py`
- Modify: `tests/test_server_browser_lifecycle_tools.py`
- Modify: `tests/test_operation_gate_integration.py`

- [ ] **Step 1: Write failing decorator/reentrancy and timeout-boundary tests**

Use an operation-aware fake session with blocking Playwright doubles to prove that direct Python method calls serialize and that inner timeouts start only after admission:

```python
@pytest.mark.asyncio
async def test_direct_session_actions_serialize(fake_browser_session: BrowserSession) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    fake_browser_session.page.goto.side_effect = blocking_call(first_started, release_first)
    first = asyncio.create_task(fake_browser_session.navigate("https://one.test"))
    await first_started.wait()
    second = asyncio.create_task(fake_browser_session.evaluate("document.title"))
    await wait_for_queue_depth(fake_browser_session._operation_gate, 1)
    fake_browser_session.page.evaluate.assert_not_awaited()
    release_first.set()
    await asyncio.gather(first, second)
    fake_browser_session.page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_inner_timeout_begins_after_gate_admission(fake_browser_session: BrowserSession) -> None:
    async with fake_browser_session.operation("owner"):
        queued = asyncio.create_task(fake_browser_session.expect_selector("#ready", timeout_ms=25))
        await wait_for_queue_depth(fake_browser_session._operation_gate, 1)
        fake_browser_session.page.wait_for_selector.assert_not_awaited()
    await queued
    fake_browser_session.page.wait_for_selector.assert_awaited_once_with("#ready", timeout=25)
```

Also add async tests for coherent `list_pages()`, `list_frames()`, and `set_dialog_policy()` and update existing direct calls to await them. Have every test-only class that directly subclasses a gated session mixin inherit `tests._operation_gate_fakes.OperationAwareFake`; do not teach the production decorator to silently bypass missing gates for old fakes.

- [ ] **Step 2: Run the focused session tests and verify RED**

Run: `uv run --active pytest tests/test_operation_gate_integration.py tests/test_session_page_mixin_branches.py tests/test_session_ops_mixin_actions.py tests/test_session_ops_mixin_diagnostic.py tests/test_session_interaction_mixin_branches.py tests/test_interception.py tests/test_popup_listeners.py tests/test_session_helpers_branches.py tests/test_multitab.py tests/test_iframes.py tests/test_default_dialog_policy.py tests/test_recorder_redaction_edge_cases.py tests/test_security_regressions.py tests/test_telemetry_fixes.py tests/test_scenarios_pool.py tests/test_scenarios_unit.py tests/test_server_browser_lifecycle_tools.py -q --no-cov`

Expected: methods interleave and migrated sync call sites fail because they do not await.

- [ ] **Step 3: Implement the defensive decorator**

Add a typed async-only decorator to `operation_gate.py`:

```python
P = ParamSpec("P")
R = TypeVar("R")


def gated_operation(
    operation_name: LiteralString,
) -> Callable[[Callable[Concatenate[object, P], Awaitable[R]]], Callable[Concatenate[object, P], Awaitable[R]]]:
    fixed_name = validate_operation_name(operation_name)

    def _decorate(
        function: Callable[Concatenate[object, P], Awaitable[R]],
    ) -> Callable[Concatenate[object, P], Awaitable[R]]:
        @functools.wraps(function)
        async def _wrapped(self: object, *args: P.args, **kwargs: P.kwargs) -> R:
            operation = cast(Any, self).operation
            async with operation(fixed_name):
                return await function(self, *args, **kwargs)

        return _wrapped

    return _decorate
```

The decorator must preserve the original signature for MCP/macro callers and always observe the root operation name on same-task nested calls.

- [ ] **Step 4: Annotate the complete session method inventory**

Convert `list_pages`, `list_frames`, and `set_dialog_policy` to `async def`, then apply these exact fixed names:

| Mixin | Methods | Fixed operation name |
|---|---|---|
| `core_page_mixin` | `list_pages` | `page_list` |
| | `switch_page`, `close_page` | `page_switch`, `page_close` |
| | `navigate`, `click`, `type_text`, `fill`, `press_key` | `browser_navigate`, `browser_click`, `browser_type`, `browser_fill`, `browser_press_key` |
| | `screenshot`, `snapshot`, `evaluate`, `wait_for` | `browser_screenshot`, `browser_snapshot`, `browser_evaluate`, `browser_wait_for` |
| | `_resolve_semantic_metadata`, `_is_password_input`, `_redacted_or_original` | `session_input_metadata`, `session_input_redaction`, `session_input_redaction` |
| `core_expect_mixin` | `_poll_until` | `browser_expect_poll` |
| | `expect_url`, `expect_text`, `expect_selector`, `expect_js` | `browser_expect_url`, `browser_expect_text`, `browser_expect_selector`, `browser_expect_js` |
| `core_ops_mixin` | `diagnostic_bundle` and its three Playwright capture helpers | `browser_diagnostic_bundle` |
| | `switch_frame`, `reset_frame`, `list_frames` | `browser_switch_frame`, `browser_reset_frame`, `browser_list_frames` |
| | `hover`, `select_option`, `drag` | `browser_hover`, `browser_select_option`, `browser_drag` |
| | `navigate_back`, `resize`, `viewport_status`, `viewport_sync`, `open_url` | matching MCP name (`browser_navigate_back`, `browser_resize`, `browser_viewport_status`, `browser_viewport_sync`, `browser_open_url`) |
| `core_locator_mixin` | `_locator`, `_is_password_locator`, `_redacted_or_original_for_locator` | `session_locator_resolve`, `session_locator_redaction`, `session_locator_redaction` |
| | `click_by`, `fill_by`, `get_text_by` | `browser_click`, `browser_fill`, `browser_get_text_by` |
| `core_interaction_mixin` | `set_dialog_policy`, `mock_route`, `unmock_route`, `set_input_files` | matching MCP name |

Do not gate `list_downloads`, `get_network_requests`, console/network/websocket buffer reads, recording paths, or saved-download metadata because they use only Octowright-owned cached state. Do not put the ordinary decorator on `close`/`_close_impl`; Task 7 gives teardown its reserved-close path.

Convert `_locator` to async before decorating it, and await it from `click_by`, `fill_by`, and `get_text_by`. Change `locators.build_locator` to accept `SessionLike`, enter `session.operation("session_locator_resolve")`, resolve `session._target()` inside, and then perform the existing finder construction. Locator construction itself is lazy, but target selection is mutable; direct helper callers therefore need a coherent selection just like the public methods. Update `tests/test_session_helpers_branches.py` to use `OperationAwareFake` rather than passing a raw target.

Likewise, change module-level `_body_contains_text` and `_evaluate_truthy` to accept the session and enter the parent operation name reentrantly before using the locator/target passed by `wait_for`. This removes caller-only safety assumptions from the two polling helpers.

- [ ] **Step 5: Make frame helpers safe for direct callers**

Change `frames.switch_frame_impl` and `frames.list_frames_impl` to accept the session rather than raw page/frame arguments. Each helper takes the same fixed lease reentrantly before dereferencing `session.page`, so static analysis does not rely on an undocumented call-graph assumption:

```python
async def list_frames_impl(session: SessionLike) -> list[dict[str, Any]]:
    async with session.operation("browser_list_frames"):
        return [
            {"index": i, "name": frame.name, "url": frame.url, "is_active": frame is session.active_frame}
            for i, frame in enumerate(session.page.frames)
        ]
```

Update `SessionOpsMixin.switch_frame/list_frames` to pass `self`, and await the list helper.

- [ ] **Step 6: Update all current async-migration callers**

Change these exact production calls:

```python
# scenarios_pool.py
if dialog_policy:
    await session.set_dialog_policy(dialog_policy)

# server/browser/views.py (completed in Task 9, but make no sync call remain now)
pages = await pool.get(instance_id).list_pages()
frames = await pool.get(instance_id).list_frames()

# server/browser/network.py
return await pool.get(instance_id).set_dialog_policy(policy, prompt_text)

# server/browser/lifecycle.py
result = await pool.get(instance_id).set_protected_state(protected)
publish_dashboard_invalidation_nowait("sessions")
return result
```

Convert the three currently synchronous MCP wrappers (`page_list`, `browser_list_frames`, and `browser_set_dialog_policy`) to `async def` without changing their decorated tool names, arguments, descriptions, or result schemas. `browser_set_protected` is already async; route its mutation through the control-plane method so Task 7's close race has one linearization point. Update every test fake that implements these methods to be async and every call to use `await`; do not add a sync shim that blocks the event loop.

- [ ] **Step 7: Run focused tests and type checking**

Run: `uv run --active pytest tests/test_operation_gate_integration.py tests/test_session_page_mixin_branches.py tests/test_session_ops_mixin_actions.py tests/test_session_ops_mixin_diagnostic.py tests/test_session_interaction_mixin_branches.py tests/test_interception.py tests/test_popup_listeners.py tests/test_session_helpers_branches.py tests/test_multitab.py tests/test_iframes.py tests/test_default_dialog_policy.py tests/test_recorder_redaction_edge_cases.py tests/test_security_regressions.py tests/test_telemetry_fixes.py tests/test_scenarios_pool.py tests/test_scenarios_unit.py tests/test_server_browser_lifecycle_tools.py -q --no-cov`

Run: `uv run --active mypy src/octowright/session src/octowright/scenarios_pool.py`

Expected: all focused tests pass; mypy reports no errors in the selected modules.

- [ ] **Step 8: Commit gated session methods**

```bash
git add src/octowright/session/operation_gate.py src/octowright/session/core_page_mixin.py src/octowright/session/core_expect_mixin.py src/octowright/session/core_ops_mixin.py src/octowright/session/core_locator_mixin.py src/octowright/session/locators.py src/octowright/session/core_interaction_mixin.py src/octowright/session/frames.py src/octowright/session/_protocols.py src/octowright/scenarios_pool.py src/octowright/server/browser/lifecycle.py tests/_operation_gate_fakes.py tests/test_operation_gate_integration.py tests/test_session_page_mixin_branches.py tests/test_session_ops_mixin_actions.py tests/test_session_ops_mixin_diagnostic.py tests/test_session_interaction_mixin_branches.py tests/test_interception.py tests/test_popup_listeners.py tests/test_session_helpers_branches.py tests/test_multitab.py tests/test_iframes.py tests/test_default_dialog_policy.py tests/test_recorder_redaction_edge_cases.py tests/test_security_regressions.py tests/test_telemetry_fixes.py tests/test_scenarios_pool.py tests/test_scenarios_unit.py tests/test_server_browser_lifecycle_tools.py
git commit -m "feat(session): gate browser actions and target snapshots"
```

### Task 6: Serialize background Playwright work and preserve event-critical callbacks

**Files:**
- Modify: `src/octowright/session/core_io_mixin.py:210-320`
- Modify: `src/octowright/session/core_interaction_mixin.py:25-145`
- Modify: `src/octowright/session/downloads.py:41-77`
- Modify: `src/octowright/session/screencast.py:49-330`
- Modify: `src/octowright/browser_pool/crash_recovery.py:110-253`
- Modify: `tests/test_session_io_mixin_branches.py`
- Modify: `tests/test_session_interaction_mixin_branches.py`
- Modify: `tests/test_downloads.py`
- Modify: `tests/test_crash_recovery.py`
- Modify: `tests/session/test_screencast_manager.py`
- Modify: `tests/session/test_screencast_rebind.py`
- Modify: `tests/session/test_screencast_lifecycle.py`
- Modify: `tests/test_operation_gate_integration.py`

- [ ] **Step 1: Write failing background-ordering and callback-unblock tests**

Add tests proving:

```python
@pytest.mark.asyncio
async def test_markdown_capture_queues_behind_navigation(session: BrowserSession) -> None:
    async with session.operation("browser_navigate"):
        session._schedule_markdown_capture(force=True)
        await wait_for_queue_depth(session._operation_gate, 1)
        session.page.content.assert_not_awaited()
    await drain_session_tasks(session)
    session.page.content.assert_awaited_once()


@pytest.mark.asyncio
async def test_dialog_callback_can_unblock_active_click(session: BrowserSession, dialog: AsyncMock) -> None:
    async with session.operation("browser_click"):
        session._handle_dialog(dialog)
        await wait_until_awaited(dialog.dismiss)
    dialog.dismiss.assert_awaited_once()


@pytest.mark.asyncio
async def test_screencast_frame_delivery_does_not_enqueue(session: BrowserSession) -> None:
    manager = ScreencastManager(session, fps=10, quality=70)
    viewer = ScreencastViewer(fps=10)
    manager._viewers.add(viewer)
    manager._handle_frame({"data": b"jpeg"})
    assert await viewer.get() == b"jpeg"
    assert session.operation_snapshot()["queue_depth"] == 0
```

Also test crash recovery waits behind the failed owner with `wait_timeout_seconds=None`, is invalidated by external close, and does not start a second recovery; screencast start/rebind/remove-viewer stop serialize; an external-close termination ends viewers without trying to enter a closed gate; route fulfill runs while the root request is active; and download save queues after its triggering action while `wait_for_download` itself remains concurrent.

- [ ] **Step 2: Run focused background tests and verify RED**

Run: `uv run --active pytest tests/test_session_io_mixin_branches.py tests/test_session_interaction_mixin_branches.py tests/test_downloads.py tests/test_crash_recovery.py tests/session/test_screencast_manager.py tests/session/test_screencast_rebind.py tests/session/test_screencast_lifecycle.py tests/test_operation_gate_integration.py -q --no-cov`

Expected: background Playwright work starts while another operation owns the session.

- [ ] **Step 3: Gate markdown and download work at the task body**

Decorate `capture_markdown` with `@gated_operation("markdown_capture")`; `_schedule_markdown_capture` continues to create a child task, so exact-task reentrancy makes it queue behind the triggering action instead of inheriting ownership.

Indent the existing `save_download` body under `async with session.operation("download_save")` before its first `Download` property read. Keep every Playwright `Download` property read and `save_as` lexically inside the operation context; extract only path/record construction that no longer touches the `Download` object. Preserve the current containment, recorder success/error, event notification, and `{}`-on-save-failure behavior. Leave `wait_for_download_impl` ungated because it waits on Octowright's event/list and would deadlock if it owned the gate while `download_save` waited behind it.

- [ ] **Step 4: Gate screencast lifecycle without lock inversion**

Add `operation(...)` to `_ScreencastSession`. For `add_viewer`, `rebind`, and ordinary last-viewer removal, acquire the session operation first and `ScreencastManager._lock` second:

```python
async def add_viewer(self, *, fps: int | None = None) -> ScreencastViewer:
    async with self._session.operation("screencast_start"):
        async with self._lock:
            return await self._add_viewer_locked(fps=fps)

async def rebind(self, new_page: _ScreencastPage) -> None:
    async with self._session.operation("screencast_rebind"):
        async with self._lock:
            await self._rebind_locked(new_page)
```

Use `screencast_stop` for ordinary removal. Rename `_start_locked` to `_start_owned_locked` and put its direct `page.screencast.start` inside `self._session.operation("screencast_start")`. Replace `_stop_bound_locked` with `_stop_bound_owned_locked`, whose direct stop is inside `self._session.operation("screencast_stop")`. These contexts re-enter the outer add/rebind/remove owner and also protect direct helper tests.

Do not hide direct Playwright access in a shared helper that is sometimes called gated and sometimes called after closure. `terminate()` calls a separate `_terminate_producer_after_close`: under only the manager lock, it ends viewers and best-effort stops the already invalid producer with the existing five-second cap. It must not try to enter a `closed` gate and receives the narrow `teardown-only` classification in Task 11.

Do not acquire per frame in `_handle_frame` or `ScreencastViewer.offer/get`.

- [ ] **Step 5: Make crash recovery a durable system operation**

Wrap the complete `_recover` body, including page replacement, state swap, screenshot, incident, recorder row, and recovery notification:

```python
async def _recover(session: Any, page: Any, reload_timeout_ms: float, url: str) -> bool:
    try:
        async with session.operation("crash_recovery", wait_timeout_seconds=None):
            return await _recover_owned(session, page, reload_timeout_ms, url)
    except (SessionClosingError, SessionClosedError, OperationGateInvariantError):
        log.info("octowright.crash.recovery_invalidated", instance_id=session.instance_id)
        return False
```

Keep the task in `session._bg_tasks`, preserve the existing recovery cap, and do not retry gate errors. A recovery ticket queued before a normal close cutoff is earlier work and completes before close; a recovery that arrives after a close cutoff or external close is invalidated. Publish recovery success/failure only from `_recover_owned`; an invalidated recovery must not claim that it repaired a closing/closed browser.

Make `_replace_crashed_page` and `_capture_recovery_screenshot` accept `SessionLike` and enter `session.operation("crash_recovery", wait_timeout_seconds=None)` around their own direct Playwright access. They re-enter `_recover` in the same task, but remain safe when unit tests or embedders call those helpers directly. `_safe_url` runs synchronously in the Playwright crash callback before the durable task exists and receives the explicit event-critical classification in Task 11.

- [ ] **Step 6: Keep only the two deadlock-breaking callbacks as Playwright bypasses**

Dialog accept/dismiss and route fulfill remain child callbacks with no gate acquisition because the admitted Playwright call may be waiting for them. Restrict each callback to response + passive recorder/error handling. Popup, console, network, websocket, close/crash, and frame-navigation listeners remain synchronous browser-event bookkeeping and will receive explicit `event-critical` scanner classifications in Task 11; none may initiate a new user action.

- [ ] **Step 7: Run focused background tests**

Run: `uv run --active pytest tests/test_session_io_mixin_branches.py tests/test_session_interaction_mixin_branches.py tests/test_downloads.py tests/test_crash_recovery.py tests/session/test_screencast_manager.py tests/session/test_screencast_rebind.py tests/session/test_screencast_lifecycle.py tests/test_operation_gate_integration.py -q --no-cov`

Expected: all tests pass, frame delivery never enqueues, and pytest reports no destroyed pending task.

- [ ] **Step 8: Commit background serialization**

```bash
git add src/octowright/session/core_io_mixin.py src/octowright/session/core_interaction_mixin.py src/octowright/session/downloads.py src/octowright/session/screencast.py src/octowright/browser_pool/crash_recovery.py tests/test_session_io_mixin_branches.py tests/test_session_interaction_mixin_branches.py tests/test_downloads.py tests/test_crash_recovery.py tests/session/test_screencast_manager.py tests/session/test_screencast_rebind.py tests/session/test_screencast_lifecycle.py tests/test_operation_gate_integration.py
git commit -m "feat(session): serialize browser background work"
```

### Task 7: Replace close with a durable, coalesced pool coordinator

**Files:**
- Modify: `src/octowright/browser_pool/pool.py:40-375`
- Modify: `src/octowright/browser_pool/lifecycle.py:40-247`
- Modify: `src/octowright/browser_pool/launch_pipeline.py`
- Modify: `src/octowright/browser_pool/roster.py:49-88`
- Modify: `src/octowright/browser_pool/listeners.py:35-257`
- Modify: `src/octowright/browser_pool/driver_relaunch.py`
- Modify: `src/octowright/session/core.py`
- Modify: `src/octowright/session/core_ops_mixin.py:361-498`
- Modify: `tests/test_browser_pool_branches.py`
- Modify: `tests/test_pool_disconnect.py`
- Modify: `tests/test_browser_pool_events.py`
- Modify: `tests/test_session_lifecycle.py`
- Modify: `tests/test_session_ops_mixin_close.py`
- Modify: `tests/test_session_ops_mixin_lifecycle.py`
- Modify: `tests/test_driver_relaunch.py`
- Modify: `tests/test_post_review_hardening.py`
- Modify: `tests/_pool_invariants.py`
- Modify: `tests/test_operation_gate_integration.py`

- [ ] **Step 1: Write failing close durability and race tests**

Add deterministic event-based tests for accepted-close draining, later rejection, duplicate outcome identity, caller cancellation, protection race, external-close win, explicit-close callback no-op, teardown error coalescing, broken-gate close, closing-registry cleanup, and close-all independence:

```python
@pytest.mark.asyncio
async def test_cancelled_close_caller_does_not_cancel_accepted_close(pool: BrowserPool, session: BrowserSession) -> None:
    active_release = asyncio.Event()
    active = asyncio.create_task(hold_operation(session, "long_action", active_release))
    await wait_for_active(session._operation_gate, "long_action")
    caller = asyncio.create_task(pool.close(session.instance_id, force=True))
    await wait_for_state(session._operation_gate, "closing")
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    active_release.set()
    await active
    await wait_until(lambda: session.instance_id not in pool._closing_sessions)
    session.context.close.assert_awaited_once()
    with pytest.raises(KeyError):
        pool.get(session.instance_id)


@pytest.mark.asyncio
async def test_duplicate_close_callers_share_one_error(pool: BrowserPool, session: BrowserSession) -> None:
    teardown_error = RuntimeError("teardown failed")
    session.context.close.side_effect = teardown_error
    first = asyncio.create_task(pool.close(session.instance_id, force=True))
    await wait_for_state(session._operation_gate, "closing")
    second = asyncio.create_task(pool.close(session.instance_id, force=True))
    first_result, second_result = await asyncio.gather(first, second, return_exceptions=True)
    assert first_result is teardown_error
    assert second_result is teardown_error
    assert session.operation_snapshot()["state"] == "closed"


@pytest.mark.asyncio
async def test_close_all_reserves_every_session_before_waiting(pool_with_two_sessions: PoolPair) -> None:
    result_task = asyncio.create_task(pool_with_two_sessions.pool.close_all(force=True))
    await wait_for_state(pool_with_two_sessions.first._operation_gate, "closing")
    await wait_for_state(pool_with_two_sessions.second._operation_gate, "closing")
    assert not result_task.done()
    pool_with_two_sessions.release_first.set()
    pool_with_two_sessions.release_second.set()
    result = await result_task
    assert set(result["closed"]) == {pool_with_two_sessions.first.instance_id, pool_with_two_sessions.second.instance_id}
```

The protection test must run both orderings: protection commits first and unforced close raises `ProtectedBrowserCloseError` while state remains open; close reserves first and later protection raises `SessionClosingError`.

Extend the existing launch-canceled-during-navigation regression in `tests/test_pool_disconnect.py`: once the session has been registered, cancellation must enter the same identity-aware durable coordinator, leave neither registry entry nor manifest behind, close once, and publish no duplicate event even if normal Playwright close callbacks fire.

In the driver-relaunch tests, assert keep-ID finalization never overlaps the old closing entry, a persistent replacement is not launched before accepted teardown finishes, teardown failure is observed/logged without an unhandled task exception, and the replacement can still launch afterward. Update the pool-invariant probes to require one external-close acceptance seam rather than a raw pop.

- [ ] **Step 2: Run close-focused tests and verify RED**

Run: `uv run --active pytest tests/test_browser_pool_branches.py tests/test_pool_disconnect.py tests/test_browser_pool_events.py tests/test_session_lifecycle.py tests/test_session_ops_mixin_close.py tests/test_session_ops_mixin_lifecycle.py tests/test_driver_relaunch.py tests/test_post_review_hardening.py tests/test_pool_invariants.py tests/test_operation_gate_integration.py -q --no-cov`

Expected: current close pops immediately, duplicate/cancellation semantics diverge, and later work is not rejected at a cutoff.

- [ ] **Step 3: Add the pool closing registry and internal outcome types**

Define private lifecycle dataclasses (not MCP schemas):

```python
@dataclass(slots=True)
class CloseCoordinatorOutcome:
    response: dict[str, Any]
    prepared: object | None


@dataclass(slots=True)
class ClosingSession:
    session: BrowserSession
    reservation: CloseReservation
    task: asyncio.Task[None] | None = None
```

Add `self._closing_sessions: dict[str, ClosingSession] = {}` to `BrowserPool`. Insert the coordinator entry as soon as close is accepted, while retaining the same object in `_sessions` until the close ticket owns the gate. During this drain interval both maps intentionally reference the same session: `_sessions` keeps it resolvable so ordinary tools receive `SessionClosingError`, while `_closing_sessions` lets duplicate closes share exactly one coordinator. When the ticket owns the gate, remove only the `_sessions` entry; retain `_closing_sessions` through teardown and outcome publication.

Update `get()`/`maybe_get()` deliberately: ordinary lookup must never return a teardown-owning session from `_closing_sessions`, but `get()` checks that registry before its generic missing-session path and raises `SessionClosingError` or `SessionClosedError` from the retained gate state. `maybe_get()` remains active-session-only for callers such as handoff replacement patching. Close-specific resolution uses its private resolver and may inspect both registries.

- [ ] **Step 4: Split public close request from reserved teardown**

Rename the existing teardown body to `BrowserSession._teardown_after_close_cutoff()`. Install an internal `_pool_close_requester` callback during `_build_session_object`, before registry publication, so a direct `await session.close()` delegates to identity-aware `pool.close(..., force=True, _expected_session=session)` instead of bypassing the cutoff. Preserve `BrowserSession.close() -> None` by awaiting and discarding the pool response. Standalone test-created sessions without a pool callback retain a `_standalone_close_task` and use a session-owned close coordinator with the same reservation/outcome/cancellation mechanics.

The pool coordinator is created and retained independently of the requesting task:

```python
async def close_browser(
    pool: BrowserPool,
    instance_id: str,
    *,
    force: bool = False,
    _reason: SessionCloseReason = "agent_close",
    _expected_session: BrowserSession | None = None,
) -> dict[str, Any]:
    entry = await reserve_close_browser(
        pool,
        instance_id,
        force=force,
        reason=_reason,
        expected_session=_expected_session,
    )
    outcome = cast(CloseCoordinatorOutcome, await entry.reservation.wait())
    return outcome.response
```

`reserve_close_browser` holds `_sessions_lock` only for identity lookup, an existing `_closing_sessions` check, registry insertion, and the short gate `reserve_close` control transaction. Its `preflight` raises the existing tailored `ProtectedBrowserCloseError` unless `force=True`. On a new reservation, create `ClosingSession`, put it in `_closing_sessions`, create `_coordinate_close` as a detached task, store that task on the entry, and attach a done callback that retrieves unexpected exceptions. It never holds `_sessions_lock` while awaiting a FIFO ticket, Playwright, artifact I/O, or task completion.

If `_closing_sessions` already contains the same session identity, return that entry; if the caller supplied `_expected_session` and the identity differs, retain the current `KeyError` behavior. `close_browser` awaits `reservation.wait()` through `asyncio.shield`, so cancellation affects only that caller.

- [ ] **Step 5: Implement coordinator teardown and shared completion**

The created coordinator executes exactly once:

```python
async def _coordinate_close(
    pool: BrowserPool,
    instance_id: str,
    entry: ClosingSession,
    *,
    reason: SessionCloseReason,
    preparation: Callable[[BrowserSession], Awaitable[object]] | None,
) -> None:
    session = entry.session
    prepared: object | None = None
    error: BaseException | None = None
    response: dict[str, Any] | None = None
    try:
        try:
            async with session._operation_gate.close_operation(entry.reservation):
                await remove_active_identity(pool, instance_id, session)
                prepared, error = await prepare_then_teardown(session, preparation)
        except SessionClosedError as exc:
            # An external browser/page close invalidated admission first.
            await remove_active_identity(pool, instance_id, session)
            prepared, teardown_error = await prepare_then_teardown(session, None)
            if preparation is None:
                error = teardown_error
            else:
                error = exc
                if teardown_error is not None:
                    log_secondary_teardown_error(session, teardown_error, primary=exc)
        response = close_response(session)
    except BaseException as exc:
        if error is None:
            error = exc
    finally:
        await remove_manifest_best_effort(instance_id)
        publish_close_once(session, instance_id, reason)
        async with pool._sessions_lock:
            pool._sessions.pop(instance_id, None)
        if error is None:
            session._operation_gate.complete_close(
                entry.reservation,
                CloseCoordinatorOutcome(response=response or close_response(session), prepared=prepared),
            )
        else:
            session._operation_gate.fail_close(entry.reservation, error)
        async with pool._sessions_lock:
            if pool._closing_sessions.get(instance_id) is entry:
                pool._closing_sessions.pop(instance_id)
```

`remove_active_identity` is the short `_sessions_lock` identity check/pop. `prepare_then_teardown` always attempts `_teardown_after_close_cutoff`, even if preparation fails. It returns the prepared value plus the first error; if teardown also fails after a preparation error, log the teardown error with the fixed operation/instance metadata and retain the preparation error as the shared outcome. If external closure invalidates a preparation-bearing reservation, retain that `SessionClosedError` as the primary outcome and log any teardown failure as secondary; for a plain close, a teardown failure remains the shared failure. The coordinator is detached and retained, so requester cancellation never reaches it; shutdown awaits it rather than canceling it. Do not let the convenience helpers publish, remove manifests, or resolve futures themselves.

The actual implementation must preserve the current manifest cleanup, structured close log, one `SessionClosedEvent`, recorder close, and nullable video/trace/HAR fields. Pass an internal recorder reason into `_teardown_after_close_cutoff`: explicit/shutdown close keeps the current reason-less terminal row, `crashed` records `reason="crashed"`, and `user_close`/`external_disconnect` records `reason="external"`. The teardown writes exactly one terminal `close` row and closes the recorder; listeners write neither. External coordinators also retain the existing bounded eviction metric and `octowright.browser.evicted_externally` log, while explicit coordinators retain `octowright.browser.closed`. If Playwright external closure wins before a plain close ticket, run teardown-only cleanup and return `closed=True` with nullable artifacts rather than closing twice. Task 8 makes preparation-bearing closes fail honestly when their preparation could not run. If teardown raises, remove the unusable session, leave the gate `closed`, and set the exact same exception object on the shared future.

- [ ] **Step 6: Route external closure through a durable teardown-only coordinator**

Refactor `_evict_session_nowait` into `_accept_external_close_nowait(instance_id, expected_session, reason)`. On the owning event loop, with no await between steps, it must:

1. identity-check `_sessions` first;
2. if the same session is still active, call `session._operation_gate.mark_closed_external()` before removing visibility or scheduling work, then remove that exact `_sessions` entry and update the existing capped `_recently_evicted` crashed/not-crashed diagnostic exactly once;
3. if an explicit `ClosingSession` already exists for that same object, return it without creating a second task or notification owner—the external event won while the accepted close was still draining, and its coordinator will take the teardown-only branch;
4. otherwise call `reserve_external_teardown("external_close")`, install one `ClosingSession`, and create the same durable coordinator in teardown-only mode;
5. if the object is absent from `_sessions` but the same object is already in `_closing_sessions`, return without calling `mark_closed_external()`: the close coordinator already owns the cutoff/teardown, and the callback was caused by its deliberate page/context/browser close (or is a duplicate external signal).

Both context/browser `_evict` and the last-page `_on_page_close` call this method synchronously. The last-page path must not schedule a later normal `pool.close`: that would leave a window in which work could still be admitted after the page was already gone. Remove recorder close, manifest cleanup, and `SessionClosedEvent` publication from the callbacks; the retained coordinator performs each exactly once. Keep the no-running-loop fallback best-effort and logged, but still call `mark_closed_external()` before removing a matching active-registry entry. Preserve the current `_recently_evicted` capacity and missing-session wording tests. Add a regression in which `_teardown_after_close_cutoff()` triggers all normal Playwright close listeners and prove they do not overwrite the explicit reason, change ownership mid-teardown, or publish twice.

If external closure wins while an accepted explicit close is still draining, `_sessions` is removed immediately, its close ticket wakes with `SessionClosedError`, and the existing explicit coordinator takes the teardown-only branch above. It retains the explicit close reason and owns the single notification.

Route `driver_relaunch._snapshot_and_evict` through the same synchronous acceptance seam with reason `external_disconnect`; do not retain a second raw-pop API. Carry each returned `ClosingSession` internally with its relaunch descriptor. Before `_relaunch_one` reuses a persistent/session-scoped profile or `_finalize_id` rebinds a keep-ID replacement, await the retained teardown outcome under shielding so the old context, manifest, and closing-registry identity have finished. A teardown failure is logged against the fixed instance/kind and does not suppress the existing best-effort relaunch, but relaunch starts only after that failed teardown attempt has completed. Preserve the current no-running-loop behavior: mark the gate closed and evict synchronously, log that durable cleanup could not be scheduled, and do not fabricate a coordinator task.

Update the driver-relaunch fakes and pool-invariant helpers to expose `_accept_external_close_nowait` and make the Step 1 regressions pass. This path is for an actually dead driver; gate errors themselves must never invoke it.

- [ ] **Step 7: Route post-registration launch cancellation through the coordinator**

Change `launch_pipeline.cancel_cleanup_after_register`: do not pop `_sessions`, remove the manifest separately, or call `session.close()` after detaching it. Call identity-aware `pool.close(instance_id, force=True, _reason="agent_close", _expected_session=session)` and join its already-durable outcome under the existing cancellation shielding. Keep the existing `SessionCloseReason` wire vocabulary unchanged; this is internal cleanup of an agent-requested launch. This keeps the gate/registries/manifest/event bus consistent even when launch cancellation lands immediately after publication.

- [ ] **Step 8: Reserve all `close_all` cutoffs before awaiting outcomes**

Replace the one-stage `gather(pool.close(...))` with:

```python
entries = await asyncio.gather(
    *(reserve_close_browser(pool, iid, force=force, reason=_reason) for iid in ids),
    return_exceptions=True,
)
outcomes = await asyncio.gather(
    *(await_reserved_or_return_error(entry) for entry in entries),
    return_exceptions=True,
)
```

Preserve the current `closed`, `failed`, and `skipped_protected` result shape. Never hold a lease for one session while reserving or awaiting another session.

`shutdown_pool` must also snapshot and await any entries already present only in `_closing_sessions` before stopping the shared Playwright driver. An external-close or canceled-close coordinator must not be abandoned merely because it is no longer in `_sessions`.

- [ ] **Step 9: Run close tests and pending-task checks**

Run: `uv run --active pytest tests/test_browser_pool_branches.py tests/test_pool_disconnect.py tests/test_browser_pool_events.py tests/test_session_lifecycle.py tests/test_session_ops_mixin_close.py tests/test_session_ops_mixin_lifecycle.py tests/test_driver_relaunch.py tests/test_post_review_hardening.py tests/test_pool_invariants.py tests/test_operation_gate_integration.py -q --no-cov`

Expected: all tests pass, `_closing_sessions` is empty at teardown, and no close coordinator is reported as pending.

- [ ] **Step 10: Commit durable close coordination**

```bash
git add src/octowright/browser_pool/pool.py src/octowright/browser_pool/lifecycle.py src/octowright/browser_pool/launch_pipeline.py src/octowright/browser_pool/roster.py src/octowright/browser_pool/listeners.py src/octowright/browser_pool/driver_relaunch.py src/octowright/session/core.py src/octowright/session/core_ops_mixin.py tests/test_browser_pool_branches.py tests/test_pool_disconnect.py tests/test_browser_pool_events.py tests/test_session_lifecycle.py tests/test_session_ops_mixin_close.py tests/test_session_ops_mixin_lifecycle.py tests/test_driver_relaunch.py tests/test_post_review_hardening.py tests/_pool_invariants.py tests/test_pool_invariants.py tests/test_operation_gate_integration.py
git commit -m "feat(pool): coordinate durable FIFO browser close"
```

### Task 8: Make capture-and-close, handoff, and fluid relaunch atomic on the source session

**Files:**
- Modify: `src/octowright/browser_pool/lifecycle.py:117-225`
- Modify: `src/octowright/browser_pool/pool.py:232-305`
- Modify: `src/octowright/server/browser/inspect.py:246-310`
- Modify: `tests/test_browser_pool_branches.py`
- Modify: `tests/test_server_browser_inspect_tools.py`
- Modify: `tests/test_operation_gate_integration.py`

- [ ] **Step 1: Write failing compound-lifecycle tests**

Add tests that queue an earlier operation, start each compound helper, then attempt a later manual action. Assert the earlier operation completes first, the preparation/capture sees its final URL, later work receives `SessionClosingError`, the source closes exactly once, and the replacement launches from the prepared state. Also test non-closing stateless handoff holds one ordinary source lease and does not set `closing`.

```python
@pytest.mark.asyncio
async def test_capture_and_close_preparation_runs_at_close_ticket(
    pool: BrowserPool,
    session: BrowserSession,
) -> None:
    release_navigation = asyncio.Event()
    navigation = asyncio.create_task(update_url_while_owned(session, "https://final.test", release_navigation))
    await wait_for_active(session._operation_gate, "browser_navigate")
    capture = asyncio.create_task(browser_capture_and_close(session.instance_id, force=True))
    await wait_for_state(session._operation_gate, "closing")
    with pytest.raises(SessionClosingError):
        await session.click("#too-late")
    release_navigation.set()
    await navigation
    result = await capture
    assert result["url"] == "https://final.test"
    assert result["closed"] is True


@pytest.mark.asyncio
async def test_nonclosing_handoff_uses_one_ordinary_source_lease(pool: BrowserPool, session: BrowserSession) -> None:
    session.profile = None
    session.user_data_dir = None
    launched = asyncio.Event()
    pool.launch = AsyncMock(side_effect=lambda **kwargs: launched.set() or {"instance_id": "replacement"})
    result = await pool.handoff(session.instance_id, close_original=False, accept_stateless=True)
    assert launched.is_set()
    assert session.operation_snapshot()["state"] == "open"
    assert result["old_closed"] is False
```

Test that a compound close request arriving after another close cutoff is rejected instead of pretending it captured evidence, that external closure before a preparation ticket runs raises `SessionClosedError` after durable cleanup rather than fabricating capture/relaunch data, and that pure invalid path/stateless validation happens before reservation with no side effects.

- [ ] **Step 2: Run focused compound tests and verify RED**

Run: `uv run --active pytest tests/test_browser_pool_branches.py tests/test_server_browser_inspect_tools.py tests/test_operation_gate_integration.py -q --no-cov`

Expected: capture/handoff/relaunch either interleave or deadlock when they try to call ordinary methods before `pool.close`.

- [ ] **Step 3: Add a preparation callback to fresh close reservations**

Extend `reserve_close_browser` with internal-only parameters:

```python
async def reserve_close_browser(
    pool: BrowserPool,
    instance_id: str,
    *,
    force: bool,
    reason: SessionCloseReason,
    operation_name: LiteralString = "browser_close",
    expected_session: BrowserSession | None = None,
    preparation: Callable[[BrowserSession], Awaitable[object]] | None = None,
    require_fresh: bool = False,
) -> ClosingSession:
```

If `require_fresh=True` and `_closing_sessions` already contains the identity, raise `SessionClosingError`; a compound helper cannot retroactively attach preparation to someone else's accepted close. If it creates the reservation, store the callback on `ClosingSession`, run it exactly once after the close ticket owns the gate and before `_teardown_after_close_cutoff`, and retain its return in `CloseCoordinatorOutcome.prepared`.

Pass `operation_name` to `gate.reserve_close`; `close_browser` uses the default `browser_close`, while capture, handoff, and relaunch pass their own fixed root identifiers. Add `close_with_preparation(...) -> CloseCoordinatorOutcome` as an internal lifecycle helper. It performs the same protection preflight and durable/shielded coordination as normal close; cancellation after acceptance cannot skip either preparation or teardown.

- [ ] **Step 4: Convert capture-and-close to a close preparation**

Keep screenshot path parsing/containment and boolean argument validation before reservation because they are pure. Move page title, active target URL, screenshot, and optional ARIA snapshot into one preparation callback:

```python
async def _capture_before_close(session: BrowserSession) -> dict[str, Any]:
    async with session.operation("browser_capture_and_close"):
        title = await session.page.title()
        frame_target = session._target()
        url = frame_target.url
        await session.screenshot(target)
        captured: dict[str, Any] = {"title": title, "url": url, "screenshot_path": str(target)}
        if snapshot:
            try:
                aria_full = await asyncio.wait_for(
                    frame_target.locator("html").aria_snapshot(),
                    timeout=SNAPSHOT_TIMEOUT_S,
                )
                captured["aria"] = aria_full[:DEFAULT_PREVIEW_CHARS]
            except TimeoutError:
                captured.update(snapshot_timeout_fields(instance_id))
        return captured
```

The inner operation context re-enters the already-owning close coordinator task even though state is `closing`; the observable root remains the reservation's `browser_capture_and_close` name, and a direct call outside that coordinator cannot bypass admission. Call `close_with_preparation` with operation name `browser_capture_and_close` and `require_fresh=True`, merge the prepared dict with `{"closed": True}`, and preserve the existing protected-refusal result rather than raising from the MCP tool. The protection check must be the reservation preflight, so no capture body runs on refusal.

If external closure invalidates the ticket before preparation, the coordinator still runs teardown but completes the shared compound outcome with `SessionClosedError`; it must not return a partial dict missing the promised title/URL/screenshot fields.

- [ ] **Step 5: Capture a relaunch snapshot under the close ticket**

Define one private immutable `RelaunchSnapshot` dataclass in `lifecycle.py` containing `kind`, `label`, `profile`, `user_data_dir`, `stabilize`, `trace`, `har_path`, `protected`, `protected_reason`, and final `target_url`. Populate it inside a reentrant `session.operation("browser_handoff")` or `session.operation("browser_relaunch_fluid")` context in the preparation callback, using `session.page.url or session.url` only after the matching close ticket owns the gate.

For `handoff(close_original=True)` and `relaunch_fluid`, reserve a fresh forced close with that callback, await the durable outcome, then launch from `outcome.prepared`. Restore `protected_reason` on the replacement as today. For `handoff(close_original=False)`, perform validation, source snapshot, replacement launch, and response construction inside:

```python
async with source.operation("browser_handoff"):
    return await _handoff_without_close_owned(pool, source, headed=headed)
```

Restore the replacement's original protection reason through `await new_session.set_protected_state(source_protected, reason=source_protected_reason)`, never by assigning `protected_reason` outside its control mutex. The replacement is newly launched by this invocation; do not acquire a lease on any other pre-existing session while the source lease is held.

- [ ] **Step 6: Run compound lifecycle tests**

Run: `uv run --active pytest tests/test_browser_pool_branches.py tests/test_server_browser_inspect_tools.py tests/test_operation_gate_integration.py -q --no-cov`

Expected: capture, handoff, and relaunch tests pass without lock inversion; closing registry entries are empty after every case.

- [ ] **Step 7: Commit compound lifecycle operations**

```bash
git add src/octowright/browser_pool/lifecycle.py src/octowright/browser_pool/pool.py src/octowright/server/browser/inspect.py tests/test_browser_pool_branches.py tests/test_server_browser_inspect_tools.py tests/test_operation_gate_integration.py
git commit -m "feat(pool): make closing compound operations atomic"
```

### Task 9: Hold one root lease for macros, sequences, conditionals, and artifact replay

**Files:**
- Modify: `tests/_operation_gate_fakes.py`
- Modify: `src/octowright/session/_protocols.py`
- Modify: `src/octowright/macros/execution.py:224-480`
- Modify: `src/octowright/macros/artifacts.py:139-270`
- Modify: `src/octowright/conditional.py:59-102`
- Modify: `src/octowright/macros/checks.py:12-68`
- Modify: `tests/test_macros.py`
- Modify: `tests/test_macro_execution_branches.py`
- Modify: `tests/test_macro_calls_branches.py`
- Modify: `tests/test_macro_artifacts.py`
- Modify: `tests/test_macro_checks.py`
- Modify: `tests/test_macro_checks_branches.py`
- Modify: `tests/test_conditional.py`
- Modify: `tests/test_telemetry_fixes.py`
- Modify: `tests/test_operation_gate_integration.py`

- [ ] **Step 1: Reuse the operation-aware fake in macro harnesses**

Use the helper created in Task 4 to give existing macro fakes a real gate without duplicating scheduler stubs. Its locked shape is:

```python
class OperationAwareFake:
    instance_id = "fake-session"
    kind = "chromium"

    def __init__(self) -> None:
        self._test_operation_gate = SessionOperationGate(
            self.instance_id,
            self.kind,
            queue_timeout_seconds=30,
        )

    def operation(
        self,
        operation_name: LiteralString,
        *,
        wait_timeout_seconds: float | None | UseDefault = USE_DEFAULT,
    ) -> AbstractAsyncContextManager[None]:
        return self._test_operation_gate.operation(
            operation_name,
            wait_timeout_seconds=wait_timeout_seconds,
        )

    def operation_snapshot(self) -> OperationGateSnapshot:
        return self._test_operation_gate.snapshot()
```

Have macro fake classes inherit this helper and call its initializer. Do not add production fallback behavior for sessions without a gate.

- [ ] **Step 2: Write failing macro atomicity tests**

Use events in fake action methods to prove a manual action cannot enter between macro actions, a nested `macro_call` re-enters, a sequence keeps the root name `macro_run_sequence` across all member macros, failure diagnostics occur before lease release, and artifact before/replay/after evidence is one boundary:

```python
@pytest.mark.asyncio
async def test_manual_action_cannot_interleave_macro(session: MacroGateFake) -> None:
    session.block_after_first = asyncio.Event()
    session.release_first = asyncio.Event()
    macro_task = asyncio.create_task(run_macro(session, "two-actions"))
    await session.block_after_first.wait()
    manual = asyncio.create_task(session.click("#manual"))
    await wait_for_queue_depth(session._test_operation_gate, 1)
    assert session.calls == ["macro:first"]
    session.release_first.set()
    await asyncio.gather(macro_task, manual)
    assert session.calls == ["macro:first", "macro:second", "manual"]


@pytest.mark.asyncio
async def test_failure_bundle_is_captured_before_manual_waiter(session: MacroGateFake) -> None:
    with pytest.raises(RuntimeError):
        await run_macro_with_waiting_manual_action(session, "failing-macro")
    assert session.calls.index("diagnostic_bundle") < session.calls.index("manual")
```

Add a recorder assertion that gate acquire/release/timeout emits no JSONL rows and a replay/export assertion that no operation-gate action kind appears.

- [ ] **Step 3: Run macro tests and verify RED**

Run: `uv run --active pytest tests/test_macros.py tests/test_macro_execution_branches.py tests/test_macro_calls_branches.py tests/test_macro_artifacts.py tests/test_macro_checks.py tests/test_macro_checks_branches.py tests/test_conditional.py tests/test_telemetry_fixes.py tests/test_operation_gate_integration.py -q --no-cov`

Expected: manual work can run between macro actions/evidence and old fakes lack `operation()`.

- [ ] **Step 4: Put leases at the public macro boundaries**

Wrap `_push_status` itself with `async with session.operation("macro_status")` so direct calls are safe and the architecture scanner sees its `session.page.evaluate` boundary. During a macro it re-enters the outer owner and leaves the root name unchanged.

Wrap `run_macro` outside its span and `_run_macro_impl`, so loading, substitution, pill start, every action/nested call, slowmo, progress, diagnostics, healing suggestion, final pill, log, metrics, and response all remain under one root:

```python
async def run_macro(
    session: SessionLike,
    name: str,
    args: dict[str, Any] | None = None,
    *,
    slowmo_ms: int | None = None,
    ctx: Any | None = None,
) -> MacroRunResult:
    async with session.operation("macro_run"):
        with span("octowright.macro.run", macro=name, instance_id=session.instance_id, kind=session.kind):
            return await _run_macro_impl(session, name, args, slowmo_ms=slowmo_ms, ctx=ctx)
```

Wrap the full `run_sequence` with `macro_run_sequence`; its calls to `run_macro` re-enter in the exact same task, so the root observable name remains `macro_run_sequence`. Nested `macro_call` already dispatches recursively in the same task and needs no second scheduler.

- [ ] **Step 5: Gate direct conditional/check helpers without changing macro schemas**

Change `selector_present` to accept the session and acquire `macro_condition` before dereferencing the active target. Update `do_if_selector` accordingly. Change `macros/checks.py` helpers to accept `SessionLike` and acquire a fixed `macro_check` lease, then use `session._target()`; callers inside a macro re-enter its root.

Do not add a macro action, recorder row, `_REPLAY_RENAME_KEYS`, `_REPLAY_DROP_KEYS`, or exporter entry for gate scheduling.

- [ ] **Step 6: Wrap artifact replay and evidence once**

Wrap the complete `run_macro_artifact` body with `async with session.operation("macro_artifact_run")`. Both screenshots, replay, failure traceback evidence, run bundle writes, verification, metrics, and response remain in that boundary. The nested `run_macro` and `session.screenshot` calls re-enter; the root status remains `macro_artifact_run`.

- [ ] **Step 7: Run macro, replay, and recorder tests**

Run: `uv run --active pytest tests/test_macros.py tests/test_macro_execution_branches.py tests/test_macro_calls_branches.py tests/test_macro_artifacts.py tests/test_macro_checks.py tests/test_macro_checks_branches.py tests/test_conditional.py tests/test_telemetry_fixes.py tests/test_operation_gate_integration.py tests/test_macro_runtime_branches.py tests/test_export.py -q --no-cov`

Expected: macro/sequence/artifact atomicity passes and macro/JSONL/export shapes are unchanged.

- [ ] **Step 8: Commit whole-invocation macro leases**

```bash
git add src/octowright/macros/execution.py src/octowright/macros/artifacts.py src/octowright/macros/checks.py src/octowright/conditional.py src/octowright/session/_protocols.py tests/_operation_gate_fakes.py tests/test_macros.py tests/test_macro_execution_branches.py tests/test_macro_calls_branches.py tests/test_macro_artifacts.py tests/test_macro_checks.py tests/test_macro_checks_branches.py tests/test_conditional.py tests/test_telemetry_fixes.py tests/test_operation_gate_integration.py tests/test_macro_runtime_branches.py tests/test_export.py
git commit -m "feat(macros): hold one lease per logical replay"
```

### Task 10: Gate complete MCP, HTTP, launch, and scenario browser workflows

**Files:**
- Create: `src/octowright/server/browser/_operation.py`
- Modify: `src/octowright/server/browser/discovery.py`
- Modify: `src/octowright/server/browser/discovery_links.py`
- Modify: `src/octowright/server/browser/input.py`
- Modify: `src/octowright/server/browser/inspect.py`
- Modify: `src/octowright/server/browser/inspect_assertions.py`
- Modify: `src/octowright/server/browser/lifecycle.py`
- Modify: `src/octowright/server/browser/network.py`
- Modify: `src/octowright/server/browser/views.py`
- Modify: `src/octowright/server/captures.py`
- Modify: `src/octowright/server/goldens.py`
- Modify: `src/octowright/http/routes/sessions.py`
- Modify: `src/octowright/http/routes/media.py`
- Modify: `src/octowright/browser_pool/launch_pipeline.py`
- Modify: `src/octowright/scenarios_pool.py:450-477`
- Modify: `tests/test_server_browser_input_tools.py`
- Modify: `tests/test_browser_click_fill_unified.py`
- Modify: `tests/test_browser_tool_wrappers.py`
- Modify: `tests/test_consolidated_tools.py`
- Modify: `tests/test_server_browser_inspect_tools.py`
- Modify: `tests/test_server_browser_lifecycle_tools.py`
- Modify: `tests/test_server_browser_views_tools.py`
- Modify: `tests/test_server_browser_network_tools.py`
- Modify: `tests/test_http_discovery.py`
- Modify: `tests/test_http_state_seam.py`
- Modify: `tests/test_captures.py`
- Modify: `tests/test_server_captures_tools.py`
- Modify: `tests/test_goldens.py`
- Modify: `tests/test_goldens_agentic.py`
- Modify: `tests/test_http_server.py`
- Modify: `tests/test_http_server_writes.py`
- Modify: `tests/test_http_routes_sessions_branches.py`
- Modify: `tests/test_http_routes_media_branches.py`
- Modify: `tests/test_scenarios_pool.py`
- Modify: `tests/test_operation_gate_integration.py`
- Create: `tests/test_operation_gate_tool_schemas.py`

- [ ] **Step 1: Write failing complete-workflow tests**

Add representative tests for each workflow family. A composite action plus outline must keep the action root through response construction; direct discovery/capture/golden paths must not release between page reads; HTTP screenshot/ARIA/selector access must queue; scenario URL waits must be per-session and parallel across participants; initial launch navigation and Firefox/WebKit new-tab redirect must queue normally after publication.

```python
@pytest.mark.asyncio
async def test_browser_click_outline_is_one_root_operation(tool_session: ToolGateFake) -> None:
    result = await browser_click(tool_session.instance_id, selector="#buy", response_mode="outline")
    assert result["ok"] is True
    assert tool_session.observed_roots == ["browser_click", "browser_click"]


@pytest.mark.asyncio
async def test_capture_create_keeps_content_and_metadata_together(tool_session: ToolGateFake) -> None:
    await capture_create(tool_session.instance_id, source="snapshot")
    assert tool_session.observed_roots == [
        "capture_create",
        "capture_create",
        "capture_create",
    ]


@pytest.mark.asyncio
async def test_http_live_screenshot_waits_for_session_gate(client_state: HttpGateState) -> None:
    async with client_state.session.operation("owner"):
        response_task = asyncio.create_task(client_state.call_live_screenshot())
        await wait_for_queue_depth(client_state.session._operation_gate, 1)
        client_state.session.page.screenshot.assert_not_awaited()
    response = await response_task
    assert response.status_code == 200
```

Add a schema-regression assertion using `mcp._tool_manager.list_tools()`: pin the current `name`, `description`, `parameters`, `tool.fn_metadata.wrap_output is False` (the installed MCP runtime's representation of `structured_output=False`), and `output_schema is None` values for `page_list`, `browser_list_frames`, and `browser_set_dialog_policy`. This is a new `tests/test_operation_gate_tool_schemas.py` file; do not refer to a nonexistent generic schema test module.

Update every affected server/HTTP test pool to return an `OperationAwareFake`-based session (with the existing action methods replaced by `AsyncMock` as needed). A bare `MagicMock.operation()` is not an async context manager and must not be papered over by weakening `browser_operation`.

- [ ] **Step 2: Run server/HTTP tests and verify RED**

Run: `uv run --active pytest tests/test_server_browser_input_tools.py tests/test_browser_click_fill_unified.py tests/test_browser_tool_wrappers.py tests/test_consolidated_tools.py tests/test_server_browser_inspect_tools.py tests/test_server_browser_lifecycle_tools.py tests/test_server_browser_views_tools.py tests/test_server_browser_network_tools.py tests/test_http_discovery.py tests/test_http_state_seam.py tests/test_captures.py tests/test_server_captures_tools.py tests/test_goldens.py tests/test_goldens_agentic.py tests/test_http_server.py tests/test_http_server_writes.py tests/test_http_routes_sessions_branches.py tests/test_http_routes_media_branches.py tests/test_scenarios_pool.py tests/test_operation_gate_integration.py tests/test_operation_gate_tool_schemas.py -q --no-cov`

Expected: direct/composite Playwright workflows expose interleaving windows.

- [ ] **Step 3: Add one DRY server-side operation context helper**

Create:

```python
@asynccontextmanager
async def browser_operation(
    browser_pool: BrowserPool,
    instance_id: str,
    operation_name: LiteralString,
) -> AsyncIterator[BrowserSession]:
    session = browser_pool.get(instance_id)
    async with session.operation(operation_name):
        yield session
```

This helper does not replace the session decorators; it defines complete feature boundaries while decorators protect direct Python calls. Always pass a source-code string literal. Perform only pure validation before entering; resolve live target/page/frame state inside.

- [ ] **Step 4: Wrap the complete browser tool workflows**

Use the helper for the following tool boundaries and keep current MCP schemas unchanged:

| Module | Complete boundaries |
|---|---|
| `discovery.py` | `browser_fields`, `browser_find_field`, `browser_page_outline` |
| `discovery_links.py` | `browser_links`, `browser_find_link` |
| `input.py` | click/type/fill/press/get-text/upload/hover/select/drag, including optional outline/brief response modes |
| `inspect.py` | screenshot/snapshot/evaluate/wait/read-markdown/brief/observe; exclude cached recording/export and Task 8's reserved capture-and-close |
| `inspect_assertions.py` | all four `browser_expect_*` methods, including bounded response construction |
| `lifecycle.py` | navigate/navigate-back/resize/viewport-status/viewport-sync/open-url, including outline/brief; exclude launch/list/close/protection/relaunch |
| `network.py` | dialog policy, mock route, unmock route; cached network reads remain concurrent |
| `views.py` | async page/frame list, switch/reset plus outline, and page close; cached download views/wait remain concurrent |
| `captures.py` | `capture_create` from source read through URL/title metadata and saved response |
| `goldens.py` | save/assert/verify-loop from snapshot through comparison/save result; list/delete remain disk-only |

Any free helper in these modules that directly dereferences `Page`, `Frame`, `_target()`, a locator, or keyboard must also accept `SessionLike` and take a literal reentrant operation context, or be inlined into the outer boundary. The outer tool boundary provides feature atomicity; the inner boundary makes direct helper calls safe and lets the architecture scanner prove the access without call-graph guessing.

The exact pattern for a composite is:

```python
async def browser_click(
    instance_id: str,
    selector: str | None = None,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
    timeout_ms: int | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]:
    if not (selector or role or label or text or test_id):
        raise ValueError("provide a selector or at least one ARIA locator (role/label/text/test_id)")
    async with browser_operation(pool, instance_id, "browser_click") as session:
        if role or label or text or test_id:
            await session.click_by(
                role=role,
                role_name=role_name,
                role_exact=role_exact,
                label=label,
                text=text,
                test_id=test_id,
                timeout_ms=timeout_ms,
            )
        elif selector:
            await session.click(selector)
        else:
            raise ValueError("provide a selector or at least one ARIA locator (role/label/text/test_id)")
        result: dict[str, Any] = {"ok": True}
        if response_mode == "outline":
            result["outline"] = await browser_page_outline(instance_id)
        elif response_mode == "brief":
            result["brief"] = await browser_brief(instance_id)
        return result
```

Nested tool/helper calls run in the same task and therefore re-enter while retaining the outer root.

- [ ] **Step 5: Gate HTTP live browser access**

Wrap `_live_session_detail_response` ARIA capture with `dashboard_session_detail`, `session_screenshot_now` with `dashboard_screenshot`, and `session_selector_validate` with `dashboard_selector_validate`. Keep request JSON/query parsing and session-ID validation outside because they are pure; resolve `live.page`, `_target`, locators, and response data inside.

Map `SessionClosingError`, `SessionClosedError`, and `SessionBusyTimeoutError` through the current endpoint-local error response style (HTTP 409 for closing/closed, 503 for queue timeout); do not restart or disconnect the ASGI/MCP server.

- [ ] **Step 6: Gate post-publication launch and new-tab redirect work**

Acquire `new_session.operation("browser_launch_navigation")` immediately before registry publication, then publish while already owning it. Move the existing `_sessions` insertion, `registered=True`, `_safe_manifest_record`, launch metrics/logging, caught `page.goto(target_url)`, markdown-task scheduling, `build_launch_result`, and `nav_warning` attachment into that context without changing their current data or error semantics.

This ordering prevents a dashboard or concurrent in-process caller from winning a ticket in the await gap between registry insertion and the initial navigation. Preserve the existing `nav_warning` behavior by catching the navigation exception inside the lease. Keep context creation, init-script installation, listener wiring, and trace start under the `launch-time-before-session-publication` classification because no caller can resolve the session yet. Extract those operations into `_prepare_session_before_publication`; do not classify all of `post_context_setup`, because it also contains the post-publication navigation.

Change `_make_new_tab_redirector` to accept `new_session`; its spawned `_redirect` child acquires `new_tab_redirect` before `opener`, load-state, URL, and `goto` access. Pass the session when installing the Firefox/WebKit handler. It uses the ordinary timeout and logs a gate rejection rather than swallowing it silently.

- [ ] **Step 7: Gate scenario URL waits without cross-session ownership**

In each independently gathered `_wait` coroutine, use:

```python
async with session.operation("scenario_wait_for_sync"):
    if not re.search(url, session.page.url):
        await session.page.wait_for_url(url, timeout=timeout_ms or 30000)
```

Do not hold a scenario-wide session lease; participants must remain parallel.

- [ ] **Step 8: Run server/HTTP/schema tests**

Run: `uv run --active pytest tests/test_server_browser_input_tools.py tests/test_browser_click_fill_unified.py tests/test_browser_tool_wrappers.py tests/test_consolidated_tools.py tests/test_server_browser_inspect_tools.py tests/test_server_browser_lifecycle_tools.py tests/test_server_browser_views_tools.py tests/test_server_browser_network_tools.py tests/test_http_discovery.py tests/test_http_state_seam.py tests/test_captures.py tests/test_server_captures_tools.py tests/test_goldens.py tests/test_goldens_agentic.py tests/test_http_server.py tests/test_http_server_writes.py tests/test_http_routes_sessions_branches.py tests/test_http_routes_media_branches.py tests/test_scenarios_pool.py tests/test_operation_gate_integration.py tests/test_operation_gate_tool_schemas.py -q --no-cov`

Expected: all workflow and schema tests pass; same-session composites retain one root and different scenario/browser sessions remain parallel.

- [ ] **Step 9: Commit complete workflow boundaries**

```bash
git add src/octowright/server/browser/_operation.py src/octowright/server/browser/discovery.py src/octowright/server/browser/discovery_links.py src/octowright/server/browser/input.py src/octowright/server/browser/inspect.py src/octowright/server/browser/inspect_assertions.py src/octowright/server/browser/lifecycle.py src/octowright/server/browser/network.py src/octowright/server/browser/views.py src/octowright/server/captures.py src/octowright/server/goldens.py src/octowright/http/routes/sessions.py src/octowright/http/routes/media.py src/octowright/browser_pool/launch_pipeline.py src/octowright/scenarios_pool.py tests/test_server_browser_input_tools.py tests/test_browser_click_fill_unified.py tests/test_browser_tool_wrappers.py tests/test_consolidated_tools.py tests/test_server_browser_inspect_tools.py tests/test_server_browser_lifecycle_tools.py tests/test_server_browser_views_tools.py tests/test_server_browser_network_tools.py tests/test_http_discovery.py tests/test_http_state_seam.py tests/test_captures.py tests/test_server_captures_tools.py tests/test_goldens.py tests/test_goldens_agentic.py tests/test_http_server.py tests/test_http_server_writes.py tests/test_http_routes_sessions_branches.py tests/test_http_routes_media_branches.py tests/test_scenarios_pool.py tests/test_operation_gate_integration.py tests/test_operation_gate_tool_schemas.py
git commit -m "feat(server): gate complete browser workflows"
```

### Task 11: Add a CI architecture scanner for future Playwright bypasses

**Files:**
- Create: `scripts/check_operation_gate_architecture.py`
- Create: `tests/test_operation_gate_architecture.py`
- Modify: `Makefile:11-25`
- Modify: gated/classified production modules reported by the first scanner run

- [ ] **Step 1: Write failing scanner contract tests**

Test a synthetic file for each acceptance/rejection mode:

```python
def test_rejects_ungated_session_page_access(tmp_path: Path) -> None:
    source = tmp_path / "bad.py"
    source.write_text(
        "async def leak(session):\n"
        "    await session.page.locator('#secret').click()\n",
        encoding="utf-8",
    )
    violations = scan_paths([source], bypasses={})
    assert [(item.function, item.line) for item in violations] == [("leak", 2)]


def test_accepts_decorator_context_and_reasoned_bypass(tmp_path: Path) -> None:
    source = tmp_path / "good.py"
    source.write_text(
        "@gated_operation('browser_click')\n"
        "async def decorated(session):\n"
        "    await session.page.click('#x')\n"
        "async def contextual(session):\n"
        "    async with session.operation('browser_click'):\n"
        "        await session.page.click('#x')\n"
        "def cached(session):\n"
        "    return session.page_count\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []
```

Add mutation-style cases for `target = session._target(); locator = target.locator(...); await locator.click()`, `page = session.page; await page.title()`, a Playwright-annotated `Page` parameter, a direct access before an otherwise-valid `async with`, a dynamic operation name, and a nested callback inside a gated function. The first three must be detected, the pre-context access must still fail, dynamic names must fail, and the nested callback must establish its own gate/classification rather than inheriting lexical ownership from its parent.

Add one accepted server-helper case using `async with browser_operation(pool, instance_id, "browser_click") as session:` and one rejected case whose third argument is dynamic. This is the complete-workflow boundary introduced in Task 10, so the scanner must understand it without treating arbitrary async context managers as gates.

Also assert a bypass with an unknown class, empty reason, missing function, or no detected Playwright access fails as stale/invalid; the inventory is a ratchet, not a blanket ignore list. Test the two operation-name forwarders described below separately: any third forwarder, stale entry, or forwarder that also contains Playwright access must fail.

- [ ] **Step 2: Run scanner tests and verify RED**

Run: `uv run --active pytest tests/test_operation_gate_architecture.py -q --no-cov`

Expected: import fails because the scanner does not exist.

- [ ] **Step 3: Implement the AST scanner**

The scanner must ignore comments/string literals, `TYPE_CHECKING` bodies, and `Protocol` method declarations, and report source path, deepest qualified function, and line. Use per-function lightweight taint propagation rather than variable names alone:

- seed `session/self/live/source.page|pages|context|browser|active_frame`, calls to `_target()`, Playwright-import-annotated parameters, and conventional direct Playwright parameters such as `page`, `frame`, `target`, `context`, and `locator`;
- propagate through assignments, tuple unpacking, comprehensions over `session.pages`/`page.frames`, and locator/page/frame-returning calls;
- report calls and property reads on a tainted value for the common Playwright API set below;
- do not confuse Starlette `Request`/`WebSocket` annotations with Playwright types; resolve the import module, not just the class name.

```python
PLAYWRIGHT_ROOT_ATTRS = frozenset({"page", "pages", "context", "browser", "active_frame"})
PLAYWRIGHT_CHAIN_ATTRS = frozenset(
    {
        "locator", "keyboard", "screencast", "frames", "goto", "click", "fill",
        "press", "evaluate", "aria_snapshot", "screenshot", "new_page", "title",
        "wait_for_selector", "query_selector", "wait_for_url", "route", "unroute",
        "set_input_files", "hover", "drag_and_drop", "select_option", "go_back",
        "set_viewport_size", "expect_popup", "wait_for_load_state", "inner_text",
        "count", "close", "is_closed", "opener", "on", "add_init_script",
        "expose_binding", "start", "stop", "save_as", "suggested_filename",
        "url", "main_frame", "video", "path", "tracing",
    }
)
APPROVED_BYPASS_CLASSES = frozenset(
    {
        "event-critical",
        "teardown-only",
        "cached-property-only",
        "launch-time-before-session-publication",
    }
)

OPERATION_NAME_FORWARDERS = {
    "session/operation_gate.py:gated_operation._decorate._wrapped": (
        "forwards the fixed name validated once when the decorator is constructed"
    ),
    "server/browser/_operation.py:browser_operation": (
        "forwards the literal name required and checked at every complete server workflow call site"
    ),
}
```

A function is fully gated when it has a literal `@gated_operation("fixed_name")`. For context-manager gating, only access nodes lexically contained in the body of a literal `async with <session>.operation("fixed_name")`, `async with browser_operation(<pool>, <instance_id>, "fixed_name") as <session>`, or the reserved `close_operation(...)` body are accepted; one context elsewhere in the function must not bless access before/after it. Nested functions are independent scopes. The scanner rejects dynamic operation-name expressions in either public context helper even when no Playwright hit is present, except for the two exact, validated forwarding functions in `OPERATION_NAME_FORWARDERS`.

The forwarder inventory is not a Playwright bypass. Each entry must exist, contain exactly one dynamic forwarding context, and contain no detected Playwright access; its non-empty reason is checked for staleness just like the access bypasses. All callers of `browser_operation` still require a source-code literal.

It scans `src/octowright/**/*.py`, excludes `src/octowright/terminal/**` because terminal gating is out of scope, and validates every allowlist entry against a real detected hit. Its CLI exits nonzero with deterministic sorted diagnostics and exposes `scan_paths` as a pure testable function.

- [ ] **Step 4: Seed only the narrow bypass inventory**

Use a mapping keyed by `relative/path.py:qualified.function`, each with one approved class and a non-empty reason. The initial inventory must contain only these categories:

```python
BYPASSES = {
    # Resources do not belong to a published BrowserSession yet.
    "browser_pool/cleanup.py:safe_close": (
        "launch-time-before-session-publication",
        "best-effort cleanup of a context/browser whose launch never published a session",
    ),
    "browser_pool/launch_helpers.py:_open_browser_context": (
        "launch-time-before-session-publication",
        "creates context/page before BrowserSession construction and registry publication",
    ),
    "browser_pool/visuals.py:wire_init_scripts": (
        "launch-time-before-session-publication",
        "injects context init scripts before BrowserSession registry publication",
    ),
    "browser_pool/launch_pipeline.py:_build_session_object": (
        "launch-time-before-session-publication",
        "captures the launch-created page video handle before registry publication",
    ),
    "browser_pool/launch_pipeline.py:_prepare_session_before_publication": (
        "launch-time-before-session-publication",
        "listener, binding, and trace setup completes before registry insertion",
    ),
    "browser_pool/pool.py:BrowserPool._expose_viewport_binding": (
        "launch-time-before-session-publication",
        "registers the viewport callback on a context before its session is published",
    ),
    "browser_pool/listeners.py:_wire_close_evictor": (
        "launch-time-before-session-publication",
        "installs context/browser close signals before the session is published",
    ),
    # These return or compare only Octowright-owned cached references.
    "session/core.py:BrowserSession.__post_init__": (
        "cached-property-only",
        "initializes the cached page list before the session is published without browser I/O",
    ),
    "session/core.py:BrowserSession._target": (
        "cached-property-only",
        "returns the cached active-frame/page reference without dereferencing Playwright",
    ),
    # A close cutoff or external close has already made ordinary admission impossible.
    "session/core_ops_mixin.py:SessionOpsMixin._teardown_after_close_cutoff": (
        "teardown-only",
        "runs only after a reserved close owns the cutoff or for broken/external cleanup",
    ),
    "session/screencast.py:ScreencastManager._terminate_producer_after_close": (
        "teardown-only",
        "best-effort producer stop runs only after external/normal close made admission impossible",
    ),
    # Browser callbacks must respond synchronously or unblock the admitted call.
    "session/core_interaction_mixin.py:SessionInteractionMixin._handle_dialog._act": (
        "event-critical",
        "dialog accept/dismiss must unblock the Playwright action that already owns the gate",
    ),
    "session/core_interaction_mixin.py:SessionInteractionMixin.mock_route._handler": (
        "event-critical",
        "route fulfill must unblock the network request awaited by the active operation",
    ),
    "browser_pool/listeners.py:_wire_listeners": (
        "event-critical",
        "attaches passive listeners to a page created by a popup event or admitted recovery",
    ),
    "browser_pool/listeners.py:_wire_close_evictor._on_page_close": (
        "event-critical",
        "last-page detection must invalidate admission synchronously with Playwright's close event",
    ),
    "browser_pool/listeners.py:_wire_close_evictor._on_page_crash": (
        "event-critical",
        "crash bookkeeping and recovery scheduling must run from the Playwright crash event",
    ),
    "session/core_io_mixin.py:SessionIOMixin._register_popup": (
        "event-critical",
        "context page event updates cached page bookkeeping synchronously; user work remains gated",
    ),
    "session/core_io_mixin.py:SessionIOMixin.attach_console": (
        "launch-time-before-session-publication",
        "registers the initial page console callback before registry publication",
    ),
    "session/core_io_mixin.py:SessionIOMixin.attach_console._on_console": (
        "event-critical",
        "copies one browser console event into Octowright's bounded cache and recorder",
    ),
    "session/core_io_mixin.py:SessionIOMixin._register_popup._on_console": (
        "event-critical",
        "copies one popup console event into Octowright's bounded cache and recorder",
    ),
    "session/core_io_mixin.py:SessionIOMixin._handle_websocket": (
        "event-critical",
        "registers passive frame/close callbacks and records browser-emitted socket metadata",
    ),
    "session/core_io_mixin.py:SessionIOMixin._handle_websocket._on_frame._handler": (
        "event-critical",
        "copies one browser-emitted websocket frame into bounded recording/cache sinks",
    ),
    "session/core_network_mixin.py:SessionNetworkMixin._handle_response": (
        "event-critical",
        "copies browser response metadata into the bounded network cache",
    ),
    "session/core_network_mixin.py:SessionNetworkMixin._handle_request_failed": (
        "event-critical",
        "copies browser failure metadata into the bounded network cache",
    ),
    "browser_pool/crash_recovery.py:_safe_url": (
        "event-critical",
        "captures the crashed page URL synchronously before scheduling durable recovery",
    ),
    "browser_pool/listeners.py:_wire_user_navigation_logger._make._on_framenavigated": (
        "event-critical",
        "passive browser-originated navigation recording cannot wait behind the operation that triggered it",
    ),
}
```

Treat this inventory as the expected baseline, not permission to add broad entries. If the first production scan finds a current access not represented here, inspect that exact function: add a literal/reentrant gate unless it demonstrably fits one of the four classes, then add one function-level entry with the concrete reason and a regression assertion. Do not classify any MCP/HTTP/macro action, active-target mutation, background markdown/download/recovery/ordinary-screencast operation, post-publication launch navigation, new-tab redirect, or helper merely because all current callers happen to be gated.

- [ ] **Step 5: Run the scanner against production and close every hit**

Run: `uv run --active python scripts/check_operation_gate_architecture.py`

Expected on the first run: a finite report of ungated/unclassified production access.

For each report, either place the complete logical function inside a literal operation boundary or add one of the reasoned entries above only when it exactly matches the four approved categories. Re-run until output is:

```text
OK: all detected Playwright access is gated or narrowly classified
```

- [ ] **Step 6: Wire the scanner into lint**

Add this line after the LOC check in `Makefile`:

```make
	uv run --active python scripts/check_operation_gate_architecture.py
```

- [ ] **Step 7: Run scanner tests, production scan, LOC, and lint subset**

Run: `uv run --active pytest tests/test_operation_gate_architecture.py -q --no-cov`

Run: `uv run --active python scripts/check_operation_gate_architecture.py`

Run: `uv run --active python scripts/check_max_loc.py`

Run: `uv run --active ruff check scripts/check_operation_gate_architecture.py tests/test_operation_gate_architecture.py src/octowright/session src/octowright/browser_pool src/octowright/server src/octowright/http`

Expected: all commands pass with no stale bypass entry and no file above 550 LOC.

- [ ] **Step 8: Commit architecture enforcement**

```bash
git add scripts/check_operation_gate_architecture.py tests/test_operation_gate_architecture.py Makefile
git commit -m "test(architecture): reject ungated Playwright access"
```

If Step 5 required a production correction, add only each exact corrected path to this commit after reviewing `git diff -- <path>`; never stage the entire `src/octowright` tree.

### Task 12: Surface one shared sanitized snapshot in MCP, HTTP, status, and the dashboard

**Files:**
- Modify: `src/octowright/browser_pool/pool.py:190-215`
- Modify: `src/octowright/http/discovery.py:119-142`
- Modify: `src/octowright/server/browser/lifecycle_summary.py:19-34`
- Modify: `src/octowright/server/meta.py:262-348`
- Modify: `packages/octowright-frontend/src/types.ts:1-13`
- Modify: `packages/octowright-frontend/src/session-table.ts:13-95`
- Modify: `packages/octowright-frontend/src/session-table.test.ts`
- Modify: `packages/octowright-frontend/static/styles.css`
- Modify: `tests/test_browser_pool_branches.py`
- Modify: `tests/test_server_browser_lifecycle_tools.py`
- Modify: `tests/test_http_server.py`
- Modify: `tests/test_status_tool.py`

- [ ] **Step 1: Write failing shared-snapshot backend tests**

Pin the exact optional shape and prove synchronous readers never touch the live asyncio queue:

```python
def test_pool_list_sessions_includes_gate_snapshot(pool: BrowserPool, session: BrowserSession) -> None:
    row = pool.list_sessions()[0]
    assert row["operation_gate"] == {
        "state": "open",
        "active_operation": None,
        "active_for_ms": None,
        "queue_depth": 0,
        "oldest_wait_ms": None,
        "queue_timeout_seconds": 300.0,
    }


def test_http_live_summary_reuses_session_snapshot(session: BrowserSession) -> None:
    expected = session.operation_snapshot()
    assert _live_summary(session)["operation_gate"] == expected


def test_status_uses_pool_snapshots_without_page_access(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"instance_id": "one", "kind": "chromium", "operation_gate": BUSY_SNAPSHOT}]
    monkeypatch.setattr(status_pool, "list_sessions", lambda: rows)
    result = octowright_status()
    assert result["pool"]["operation_gates"] == [
        {"instance_id": "one", "kind": "chromium", **BUSY_SNAPSHOT}
    ]
```

Assert browser-list summary rows retain the optional snapshot, while a terminal `_live_summary` row with no `operation_snapshot` omits `operation_gate` rather than fabricating idle state.

- [ ] **Step 2: Run backend snapshot tests and verify RED**

Run: `uv run --active pytest tests/test_browser_pool_branches.py tests/test_server_browser_lifecycle_tools.py tests/test_http_server.py tests/test_status_tool.py -q --no-cov`

Expected: `operation_gate`/`operation_gates` fields are absent.

- [ ] **Step 3: Reuse `operation_snapshot()` in every backend flow**

Add the field once in `BrowserPool.list_sessions()`:

```python
"operation_gate": s.operation_snapshot(),
```

In HTTP `_live_summary`, add it only when the object supplies a callable snapshot method:

```python
snapshot = getattr(session, "operation_snapshot", None)
if callable(snapshot):
    summary["operation_gate"] = snapshot()
```

Preserve `operation_gate` in `browser_list_summary_row`. In `octowright_status`, call `pool.list_sessions()` once, derive `live_browsers`, `protected_browsers`, and `operation_gates` from those rows, and include only `instance_id`, `kind`, and the shared snapshot fields. Do not read Page/Frame or build a second state model.

- [ ] **Step 4: Run backend snapshot tests and verify GREEN**

Run: `uv run --active pytest tests/test_browser_pool_branches.py tests/test_server_browser_lifecycle_tools.py tests/test_http_server.py tests/test_status_tool.py -q --no-cov`

Expected: all backend tests pass and terminal rows omit the optional field.

- [ ] **Step 5: Write failing frontend status-indicator tests**

Add the exact TypeScript type and tests:

```typescript
export interface OperationGateSnapshot {
  state: "open" | "closing" | "closed" | "broken";
  active_operation: string | null;
  active_for_ms: number | null;
  queue_depth: number;
  oldest_wait_ms: number | null;
  queue_timeout_seconds: number;
}
```

```typescript
it("shows busy operation and queue depth", () => {
  const table = renderSessionTable([
    {
      ...row,
      live: true,
      operation_gate: {
        state: "open",
        active_operation: "macro_run",
        active_for_ms: 1250,
        queue_depth: 2,
        oldest_wait_ms: 900,
        queue_timeout_seconds: 300,
      },
    },
  ], true, actions);
  expect(table.querySelector(".operation-badge")?.textContent).toBe("busy macro_run +2");
});

it.each(["closing", "broken"] as const)("shows %s state", (state) => {
  const table = renderSessionTable([{ ...row, live: true, operation_gate: { ...IDLE_GATE, state } }], true, actions);
  expect(table.querySelector(`.operation-badge--${state}`)?.textContent).toBe(state);
});

it("is quiet for idle browsers, closed rows, and terminals", () => {
  const rows = [
    { ...row, live: true, operation_gate: IDLE_GATE },
    { ...row, id: "closed", live: false, operation_gate: { ...IDLE_GATE, state: "closed" as const } },
    { ...row, id: "terminal", kind: "terminal" as const, live: true },
  ];
  expect(renderSessionTable(rows, true, actions).querySelector(".operation-badge")).toBeNull();
});
```

- [ ] **Step 6: Run frontend tests and verify RED**

Run: `cd packages/octowright-frontend && npm test -- --run src/session-table.test.ts`

Expected: TypeScript rejects `operation_gate` and no badge renders.

- [ ] **Step 7: Implement the compact frontend indicator**

Add `operation_gate?: OperationGateSnapshot` to `SessionSummary`. Render the status in the existing leading status cell beside the protection lock:

```typescript
function operationBadge(row: SessionSummary): HTMLElement | null {
  const gate = row.operation_gate;
  if (!gate || row.kind === "terminal" || !row.live) return null;
  let text: string | null = null;
  let renderState: "busy" | "closing" | "broken" | null = null;
  if (gate.state === "open" && gate.active_operation) {
    renderState = "busy";
    text = `busy ${gate.active_operation}${gate.queue_depth > 0 ? ` +${gate.queue_depth}` : ""}`;
  } else if (gate.state === "closing" || gate.state === "broken") {
    renderState = gate.state;
    text = gate.state;
  }
  if (text === null || renderState === null) return null;
  const badge = document.createElement("span");
  badge.className = `operation-badge operation-badge--${renderState}`;
  badge.textContent = text;
  badge.setAttribute("role", "status");
  return badge;
}
```

Use the local render-state union shown above rather than assigning `"busy"` to `OperationGateState`. Style the badge as a compact monospace pill; busy uses the existing warning color, closing uses muted foreground, and broken uses error color. Do not add an endpoint, timer, poller, tooltip containing arguments, or idle badge.

- [ ] **Step 8: Run frontend unit tests and build**

Run: `cd packages/octowright-frontend && npm test -- --run src/session-table.test.ts`

Run: `cd packages/octowright-frontend && npm run build`

Expected: tests and TypeScript build pass; compiled frontend assets update under `src/octowright/server/frontend/`.

- [ ] **Step 9: Commit status observability**

```bash
git add src/octowright/browser_pool/pool.py src/octowright/http/discovery.py src/octowright/server/browser/lifecycle_summary.py src/octowright/server/meta.py packages/octowright-frontend/src/types.ts packages/octowright-frontend/src/session-table.ts packages/octowright-frontend/src/session-table.test.ts packages/octowright-frontend/static/styles.css src/octowright/server/frontend tests/test_browser_pool_branches.py tests/test_server_browser_lifecycle_tools.py tests/test_http_server.py tests/test_status_tool.py
git commit -m "feat(dashboard): show browser operation state"
```

### Task 13: Prove transport, idempotency, and cross-session failure containment

**Files:**
- Modify: `tests/test_operation_gate_integration.py`
- Modify: `tests/test_idempotency_cache.py`
- Modify: `tests/test_progress_heartbeat.py`
- Modify: `tests/test_server_browser_input_tools.py`
- Modify: `tests/test_pool_disconnect.py`

- [ ] **Step 1: Write a failing idempotent-producer test**

Exercise the real `_idempotent_dispatch` wrapper with one idempotency key. Cancel the first request waiter while its detached producer is queued behind a held gate, resend the same key, and assert one producer/ticket and one browser side effect:

```python
@pytest.mark.asyncio
async def test_idempotent_reconnect_reuses_same_gate_ticket(idempotent_tool: IdempotentGateHarness) -> None:
    async with idempotent_tool.session.operation("owner"):
        first = asyncio.create_task(idempotent_tool.call(key="same-key"))
        await wait_for_queue_depth(idempotent_tool.session._operation_gate, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(idempotent_tool.call(key="same-key"))
        assert idempotent_tool.session.operation_snapshot()["queue_depth"] == 1
    assert await second == {"ok": True}
    idempotent_tool.session.page.click.assert_awaited_once()
```

- [ ] **Step 2: Write failure-containment tests at the tool boundary**

Hold browser A beyond its short test queue timeout, call a real browser tool on A, and call another on B. Assert A returns/raises `SessionBusyTimeoutError` through the existing MCP tool error path, B succeeds, both the MCP server registry and event loop remain alive, A remains usable after release, and no browser is automatically closed/retried.

Also test:

- an admitted arbitrary Playwright exception releases A and does not mark it broken;
- a gate invariant error marks only A broken, while B and tool registration remain usable;
- external closure fails A's queued calls but does not request driver reset or daemon restart;
- an expired/cancelled waiter cannot execute later and appends no recorder row;
- queue time does not reduce a later Playwright `timeout_ms` argument;
- an accepted close continues after request cancellation.

- [ ] **Step 3: Run reliability tests and verify RED where integration is incomplete**

Run: `uv run --active pytest tests/test_operation_gate_integration.py tests/test_idempotency_cache.py tests/test_progress_heartbeat.py tests/test_server_browser_input_tools.py tests/test_pool_disconnect.py -q --no-cov`

Expected: any remaining producer duplication, server-level error translation, or cleanup gap fails explicitly.

- [ ] **Step 4: Fix only the demonstrated integration gaps**

The implementation rules are fixed:

```python
# Request cancellation isolates the waiter, not an existing idempotency producer.
return await asyncio.shield(producer)

# Gate errors propagate as one tool error. They never call these recovery paths.
assert not driver_health.is_driver_dead_error(gate_error)
assert pool.driver_restart_count() == prior_restart_count

# No operation body means no behavioral record.
assert recorder.action_count == before_action_count
```

Do not add retry loops, server restart hooks, gate-error swallowing, or a gate-specific JSON response that diverges from the current MCP exception boundary.

- [ ] **Step 5: Test queue-timeout versus heartbeat configuration**

Pin the default relationship `300 < HEARTBEAT_MAX_SECONDS (600)`. Exercise the comparison helper used by `server/_state.py`: a server pool timeout at/above the effective heartbeat ceiling emits one warning but is accepted, while a smaller value emits no warning. Do not make ordinary library `BrowserPool` construction import the server package merely to warn. The heartbeat remains the outer MCP wrapper; a waiting call continues to produce progress until admitted, rejected, or the existing heartbeat ceiling is reached.

- [ ] **Step 6: Run reliability tests and verify GREEN**

Run: `uv run --active pytest tests/test_operation_gate_integration.py tests/test_idempotency_cache.py tests/test_progress_heartbeat.py tests/test_server_browser_input_tools.py tests/test_pool_disconnect.py -q --no-cov`

Expected: all pass with no server disconnect, driver reset, retry, late side effect, or pending task.

- [ ] **Step 7: Commit failure-containment coverage**

```bash
git add tests/test_operation_gate_integration.py tests/test_idempotency_cache.py tests/test_progress_heartbeat.py tests/test_server_browser_input_tools.py tests/test_pool_disconnect.py
git commit -m "test(stability): prove operation gate failure isolation"
```

### Task 14: Document configuration, metrics, embedder migration, and operational semantics

**Files:**
- Modify: `README.md`
- Modify: `docs/troubleshooting.md`
- Modify: `CHANGELOG.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `scripts/check_telemetry_docs.py`
- Modify: `tests/test_check_telemetry_docs.py`

- [ ] **Step 1: Write failing documentation-ratchet tests for gauges**

Extend the metric extractor suffixes to include `_depth`, then pin every new instrument:

```python
def test_extracts_operation_gate_metrics() -> None:
    assert {
        "octowright_operation_queue_wait_seconds",
        "octowright_operation_active_duration_seconds",
        "octowright_operation_queue_timeout_total",
        "octowright_operation_rejected_total",
        "octowright_operation_queue_depth",
    } <= checker.metric_names()
```

Run: `uv run --active pytest tests/test_check_telemetry_docs.py -q --no-cov`

Expected: `_depth` is not extracted and/or AGENTS.md does not document the instruments.

- [ ] **Step 2: Update operator and embedder documentation**

Document these exact contracts:

- `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS`, default `300`, positive finite seconds, with `BrowserPool(operation_queue_timeout_seconds=...)` precedence over env.
- Queue wait is separate from Playwright action/navigation/expect timeouts; no retries are added.
- The default stays below the 600-second heartbeat ceiling; at/above-ceiling configurations warn because transport visibility may expire first.
- Same-session calls are FIFO; different sessions remain parallel; macros/sequences/artifact runs and closing compound helpers are atomic on their source browser.
- Normal close drains already queued work, rejects later work, and remains durable after caller cancellation.
- External browser/page closure can still interrupt an active operation; queued work receives a session-closed error.
- `BrowserSession.list_pages()`, `list_frames()`, and `set_dialog_policy()` are now async and must be awaited by embedders; use `BrowserPool.close()` rather than raw Playwright teardown.
- Gate errors affect only the tool/session and never mean the MCP transport should be restarted.
- The snapshot contains only fixed operation identifiers and timing/depth state; no selector, URL, credential, argument, or task identity.
- Telemetry attributes are bounded and omit instance IDs; current queue depth is an aggregate per browser kind.
- Gate scheduling never appears in JSONL, macros, replay normalization, or exports.
- Accessible drag/drop, control leases/Take control, terminal gating, and the repo-wide DRY audit remain separate future work.

Add a concise unreleased changelog entry rather than claiming a release version.

- [ ] **Step 3: Document all five metric names in canonical agent guidance**

Add one “Browser session operation gate” paragraph to `AGENTS.md` containing the five literal metric names, their bounded attributes, timeout semantics, close behavior, and async migration. Copy `AGENTS.md` to `CLAUDE.md` byte-for-byte using `cp AGENTS.md CLAUDE.md`; do not edit the compatibility copy independently.

- [ ] **Step 4: Run documentation ratchets**

Run: `uv run --active pytest tests/test_check_telemetry_docs.py -q --no-cov`

Run: `uv run --active python scripts/check_telemetry_docs.py`

Run: `uv run --active python scripts/check_agent_docs_sync.py`

Run: `uv run --active codespell README.md docs/troubleshooting.md CHANGELOG.md AGENTS.md CLAUDE.md`

Expected: metrics are all discovered/documented, agent docs are identical, and spelling passes.

- [ ] **Step 5: Commit documentation and ratchets**

```bash
git add README.md docs/troubleshooting.md CHANGELOG.md AGENTS.md CLAUDE.md scripts/check_telemetry_docs.py tests/test_check_telemetry_docs.py
git commit -m "docs: explain browser operation serialization"
```

### Task 15: Prove the gate with real Chromium and extend the chaos suite

**Files:**
- Create: `tests/test_operation_gate_live.py`
- Modify: `tests/test_stability_chaos_live.py`

- [ ] **Step 1: Write the focused local-playground test**

Mark the new module with both `pytest.mark.live_browser` and `pytest.mark.integration_local`. Use the existing `integration_local_base_url`/`playground_server` fixtures, an ephemeral `BrowserPool(operation_queue_timeout_seconds=0.25)`, and two headless Chromium sessions. Write a two-action macro with `macros.storage.write_macro` into a monkeypatched temporary `MACROS_DIR`.

The single focused test must prove all four acceptance behaviors against real Playwright:

1. Start `run_macro(session_a, "gate-order", slowmo_ms=40)` with two `evaluate` actions that append `macro-1` and `macro-2` to `window.__gate_order`; after the snapshot reports `active_operation == "macro_run"`, queue a direct `session_a.evaluate` appending `manual`. Assert the final array is `['macro-1', 'macro-2', 'manual']`.
2. Hold session A with a one-second in-page promise, wait until its gate reports `browser_evaluate`, then await a real evaluate on session B with a generous two-second safety timeout and assert A is still pending when B returns. This relative ordering proves the gate is per-session without a flaky machine-speed threshold.
3. While A is held, make another evaluate exceed the 250 ms ordinary queue timeout and assert `SessionBusyTimeoutError`; after the holder finishes, call the real `server.browser.inspect.browser_evaluate` wrapper (with its module pool monkeypatched to this live pool) on A and B and assert both still work. This proves a rejection does not poison a browser, driver, event loop, or tool registry.
4. Hold A again, queue one earlier evaluate, start `pool.close(A)`, wait for `state == "closing"`, and assert a later evaluate raises `SessionClosingError`. Assert the earlier result completes before close, close returns `closed=True`, A leaves both pool registries, and B remains usable.

Use a bounded async polling helper around `operation_snapshot()`; do not infer ordering from arbitrary sleeps. Keep the in-page promise durations only for the real-browser hold itself. Always `await pool.shutdown()` in `finally`.

- [ ] **Step 2: Run the new live test and verify it fails before final wiring**

Run: `uv run --active pytest tests/test_operation_gate_live.py -m "live_browser and integration_local" -v --no-cov`

Expected before the complete implementation: import/behavior assertions fail because operation-gate state and serialization do not yet exist. If Chromium is not installed, the existing live-test convention may skip with an explicit engine-unavailable reason; it must not silently xfail a behavioral failure.

- [ ] **Step 3: Add the external-close chaos regression**

Extend `tests/test_stability_chaos_live.py` with one real-browser test that launches two Chromium sessions, holds an evaluate on A, queues and cancels one waiter, queues a second waiter, and calls raw `await session_a.page.close()` to simulate an external last-page close. Assert:

- the canceled waiter never executes;
- the surviving queued waiter receives `SessionClosedError`;
- the active real Playwright call receives its normal page/context-closed failure rather than a fabricated gate error;
- A's gate is `closed`, A is absent from `_sessions`, and its durable external teardown eventually leaves no `_closing_sessions` entry;
- B performs a real evaluate successfully and `pool.driver_restart_count()` is unchanged.

This test must use the current `_skip_if_no_engine` helper and `assert_pool_consistent`; it must not restart Playwright or the daemon as part of recovery.

- [ ] **Step 4: Run the focused live and chaos tests**

Run: `uv run --active pytest tests/test_operation_gate_live.py -m "live_browser and integration_local" -v --no-cov`

Run: `uv run --active pytest tests/test_stability_chaos_live.py -m live_browser -v --no-cov`

Expected: both commands pass when Chromium is available, with no pending-task warnings, driver restart, or cross-session blockage. Engine-unavailable skips are acceptable only when they contain the existing explicit reason.

- [ ] **Step 5: Commit live stability coverage**

```bash
git add tests/test_operation_gate_live.py tests/test_stability_chaos_live.py
git commit -m "test(stability): exercise operation gate in Chromium"
```

### Task 16: Run the complete acceptance matrix and verify scope

**Files:**
- Verify only; modify the smallest owning task's files if a command exposes a defect

- [ ] **Step 1: Run the focused backend regression matrix**

Run: `uv run --active pytest tests/session/test_operation_gate.py tests/test_operation_gate_integration.py tests/test_operation_gate_architecture.py tests/test_operation_gate_tool_schemas.py tests/test_idempotency_cache.py tests/test_progress_heartbeat.py tests/test_pool_disconnect.py tests/test_crash_recovery.py tests/test_macros.py tests/test_macro_artifacts.py tests/test_server_browser_input_tools.py tests/test_server_browser_inspect_tools.py tests/test_http_server.py -q --no-cov`

Expected: all pass with no unhandled task exception, destroyed-pending-task warning, closing-registry residue, or schema drift.

- [ ] **Step 2: Run frontend tests and a production build**

Run: `cd packages/octowright-frontend && npm test -- --run src/session-table.test.ts`

Run: `cd packages/octowright-frontend && npm run build`

Expected: Vitest and TypeScript/build pass; generated assets are current.

- [ ] **Step 3: Run repository-wide non-live verification**

Run: `make test`

Run: `make typecheck`

Run: `make lint`

Expected: the full non-live suite, static types, Ruff/format, Bandit, codespell, SPDX, LOC, vulture, xenon, secrets, telemetry docs, agent-doc sync, and the new operation-gate architecture scan all pass.

- [ ] **Step 4: Run applicable live verification**

Run: `uv run --active pytest tests/test_operation_gate_live.py -m "live_browser and integration_local" -v --no-cov`

Run: `uv run --active pytest tests/test_stability_chaos_live.py -m live_browser -v --no-cov`

Expected: pass when Chromium is installed; otherwise report the explicit skips in the handoff. Do not add Firefox/WebKit copies of the scheduling assertions—the existing engine matrix remains the compatibility signal.

- [ ] **Step 5: Audit invariants and project scope**

Run: `uv run --active python scripts/check_operation_gate_architecture.py`

Run: `uv run --active python scripts/check_max_loc.py`

Run: `uv run --active python scripts/check_agent_docs_sync.py`

Run: `git diff --check`

Run: `git status --short`

Confirm from the final diff that:

- every new operation name is a fixed literal and no selector/URL/argument is logged or used as a metric attribute;
- no gate scheduling action was added to recorder JSONL, macro replay maps, recording import, or exporters;
- the existing mouse `browser_drag` implementation/schema remains behaviorally unchanged;
- no accessible drag/drop, control lease/Take control, terminal gate, automatic retry, or repository-wide DRY change entered this branch;
- `_closing_sessions` and all durable tasks are emptied by tests, and a gate failure has no daemon/driver restart hook.

If verification requires a fix, return to the task that owns the defect, add the smallest regression, rerun that task's focused command, commit the fix with a scoped message, then repeat this complete matrix. Do not declare completion from a partial rerun.
