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
