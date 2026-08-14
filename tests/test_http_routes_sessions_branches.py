# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.http.routes.sessions.

Targets endpoints / branches that the existing test_http_server.py +
test_http_server_writes.py suites don't pin:

- recording_delete (DELETE /api/sessions/{id}/recording) — 409/404/happy
  + unlink-failure swallow
- session_selector_validate — entirely uncovered
- session_close 500 / warm_close failure swallow
- session_navigate 500 path
- session_relaunch viewport non-dict fallback + video_dir → record_video flag
- session_detail live-path aria swallow + macro_intent attach swallow
- _live_summary_from_launch fallback when log_path file missing
- _resolve_live_markdown_path precedence (live attr > artifact lookup > None)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from octowright import http as _http
from octowright.browser_pool.launch_helpers import _record_launch_event
from octowright.http import state as _http_state
from octowright.http.routes import sessions as session_routes
from octowright.recorder import Recorder
from octowright.server import _state
from octowright.session.operation_gate import SessionOperationGate

# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    # LaunchOptions.from_launch_record now validates HAR path containment
    # against defaults.RECORDINGS_DIR — patch both so relaunch-from-JSONL
    # tests can write har_path values under the tmp dir.
    from octowright import defaults as _defaults

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    return rec


class _FakePool:
    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}
        self.launch_calls: list[dict[str, Any]] = []
        self.launch_result: dict[str, Any] | None = None
        self.launch_raises: BaseException | None = None
        self.close_raises: BaseException | None = None
        self.close_result: dict[str, Any] | None = None

    async def launch(self, **kwargs: Any) -> dict[str, Any]:
        self.launch_calls.append(kwargs)
        if self.launch_raises is not None:
            raise self.launch_raises
        if self.launch_result is not None:
            iid = self.launch_result["instance_id"]
            self._sessions[iid] = SimpleNamespace(instance_id=iid)
            return self.launch_result
        iid = "newinst000001"
        result = {
            "instance_id": iid,
            "kind": kwargs["kind"],
            "label": kwargs.get("label"),
            "profile": kwargs.get("profile"),
            "url": kwargs.get("url"),
            "log_path": f"/tmp/{iid}.jsonl",
        }
        self._sessions[iid] = SimpleNamespace(instance_id=iid)
        return result

    async def close(self, instance_id: str, *, force: bool = False) -> dict[str, Any]:
        if self.close_raises is not None:
            raise self.close_raises
        self._sessions.pop(instance_id, None)
        if self.close_result is not None:
            return self.close_result
        return {"closed": True, "log_path": "", "video_path": None, "trace_path": None}

    def get(self, instance_id: str) -> Any:
        return self._sessions[instance_id]

    def maybe_get(self, instance_id: str) -> Any | None:
        return self._sessions.get(instance_id)

    def has_session(self, instance_id: str) -> bool:
        return instance_id in self._sessions

    def iter_sessions(self) -> tuple[Any, ...]:
        return tuple(self._sessions.values())


class _FakeScenarioPool:
    def list_live(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    pool = _FakePool()
    spool = _FakeScenarioPool()
    monkeypatch.setattr(_state, "pool", pool)
    monkeypatch.setattr(_state, "scenario_pool", spool)
    return {"pool": pool, "scenario_pool": spool}


@pytest.fixture
def client(isolated_recordings: Path, fakes: dict[str, Any]) -> TestClient:
    return TestClient(_http.build_app())


def _write_recording(rec_dir: Path, instance_id: str, *, kind: str = "chromium") -> Path:
    name = f"20260101T000000Z-{kind}-{instance_id}.jsonl"
    p = rec_dir / name
    rows = [
        {
            "ts": "2026-01-01T00:00:00Z",
            "action": "launch",
            "kind": kind,
            "url": "https://octowright.com",
            "label": None,
            "profile": None,
        },
        {"ts": "2026-01-01T00:00:01Z", "action": "navigate", "url": "https://octowright.com/x"},
        {"ts": "2026-01-01T00:00:02Z", "action": "close"},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


# ─── recording_delete (DELETE /api/sessions/{id}/recording) ─────────────────


class TestRecordingDelete:
    def test_409_when_session_still_live(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """A live session must be closed first — 409 conflict."""
        pool: _FakePool = fakes["pool"]
        pool._sessions["liveinst0001"] = SimpleNamespace(instance_id="liveinst0001")
        r = client.delete("/api/sessions/liveinst0001/recording")
        assert r.status_code == 409
        assert "still live" in r.json()["error"]

    def test_404_when_no_recording(self, client: TestClient) -> None:
        """No JSONL on disk → 404 with 'no recording found'."""
        r = client.delete("/api/sessions/missingxxxxxx/recording")
        assert r.status_code == 404
        assert "no recording found" in r.json()["error"]

    def test_happy_path_removes_files(self, client: TestClient, isolated_recordings: Path) -> None:
        """Deletes every file whose name starts with the JSONL stem."""
        jsonl = _write_recording(isolated_recordings, "delsesn0001")
        sibling_video = isolated_recordings / f"{jsonl.stem}.webm"
        sibling_video.write_bytes(b"fake")
        sibling_screenshot = isolated_recordings / f"{jsonl.stem}.0.png"
        sibling_screenshot.write_bytes(b"fakepng")
        r = client.delete("/api/sessions/delsesn0001/recording")
        assert r.status_code == 200
        body = r.json()
        assert body["deleted"] is True
        assert body["session_id"] == "delsesn0001"
        assert body["files_removed"] == 3
        assert not jsonl.exists()
        assert not sibling_video.exists()
        assert not sibling_screenshot.exists()

    def test_only_files_with_stem_prefix_removed(self, client: TestClient, isolated_recordings: Path) -> None:
        """Other files in the recordings dir aren't touched."""
        _write_recording(isolated_recordings, "rmttest00001")
        unrelated = isolated_recordings / "20260102T000000Z-chromium-otherses01.jsonl"
        unrelated.write_text("{}")
        r = client.delete("/api/sessions/rmttest00001/recording")
        assert r.status_code == 200
        assert unrelated.exists()

    def test_unlink_failure_swallowed_continues(
        self,
        client: TestClient,
        isolated_recordings: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An OSError on one file is logged but doesn't stop deleting the rest."""
        jsonl = _write_recording(isolated_recordings, "errfile00001")
        sibling = isolated_recordings / f"{jsonl.stem}.webm"
        sibling.write_bytes(b"fake")

        original_unlink = Path.unlink
        unlinked_via_real: list[str] = []

        def maybe_fail_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
            if self.name == sibling.name:
                raise OSError("permission denied")
            unlinked_via_real.append(self.name)
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", maybe_fail_unlink)
        r = client.delete("/api/sessions/errfile00001/recording")
        # Status still 200; one file removed, one failed silently.
        assert r.status_code == 200
        assert r.json()["files_removed"] == 1
        assert jsonl.name in unlinked_via_real

    def test_publishes_dashboard_invalidation(
        self,
        client: TestClient,
        isolated_recordings: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Successful delete fires the dashboard 'sessions' invalidation."""
        _write_recording(isolated_recordings, "invalsesn001")
        publish = AsyncMock()
        monkeypatch.setattr(session_routes, "publish_dashboard_invalidation", publish)
        client.delete("/api/sessions/invalsesn001/recording")
        publish.assert_awaited_once_with("sessions")

    def test_stem_prefix_overlap_does_not_drag_neighbour(
        self,
        client: TestClient,
        isolated_recordings: Path,
    ) -> None:
        """Two recordings whose stems share a leading prefix (``abc`` vs
        ``abcde``) must not delete each other's sidecars. The legacy
        ``startswith(stem)`` check would falsely match ``...-abcde.jsonl``
        when deleting ``...-abc``."""
        short = _write_recording(isolated_recordings, "abc000000001")
        longer = _write_recording(isolated_recordings, "abc000000001x")
        # Plausible-shape sidecars next to both stems.
        short_video = isolated_recordings / f"{short.stem}.webm"
        short_video.write_bytes(b"shortvid")
        longer_video = isolated_recordings / f"{longer.stem}.webm"
        longer_video.write_bytes(b"longervid")
        longer_md = isolated_recordings / f"{longer.stem}.markdown.md"
        longer_md.write_bytes(b"longermd")

        r = client.delete("/api/sessions/abc000000001/recording")
        assert r.status_code == 200

        # Short stem's files gone.
        assert not short.exists()
        assert not short_video.exists()
        # Longer stem's files survive — the bug would unlink these.
        assert longer.exists(), "neighbour JSONL was incorrectly deleted"
        assert longer_video.exists(), "neighbour video was incorrectly deleted"
        assert longer_md.exists(), "neighbour markdown was incorrectly deleted"

    def test_unrelated_extension_in_dir_is_skipped(
        self,
        client: TestClient,
        isolated_recordings: Path,
    ) -> None:
        """Files with the right stem prefix but a suffix outside the
        sidecar allowlist (e.g. a stray ``.bak`` an operator dropped in
        the recordings dir) are left alone."""
        jsonl = _write_recording(isolated_recordings, "extguard00001")
        stray = isolated_recordings / f"{jsonl.stem}.bak"
        stray.write_bytes(b"backup")
        r = client.delete("/api/sessions/extguard00001/recording")
        assert r.status_code == 200
        assert not jsonl.exists()
        assert stray.exists(), "non-allowlisted suffix should not be unlinked"


# ─── session_selector_validate (POST /api/sessions/{id}/selector/validate) ──


def _gated_session(instance_id: str, **overrides: Any) -> SimpleNamespace:
    """A SimpleNamespace-based session fake with a real operation gate —
    ``session_selector_validate`` awaits `.operation(...)` as an async
    context manager, which a bare SimpleNamespace/MagicMock does not provide.
    """
    gate = SessionOperationGate(instance_id, "chromium", queue_timeout_seconds=30)
    return SimpleNamespace(
        instance_id=instance_id,
        operation=gate.operation,
        operation_snapshot=gate.snapshot,
        _test_operation_gate=gate,
        **overrides,
    )


def _selector_validate_request(sid: str, selector: str = ".btn-primary") -> SimpleNamespace:
    """A minimal fake Starlette ``Request`` for calling
    ``session_selector_validate`` directly (bypassing ``TestClient``, which
    doesn't mix cleanly with a background task holding a session's gate open
    in the same event loop)."""

    async def _body() -> bytes:
        return json.dumps({"selector": selector}).encode()

    return SimpleNamespace(
        path_params={"id": sid},
        headers={"content-type": "application/json"},
        body=_body,
    )


class _RaisingOperation:
    """A ``.operation()`` replacement whose entry raises immediately --
    proves a route's exception handler maps this specific gate error. A
    plain class (not ``@asynccontextmanager``) so there's no syntactically-
    required-but-unreachable ``yield`` after the ``raise`` for vulture to
    (correctly) flag as dead code."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __call__(self, _name: str, *, wait_timeout_seconds: object = None) -> _RaisingOperation:
        return self

    async def __aenter__(self) -> None:
        raise self._exc

    async def __aexit__(self, *_args: object) -> bool:
        return False


class TestSelectorValidate:
    def test_404_unknown_session(self, client: TestClient) -> None:
        """No live session with that id → 404."""
        r = client.post("/api/sessions/missingxxxxxx/selector/validate", json={"selector": "#x"})
        assert r.status_code == 404
        assert "no live session" in r.json()["error"]

    def test_400_missing_selector(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """Empty body / no selector → 400."""
        pool: _FakePool = fakes["pool"]
        pool._sessions["selsesn00001"] = SimpleNamespace(instance_id="selsesn00001")
        r = client.post("/api/sessions/selsesn00001/selector/validate", json={})
        assert r.status_code == 400
        assert "selector is required" in r.json()["error"]

    def test_400_empty_selector(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """Whitespace-only selector → 400."""
        pool: _FakePool = fakes["pool"]
        pool._sessions["selsesn00002"] = SimpleNamespace(instance_id="selsesn00002")
        r = client.post("/api/sessions/selsesn00002/selector/validate", json={"selector": "   "})
        assert r.status_code == 400

    def test_400_non_string_selector(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """Non-string selector → 400 with same error."""
        pool: _FakePool = fakes["pool"]
        pool._sessions["selsesn00003"] = SimpleNamespace(instance_id="selsesn00003")
        r = client.post("/api/sessions/selsesn00003/selector/validate", json={"selector": 42})
        assert r.status_code == 400

    def test_400_malformed_body(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """Non-JSON body returns 400 from _read_json_body."""
        pool: _FakePool = fakes["pool"]
        pool._sessions["selsesn00004"] = SimpleNamespace(instance_id="selsesn00004")
        r = client.post(
            "/api/sessions/selsesn00004/selector/validate",
            content=b"{ bad json",
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 400

    def test_happy_path_found(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """Selector matches → ok=True, found=True, count=N."""
        pool: _FakePool = fakes["pool"]
        locator = MagicMock()
        locator.count = AsyncMock(return_value=3)
        page = MagicMock()
        page.locator = MagicMock(return_value=locator)
        pool._sessions["selsesn00005"] = _gated_session("selsesn00005", page=page)
        r = client.post(
            "/api/sessions/selsesn00005/selector/validate",
            json={"selector": ".btn-primary"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"ok": True, "selector": ".btn-primary", "found": True, "count": 3}

    def test_happy_path_count_zero_returns_found_false(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """count=0 → found=False but still ok=True."""
        pool: _FakePool = fakes["pool"]
        locator = MagicMock()
        locator.count = AsyncMock(return_value=0)
        page = MagicMock()
        page.locator = MagicMock(return_value=locator)
        pool._sessions["selsesn00006"] = _gated_session("selsesn00006", page=page)
        r = client.post(
            "/api/sessions/selsesn00006/selector/validate",
            json={"selector": ".missing"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"ok": True, "selector": ".missing", "found": False, "count": 0}

    def test_400_when_locator_raises(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """page.locator(...).count() raises → 400 with error string + ok=False."""
        pool: _FakePool = fakes["pool"]
        locator = MagicMock()
        locator.count = AsyncMock(side_effect=ValueError("invalid selector"))
        page = MagicMock()
        page.locator = MagicMock(return_value=locator)
        pool._sessions["selsesn00007"] = _gated_session("selsesn00007", page=page)
        r = client.post(
            "/api/sessions/selsesn00007/selector/validate",
            json={"selector": ":bogus(["},
        )
        assert r.status_code == 400
        body = r.json()
        assert body["ok"] is False
        assert body["selector"] == ":bogus(["
        assert body["found"] is False
        assert body["count"] == 0
        assert "invalid selector" in body["error"]

    async def test_gate_busy_times_out_fast_not_300s(
        self, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dashboard_selector_validate must fail fast on a busy gate instead
        of inheriting the operation gate's 300s MCP-tool default -- a human
        watching the dashboard needs a legible failure, not a silent
        multi-minute stall. Overrides the dashboard timeout down so the real
        gate's timeout path is exercisable in a fast test."""
        import asyncio
        import time

        monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_OPERATION_TIMEOUT_SECONDS", "0.05")
        pool: _FakePool = fakes["pool"]
        page = MagicMock()
        page.locator = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=1)))
        session = _gated_session("selbusy0001", page=page)
        pool._sessions["selbusy0001"] = session

        release = asyncio.Event()

        async def _hold() -> None:
            async with session.operation("owner"):
                await release.wait()

        holder = asyncio.create_task(_hold())
        async with asyncio.timeout(1):
            while session.operation_snapshot()["active_operation"] != "owner":
                await asyncio.sleep(0)

        request = _selector_validate_request("selbusy0001")
        started = time.monotonic()
        response = await asyncio.wait_for(session_routes.session_selector_validate(request), timeout=2.0)
        elapsed = time.monotonic() - started

        assert response.status_code == 503
        # Comfortably under the 300s MCP-tool default -- proves the
        # dashboard override is actually in effect, not just documented.
        assert elapsed < 1.0
        page.locator.assert_not_called()

        release.set()
        await holder

    async def test_session_busy_timeout_error_maps_to_503(self, fakes: dict[str, Any]) -> None:
        """A SessionBusyTimeoutError from the gate maps to 503, distinctly
        from the generic locator-exception 400 branch -- locks in Task 10's
        Step 5 error-mapping contract."""
        from octowright.session.operation_gate import SessionBusyTimeoutError

        pool: _FakePool = fakes["pool"]
        page = MagicMock()
        page.locator = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=1)))
        session = _gated_session("selbusyerr1", page=page)

        session.operation = _RaisingOperation(
            SessionBusyTimeoutError("session 'selbusyerr1' operation 'dashboard_selector_validate' timed out")
        )
        pool._sessions["selbusyerr1"] = session

        request = _selector_validate_request("selbusyerr1")
        response = await session_routes.session_selector_validate(request)

        assert response.status_code == 503
        page.locator.assert_not_called()

    async def test_session_closing_error_maps_to_409(self, fakes: dict[str, Any]) -> None:
        """A SessionClosingError from the gate maps to 409, not the generic
        locator-exception 400 branch -- locks in Task 10's Step 5
        error-mapping contract."""
        from octowright.session.operation_gate import SessionClosingError

        pool: _FakePool = fakes["pool"]
        page = MagicMock()
        page.locator = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=1)))
        session = _gated_session("selclosing1", page=page)

        session.operation = _RaisingOperation(
            SessionClosingError("session 'selclosing1' operation 'dashboard_selector_validate' rejected")
        )
        pool._sessions["selclosing1"] = session

        request = _selector_validate_request("selclosing1")
        response = await session_routes.session_selector_validate(request)

        assert response.status_code == 409
        page.locator.assert_not_called()


# ─── session_close: 500 path + warm_close failure swallow ───────────────────


class TestSessionCloseEdges:
    def test_500_when_pool_close_raises(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """pool.close raising → 500 with error message."""
        pool: _FakePool = fakes["pool"]
        pool._sessions["closing00001"] = SimpleNamespace(instance_id="closing00001")
        pool.close_raises = RuntimeError("playwright crashed")
        r = client.delete("/api/sessions/closing00001")
        assert r.status_code == 500
        assert "close failed" in r.json()["error"]
        assert "playwright crashed" in r.json()["error"]

    def test_404_when_session_not_live(self, client: TestClient) -> None:
        """No live session → 404, even if a closed recording exists."""
        r = client.delete("/api/sessions/missing00001")
        assert r.status_code == 404
        assert "cannot be re-closed" in r.json()["error"]

    def test_warm_close_failure_swallowed(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If warm_close raises, response still 200 (cache.warm_close is best-effort)."""
        pool: _FakePool = fakes["pool"]
        jsonl = _write_recording(isolated_recordings, "warmcls0001")
        pool._sessions["warmcls0001"] = SimpleNamespace(instance_id="warmcls0001")
        pool.close_result = {
            "closed": True,
            "log_path": str(jsonl),
            "video_path": None,
            "trace_path": None,
        }

        # Force warm_close to blow up.
        from octowright.http import session_artifacts as _sa

        monkeypatch.setattr(
            _sa.session_artifact_cache,
            "warm_close",
            MagicMock(side_effect=RuntimeError("disk full")),
        )

        r = client.delete("/api/sessions/warmcls0001")
        # Still 200; cache field absent or gracefully omitted.
        assert r.status_code == 200
        body = r.json()
        assert body["closed"] is True
        assert "cache" not in body  # warm_close swallow path doesn't attach cache

    def test_warm_close_failure_logs_error_detail(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Silent-swallow policy: the warm_close error must reach the log so
        the failure mode is diagnosable from operator output. Capture the
        ``state.log.warning`` call and confirm ``error=repr(exc)`` is set."""
        pool: _FakePool = fakes["pool"]
        jsonl = _write_recording(isolated_recordings, "warmlog00001")
        pool._sessions["warmlog00001"] = SimpleNamespace(instance_id="warmlog00001")
        pool.close_result = {
            "closed": True,
            "log_path": str(jsonl),
            "video_path": None,
            "trace_path": None,
        }

        from octowright.http import session_artifacts as _sa

        boom = RuntimeError("disk full")
        monkeypatch.setattr(
            _sa.session_artifact_cache,
            "warm_close",
            MagicMock(side_effect=boom),
        )

        warning_calls: list[tuple[str, dict[str, Any]]] = []
        real_log = _http_state.log

        def _capture_warning(event: str, **kwargs: Any) -> None:
            warning_calls.append((event, kwargs))

        monkeypatch.setattr(
            _http_state,
            "log",
            SimpleNamespace(
                warning=_capture_warning,
                info=real_log.info,
                exception=real_log.exception,
                debug=real_log.debug,
                error=real_log.error,
            ),
        )

        r = client.delete("/api/sessions/warmlog00001")
        assert r.status_code == 200

        cache_failed = [
            (event, kwargs)
            for event, kwargs in warning_calls
            if event == "octowright.http.session_close_cache_report_failed"
        ]
        assert len(cache_failed) == 1
        _, kwargs = cache_failed[0]
        # The repr of the exception is the load-bearing field — without it,
        # operators see "cache report failed" with no cause to chase.
        assert kwargs["error"] == repr(boom)
        assert kwargs["instance_id"] == "warmlog00001"

    def test_log_path_empty_skips_warm_close(
        self,
        client: TestClient,
        fakes: dict[str, Any],
    ) -> None:
        """If pool.close returns no log_path, warm_close is skipped without error."""
        pool: _FakePool = fakes["pool"]
        pool._sessions["nologp00001"] = SimpleNamespace(instance_id="nologp00001")
        pool.close_result = {"closed": True, "log_path": "", "video_path": None}
        r = client.delete("/api/sessions/nologp00001")
        assert r.status_code == 200
        body = r.json()
        assert body["closed"] is True
        assert "cache" not in body


# ─── session_navigate: 500 path ─────────────────────────────────────────────


class TestSessionNavigateEdges:
    def test_500_when_navigate_raises(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """session.navigate exception → 500 with error string."""
        pool: _FakePool = fakes["pool"]
        session = SimpleNamespace(
            instance_id="navsess00001",
            navigate=AsyncMock(side_effect=RuntimeError("DNS failure")),
        )
        pool._sessions["navsess00001"] = session
        r = client.post("/api/sessions/navsess00001/navigate", json={"url": "https://x.test"})
        assert r.status_code == 500
        assert "navigate failed" in r.json()["error"]
        assert "DNS failure" in r.json()["error"]

    def test_400_non_string_url(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """url=int → 400 with 'non-empty string'."""
        pool: _FakePool = fakes["pool"]
        pool._sessions["navsess00002"] = SimpleNamespace(instance_id="navsess00002", navigate=AsyncMock())
        r = client.post("/api/sessions/navsess00002/navigate", json={"url": 42})
        assert r.status_code == 400
        assert "non-empty string" in r.json()["error"]

    def test_400_whitespace_url(self, client: TestClient, fakes: dict[str, Any]) -> None:
        """Whitespace-only url → 400 (.strip() is falsy)."""
        pool: _FakePool = fakes["pool"]
        pool._sessions["navsess00003"] = SimpleNamespace(instance_id="navsess00003", navigate=AsyncMock())
        r = client.post("/api/sessions/navsess00003/navigate", json={"url": "  "})
        assert r.status_code == 400


# ─── session_relaunch: viewport non-dict + video_dir → record_video ─────────


class TestSessionRelaunchEdges:
    def _write_with_launch(self, rec_dir: Path, instance_id: str, launch_extra: dict[str, Any]) -> Path:
        name = f"20260101T000000Z-chromium-{instance_id}.jsonl"
        p = rec_dir / name
        launch_row = {
            "ts": "2026-01-01T00:00:00Z",
            "action": "launch",
            "kind": "chromium",
            "url": "https://octowright.com",
            "label": None,
            "profile": None,
            **launch_extra,
        }
        p.write_text(
            json.dumps(launch_row) + "\n" + json.dumps({"ts": "2026-01-01T00:00:02Z", "action": "close"}) + "\n"
        )
        return p

    def test_viewport_non_dict_falls_back_to_none(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        """A scalar viewport in the JSONL → relaunch passes viewport_w/h=None."""
        self._write_with_launch(isolated_recordings, "rlchscalar01", {"viewport": 42})
        pool: _FakePool = fakes["pool"]
        r = client.post("/api/sessions/rlchscalar01/relaunch")
        assert r.status_code == 201, r.text
        kwargs = pool.launch_calls[0]
        assert kwargs["viewport_w"] is None
        assert kwargs["viewport_h"] is None

    def test_viewport_string_falls_back_to_none(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        """String viewport (malformed export) → None/None."""
        self._write_with_launch(isolated_recordings, "rlchstring01", {"viewport": "1024x768"})
        pool: _FakePool = fakes["pool"]
        r = client.post("/api/sessions/rlchstring01/relaunch")
        assert r.status_code == 201
        kwargs = pool.launch_calls[0]
        assert kwargs["viewport_w"] is None
        assert kwargs["viewport_h"] is None

    def test_video_dir_implies_record_video_true(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        """A non-empty video_dir on the launch record → record_video=True on relaunch."""
        self._write_with_launch(isolated_recordings, "rlchvideo001", {"video_dir": "/tmp/v"})
        pool: _FakePool = fakes["pool"]
        r = client.post("/api/sessions/rlchvideo001/relaunch")
        assert r.status_code == 201
        assert pool.launch_calls[0]["record_video"] is True

    def test_no_video_dir_means_record_video_false(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        """No video_dir → record_video=False (bool() of None / missing is False)."""
        self._write_with_launch(isolated_recordings, "rlchnovid001", {})
        pool: _FakePool = fakes["pool"]
        r = client.post("/api/sessions/rlchnovid001/relaunch")
        assert r.status_code == 201
        assert pool.launch_calls[0]["record_video"] is False

    def test_relaunch_preserves_session_scoping_flags(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        self._write_with_launch(isolated_recordings, "rlchsession1", {"session": True, "ephemeral": False})
        pool: _FakePool = fakes["pool"]
        r = client.post("/api/sessions/rlchsession1/relaunch")
        assert r.status_code == 201
        assert pool.launch_calls[0]["session"] is True
        assert pool.launch_calls[0]["ephemeral"] is False

    def test_relaunch_uses_fresh_har_path_when_prior_har_exists(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        har_path = isolated_recordings / "capture.har"
        har_path.write_text("prior HAR", encoding="utf-8")
        self._write_with_launch(isolated_recordings, "rlchhar00001", {"har": True, "har_path": str(har_path)})
        pool: _FakePool = fakes["pool"]
        r = client.post("/api/sessions/rlchhar00001/relaunch")
        assert r.status_code == 201
        assert pool.launch_calls[0]["har"] is True
        assert pool.launch_calls[0]["har_path"] == str(isolated_recordings / "capture.1.har")

    def test_relaunch_reuses_prior_har_path_when_file_is_gone(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        """If the prior HAR has been deleted, relaunch reuses the original path
        rather than producing a needlessly suffixed sibling."""
        har_path = isolated_recordings / "deleted.har"
        # Note: do NOT write the file — simulate post-cleanup state.
        self._write_with_launch(isolated_recordings, "rlchhardel01", {"har": True, "har_path": str(har_path)})
        pool: _FakePool = fakes["pool"]
        r = client.post("/api/sessions/rlchhardel01/relaunch")
        assert r.status_code == 201
        assert pool.launch_calls[0]["har"] is True
        assert pool.launch_calls[0]["har_path"] == str(har_path)

    def test_500_when_pool_launch_raises(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        """pool.launch raising during relaunch → 500."""
        self._write_with_launch(isolated_recordings, "rlchboom0001", {})
        pool: _FakePool = fakes["pool"]
        pool.launch_raises = RuntimeError("playwright down")
        r = client.post("/api/sessions/rlchboom0001/relaunch")
        assert r.status_code == 500
        assert "relaunch failed" in r.json()["error"]


def test_launch_recording_persists_relaunch_relevant_options(tmp_path: Path) -> None:
    log_path = tmp_path / "launch.jsonl"
    recorder = Recorder(log_path)
    _record_launch_event(
        recorder,
        instance_id="abc123",
        kind="chromium",
        label="temporary",
        profile=None,
        user_data_dir=str(tmp_path / "profile"),
        target_url="https://octowright.com",
        headless=False,
        log_viewport={"mode": "fluid"},
        stabilize=True,
        record_video=False,
        video_dir=None,
        trace=False,
        har_path=None,
        har_mode="minimal",
        har_url_filter=None,
        har_content=None,
        badge=False,
        badge_position="top-left",
        tile=True,
        ephemeral=False,
        session=True,
    )
    recorder.close()

    row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["badge"] is False
    assert row["badge_position"] == "top-left"
    assert row["tile"] is True
    assert row["ephemeral"] is False
    assert row["session"] is True


# ─── session_detail live-path: aria swallow + macro_intent attach swallow ──


class TestSessionDetailLiveEdges:
    def _live_session(self, instance_id: str, log_path: Path) -> SimpleNamespace:
        page = MagicMock()
        locator = MagicMock()
        locator.aria_snapshot = AsyncMock(return_value="- button 'OK'")
        page.locator = MagicMock(return_value=locator)
        page.url = "https://octowright.com"
        recorder = SimpleNamespace(event_count=5, action_count=3)
        # A real gate (not a MagicMock) — _live_session_detail_response awaits
        # `.operation(...)` as an async context manager.
        gate = SessionOperationGate(instance_id, "chromium", queue_timeout_seconds=30)
        return SimpleNamespace(
            instance_id=instance_id,
            kind="chromium",
            label=None,
            profile=None,
            url="https://octowright.com",
            log_path=str(log_path),
            video_path=None,
            trace_path=None,
            markdown_path=None,
            websocket_path=None,
            console=[],
            console_count=0,
            downloads=[],
            download_count=0,
            pages=[page],
            page_count=1,
            page=page,
            recorder=recorder,
            operation=gate.operation,
            operation_snapshot=gate.snapshot,
            _test_operation_gate=gate,
        )

    def test_aria_failure_does_not_break_detail(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        """If aria_snapshot raises, the detail response still returns 200."""
        log_path = _write_recording(isolated_recordings, "ariafail0001")
        live = self._live_session("ariafail0001", log_path)
        live.page.locator = MagicMock(
            return_value=SimpleNamespace(aria_snapshot=AsyncMock(side_effect=RuntimeError("nope")))
        )
        pool: _FakePool = fakes["pool"]
        pool._sessions["ariafail0001"] = live
        r = client.get("/api/sessions/ariafail0001")
        assert r.status_code == 200
        body = r.json()
        # `aria` field should be absent (swallowed), but other fields present.
        assert "aria" not in body
        assert body["id"] == "ariafail0001"

    def test_aria_success_attaches_to_response(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
    ) -> None:
        """Happy aria_snapshot → 'aria' field populated."""
        log_path = _write_recording(isolated_recordings, "ariaok000001")
        live = self._live_session("ariaok000001", log_path)
        pool: _FakePool = fakes["pool"]
        pool._sessions["ariaok000001"] = live
        r = client.get("/api/sessions/ariaok000001")
        assert r.status_code == 200
        assert r.json()["aria"] == "- button 'OK'"

    async def test_busy_gate_degrades_fast_not_300s(
        self, isolated_recordings: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The aria capture's operation boundary must fail fast on a busy
        gate (falling into the existing debug-log-and-degrade branch)
        instead of inheriting the operation gate's 300s MCP-tool default and
        silently stalling the whole session-detail response for minutes.
        Calls _live_session_detail_response directly rather than through
        TestClient, which runs the ASGI app on its own thread/event loop —
        incompatible with a gate Future created on this test's loop."""
        import asyncio
        import time

        from octowright.http.routes.sessions import _live_session_detail_response

        monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_OPERATION_TIMEOUT_SECONDS", "0.05")
        log_path = _write_recording(isolated_recordings, "ariabusy0001")
        live = self._live_session("ariabusy0001", log_path)

        release = asyncio.Event()

        async def _hold() -> None:
            async with live.operation("owner"):
                await release.wait()

        holder = asyncio.create_task(_hold())
        async with asyncio.timeout(1):
            while live.operation_snapshot()["active_operation"] != "owner":
                await asyncio.sleep(0)

        started = time.monotonic()
        response = await asyncio.wait_for(_live_session_detail_response(live), timeout=2.0)
        elapsed = time.monotonic() - started

        assert response.status_code == 200
        body = json.loads(response.body)
        assert "aria" not in body
        # Comfortably under the 300s MCP-tool default -- proves the
        # dashboard override is actually in effect, not just documented.
        assert elapsed < 1.0
        live.page.locator.assert_not_called()

        release.set()
        await holder

    def test_aria_failure_logs_at_debug(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The swallowed aria_snapshot exception must hit the log so an
        operator can see why the 'aria' field is missing from a live
        session response. Previously the contextlib.suppress made it
        invisible — that violated the project's silent-swallow policy."""
        log_path = _write_recording(isolated_recordings, "arialog00001")
        live = self._live_session("arialog00001", log_path)
        live.page.locator = MagicMock(
            return_value=SimpleNamespace(aria_snapshot=AsyncMock(side_effect=RuntimeError("aria boom")))
        )
        pool: _FakePool = fakes["pool"]
        pool._sessions["arialog00001"] = live

        # state.log is the structured logger sessions.py emits through;
        # patch its .debug to capture the call without relying on
        # provide.telemetry's stdlib propagation (which is flaky on some
        # CI runners — see test_pool_disconnect._LogCapture).
        from octowright.http import state as _http_state

        events: list[tuple[str, dict]] = []

        class _LogProxy:
            """Capture .debug calls; delegate everything else to the real
            logger so unrelated log lines emitted during the request still
            flow through provide.telemetry."""

            def __init__(self, real: Any) -> None:
                self._real = real

            def debug(self, event: str, **kw: Any) -> None:
                events.append((event, kw))

            def __getattr__(self, name: str) -> Any:
                return getattr(self._real, name)

        monkeypatch.setattr(_http_state, "log", _LogProxy(_http_state.log))

        r = client.get("/api/sessions/arialog00001")
        assert r.status_code == 200
        assert any("live_aria_snapshot_failed" in name for name, _ in events)

    def test_macro_intent_failure_logs_at_debug(
        self,
        client: TestClient,
        fakes: dict[str, Any],
        isolated_recordings: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If load_macro_from_recording or get_semantic_intent raises, the
        macro_intent field is dropped from the response (it's purely
        informational) — but the failure must be visible at debug level so
        a real bug (e.g. a semantic resolver crash on a new action shape)
        doesn't sit silently in production."""
        log_path = _write_recording(isolated_recordings, "intfail00001")
        live = self._live_session("intfail00001", log_path)
        pool: _FakePool = fakes["pool"]
        pool._sessions["intfail00001"] = live

        from octowright.macros import semantic as _ms

        def _boom(_actions: Any) -> str:
            raise RuntimeError("semantic boom")

        monkeypatch.setattr(_ms, "get_semantic_intent", _boom)

        from octowright.http import state as _http_state

        events: list[tuple[str, dict]] = []

        class _LogProxy:
            """Capture .debug calls; delegate everything else to the real
            logger so unrelated log lines emitted during the request still
            flow through provide.telemetry."""

            def __init__(self, real: Any) -> None:
                self._real = real

            def debug(self, event: str, **kw: Any) -> None:
                events.append((event, kw))

            def __getattr__(self, name: str) -> Any:
                return getattr(self._real, name)

        monkeypatch.setattr(_http_state, "log", _LogProxy(_http_state.log))

        r = client.get("/api/sessions/intfail00001")
        assert r.status_code == 200
        body = r.json()
        assert "macro_intent" not in body
        assert any("macro_intent_failed" in name for name, _ in events)


# ─── _live_summary_from_launch helper ───────────────────────────────────────


class TestLiveSummaryFromLaunch:
    def test_uses_log_ctime_when_file_exists(self, tmp_path: Path) -> None:
        """When the log_path exists, started_at comes from its ctime."""
        log_path = tmp_path / "real.jsonl"
        log_path.write_text("{}\n")
        result = {
            "instance_id": "iid12345",
            "kind": "chromium",
            "label": None,
            "profile": None,
            "url": "https://x",
            "log_path": str(log_path),
        }
        summary = session_routes._live_summary_from_launch(result)
        assert summary["id"] == "iid12345"
        assert summary["log_path"] == str(log_path)
        assert summary["live"] is True
        # started_at present and ISO-8601-ish.
        assert "T" in summary["started_at"]

    def test_falls_back_to_now_when_file_missing(self, tmp_path: Path) -> None:
        """If log_path doesn't exist, started_at uses time.time()."""
        result = {
            "instance_id": "iid67890",
            "kind": "firefox",
            "label": "qa",
            "profile": "cosmo",
            "url": "https://y",
            "log_path": str(tmp_path / "missing.jsonl"),
        }
        summary = session_routes._live_summary_from_launch(result)
        # Must not raise; started_at must be present.
        assert "T" in summary["started_at"]
        assert summary["label"] == "qa"
        assert summary["profile"] == "cosmo"

    def test_default_counts_for_freshly_launched(self, tmp_path: Path) -> None:
        """A just-launched session has event_count=1 (the launch row), zeros elsewhere."""
        result = {
            "instance_id": "iidcount1",
            "kind": "webkit",
            "label": None,
            "profile": None,
            "url": "https://z",
            "log_path": str(tmp_path / "missing.jsonl"),
        }
        summary = session_routes._live_summary_from_launch(result)
        assert summary["event_count"] == 1
        assert summary["console_count"] == 0
        assert summary["download_count"] == 0
        assert summary["page_count"] == 1


# ─── _resolve_live_markdown_path: precedence ────────────────────────────────


class TestResolveLiveMarkdownPath:
    def test_prefers_live_attribute(self, tmp_path: Path) -> None:
        """If live.markdown_path is set, that wins over the artifact-lookup result."""
        live = SimpleNamespace(instance_id="x", markdown_path=tmp_path / "live.md")
        result = session_routes._resolve_live_markdown_path(live)
        assert result == str(tmp_path / "live.md")

    def test_falls_back_to_artifact_lookup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """live.markdown_path None → use _resolve_artifact_path (must patch in routes module)."""
        monkeypatch.setattr(
            session_routes,
            "_resolve_artifact_path",
            lambda _id, _kind: tmp_path / "from_lookup.md",
        )
        live = SimpleNamespace(instance_id="x", markdown_path=None)
        assert session_routes._resolve_live_markdown_path(live) == str(tmp_path / "from_lookup.md")

    def test_returns_none_when_both_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Neither path source has a value → None."""
        monkeypatch.setattr(session_routes, "_resolve_artifact_path", lambda _id, _kind: None)
        live = SimpleNamespace(instance_id="x", markdown_path=None)
        assert session_routes._resolve_live_markdown_path(live) is None
