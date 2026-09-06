# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-engine launch health (``BrowserPool.engine_health`` / ``_record_engine_health``).

Diagnosing a real incident spent about an hour of a 12.6-hour wedge just
establishing "WebKit is broken on this machine, Chromium is fine" -- the pool
already saw every launch and every failure per engine kind, it just never said
so. These tests cover: a successful launch records ``ok`` with a timestamp; a
failed launch records the exception CLASS NAME and never its message; each
kind is tracked independently; a kind never launched is absent (not falsely
reported healthy); and the block is wired into
``octowright_status()["pool"]["engine_health"]``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.request_errors import InvalidRequestError


def test_engine_health_empty_when_nothing_launched() -> None:
    pool = BrowserPool()
    assert pool.engine_health() == {}


async def test_successful_launch_records_ok_with_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        return {"instance_id": "healthy-chromium"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    out = await pool.launch(kind="chromium")

    assert out == {"instance_id": "healthy-chromium"}
    health = pool.engine_health()
    assert set(health.keys()) == {"chromium"}
    entry = health["chromium"]
    assert entry["outcome"] == "ok"
    assert "error" not in entry
    # "at" is a real, parseable ISO-8601 UTC timestamp, not a placeholder.
    parsed = datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert (datetime.now(UTC) - parsed).total_seconds() < 30


async def test_failed_launch_records_error_class_only(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    # Message deliberately carries the kind of sensitive detail a launch
    # failure can leak -- a filesystem path with a real username/profile name.
    secret_message = "Executable doesn't exist at /Users/tanuki-tim/.config/octowright/profiles/secret-persona"

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        raise RuntimeError(secret_message)

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    with pytest.raises(RuntimeError, match="secret-persona"):
        await pool.launch(kind="webkit")

    health = pool.engine_health()
    entry = health["webkit"]
    assert entry["outcome"] == "error"
    assert entry["error"] == "RuntimeError"
    # The message text must never appear anywhere in the recorded entry --
    # this is a hard requirement, not a style preference.
    rendered = repr(entry)
    assert "secret-persona" not in rendered
    assert "tanuki-tim" not in rendered
    assert secret_message not in rendered


async def test_kinds_tracked_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact case this exists to show: chromium healthy, webkit failing."""
    pool = BrowserPool()

    async def _impl(options: dict[str, Any], _sp: object) -> dict[str, Any]:
        if options.get("kind") == "webkit":
            raise RuntimeError("WebKit is broken on this machine")
        return {"instance_id": f"{options.get('kind')}-ok"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    await pool.launch(kind="chromium")
    with pytest.raises(RuntimeError):
        await pool.launch(kind="webkit")

    health = pool.engine_health()
    assert health["chromium"]["outcome"] == "ok"
    assert health["webkit"]["outcome"] == "error"
    assert health["webkit"]["error"] == "RuntimeError"
    # Neither entry's fields bled into the other.
    assert "error" not in health["chromium"]


async def test_kind_never_launched_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent means 'no data', not 'healthy' -- conflating them is what made
    the original diagnosis slow."""
    pool = BrowserPool()

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        return {"instance_id": "chromium-only"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    await pool.launch(kind="chromium")

    health = pool.engine_health()
    assert "chromium" in health
    assert "webkit" not in health
    assert "firefox" not in health


async def test_repeated_launches_keep_only_the_last_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    calls = {"n": 0}

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first attempt failed")
        return {"instance_id": "recovered"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    with pytest.raises(RuntimeError):
        await pool.launch(kind="chromium")
    assert pool.engine_health()["chromium"]["outcome"] == "error"

    await pool.launch(kind="chromium")
    assert pool.engine_health()["chromium"]["outcome"] == "ok"
    assert "error" not in pool.engine_health()["chromium"]


async def test_driver_death_retry_records_success_once_healed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A launch that survives a driver-death retry is recorded ``ok``.

    Scope, honestly: this pins the OUTCOME, not the mechanism. Recording
    per-attempt inside ``_launch_with_driver_retry`` instead of once in
    ``launch()`` also leaves this green, because the later write overwrites
    the transient ``error`` before any assertion reads it. The split is
    still the right design -- a concurrent ``octowright_status()`` landing
    between the transient failure and the retry's success would briefly
    report ``error`` under the per-attempt shape and never does under this
    one -- but that race is not what this test proves.
    """
    from unittest.mock import AsyncMock

    pool = BrowserPool()
    monkeypatch.setattr(pool, "_reset_driver", AsyncMock())
    calls = {"n": 0}

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("BrowserType.launch: Connection closed")
        return {"instance_id": "healed"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    out = await pool.launch(kind="chromium")

    assert out == {"instance_id": "healed"}
    assert calls["n"] == 2
    entry = pool.engine_health()["chromium"]
    assert entry["outcome"] == "ok"
    assert "error" not in entry


def test_engine_health_returns_a_copy() -> None:
    """Mutating a returned snapshot must not corrupt the pool's own state."""
    pool = BrowserPool()
    pool._record_engine_health("chromium", None)

    snapshot = pool.engine_health()
    snapshot["chromium"]["outcome"] = "MUTATED"
    snapshot["firefox"] = {"outcome": "MUTATED", "at": "x"}

    fresh = pool.engine_health()
    assert fresh["chromium"]["outcome"] == "ok"
    assert "firefox" not in fresh


async def test_engine_health_surfaced_in_octowright_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end wiring: a real launch on the process-wide singleton pool is
    visible at octowright_status()["pool"]["engine_health"]."""
    from octowright.server.meta import octowright_status
    from octowright.server.meta import pool as status_pool

    # The singleton pool is shared across the whole test session -- snapshot
    # and restore its engine-health state so this test can't leak into others.
    original = dict(status_pool._engine_health)

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        return {"instance_id": "status-wiring-firefox"}

    try:
        monkeypatch.setattr(status_pool, "_launch_impl", _impl)
        await status_pool.launch(kind="firefox")

        snap = octowright_status()

        assert "engine_health" in snap["pool"]
        assert snap["pool"]["engine_health"]["firefox"]["outcome"] == "ok"
        assert "at" in snap["pool"]["engine_health"]["firefox"]
    finally:
        status_pool._engine_health.clear()
        status_pool._engine_health.update(original)


async def test_an_unsupported_kind_is_clamped_before_it_reaches_any_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-supplied ``kind`` must not become a permanent key or label.

    ``browser_launch``'s signature is ``kind: str``, not a ``Literal``, so the
    MCP schema accepts anything, and ``kind_hint`` feeds three sinks: the
    engine-health key, the ``octowright.browser.launch`` span attribute, and
    the ``kind`` label on ``octowright_browser_launch_failed_total``. An
    unbounded metric label is worse than an unbounded dict -- it creates a
    permanent time series per distinct string, fleet-wide.

    The SPAN attribute is the one still reachable with a bogus kind, and it is
    what this asserts, through the real unpatched path: ``launch_span`` opens
    inside ``_launch_with_driver_retry``, BEFORE ``LaunchOptions.validate``
    runs in ``_launch_impl``, so the span is stamped whether or not the request
    survives validation. Stubbing ``_launch_with_driver_retry`` would stub away
    ``launch_span`` itself and assert only what was handed to the stub -- so a
    change stamping ``raw_kind`` instead of ``kind_hint`` would still pass.
    Intercepting ``span`` catches that. No browser is launched: validation
    rejects the kind before any driver work.

    A bogus kind reaching the engine-health dict is covered separately by
    ``test_a_refused_request_is_never_recorded_as_an_engine_fault``, which
    asserts the stronger thing -- that it is not recorded under any key.
    """
    from octowright.browser_pool import _metrics

    stamped: list[str] = []
    real_span = _metrics.span

    def _recording_span(name: str, **attrs: Any) -> Any:
        stamped.append(attrs.get("kind", "<unset>"))
        return real_span(name, **attrs)

    monkeypatch.setattr(_metrics, "span", _recording_span)
    pool = BrowserPool()

    for bogus in ("../../etc/passwd", "chrome", "Chromium", "x" * 80):
        with pytest.raises(InvalidRequestError):
            await pool.launch(kind=bogus)
        assert bogus not in pool.engine_health()

    assert set(stamped) == {"unknown"}
    assert pool.engine_health() == {}


@pytest.mark.parametrize(
    ("label", "options"),
    [
        ("url", {"kind": "chromium", "url": "file:///etc/passwd"}),
        ("kind", {"kind": "not-an-engine"}),
    ],
)
async def test_a_refused_request_is_never_recorded_as_an_engine_fault(label: str, options: dict[str, Any]) -> None:
    """A caller error must not be filed as an engine fault (issue #214).

    ``browser_launch(url="file:///etc/passwd")`` left ``octowright_status``
    reporting ``chromium: {"outcome": "error", "error": "ValueError"}``. Only
    the exception CLASS name is kept (deliberately -- a launch failure message
    can carry a path or a profile name), so that is byte-identical to a
    genuinely broken engine: it was read as one, retried on firefox for the
    identical signal, and cost about an hour -- inverting the one thing this
    block exists to say.

    Deliberately does NOT monkeypatch ``_launch_impl``, unlike every other
    failure case in this file: the URL guard lives inside it, so patching it
    out is precisely what would hide this. Only the two guards that reject
    before any driver starts are driven end-to-end here; the deeper ones are
    covered by ``test_every_launch_input_guard_raises_invalid_request`` plus
    ``test_launch_classifies_a_refusal_by_type_not_by_depth``, which together
    make the same claim without a browser.
    """
    pool = BrowserPool()

    with pytest.raises(InvalidRequestError):
        await pool.launch(**options)

    assert pool.engine_health() == {}, f"{label} rejection polluted engine health"


async def test_launch_classifies_a_refusal_by_type_not_by_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Depth must not matter -- which is the whole reason to classify by type.

    The first repair hoisted the URL check above the recording window. That
    closes the guards that happen to be hoistable and leaves the ones that are
    not: ``har_path`` containment needs the session log path and the pool's
    recordings root, and ``base_url`` needs the persona lock held, so both
    raise from deep inside ``launch_profile_locked`` -- after the driver has
    started, which is why they cannot be driven here without a real browser.
    Stubbing the raise at ``_launch_impl`` asserts the property that actually
    matters: wherever an ``InvalidRequestError`` comes from, ``launch`` does
    not record it. Whether a future guard actually *raises* that type is a
    separate claim, enforced by
    ``tests/test_launch_guard_classification.py`` rather than assumed here.
    """
    pool = BrowserPool()

    async def _refuse(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        raise InvalidRequestError("har_path '/etc/evil.har' resolves outside '.../sessions'")

    monkeypatch.setattr(pool, "_launch_impl", _refuse)
    with pytest.raises(InvalidRequestError):
        await pool.launch(kind="chromium")
    assert pool.engine_health() == {}

    # The mirror image: a genuine engine failure at the same depth IS recorded.
    # Without this the test passes just as well against a launch() that records
    # nothing at all.
    async def _break(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        raise RuntimeError("BrowserType.launch: engine is genuinely broken")

    monkeypatch.setattr(pool, "_launch_impl", _break)
    with pytest.raises(RuntimeError):
        await pool.launch(kind="chromium")
    entry = pool.engine_health()["chromium"]
    # `at` is asserted by FORMAT, not fed back from the value under test:
    # `"at": entry["at"]` compares the field to itself, so an empty string or a
    # wrong-timezone stamp would pass a whole-record equality that looks strict.
    recorded_at = entry.pop("at")
    assert entry == {"outcome": "error", "error": "RuntimeError"}
    parsed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert (datetime.now(UTC) - parsed).total_seconds() < 30


def test_every_launch_input_guard_raises_invalid_request(tmp_path: Path) -> None:
    """Each guard reachable from ``launch`` must carry the classification.

    The four are listed together because the bug was not one guard being
    wrong -- it was there being no shared way to say "this is the caller's
    mistake". ``har_path`` and ``base_url`` are the two a hoist could never
    have reached, and are the reason this list exists rather than a comment.
    """
    from octowright._paths import reject_unsafe_path
    from octowright.browser_pool.launch_helpers import base_url_kwargs, build_recording_kwargs
    from octowright.browser_pool.options import LaunchOptions
    from octowright.session.core_page_mixin import _reject_unsafe_url

    with pytest.raises(InvalidRequestError):
        _reject_unsafe_url("file:///etc/passwd")
    with pytest.raises(InvalidRequestError):
        LaunchOptions(kind="not-an-engine").validate()
    # Derived from tmp_path, not a POSIX literal: on Windows a rooted path with
    # no drive (`/etc/evil.har`) is NOT absolute, so build_recording_kwargs
    # would sandbox it under the root and never reject it -- the test would
    # fail for a path-spelling reason rather than a classification one.
    outside_har = tmp_path.parent / "escape.har"

    with pytest.raises(InvalidRequestError):
        reject_unsafe_path(outside_har, tmp_path, label="har_path")
    with pytest.raises(InvalidRequestError):
        base_url_kwargs(None, "file:///etc/passwd")
    with pytest.raises(InvalidRequestError):
        build_recording_kwargs(
            LaunchOptions(kind="chromium", har=True, har_path=str(outside_har)),
            headless=True,
            explicit_size=False,
            log_path=tmp_path / "s.jsonl",
            recordings_dir=tmp_path,
        )


def test_invalid_request_error_stays_catchable_as_value_error() -> None:
    """Subclassing ``ValueError`` is what made the conversion free.

    Every ``except ValueError`` and ``pytest.raises(ValueError)`` in the tree
    (and in embedders) predates this type; if it were a plain ``Exception``
    the reclassification would silently stop being caught at call sites nobody
    audited.
    """
    assert issubclass(InvalidRequestError, ValueError)
    with pytest.raises(ValueError, match="file"):
        from octowright.session.core_page_mixin import _reject_unsafe_url

        _reject_unsafe_url("file:///etc/passwd")


async def test_a_refused_request_does_not_erase_a_known_good_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stronger claim: a refusal leaves a PROVEN engine reading ``ok``.

    An empty block is merely "no data"; the misdiagnosis happened because the
    block actively contradicted an engine that was working.
    """
    pool = BrowserPool()

    async def _ok(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        return {"instance_id": "proven-healthy"}

    monkeypatch.setattr(pool, "_launch_impl", _ok)
    await pool.launch(kind="chromium")
    assert pool.engine_health()["chromium"]["outcome"] == "ok"

    async def _refuse(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        raise InvalidRequestError("navigate url scheme 'file' is not allowed")

    monkeypatch.setattr(pool, "_launch_impl", _refuse)
    with pytest.raises(InvalidRequestError):
        await pool.launch(kind="chromium")

    assert pool.engine_health()["chromium"]["outcome"] == "ok"
    assert "error" not in pool.engine_health()["chromium"]


async def test_a_refused_request_is_not_counted_as_a_launch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The metric carries the same lie, and needs the same exemption.

    ``octowright_browser_launch_failed_total{kind, error}`` is incremented by
    ``launch_span`` on the way out, so a refusal recorded there tells an
    operator alerting on per-engine launch failures that chromium is failing
    when nothing ever asked it to launch. It moves to
    ``octowright_launch_refused_total{reason="invalid_request"}`` rather than
    vanishing, and both halves are asserted -- dropping it outright would hide
    a rejection flood behind a flat counter. Asserted on the counters themselves
    rather than on a proxy ("did we reach ``_launch_with_driver_retry``"):
    moving the ``add`` into ``launch()``'s own ``except`` is a plausible
    refactor that a proxy assertion would sail straight through.
    """
    from octowright.browser_pool import _metrics

    recorded: list[dict[str, Any]] = []

    class _Counter:
        def add(self, amount: int, attributes: dict[str, Any] | None = None) -> None:
            recorded.append({"amount": amount, "attributes": attributes})

    refused: list[dict[str, Any]] = []

    class _RefusedCounter:
        def add(self, amount: int, attributes: dict[str, Any] | None = None) -> None:
            refused.append({"amount": amount, "attributes": attributes})

    # Patched on `_metrics` because `launch_span` resolves both through the
    # module. `limits.py` does `from ... import LAUNCH_REFUSED`, binding the
    # object at import, so a test asserting the `cap`/`memory` reasons must
    # patch `limits.LAUNCH_REFUSED` instead -- patching here would watch the
    # original counter increment and pass vacuously.
    monkeypatch.setattr(_metrics, "LAUNCH_FAILED", _Counter())
    monkeypatch.setattr(_metrics, "LAUNCH_REFUSED", _RefusedCounter())

    with pytest.raises(InvalidRequestError):
        await BrowserPool().launch(kind="chromium", url="file:///etc/passwd")
    assert recorded == []
    # Moved, not dropped: silence would hide a client regression spamming
    # invalid requests behind a flat machinery-only counter.
    assert refused == [{"amount": 1, "attributes": {"reason": "invalid_request"}}]

    # A real engine failure still counts -- the exemption is by type, and must
    # not have quietly disabled the counter for everyone.
    pool = BrowserPool()

    async def _break(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        raise RuntimeError("BrowserType.launch: engine is genuinely broken")

    monkeypatch.setattr(pool, "_launch_impl", _break)
    with pytest.raises(RuntimeError):
        await pool.launch(kind="webkit")

    assert recorded == [{"amount": 1, "attributes": {"kind": "webkit", "error": "RuntimeError"}}]
