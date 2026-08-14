# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for BrowserPool + its helpers in browser_pool/.

Pins:
- LaunchOptions.validate() / promoted_profile() / session_name() / from_mapping()
- BrowserPool.get raises with the empty-pool vs known-ids hint
- maybe_get / has_session / active_count / iter_sessions / list_sessions /
  profile_in_use / _accept_external_close_nowait shape
- _build_launch_kwargs (chromium-headed-tile vs everything-else)
- _resolve_session_dir (None when session=False, tmp creation, key reuse,
  recreation when tmpdir vanishes)
- close_browser KeyError on missing, manifest-remove failure swallow
- handoff_browser stateless rejection, persistent close_original=False rejection
- close_all + spawn_roster (per-spec error captured without aborting siblings)
- shutdown_pool (close_all + pw stop + tmpdir cleanup)
- TestDurableCloseCoordinator: the durable, coalesced close coordinator
  (Task 7) -- caller cancellation vs. accepted-close durability, duplicate
  close identity coalescing, close_all's two-stage reserve-then-await,
  protection-race both orderings, external-close-wins-mid-drain, a broken
  gate's teardown-only path, and replayed Playwright close-listener no-ops.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool import BrowserPool
from octowright.browser_pool.errors import ProtectedBrowserCloseError
from octowright.browser_pool.events import SessionClosedEvent
from octowright.browser_pool.lifecycle import close_browser, close_with_preparation, shutdown_pool
from octowright.browser_pool.options import LaunchOptions
from octowright.browser_pool.relaunch import handoff_browser
from octowright.browser_pool.roster import close_all, spawn_roster
from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.session import BrowserSession
from octowright.session.operation_gate import SessionClosedError, SessionClosingError
from tests._pool_invariants import hold_operation, wait_for_active, wait_for_state, wait_until


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _fake_session(
    *,
    instance_id: str = "abc123",
    kind: str = "chromium",
    label: str | None = "demo",
    profile: str | None = "demo",
    url: str = "https://octowright.com",
    log_path: str = "/tmp/demo.jsonl",
    har_path: Any = None,
    user_data_dir: Any = None,
    stabilize: bool = False,
    protected: bool = False,
    trace: bool = False,
) -> Any:
    """A duck-typed session double carrying a REAL ``SessionOperationGate`` --
    the close coordinator (``lifecycle.py``) drives ``_operation_gate``
    directly, so a bare mock gate would not exercise its actual state
    machine. ``_teardown_after_close_cutoff`` stands in for the real
    session's teardown body (the coordinator calls it, never ``.close()``)."""
    from octowright.session.operation_gate import SessionOperationGate

    gate = SessionOperationGate(instance_id, kind)
    session = SimpleNamespace(
        instance_id=instance_id,
        kind=kind,
        label=label,
        profile=profile,
        url=url,
        log_path=log_path,
        har_path=har_path,
        user_data_dir=user_data_dir,
        stabilize=stabilize,
        protected=protected,
        protected_reason="explicit",
        trace=trace,
        page=SimpleNamespace(url=url),
        video_path=None,
        trace_path=None,
        close=AsyncMock(),
        _teardown_after_close_cutoff=AsyncMock(),
        _operation_gate=gate,
        _crashed=False,
    )
    # Compound helpers (capture-and-close/handoff/relaunch preparation
    # callbacks) call session.operation(...)/set_protected_state(...), not
    # the gate directly -- bind the same forwarding shape BrowserSession uses
    # (session/core.py) so this double exercises the identical call surface.
    session.operation = gate.operation
    session.operation_snapshot = gate.snapshot

    async def _set_protected_state(protected_value: bool, *, reason: str = "explicit") -> dict[str, object]:
        def _commit() -> dict[str, object]:
            session.protected = protected_value
            session.protected_reason = reason
            return {"instance_id": instance_id, "protected": protected_value}

        return await gate.control_update("browser_set_protected", _commit)

    session.set_protected_state = _set_protected_state
    return session


# ─── operation_queue_timeout_seconds ────────────────────────────────────────


class TestOperationQueueTimeoutSeconds:
    def test_default_resolves_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", raising=False)
        assert BrowserPool().operation_queue_timeout_seconds == 300.0

    def test_explicit_value_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", "99")
        pool = BrowserPool(operation_queue_timeout_seconds=17.0)
        assert pool.operation_queue_timeout_seconds == 17.0

    def test_env_value_used_when_not_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", "42")
        assert BrowserPool().operation_queue_timeout_seconds == 42.0

    def test_property_is_read_only(self) -> None:
        pool = BrowserPool()
        with pytest.raises(AttributeError):
            pool.operation_queue_timeout_seconds = 5.0  # type: ignore[misc]


# ─── LaunchOptions ───────────────────────────────────────────────────────────


class TestLaunchOptionsValidate:
    def test_unknown_kind_raises(self) -> None:
        """Mutating SUPPORTED_KINDS or the if-not-in check leaks bad kinds."""
        with pytest.raises(ValueError, match=r"kind must be one of"):
            LaunchOptions(kind="opera").validate()

    def test_unknown_badge_position_raises(self) -> None:
        """Unknown badge_position rejected with sorted-set hint."""
        with pytest.raises(ValueError, match=r"badge_position must be one of"):
            LaunchOptions(badge_position="middle").validate()

    def test_ephemeral_and_session_mutually_exclusive(self) -> None:
        """Both flags True → exact error message."""
        with pytest.raises(ValueError, match=r"ephemeral and session are mutually exclusive"):
            LaunchOptions(ephemeral=True, session=True).validate()

    def test_profile_and_session_mutually_exclusive(self) -> None:
        """profile + session=True rejected."""
        with pytest.raises(ValueError, match=r"profile and session are mutually exclusive"):
            LaunchOptions(profile="x", session=True).validate()

    def test_invalid_har_mode_raises(self) -> None:
        """har_mode allowed = {'full', 'minimal'} only."""
        with pytest.raises(ValueError, match=r"har_mode must be one of"):
            LaunchOptions(har_mode="medium").validate()

    def test_invalid_har_content_raises(self) -> None:
        """har_content allowed = {'omit', 'embed', 'attach'} (or None)."""
        with pytest.raises(ValueError, match=r"har_content must be one of"):
            LaunchOptions(har_content="external").validate()

    def test_har_content_none_allowed(self) -> None:
        """har_content=None passes (default value)."""
        LaunchOptions(har_content=None).validate()

    def test_default_passes(self) -> None:
        """Bare default LaunchOptions validates clean."""
        LaunchOptions().validate()


class TestLaunchOptionsPromotedProfile:
    def test_returns_explicit_profile_when_provided(self) -> None:
        """profile= explicit wins over label."""
        assert LaunchOptions(profile="explicit", label="lbl").promoted_profile() == "explicit"

    def test_promotes_label_when_no_profile(self) -> None:
        """label=X with no profile, no ephemeral, no session → returns label."""
        assert LaunchOptions(label="my-flow").promoted_profile() == "my-flow"

    def test_does_not_promote_when_ephemeral(self) -> None:
        """ephemeral=True → no promotion (returns None even with label)."""
        assert LaunchOptions(label="my-flow", ephemeral=True).promoted_profile() is None

    def test_does_not_promote_when_session(self) -> None:
        """session=True → no promotion."""
        assert LaunchOptions(label="my-flow", session=True).promoted_profile() is None

    def test_no_promotion_without_label(self) -> None:
        """No label and no profile → None."""
        assert LaunchOptions().promoted_profile() is None


class TestLaunchOptionsSessionName:
    def test_uses_label_when_present(self) -> None:
        """Named session reuses across launches under the same label."""
        assert LaunchOptions(label="demo").session_name("instX") == "demo"

    def test_falls_back_to_instance_id(self) -> None:
        """Anonymous session keys by instance_id (no cross-launch reuse)."""
        assert LaunchOptions().session_name("instX") == "instX"


class TestLaunchOptionsFromMapping:
    def test_unknown_keys_ignored(self) -> None:
        """Unknown options in dict don't crash from_mapping."""
        opts = LaunchOptions.from_mapping({"kind": "firefox", "extraneous": "drop me"})
        assert opts.kind == "firefox"

    def test_validate_called_during_construction(self) -> None:
        """from_mapping invokes validate() — bad input raises here."""
        with pytest.raises(ValueError):
            LaunchOptions.from_mapping({"kind": "opera"})

    def test_defaults_match_dataclass_defaults(self) -> None:
        """Bare {} produces the same shape as LaunchOptions()."""
        assert LaunchOptions.from_mapping({}) == LaunchOptions()


# ─── Pool public state API ───────────────────────────────────────────────────


class TestPoolLookupAPI:
    def test_get_raises_keyerror_with_empty_pool_hint(self) -> None:
        """Empty pool → 'no browsers are live — call browser_launch first'."""
        pool = BrowserPool()
        with pytest.raises(KeyError) as exc:
            pool.get("missing")
        assert "no browsers are live" in str(exc.value)
        assert "browser_launch" in str(exc.value)

    def test_get_raises_keyerror_with_known_ids_hint(self) -> None:
        """Non-empty pool but missing id → mentions browser_list + known ids."""
        pool = BrowserPool()
        pool._sessions["abc123"] = _fake_session()
        with pytest.raises(KeyError) as exc:
            pool.get("nope")
        msg = str(exc.value)
        assert "browser_list" in msg
        assert "abc123" in msg

    def test_get_returns_session_when_present(self) -> None:
        """Happy path: get returns the exact stored session."""
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions["abc123"] = sess
        assert pool.get("abc123") is sess

    def test_get_raises_with_relaunch_hint_for_externally_evicted_instance(self) -> None:
        """A browser dropped via the external-close/crash path
        (_accept_external_close_nowait) yields a 'relaunch' message, distinct
        from the generic 'no such id' — so an agent whose browser crashed
        knows it died rather than thinking it mistyped. Called with no
        running loop (a sync test), so no durable-teardown coordinator is
        created — the eviction itself is still synchronous."""
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions["abc123"] = sess
        pool._accept_external_close_nowait("abc123", expected_session=sess, reason="user_close")
        assert "abc123" not in pool._sessions  # the external-close path actually dropped it
        with pytest.raises(KeyError) as exc:
            pool.get("abc123")
        msg = str(exc.value)
        assert "abc123" in msg
        assert "relaunch" in msg.lower()

    def test_accept_external_close_miss_does_not_record(self) -> None:
        """A no-op eviction (id never live) must not mark the id as crashed —
        get() on a never-seen id keeps the plain 'no such id' message."""
        pool = BrowserPool()
        assert pool._accept_external_close_nowait("never") is None
        with pytest.raises(KeyError) as exc:
            pool.get("never")
        assert "relaunch" not in str(exc.value).lower()

    def test_maybe_get_missing_returns_none(self) -> None:
        """maybe_get is the no-raise variant."""
        assert BrowserPool().maybe_get("nope") is None

    def test_maybe_get_present_returns_session(self) -> None:
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions["x"] = sess
        assert pool.maybe_get("x") is sess

    def test_has_session_truthy_check(self) -> None:
        pool = BrowserPool()
        assert pool.has_session("nope") is False
        pool._sessions["x"] = _fake_session()
        assert pool.has_session("x") is True

    def test_iter_sessions_returns_tuple_snapshot(self) -> None:
        """iter_sessions returns a snapshot tuple — safe to mutate the dict afterward."""
        pool = BrowserPool()
        s1 = _fake_session(instance_id="a")
        s2 = _fake_session(instance_id="b")
        pool._sessions["a"] = s1
        pool._sessions["b"] = s2
        snapshot = pool.iter_sessions()
        assert isinstance(snapshot, tuple)
        # Mutating the dict afterwards shouldn't change the snapshot.
        pool._sessions.clear()
        assert len(snapshot) == 2

    def test_active_count_matches_dict_size(self) -> None:
        pool = BrowserPool()
        assert pool.active_count() == 0
        pool._sessions["a"] = _fake_session(instance_id="a")
        assert pool.active_count() == 1


class TestListSessions:
    def test_empty_pool_returns_empty_list(self) -> None:
        assert BrowserPool().list_sessions() == []

    def test_har_path_none_serialised_as_none(self) -> None:
        """har_path=None → None in the dict, not 'None' string."""
        pool = BrowserPool()
        pool._sessions["a"] = _fake_session(instance_id="a", har_path=None)
        rows = pool.list_sessions()
        assert rows[0]["har_path"] is None

    def test_har_path_present_str_coerced(self) -> None:
        """har_path Path → str(Path) for JSON-serialisability."""
        pool = BrowserPool()
        har = Path("/tmp/x.har")
        pool._sessions["a"] = _fake_session(instance_id="a", har_path=har)
        rows = pool.list_sessions()
        assert rows[0]["har_path"] == str(har)

    def test_log_path_always_str(self) -> None:
        """log_path always coerced to str."""
        pool = BrowserPool()
        pool._sessions["a"] = _fake_session(instance_id="a", log_path=Path("/tmp/x.jsonl"))
        rows = pool.list_sessions()
        assert isinstance(rows[0]["log_path"], str)

    def test_field_set_exact(self) -> None:
        """Each row carries exactly these fields — adding/dropping fields breaks."""
        pool = BrowserPool()
        pool._sessions["a"] = _fake_session(instance_id="a")
        rows = pool.list_sessions()
        assert set(rows[0]) == {"instance_id", "kind", "label", "profile", "url", "log_path", "har_path", "protected"}


class TestProfileInUse:
    def test_match_on_kind_and_profile(self) -> None:
        """profile_in_use is True only when BOTH kind and profile match."""
        pool = BrowserPool()
        pool._sessions["a"] = _fake_session(instance_id="a", kind="webkit", profile="cosmo one")
        assert pool.profile_in_use(kind="webkit", profile="cosmo one") is True
        assert pool.profile_in_use(kind="webkit", profile="cosmo-one") is True
        assert pool.profile_in_use(kind="chromium", profile="cosmo-one") is False
        assert pool.profile_in_use(kind="webkit", profile="ziggy") is False

    def test_empty_pool_returns_false(self) -> None:
        """Empty pool → never in use."""
        assert BrowserPool().profile_in_use(kind="chromium", profile="x") is False


class TestAcceptExternalCloseNowait:
    def test_removes_session_synchronously(self) -> None:
        """No running loop (a sync test) → the registry eviction still
        happens synchronously, even though no coordinator task is created."""
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions["x"] = sess
        result = pool._accept_external_close_nowait("x", expected_session=sess, reason="user_close")
        assert result is None  # no running loop to schedule durable cleanup on
        assert "x" not in pool._sessions
        assert sess._operation_gate.snapshot()["state"] == "closed"

    def test_missing_returns_none_no_raise(self) -> None:
        assert BrowserPool()._accept_external_close_nowait("nope") is None


# ─── _build_launch_kwargs ────────────────────────────────────────────────────


class TestBuildLaunchKwargs:
    @pytest.mark.anyio
    async def test_chromium_tile_headed_returns_args(self) -> None:
        """tile=True + chromium + headless=False → 'args' key with positional flags."""
        pool = BrowserPool()
        out = await pool._build_launch_kwargs(tile=True, kind="chromium", headless=False)
        assert "args" in out
        assert isinstance(out["args"], list)

    @pytest.mark.anyio
    async def test_increments_tile_counter(self) -> None:
        """Each headed-chromium tile launch advances _tile_counter."""
        pool = BrowserPool()
        await pool._build_launch_kwargs(tile=True, kind="chromium", headless=False)
        await pool._build_launch_kwargs(tile=True, kind="chromium", headless=False)
        assert pool._tile_counter == 2

    @pytest.mark.anyio
    async def test_no_args_when_tile_false(self) -> None:
        """tile=False + headed chromium → new-tab extension args, no tiling flags."""
        pool = BrowserPool()
        out = await pool._build_launch_kwargs(tile=False, kind="chromium", headless=False)
        assert "args" in out
        assert any("--load-extension" in a for a in out["args"])
        # No tiling flags present
        assert not any("--window-position" in a or "--window-size" in a for a in out["args"])

    @pytest.mark.anyio
    async def test_no_args_for_firefox(self) -> None:
        """Tiling is chromium-only; firefox returns empty dict."""
        pool = BrowserPool()
        out = await pool._build_launch_kwargs(tile=True, kind="firefox", headless=False)
        assert out == {}
        assert pool._tile_counter == 0  # not advanced

    @pytest.mark.anyio
    async def test_no_args_for_webkit(self) -> None:
        """Tiling is chromium-only; webkit returns empty dict."""
        pool = BrowserPool()
        out = await pool._build_launch_kwargs(tile=True, kind="webkit", headless=False)
        assert out == {}

    @pytest.mark.anyio
    async def test_no_args_when_headless_non_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Headless chromium on macOS/Windows: no extension args, no dev-shm flag."""
        monkeypatch.setattr(sys, "platform", "darwin")
        pool = BrowserPool()
        out = await pool._build_launch_kwargs(tile=True, kind="chromium", headless=True)
        assert out == {}
        # Tiling is meaningless headless — tile counter must not advance
        assert pool._tile_counter == 0

    @pytest.mark.anyio
    async def test_headless_linux_gets_dev_shm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Headless chromium on Linux gets --disable-dev-shm-usage (renderer-crash
        guard for small /dev/shm), but no extension/tile flags."""
        monkeypatch.setattr(sys, "platform", "linux")
        pool = BrowserPool()
        out = await pool._build_launch_kwargs(tile=True, kind="chromium", headless=True)
        assert out["args"] == ["--disable-dev-shm-usage"]
        assert not any("--load-extension" in a for a in out["args"])
        assert pool._tile_counter == 0  # tiling is meaningless headless

    @pytest.mark.anyio
    async def test_headed_linux_has_dev_shm_and_extension(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Headed chromium on Linux gets BOTH the dev-shm flag and the new-tab extension."""
        monkeypatch.setattr(sys, "platform", "linux")
        pool = BrowserPool()
        out = await pool._build_launch_kwargs(tile=False, kind="chromium", headless=False)
        assert "--disable-dev-shm-usage" in out["args"]
        assert any("--load-extension" in a for a in out["args"])

    @pytest.mark.anyio
    async def test_non_chromium_never_gets_dev_shm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The dev-shm flag is chromium-only even on Linux."""
        monkeypatch.setattr(sys, "platform", "linux")
        pool = BrowserPool()
        assert await pool._build_launch_kwargs(tile=False, kind="firefox", headless=True) == {}


# ─── _resolve_session_dir ────────────────────────────────────────────────────


class TestResolveSessionDir:
    @pytest.mark.anyio
    async def test_session_false_returns_none(self) -> None:
        """session=False short-circuits to None."""
        pool = BrowserPool()
        opts = LaunchOptions(session=False)
        assert await pool._resolve_session_dir(False, opts, "instX", "chromium") is None

    @pytest.mark.anyio
    async def test_creates_tmpdir_first_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """First session=True call mints a tmpdir under the prefix and stores it."""
        pool = BrowserPool()
        opts = LaunchOptions(session=True, label="demo")
        # Force tempfile.mkdtemp to use tmp_path so we can inspect.
        import tempfile

        captured: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def fake_mkdtemp(*, prefix: str) -> str:
            captured.append(prefix)
            return real_mkdtemp(prefix=prefix, dir=str(tmp_path))

        monkeypatch.setattr(tempfile, "mkdtemp", fake_mkdtemp)
        out = await pool._resolve_session_dir(True, opts, "instX", "chromium")
        assert out is not None
        assert "octowright-session-demo-chromium-" in captured[0]
        assert pool._session_profile_dirs[("demo", "chromium")] == Path(out)

    @pytest.mark.anyio
    async def test_reuses_existing_dir_for_same_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second call with same (label, kind) reuses the cached tmpdir."""
        pool = BrowserPool()
        opts = LaunchOptions(session=True, label="demo")
        import tempfile

        monkeypatch.setattr(tempfile, "mkdtemp", lambda *, prefix: str(tmp_path / "first"))
        # First call creates /first.
        Path(tmp_path / "first").mkdir()
        first = await pool._resolve_session_dir(True, opts, "instX", "chromium")
        # Second call: tmpdir still exists → reuse.
        second = await pool._resolve_session_dir(True, opts, "instY", "chromium")
        assert first == second

    @pytest.mark.anyio
    async def test_recreates_when_existing_dir_vanishes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the cached tmpdir was rm'd externally, _resolve_session_dir mints a fresh one."""
        pool = BrowserPool()
        opts = LaunchOptions(session=True, label="demo")
        import tempfile

        # Stage two distinct tmpdirs.
        d1 = tmp_path / "one"
        d2 = tmp_path / "two"
        d1.mkdir()
        d2.mkdir()
        seq = iter([str(d1), str(d2)])
        monkeypatch.setattr(tempfile, "mkdtemp", lambda *, prefix: next(seq))
        first = await pool._resolve_session_dir(True, opts, "instX", "chromium")
        assert first == str(d1)
        # Simulate tmpdir vanishing.
        import shutil

        shutil.rmtree(d1)
        second = await pool._resolve_session_dir(True, opts, "instY", "chromium")
        assert second == str(d2)

    @pytest.mark.anyio
    async def test_different_kinds_get_distinct_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same label + different engine kinds → distinct keys → distinct tmpdirs."""
        pool = BrowserPool()
        opts = LaunchOptions(session=True, label="demo")
        import tempfile

        seq = iter([str(tmp_path / "chrom"), str(tmp_path / "fire")])
        for d in (tmp_path / "chrom", tmp_path / "fire"):
            d.mkdir()
        monkeypatch.setattr(tempfile, "mkdtemp", lambda *, prefix: next(seq))
        chrom = await pool._resolve_session_dir(True, opts, "instX", "chromium")
        fire = await pool._resolve_session_dir(True, opts, "instX", "firefox")
        assert chrom != fire

    @pytest.mark.anyio
    async def test_anonymous_session_keys_by_instance_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No label → session key uses instance_id, so two anonymous launches don't share state."""
        pool = BrowserPool()
        opts = LaunchOptions(session=True, label=None)
        import tempfile

        seq = iter([str(tmp_path / "a"), str(tmp_path / "b")])
        for d in (tmp_path / "a", tmp_path / "b"):
            d.mkdir()
        monkeypatch.setattr(tempfile, "mkdtemp", lambda *, prefix: next(seq))
        a = await pool._resolve_session_dir(True, opts, "inst-A", "chromium")
        b = await pool._resolve_session_dir(True, opts, "inst-B", "chromium")
        assert a != b

    @pytest.mark.anyio
    async def test_concurrent_resolve_same_key_keeps_single_tmpdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Concurrent _resolve_session_dir calls with the same key must collapse
        to a single tmpdir on disk.

        Regression for the C8 race: ``spawn_roster`` gathers many ``_launch_one``
        coroutines; with the same session label they each enter
        ``_resolve_session_dir``. Without serialisation the read-then-create
        critical section can interleave such that every coroutine mints its
        own tmpdir, leaks all but one, and the registry only remembers the
        last winner.

        The deterministic trigger here parks every coroutine on a shared
        Event before they call ``_resolve_session_dir``, then patches
        ``tempfile.mkdtemp`` so that the FIRST mkdtemp invocation yields
        control to the event loop via ``await asyncio.sleep(0)``. Without
        the ``_sessions_lock`` serialisation in ``_resolve_session_dir``,
        that yield would let a second coroutine observe ``existing is None``
        and queue a duplicate mkdtemp. With the lock, the second coroutine
        blocks at the lock acquisition and never reaches the duplicate
        check.
        """
        import tempfile

        pool = BrowserPool()
        opts = LaunchOptions(session=True, label="roster-demo")
        created: list[str] = []
        real_mkdtemp = tempfile.mkdtemp
        first_call_done = asyncio.Event()

        def fake_mkdtemp(*, prefix: str) -> str:
            path = real_mkdtemp(prefix=prefix, dir=str(tmp_path))
            created.append(path)
            if len(created) == 1:
                # Schedule the gate flip without awaiting (mkdtemp is sync,
                # asyncio can only reach the gate when the next await
                # happens — which is the lock release in our fix).
                first_call_done.set()
            return path

        monkeypatch.setattr(tempfile, "mkdtemp", fake_mkdtemp)

        gate = asyncio.Event()

        async def _call() -> str | None:
            await gate.wait()
            return await pool._resolve_session_dir(True, opts, "inst-X", "chromium")

        coros = [_call() for _ in range(5)]
        task_group = asyncio.gather(*coros)
        await asyncio.sleep(0)
        gate.set()
        results = await task_group
        # Sanity: at least one mkdtemp ran.
        assert first_call_done.is_set()

        # Every caller got the SAME string back.
        assert len(set(results)) == 1
        winning = results[0]
        assert winning is not None

        # Exactly one tmpdir was minted: the lock kept later coros from
        # entering the create branch at all.
        assert created == [winning], f"expected exactly one mkdtemp call, got {created}"
        assert Path(winning).exists()
        assert pool._session_profile_dirs[("roster-demo", "chromium")] == Path(winning)


# ─── close_browser ───────────────────────────────────────────────────────────


class TestCloseBrowser:
    @pytest.mark.anyio
    async def test_missing_id_raises_keyerror(self) -> None:
        """KeyError mentions the missing instance and the empty-pool hint."""
        pool = BrowserPool()
        with pytest.raises(KeyError) as exc:
            await close_browser(pool, "nope")
        assert "no browsers are live" in str(exc.value)

    @pytest.mark.anyio
    async def test_pool_close_refuses_protected_without_force(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Protected sessions stay registered unless caller opts in with force=True."""
        pool = BrowserPool()
        sess = _fake_session(protected=True)
        pool._sessions[sess.instance_id] = sess

        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        with pytest.raises(ValueError, match=r"protected.*force=True"):
            await pool.close(sess.instance_id)

        assert pool._sessions[sess.instance_id] is sess
        sess._teardown_after_close_cutoff.assert_not_awaited()

    @pytest.mark.anyio
    async def test_pool_close_force_closes_protected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """force=True is the explicit protected-close override."""
        pool = BrowserPool()
        sess = _fake_session(protected=True)
        pool._sessions[sess.instance_id] = sess

        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        result = await pool.close(sess.instance_id, force=True)

        assert result["closed"] is True
        assert sess.instance_id not in pool._sessions
        sess._teardown_after_close_cutoff.assert_awaited_once()

    @pytest.mark.anyio
    async def test_evicts_session_before_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The session leaves _sessions once its ticket owns the gate, BEFORE
        the teardown body is awaited (see lifecycle._coordinate_close)."""
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions[sess.instance_id] = sess

        # Stub manifest removal so it doesn't touch real config.
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)

        await close_browser(pool, sess.instance_id)
        assert sess.instance_id not in pool._sessions
        sess._teardown_after_close_cutoff.assert_awaited_once()

    @pytest.mark.anyio
    async def test_manifest_remove_failure_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If remove_manifest_session raises, close still succeeds (warning logged)."""
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions[sess.instance_id] = sess

        from octowright.browser_pool import close_helpers as _lc

        def boom(_id: str) -> None:
            raise OSError("manifest write failed")

        monkeypatch.setattr(_lc, "remove_manifest_session", boom)
        result = await close_browser(pool, sess.instance_id)
        assert result["closed"] is True

    @pytest.mark.anyio
    async def test_returns_paths_as_strings_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """video_path/trace_path/har_path are str()-coerced when present."""
        pool = BrowserPool()
        video = Path("/tmp/v.webm")
        trace = Path("/tmp/t.zip")
        har = Path("/tmp/x.har")
        sess = _fake_session(har_path=har)
        sess.video_path = video
        sess.trace_path = trace
        pool._sessions[sess.instance_id] = sess

        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        result = await close_browser(pool, sess.instance_id)
        # Compare via str(Path(...)) so Windows back-slashes match.
        assert result["video_path"] == str(video)
        assert result["trace_path"] == str(trace)
        assert result["har_path"] == str(har)

    @pytest.mark.anyio
    async def test_returns_none_paths_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falsy paths return None (not 'None' string)."""
        pool = BrowserPool()
        sess = _fake_session(har_path=None)
        pool._sessions[sess.instance_id] = sess

        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        result = await close_browser(pool, sess.instance_id)
        assert result["video_path"] is None
        assert result["trace_path"] is None
        assert result["har_path"] is None


# ─── handoff_browser ─────────────────────────────────────────────────────────


class TestHandoffBrowser:
    @pytest.mark.anyio
    async def test_stateless_source_rejected_without_opt_in(self) -> None:
        """No profile + no user_data_dir + no accept_stateless → ValueError."""
        pool = BrowserPool()
        sess = _fake_session(profile=None, user_data_dir=None)
        pool._sessions[sess.instance_id] = sess
        with pytest.raises(ValueError, match=r"handoff would be stateless"):
            await handoff_browser(pool, sess.instance_id)

    @pytest.mark.anyio
    async def test_persistent_close_original_false_rejected(self) -> None:
        """Persistent (has profile) + close_original=False → exact ValueError."""
        pool = BrowserPool()
        sess = _fake_session(profile="cosmo", user_data_dir=Path("/tmp/x"))
        pool._sessions[sess.instance_id] = sess
        with pytest.raises(ValueError, match=r"persistent handoff requires close_original=True"):
            await handoff_browser(pool, sess.instance_id, close_original=False)

    @pytest.mark.anyio
    async def test_stateless_with_opt_in_proceeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """accept_stateless=True bypasses the stateless guard. Routes through
        the REAL close coordinator (Task 8: close_original=True no longer
        calls pool.close() directly) so the replacement launches from the
        preparation callback's RelaunchSnapshot, not a pre-close read."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        pool = BrowserPool()
        sess = _fake_session(profile=None, user_data_dir=None)
        pool._sessions[sess.instance_id] = sess
        pool.launch = AsyncMock(  # type: ignore[method-assign]
            return_value={"instance_id": "newX", "har_path": None}
        )
        result = await handoff_browser(pool, sess.instance_id, accept_stateless=True)
        assert result["ok"] is True
        assert result["new_instance_id"] == "newX"
        assert result["old_closed"] is True
        sess._teardown_after_close_cutoff.assert_awaited_once()


# ─── close_all + spawn_roster ───────────────────────────────────────────────


class TestCloseAll:
    @pytest.mark.anyio
    async def test_empty_pool_returns_empty_list(self) -> None:
        """Empty pool → {'closed': []}."""
        pool = BrowserPool()
        assert await close_all(pool) == {"closed": []}

    @pytest.mark.anyio
    async def test_closes_each_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every live session is reserved and torn down through the real
        two-stage coordinator (reserve-all, then await-all)."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        pool = BrowserPool()
        a = _fake_session(instance_id="a")
        b = _fake_session(instance_id="b")
        pool._sessions["a"] = a
        pool._sessions["b"] = b

        result = await close_all(pool)
        assert sorted(result["closed"]) == ["a", "b"]
        a._teardown_after_close_cutoff.assert_awaited_once()
        b._teardown_after_close_cutoff.assert_awaited_once()
        assert pool._closing_sessions == {}

    @pytest.mark.anyio
    async def test_force_passed_to_each_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """close_all(force=True) intentionally overrides protected sessions."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        pool = BrowserPool()
        a = _fake_session(instance_id="a", protected=True)
        pool._sessions["a"] = a

        result = await close_all(pool, force=True)
        assert result == {"closed": ["a"]}
        a._teardown_after_close_cutoff.assert_awaited_once()

    @pytest.mark.anyio
    async def test_skips_protected_without_force(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """close_all reports protected sessions skipped from the owner-layer guard."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        pool = BrowserPool()
        pool._sessions["a"] = _fake_session(instance_id="a", protected=True)
        pool._sessions["b"] = _fake_session(instance_id="b", protected=False)

        result = await close_all(pool)
        assert result["closed"] == ["b"]
        assert result["skipped_protected"] == ["a"]
        assert "force=True" in result["message"]
        # The refused protected session was never reserved -- still fully open.
        assert pool._sessions["a"].instance_id == "a"

    @pytest.mark.anyio
    async def test_reports_non_protected_close_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """close_all returns failures so callers can distinguish them from an empty pool."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        pool = BrowserPool()
        a = _fake_session(instance_id="a")
        b = _fake_session(instance_id="b")
        a._teardown_after_close_cutoff.side_effect = RuntimeError("page crashed")
        b._teardown_after_close_cutoff.side_effect = ValueError("not protected, just invalid")
        pool._sessions["a"] = a
        pool._sessions["b"] = b

        result = await close_all(pool)
        assert result["closed"] == []
        assert sorted(result["failed"], key=lambda row: row["instance_id"]) == [
            {"instance_id": "a", "error": "RuntimeError: page crashed"},
            {"instance_id": "b", "error": "ValueError: not protected, just invalid"},
        ]

    @pytest.mark.anyio
    async def test_shielded_rollback_force_closes(self) -> None:
        """Rollback cleanup must not be blocked by protected-session guards."""
        from octowright.browser_pool.roster import shielded_rollback_close

        pool = BrowserPool()
        called: list[tuple[str, bool]] = []

        async def fake_close(iid: str, *, force: bool = False, **_kw: Any) -> dict[str, Any]:
            called.append((iid, force))
            return {"closed": True}

        pool.close = fake_close  # type: ignore[method-assign]
        await shielded_rollback_close(pool, ["a"], logger=MagicMock(), event="rollback")
        assert called == [("a", True)]


class TestSpawnRoster:
    @pytest.mark.anyio
    async def test_per_spec_error_does_not_abort_siblings(self) -> None:
        """gather(..., return_exceptions=True) — failed launch is captured, others succeed."""
        pool = BrowserPool()
        results = [
            {"instance_id": "ok-1"},
            RuntimeError("boom"),
            {"instance_id": "ok-2"},
        ]
        seq = iter(results)

        async def fake_launch(**_kw: Any) -> dict[str, Any]:
            v = next(seq)
            if isinstance(v, BaseException):
                raise v
            return v

        pool.launch = fake_launch  # type: ignore[method-assign]
        out = await spawn_roster(
            pool,
            [{"kind": "chromium"}, {"kind": "firefox"}, {"kind": "webkit"}],
        )
        assert len(out["launched"]) == 2
        assert len(out["errors"]) == 1
        assert out["errors"][0]["error"] == "boom"
        assert out["errors"][0]["spec"] == {"kind": "firefox"}

    @pytest.mark.anyio
    async def test_empty_specs_returns_empty_lists(self) -> None:
        pool = BrowserPool()
        out = await spawn_roster(pool, [])
        assert out == {"launched": [], "errors": []}

    @pytest.mark.anyio
    async def test_passes_through_spec_fields(self) -> None:
        """All documented spec fields flow through to pool.launch."""
        pool = BrowserPool()
        captured: dict[str, Any] = {}

        async def fake_launch(**kw: Any) -> dict[str, Any]:
            captured.update(kw)
            return {"instance_id": "x"}

        pool.launch = fake_launch  # type: ignore[method-assign]
        await spawn_roster(
            pool,
            [
                {
                    "kind": "firefox",
                    "url": "https://x",
                    "label": "lbl",
                    "profile": "cosmo",
                    "viewport_w": 800,
                    "viewport_h": 600,
                    "stabilize": True,
                    "record_video": True,
                    "trace": True,
                }
            ],
        )
        assert captured["kind"] == "firefox"
        assert captured["url"] == "https://x"
        assert captured["label"] == "lbl"
        assert captured["profile"] == "cosmo"
        assert captured["viewport_w"] == 800
        assert captured["viewport_h"] == 600
        assert captured["stabilize"] is True
        assert captured["record_video"] is True
        assert captured["trace"] is True

    @pytest.mark.anyio
    async def test_default_kind_chromium(self) -> None:
        """Spec without 'kind' defaults to chromium."""
        pool = BrowserPool()
        captured: dict[str, Any] = {}

        async def fake_launch(**kw: Any) -> dict[str, Any]:
            captured.update(kw)
            return {"instance_id": "x"}

        pool.launch = fake_launch  # type: ignore[method-assign]
        await spawn_roster(pool, [{}])
        assert captured["kind"] == "chromium"


# ─── shutdown_pool ───────────────────────────────────────────────────────────


class TestShutdownPool:
    @pytest.mark.anyio
    async def test_calls_close_all_then_stops_pw(self) -> None:
        """close_all called, then pw.stop awaited, then _pw set to None."""
        pool = BrowserPool()
        order: list[str] = []
        pool.close_all = AsyncMock(side_effect=lambda **_kw: order.append("close_all"))  # type: ignore[method-assign]
        fake_pw = MagicMock()
        fake_pw.stop = AsyncMock(side_effect=lambda: order.append("stop"))
        pool._pw = fake_pw
        await shutdown_pool(pool)
        assert order == ["close_all", "stop"]
        pool.close_all.assert_awaited_once_with(_reason="shutdown", force=True)
        assert pool._pw is None

    @pytest.mark.anyio
    async def test_skips_pw_stop_when_unset(self) -> None:
        """If _pw was never started, no stop is awaited."""
        pool = BrowserPool()
        pool.close_all = AsyncMock()  # type: ignore[method-assign]
        # _pw is None by default — must not crash.
        await shutdown_pool(pool)

    @pytest.mark.anyio
    async def test_clears_session_profile_dirs(self, tmp_path: Path) -> None:
        """tmpdirs from session=True launches are rm-rf'd and the dict is emptied."""
        pool = BrowserPool()
        pool.close_all = AsyncMock()  # type: ignore[method-assign]
        d = tmp_path / "session-tmp"
        d.mkdir()
        pool._session_profile_dirs[("demo", "chromium")] = d
        await shutdown_pool(pool)
        assert pool._session_profile_dirs == {}
        assert not d.exists()

    @pytest.mark.anyio
    async def test_holds_sessions_lock_while_clearing_session_profile_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Shutdown must hold ``_sessions_lock`` across the snapshot+clear of
        ``_session_profile_dirs``. If the lock isn't held, a concurrent task
        blocked on ``_sessions_lock`` could insert a tmpdir during the
        iterate-then-clear window — leaking it.

        Approach: pre-acquire the lock from a "concurrent" coroutine. Spawn
        ``shutdown_pool`` while the lock is held; assert it blocks. Then
        release; assert shutdown completes and the dict is empty.
        """
        pool = BrowserPool()
        pool.close_all = AsyncMock()  # type: ignore[method-assign]
        existing = tmp_path / "existing"
        existing.mkdir()
        pool._session_profile_dirs[("demo", "chromium")] = existing

        release_lock = asyncio.Event()
        holding_lock = asyncio.Event()

        async def lock_holder() -> None:
            async with pool._sessions_lock:
                holding_lock.set()
                await release_lock.wait()
                # Inserting under the lock simulates a concurrent
                # _resolve_session_dir that's racing shutdown.
                new_tmp = tmp_path / "raced"
                new_tmp.mkdir()
                pool._session_profile_dirs[("raced", "chromium")] = new_tmp

        holder_task = asyncio.create_task(lock_holder())
        await holding_lock.wait()
        shutdown_task = asyncio.create_task(shutdown_pool(pool))
        # Give the scheduler a chance — shutdown should be blocked on the lock.
        await asyncio.sleep(0.01)
        assert not shutdown_task.done(), "shutdown_pool acquired _sessions_lock while it was held — lock not honored"
        # Pre-existing entry is still present; cleanup hasn't run yet.
        assert existing.exists()
        # Release the holder. shutdown can now acquire, snapshot+clear under
        # the lock, then rmtree the snapshotted entries. The "raced" entry
        # added under the same lock by the holder is included in the snapshot.
        release_lock.set()
        await holder_task
        await shutdown_task
        assert pool._session_profile_dirs == {}
        assert not existing.exists()

    @pytest.mark.anyio
    async def test_tmpdir_oserror_swallowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """rmtree raising OSError doesn't propagate (best-effort cleanup)."""
        pool = BrowserPool()
        pool.close_all = AsyncMock()  # type: ignore[method-assign]
        d = tmp_path / "session-tmp"
        d.mkdir()
        pool._session_profile_dirs[("demo", "chromium")] = d

        # Patch shutil.rmtree to raise OSError despite ignore_errors=True (synthetic).
        from octowright.browser_pool import lifecycle as _lc

        def boom(*args: Any, **kw: Any) -> None:
            raise OSError("filesystem full")

        monkeypatch.setattr(_lc.shutil, "rmtree", boom)
        # Must not raise.
        await shutdown_pool(pool)
        assert pool._session_profile_dirs == {}


# ─── Concurrency / lock guarantees ───────────────────────────────────────────


class TestSafeCleanupOnLaunchFailure:
    """_safe_cleanup_on_launch_failure is the shared best-effort teardown
    for both launch() failure paths. PR #20 fixed the happy-path persistent-
    context browser leak; the same fallback (`getattr(context, 'browser',
    None)`) needs to fire on launch-failure cleanup, and the empty per-
    launch video_dir under RECORDINGS_DIR/videos/<hex>/ needs to be removed
    so failed launches don't leak directories."""

    @pytest.mark.anyio
    async def test_empty_video_dir_removed(self, tmp_path: Path) -> None:
        """A failed launch with record_video=True must remove the empty
        video_dir it created — otherwise the recordings tree accumulates
        an orphan hex-named directory per failed launch."""
        from octowright.browser_pool.pool import _safe_cleanup_on_launch_failure

        video_dir = tmp_path / "videos" / "abc12345"
        video_dir.mkdir(parents=True)
        await _safe_cleanup_on_launch_failure(context=None, browser=None, video_dir=video_dir)
        assert not video_dir.exists()

    @pytest.mark.anyio
    async def test_non_empty_video_dir_preserved(self, tmp_path: Path) -> None:
        """A partial launch that managed to record SOME video must keep
        the directory — the user may still want the partial artefact for
        debugging. Only empty dirs are removed."""
        from octowright.browser_pool.pool import _safe_cleanup_on_launch_failure

        video_dir = tmp_path / "videos" / "def67890"
        video_dir.mkdir(parents=True)
        (video_dir / "page.webm").write_bytes(b"\x00fake")
        await _safe_cleanup_on_launch_failure(context=None, browser=None, video_dir=video_dir)
        assert video_dir.exists()
        assert (video_dir / "page.webm").exists()

    @pytest.mark.anyio
    async def test_video_dir_none_no_op(self) -> None:
        """video_dir=None (record_video=False launches) is a no-op."""
        from octowright.browser_pool.pool import _safe_cleanup_on_launch_failure

        await _safe_cleanup_on_launch_failure(context=None, browser=None, video_dir=None)

    @pytest.mark.anyio
    async def test_persistent_context_browser_fallback(self) -> None:
        """When browser is None (persistent-context launch path) the cleanup
        must still close `context.browser` — PR #20 established this is the
        only way to reliably reap the persistent browser process across all
        Playwright versions. Without the fallback, a launch that fails mid-
        wire-up leaks the browser process."""
        from octowright.browser_pool.pool import _safe_cleanup_on_launch_failure

        persistent_browser = MagicMock()
        persistent_browser.close = AsyncMock()
        context = MagicMock()
        context.close = AsyncMock()
        context.browser = persistent_browser

        await _safe_cleanup_on_launch_failure(context=context, browser=None, video_dir=None)
        context.close.assert_awaited_once()
        persistent_browser.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_standalone_browser_closed_directly(self) -> None:
        """Ephemeral launches: both context and browser handles are present;
        each gets its own close call. The persistent fallback must NOT fire
        when browser is already set (we'd double-close)."""
        from octowright.browser_pool.pool import _safe_cleanup_on_launch_failure

        context = MagicMock()
        context.close = AsyncMock()
        # context.browser would normally be the same Browser as `browser`
        # for ephemeral contexts. Set it to a distinct mock to detect the
        # accidental double-close case.
        context.browser = MagicMock()
        context.browser.close = AsyncMock()
        browser = MagicMock()
        browser.close = AsyncMock()

        await _safe_cleanup_on_launch_failure(context=context, browser=browser, video_dir=None)
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        # Persistent-fallback path skipped because browser is non-None.
        context.browser.close.assert_not_awaited()

    @pytest.mark.anyio
    async def test_close_errors_swallowed(self, tmp_path: Path) -> None:
        """All close calls are best-effort — the caller is already
        propagating the launch exception, so cleanup errors must not
        replace or mask it."""
        from octowright.browser_pool.pool import _safe_cleanup_on_launch_failure

        context = MagicMock()
        context.close = AsyncMock(side_effect=RuntimeError("ctx boom"))
        browser = MagicMock()
        browser.close = AsyncMock(side_effect=RuntimeError("browser boom"))
        video_dir = tmp_path / "videos" / "x"
        video_dir.mkdir(parents=True)

        # Must not raise.
        await _safe_cleanup_on_launch_failure(context=context, browser=browser, video_dir=video_dir)

    @pytest.mark.anyio
    async def test_cleanup_completes_under_cancellation(self) -> None:
        """A cancellation arriving mid-cleanup must not abort the sequence and
        leak the browser: every close must still run to completion before the
        cancellation is delivered."""
        import anyio

        from octowright.browser_pool.pool import _safe_cleanup_on_launch_failure

        started = anyio.Event()
        release = anyio.Event()
        closed: list[str] = []

        async def _ctx_close() -> None:
            started.set()
            await release.wait()
            closed.append("context")

        async def _browser_close() -> None:
            closed.append("browser")

        context = MagicMock()
        context.close = _ctx_close
        browser = MagicMock()
        browser.close = _browser_close

        holder: dict[str, anyio.CancelScope] = {}

        async def _run() -> None:
            with anyio.CancelScope() as scope:
                holder["scope"] = scope
                await _safe_cleanup_on_launch_failure(context=context, browser=browser, video_dir=None)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run)
            await started.wait()
            # Cancel while the first close is in flight, then let it proceed.
            holder["scope"].cancel()
            release.set()

        assert closed == ["context", "browser"]


class TestSessionsLock:
    @pytest.mark.anyio
    async def test_close_browser_acquires_sessions_lock(self) -> None:
        """close_browser pops via the lock; verify the lock is awaited."""
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions[sess.instance_id] = sess

        # Stub manifest removal.
        from unittest.mock import patch

        from octowright.browser_pool import close_helpers as _lc

        with patch.object(_lc, "remove_manifest_session", lambda _id: None):
            # If the lock weren't acquired, parallel pop would race; here we
            # just verify the lock object isn't replaced and the call succeeds.
            assert isinstance(pool._sessions_lock, asyncio.Lock)
            await close_browser(pool, sess.instance_id)
        assert sess.instance_id not in pool._sessions


# ─── durable, coalesced close coordinator (Task 7) ──────────────────────────
#
# These tests use a REAL BrowserSession (not the SimpleNamespace double
# above) with a mocked context/page/recorder, so the coordinator's actual
# gate transitions (reserve_close / close_operation / complete_close /
# fail_close) and real ``_teardown_after_close_cutoff`` body run for real.


def _real_session(*, instance_id: str, tmp_path: Path, protected: bool = False) -> BrowserSession:
    context = MagicMock()
    context.close = AsyncMock()
    context.tracing = MagicMock()
    context.on = MagicMock()
    page = MagicMock()
    return BrowserSession(
        instance_id=instance_id,
        kind="chromium",
        label=None,
        url="https://octowright.com",
        browser=None,
        context=context,
        page=page,
        recorder=MagicMock(),
        log_path=tmp_path / f"{instance_id}.jsonl",
        protected=protected,
    )


@pytest.fixture
def pool() -> BrowserPool:
    return BrowserPool()


@pytest.fixture
def session(pool: BrowserPool, tmp_path: Path) -> BrowserSession:
    sess = _real_session(instance_id="sess-1", tmp_path=tmp_path)
    pool._sessions[sess.instance_id] = sess
    return sess


@dataclass
class PoolPair:
    pool: BrowserPool
    first: BrowserSession
    second: BrowserSession
    release_first: asyncio.Event
    release_second: asyncio.Event


@pytest.fixture
def pool_with_two_sessions(pool: BrowserPool, tmp_path: Path) -> PoolPair:
    # Deliberately a SYNC fixture: an async fixture alongside this file's
    # @pytest.mark.anyio tests risks the fixture and the test running on two
    # different event loops under anyio's plugin. The "hold each gate busy"
    # setup instead happens explicitly inside the one test that needs it.
    first = _real_session(instance_id="first", tmp_path=tmp_path)
    second = _real_session(instance_id="second", tmp_path=tmp_path)
    pool._sessions[first.instance_id] = first
    pool._sessions[second.instance_id] = second
    return PoolPair(
        pool=pool,
        first=first,
        second=second,
        release_first=asyncio.Event(),
        release_second=asyncio.Event(),
    )


class TestDurableCloseCoordinator:
    @pytest.mark.anyio
    async def test_cancelled_close_caller_does_not_cancel_accepted_close(
        self, pool: BrowserPool, session: BrowserSession
    ) -> None:
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

    @pytest.mark.anyio
    async def test_duplicate_close_callers_share_one_error(self, pool: BrowserPool, session: BrowserSession) -> None:
        teardown_error = RuntimeError("teardown failed")
        session.context.close.side_effect = teardown_error
        first = asyncio.create_task(pool.close(session.instance_id, force=True))
        await wait_for_state(session._operation_gate, "closing")
        second = asyncio.create_task(pool.close(session.instance_id, force=True))
        first_result, second_result = await asyncio.gather(first, second, return_exceptions=True)
        assert first_result is teardown_error
        assert second_result is teardown_error
        assert session.operation_snapshot()["state"] == "closed"
        await wait_until(lambda: session.instance_id not in pool._closing_sessions)

    @pytest.mark.anyio
    async def test_duplicate_close_callers_share_one_success(self, pool: BrowserPool, session: BrowserSession) -> None:
        """The happy-path twin of the error-coalescing test above: two
        concurrent callers for the same identity get the SAME response dict
        (one coordinator, one outcome), and the context is only closed once."""
        first = asyncio.create_task(pool.close(session.instance_id, force=True))
        await wait_for_state(session._operation_gate, "closing")
        second = asyncio.create_task(pool.close(session.instance_id, force=True))
        first_result, second_result = await asyncio.gather(first, second)
        assert first_result == second_result
        session.context.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_close_all_reserves_every_session_before_waiting(self, pool_with_two_sessions: PoolPair) -> None:
        pair = pool_with_two_sessions
        # Hold each session's gate busy so close_all's reservation is
        # accepted (state -> closing) but not yet granted -- the whole point
        # of the two-stage close_all test is proving the SECOND reservation
        # isn't blocked behind the FIRST session's still-active operation.
        first_holder = asyncio.create_task(hold_operation(pair.first, "long_action", pair.release_first))
        second_holder = asyncio.create_task(hold_operation(pair.second, "long_action", pair.release_second))
        await wait_for_active(pair.first._operation_gate, "long_action")
        await wait_for_active(pair.second._operation_gate, "long_action")

        result_task = asyncio.create_task(pair.pool.close_all(force=True))
        await wait_for_state(pair.first._operation_gate, "closing")
        await wait_for_state(pair.second._operation_gate, "closing")
        assert not result_task.done()
        pair.release_first.set()
        pair.release_second.set()
        result = await result_task
        assert set(result["closed"]) == {pair.first.instance_id, pair.second.instance_id}
        assert pair.pool._closing_sessions == {}
        await first_holder
        await second_holder

    @pytest.mark.anyio
    async def test_protection_commits_first_then_unforced_close_is_refused(
        self, pool: BrowserPool, session: BrowserSession
    ) -> None:
        """Protection wins the race: it commits before any close reservation
        exists, so an unforced close is refused and the gate stays open."""
        await session.set_protected_state(True)
        with pytest.raises(ProtectedBrowserCloseError):
            await pool.close(session.instance_id)
        assert session.operation_snapshot()["state"] == "open"
        assert pool.get(session.instance_id) is session

    @pytest.mark.anyio
    async def test_close_reserves_first_then_late_protection_check_is_moot(
        self, pool: BrowserPool, session: BrowserSession
    ) -> None:
        """Close reserves the cutoff first (force=True, so its own preflight
        passes); a caller that then tries to read/mutate protected state
        through the gate sees SessionClosingError, not a stale open gate."""
        active_release = asyncio.Event()
        active = asyncio.create_task(hold_operation(session, "long_action", active_release))
        await wait_for_active(session._operation_gate, "long_action")
        close_task = asyncio.create_task(pool.close(session.instance_id, force=True))
        await wait_for_state(session._operation_gate, "closing")
        with pytest.raises(SessionClosingError):
            pool.get(session.instance_id)
        active_release.set()
        await active
        result = await close_task
        assert result["closed"] is True

    @pytest.mark.anyio
    async def test_external_close_wins_while_explicit_close_drains(
        self, pool: BrowserPool, session: BrowserSession
    ) -> None:
        """Playwright's own close signal fires while an accepted explicit
        close is still waiting on its FIFO turn: the SAME coordinator takes
        the teardown-only branch instead of a second one spinning up."""
        release = asyncio.Event()
        holder = asyncio.create_task(hold_operation(session, "long_action", release))
        await wait_for_active(session._operation_gate, "long_action")

        close_task = asyncio.create_task(pool.close(session.instance_id, force=True))
        await wait_for_state(session._operation_gate, "closing")

        won = pool._accept_external_close_nowait(session.instance_id, expected_session=session, reason="user_close")
        assert won is pool._closing_sessions[session.instance_id]
        assert session.instance_id not in pool._sessions

        release.set()
        await holder
        result = await close_task
        assert result["closed"] is True
        session.context.close.assert_awaited_once()
        await wait_until(lambda: session.instance_id not in pool._closing_sessions)

    @pytest.mark.anyio
    async def test_close_on_broken_gate_runs_teardown_only(self, pool: BrowserPool, session: BrowserSession) -> None:
        """A broken gate still gets a durable, once-only teardown -- the
        close reservation takes the ready/teardown_only path immediately."""
        session._operation_gate._break_locked("test-induced break")
        result = await pool.close(session.instance_id, force=True)
        assert result["closed"] is True
        session.context.close.assert_awaited_once()
        assert session._operation_gate.snapshot()["state"] == "closed"
        await wait_until(lambda: session.instance_id not in pool._closing_sessions)

    @pytest.mark.anyio
    async def test_explicit_close_ignores_replayed_playwright_close_signal(
        self, pool: BrowserPool, session: BrowserSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_teardown_after_close_cutoff`` calling ``context.close()`` fires
        every normal Playwright close listener wired on it (real Playwright
        behavior). Those listeners must not overwrite the explicit reason,
        change coordinator ownership mid-teardown, or publish a second event
        -- whether they fire as a side effect of the coordinator's own
        context.close() or are replayed afterward."""
        from octowright.browser_pool.listeners import _wire_close_evictor

        monkeypatch.setattr("octowright.browser_pool.close_helpers.remove_manifest_session", lambda _id: None)
        events: list[Any] = []
        monkeypatch.setattr(session_event_bus, "publish_nowait", events.append)

        _wire_close_evictor(pool, session)

        def _fire_registered_close(*_a: Any, **_kw: Any) -> None:
            for call in session.context.on.call_args_list:
                if call.args[0] == "close":
                    call.args[1]()

        session.context.close.side_effect = _fire_registered_close

        result = await pool.close(session.instance_id, force=True)
        assert result["closed"] is True
        # Replay the same signal again after the coordinator finished.
        _fire_registered_close()

        closed_events = [e for e in events if isinstance(e, SessionClosedEvent)]
        assert len(closed_events) == 1, closed_events
        assert closed_events[0].reason == "agent_close"
        assert pool._closing_sessions == {}

    @pytest.mark.anyio
    async def test_external_close_racing_reserve_close_admission_does_not_double_coordinate(
        self, pool: BrowserPool, session: BrowserSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``gate.reserve_close`` takes the gate's own ``_admission_lock``,
        which can suspend ``reserve_close_browser`` for a full loop turn
        while it STILL holds ``pool._sessions_lock`` (contended by any other
        gated operation on this session). The synchronous external-close
        acceptance seam never takes ``_admission_lock``, so it can win the
        close race in exactly that window: mark the gate closed and install
        its own ``ClosingSession`` wrapping the SAME reservation
        ``reserve_close`` then hands back to the parked caller (its own
        ``_close_reservation is not None`` short-circuit). Only ONE
        coordinator must ever run -- not two wrapping the identical
        reservation."""
        from octowright.browser_pool.lifecycle import reserve_close_browser

        events: list[Any] = []
        monkeypatch.setattr(session_event_bus, "publish_nowait", events.append)

        admission_lock = session._operation_gate._admission_lock
        await admission_lock.acquire()
        try:
            reserve_task = asyncio.create_task(
                reserve_close_browser(pool, session.instance_id, force=True, reason="agent_close")
            )
            # Let reserve_close_browser run up to (and park on) gate.reserve_close's
            # admission-lock wait -- it's held, so this is where it suspends.
            await asyncio.sleep(0)
            assert not reserve_task.done()
            # Win the close race while reserve_close_browser is parked: the
            # synchronous acceptance seam never touches _admission_lock.
            won = pool._accept_external_close_nowait(session.instance_id, expected_session=session, reason="user_close")
            assert won is not None
        finally:
            admission_lock.release()

        entry = await reserve_task
        assert entry is won, "a second ClosingSession was installed over the already-accepted external one"
        outcome = await entry.reservation.wait()
        assert outcome.response["closed"] is True

        session.context.close.assert_awaited_once()
        close_rows = [call for call in session.recorder.record.call_args_list if call.args[0] == "close"]
        assert len(close_rows) == 1, close_rows
        session.recorder.close.assert_called_once()
        closed_events = [e for e in events if isinstance(e, SessionClosedEvent)]
        assert len(closed_events) == 1, closed_events
        assert closed_events[0].reason == "user_close"
        await wait_until(lambda: session.instance_id not in pool._closing_sessions)

    @pytest.mark.anyio
    async def test_coordinator_counts_explicit_close_only_but_spans_both(
        self, pool: BrowserPool, session: BrowserSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``octowright_browser_closed_total`` and the ``octowright.session.close``
        span are owned by the close coordinator (not ``SessionOpsMixin.close()``,
        which no production close routes through anymore).

        Per spec: the counter stays disjoint from ``octowright_browser_
        evicted_total`` (so ``launched - closed - evicted`` keeps meaning
        "still live") and fires ONLY for an explicit ``pool.close()`` --
        never for an external-origin reason. The span isn't summed, so it
        fires for EVERY close (explicit or external), carrying ``reason`` as
        an attribute so the two stay filterable in a trace backend despite
        sharing one span name."""
        from octowright.browser_pool import lifecycle as _lifecycle

        counted: list[tuple[int, dict[str, str] | None]] = []
        monkeypatch.setattr(
            _lifecycle._SESSION_CLOSED, "add", lambda amount, attributes=None: counted.append((amount, attributes))
        )
        spans: list[tuple[str, dict[str, object]]] = []

        class _FakeSpanCtx:
            def __enter__(self) -> _FakeSpanCtx:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

        def _fake_span(name: str, **kw: object) -> _FakeSpanCtx:
            spans.append((name, kw))
            return _FakeSpanCtx()

        monkeypatch.setattr(_lifecycle, "span", _fake_span)

        # Explicit close: counter fires once; span fires once with reason=agent_close.
        result = await pool.close(session.instance_id, force=True)
        assert result["closed"] is True
        assert counted == [(1, {"kind": session.kind})]
        assert len(spans) == 1
        assert spans[0][0] == "octowright.session.close"
        assert spans[0][1]["reason"] == "agent_close"

        # External close: counter must NOT fire again; span fires again with
        # reason=user_close.
        second = _real_session(instance_id="sess-2", tmp_path=tmp_path)
        pool._sessions[second.instance_id] = second
        won = pool._accept_external_close_nowait(second.instance_id, expected_session=second, reason="user_close")
        assert won is not None
        await won.reservation.wait()

        assert counted == [(1, {"kind": session.kind})], "external close must not bump the closed-total counter"
        assert len(spans) == 2
        assert spans[1][0] == "octowright.session.close"
        assert spans[1][1]["reason"] == "user_close"


# ─── _coordinate_close finally-block resilience (hardening, Task 8 review) ──


class TestCloseCoordinatorFinallyResilience:
    """A secondary exception inside ``_coordinate_close``'s ``finally``
    block used to be able to escape the coordinator entirely, skipping
    ``complete_close``/``fail_close`` (stranding every ``reservation.wait()``
    caller forever) and the ``_closing_sessions`` pop (permanently poisoning
    the instance_id). ``close_helpers.run_close_bookkeeping``/
    ``resolve_close_outcome`` now contain that -- these pin the guarantee
    directly, independent of the compound-operation (preparation-callback)
    machinery above."""

    @pytest.mark.anyio
    async def test_secondary_finally_failure_does_not_strand_reservation(
        self, pool: BrowserPool, session: BrowserSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A secondary exception in the finally block's own bookkeeping
        (e.g. a raising log handler) becomes the close's error rather than
        propagating -- the caller sees a clean exception, not a hang."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)

        def _raising_publish(*_a: object, **_kw: object) -> None:
            raise RuntimeError("publish blew up")

        monkeypatch.setattr(_lc, "publish_close_once", _raising_publish)

        with pytest.raises(RuntimeError, match="publish blew up"):
            await asyncio.wait_for(pool.close(session.instance_id, force=True), timeout=2.0)

        await wait_until(lambda: session.instance_id not in pool._closing_sessions)

    @pytest.mark.anyio
    async def test_primary_close_error_wins_over_secondary_finally_failure(
        self, pool: BrowserPool, session: BrowserSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When teardown itself fails AND the finally block's own
        bookkeeping also fails, the caller sees the ORIGINAL teardown error
        -- the secondary failure is logged, never substituted in its place."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        primary_error = RuntimeError("teardown failed")
        session.context.close.side_effect = primary_error

        def _raising_publish(*_a: object, **_kw: object) -> None:
            raise RuntimeError("publish blew up too")

        monkeypatch.setattr(_lc, "publish_close_once", _raising_publish)

        with pytest.raises(RuntimeError, match="teardown failed"):
            await asyncio.wait_for(pool.close(session.instance_id, force=True), timeout=2.0)

        await wait_until(lambda: session.instance_id not in pool._closing_sessions)

    @pytest.mark.anyio
    async def test_response_computation_failure_does_not_hang(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A session double missing an attribute close_response needs (the
        exact failure mode that motivated this hardening -- see Task 8's
        report) fails the close cleanly with a bounded timeout instead of
        hanging every caller of reservation.wait() forever."""
        from octowright.browser_pool import close_helpers as _lc
        from octowright.session.operation_gate import SessionOperationGate

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        pool = BrowserPool()
        broken = SimpleNamespace(
            instance_id="broken1",
            kind="chromium",
            protected=False,
            protected_reason="explicit",
            _teardown_after_close_cutoff=AsyncMock(),
            _operation_gate=SessionOperationGate("broken1", "chromium"),
            # log_path/video_path/trace_path deliberately OMITTED --
            # close_response(session) raises AttributeError reading them.
        )
        pool._sessions["broken1"] = broken

        with pytest.raises(AttributeError):
            await asyncio.wait_for(pool.close("broken1", force=True), timeout=2.0)

        await wait_until(lambda: "broken1" not in pool._closing_sessions)


# ─── Compound close operations (Task 8): preparation-at-ticket atomicity ────


class TestCompoundCloseOperations:
    @pytest.mark.anyio
    async def test_handoff_preparation_captures_final_url_after_navigation_race(
        self, pool: BrowserPool, session: BrowserSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A navigation racing the close ticket must be captured by the
        RelaunchSnapshot preparation callback -- the replacement launches at
        the FINAL url (read only after the ticket owns the gate), not a
        pre-close snapshot a concurrent navigation could have raced past. A
        late manual op attempted after the ticket is accepted is rejected."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        pool.launch = AsyncMock(return_value={"instance_id": "new-handoff", "har_path": None})  # type: ignore[method-assign]

        release_navigation = asyncio.Event()

        async def _navigate() -> None:
            async with session.operation("browser_navigate"):
                session.page.url = "https://final.test"
                await release_navigation.wait()

        navigation = asyncio.create_task(_navigate())
        await wait_for_active(session._operation_gate, "browser_navigate")

        handoff_task = asyncio.create_task(handoff_browser(pool, session.instance_id, accept_stateless=True))
        await wait_for_state(session._operation_gate, "closing")
        with pytest.raises(SessionClosingError):
            async with session.operation("late_action"):
                pass

        release_navigation.set()
        await navigation
        result = await handoff_task
        assert result["url"] == "https://final.test"
        assert result["old_closed"] is True
        assert pool.launch.call_args.kwargs["url"] == "https://final.test"
        # Regression: handoff replacements must keep the corner badge.
        assert pool.launch.call_args.kwargs["badge"] is True

    @pytest.mark.anyio
    async def test_nonclosing_handoff_uses_one_ordinary_source_lease(
        self, pool: BrowserPool, session: BrowserSession
    ) -> None:
        """close_original=False never reserves a close cutoff at all -- the
        source stays ``open`` the whole time, under one ordinary lease."""
        session.profile = None
        session.user_data_dir = None
        launched = asyncio.Event()
        pool.launch = AsyncMock(side_effect=lambda **kwargs: launched.set() or {"instance_id": "replacement"})  # type: ignore[method-assign]

        result = await pool.handoff(session.instance_id, close_original=False, accept_stateless=True)

        assert launched.is_set()
        assert session.operation_snapshot()["state"] == "open"
        assert result["old_closed"] is False
        assert session.instance_id not in pool._closing_sessions

    @pytest.mark.anyio
    async def test_compound_close_rejects_when_another_close_cutoff_already_owns_the_ticket(
        self, pool: BrowserPool, session: BrowserSession
    ) -> None:
        """A compound close (require_fresh=True) arriving after an ordinary
        close already claimed the cutoff is rejected outright instead of
        silently sharing someone else's ticket and pretending its own
        preparation ran (it never even gets called)."""
        release = asyncio.Event()
        holder = asyncio.create_task(hold_operation(session, "long_action", release))
        await wait_for_active(session._operation_gate, "long_action")

        ordinary_close = asyncio.create_task(pool.close(session.instance_id, force=True))
        await wait_for_state(session._operation_gate, "closing")

        prepared_calls: list[str] = []

        async def _preparation(_session: BrowserSession) -> dict[str, str]:
            prepared_calls.append("ran")
            return {"should": "never happen"}

        with pytest.raises(SessionClosingError):
            await close_with_preparation(
                pool,
                session.instance_id,
                force=True,
                reason="agent_close",
                operation_name="browser_capture_and_close",
                preparation=_preparation,
                expected_session=session,
            )
        assert prepared_calls == []

        release.set()
        await holder
        result = await ordinary_close
        assert result["closed"] is True
        await wait_until(lambda: session.instance_id not in pool._closing_sessions)

    @pytest.mark.anyio
    async def test_compound_close_external_closure_fails_call_instead_of_partial_result(
        self, pool: BrowserPool, session: BrowserSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """External closure racing ahead of a compound close's own admission
        fails the WHOLE call with SessionClosedError after durable cleanup --
        it never returns a partial/fabricated ``prepared`` payload, and the
        preparation callback never runs."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)

        release = asyncio.Event()
        holder = asyncio.create_task(hold_operation(session, "long_action", release))
        await wait_for_active(session._operation_gate, "long_action")

        prepared_calls: list[str] = []

        async def _preparation(_session: BrowserSession) -> dict[str, str]:
            prepared_calls.append("ran")
            return {"title": "should never surface"}

        compound = asyncio.create_task(
            close_with_preparation(
                pool,
                session.instance_id,
                force=True,
                reason="agent_close",
                operation_name="browser_capture_and_close",
                preparation=_preparation,
                expected_session=session,
            )
        )
        await wait_for_state(session._operation_gate, "closing")

        won = pool._accept_external_close_nowait(session.instance_id, expected_session=session, reason="user_close")
        assert won is pool._closing_sessions[session.instance_id]

        release.set()
        await holder
        with pytest.raises(SessionClosedError):
            await compound
        assert prepared_calls == [], "preparation must never run once external closure won the ticket"
        await wait_until(lambda: session.instance_id not in pool._closing_sessions)

    @pytest.mark.anyio
    async def test_relaunch_fluid_delegates_to_close_with_preparation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """pool.relaunch_fluid delegates to relaunch_fluid_browser, which
        captures the RelaunchSnapshot inside the close ticket."""
        from octowright.browser_pool import close_helpers as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)
        pool = BrowserPool()
        sess = _fake_session(profile="dante", user_data_dir=None)
        pool._sessions[sess.instance_id] = sess
        pool.launch = AsyncMock(return_value={"instance_id": "fluid-new", "har_path": None})  # type: ignore[method-assign]

        result = await pool.relaunch_fluid(sess.instance_id)

        assert result["mode"] == "fluid"
        assert result["new_instance_id"] == "fluid-new"
        assert result["old_closed"] is True
        sess._teardown_after_close_cutoff.assert_awaited_once()
        _, kwargs = pool.launch.call_args
        assert kwargs["headed"] is True
        assert kwargs["badge"] is True
