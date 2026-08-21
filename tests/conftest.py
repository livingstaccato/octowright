# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pytest configuration shared across the suite.

The `octowright_demos` package lives under `tools/` (not under `src/octowright/`)
so demo-generation tooling never ships in the wheel. Tests that import it need
`tools/` on sys.path; this conftest does that once for the whole suite.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

_AMBIENT_OTLP_ENV_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
    "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
)


@pytest.fixture(autouse=True)
def _isolate_upgrade_marker(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Keep the post-upgrade what's-new marker out of the developer's real config.

    The marker records the last version a leader started on, and it fires ONCE
    per version bump -- so anything that writes the real
    ``~/.config/octowright/upgrade.json`` during a test run consumes the notice
    for an actual upgrade, and the operator never sees it. Observed for real on
    the 0.16.3 release: the marker was already at 0.16.3, timestamped fifteen
    minutes before the daemon restart that was supposed to trigger the banner.

    0.16.2 fixed this by making five named live-daemon modules set
    ``XDG_CONFIG_HOME``, and a guard test pins that they still do. But the guard
    enumerates modules, so it only ever covers the offenders known at the time:
    ``test_daemonize.py`` spawns a real daemon too, isolates ``XDG_STATE_HOME``
    with a careful comment about polluting the developer's daemon log, and never
    got the config half. Adding a sixth name would just reset the trap.

    Exporting the override here covers every spawned subprocess at once,
    whichever module spawns it, because a child reads the variable at ITS import
    time. That is the whole point of doing it in conftest rather than per module.
    """
    monkeypatch.setenv("OCTOWRIGHT_UPGRADE_STATE", str(tmp_path_factory.mktemp("upgrade") / "upgrade.json"))


@pytest.fixture(autouse=True)
def _clear_ambient_otlp_export_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic when a developer shell has OTLP export configured."""
    for name in _AMBIENT_OTLP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_actual_http_port() -> None:
    """Reset the runtime-resolved HTTP port so tests don't bleed port state."""
    import octowright.defaults as _d

    _d._bound_http_port = None
    yield  # type: ignore[misc]
    _d._bound_http_port = None


# The dashboard binds to a loopback address, so a real browser always sends a
# loopback Host header. Starlette's TestClient otherwise defaults to the
# non-loopback `http://testserver`, which the DNS-rebinding guard in
# `http/exposure.py` now (correctly) rejects on every sensitive route. Default
# the test client to a loopback base URL so endpoint tests model real traffic;
# tests that exercise the rebinding/remote-bind guards still override `base_url`
# or pass an explicit `host` header to assert the rejection paths.
_DASHBOARD_LOOPBACK_BASE_URL = "http://127.0.0.1"


_DASHBOARD_LOOPBACK_HOST = "127.0.0.1"


@pytest.fixture(autouse=True)
def _loopback_dashboard_testclient(monkeypatch: pytest.MonkeyPatch) -> None:
    import starlette.testclient as _starlette_testclient

    original_init = _starlette_testclient.TestClient.__init__
    original_ws_connect = _starlette_testclient.TestClient.websocket_connect

    def _init(self: object, *args: object, **kwargs: object) -> None:
        # HTTP requests derive the Host header from base_url; only inject the
        # default when the caller didn't set base_url itself — positionally
        # (args beyond `app`) or by keyword.
        if "base_url" not in kwargs and len(args) < 2:
            kwargs["base_url"] = _DASHBOARD_LOOPBACK_BASE_URL
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    def _websocket_connect(self: object, url: object, *args: object, **kwargs: object) -> object:
        # websocket_connect ignores base_url for the Host header (it always
        # sends `testserver`), so inject a loopback Host unless the test set one.
        headers = dict(kwargs.get("headers") or {})  # type: ignore[arg-type]
        if not any(str(key).lower() == "host" for key in headers):
            headers["host"] = _DASHBOARD_LOOPBACK_HOST
        kwargs["headers"] = headers
        return original_ws_connect(self, url, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_starlette_testclient.TestClient, "__init__", _init)
    monkeypatch.setattr(_starlette_testclient.TestClient, "websocket_connect", _websocket_connect)


@pytest.fixture(autouse=True)
def _isolate_dashboard_pairing_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep one test's built app from lending its pairing store to the next.

    ``build_app`` publishes the app's ``DashboardPairingState`` into module
    state so ``octowright_dashboard_url`` can mint a pairing code without a
    handle on the Starlette app. That is process-global: any test that builds
    a token-carrying app would otherwise leave one behind, and an unrelated
    test calling the tool would get a ``/pair#<code>`` URL instead of the
    plain address it expected. Snapshotting through monkeypatch restores the
    prior value at teardown.
    """
    from octowright.http import state as _http_state

    monkeypatch.setattr(_http_state, "_DASHBOARD_PAIRING", None, raising=False)


@pytest.fixture(autouse=True)
def _isolate_canonical_http_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CANONICAL dashboard port away from any real local daemon.

    ``_leader_election._canonical_port_serves_octowright`` is the split-brain
    guard: before spawning a replacement daemon it probes the *canonical* port
    (``defaults.HTTP_PORT``, normally 6286) and refuses to spawn if octowright
    already answers there. That probe ignores whatever port a test picked, so a
    developer running ``octowright serve`` on 6286 silently flipped the guard
    ON for the whole suite and every "should spawn a replacement" assertion
    failed — a false red that only appears on a machine with a live daemon.

    Repointing it at an OS-assigned free port makes the suite hermetic. The
    guard's logic is unchanged and still exercised: tests that need it to fire
    stub the probe explicitly, and the probe's own classification tests stub
    the HTTP layer, so neither depends on this port. Even if something did bind
    the port, only a real octowright ``/api/health`` (``ok: true``) trips the
    guard, so an unrelated listener still reads as free.
    """
    from octowright import defaults as _defaults

    monkeypatch.setattr(_defaults, "HTTP_PORT", _free_port(), raising=False)


def _free_port() -> int:
    """Return an OS-assigned free TCP port on 127.0.0.1.

    Tests that spin up a real server (uvicorn, octowright daemon) should use
    this instead of hardcoded high-numbered ports. Hardcoded ports collide
    when the suite is run in parallel (e.g. pytest-xdist) or alongside an
    already-running daemon on the same dev machine.

    The kernel picks an ephemeral port, we close the probe socket immediately,
    then hand the number back. There is a tiny TOCTOU window between close
    and reuse — small enough in practice that the suite has not flaked on it.
    """
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _override_base_url(default_url: str, override: str | None) -> str:
    """Apply optional host override to the default local integration URL.

    `override` can be a full URL (e.g. ``http://test.octowright.com``) or a
    bare host. When no port is provided, the default URL port is preserved.
    """
    if not override:
        return default_url
    candidate = override if "://" in override else f"http://{override}"
    default_parts = urlsplit(default_url)
    cand_parts = urlsplit(candidate)
    scheme = cand_parts.scheme or default_parts.scheme
    host = cand_parts.hostname or default_parts.hostname
    port = cand_parts.port or default_parts.port
    if host is None:
        return default_url
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((scheme, netloc, default_parts.path, default_parts.query, default_parts.fragment))


@pytest.fixture
async def playground_server(monkeypatch: pytest.MonkeyPatch):
    """Run the demo playground on an ephemeral local port for integration tests."""
    from demo.playground.server import PlaygroundServer

    port = _free_port()
    server = PlaygroundServer(port=port)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
def integration_local_base_url(playground_server: object, monkeypatch: pytest.MonkeyPatch) -> str:
    """Base URL used by local-server-backed integration tests.

    Default is the started PlaygroundServer URL. Set OCTOWRIGHT_TEST_BASE_URL
    for local alias runs (e.g. http://test.octowright.com).
    """
    _ = monkeypatch
    raw = playground_server.url
    override = os.environ.get("OCTOWRIGHT_TEST_BASE_URL")
    return _override_base_url(str(raw), override)
