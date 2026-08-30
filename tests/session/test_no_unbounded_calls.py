# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Fail if a new Playwright call reintroduces the 2026-08-29 hang.

The incident: ``page.title()`` / ``page.content()`` / ``page.evaluate()`` (and
their ``Frame`` equivalents) take no ``timeout`` at all, so a target that
stops answering hangs the calling coroutine forever -- a full ``make ci`` run
wedged for 12.6 hours against a broken WebKit. Task 1 wrapped every known
site in ``octowright.session.timeouts.bounded()``; F2 of its review found 17
more that hand enumeration had missed. This is the AST-scan backstop the
review demanded so a THIRD round is not required by hand: nothing here fails
if a refactor quietly reverts a site to a bare ``await self.page.title()``,
which is exactly the gap ``tests/aria_redaction/test_no_unscrubbed_sinks.py``
closes for the credential-scrubbing sinks ("the leak was not one bug in one
place"), and the pattern this file follows.

``Locator.evaluate()`` is explicitly OUT of scope: unlike ``Page``/``Frame``,
it accepts a real ``timeout=`` (and falls back to Playwright's own default
action timeout when omitted), so it is already bounded and must keep using
that parameter rather than being wrapped a second time.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "octowright"

# .title()/.content() exist only on Page/Frame -- Locator has neither, so
# these two are never ambiguous. .evaluate() exists on both Page/Frame AND
# Locator (with a real timeout=), so it needs the receiver-based exclusions
# below.
_UNBOUNDED_METHODS = frozenset(
    {
        "title",
        "content",
        "evaluate",
        # Context/page SETUP calls, added after a second incident. Playwright
        # gives these no `timeout` either, and on a WebKit build that could not
        # navigate to about:blank they were measured never returning at all --
        # while `evaluate` on the same page still answered in ~6s. Launch
        # therefore wedged in `expose_binding`, several steps BEFORE the
        # `page.goto` whose own 30s timeout would have surfaced the broken
        # engine as an ordinary error. The original three methods are the ones
        # a running page answers; these are the ones a launch depends on, and
        # missing them meant the guard could not see the hang that actually
        # happened.
        "add_init_script",
        "expose_binding",
        "expose_function",
        "route",
        "unroute",
    }
)

# Narrow, commented allowlist: calls this scanner would otherwise flag that
# are legitimately unbounded today. Each entry names the reason so a future
# reader can tell "known and accepted" from "nobody looked yet".
#
# Keyed by (enclosing function, receiver expression), NOT by line number. Line
# numbers were the original key and they drift: an unrelated edit ABOVE a call
# site silently shifts it out of the allowlist, and the scanner then reports a
# long-accepted exemption as a new offence. That happened for real when a
# feature branch added lines above `_is_password_input` -- the failure named a
# call nobody had touched. A function name plus the receiver text moves with
# the code and stays meaningful in the diff.
ALLOWED: dict[str, frozenset[tuple[str, str]]] = {
    # Locator.evaluate(..., timeout=...) -- a real, honoured timeout, not
    # Page/Frame's timeout-less method. The receiver is a bare `locator`
    # PARAMETER, not an inline `.locator(...)` chain this scanner could
    # recognise structurally, so it needs an explicit entry.
    "session/aria_redaction.py": frozenset({("collect_credential_values", "locator.first")}),
    # Same shape: `locator.first.evaluate(...)` on a Locator parameter.
    "session/core_locator_mixin.py": frozenset({("_is_password_locator", "locator.first")}),
    # `source = target.locator(source_selector)` a few statements above, then
    # `source.evaluate(...)` -- a genuine Locator.evaluate, already bounded by
    # Playwright's own action timeout.
    "session/a11y_dragdrop.py": frozenset({("run_a11y_dragdrop", "source")}),
    "session/core_page_mixin.py": frozenset(
        {
            # `loc = self._target().locator(selector).first` two statements
            # above, then `loc.evaluate(...)` -- a genuine Locator.evaluate,
            # just not inline where a syntactic check could see `.locator(`.
            ("_is_password_input", "loc"),
            # `_evaluate_truthy`'s `target.evaluate(expression)` is the
            # predicate `_poll_until` calls on every iteration of a
            # `wait_for`/`expect_js` poll loop. It is genuinely unbounded
            # today -- a wedge here can outlast `_poll_until`'s own
            # `timeout_ms` -- but it is deliberately NOT part of the
            # per-call-site fix. It is the gap the active-duration ceiling
            # (off by default) exists to backstop: "the ones nobody has
            # found yet". See docs/superpowers/plans/2026-08-29-hang-resilience.md.
            ("_evaluate_truthy", "target"),
        }
    ),
}

# session.evaluate(...) calls SessionPageMixin.evaluate() (session/core_page_
# mixin.py), which is itself already wrapped in bounded() -- it is the
# session's own high-level method, not raw Playwright access, so a call
# site that reaches it is not a new unbounded sink. Structural rather than
# an ever-growing per-line allowlist: every server/macro module that hands a
# JS expression through to the page goes via this one indirection, by
# convention always through a variable literally named `session`.
_SESSION_WRAPPER_RECEIVER = "session"


def _wrapped_call_ids(tree: ast.AST) -> set[int]:
    """id() of every Call node that is bounded()'s first positional argument."""
    wrapped: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "bounded"
            and node.args
            and isinstance(node.args[0], ast.Call)
        ):
            wrapped.add(id(node.args[0]))
    return wrapped


def _is_session_wrapper_call(receiver: ast.AST) -> bool:
    return isinstance(receiver, ast.Name) and receiver.id == _SESSION_WRAPPER_RECEIVER


def _enclosing_function(tree: ast.AST, node: ast.AST) -> str:
    """Innermost def enclosing *node*, or "<module>" for a top-level call.

    Half of the allowlist key. Together with the receiver text it survives
    edits above the call site, which a line number does not.
    """
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for candidate in ast.walk(tree):
        if not isinstance(candidate, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        end = candidate.end_lineno or candidate.lineno
        if candidate.lineno <= node.lineno <= end and (best is None or candidate.lineno > best.lineno):
            best = candidate
    return best.name if best is not None else "<module>"


def _unbounded_calls(path: Path) -> list[tuple[int, str, str, str]]:
    """(lineno, method, enclosing function, receiver) per unwrapped call.

    ``lineno`` is carried for the failure message only -- it is deliberately
    NOT part of the allowlist key.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    wrapped = _wrapped_call_ids(tree)
    offenders: list[tuple[int, str, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method = node.func.attr
        if method not in _UNBOUNDED_METHODS:
            continue
        if id(node) in wrapped:
            continue
        if method == "evaluate" and _is_session_wrapper_call(node.func.value):
            continue
        offenders.append((node.lineno, method, _enclosing_function(tree, node), ast.unparse(node.func.value)))
    return offenders


def test_no_unbounded_title_content_or_evaluate_calls() -> None:
    offenders = {}
    for path in sorted(SRC.rglob("*.py")):
        # as_posix(), not str(): on Windows str() yields backslash separators
        # ('session\\aria_redaction.py'), which never match ALLOWED's forward-slash
        # keys -- so every allowlisted line reported as an offender and this test
        # failed on both Windows legs while passing on linux and macOS.
        rel = path.relative_to(SRC).as_posix()
        allowed = ALLOWED.get(rel, frozenset())
        found = [
            f"L{lineno} {func}(): {recv}.{method}()"
            for lineno, method, func, recv in _unbounded_calls(path)
            if (func, recv) not in allowed
        ]
        if found:
            offenders[rel] = found
    assert not offenders, (
        "a Playwright Page/Frame .title()/.content()/.evaluate() call is not "
        "wrapped in octowright.session.timeouts.bounded() -- a wedged target "
        "hangs this call forever (the 2026-08-29 incident). Wrap it, or add a "
        "commented ALLOWED entry, keyed (enclosing function, receiver), if it "
        f"is genuinely bounded another way: {offenders}"
    )


def test_the_guard_can_actually_see_an_unbounded_call(tmp_path: Path) -> None:
    """A scanner that never matches would pass the test above forever."""
    sample = tmp_path / "sink.py"
    sample.write_text("async def f(page):\n    return await page.title()\n")
    assert _unbounded_calls(sample) == [(2, "title", "f", "page")]


def test_bounded_wrapping_is_recognised(tmp_path: Path) -> None:
    """A genuinely-wrapped call must NOT be flagged, or every real site fails."""
    sample = tmp_path / "sink.py"
    sample.write_text(
        "from octowright.session.timeouts import bounded\n"
        "async def f(page):\n"
        '    return await bounded(page.title(), operation="x")\n'
    )
    assert _unbounded_calls(sample) == []


def test_locator_evaluate_with_an_inline_locator_call_is_still_flagged(tmp_path: Path) -> None:
    """Only the hand-curated ALLOWED entries are exempt -- an inline
    `.locator(...)` chain is NOT auto-recognised as safe. This scanner
    trades a few extra allowlist entries for never silently trusting a new
    receiver shape it has not been told about."""
    sample = tmp_path / "sink.py"
    sample.write_text('async def f(page):\n    return await page.locator("x").evaluate("() => 1")\n')
    assert _unbounded_calls(sample) == [(2, "evaluate", "f", "page.locator('x')")]


def test_session_evaluate_wrapper_call_is_not_flagged(tmp_path: Path) -> None:
    sample = tmp_path / "sink.py"
    sample.write_text("async def f(session):\n    return await session.evaluate('1')\n")
    assert _unbounded_calls(sample) == []
