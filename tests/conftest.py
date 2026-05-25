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

import socket
import sys
from pathlib import Path

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
