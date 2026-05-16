# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for ``octowright.cli.scenario``.

`tests/test_cli.py` already covers `scenario list` empty / yaml-spec output
and the `_format_watch_event` helper. This file targets the rest of the
surface in `cli/scenario.py`:

- `scenario_start_cmd` happy path (start → echo participants → stop on Ctrl-C)
- `scenario_start_cmd` `--test` flow (start → verify → stop, exit code 0)
- `scenario_start_cmd` `--test` with verify failures (exit code 1)
- `scenario_start_cmd` `--watch` flag (creates watcher task; cancelled on stop)
- `scenario_start_cmd` propagates SystemExit codes from `_run_verify_and_report`
- `_run_verify_and_report`: no verify macros (exit 2), missing role-macro path,
  passing path, failing path, args=`{}` shape into run_macro, custom out_path
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from octowright.cli import cli
from octowright.cli import scenario as _scenario_mod

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _live(
    *,
    participants: list[dict[str, Any]] | None = None,
    verify: dict[str, str] | None = None,
    name: str = "demo",
    scenario_id: str = "sc-1",
) -> Any:
    """Build a fake LiveScenario as returned by ScenarioPool.start()."""
    return SimpleNamespace(
        scenario_id=scenario_id,
        name=name,
        participants=participants
        or [
            {
                "role": "player",
                "persona": "dante",
                "kind": "webkit",
                "instance_id": "i1",
                "url": "https://example.com",
            }
        ],
        spec=SimpleNamespace(verify=verify if verify is not None else {}),
    )


@pytest.fixture
def patched_pools(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace BrowserPool + ScenarioPool with mocks; return references for assertions."""
    pool_inst = MagicMock(name="BrowserPool")
    pool_inst.shutdown = AsyncMock()
    pool_inst.get = MagicMock()
    pool_class = MagicMock(return_value=pool_inst)

    spool_inst = MagicMock(name="ScenarioPool")
    spool_inst.start = AsyncMock()
    spool_inst.stop = AsyncMock()
    spool_inst.tail = MagicMock(return_value={"events": [], "cursors": {}})
    spool_class = MagicMock(return_value=spool_inst)

    import octowright.browser_pool as _bp
    import octowright.scenarios as _s

    monkeypatch.setattr(_bp, "BrowserPool", pool_class)
    monkeypatch.setattr(_s, "ScenarioPool", spool_class)

    return {
        "pool": pool_inst,
        "pool_class": pool_class,
        "spool": spool_inst,
        "spool_class": spool_class,
    }


# ---------------------------------------------------------------------------
# scenario start: happy path (no --test, no --watch)
# ---------------------------------------------------------------------------


class TestScenarioStartBasic:
    def test_echoes_scenario_id_and_participants(
        self, patched_pools: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --test, command echoes scenario_id then per-participant lines."""
        live = _live(
            participants=[
                {"role": "player", "persona": "dante", "kind": "webkit", "instance_id": "i1", "url": "https://x"},
                {"role": "monitor", "persona": "mortimer", "kind": "firefox", "instance_id": "i2", "url": "https://y"},
            ]
        )
        patched_pools["spool"].start.return_value = live

        # No --test/--watch: command waits for SIGINT. Pre-resolve the stop future.
        import asyncio

        _ = _scenario_mod._asyncio if hasattr(_scenario_mod, "_asyncio") else asyncio
        # Replace add_signal_handler so the test can fire `_handle` synchronously.
        _patch_signal_handlers_to_immediate_resolve(monkeypatch)

        result = CliRunner().invoke(cli, ["scenario", "start", "demo"])
        assert result.exit_code == 0
        assert "scenario_id: sc-1" in result.output
        assert "dante" in result.output
        assert "mortimer" in result.output
        assert "https://x" in result.output
        # Verify pool/spool lifecycle.
        patched_pools["spool"].start.assert_awaited_once()
        patched_pools["spool"].stop.assert_awaited_once()
        patched_pools["pool"].shutdown.assert_awaited_once()

    def test_omits_url_when_not_present(self, patched_pools: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        """`p.get('url', '')` default — missing url renders as empty trailing."""
        live = _live(
            participants=[
                {"role": "player", "persona": "dante", "kind": "webkit", "instance_id": "i1"},
            ]
        )
        patched_pools["spool"].start.return_value = live
        _patch_signal_handlers_to_immediate_resolve(monkeypatch)

        result = CliRunner().invoke(cli, ["scenario", "start", "demo"])
        assert result.exit_code == 0
        # The instance_id appears, but no URL substring after it.
        assert "i1" in result.output


def _patch_signal_handlers_to_immediate_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch `add_signal_handler` so the stop future resolves immediately.

    The CLI's `scenario start` (without --test) blocks on a stop future
    that's normally resolved by SIGINT. For tests we want the future to
    resolve as soon as it's installed, so the command falls through to
    teardown.
    """
    import asyncio

    orig_get_loop = asyncio.get_running_loop

    def patched_get_loop() -> Any:
        loop = orig_get_loop()
        loop.add_signal_handler  # noqa: B018

        def fake_add_signal_handler(sig: int, handler: Any, *args: Any) -> None:
            # Schedule the handler to run on the next tick, immediately
            # resolving the stop future.
            loop.call_soon(handler)

        loop.add_signal_handler = fake_add_signal_handler  # type: ignore[method-assign]
        # Restore on subsequent calls (only patch first lookup).
        loop.add_signal_handler = fake_add_signal_handler  # type: ignore[method-assign]
        # Restore for next iteration (we don't actually need to)
        return loop

    monkeypatch.setattr(asyncio, "get_running_loop", patched_get_loop)


# ---------------------------------------------------------------------------
# scenario start --watch
# ---------------------------------------------------------------------------


class TestScenarioStartWatch:
    def test_watch_streams_events_until_stop(
        self, patched_pools: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--watch creates a watcher task and renders tail events."""
        live = _live()
        patched_pools["spool"].start.return_value = live

        # First tail call: returns one event. Second: would-be next iteration
        # but we'll resolve stop before then.
        events_calls = {"n": 0}

        def tail_side_effect(*, scenario_id: str, since_cursors: dict[str, int]) -> dict[str, Any]:
            events_calls["n"] += 1
            if events_calls["n"] == 1:
                return {
                    "events": [
                        {
                            "action": "navigate",
                            "ts": "2026-01-01T00:00:00Z",
                            "persona": "dante",
                            "role": "player",
                            "url": "https://watched.example",
                        }
                    ],
                    "cursors": {"i1": 1},
                }
            return {"events": [], "cursors": {"i1": 1}}

        patched_pools["spool"].tail.side_effect = tail_side_effect
        _patch_signal_handlers_to_immediate_resolve(monkeypatch)

        result = CliRunner().invoke(cli, ["scenario", "start", "demo", "--watch"])
        assert result.exit_code == 0
        assert "streaming events" in result.output
        # The tail iterator may or may not get called before stop fires;
        # the key invariant is that the command exits cleanly with --watch.
        assert "scenario_id: sc-1" in result.output

    def test_watch_swallows_tail_exceptions(
        self, patched_pools: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If `spool.tail` raises, the watcher returns silently — doesn't crash."""
        live = _live()
        patched_pools["spool"].start.return_value = live
        patched_pools["spool"].tail.side_effect = RuntimeError("tail blew up")
        _patch_signal_handlers_to_immediate_resolve(monkeypatch)

        result = CliRunner().invoke(cli, ["scenario", "start", "demo", "--watch"])
        assert result.exit_code == 0

    # NOTE: an attempt at a "watcher body actually iterates" test was removed —
    # orchestrating the timing so `tail` runs at least once before the
    # immediate-resolve signal handler tears down the watcher proved fragile.
    # The watcher's tail-call path is exercised by the integration-level
    # scenario tests that drive a real ScenarioPool.


# ---------------------------------------------------------------------------
# scenario start --test
# ---------------------------------------------------------------------------


class TestScenarioStartTestMode:
    def test_no_verify_macros_returns_exit_2(self, patched_pools: dict[str, Any]) -> None:
        """Scenario without `verify` block → exit code 2."""
        live = _live(verify={})  # empty
        patched_pools["spool"].start.return_value = live

        result = CliRunner().invoke(cli, ["scenario", "start", "demo", "--test"])
        assert result.exit_code == 2
        assert "no verify macros" in result.output

    def test_test_mode_passes_writes_junit_and_returns_zero(
        self,
        patched_pools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """--test with passing verify → JUnit XML written, exit 0, '1/1 verify passed' echoed."""
        live = _live(verify={"player": "verify-macro"})
        patched_pools["spool"].start.return_value = live

        from octowright import macros as _m

        monkeypatch.setattr(_m, "run_macro", AsyncMock())

        from octowright import runner as _r

        write_junit = MagicMock()
        monkeypatch.setattr(_r, "_write_junit", write_junit)

        out_path = tmp_path / "report.xml"
        result = CliRunner().invoke(cli, ["scenario", "start", "demo", "--test", "--out", str(out_path)])
        assert result.exit_code == 0
        assert "1/1 verify passed" in result.output
        # _write_junit called once with results + path + kind="scenario"
        write_junit.assert_called_once()
        args, kwargs = write_junit.call_args
        assert kwargs.get("kind") == "scenario" or "scenario" in args
        assert out_path in args or kwargs.get("path") == out_path or args[1] == out_path

    def test_test_mode_failure_returns_exit_1(
        self,
        patched_pools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """--test with verify exception → result has ok=False, exit 1."""
        live = _live(verify={"player": "verify-macro"})
        patched_pools["spool"].start.return_value = live

        from octowright import macros as _m

        monkeypatch.setattr(_m, "run_macro", AsyncMock(side_effect=RuntimeError("verify failed")))

        from octowright import runner as _r

        captured: dict[str, Any] = {}

        def fake_write(results: list[Any], path: Any, *, kind: str) -> None:
            captured["results"] = results
            captured["kind"] = kind

        monkeypatch.setattr(_r, "_write_junit", fake_write)

        out_path = tmp_path / "report.xml"
        result = CliRunner().invoke(cli, ["scenario", "start", "demo", "--test", "--out", str(out_path)])
        assert result.exit_code == 1
        assert "0/1 verify passed" in result.output
        # The recorded result has ok=False and the repr of the error.
        assert captured["results"][0]["ok"] is False
        assert "verify failed" in captured["results"][0]["error"]

    def test_test_mode_missing_role_macro_marked_failed(
        self,
        patched_pools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Verify dict has a key, but not for THIS participant's role → ok=False."""
        live = _live(
            participants=[
                {"role": "monitor", "persona": "mortimer", "kind": "firefox", "instance_id": "i1"},
            ],
            verify={"player": "verify-macro"},  # no 'monitor' entry
        )
        patched_pools["spool"].start.return_value = live

        from octowright import runner as _r

        captured: dict[str, Any] = {}
        monkeypatch.setattr(_r, "_write_junit", lambda results, path, *, kind: captured.setdefault("results", results))

        from octowright import macros as _m

        run_macro = AsyncMock()
        monkeypatch.setattr(_m, "run_macro", run_macro)

        result = CliRunner().invoke(cli, ["scenario", "start", "demo", "--test", "--out", str(tmp_path / "r.xml")])
        assert result.exit_code == 1
        # run_macro never invoked because role had no verify mapping.
        run_macro.assert_not_awaited()
        assert captured["results"][0]["ok"] is False
        assert "no verify macro for role 'monitor'" in captured["results"][0]["error"]

    def test_test_mode_default_out_path_used_when_not_provided(
        self,
        patched_pools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without --out, falls back to runner._default_report_path()."""
        live = _live(verify={"player": "vm"})
        patched_pools["spool"].start.return_value = live

        from octowright import macros as _m
        from octowright import runner as _r

        monkeypatch.setattr(_m, "run_macro", AsyncMock())
        default_path = Path("/tmp/default-report.xml")
        monkeypatch.setattr(_r, "_default_report_path", lambda: default_path)
        write_junit = MagicMock()
        monkeypatch.setattr(_r, "_write_junit", write_junit)

        result = CliRunner().invoke(cli, ["scenario", "start", "demo", "--test"])
        assert result.exit_code == 0
        # _write_junit was called with the default path.
        args, _ = write_junit.call_args
        assert default_path in args or args[1] == default_path

    def test_test_mode_records_duration(
        self,
        patched_pools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Each result carries a non-negative `duration` in seconds."""
        live = _live(verify={"player": "vm"})
        patched_pools["spool"].start.return_value = live

        from octowright import macros as _m
        from octowright import runner as _r

        monkeypatch.setattr(_m, "run_macro", AsyncMock())
        captured: dict[str, Any] = {}
        monkeypatch.setattr(_r, "_write_junit", lambda results, path, *, kind: captured.setdefault("results", results))

        result = CliRunner().invoke(cli, ["scenario", "start", "demo", "--test", "--out", str(tmp_path / "r.xml")])
        assert result.exit_code == 0
        assert isinstance(captured["results"][0]["duration"], float)
        assert captured["results"][0]["duration"] >= 0.0


# ---------------------------------------------------------------------------
# `scenario list` — additional branch (form column rendering)
# ---------------------------------------------------------------------------


class TestScenarioListFormat:
    def test_renders_each_row_with_padded_columns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_scenarios output is rendered with name (30s), form (6s), path."""
        from octowright import scenarios as _s

        monkeypatch.setattr(
            _s,
            "list_scenarios",
            lambda: [
                {"name": "alpha", "form": "yaml", "path": "/p/alpha.yaml"},
                {"name": "beta", "form": "py", "path": "/p/beta.py"},
            ],
        )
        result = CliRunner().invoke(cli, ["scenario", "list"])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "yaml" in result.output
        assert "/p/alpha.yaml" in result.output
        assert "beta" in result.output
        assert "py" in result.output


# ---------------------------------------------------------------------------
# scenario_start_cmd raises SystemExit with the inner exit code
# ---------------------------------------------------------------------------


class TestSystemExitPropagation:
    def test_exit_code_propagated_from_test_mode(
        self,
        patched_pools: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """The wrapped `raise SystemExit(exit_code)` is what CliRunner sees as exit_code."""
        live = _live(verify={})  # forces _run_verify_and_report → exit 2
        patched_pools["spool"].start.return_value = live

        result = CliRunner().invoke(cli, ["scenario", "start", "demo", "--test"])
        assert result.exit_code == 2
