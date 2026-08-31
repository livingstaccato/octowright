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

import functools
import os
import signal
import socket
import sys
import weakref
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


# Where the id of the currently-running test is parked so a killed run can still
# be attributed. Git-ignored; rewritten in place rather than appended to, so it
# always holds exactly one line.
CURRENT_TEST_BREADCRUMB = Path(__file__).resolve().parent.parent / ".pytest-current-test"


# Every BrowserPool built during the run, so a test that forgets to shut one
# down cannot leak its Playwright driver into the rest of the session.
_TRACKED_POOLS: weakref.WeakSet = weakref.WeakSet()


def _install_pool_leak_tracking() -> None:
    """Record every BrowserPool as it is constructed.

    A pool starts its Playwright driver lazily (``_ensure_pw``) and only
    ``shutdown_pool`` ever calls ``pw.stop()``. Of the test modules that launch
    a real browser, most never shut their pool down -- so the drivers pile up:
    9-10 live ``playwright/driver/node`` children under a single pytest process
    were counted mid-run, each holding a pipe, an OS process and an
    ``asyncio-waitpid`` thread for the rest of the session.

    Patching the constructor rather than adding a registry to ``BrowserPool``
    keeps this entirely in the tests, where the defect is: production has one
    pool with an explicit lifecycle and needs no registry. The import costs
    ~157ms once, at conftest import, against a suite that runs for minutes.
    """
    from octowright.browser_pool.pool import BrowserPool

    original = BrowserPool.__init__
    if getattr(original, "_octowright_leak_tracked", False):
        return

    @functools.wraps(original)
    def tracked(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        original(self, *args, **kwargs)
        _TRACKED_POOLS.add(self)

    tracked._octowright_leak_tracked = True  # type: ignore[attr-defined]
    BrowserPool.__init__ = tracked  # type: ignore[method-assign]


_install_pool_leak_tracking()


def _driver_pid(pw: object) -> int | None:
    """OS pid of a live Playwright driver, or None if the chain has moved.

    Playwright exposes no public handle on the node process it spawned, so this
    walks ``_impl_obj._connection._transport._proc`` defensively: every hop is a
    ``getattr`` and a broken chain simply means no reaping, never an error.
    """
    node: object | None = pw
    for attr in ("_impl_obj", "_connection", "_transport", "_proc"):
        node = getattr(node, attr, None)
        if node is None:
            return None
    pid = getattr(node, "pid", None)
    return pid if isinstance(pid, int) else None


@pytest.fixture(autouse=True)
def _reap_leaked_browser_drivers() -> Iterator[None]:
    """Kill the Playwright driver of any pool the test left running.

    A pool starts its driver lazily (``_ensure_pw``) and only ``shutdown_pool``
    ever calls ``pw.stop()``. Most test modules that launch a real browser never
    shut their pool down, so the drivers accumulate: 9-10 live
    ``playwright/driver/node`` children were counted under a single pytest
    process mid-run, each holding a pipe, an OS process and an
    ``asyncio-waitpid`` thread for the rest of the session.

    Signalling the process rather than awaiting ``pool.shutdown()`` is the
    point, and the graceful version was tried first and reverted: an async
    autouse fixture DOES run for sync tests under ``asyncio_mode = "auto"``, but
    it also forces an asyncio loop onto the trio half of every
    ``pytest-anyio``-parametrized test, and those then fail inside anyio's
    shielded ``CancelScope`` with "must be called from async context"
    (measured: two ``tests/test_roster.py`` trio cases went red, green again the
    moment the fixture stopped being autouse). A sync fixture that signals a pid
    needs no loop at all and so cannot care which backend the test ran on.

    Autouse fixtures are set up before the test's own, so this tears down after
    them: a test that shuts its pool down properly clears ``_pw`` first and this
    finds nothing to do. It is a backstop, not a licence to skip cleanup.
    """
    yield
    for pool in list(_TRACKED_POOLS):
        pw = getattr(pool, "_pw", None)
        if pw is None:
            continue
        # Clear first: the handle is dead either way once the process is gone,
        # and leaving it set would make every later teardown retry a dead pid.
        pool._pw = None
        pid = _driver_pid(pw)
        if pid is None:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            # Already gone, or not ours to signal. Nothing to clean up.
            pass


def _breadcrumb(item: pytest.Item, phase: str) -> None:
    """Record which test/phase is in flight, for a run that dies without reporting.

    pytest-timeout's ``thread`` method (see ``timeout_method`` in pyproject)
    dumps every thread's stack and then calls ``os._exit(1)``. What it never
    writes is the nodeid: ``dump_stacks`` titles each section with a THREAD
    name, and the process dies before pytest can report which item was in
    flight. Under the suite's ``-q`` that hands the operator a wall of stacks
    and no test name -- the first thing anyone needs, and exactly what was
    missing when a wedged run went unnamed for 12.6 hours.

    A file rather than a print, because stderr does not survive the trip. Two
    routes were measured and both failed: ``pytest_runtest_logstart`` fires
    before per-item capture is installed, so it puts one stray line per test on
    a green run; and writing under capture does not reach the dump either,
    since pytest drains the buffer at the end of every phase and the capture
    manager's own hookwrapper does not reliably enclose a conftest one. The
    file has neither problem -- it costs one small write per phase, adds
    nothing to any run's output, and is readable after the process is gone.

    The phase is recorded too, because it changes the diagnosis: a wedge in
    ``teardown`` (a hung ``pool.shutdown()``, say) is a different bug from one
    in the test body.
    """
    try:
        CURRENT_TEST_BREADCRUMB.write_text(f"{phase} {item.nodeid}\n")
    except OSError:
        # Diagnostics must never fail a test run. A read-only or full rootdir
        # loses the breadcrumb; every other guard here still applies.
        pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Iterator[None]:
    _breadcrumb(item, "setup")
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    _breadcrumb(item, "call")
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Iterator[None]:
    _breadcrumb(item, "teardown")
    yield


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Remove the breadcrumb on any run that reaches the end under its own power.

    Its only meaning is "this is where a run that never reported died", so a
    file left behind by a completed session would point at that session's last
    test and misattribute the NEXT wedge.
    """
    _ = session, exitstatus
    CURRENT_TEST_BREADCRUMB.unlink(missing_ok=True)


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


def _checkout_project_config() -> Path | None:
    """This checkout's own ``.octowright/config.yaml``, if we run from a source tree."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".octowright" / "config.yaml"
        if candidate.exists():
            return candidate
    return None


_CHECKOUT_PROJECT_CONFIG = _checkout_project_config()

# Bound at import, while ``os.stat`` is still the real one. Tests legitimately
# monkeypatch ``os.stat`` (tests/test_housekeeping.py patches it module-wide to
# fake a stat_result), and ``Path.stat()`` goes through that patch -- so a guard
# calling it would read a fabricated st_mtime of 0 and accuse an innocent test.
_REAL_OS_STAT = os.stat


@pytest.fixture(autouse=True)
def _guard_checkout_project_config() -> Iterator[None]:
    """Fail the test that rewrites the checkout's own project config.

    ``scaffold.scaffold_all`` defaults ``target_dir`` to ``Path.cwd()``, which is
    right for the real ``octowright init`` -- it scaffolds the project you are
    standing in. Under pytest the cwd is this checkout, so any test that invokes
    ``init`` without ``CliRunner.isolated_filesystem()`` rewrites the repo's own
    tracked ``.octowright/config.yaml``.

    That hid for a long time because ``write_project_config`` derives ``label``
    from the basename of ``git rev-parse --show-toplevel``: in a checkout named
    "octowright" the rewrite is byte-identical, so git reports nothing. It only
    surfaces in a git worktree, whose directory is named after the branch -- and
    there every ``git add -A`` after a test run silently commits a label change.

    Hence mtime, not content: content is identical in exactly the checkout where
    the suite is usually run. Per-test rather than per-session so the failure
    names the offending test instead of the whole run.
    """
    config = _CHECKOUT_PROJECT_CONFIG
    before = _REAL_OS_STAT(config).st_mtime_ns if config is not None else None
    yield
    if config is None or before is None:
        return
    if _REAL_OS_STAT(config).st_mtime_ns != before:
        raise AssertionError(
            f"this test rewrote {config}, the checkout's own project config. "
            "A CLI test that scaffolds must run inside CliRunner.isolated_filesystem(), "
            "or pass an explicit target_dir."
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
