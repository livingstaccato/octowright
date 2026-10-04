# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""No safety refusal is catchable by a macro's ``try`` or ``try_each``.

abe0035c made the credential guard's refusals uncatchable; the SSRF policy's
and the classified-screenshot boundary's were still suppressed. Under
``block-private`` a ``try`` around a navigation to a private host reported the
run as a success, and a ``try_each`` went on to its next branch -- the policy
held (the navigation never happened) but nobody was told, and the macro's
author learned that a refusal reads exactly like a missing cookie banner. The
same for a classified screenshot refused for want of a privacy handler. A
refusal is a verdict on the macro, so every one is a ``SafetyStop``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.safety_stop import SafetyStop
from tests.test_macro_credential_fill_origin import SECRET, _session

pytestmark = pytest.mark.anyio

OWN = "https://app.example.test/"
PRIVATE = "http://127.0.0.1:9/admin"


async def _run(monkeypatch: pytest.MonkeyPatch, session: Any, actions: list[dict[str, Any]], **args: Any) -> Any:
    from octowright.macros import execution

    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    return await execution.run_macro(session, "outer", args)


@pytest.fixture
def block_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")


@pytest.mark.usefixtures("block_private")
async def test_an_ssrf_refusal_inside_try_fails_the_run(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch=OWN, current=OWN)
    with pytest.raises(RuntimeError, match=r"SSRF policy"):
        await _run(monkeypatch, session, [{"action": "try", "actions": [{"action": "navigate", "url": PRIVATE}]}])
    session.page.goto.assert_not_awaited()


@pytest.mark.usefixtures("block_private")
async def test_try_each_does_not_move_past_an_ssrf_refusal(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch=OWN, current=OWN)
    session.press_key = AsyncMock()
    branches = [[{"action": "navigate", "url": PRIVATE}], [{"action": "press_key", "key": "Escape"}]]
    with pytest.raises(RuntimeError, match=r"SSRF policy"):
        await _run(monkeypatch, session, [{"action": "try_each", "branches": branches}])
    session.press_key.assert_not_awaited()


async def test_a_refused_classified_screenshot_inside_try_fails_the_run(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS", raising=False)
    session = _session(tmp_path, launch=OWN, current=OWN)
    actions = [
        {"action": "fill", "selector": "#pw", "value": "{{password}}"},
        {"action": "try", "actions": [{"action": "screenshot", "path": "shot.png"}]},
    ]
    with pytest.raises(RuntimeError, match=r"screenshot refused") as caught:
        await _run(monkeypatch, session, actions, password=SECRET)
    assert SECRET not in str(caught.value)


async def test_try_each_does_not_move_past_a_refused_screenshot(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS", raising=False)
    session = _session(tmp_path, launch=OWN, current=OWN)
    session.press_key = AsyncMock()
    actions = [
        {"action": "fill", "selector": "#pw", "value": "{{password}}"},
        {
            "action": "try_each",
            "branches": [[{"action": "screenshot", "path": "shot.png"}], [{"action": "press_key", "key": "Escape"}]],
        },
    ]
    with pytest.raises(RuntimeError, match=r"screenshot refused"):
        await _run(monkeypatch, session, actions, password=SECRET)
    session.press_key.assert_not_awaited()


async def test_an_unsafe_navigation_that_is_not_a_refusal_is_still_suppressed(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The policy is off: an ordinary navigation failure stays best-effort.
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)
    session = _session(tmp_path, launch=OWN, current=OWN)
    session.page.goto.side_effect = TimeoutError("slow")
    result = await _run(monkeypatch, session, [{"action": "try", "actions": [{"action": "navigate", "url": PRIVATE}]}])
    assert result["skipped"] >= 1


def test_every_safety_refusal_is_a_safety_stop_and_keeps_its_old_type() -> None:
    from octowright.credential_sinks import CredentialSafetyStop
    from octowright.macros.screenshot_refusal import ScreenshotRefused
    from octowright.request_errors import InvalidRequestError
    from octowright.ssrf import SsrfRefusal
    from octowright.ssrf_guard import FrameChain

    assert issubclass(CredentialSafetyStop, SafetyStop)
    assert issubclass(ScreenshotRefused, SafetyStop)
    assert issubclass(ScreenshotRefused, RuntimeError)
    assert issubclass(SsrfRefusal, SafetyStop)
    # Every caller that classified an SSRF refusal as the caller's own input still does.
    assert issubclass(SsrfRefusal, InvalidRequestError)
    chain = FrameChain()
    chain.end("refused", failed=False)
    assert isinstance(chain.error(), SsrfRefusal)
    chain.end("reset", failed=True)
    assert not isinstance(chain.error(), SafetyStop)


@pytest.mark.usefixtures("block_private")
async def test_every_ssrf_check_raises_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import ssrf

    with pytest.raises(ssrf.SsrfRefusal):
        ssrf.check_navigation_url(PRIVATE)
    with pytest.raises(ssrf.SsrfRefusal):
        await ssrf.check_navigation_url_resolved(PRIVATE)
    monkeypatch.setattr(ssrf, "_getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("10.0.0.1", 0))])
    with pytest.raises(ssrf.SsrfRefusal):
        await ssrf.check_navigation_url_resolved("http://intranet.example.test/")
    ssrf._subresource_verdicts.clear()
    with pytest.raises(ssrf.SsrfRefusal):
        await ssrf.check_request_url_cached("http://intranet2.example.test/")
