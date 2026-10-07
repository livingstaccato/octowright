# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a redacted screenshot says when it refuses, which step a timeout names, and what it leaves behind.

`test_macro_redacted_screenshot.py` pins the redaction protocol over a fake page;
this reuses that page and pins the rest: the exact refusal message and
``screenshot_refused`` fields of every refusal, the operation a hung DevTools call
is reported under, the DevTools listeners and the session left after a run, the
policies' warnings, and how a privacy handler is called and refused.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from collections.abc import AsyncIterator, Iterable, MutableMapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from octowright import defaults
from octowright.macros import animation_freeze, safe_screenshot
from octowright.macros import redaction_page_js as page_js
from octowright.macros.privacy_ledger import SESSION_PRIVACY_LEDGER_ATTR, SessionPrivacyLedger
from octowright.macros.screenshot_refusal import ScreenshotRefused
from octowright.session.timeouts import SessionCallTimeoutError
from tests.test_macro_redacted_screenshot import CLEAN, PNG, FakeCDP, FakePage

PASSWORD = "B7-REDACT-PASSWORD-CANARY-4c2e"  # pragma: allowlist secret
POLICY_ENV = safe_screenshot.POLICY_ENV
LEDGER_ENV = safe_screenshot.LEDGER_POLICY_ENV
TIMEOUT_ENV = "OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS"
TIMED_OUT = "macro_redacted_screenshot did not answer within 0.05s"


def _key(method: str, params: dict[str, Any] | None) -> str:
    """The DevTools call, named by the controller method or page script it runs."""
    if method != "Runtime.callFunctionOn" or params is None:
        return method
    function = params["functionDeclaration"]
    named = {
        page_js.CONTROLLER_JS: "controller",
        page_js.END_VIEW_TRANSITIONS_JS: "end_view_transitions",
        animation_freeze.PENDING_JS: "note_animations",
        animation_freeze.STILL_PENDING_JS: "poll_animations",
        animation_freeze.REPAIR_JS: "repair_animations",
    }
    if function in named:
        return named[function]
    return "this." + function.split("this.", 1)[1].split("(", 1)[0]


class ScriptedCDP(FakeCDP):
    """The fake page's DevTools session, where one call can hang or fail and a pending animation can be noted."""

    def __init__(
        self,
        page: FakePage,
        *,
        hang: tuple[str, int] | None = None,
        fail: dict[str, Exception] | None = None,
        detach_error: Exception | None = None,
        repairs: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(page)
        self.hang = hang
        self.fail = dict(fail or {})
        self.detach_error = detach_error
        self.repairs = None if repairs is None else [dict(reply) for reply in repairs]
        self.seen: Counter[str] = Counter()

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        key = _key(method, params)
        self.seen[key] += 1
        if self.hang == (key, self.seen[key]):
            await asyncio.Event().wait()
        if key in self.fail:
            raise self.fail[key]
        if self.repairs is not None and key == "note_animations":
            return {"result": {"type": "object", "objectId": "noted"}}
        if self.repairs is not None and key == "poll_animations":
            return {"result": {"value": False}}
        if self.repairs is not None and key == "repair_animations":
            return self.repairs.pop(0)
        return await super().send(method, params)

    async def detach(self) -> None:
        await super().detach()
        if self.detach_error is not None:
            raise self.detach_error


class ScriptedContext:
    def __init__(self, page: FakePage, **options: Any) -> None:
        self.page = page
        self.options = dict(options)  # a copy: parametrized options are shared between runs
        self.cdp: ScriptedCDP | None = None
        self.targets: list[object] = []

    async def new_cdp_session(self, target: object) -> ScriptedCDP:
        self.targets.append(target)
        if self.options.pop("hang_session", False):
            await asyncio.Event().wait()
        self.cdp = ScriptedCDP(self.page, **self.options)
        return self.cdp


class Session:
    """A session with no recorder, that records which operation lease it was entered under."""

    def __init__(self, page: FakePage, *sources: tuple[str, str, str]) -> None:
        self.page = page
        self.operations: list[str] = []
        if sources:
            ledger = SessionPrivacyLedger([value for value, _, _ in sources])
            ledger.note_sources(sources)
            setattr(self, SESSION_PRIVACY_LEDGER_ATTR, ledger)

    @contextlib.asynccontextmanager
    async def operation(self, name: str) -> AsyncIterator[None]:
        self.operations.append(name)
        yield


def _page(**options: Any) -> tuple[FakePage, ScriptedContext]:
    page_options = {key: options.pop(key) for key in list(options) if key not in _CDP_OPTIONS}
    page = FakePage(**page_options)
    context = ScriptedContext(page, **options)
    page.context = context  # type: ignore[assignment]
    return page, context


_CDP_OPTIONS = {"hang", "fail", "detach_error", "repairs", "hang_session"}


async def _shoot(page: FakePage, target: Path, *, root: Path | None = None) -> tuple[int, int]:
    action = {"action": "screenshot", "path": str(target)}
    return await safe_screenshot.redacted_screenshot(Session(page), action, (PASSWORD,), root=root)


def _warnings(logs: Iterable[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["log_level"] == "warning"]


@pytest.fixture(autouse=True)
def _recordings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path)
    monkeypatch.delenv(POLICY_ENV, raising=False)
    monkeypatch.delenv(LEDGER_ENV, raising=False)
    return tmp_path


@pytest.fixture
def short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TIMEOUT_ENV, "0.05")


# --- policies ---------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "refuse", " Refuse ", "", "  "])
def test_the_ledger_policy_refuses_quietly_by_default(raw: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    if raw is not None:
        monkeypatch.setenv(LEDGER_ENV, raw)

    with structlog.testing.capture_logs() as logs:
        assert safe_screenshot.ledger_screenshot_policy() == "refuse"

    assert logs == []


def test_an_unknown_ledger_policy_refuses_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LEDGER_ENV, " Sometimes ")

    with structlog.testing.capture_logs() as logs:
        assert safe_screenshot.ledger_screenshot_policy() == "refuse"

    assert logs == [
        {
            "event": "octowright.screenshot.ledger_policy_unknown",
            "env": "OCTOWRIGHT_LEDGER_SCREENSHOTS",
            "value": "sometimes",
            "log_level": "warning",
        }
    ]


def test_a_handler_installed_by_hand_is_never_the_built_in_redaction() -> None:
    async def handler(**_: Any) -> tuple[int, int]:
        return 1, 0

    session = SimpleNamespace(**{safe_screenshot.AUTHORITY_ATTR: True, safe_screenshot.HANDLER_ATTR: handler})

    assert safe_screenshot.built_in_redaction_applies(session) is False


# --- refusals ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reports", "stage", "message", "reason"),
    [
        (
            ({"remaining": 0, "transitioning": 0},),
            "before",
            "the page changed before the redacted screenshot",
            "page changed",
        ),
        ((CLEAN, {**CLEAN, "changed": 1}), "after", "the page changed after the redacted screenshot", "page changed"),
        (
            ({"changed": 0, "transitioning": 0},),
            "before",
            "classified values are still in the page before the screenshot",
            "value left in the page",
        ),
        (
            (CLEAN, {**CLEAN, "remaining": 3}),
            "after",
            "classified values are still in the page after the screenshot",
            "value left in the page",
        ),
        (
            ({**CLEAN, "transitioning": 1},),
            "before",
            "a view transition is running before the screenshot",
            "view transition",
        ),
        (
            (CLEAN, {**CLEAN, "transitioning": 1}),
            "after",
            "a view transition is running after the screenshot",
            "view transition",
        ),
    ],
    ids=["missing-changed", "changed-after", "missing-remaining", "remaining-after", "running", "running-after"],
)
async def test_each_page_report_refusal_says_what_and_when(
    reports: tuple[dict[str, Any], ...], stage: str, message: str, reason: str, tmp_path: Path
) -> None:
    page, _ = _page(reports=reports)

    with pytest.raises(ScreenshotRefused) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert str(raised.value) == f"{message}; screenshot refused"
    assert raised.value.fields == {"reasons": [reason], "stage": stage, "tiers": [], "args": []}
    assert not (tmp_path / "shot.png").exists()


async def test_a_drawn_view_transition_refuses_before_the_capture(tmp_path: Path) -> None:
    drawn = {"children": [{"pseudoElements": [{"pseudoType": "view-transition"}]}]}
    page, _ = _page(document=drawn)

    with pytest.raises(ScreenshotRefused) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert str(raised.value) == "a view transition is drawn before the screenshot; screenshot refused"
    assert raised.value.fields == {"reasons": ["view transition"], "stage": "before", "tiers": [], "args": []}


async def test_a_document_reply_without_a_root_draws_no_view_transition(tmp_path: Path) -> None:
    class RootlessCDP(ScriptedCDP):
        async def _DOM_getDocument(self, params: dict[str, Any]) -> dict[str, Any]:
            return {}

    page = FakePage()

    class Context(ScriptedContext):
        async def new_cdp_session(self, target: object) -> ScriptedCDP:
            self.cdp = RootlessCDP(self.page)
            return self.cdp

    page.context = Context(page)  # type: ignore[assignment]

    assert await _shoot(page, tmp_path / "shot.png") == (1, 0)
    assert (tmp_path / "shot.png").read_bytes() == PNG


async def test_a_view_transition_that_cannot_be_ended_refuses(tmp_path: Path) -> None:
    page, _ = _page(fail={"end_view_transitions": RuntimeError("target closed")})

    with pytest.raises(ScreenshotRefused) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert str(raised.value) == "a view transition could not be ended; screenshot refused"
    assert raised.value.fields == {"reasons": ["view transition"], "tiers": [], "args": []}


async def test_styles_that_cannot_be_applied_refuse(tmp_path: Path) -> None:
    page, _ = _page(style_error=RuntimeError("no node"), document={"children": [{"nodeId": 5, "nodeType": 1}]})

    with pytest.raises(ScreenshotRefused) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert str(raised.value) == "the page's styles could not be applied; screenshot refused"
    assert raised.value.fields == {"reasons": ["styles not applied"], "tiers": [], "args": []}


async def test_a_page_without_devtools_refuses_with_the_reason(tmp_path: Path) -> None:
    page = FakePage(chromium=False)

    with pytest.raises(ScreenshotRefused) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert str(raised.value) == (
        "a redacted screenshot needs a Chromium page to read what is rendered; screenshot refused"
    )
    assert raised.value.fields == {"reasons": ["no rendered-surface snapshot"], "tiers": [], "args": []}


async def test_a_refusal_before_the_redaction_does_not_try_to_restore(tmp_path: Path) -> None:
    page, _ = _page(fail={"end_view_transitions": RuntimeError("target closed")})

    with structlog.testing.capture_logs() as logs, pytest.raises(ScreenshotRefused):
        await _shoot(page, tmp_path / "shot.png")

    assert [entry["event"] for entry in _warnings(logs)] == ["octowright.macro.screenshot_refused"]


async def test_a_failed_restore_is_logged_by_its_error_type(tmp_path: Path) -> None:
    page, _ = _page(restore_error=RuntimeError("page navigated"))

    with structlog.testing.capture_logs() as logs, pytest.raises(RuntimeError, match="page navigated"):
        await _shoot(page, tmp_path / "shot.png")

    assert _warnings(logs) == [
        {
            "event": "octowright.macro.redacted_screenshot.restore_failed",
            "error": "RuntimeError",
            "log_level": "warning",
        }
    ]


# --- timeouts ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hang",
    [
        ("DOM.enable", 1),  # counting starts
        ("controller", 1),  # the controller is created
        ("this.redact", 1),
        ("this.watch", 1),
        ("this.verify", 1),
        ("DOM.getDocument", 4),  # the drawn view-transition check, after ending, starting and applying styles
        ("DOMSnapshot.captureSnapshot", 1),
        ("Page.captureScreenshot", 1),
        ("this.restore", 1),
    ],
)
@pytest.mark.usefixtures("short_timeout")
async def test_a_step_that_never_answers_is_named_by_the_operation(hang: tuple[str, int], tmp_path: Path) -> None:
    page, _ = _page(hang=hang)

    with pytest.raises(SessionCallTimeoutError) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert str(raised.value).startswith(TIMED_OUT)
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.usefixtures("short_timeout")
async def test_a_stylesheet_read_that_never_answers_is_named_by_the_operation(tmp_path: Path) -> None:
    page, _ = _page(hang=("CSS.getStyleSheetText", 1), sheets={"sheet-1": "body { color: red }"})

    with pytest.raises(SessionCallTimeoutError) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert str(raised.value).startswith(TIMED_OUT)


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"hang": ("end_view_transitions", 1)}, "view transition"),
        (
            {"hang": ("CSS.getComputedStyleForNode", 1), "document": {"children": [{"nodeId": 5, "nodeType": 1}]}},
            "styles not applied",
        ),
        ({"hang_session": True}, "no rendered-surface snapshot"),
    ],
    ids=["end-view-transitions", "apply-styles", "devtools-session"],
)
@pytest.mark.usefixtures("short_timeout")
async def test_a_refusal_caused_by_a_timeout_names_the_operation(
    options: dict[str, Any], reason: str, tmp_path: Path
) -> None:
    page, _ = _page(**options)

    with pytest.raises(ScreenshotRefused) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert raised.value.fields["reasons"] == [reason]
    assert isinstance(raised.value.__cause__, SessionCallTimeoutError)
    assert str(raised.value.__cause__).startswith(TIMED_OUT)


# --- what a run leaves behind -----------------------------------------------------------------


async def test_a_kept_screenshot_leaves_no_listener_and_entered_the_macro_lease(tmp_path: Path) -> None:
    page, context = _page()
    session = Session(page)
    target = tmp_path / "nested" / "deeper" / "shot.png"

    action = {"action": "screenshot", "path": str(target)}
    assert await safe_screenshot.redacted_screenshot(session, action, (PASSWORD,)) == (1, 0)

    assert target.read_bytes() == PNG
    assert session.operations == ["macro_run"]
    assert context.targets == [page]
    assert context.cdp is not None
    assert not any(context.cdp.handlers.values())
    assert page.calls[-1] == "detach"


async def test_an_upper_case_jpeg_suffix_is_captured_as_jpeg(tmp_path: Path) -> None:
    page, _ = _page(image_format="jpeg")

    assert await _shoot(page, tmp_path / "shot.JPEG") == (1, 0)
    assert (tmp_path / "shot.JPEG").read_bytes() == PNG


@pytest.mark.parametrize(
    "options",
    [
        # Each of these steps suppresses its own errors, so only a timeout escapes it.
        {"hang": ("CSS.disable", 1)},
        {"hang": ("Runtime.releaseObjectGroup", 1)},
        {"detach_error": RuntimeError("target closed")},
        # The animation freeze's last repair reply is malformed, so resuming raises.
        {"repairs": [{"result": {"value": 0}}, {"result": None}]},
    ],
    ids=["counting-stops", "controller-disposed", "detach", "animations-resume"],
)
@pytest.mark.usefixtures("short_timeout")
async def test_a_failure_while_releasing_the_page_keeps_the_screenshot(options: dict[str, Any], tmp_path: Path) -> None:
    page, context = _page(**options)

    assert await _shoot(page, tmp_path / "shot.png") == (1, 0)

    assert (tmp_path / "shot.png").read_bytes() == PNG
    assert context.cdp is not None and context.cdp.seen["CSS.disable"] == 1


async def test_an_explicit_root_contains_the_path_instead_of_the_recordings_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path / "recordings")
    root = tmp_path / "chosen"
    page, _ = _page()

    assert await _shoot(page, root / "shot.png", root=root) == (1, 0)
    assert (root / "shot.png").read_bytes() == PNG


async def test_a_path_outside_its_root_names_the_screenshot_path(tmp_path: Path) -> None:
    page, _ = _page()
    outside = tmp_path.parent / "elsewhere" / "shot.png"

    with pytest.raises(ValueError) as raised:
        await _shoot(page, outside)

    assert str(raised.value) == f"screenshot path {str(outside)!r} resolves outside {str(tmp_path)!r}"
    assert page.calls == []


# --- privacy handlers -------------------------------------------------------------------------


_SOURCES = (PASSWORD, "credential", "login.password")


def _handled_session(result: tuple[int, int] | None) -> tuple[Session, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    async def handler(**kwargs: Any) -> tuple[int, int] | None:
        calls.append(kwargs)
        return result

    session = Session(FakePage(), _SOURCES)
    safe_screenshot.enable_redacted_screenshots(session, handler=handler)
    return session, calls


async def test_the_ledger_screenshot_hands_the_handler_the_action_and_values(tmp_path: Path) -> None:
    session, calls = _handled_session((1, 0))
    target = tmp_path / "shot.png"

    await safe_screenshot.ledger_screenshot(session, target, (PASSWORD,))

    assert calls == [{"action": {"action": "screenshot", "path": str(target)}, "sensitive_values": (PASSWORD,)}]


async def test_a_ledger_screenshot_the_handler_refuses_is_attributed(tmp_path: Path) -> None:
    session, _ = _handled_session(None)

    with pytest.raises(ScreenshotRefused) as raised:
        await safe_screenshot.ledger_screenshot(session, tmp_path / "shot.png", (PASSWORD,))

    assert str(raised.value) == (
        "the session's screenshot privacy handler refused the screenshot "
        "(tier credential; argument login.password); screenshot refused"
    )
    assert raised.value.fields == {
        "reasons": ["handler refused"],
        "tiers": ["credential"],
        "args": ["login.password"],
    }


async def test_a_classified_screenshot_the_handler_refuses_is_attributed() -> None:
    session, _ = _handled_session(None)
    action = {"action": "screenshot", "path": "shot.png"}

    with pytest.raises(ScreenshotRefused) as raised:
        await safe_screenshot.dispatch_classified_screenshot(session, action, (PASSWORD,))

    assert str(raised.value) == (
        "classified macro screenshot privacy handler refused the action "
        "(tier credential; argument login.password); screenshot refused"
    )
    assert raised.value.fields == {
        "reasons": ["handler refused"],
        "tiers": ["credential"],
        "args": ["login.password"],
    }


async def test_a_classified_screenshot_without_a_handler_is_refused_by_default() -> None:
    session = Session(FakePage(), _SOURCES)

    with pytest.raises(ScreenshotRefused) as raised:
        await safe_screenshot.dispatch_classified_screenshot(session, {"action": "screenshot"}, (PASSWORD,))

    assert str(raised.value) == (
        "classified macro screenshot requires an explicit privacy handler "
        "(tier credential; argument login.password); screenshot refused"
    )
    assert raised.value.fields == {
        "reasons": ["no privacy handler"],
        "tiers": ["credential"],
        "args": ["login.password"],
    }
