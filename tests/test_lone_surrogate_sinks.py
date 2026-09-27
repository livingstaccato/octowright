# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A lone UTF-16 surrogate from the page must not break a JSON sink.

JavaScript strings are UTF-16 and may hold an unpaired surrogate
(``console.log('\\uD800')``); Python carries it as a lone code point that
strict UTF-8 refuses to encode. ``json.dumps(..., ensure_ascii=False)``
passes it straight through, so the recorder's write raised -- the row was
lost and a click/fill that records after acting reported failure for an
action that happened -- and the dashboard's JSON responses 500'd for as
long as the entry stayed in the console ring.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from octowright import http as _http
from octowright._json_text import dumps_utf8_safe
from octowright.http import state as _http_state
from octowright.recorder import Recorder, tail_log
from octowright.server import _state

LONE = "\ud800"


class _FakePool:
    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}

    def maybe_get(self, instance_id: str) -> Any | None:
        return self._sessions.get(instance_id)

    def has_session(self, instance_id: str) -> bool:
        return instance_id in self._sessions

    def iter_sessions(self) -> tuple[Any, ...]:
        return tuple(self._sessions.values())


class _FakeScenarioPool:
    def has_live(self, scenario_id: str) -> bool:
        return False

    def list_live(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    return rec


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    fake = _FakePool()
    monkeypatch.setattr(_state, "pool", fake)
    monkeypatch.setattr(_state, "scenario_pool", _FakeScenarioPool())
    return fake


@pytest.fixture
def client(recordings: Path, pool: _FakePool) -> TestClient:
    return TestClient(_http.build_app())


def test_dumps_keeps_ordinary_text_readable() -> None:
    """The fallback is for the unencodable case only; non-ASCII stays unescaped."""
    assert dumps_utf8_safe({"t": "café"}) == '{"t": "café"}'


def test_dumps_escapes_a_lone_surrogate_into_valid_utf8() -> None:
    text = dumps_utf8_safe({"t": LONE})
    text.encode("utf-8")  # must not raise
    assert json.loads(text) == {"t": LONE}


def test_recorder_keeps_a_row_holding_a_lone_surrogate(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    recorder = Recorder(path)
    recorder.record("console", level="log", text=f"before{LONE}after")
    recorder.record("click", selector="#ok", role_name=LONE)
    recorder.close()

    events, _cursor, _total = tail_log(path, 0)
    assert [e["action"] for e in events] == ["console", "click"]
    assert events[0]["text"] == f"before{LONE}after"
    assert recorder.event_count == 2


def test_recorder_counts_the_escaped_bytes_against_the_ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ceiling measures what reaches disk, i.e. the escaped spelling."""
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "100000")
    path = tmp_path / "rec.jsonl"
    recorder = Recorder(path)
    recorder.record("console", text=LONE)
    recorder.close()
    assert recorder._bytes_written == path.stat().st_size


def test_live_console_endpoint_returns_a_lone_surrogate(client: TestClient, recordings: Path, pool: _FakePool) -> None:
    log_path = recordings / "20260101T000000Z-chromium-surrogat01.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    pool._sessions["surrogat01"] = SimpleNamespace(
        instance_id="surrogat01",
        log_path=log_path,
        console=[{"level": "log", "text": LONE}],
    )

    response = client.get("/api/sessions/surrogat01/console")

    assert response.status_code == 200
    assert response.json()["messages"] == [{"level": "log", "text": LONE}]


def test_closed_console_and_events_endpoints_return_a_lone_surrogate(
    client: TestClient, recordings: Path, pool: _FakePool
) -> None:
    """A recording written by the fixed recorder carries the escape, which
    reads back as the lone code point and must survive the response too."""
    path = recordings / "20260101T000000Z-chromium-surrogat02.jsonl"
    recorder = Recorder(path)
    recorder.record("console", level="log", text=LONE)
    recorder.close()

    console = client.get("/api/sessions/surrogat02/console")
    events = client.get("/api/sessions/surrogat02/events")

    assert console.status_code == 200
    assert console.json()["messages"][0]["text"] == LONE
    assert events.status_code == 200
    assert events.json()["events"][0]["text"] == LONE


def test_tail_websocket_pushes_a_lone_surrogate(client: TestClient, recordings: Path, pool: _FakePool) -> None:
    path = recordings / "20260101T000000Z-chromium-surrogat03.jsonl"
    recorder = Recorder(path)
    recorder.record("console", level="log", text=LONE)
    recorder.close()
    pool._sessions["surrogat03"] = SimpleNamespace(instance_id="surrogat03", log_path=path, console=[])

    with client.websocket_connect("/api/sessions/surrogat03/tail") as ws:
        payload = ws.receive_json()
        ws.close()

    assert payload["events"][0]["text"] == LONE


def test_websocket_sidecar_keeps_a_frame_holding_a_lone_surrogate(tmp_path: Path) -> None:
    from tests._websocket_fakes import FakeSocket, io_mixin_session, sidecar_rows

    session = io_mixin_session(tmp_path)
    socket = FakeSocket()
    session._handle_websocket(socket)
    socket.emit("framereceived", f"frame{LONE}")

    rows = [row for row in sidecar_rows(session) if row["action"] == "websocket_framereceived"]
    assert rows[0]["payload_text"] == f"frame{LONE}"


# --- Readers turn the escape back into a lone code point --------------------
#
# The recorder writes ``\ud800`` as JSON's escape, which is valid UTF-8 -- but
# ``json.loads`` of that row yields the raw lone code point again. Every writer
# downstream of a read (macro storage, every HTTP route) must therefore be as
# surrogate-safe as the recorder, or the crash just moves one hop along.


def _recording_with_lone_role_name(path: Path) -> Path:
    recorder = Recorder(path)
    recorder.record("navigate", url="https://example.test/")
    recorder.record("click", selector="#ok", role="button", role_name=f"Go{LONE}")
    recorder.close()
    return path


def test_macro_saved_from_a_recording_holding_a_lone_surrogate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import octowright.macros.storage as storage

    monkeypatch.setattr(storage, "MACROS_DIR", tmp_path / "macros")
    rec = _recording_with_lone_role_name(tmp_path / "rec.jsonl")

    dest = storage.save_macro(recording_path=rec, name="surrogate-save")

    dest.read_bytes().decode("utf-8")  # the file on disk is valid UTF-8
    clicks = [a for a in storage.load_macro("surrogate-save")["actions"] if a["action"] == "click"]
    assert clicks[0]["role_name"] == f"Go{LONE}"

    # write_macro (the dashboard's PUT path) re-writes the loaded document.
    storage.write_macro(name="surrogate-save", macro=storage.load_macro("surrogate-save"))
    assert storage.load_macro("surrogate-save")["actions"][1]["role_name"] == f"Go{LONE}"


def test_meta_macro_detail_route_returns_a_lone_surrogate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import octowright.macros.storage as storage

    monkeypatch.setattr(storage, "MACROS_DIR", tmp_path / "macros")
    rec = _recording_with_lone_role_name(tmp_path / "rec.jsonl")
    storage.save_macro(recording_path=rec, name="surrogate-detail")

    response = client.get("/api/macros/surrogate-detail")

    assert response.status_code == 200
    assert response.json()["actions"][1]["role_name"] == f"Go{LONE}"


def test_scenario_run_macro_route_returns_a_lone_surrogate(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed broadcast carries the page's console tail, i.e. page text."""

    class _LiveScenarioPool(_FakeScenarioPool):
        def has_live(self, scenario_id: str) -> bool:
            return True

        async def run_macro(self, **_kwargs: Any) -> dict[str, Any]:
            return {"results": [{"ok": False, "console_tail": [{"level": "error", "text": LONE}]}]}

    monkeypatch.setattr(_state, "scenario_pool", _LiveScenarioPool())

    response = client.post("/api/scenarios/scen01/run_macro", json={"macro": "m"})

    assert response.status_code == 200
    assert response.json()["results"][0]["console_tail"][0]["text"] == LONE


# --- One response class, one writer ----------------------------------------

_SRC = Path(__file__).resolve().parents[1] / "src" / "octowright"


def test_no_http_module_builds_starlettes_strict_json_response() -> None:
    """Every route answers through the surrogate-safe class.

    A per-route opt-in is how the scenario and meta routes were missed; the
    guard makes a new route that reaches for Starlette's class fail here
    instead of in production on the first page with a lone surrogate.
    """
    offenders = [
        str(path.relative_to(_SRC))
        for path in sorted((_SRC / "http").rglob("*.py"))
        if path.name != "json_response.py"
        and any(
            line.startswith("from starlette.responses import") and "JSONResponse" in line
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    ]
    assert offenders == []


def test_no_module_writes_json_with_ensure_ascii_false_directly() -> None:
    """``ensure_ascii=False`` text is only safe through ``dumps_utf8_safe``."""
    allowed = {
        "_json_text.py",  # the safe writer itself
        # Builds scrub VARIANTS of a value (an in-memory string to match
        # against), never text that is encoded or written.
        "macros/privacy.py",
    }
    offenders = [
        str(path.relative_to(_SRC))
        for path in sorted(_SRC.rglob("*.py"))
        if str(path.relative_to(_SRC)) not in allowed and _passes_ensure_ascii_false(path)
    ]
    assert offenders == []


def _passes_ensure_ascii_false(path: Path) -> bool:
    # A call keyword, not a substring: docstrings explain the flag by name.
    return any(
        isinstance(node, ast.keyword)
        and node.arg == "ensure_ascii"
        and isinstance(node.value, ast.Constant)
        and node.value.value is False
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )
