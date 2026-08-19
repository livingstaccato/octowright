# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from provide.telemetry import get_logger

from octowright import ssrf
from octowright._tracing import histogram, span
from octowright.defaults import (
    DEFAULT_ACTION_TIMEOUT_MS,
    DEFAULT_NAV_TIMEOUT_MS,
    REDACTED_INPUT_PLACEHOLDER,
)
from octowright.session._protocols import SessionLike
from octowright.session.operation_gate import gated_operation
from octowright.session.screencast import notify_active_page

log = get_logger(__name__)

# Recognised values for the OCTOWRIGHT_REDACT_INPUTS env var. Re-read on
# every type/fill call so monkeypatched env changes (and operator
# adjustments without a restart) take effect immediately.
_REDACTION_MODES: frozenset[str] = frozenset({"off", "passwords", "all"})


def _current_redaction_mode() -> str:
    """Return the effective input-redaction mode for the current call.

    Reads ``OCTOWRIGHT_REDACT_INPUTS`` at call time. Unknown values fall
    back to ``"passwords"`` (safer default than silently disabling
    redaction) and emit a one-time debug log so a typo is at least
    observable.
    """
    raw = os.environ.get("OCTOWRIGHT_REDACT_INPUTS", "passwords").strip().lower() or "passwords"
    if raw not in _REDACTION_MODES:
        log.debug(
            "core_page_mixin.unknown_redaction_mode",
            value=raw,
            fallback="passwords",
            supported=sorted(_REDACTION_MODES),
        )
        return "passwords"
    return raw


def _redact_sink_value(value: str | None) -> str | None:
    """Redact a recorded value that has no inspectable field to classify it.

    ``fill``/``type`` consult the target element to tell a credential from a
    benign value; ``press_key`` (key), ``evaluate`` (expression), and
    ``select_option`` (value/label) carry no such element. The only coherent
    rule for those sinks is: scrub under the blanket ``all`` mode, leave raw
    under ``off``/``passwords`` (which key off element type and so can't reason
    about a selector-less value). ``None`` passes through unchanged (an absent
    optional arg). The page action always receives the real value — only the
    JSONL record sees this result.
    """
    if value is None:
        return None
    return REDACTED_INPUT_PLACEHOLDER if _current_redaction_mode() == "all" else value


_NAVIGATE_DURATION = histogram(
    "octowright_session_navigate_duration_seconds",
    description="Duration of session.navigate() including page.goto",
    unit="s",
)

# Schemes the MCP/HTTP navigate paths refuse to send to Playwright.
# - file://     reads local files; combined with snapshot/read_markdown
#               tools this is a clean local-file exfiltration vector.
# - javascript: executes arbitrary script in the current page context,
#               bypassing the explicit browser_evaluate audit trail.
# - chrome:/chrome-extension:/view-source: similarly privileged browser-
#               internal schemes.
# data:, http, https, ws/wss, about: are not blocked. data: in particular
# is used by tests for in-memory launches and is reasonable for the
# operator-controlled launch URL; it cannot read local files.
_NAV_DENIED_SCHEMES = frozenset({"file", "javascript", "chrome", "chrome-extension", "view-source"})


def _sanitize_url_for_span(url: str) -> str:
    """Strip credentials + query string from ``url`` before stamping it as a span attribute.

    Query strings on navigation targets routinely carry session tokens, signed
    URLs, account IDs, and other PII that we do not want to land in traces /
    exporter backends. ``user:pass@`` basic-auth userinfo is even more
    sensitive — a cleartext credential — so it is dropped too. The full URL
    still goes to ``self.url`` and the recorder's ``navigate`` event — only the
    span attribute is sanitized.

    Userinfo is removed by dropping everything up to the last ``@`` in the
    netloc (the RFC-3986 userinfo delimiter), which preserves ``host:port``
    verbatim — including original case and IPv6 brackets — unlike rebuilding
    from ``.hostname``/``.port``.

    Falls back to the original value if parsing fails for any reason; the
    sanitization is best-effort and must never block a navigation.
    """
    try:
        parts = urlsplit(url)
        netloc = parts.netloc.rsplit("@", 1)[-1]
        return parts._replace(query="", netloc=netloc).geturl()
    except Exception:
        return url


#: ASCII tab / LF / CR. The WHATWG URL parser REMOVES these from a URL outright
#: (they are not encoded, not rejected — deleted), so they can be used to hide
#: the second slash of an authority from a naive string test.
_URL_STRIPPED_CONTROLS = {0x09: None, 0x0A: None, 0x0D: None}

#: Every C0 control or space (U+0000-U+0020). The WHATWG parser strips all of
#: these from both ends BEFORE parsing; ``str.strip()`` removes only Python
#: whitespace, so ``\x01file:///etc/passwd`` partitioned to a scheme of
#: ``\x01file`` -- absent from the deny-list -- while Chromium stripped the
#: control and loaded the file. Confirmed live against headless Chromium.
_C0_OR_SPACE = "".join(chr(c) for c in range(0x21))


def _canonicalize_for_guard(url: str) -> str:
    """Fold a URL into the one spelling the guard's string tests reason about.

    The guard decides "is this same-origin?" by looking for a leading ``//``.
    That is a valid reading of RFC 3986 and the wrong reading of the WHATWG URL
    Standard, which is what Chromium/Firefox/WebKit and Playwright's ``base_url``
    resolution actually implement. WHATWG gives an authority several spellings:

    * ``\\`` is equivalent to ``/`` for a special scheme (http/https), so
      ``/\\evil.test/x`` is an authority — it resolves to host ``evil.test``;
    * tab/LF/CR are deleted before parsing, so ``/<TAB>/evil.test/x`` becomes
      ``//evil.test/x`` — also host ``evil.test``.

    Both passed a ``startswith("//")`` test while reaching a different host, which
    turned the host-relative relaxation into an SSRF-policy bypass (a poisoned
    macro navigating ``/\\169.254.169.254/latest/meta-data/`` skipped the host
    check entirely). Canonicalizing first makes the string test agree with the
    parser that ultimately resolves the value.

    Note this is used ONLY to classify and check the URL; the original string is
    what gets handed to Playwright, so no caller's URL is rewritten.
    """
    return url.strip(_C0_OR_SPACE).translate(_URL_STRIPPED_CONTROLS).replace("\\", "/")


def _reject_unsafe_url(url: str) -> None:
    """Raise ValueError if ``url`` is on the deny-list of unsafe schemes, or the
    active ``OCTOWRIGHT_SSRF_POLICY`` refuses its host. Every navigation entry
    point (navigate / open_url / launch) and macro replay routes through here, so
    one call covers them all."""
    if not isinstance(url, str) or not url:
        raise ValueError("navigate url must be a non-empty string")
    stripped = _canonicalize_for_guard(url)
    # One leading slash is a path on the context's own base_url: same origin by
    # construction, so there is no scheme to deny and no new host to check. This
    # is what lets a macro navigate '/orders' and stay portable across
    # deployments. TWO slashes is protocol-relative -- '//evil.test/x' resolves
    # to a DIFFERENT host -- so it falls through to the absolute checks below.
    # The base_url it resolves against is validated where it is set.
    #
    # This test is only sound on a CANONICALIZED string: WHATWG has more than one
    # spelling of an authority, and `_canonicalize_for_guard` folds them all into
    # the `//` form before we get here (see its docstring).
    if stripped.startswith("/") and not stripped.startswith("//"):
        return
    scheme, sep, _rest = stripped.partition(":")
    if not sep:
        raise ValueError(f"navigate url missing scheme: {url!r}")
    if scheme.lower() in _NAV_DENIED_SCHEMES:
        raise ValueError(f"navigate url scheme {scheme!r} is not allowed (blocked: {sorted(_NAV_DENIED_SCHEMES)})")
    ssrf.check_navigation_url(stripped)


async def _body_contains_text(session: SessionLike, body: Any, text: str) -> bool:
    # Re-enters the parent's "browser_wait_for" lease reentrantly (same task,
    # so this never queues) rather than assuming the caller already holds it --
    # a direct caller of this module-level helper gets the same coherence
    # guarantee as going through wait_for().
    async with session.operation("browser_wait_for"):
        return text in await body.inner_text(timeout=1000)


async def _evaluate_truthy(session: SessionLike, target: Any, expression: str) -> bool:
    async with session.operation("browser_wait_for"):
        return bool(await target.evaluate(expression))


class SessionPageMixin(SessionLike):
    _last_mcp_navigation: str | None

    @gated_operation("page_list")
    async def list_pages(self) -> list[dict[str, Any]]:
        """Return [{index, url, title, is_active}, ...]. title is None for unloaded pages."""
        result = []
        for i, p in enumerate(self.pages):
            try:
                url = p.url
            except Exception:
                url = None
            result.append(
                {
                    "index": i,
                    "url": url,
                    "title": None,  # title requires async; callers can use browser_evaluate
                    "is_active": p is self.page,
                }
            )
        return result

    @gated_operation("page_switch")
    async def switch_page(self, index: int) -> dict[str, Any]:
        """Set self.page to self.pages[index]. Raises IndexError if out of bounds."""
        if index < 0 or index >= len(self.pages):
            raise IndexError(f"page index {index} out of range (0..{len(self.pages) - 1})")
        selected_page = self.pages[index]
        self.page = selected_page
        await notify_active_page(self.instance_id, selected_page)
        selected_url = selected_page.url
        self.recorder.record("switch_page", index=index, url=selected_url)
        return {"index": index, "url": selected_url, "page_count": len(self.pages)}

    @gated_operation("page_close")
    async def close_page(self, index: int) -> dict[str, Any]:
        """Close self.pages[index] and remove it from the list.

        If the closed page was active, switches to the first remaining page.
        Raises RuntimeError if this would close the last page.

        Removal-before-await is intentional: Playwright's ``page.close``
        fires its ``_on_page_close`` listener synchronously, and that
        listener evaluates ``[p for p in session.pages if not p.is_closed()]``
        to decide whether to cascade a full session eviction. If the closing
        page is still in ``self.pages`` when the listener runs AND a sibling
        page closes in the same tick (popup auto-close, concurrent close
        race), the comprehension comes up empty and fires a spurious
        eviction — then the post-await ``self.pages.index(self.page)`` here
        raises ValueError on the cleared session. Pop first so the
        listener's view always reflects the truth: this page is being
        closed deliberately by us.
        """
        if len(self.pages) <= 1:
            raise RuntimeError("cannot close the last remaining page; use browser_close to shut the whole instance")
        if index < 0 or index >= len(self.pages):
            raise IndexError(f"page index {index} out of range (0..{len(self.pages) - 1})")
        target = self.pages[index]
        was_active = target is self.page
        # Reassign self.page BEFORE the pop so neither pop nor the
        # synchronous _on_page_close ever sees self.page dangling at a
        # popped index. The len(self.pages) > 1 guard above means a
        # non-target sibling is always present when ``was_active``; we
        # still snapshot here to defend against a concurrent popup-close
        # listener mutating self.pages between the guard and this lookup.
        if was_active:
            sibling = next((p for p in self.pages if p is not target), None)
            if sibling is None:
                raise RuntimeError("no sibling page available; another close raced ahead")
            self.page = sibling
        self.pages.pop(index)
        await target.close()
        if was_active:
            await notify_active_page(self.instance_id, self.page)
        self.recorder.record("close_page", index=index, was_active=was_active)
        # Re-derive active_index after the await: a popup _on_page_close
        # listener can fire synchronously during target.close() and mutate
        # self.pages. self.page may no longer be in self.pages, in which case
        # we fall back to 0 rather than raising ValueError on the return.
        active_index = self.pages.index(self.page) if self.page in self.pages else 0
        return {
            "closed_index": index,
            "was_active": was_active,
            "active_index": active_index,
            "page_count": len(self.pages),
        }

    @gated_operation("browser_navigate")
    async def navigate(self, url: str) -> dict[str, Any]:
        _reject_unsafe_url(url)
        instance_id = getattr(self, "instance_id", None)
        kind = getattr(self, "kind", None)
        t0 = time.perf_counter()
        with span(
            "octowright.session.navigate",
            instance_id=instance_id,
            kind=kind,
            url=_sanitize_url_for_span(url),
        ):
            # Tag the upcoming framenavigated event so pool's user_navigation
            # listener skips it (we already record "navigate" below).
            prior_mcp_navigation = getattr(self, "_last_mcp_navigation", None)
            self._last_mcp_navigation = url
            try:
                await self.page.goto(url, timeout=DEFAULT_NAV_TIMEOUT_MS)
            except BaseException:
                # Reset the dedupe tag on failure: if the user then navigates to
                # the same URL manually, that's a genuine user_navigation event
                # and should not be suppressed.
                self._last_mcp_navigation = prior_mcp_navigation
                raise
            title = await self.page.title()
            self.url = url
            self._schedule_markdown_capture()
            self.recorder.record("navigate", url=url)
            _NAVIGATE_DURATION.record(time.perf_counter() - t0, attributes={"kind": kind or "unknown"})
            return {"url": url, "title": title}

    @gated_operation("session_input_metadata")
    async def _resolve_semantic_metadata(self, selector: str) -> dict[str, str]:
        """Attempt to resolve the role and role_name of the element at selector."""
        try:
            # Playwright 1.50+ aria_snapshot() returns a YAML-like string for the locator.
            # Example: '- button "Confirm Order"'
            loc = self._target().locator(selector)
            snapshot = await loc.aria_snapshot()
            if snapshot and snapshot.startswith("- "):
                line = snapshot[2:].strip()
                # line is e.g. 'button "Confirm Order"'
                parts = line.split(' "', 1)
                role = parts[0]
                role_name = parts[1].rstrip('"') if len(parts) > 1 else ""
                return {"role": role, "role_name": role_name}
        except Exception:
            pass
        return {}

    @gated_operation("browser_click")
    async def click(self, selector: str) -> None:
        meta = await self._resolve_semantic_metadata(selector)
        await self._target().click(selector, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("click", selector=selector, **meta)

    @gated_operation("session_input_redaction")
    async def _is_password_input(self, selector: str) -> bool:
        """Best-effort check: does *selector* resolve to a credential input?

        Treats both ``type=password`` AND ``autocomplete in {current-password,
        new-password, one-time-code}`` as credential-bearing so SPAs that
        implement custom password fields as ``<input type=text>`` with the
        appropriate autocomplete hint still get scrubbed.

        Uses ``locator.first.evaluate(...)`` so multi-match selectors don't
        raise. Any Playwright/JS error fails closed to ``True`` so a selector
        that disappears around a typing/fill action cannot write cleartext
        credentials into the JSONL recording.
        """
        try:
            loc = self._target().locator(selector).first
            # Read both el.autocomplete (the IDL property — only present on
            # form-control elements) and el.getAttribute('autocomplete') (the
            # raw attribute — present on any element that declares it). Custom
            # elements / <div contenteditable> declare autocomplete via the
            # attribute, not the property, so the property-only read would
            # silently leak.
            info = await loc.evaluate(
                "el => el ? {"
                "  type: el.type ? String(el.type).toLowerCase() : '',"
                "  ac: el.autocomplete ? String(el.autocomplete).toLowerCase() : ''"
                "    || (el.getAttribute && el.getAttribute('autocomplete')"
                "         ? String(el.getAttribute('autocomplete')).toLowerCase() : '')"
                "} : {type: '', ac: ''}"
            )
        except Exception as exc:
            log.debug("core_page_mixin.password_lookup_failed", selector=selector, error=str(exc))
            return True
        # New shape: {type, ac}. Legacy callers / tests may stub `evaluate`
        # to return a bare string (the old behaviour); accept that shape too
        # so the redaction policy stays the same for callers that haven't
        # upgraded their mocks.
        if isinstance(info, str):
            return info == "password"
        if not isinstance(info, dict):
            return True
        if info.get("type") == "password":
            return True
        return info.get("ac") in ("current-password", "new-password", "one-time-code")

    @gated_operation("session_input_redaction")
    async def _redacted_or_original(self, selector: str, value: str) -> str:
        """Return ``REDACTED_INPUT_PLACEHOLDER`` if the current redaction
        policy says to scrub this value, else *value* unchanged. The page
        action itself always receives the original value — only the JSONL
        record sees the result of this call."""
        mode = _current_redaction_mode()
        if mode == "off":
            return value
        if mode == "all":
            return REDACTED_INPUT_PLACEHOLDER
        # mode == "passwords"
        if await self._is_password_input(selector):
            return REDACTED_INPUT_PLACEHOLDER
        return value

    @gated_operation("browser_type")
    async def type_text(self, selector: str, text: str, delay_ms: int | None) -> None:
        meta = await self._resolve_semantic_metadata(selector)
        recorded_text = await self._redacted_or_original(selector, text)
        await self._target().type(selector, text, delay=delay_ms or 0, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("type", selector=selector, text=recorded_text, delay_ms=delay_ms, **meta)

    @gated_operation("browser_fill")
    async def fill(self, selector: str, value: str) -> None:
        meta = await self._resolve_semantic_metadata(selector)
        recorded_value = await self._redacted_or_original(selector, value)
        await self._target().fill(selector, value, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("fill", selector=selector, value=recorded_value, **meta)

    @gated_operation("browser_press_key")
    async def press_key(self, key: str) -> None:
        await self.page.keyboard.press(key)
        self.recorder.record("press_key", key=_redact_sink_value(key))

    @gated_operation("browser_screenshot")
    async def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write via the shared helper — defeats the symlink-swap
        # window between the caller's containment check and Playwright's
        # write. See ``octowright._paths.atomic_write_via_writer`` for the
        # full reasoning. The temp sibling has a ``.tmp`` suffix so the
        # final path suffix is the only signal of image format; pass it
        # explicitly so Playwright doesn't try to infer from ``.tmp``.
        img_type: Literal["jpeg", "png"] = "jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "png"

        async def _write(tmp: Path) -> None:
            # Nested closure is its own scope, independent of the decorator
            # above (Task 11 scanner rule) -- re-enters the same "browser_screenshot"
            # lease this method's own @gated_operation already holds (same task).
            async with self.operation("browser_screenshot"):
                await self.page.screenshot(path=str(tmp), type=img_type)

        from octowright._paths import atomic_write_via_writer

        await atomic_write_via_writer(path, _write)
        self.recorder.record("screenshot", path=str(path))
        return path

    @gated_operation("browser_snapshot")
    async def snapshot(self, selector: str | None = None) -> dict[str, Any]:
        # Route through _target() so a switched frame's aria-tree is what you see —
        # every action tool (click/fill/evaluate/wait_for) already respects the
        # active frame; snapshot must too, or you can act in a frame you can't inspect.
        # selector=None preserves the legacy "html"-root JSONL event so existing
        # macro replays / golden diffs don't drift; explicit selectors are recorded.
        target = self._target()
        aria_yaml = await target.locator(selector or "html").aria_snapshot()
        record_kwargs = {"selector": selector} if selector is not None else {}
        self.recorder.record("snapshot", **record_kwargs)
        # url comes from the snapshotted document (frame when active); title is
        # page-level — Playwright Frames have no title().
        return {"aria": aria_yaml, "url": target.url, "title": await self.page.title()}

    @gated_operation("browser_evaluate")
    async def evaluate(self, expression: str) -> Any:
        result = await self._target().evaluate(expression)
        self.recorder.record("evaluate", expression=_redact_sink_value(expression))
        return result

    @gated_operation("browser_wait_for")
    async def wait_for(
        self,
        selector: str | None,
        text: str | None,
        timeout_ms: int | None,
        expression: str | None = None,
    ) -> None:
        """Block until one of the supplied conditions becomes true.

        Exactly one of ``selector`` / ``text`` / ``expression`` may be set;
        with all three None the call waits for ``networkidle``. ``expression``
        is evaluated repeatedly with ``evaluate`` until it returns truthy — useful for
        compound conditions like "spinner removed AND table has rows" that
        a single selector can't express. The text and expression branches avoid
        ``wait_for_function`` because CSP-protected sites can reject its injected
        eval path even when normal page reads and evaluates work.
        """
        # Truthiness, not `is not None`: an empty string from a hand-edited
        # macro shouldn't count as "selector provided" — and the if/elif
        # chain below already routes via truthiness, so the validation needs
        # to match to keep the error message consistent with reality.
        provided = sum(1 for x in (selector, text, expression) if x)
        if provided > 1:
            raise ValueError(
                "wait_for accepts at most one of selector / text / expression; "
                f"got selector={bool(selector)}, text={bool(text)}, expression={bool(expression)}"
            )
        # Distinguish "no timeout supplied" (None → fall back to default) from
        # "wait forever" (0 → Playwright's documented sentinel). The old
        # `timeout_ms or DEFAULT` collapsed both to default.
        timeout = DEFAULT_ACTION_TIMEOUT_MS if timeout_ms is None else timeout_ms
        target = self._target()
        if selector:
            await target.wait_for_selector(selector, timeout=timeout)
            self.recorder.record("wait_for", selector=selector, timeout_ms=timeout)
        elif text:
            body = target.locator("body")
            await self._poll_until(timeout, lambda: _body_contains_text(self, body, text), f"text={text!r}")
            self.recorder.record("wait_for", text=text, timeout_ms=timeout)
        elif expression:
            await self._poll_until(
                timeout, lambda: _evaluate_truthy(self, target, expression), f"expression={expression!r}"
            )
            self.recorder.record("wait_for", expression=expression, timeout_ms=timeout)
        else:
            await self.page.wait_for_load_state("networkidle", timeout=timeout)
            self.recorder.record("wait_for", timeout_ms=timeout)
