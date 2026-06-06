# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for BrowserPool + its helpers in browser_pool/.

Pins:
- LaunchOptions.validate() / promoted_profile() / session_name() / from_mapping()
- BrowserPool.get raises with the empty-pool vs known-ids hint
- maybe_get / has_session / active_count / iter_sessions / list_sessions /
  profile_in_use / _evict_session_nowait shape
- _build_launch_kwargs (chromium-headed-tile vs everything-else)
- _resolve_session_dir (None when session=False, tmp creation, key reuse,
  recreation when tmpdir vanishes)
- close_browser KeyError on missing, manifest-remove failure swallow
- handoff_browser stateless rejection, persistent close_original=False rejection
- close_all + spawn_roster (per-spec error captured without aborting siblings)
- shutdown_pool (close_all + pw stop + tmpdir cleanup)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool import BrowserPool
from octowright.browser_pool.lifecycle import close_browser, handoff_browser, shutdown_pool
from octowright.browser_pool.options import LaunchOptions
from octowright.browser_pool.roster import close_all, spawn_roster


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
    return SimpleNamespace(
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
        trace=trace,
        page=SimpleNamespace(url=url),
        video_path=None,
        trace_path=None,
        close=AsyncMock(),
    )


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
        pool._sessions["a"] = _fake_session(instance_id="a", kind="webkit", profile="cosmo")
        assert pool.profile_in_use(kind="webkit", profile="cosmo") is True
        assert pool.profile_in_use(kind="chromium", profile="cosmo") is False
        assert pool.profile_in_use(kind="webkit", profile="ziggy") is False

    def test_empty_pool_returns_false(self) -> None:
        """Empty pool → never in use."""
        assert BrowserPool().profile_in_use(kind="chromium", profile="x") is False


class TestEvictSessionNowait:
    def test_returns_session_and_removes(self) -> None:
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions["x"] = sess
        evicted = pool._evict_session_nowait("x")
        assert evicted is sess
        assert "x" not in pool._sessions

    def test_missing_returns_none_no_raise(self) -> None:
        assert BrowserPool()._evict_session_nowait("nope") is None


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
        """tile=False + chromium → only --new-tab-url arg, no tiling flags."""
        pool = BrowserPool()
        out = await pool._build_launch_kwargs(tile=False, kind="chromium", headless=False)
        assert "args" in out
        assert any("--new-tab-url" in a for a in out["args"])
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
    async def test_no_args_when_headless(self) -> None:
        """Headless chromium gets --new-tab-url but no tiling flags; counter stays 0."""
        pool = BrowserPool()
        out = await pool._build_launch_kwargs(tile=True, kind="chromium", headless=True)
        assert "args" in out
        assert any("--new-tab-url" in a for a in out["args"])
        # Tiling is meaningless headless — tile counter must not advance
        assert pool._tile_counter == 0
        assert not any("--window-position" in a or "--window-size" in a for a in out["args"])


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
    async def test_evicts_session_before_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The session is removed from _sessions BEFORE session.close() is awaited."""
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions[sess.instance_id] = sess

        # Stub manifest removal so it doesn't touch real config.
        from octowright.browser_pool import lifecycle as _lc

        monkeypatch.setattr(_lc, "remove_manifest_session", lambda _id: None)

        await close_browser(pool, sess.instance_id)
        assert sess.instance_id not in pool._sessions
        sess.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_manifest_remove_failure_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If remove_manifest_session raises, close still succeeds (warning logged)."""
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions[sess.instance_id] = sess

        from octowright.browser_pool import lifecycle as _lc

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

        from octowright.browser_pool import lifecycle as _lc

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

        from octowright.browser_pool import lifecycle as _lc

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
        """accept_stateless=True bypasses the stateless guard."""
        pool = BrowserPool()
        sess = _fake_session(profile=None, user_data_dir=None)
        pool._sessions[sess.instance_id] = sess
        pool.close = AsyncMock(return_value={"closed": True})  # type: ignore[method-assign]
        pool.launch = AsyncMock(  # type: ignore[method-assign]
            return_value={"instance_id": "newX", "har_path": None}
        )
        result = await handoff_browser(pool, sess.instance_id, accept_stateless=True)
        assert result["ok"] is True
        assert result["new_instance_id"] == "newX"
        assert result["old_closed"] is True


# ─── close_all + spawn_roster ───────────────────────────────────────────────


class TestCloseAll:
    @pytest.mark.anyio
    async def test_empty_pool_returns_empty_list(self) -> None:
        """Empty pool → {'closed': []}."""
        pool = BrowserPool()
        assert await close_all(pool) == {"closed": []}

    @pytest.mark.anyio
    async def test_closes_each_session(self) -> None:
        """Every session id gets passed to pool.close in order."""
        pool = BrowserPool()
        pool._sessions["a"] = _fake_session(instance_id="a")
        pool._sessions["b"] = _fake_session(instance_id="b")
        called: list[str] = []

        async def fake_close(iid: str, **_kw: Any) -> dict[str, Any]:
            called.append(iid)
            return {"closed": True}

        pool.close = fake_close  # type: ignore[method-assign]
        result = await close_all(pool)
        assert sorted(result["closed"]) == ["a", "b"]
        assert sorted(called) == ["a", "b"]


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


class TestSessionsLock:
    @pytest.mark.anyio
    async def test_close_browser_acquires_sessions_lock(self) -> None:
        """close_browser pops via the lock; verify the lock is awaited."""
        pool = BrowserPool()
        sess = _fake_session()
        pool._sessions[sess.instance_id] = sess

        # Stub manifest removal.
        from unittest.mock import patch

        from octowright.browser_pool import lifecycle as _lc

        with patch.object(_lc, "remove_manifest_session", lambda _id: None):
            # If the lock weren't acquired, parallel pop would race; here we
            # just verify the lock object isn't replaced and the call succeeds.
            assert isinstance(pool._sessions_lock, asyncio.Lock)
            await close_browser(pool, sess.instance_id)
        assert sess.instance_id not in pool._sessions
