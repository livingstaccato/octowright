# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""`timeout_ms` must reach Playwright on the CSS-selector path too.

It was accepted everywhere and honoured only on the semantic (ARIA) path.
`_dispatch_click_or_fill` forwarded it to `click_by`/`fill_by` and then
explicitly `pop`ped it before the `click`/`fill` fallback, and `session.click`
had no timeout parameter at all -- it hardcoded `DEFAULT_ACTION_TIMEOUT_MS`.

So `{"action": "click", "selector": "#x", "timeout_ms": 3000}` linted clean,
saved from the dashboard editor, and then waited 15s. Reported from the field:
a failing click cost 15s four times over and blew an item budget, and the
obvious mitigation was a no-op.

The same hole was on the MCP tool surface, which the report did not mention:
`browser_click(selector=..., timeout_ms=...)` forwarded the timeout to
`click_by` and dropped it on `session.click`, so an agent had no working knob
either.

Honouring it was chosen over rejecting or warning because the plumbing is
one parameter: `click_by`/`fill_by` already resolve `timeout_ms or
DEFAULT_ACTION_TIMEOUT_MS`, and `click`/`fill` now do the same. A
silently-ignored knob is the worst of the three options; a rejected one would
break every macro already carrying the field.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS
from octowright.macros.runtime import _dispatch_click_or_fill
from octowright.macros.substitution import SEMANTIC_LOCATOR_KEYS
from octowright.session.core_page_mixin import SessionPageMixin


class _RecordingTarget:
    """Captures the timeout Playwright would actually receive."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def click(self, selector: str, **kwargs: Any) -> None:
        self.calls.append(("click", kwargs))

    async def fill(self, selector: str, value: str, **kwargs: Any) -> None:
        self.calls.append(("fill", kwargs))


class _Session:
    """Minimal session exposing the real click/fill bodies over a fake target."""

    def __init__(self) -> None:
        self.instance_id = "i"
        self.target = _RecordingTarget()
        self.metadata_timeouts: list[int | None] = []
        self.recorder = type("R", (), {"record": lambda *a, **k: None})()

    def _target(self) -> _RecordingTarget:
        return self.target

    async def _resolve_semantic_metadata(self, selector: str, *, timeout_ms: int | None = None) -> dict[str, Any]:
        # Records the budget it was handed: the aria read that annotates an
        # action honours that action's timeout, so an unresolvable selector
        # cannot spend the full default before the action starts.
        self.metadata_timeouts.append(timeout_ms)
        return {}

    async def _redacted_or_original(self, selector: str, value: str) -> str:
        return value

    def operation(self, *args: Any, **kwargs: Any) -> Any:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _cm() -> Any:
            yield None

        return _cm()

    click = SessionPageMixin.click
    fill = SessionPageMixin.fill


class TestSessionSignatures:
    """The parameter has to exist before anything can forward it."""

    @pytest.mark.parametrize("method", ["click", "fill"])
    def test_the_method_accepts_timeout_ms(self, method: str) -> None:
        assert "timeout_ms" in inspect.signature(getattr(SessionPageMixin, method)).parameters

    @pytest.mark.parametrize("method", ["click", "fill"])
    def test_the_protocol_matches(self, method: str) -> None:
        """`SessionLike` is what the macro runtime is typed against, so a
        signature that drifts from it fails the type gate rather than at run
        time on a live browser."""
        from octowright.session._protocols import SessionLike

        assert "timeout_ms" in inspect.signature(getattr(SessionLike, method)).parameters

    def test_click_accepts_no_wait_after(self) -> None:
        assert "no_wait_after" in inspect.signature(SessionPageMixin.click).parameters

        from octowright.session._protocols import SessionLike

        assert "no_wait_after" in inspect.signature(SessionLike.click).parameters


class TestSessionHonoursTimeout:
    async def test_click_forwards_an_explicit_timeout(self) -> None:
        session = _Session()

        await session.click("#Choice6", timeout_ms=3000)

        assert session.target.calls == [("click", {"timeout": 3000})]

    async def test_click_forwards_no_wait_after(self) -> None:
        session = _Session()

        await session.click("#sign-in", no_wait_after=True)

        assert session.target.calls == [("click", {"timeout": DEFAULT_ACTION_TIMEOUT_MS, "no_wait_after": True})]

    async def test_fill_forwards_an_explicit_timeout(self) -> None:
        session = _Session()

        await session.fill("#name", "Ada", timeout_ms=2500)

        assert session.target.calls == [("fill", {"timeout": 2500})]

    @pytest.mark.parametrize("method,args", [("click", ("#x",)), ("fill", ("#x", "v"))])
    async def test_omitting_it_keeps_the_default(self, method: str, args: tuple[Any, ...]) -> None:
        """Every pre-existing call site passes nothing and must be untouched."""
        session = _Session()

        await getattr(session, method)(*args)

        assert session.target.calls[0][1] == {"timeout": DEFAULT_ACTION_TIMEOUT_MS}

    @pytest.mark.parametrize("method,args", [("click", ("#x",)), ("fill", ("#x", "v"))])
    async def test_an_explicit_none_also_means_the_default(self, method: str, args: tuple[Any, ...]) -> None:
        """The macro runtime splats whatever the action carries, so an action
        with `"timeout_ms": null` must not become `timeout=None` -- Playwright
        reads that as "no timeout", i.e. wait forever."""
        session = _Session()

        await getattr(session, method)(*args, timeout_ms=None)

        assert session.target.calls[0][1] == {"timeout": DEFAULT_ACTION_TIMEOUT_MS}


class TestMacroDispatchKeepsTimeout:
    """The regression proper: the CSS fallback used to pop the field."""

    async def test_a_selector_click_keeps_its_timeout(self) -> None:
        session = _Session()

        await _dispatch_click_or_fill(
            session,  # type: ignore[arg-type]
            "click",
            {"selector": "#Choice6", "timeout_ms": 3000},
            SEMANTIC_LOCATOR_KEYS,
        )

        assert session.target.calls == [("click", {"timeout": 3000})]

    async def test_a_selector_fill_keeps_its_timeout(self) -> None:
        session = _Session()

        await _dispatch_click_or_fill(
            session,  # type: ignore[arg-type]
            "fill",
            {"selector": "#name", "value": "Ada", "timeout_ms": 2500},
            SEMANTIC_LOCATOR_KEYS,
        )

        assert session.target.calls == [("fill", {"timeout": 2500})]

    async def test_a_selector_click_keeps_no_wait_after(self) -> None:
        session = _Session()

        await _dispatch_click_or_fill(
            session,  # type: ignore[arg-type]
            "click",
            {"selector": "#sign-in", "no_wait_after": True},
            SEMANTIC_LOCATOR_KEYS,
        )

        assert session.target.calls == [("click", {"timeout": DEFAULT_ACTION_TIMEOUT_MS, "no_wait_after": True})]


class TestLintStaysDerived:
    """`timeout_ms` was in the allowed set as a hardcoded literal, which is what
    let lint bless a field the runtime discarded. It is now a real parameter of
    the fallback method, so the derivation covers it with nothing hand-listed --
    the property this module's docstring argues for at length."""

    @pytest.mark.parametrize("kind", ["click", "fill", "click_by", "fill_by"])
    def test_timeout_ms_is_still_allowed(self, kind: str) -> None:
        from octowright.macros.lint_fields import allowed_fields_for

        assert "timeout_ms" in allowed_fields_for(kind)

    @pytest.mark.parametrize("kind", ["click", "click_by"])
    def test_no_wait_after_is_allowed_for_clicks(self, kind: str) -> None:
        from octowright.macros.lint_fields import allowed_fields_for

        assert "no_wait_after" in allowed_fields_for(kind)

    def test_it_comes_from_the_signature_not_a_literal(self) -> None:
        from octowright.macros import lint_fields

        source = inspect.getsource(lint_fields._click_or_fill_allowed)

        assert '"timeout_ms"' not in source, "allowed set must derive timeout_ms from the signature"


class TestToolSurfaceForwardsTimeout:
    """The hole was on the MCP tools too, which the field report did not cover:
    both forwarded `timeout_ms` to the semantic pair and dropped it on the
    selector branch, so an agent had no working knob either."""

    @pytest.mark.parametrize(
        "tool,call",
        [
            ("browser_click", "await session.click(selector, timeout_ms=timeout_ms, no_wait_after=no_wait_after)"),
            ("browser_fill", "await session.fill(selector, value, timeout_ms=timeout_ms)"),
        ],
    )
    def test_the_selector_branch_passes_it_through(self, tool: str, call: str) -> None:
        from octowright.server.browser import input as _input

        source = inspect.getsource(getattr(_input, tool))

        assert call in source

    def test_browser_click_forwards_no_wait_after_on_both_paths(self) -> None:
        from octowright.server.browser import input as _input

        signature = inspect.signature(_input.browser_click)
        source = inspect.getsource(_input.browser_click)

        assert "no_wait_after" in signature.parameters
        assert "no_wait_after=no_wait_after" in source


class TestZeroIsNotForwarded:
    """`timeout_ms or DEFAULT` maps 0 to the default, and that is deliberate.

    Playwright reads `timeout=0` as "disable the timeout" — wait forever. A
    macro author writing `"timeout_ms": 0` is far more likely to mean "don't
    wait" than "block this run indefinitely", and a macro that hangs forever is
    exactly the failure this whole change exists to prevent. Falling back to the
    bounded default is the safe reading.

    Pinned rather than left implicit: `x or DEFAULT` looks like a null check,
    so a later refactor to `x if x is not None else DEFAULT` would silently
    reintroduce the hang. That is the same silent-behaviour-change class as the
    dropped timeout itself.
    """

    @pytest.mark.parametrize("method,args", [("click", ("#x",)), ("fill", ("#x", "v"))])
    async def test_zero_falls_back_to_the_default_rather_than_disabling(
        self, method: str, args: tuple[Any, ...]
    ) -> None:
        session = _Session()

        await getattr(session, method)(*args, timeout_ms=0)

        assert session.target.calls[0][1] == {"timeout": DEFAULT_ACTION_TIMEOUT_MS}

    @pytest.mark.parametrize("method,args", [("click", ("#x",)), ("fill", ("#x", "v"))])
    async def test_the_semantic_path_agrees(self, method: str, args: tuple[Any, ...]) -> None:
        """click_by/fill_by resolve it the same way; the two paths must not
        disagree about what 0 means."""
        import inspect

        from octowright.session.core_locator_mixin import SessionLocatorMixin

        source = inspect.getsource(getattr(SessionLocatorMixin, f"{method}_by"))

        assert "timeout_ms or DEFAULT_ACTION_TIMEOUT_MS" in source


class TestMetadataReadHonoursTheSameBudget:
    """The aria read that annotates an action used to be unbounded.

    Its credential scan uses DEFAULT_ACTION_TIMEOUT_MS, so a selector that
    never resolves spent that in full BEFORE the bounded action started: a
    click carrying timeout_ms=4000 measured 19.1s downstream. Whatever budget
    the action gets, the annotation gets too.
    """

    @pytest.mark.anyio
    async def test_click_passes_its_budget_down(self) -> None:
        session = _Session()
        await SessionPageMixin.click(session, "#x", timeout_ms=4000)  # type: ignore[arg-type]
        assert session.metadata_timeouts == [4000]

    @pytest.mark.anyio
    async def test_fill_passes_its_budget_down(self) -> None:
        session = _Session()
        await SessionPageMixin.fill(session, "#x", "v", timeout_ms=2500)  # type: ignore[arg-type]
        assert session.metadata_timeouts == [2500]

    @pytest.mark.anyio
    async def test_an_unset_budget_still_bounds_the_read(self) -> None:
        """None means "use the default", not "wait forever" -- the read must
        be bounded either way, or the bug survives for every default call."""
        session = _Session()
        await SessionPageMixin.click(session, "#x")  # type: ignore[arg-type]
        assert session.metadata_timeouts == [DEFAULT_ACTION_TIMEOUT_MS]
