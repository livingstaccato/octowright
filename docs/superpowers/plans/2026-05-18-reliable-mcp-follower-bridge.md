# Reliable MCP Follower Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Octowright's MCP stdio follower alive across leader stream failures, fail broken calls quickly, and make later calls reconnect without restarting the LLM client.

**Architecture:** Replace the raw bidirectional pump in `proxy_bridge.py` with a supervised local-stdio-to-remote-HTTP bridge. The local stdio endpoint remains stable; the remote HTTP-MCP session is disposable, reinitialized from a cached `initialize` request, and reset on timeout or stream failure.

**Tech Stack:** Python 3.11, anyio, httpx, MCP Python SDK (`SessionMessage`, `JSONRPCMessage`, `JSONRPCRequest`, `JSONRPCError`, `ErrorData`), pytest/anyio, Click CLI.

---

## File Structure

- Create `src/octowright/bridge_state.py`
  - Bounded state snapshots for follower bridge diagnostics.
  - Pure sync file I/O with safe best-effort writes.

- Create `src/octowright/proxy_supervisor.py`
  - The supervised bridge implementation.
  - Owns message classification, in-flight request tracking, remote reconnect, initialize replay, and bridge JSON-RPC errors.

- Modify `src/octowright/proxy_bridge.py`
  - Keep `_pump` and `_heartbeat` for compatibility tests.
  - Change `run_proxy()` to delegate to `proxy_supervisor.run_supervised_proxy()`.

- Modify `src/octowright/defaults.py`
  - Add bridge timeout/backoff/env defaults.
  - Add bridge state file path default.

- Modify `src/octowright/server/meta.py`
  - Include bridge diagnostics in `octowright_status`.

- Modify `tests/test_proxy_bridge.py`
  - Keep existing pump/heartbeat tests.
  - Add a thin test that `run_proxy()` delegates to the supervisor with the same arguments.

- Create `tests/test_proxy_supervisor.py`
  - Unit tests for request classification, bridge errors, in-flight deadline handling, reconnect behavior, and initialize replay using fake memory streams.

- Create `tests/test_bridge_state.py`
  - Tests for bounded diagnostics persistence.

- Create `scripts/bridge_reconnect_smoke.py`
  - Opt-in smoke proof using a fresh stdio MCP client.

---

### Task 1: Add Bridge Defaults

**Files:**
- Modify: `src/octowright/defaults.py`
- Test: no dedicated test; covered by imports and later supervisor tests.

- [ ] **Step 1: Add default constants**

In `src/octowright/defaults.py`, after `BROWSER_LAUNCH_TIMEOUT_SECONDS`, add:

```python
# Follower bridge protection. These defaults are intentionally below common MCP
# client tool-call deadlines so bridge failures return explicit JSON-RPC errors
# instead of leaving the host to time out at ~120s.
BRIDGE_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_REQUEST_TIMEOUT_SECONDS", "20"))
BRIDGE_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_CONNECT_TIMEOUT_SECONDS", "10"))
BRIDGE_RECONNECT_MAX_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_RECONNECT_MAX_SECONDS", "5"))
BRIDGE_STATE_PATH = Path(os.environ.get("OCTOWRIGHT_BRIDGE_STATE", str(_STATE_DIR / "bridge-state.json")))
```

- [ ] **Step 2: Run import check**

Run:

```bash
uv run --active python - <<'PY'
from octowright import defaults
print(defaults.BRIDGE_REQUEST_TIMEOUT_SECONDS)
print(defaults.BRIDGE_CONNECT_TIMEOUT_SECONDS)
print(defaults.BRIDGE_RECONNECT_MAX_SECONDS)
print(defaults.BRIDGE_STATE_PATH)
PY
```

Expected: prints `20.0`, `10.0`, `5.0`, and an XDG state path ending in `bridge-state.json`.

- [ ] **Step 3: Commit**

```bash
git add src/octowright/defaults.py
git commit -m "feat: add bridge reliability defaults"
```

---

### Task 2: Add Bounded Bridge Diagnostics State

**Files:**
- Create: `src/octowright/bridge_state.py`
- Create: `tests/test_bridge_state.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_bridge_state.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

from octowright import bridge_state


def test_record_snapshot_writes_latest_by_pid(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"

    bridge_state.record_snapshot(
        path=path,
        follower_pid=123,
        remote_url="http://127.0.0.1:8765/mcp/",
        remote_session_id="sid-1",
        last_error=None,
        in_flight=0,
        reconnect_attempts=1,
        request_timeouts=0,
    )

    data = json.loads(path.read_text())
    assert data["followers"]["123"]["remote_url"] == "http://127.0.0.1:8765/mcp/"
    assert data["followers"]["123"]["remote_session_id"] == "sid-1"
    assert data["followers"]["123"]["in_flight"] == 0
    assert data["events"][-1]["event"] == "snapshot"


def test_record_snapshot_bounds_events(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"

    for i in range(12):
        bridge_state.record_snapshot(
            path=path,
            follower_pid=123,
            remote_url=f"http://127.0.0.1:{8765 + i}/mcp/",
            remote_session_id=f"sid-{i}",
            last_error=f"err-{i}",
            in_flight=i,
            reconnect_attempts=i,
            request_timeouts=i,
            max_events=5,
        )

    data = json.loads(path.read_text())
    assert len(data["events"]) == 5
    assert data["events"][0]["last_error"] == "err-7"
    assert data["events"][-1]["last_error"] == "err-11"
    assert data["followers"]["123"]["remote_session_id"] == "sid-11"


def test_read_state_returns_empty_shape_for_missing_file(tmp_path: Path) -> None:
    data = bridge_state.read_state(tmp_path / "missing.json")
    assert data == {"followers": {}, "events": []}


def test_read_state_recovers_from_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"
    path.write_text("{not json")
    data = bridge_state.read_state(path)
    assert data == {"followers": {}, "events": []}
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run --active pytest tests/test_bridge_state.py -q --no-cov
```

Expected: FAIL with `ImportError: cannot import name 'bridge_state'`.

- [ ] **Step 3: Implement diagnostics state**

Create `src/octowright/bridge_state.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _empty_state() -> dict[str, Any]:
    return {"followers": {}, "events": []}


def read_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    followers = raw.get("followers")
    events = raw.get("events")
    if not isinstance(followers, dict) or not isinstance(events, list):
        return _empty_state()
    return {"followers": followers, "events": events}


def record_snapshot(
    *,
    path: Path,
    follower_pid: int,
    remote_url: str | None,
    remote_session_id: str | None,
    last_error: str | None,
    in_flight: int,
    reconnect_attempts: int,
    request_timeouts: int,
    max_events: int = 50,
) -> None:
    snapshot = {
        "ts": time.time(),
        "event": "snapshot",
        "follower_pid": follower_pid,
        "remote_url": remote_url,
        "remote_session_id": remote_session_id,
        "last_error": last_error,
        "in_flight": in_flight,
        "reconnect_attempts": reconnect_attempts,
        "request_timeouts": request_timeouts,
    }
    state = read_state(path)
    state["followers"][str(follower_pid)] = snapshot
    state["events"].append(snapshot)
    state["events"] = state["events"][-max_events:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{follower_pid}.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run --active pytest tests/test_bridge_state.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/bridge_state.py tests/test_bridge_state.py
git commit -m "feat: add bridge diagnostics state"
```

---

### Task 3: Add Message Helpers And Bridge Error Construction

**Files:**
- Create: `src/octowright/proxy_supervisor.py`
- Create: `tests/test_proxy_supervisor.py`

- [ ] **Step 1: Write failing message-helper tests**

Create `tests/test_proxy_supervisor.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

from octowright import proxy_supervisor as supervisor


def _request(method: str, request_id: str = "r1") -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params={"x": 1})
        )
    )


def _notification(method: str) -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(root=JSONRPCNotification(jsonrpc="2.0", method=method, params={"x": 1}))
    )


def _response(request_id: str = "r1") -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=request_id, result={"ok": True}))
    )


def test_request_id_and_method_for_request() -> None:
    msg = _request("tools/call", "abc")
    assert supervisor.message_request_id(msg) == "abc"
    assert supervisor.message_method(msg) == "tools/call"
    assert supervisor.is_request(msg) is True
    assert supervisor.is_response(msg) is False


def test_request_id_for_response() -> None:
    msg = _response("abc")
    assert supervisor.message_request_id(msg) == "abc"
    assert supervisor.message_method(msg) is None
    assert supervisor.is_request(msg) is False
    assert supervisor.is_response(msg) is True


def test_notification_has_method_but_no_request_id() -> None:
    msg = _notification("notifications/initialized")
    assert supervisor.message_request_id(msg) is None
    assert supervisor.message_method(msg) == "notifications/initialized"
    assert supervisor.is_request(msg) is False


def test_bridge_error_message_shape() -> None:
    error = supervisor.bridge_error("abc", "remote request timed out")
    root = error.message.root
    assert isinstance(root, JSONRPCError)
    assert root.id == "abc"
    assert root.error.code == -32000
    assert root.error.message == "Octowright bridge error: remote request timed out"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run --active pytest tests/test_proxy_supervisor.py -q --no-cov
```

Expected: FAIL with `ImportError: cannot import name 'proxy_supervisor'`.

- [ ] **Step 3: Implement message helpers**

Create `src/octowright/proxy_supervisor.py` with this initial content:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

BRIDGE_ERROR_CODE = -32000
BRIDGE_ERROR_PREFIX = "Octowright bridge error:"


def message_root(message: SessionMessage) -> Any:
    return message.message.root


def message_request_id(message: SessionMessage) -> str | int | None:
    root = message_root(message)
    if isinstance(root, (JSONRPCRequest, JSONRPCResponse, JSONRPCError)):
        return root.id
    return None


def message_method(message: SessionMessage) -> str | None:
    root = message_root(message)
    if isinstance(root, (JSONRPCRequest, JSONRPCNotification)):
        return root.method
    return None


def is_request(message: SessionMessage) -> bool:
    return isinstance(message_root(message), JSONRPCRequest)


def is_response(message: SessionMessage) -> bool:
    return isinstance(message_root(message), (JSONRPCResponse, JSONRPCError))


def bridge_error(request_id: str | int, reason: str) -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCError(
                jsonrpc="2.0",
                id=request_id,
                error=ErrorData(
                    code=BRIDGE_ERROR_CODE,
                    message=f"{BRIDGE_ERROR_PREFIX} {reason}",
                ),
            )
        )
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run --active pytest tests/test_proxy_supervisor.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/proxy_supervisor.py tests/test_proxy_supervisor.py
git commit -m "feat: add mcp bridge message helpers"
```

---

### Task 4: Implement In-Flight Deadline Handling

**Files:**
- Modify: `src/octowright/proxy_supervisor.py`
- Modify: `tests/test_proxy_supervisor.py`

- [ ] **Step 1: Add failing in-flight tests**

Append to `tests/test_proxy_supervisor.py`:

```python
import anyio
import pytest


@pytest.mark.anyio
async def test_request_timeout_returns_bridge_error() -> None:
    local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=0.05,
    )

    await local_send.send(_request("tools/call", "timeout-id"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.forward_local_to_remote, remote_send)
        tg.start_soon(supervisor_obj.watch_deadlines)
        forwarded = await remote_recv.receive()
        assert supervisor.message_request_id(forwarded) == "timeout-id"
        error = await outgoing_recv.receive()
        root = error.message.root
        assert isinstance(root, JSONRPCError)
        assert root.id == "timeout-id"
        assert "timed out" in root.error.message
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_remote_response_clears_in_flight() -> None:
    local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_write_send, remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    await local_send.send(_request("tools/call", "ok-id"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.forward_local_to_remote, remote_write_send)
        forwarded = await remote_write_recv.receive()
        assert supervisor.message_request_id(forwarded) == "ok-id"
        await supervisor_obj.forward_remote_message(_response("ok-id"))
        response = await outgoing_recv.receive()
        assert supervisor.message_request_id(response) == "ok-id"
        assert supervisor_obj.in_flight_count == 0
        tg.cancel_scope.cancel()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run --active pytest tests/test_proxy_supervisor.py::test_request_timeout_returns_bridge_error tests/test_proxy_supervisor.py::test_remote_response_clears_in_flight -q --no-cov
```

Expected: FAIL with `AttributeError: module 'octowright.proxy_supervisor' has no attribute 'BridgeSupervisor'`.

- [ ] **Step 3: Implement in-flight tracking**

Append this implementation to `src/octowright/proxy_supervisor.py`:

```python
import time
from dataclasses import dataclass

import anyio


@dataclass
class InFlightRequest:
    request_id: str | int
    method: str | None
    started_at: float
    deadline: float


class BridgeSupervisor:
    def __init__(
        self,
        *,
        local_read: Any,
        local_write: Any,
        request_timeout_seconds: float,
    ) -> None:
        self.local_read = local_read
        self.local_write = local_write
        self.request_timeout_seconds = request_timeout_seconds
        self._in_flight: dict[str | int, InFlightRequest] = {}
        self.request_timeouts = 0
        self.last_error: str | None = None

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    async def forward_local_to_remote(self, remote_write: Any) -> None:
        async for message in self.local_read:
            request_id = message_request_id(message)
            if is_request(message) and request_id is not None:
                now = time.monotonic()
                self._in_flight[request_id] = InFlightRequest(
                    request_id=request_id,
                    method=message_method(message),
                    started_at=now,
                    deadline=now + self.request_timeout_seconds,
                )
            await remote_write.send(message)

    async def forward_remote_message(self, message: SessionMessage) -> None:
        request_id = message_request_id(message)
        if request_id is not None:
            self._in_flight.pop(request_id, None)
        await self.local_write.send(message)

    async def watch_deadlines(self, interval: float = 0.01) -> None:
        while True:
            await anyio.sleep(interval)
            now = time.monotonic()
            expired = [item for item in self._in_flight.values() if item.deadline <= now]
            for item in expired:
                self._in_flight.pop(item.request_id, None)
                self.request_timeouts += 1
                self.last_error = f"request {item.request_id!r} timed out while waiting for leader response"
                await self.local_write.send(bridge_error(item.request_id, self.last_error))

    async def fail_all_in_flight(self, reason: str) -> None:
        pending = list(self._in_flight.values())
        self._in_flight.clear()
        self.last_error = reason
        for item in pending:
            await self.local_write.send(bridge_error(item.request_id, reason))
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run --active pytest tests/test_proxy_supervisor.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/proxy_supervisor.py tests/test_proxy_supervisor.py
git commit -m "feat: track bridge in-flight request deadlines"
```

---

### Task 5: Add Remote Session Reconnect And Initialize Replay

**Files:**
- Modify: `src/octowright/proxy_supervisor.py`
- Modify: `tests/test_proxy_supervisor.py`

- [ ] **Step 1: Add failing reconnect tests**

Append to `tests/test_proxy_supervisor.py`:

```python
class FakeRemoteConnector:
    def __init__(self) -> None:
        self.sessions: list[tuple[Any, Any]] = []
        self.connect_count = 0

    async def connect(self) -> tuple[Any, Any, str | None]:
        self.connect_count += 1
        client_to_remote_send, client_to_remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_to_client_send, remote_to_client_recv = anyio.create_memory_object_stream[SessionMessage](10)
        self.sessions.append((client_to_remote_recv, remote_to_client_send))
        return remote_to_client_recv, client_to_remote_send, f"session-{self.connect_count}"


@pytest.mark.anyio
async def test_initialize_is_replayed_after_reconnect() -> None:
    local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    connector = FakeRemoteConnector()
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )

    await local_send.send(_request("initialize", "init-1"))
    remote_read, remote_write, _sid = await connector.connect()
    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.forward_local_to_remote, remote_write)
        first_remote_recv, first_remote_send = connector.sessions[0]
        init_msg = await first_remote_recv.receive()
        assert supervisor.message_method(init_msg) == "initialize"
        await supervisor_obj.forward_remote_message(_response("init-1"))
        assert supervisor.message_request_id(await local_out_recv.receive()) == "init-1"
        await supervisor_obj.replay_initialize(remote_write)
        replayed = await first_remote_recv.receive()
        assert supervisor.message_method(replayed) == "initialize"
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_remote_failure_fails_in_flight() -> None:
    local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_write_send, remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )

    await local_send.send(_request("tools/call", "lost-id"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.forward_local_to_remote, remote_write_send)
        assert supervisor.message_request_id(await remote_write_recv.receive()) == "lost-id"
        await supervisor_obj.fail_all_in_flight("remote leader stream closed")
        error = await local_out_recv.receive()
        root = error.message.root
        assert isinstance(root, JSONRPCError)
        assert root.id == "lost-id"
        assert "remote leader stream closed" in root.error.message
        tg.cancel_scope.cancel()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run --active pytest tests/test_proxy_supervisor.py::test_initialize_is_replayed_after_reconnect tests/test_proxy_supervisor.py::test_remote_failure_fails_in_flight -q --no-cov
```

Expected: first test FAILS because `replay_initialize` is missing.

- [ ] **Step 3: Implement initialize caching and replay**

Modify `BridgeSupervisor` in `src/octowright/proxy_supervisor.py`:

```python
class BridgeSupervisor:
    def __init__(
        self,
        *,
        local_read: Any,
        local_write: Any,
        request_timeout_seconds: float,
    ) -> None:
        self.local_read = local_read
        self.local_write = local_write
        self.request_timeout_seconds = request_timeout_seconds
        self._in_flight: dict[str | int, InFlightRequest] = {}
        self._initialize_message: SessionMessage | None = None
        self.request_timeouts = 0
        self.last_error: str | None = None
        self.remote_session_id: str | None = None
        self.reconnect_attempts = 0

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    async def forward_local_to_remote(self, remote_write: Any) -> None:
        async for message in self.local_read:
            request_id = message_request_id(message)
            if is_request(message) and message_method(message) == "initialize":
                self._initialize_message = message
            if is_request(message) and request_id is not None:
                now = time.monotonic()
                self._in_flight[request_id] = InFlightRequest(
                    request_id=request_id,
                    method=message_method(message),
                    started_at=now,
                    deadline=now + self.request_timeout_seconds,
                )
            await remote_write.send(message)

    async def replay_initialize(self, remote_write: Any) -> None:
        if self._initialize_message is not None:
            await remote_write.send(self._initialize_message)
```

Keep the existing `forward_remote_message`, `watch_deadlines`, and `fail_all_in_flight` methods unchanged.

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run --active pytest tests/test_proxy_supervisor.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/proxy_supervisor.py tests/test_proxy_supervisor.py
git commit -m "feat: replay bridge initialize after reconnect"
```

---

### Task 6: Implement Supervised `run_supervised_proxy`

**Files:**
- Modify: `src/octowright/proxy_supervisor.py`
- Modify: `src/octowright/proxy_bridge.py`
- Modify: `tests/test_proxy_bridge.py`

- [ ] **Step 1: Add failing delegation test**

Append to `tests/test_proxy_bridge.py`:

```python
@pytest.mark.anyio
async def test_run_proxy_delegates_to_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_run_supervised_proxy(**kwargs: object) -> None:
        calls.append(dict(kwargs))

    monkeypatch.setattr(_bridge, "run_supervised_proxy", fake_run_supervised_proxy)

    await _bridge.run_proxy(
        "http://leader/mcp/",
        health_url="http://leader/api/health",
        heartbeat_interval=3.0,
        heartbeat_max_failures=7,
    )

    assert calls == [
        {
            "leader_mcp_url": "http://leader/mcp/",
            "health_url": "http://leader/api/health",
            "heartbeat_interval": 3.0,
            "heartbeat_max_failures": 7,
        }
    ]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run --active pytest tests/test_proxy_bridge.py::test_run_proxy_delegates_to_supervisor -q --no-cov
```

Expected: FAIL because `proxy_bridge.run_proxy` still opens raw streams directly.

- [ ] **Step 3: Add supervised proxy entry point**

Append this minimal production entry point to `src/octowright/proxy_supervisor.py`:

```python
from collections.abc import Callable

from mcp.client.streamable_http import streamablehttp_client
from mcp.server.stdio import stdio_server

from octowright import bridge_state, singleton
from octowright.defaults import (
    BRIDGE_CONNECT_TIMEOUT_SECONDS,
    BRIDGE_RECONNECT_MAX_SECONDS,
    BRIDGE_REQUEST_TIMEOUT_SECONDS,
    BRIDGE_STATE_PATH,
)


def resolve_leader_url(fallback_url: str) -> str:
    info = singleton.read_lock()
    if info is not None and not singleton.is_stale(info):
        return info.mcp_url
    return fallback_url


async def run_supervised_proxy(
    *,
    leader_mcp_url: str,
    health_url: str | None = None,
    heartbeat_interval: float = 10.0,
    heartbeat_max_failures: int = 3,
) -> None:
    async with stdio_server() as (local_read, local_write):
        supervisor_obj = BridgeSupervisor(
            local_read=local_read,
            local_write=local_write,
            request_timeout_seconds=BRIDGE_REQUEST_TIMEOUT_SECONDS,
        )
        remote_url = resolve_leader_url(leader_mcp_url)
        async with (
            streamablehttp_client(remote_url) as (remote_read, remote_write, get_sid),
            anyio.create_task_group() as tg,
        ):
            try:
                supervisor_obj.remote_session_id = get_sid()
            except Exception:
                supervisor_obj.remote_session_id = None
            bridge_state.record_snapshot(
                path=BRIDGE_STATE_PATH,
                follower_pid=__import__("os").getpid(),
                remote_url=remote_url,
                remote_session_id=supervisor_obj.remote_session_id,
                last_error=None,
                in_flight=supervisor_obj.in_flight_count,
                reconnect_attempts=supervisor_obj.reconnect_attempts,
                request_timeouts=supervisor_obj.request_timeouts,
            )
            tg.start_soon(supervisor_obj.forward_local_to_remote, remote_write)

            async def _remote_to_local() -> None:
                async for message in remote_read:
                    if isinstance(message, Exception):
                        raise message
                    await supervisor_obj.forward_remote_message(message)

            tg.start_soon(_remote_to_local)
            tg.start_soon(supervisor_obj.watch_deadlines)
```

This step intentionally keeps reconnect incomplete; the next task replaces the single-session body with a reconnect loop. This staged commit keeps `run_proxy` delegation and existing behavior testable.

- [ ] **Step 4: Delegate `run_proxy`**

Modify `src/octowright/proxy_bridge.py` imports:

```python
from octowright.proxy_supervisor import run_supervised_proxy
```

Replace `run_proxy()` body with:

```python
    await run_supervised_proxy(
        leader_mcp_url=leader_mcp_url,
        health_url=health_url,
        heartbeat_interval=heartbeat_interval,
        heartbeat_max_failures=heartbeat_max_failures,
    )
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
uv run --active pytest tests/test_proxy_bridge.py tests/test_proxy_bridge_branches.py tests/test_proxy_supervisor.py -q --no-cov
```

Expected: PASS. If old branch tests assert raw `streamablehttp_client` usage in `proxy_bridge`, update them to patch `run_supervised_proxy` instead of the raw stream client. Preserve `_pump` and `_heartbeat` tests.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/proxy_supervisor.py src/octowright/proxy_bridge.py tests/test_proxy_bridge.py tests/test_proxy_bridge_branches.py
git commit -m "feat: route proxy through supervised bridge"
```

---

### Task 7: Add Reconnect Loop And Fast Remote-Failure Errors

**Files:**
- Modify: `src/octowright/proxy_supervisor.py`
- Modify: `tests/test_proxy_supervisor.py`

- [ ] **Step 1: Add failing reconnect-loop unit test**

Append to `tests/test_proxy_supervisor.py`:

```python
@pytest.mark.anyio
async def test_backoff_sequence_caps_at_max() -> None:
    assert [supervisor.reconnect_delay(i, max_delay=5.0) for i in range(6)] == [
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        5.0,
    ]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run --active pytest tests/test_proxy_supervisor.py::test_backoff_sequence_caps_at_max -q --no-cov
```

Expected: FAIL because `reconnect_delay` is missing.

- [ ] **Step 3: Implement reconnect delay**

Append to `src/octowright/proxy_supervisor.py`:

```python
def reconnect_delay(attempt: int, *, max_delay: float) -> float:
    base = 0.25 * (2**attempt)
    return min(base, max_delay)
```

- [ ] **Step 4: Replace `run_supervised_proxy` with reconnect loop**

Replace the body of `run_supervised_proxy()` after `supervisor_obj = ...` with:

```python
        async with anyio.create_task_group() as local_tg:
            remote_write_box: dict[str, Any] = {}

            async def _local_forwarder() -> None:
                async for message in local_read:
                    remote_write = remote_write_box.get("remote_write")
                    request_id = message_request_id(message)
                    if remote_write is None:
                        if is_request(message) and request_id is not None:
                            await local_write.send(bridge_error(request_id, "leader session unavailable; retry"))
                        continue
                    if is_request(message) and message_method(message) == "initialize":
                        supervisor_obj._initialize_message = message
                    if is_request(message) and request_id is not None:
                        now = time.monotonic()
                        supervisor_obj._in_flight[request_id] = InFlightRequest(
                            request_id=request_id,
                            method=message_method(message),
                            started_at=now,
                            deadline=now + supervisor_obj.request_timeout_seconds,
                        )
                    await remote_write.send(message)

            async def _remote_supervisor() -> None:
                attempt = 0
                while True:
                    remote_url = resolve_leader_url(leader_mcp_url)
                    try:
                        with anyio.fail_after(BRIDGE_CONNECT_TIMEOUT_SECONDS):
                            async with streamablehttp_client(remote_url) as (remote_read, remote_write, get_sid):
                                remote_write_box["remote_write"] = remote_write
                                try:
                                    supervisor_obj.remote_session_id = get_sid()
                                except Exception:
                                    supervisor_obj.remote_session_id = None
                                supervisor_obj.reconnect_attempts = attempt
                                bridge_state.record_snapshot(
                                    path=BRIDGE_STATE_PATH,
                                    follower_pid=__import__("os").getpid(),
                                    remote_url=remote_url,
                                    remote_session_id=supervisor_obj.remote_session_id,
                                    last_error=supervisor_obj.last_error,
                                    in_flight=supervisor_obj.in_flight_count,
                                    reconnect_attempts=supervisor_obj.reconnect_attempts,
                                    request_timeouts=supervisor_obj.request_timeouts,
                                )
                                await supervisor_obj.replay_initialize(remote_write)
                                attempt = 0
                                async for message in remote_read:
                                    if isinstance(message, Exception):
                                        raise message
                                    await supervisor_obj.forward_remote_message(message)
                    except Exception as exc:
                        remote_write_box.pop("remote_write", None)
                        await supervisor_obj.fail_all_in_flight(f"remote leader session reset: {exc!r}")
                        supervisor_obj.last_error = repr(exc)
                        bridge_state.record_snapshot(
                            path=BRIDGE_STATE_PATH,
                            follower_pid=__import__("os").getpid(),
                            remote_url=remote_url,
                            remote_session_id=supervisor_obj.remote_session_id,
                            last_error=supervisor_obj.last_error,
                            in_flight=supervisor_obj.in_flight_count,
                            reconnect_attempts=attempt,
                            request_timeouts=supervisor_obj.request_timeouts,
                        )
                        await anyio.sleep(reconnect_delay(attempt, max_delay=BRIDGE_RECONNECT_MAX_SECONDS))
                        attempt += 1

            local_tg.start_soon(_local_forwarder)
            local_tg.start_soon(_remote_supervisor)
            local_tg.start_soon(supervisor_obj.watch_deadlines)
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
uv run --active pytest tests/test_proxy_supervisor.py tests/test_proxy_bridge.py tests/test_proxy_bridge_branches.py -q --no-cov
```

Expected: PASS. If a test hangs, wrap the failing test in `anyio.fail_after(2)` and fix the bridge loop so local stream closure cancels the task group.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/proxy_supervisor.py tests/test_proxy_supervisor.py
git commit -m "feat: reconnect follower bridge remote sessions"
```

---

### Task 8: Expose Bridge Diagnostics In Status

**Files:**
- Modify: `src/octowright/server/meta.py`
- Modify: `tests/test_server_meta.py` or existing meta-status test file found with `rg "octowright_status" tests`

- [ ] **Step 1: Find existing status tests**

Run:

```bash
rg -n "octowright_status|bridge" tests/test_*meta*.py tests
```

Expected: identify the test file that already covers `octowright_status`. Use that file for the next step.

- [ ] **Step 2: Add failing status test**

In the existing status test file, add:

```python
def test_octowright_status_includes_bridge_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from octowright import bridge_state
    from octowright import defaults
    from octowright.server import meta

    path = tmp_path / "bridge-state.json"
    bridge_state.record_snapshot(
        path=path,
        follower_pid=123,
        remote_url="http://127.0.0.1:8765/mcp/",
        remote_session_id="sid",
        last_error="remote reset",
        in_flight=0,
        reconnect_attempts=2,
        request_timeouts=1,
    )
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)

    status = meta.octowright_status()

    assert status["bridge"]["followers"]["123"]["last_error"] == "remote reset"
    assert status["bridge"]["followers"]["123"]["request_timeouts"] == 1
```

Add imports if missing:

```python
from pathlib import Path
import pytest
```

- [ ] **Step 3: Run test to verify failure**

Run the exact test selected in Step 1:

```bash
uv run --active pytest tests/<selected_file>.py::test_octowright_status_includes_bridge_diagnostics -q --no-cov
```

Expected: FAIL with missing `"bridge"` key.

- [ ] **Step 4: Implement status field**

In `src/octowright/server/meta.py`, inside `octowright_status()`, import bridge state:

```python
    from octowright import bridge_state as _bridge_state
```

Add this to the returned dict:

```python
        "bridge": _bridge_state.read_state(defaults.BRIDGE_STATE_PATH),
```

- [ ] **Step 5: Run status tests**

Run:

```bash
uv run --active pytest tests/<selected_file>.py::test_octowright_status_includes_bridge_diagnostics -q --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/server/meta.py tests/<selected_file>.py
git commit -m "feat: expose bridge diagnostics in status"
```

---

### Task 9: Add Real Opt-In Bridge Reconnect Smoke Script

**Files:**
- Create: `scripts/bridge_reconnect_smoke.py`
- Modify: `Makefile` if this repo keeps script commands there.

- [ ] **Step 1: Create smoke script**

Create `scripts/bridge_reconnect_smoke.py`:

```python
#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def _call_status(session: ClientSession) -> str:
    result = await session.call_tool("octowright_status", {})
    return result.content[0].text


async def main() -> None:
    params = StdioServerParameters(command=".venv/bin/octowright", args=["serve"], cwd=".")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            with anyio.fail_after(30):
                await session.initialize()
                tools = await session.list_tools()
                print(f"tools={len(tools.tools)}")
                first = await _call_status(session)
                print(f"first_status_bytes={len(first)}")
                second = await _call_status(session)
                print(f"second_status_bytes={len(second)}")


if __name__ == "__main__":
    anyio.run(main)
```

- [ ] **Step 2: Run smoke script**

Run:

```bash
uv run --active python scripts/bridge_reconnect_smoke.py
```

Expected output includes:

```text
tools=106
first_status_bytes=
second_status_bytes=
```

- [ ] **Step 3: Commit**

```bash
git add scripts/bridge_reconnect_smoke.py
git commit -m "test: add bridge reconnect smoke script"
```

---

### Task 10: Documentation And Final Verification

**Files:**
- Modify: `AGENTS.md` if the agent guidance is generated manually.
- Modify: `src/octowright/skills/using-octowright/SKILL.md` or skill source if this repo owns it.

- [ ] **Step 1: Update agent guidance**

Find the source skill/doc:

```bash
rg -n "Transport closed|octowright_status|fresh MCP" AGENTS.md src docs
```

Add this guidance to the relevant Octowright skill/doc source:

```markdown
### Transport Recovery

If an Octowright MCP call returns `Transport closed` or times out:

1. Check daemon health with `curl http://127.0.0.1:8765/api/health`.
2. If health is good, retry one Octowright MCP call. The follower bridge should now fail fast and reconnect for the next call.
3. If the same client handle still fails, run `uv run --active python scripts/bridge_reconnect_smoke.py` to distinguish a broken client handle from a broken daemon.
4. Do not run `octowright restart` unless daemon health fails or the user explicitly asks for a restart.
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run --active pytest tests/test_proxy_supervisor.py tests/test_proxy_bridge.py tests/test_proxy_bridge_branches.py tests/test_bridge_state.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 3: Run full tests and quality**

Run:

```bash
uv run --active pytest -q
make lint
```

Expected:

- pytest PASS with coverage over the configured threshold.
- `make lint` PASS.

- [ ] **Step 4: Run real smoke**

Run:

```bash
uv run --active python scripts/bridge_reconnect_smoke.py
```

Expected output includes:

```text
tools=106
first_status_bytes=
second_status_bytes=
```

- [ ] **Step 5: Commit final docs**

```bash
git add AGENTS.md src/octowright/skills docs scripts tests src
git commit -m "docs: document octowright transport recovery"
```

Use a narrower `git add` if only one doc file changed.

---

## Self-Review

Spec coverage:

- Stable local stdio process: Tasks 4, 6, 7.
- Disposable remote leader session: Tasks 6, 7.
- Initialize caching/replay: Task 5.
- Fast JSON-RPC bridge errors: Tasks 3, 4, 7.
- Reconnect to current lockfile URL: Task 6.
- Diagnostics state and status: Tasks 2, 8.
- Cross-platform constraints: Tasks avoid POSIX-only bridge core; process smoke remains a script.
- Testing levels: Tasks 2-9 cover unit, integration-like fake stream tests, and opt-in real smoke.

Placeholder scan:

- No unfinished-work markers or open-ended "add tests" steps remain.
- The plan contains concrete code for new files and exact commands.

Type consistency:

- Uses MCP SDK `SessionMessage`, `JSONRPCMessage`, `JSONRPCRequest`, `JSONRPCResponse`, `JSONRPCError`, and `ErrorData` with inspected field names.
- `BridgeSupervisor` methods referenced by tests are defined before later tasks rely on them.
