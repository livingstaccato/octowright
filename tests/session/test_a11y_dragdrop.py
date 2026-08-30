# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from contextlib import asynccontextmanager
from typing import Any

import pytest

from octowright.session.a11y_dragdrop import run_a11y_dragdrop, validate_params


def test_rejects_zero_verify_fields() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_params(nav_key="tab", nav_key_sequence=None, verify_fields_set=0)


def test_rejects_two_verify_fields() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_params(nav_key="tab", nav_key_sequence=None, verify_fields_set=2)


def test_keys_mode_requires_a_sequence() -> None:
    with pytest.raises(ValueError, match="nav_key_sequence"):
        validate_params(nav_key="keys", nav_key_sequence=None, verify_fields_set=1)


def test_non_keys_mode_rejects_a_sequence() -> None:
    with pytest.raises(ValueError, match="nav_key_sequence"):
        validate_params(nav_key="tab", nav_key_sequence=["Tab"], verify_fields_set=1)


def test_accepts_the_valid_shapes() -> None:
    validate_params(nav_key="tab", nav_key_sequence=None, verify_fields_set=1)
    validate_params(nav_key="arrow", nav_key_sequence=None, verify_fields_set=1)
    validate_params(nav_key="keys", nav_key_sequence=["Tab", "Enter"], verify_fields_set=1)


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self._page, self._selector = page, selector

    async def focus(self, **_kw: Any) -> None:
        if self._selector in self._page.missing:
            raise RuntimeError(f"no element for {self._selector}")
        self._page.events.append(("focus", self._selector))

    async def evaluate(self, _js: str, *_args: Any, **_kw: Any) -> Any:
        self._page.events.append(("evaluate", self._selector))
        return self._page.grabbed_result

    async def count(self) -> int:
        return 0 if self._selector in self._page.missing else 1


class FakeKeyboard:
    def __init__(self, page: "FakePage") -> None:
        self._page = page

    async def press(self, key: str) -> None:
        self._page.events.append(("press", key))


class FakePage:
    """Models only what the engine touches: locator/keyboard/evaluate."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.missing: set[str] = set()
        self.grabbed_result: Any = True
        self.verify_results: list[Any] = [True]
        self.keyboard = FakeKeyboard(self)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def evaluate(self, _js: str, *_args: Any, **_kw: Any) -> Any:
        self.events.append(("verify", None))
        return self.verify_results.pop(0) if len(self.verify_results) > 1 else self.verify_results[0]


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.leases: list[str] = []

    def _target(self) -> FakePage:
        return self.page

    @asynccontextmanager
    async def operation(self, name: str, **_kw: Any):
        self.leases.append(name)
        yield


async def test_happy_path_tab_navigation() -> None:
    page = FakePage()
    session = FakeSession(page)
    result = await run_a11y_dragdrop(
        session, source_selector="#item", nav_key="tab", max_nav_steps=3, verify_js="() => true"
    )
    assert result["stage_reached"] == "verified"
    assert result["ok"] is True and result["verified"] is True
    assert result["released"] is False, "a verified drop already exits grab mode; no Escape"
    assert result["nav_steps_taken"] == 3
    assert ("press", "Tab") in page.events


async def test_failed_grab_short_circuits_without_release() -> None:
    page = FakePage()
    page.grabbed_result = False
    session = FakeSession(page)
    result = await run_a11y_dragdrop(session, source_selector="#item", verify_js="() => true")
    assert result["stage_reached"] == "failed_grab"
    assert result["grabbed"] is False and result["dropped"] is False
    assert result["released"] is False, "nothing entered grab mode, so nothing to release"
    assert ("press", "Escape") not in page.events


async def test_failed_verify_presses_release() -> None:
    page = FakePage()
    page.verify_results = [False]
    session = FakeSession(page)
    result = await run_a11y_dragdrop(
        session, source_selector="#item", verify_js="() => false", verify_timeout_ms=30, verify_poll_ms=10
    )
    assert result["stage_reached"] == "failed_verify"
    assert result["dropped"] is True and result["verified"] is False
    assert result["released"] is True
    assert ("press", "Escape") in page.events


async def test_arrow_mode_sends_the_direction_key() -> None:
    page = FakePage()
    session = FakeSession(page)
    await run_a11y_dragdrop(
        session,
        source_selector="#i",
        nav_key="arrow",
        nav_direction="left",
        max_nav_steps=2,
        verify_js="() => true",
    )
    assert [e for e in page.events if e == ("press", "ArrowLeft")] == [("press", "ArrowLeft")] * 2


async def test_keys_mode_sends_the_sequence_once() -> None:
    page = FakePage()
    session = FakeSession(page)
    result = await run_a11y_dragdrop(
        session,
        source_selector="#i",
        nav_key="keys",
        nav_key_sequence=["Tab", "Tab", "End"],
        verify_js="() => true",
    )
    pressed = [k for kind, k in page.events if kind == "press"]
    assert pressed[1:4] == ["Tab", "Tab", "End"]
    assert result["nav_steps_taken"] == 3


async def test_exception_after_grab_releases_before_propagating() -> None:
    page = FakePage()
    session = FakeSession(page)

    async def boom(_js: str, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("page detached")

    page.evaluate = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="page detached"):
        await run_a11y_dragdrop(session, source_selector="#i", verify_js="() => true")
    assert ("press", "Escape") in page.events, "grabbed widget must not be left stuck"


async def test_engine_takes_a_named_operation_lease() -> None:
    page = FakePage()
    session = FakeSession(page)
    await run_a11y_dragdrop(session, source_selector="#i", verify_js="() => true")
    assert "browser_a11y_dragdrop" in session.leases


async def test_verify_selector_appears_checks_locator_count() -> None:
    page = FakePage()
    page.missing.add("#dropzone-item")
    session = FakeSession(page)
    result = await run_a11y_dragdrop(
        session,
        source_selector="#i",
        verify_selector_appears="#dropzone-item",
        verify_timeout_ms=30,
        verify_poll_ms=10,
    )
    assert result["stage_reached"] == "failed_verify", "selector never appears while it stays missing"

    page2 = FakePage()
    session2 = FakeSession(page2)
    result2 = await run_a11y_dragdrop(session2, source_selector="#i", verify_selector_appears="#dropzone-item")
    assert result2["stage_reached"] == "verified", "selector present by default -> locator.count() > 0"


async def test_verify_selector_gone_checks_locator_absence() -> None:
    page = FakePage()
    session = FakeSession(page)  # "#loading-spinner" present by default -> count() == 1, not gone
    result = await run_a11y_dragdrop(
        session,
        source_selector="#i",
        verify_selector_gone="#loading-spinner",
        verify_timeout_ms=30,
        verify_poll_ms=10,
    )
    assert result["stage_reached"] == "failed_verify", "selector still present -> not gone"

    page2 = FakePage()
    page2.missing.add("#loading-spinner")
    session2 = FakeSession(page2)
    result2 = await run_a11y_dragdrop(session2, source_selector="#i", verify_selector_gone="#loading-spinner")
    assert result2["stage_reached"] == "verified", "selector absent -> locator.count() == 0"


async def test_verify_text_contains_checks_body_text() -> None:
    page = FakePage()
    page.verify_results = [False]
    session = FakeSession(page)
    result = await run_a11y_dragdrop(
        session,
        source_selector="#i",
        verify_text_contains="Item moved to Done",
        verify_timeout_ms=30,
        verify_poll_ms=10,
    )
    assert result["stage_reached"] == "failed_verify"

    page2 = FakePage()
    session2 = FakeSession(page2)
    result2 = await run_a11y_dragdrop(session2, source_selector="#i", verify_text_contains="Item moved to Done")
    assert result2["stage_reached"] == "verified"


async def test_grab_predicate_exception_still_presses_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Important-2: `grab_key` is already pressed before the predicate check
    runs, so a predicate that THROWS (arbitrary caller-supplied JS, or a
    target-closed/detached ``evaluate``) must still release -- unlike the
    predicate returning False, which legitimately presses nothing further.
    """
    page = FakePage()
    session = FakeSession(page)

    async def boom(_self: FakeLocator, _js: str, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("target closed")

    monkeypatch.setattr(FakeLocator, "evaluate", boom)

    with pytest.raises(RuntimeError, match="target closed"):
        await run_a11y_dragdrop(session, source_selector="#i", verify_js="() => true")
    assert ("press", "Space") in page.events, "grab_key press happens before the predicate check"
    assert ("press", "Escape") in page.events, "grab_key was already pressed; a throwing predicate must still release"


async def test_release_failure_does_not_mask_original_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Important-1: a release press that itself fails (most likely the exact
    page/connection-gone condition the handler exists for) must not replace
    the original exception the caller needs to see.
    """
    page = FakePage()
    session = FakeSession(page)

    async def boom_verify(_js: str, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("page detached")

    page.evaluate = boom_verify  # type: ignore[method-assign]

    async def boom_release(_self: FakeKeyboard, key: str) -> None:
        if key == "Escape":
            raise RuntimeError("connection already closed")
        page.events.append(("press", key))

    monkeypatch.setattr(FakeKeyboard, "press", boom_release)

    with pytest.raises(RuntimeError, match="page detached"):
        await run_a11y_dragdrop(session, source_selector="#i", verify_js="() => true")


async def test_empty_verify_js_falls_through_to_the_text_check() -> None:
    """The reachable asymmetry: lint/validate count verify fields by
    TRUTHINESS, so ``verify_js=""`` alongside a real ``verify_text_contains``
    is arity 1 and passes validation. Dispatching on ``is not None`` then took
    the ``verify_js`` branch and evaluated the empty string -- the author's
    text check never ran, and the failure surfaced as an unrelated Playwright
    error rather than as a failed verify.
    """
    page = FakePage()
    session = FakeSession(page)
    seen: list[str] = []

    async def capture(js: str, *_a: Any, **_k: Any) -> Any:
        seen.append(js)
        return True

    page.evaluate = capture  # type: ignore[method-assign]

    result = await run_a11y_dragdrop(session, source_selector="#i", verify_js="", verify_text_contains="Done")
    assert result["stage_reached"] == "verified"
    assert seen and "innerText.includes" in seen[0], seen


async def test_grab_key_press_failure_still_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    """The grab press is inside the protected region: a press that raises may
    still have delivered its keydown, which is the same "grabbed state is
    unknown" condition the release path exists for.
    """
    page = FakePage()
    session = FakeSession(page)

    async def boom_press(_self: FakeKeyboard, key: str) -> None:
        page.events.append(("press", key))
        if key == "Space":
            raise RuntimeError("press interrupted")

    monkeypatch.setattr(FakeKeyboard, "press", boom_press)

    with pytest.raises(RuntimeError, match="press interrupted"):
        await run_a11y_dragdrop(session, source_selector="#i", verify_js="() => true")
    assert ("press", "Escape") in page.events, "a failed grab press must still release"


async def test_session_method_records_the_action() -> None:
    from octowright.session.core_ops_mixin import SessionOpsMixin

    class Recorder:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, Any]]] = []

        def record(self, kind: str, **fields: Any) -> None:
            self.events.append((kind, fields))

    page = FakePage()

    class Session(SessionOpsMixin):
        def __init__(self) -> None:
            self.page = page
            self.recorder = Recorder()
            self.leases: list[str] = []

        def _target(self) -> FakePage:
            return page

        @asynccontextmanager
        async def operation(self, name: str, **_kw: Any):
            self.leases.append(name)
            yield

    session = Session()
    result = await session.a11y_dragdrop(source_selector="#item", verify_js="() => true")
    assert result["verified"] is True
    kind, fields = session.recorder.events[0]
    assert kind == "a11y_dragdrop"
    assert fields["source_selector"] == "#item"
    assert fields["verify_js"] == "() => true"
    assert "ok" not in fields, "record the inputs, not the outcome"
