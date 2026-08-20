# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Browser-event interaction surface for ``BrowserSession``: downloads,
dialogs, route mocking, and file-input upload.

Each of these is glue between Playwright's event-driven APIs and the
session's recorder + bookkeeping. They share little with the locator or
network-capture concerns; isolating them here keeps each mixin focused
and trims ``core_ops_mixin`` below the LOC ratchet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from provide.telemetry import get_logger

from octowright.http_headers import (
    REDACTED_HEADER_PLACEHOLDER,
    redact_header_values,
    validate_extra_http_headers,
)
from octowright.session._protocols import SessionLike
from octowright.session.aria_redaction import resolve_redaction_mode
from octowright.session.operation_gate import gated_operation

log = get_logger(__name__)

if TYPE_CHECKING:
    from octowright.session.core import BrowserSession


def _reject_redacted_headers(headers: dict[str, str]) -> None:
    """Refuse to replay a value the recorder scrubbed.

    A macro saved from a recording carries whatever the JSONL holds, and for a
    credential header that is the placeholder, not the token. Sending it would
    authenticate as nobody and surface as a confusing 401 several actions
    later; failing here names the fix instead.
    """
    if not isinstance(headers, dict):
        return
    scrubbed = sorted(name for name, value in headers.items() if value == REDACTED_HEADER_PLACEHOLDER)
    if scrubbed:
        raise ValueError(
            f"header(s) {', '.join(scrubbed)} still hold the recorder's redaction placeholder -- "
            "the recording never stored the real value. Parameterize the macro "
            '(e.g. "Authorization": "Bearer {{token}}") and pass it at run time.'
        )


class SessionInteractionMixin(SessionLike):
    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------

    def _handle_download(self, download: Any) -> None:
        """Registered as page.on('download', ...). Schedules an async save, appends a
        record to self.downloads once the file lands on disk."""
        import asyncio

        from octowright.session import downloads as _downloads

        # Fire-and-forget: Playwright dispatches downloads synchronously but saving is async.
        # Task reference is kept on the session to prevent GC collecting it mid-flight (RUF006).
        task = asyncio.create_task(_downloads.save_download(cast("BrowserSession", self), download))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def list_downloads(self) -> list[dict[str, Any]]:
        return list(self.downloads)

    async def wait_for_download(self, timeout_ms: int = 15000) -> dict[str, Any]:
        """Block until the next download completes (save-to-disk). Raises TimeoutError
        if no download arrives within timeout_ms. Returns the new download record."""
        from octowright.session import downloads as _downloads

        return await _downloads.wait_for_download_impl(cast("BrowserSession", self), timeout_ms)

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

    def _handle_dialog(self, dialog: Any) -> None:
        """Registered as page.on('dialog', ...). Consults self._dialog_policy and acts
        accordingly. Records 'dialog_handled' action with type, message, policy, response.
        For 'manual' policy: do nothing (the test/user is expected to handle it).
        accept/dismiss call dialog.accept()/dialog.dismiss(); accept with a prompt needs
        the prompt_text."""
        import asyncio

        async def _act() -> None:
            try:
                if self._dialog_policy == "accept":
                    if dialog.type == "prompt":
                        await dialog.accept(self._dialog_prompt_text or "")
                    else:
                        await dialog.accept()
                elif self._dialog_policy == "dismiss":
                    await dialog.dismiss()
                # manual: do nothing; test-code is expected to handle
                self.recorder.record(
                    "dialog_handled",
                    dtype=dialog.type,
                    message=dialog.message,
                    policy=self._dialog_policy,
                    prompt_text=self._dialog_prompt_text,
                )
            except Exception as e:
                self.recorder.record("dialog_handler_error", error=repr(e))

        task = asyncio.create_task(_act())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    @gated_operation("browser_set_dialog_policy")
    async def set_dialog_policy(self, policy: str, prompt_text: str | None = None) -> dict[str, Any]:
        """Update the session's dialog-handling policy. policy in {accept, dismiss, manual}."""
        if policy not in ("accept", "dismiss", "manual"):
            raise ValueError(f"policy must be accept|dismiss|manual, got {policy!r}")
        self._dialog_policy = policy
        self._dialog_prompt_text = prompt_text
        self.recorder.record("set_dialog_policy", policy=policy, prompt_text=prompt_text)
        return {"ok": True, "policy": policy, "prompt_text": prompt_text}

    # ------------------------------------------------------------------
    # Route mocking
    # ------------------------------------------------------------------

    @gated_operation("browser_mock_route")
    async def mock_route(
        self,
        url_pattern: str,
        *,
        status: int = 200,
        body: str | None = None,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Install a page.route handler that fulfills matching requests with the given
        response. Store the handler in self._active_routes keyed by url_pattern so we can
        remove it later."""

        async def _handler(route: Any) -> None:
            await route.fulfill(
                status=status,
                body=body or "",
                content_type=content_type,
                headers=headers or {},
            )

        if url_pattern in self._active_routes:
            await self.page.unroute(url_pattern, self._active_routes[url_pattern])
        await self.page.route(url_pattern, _handler)
        self._active_routes[url_pattern] = _handler
        self.recorder.record(
            "mock_route",
            pattern=url_pattern,
            status=status,
            content_type=content_type,
            body=body,
            headers=headers or {},
        )
        return {"ok": True, "pattern": url_pattern, "status": status}

    @gated_operation("browser_inject_headers")
    async def inject_headers(self, url_pattern: str, headers: dict[str, str]) -> dict[str, Any]:
        """Add headers to requests matching ``url_pattern``, leaving others alone.

        The per-endpoint layer. Prefer the launch-time option (whole browser)
        or ``set_extra_http_headers`` (whole page) unless the headers genuinely
        have to vary by URL -- this one intercepts, which costs a round trip
        through the handler on every matching request.

        ORDER MATTERS, and silently. Measured on chromium, firefox and webkit:
        page route handlers run LAST-REGISTERED FIRST, and a handler that
        fulfills (``mock_route``) ends the chain -- so a mock installed AFTER
        an injector on an overlapping pattern suppresses it completely and the
        injector never runs. An exact-pattern collision is warned about here;
        an overlapping-glob collision cannot be detected and is documented.
        """
        _reject_redacted_headers(headers)
        validate_extra_http_headers(headers)
        if url_pattern in self._active_routes:
            log.warning(
                "octowright.session.header_injection_shadowed_by_mock",
                instance_id=self.instance_id,
                pattern=url_pattern,
                hint="mock_route fulfills and ends the route chain, so these headers will not be applied",
            )

        async def _handler(route: Any) -> None:
            await route.fallback(headers={**route.request.headers, **headers})

        if url_pattern in self._header_routes:
            await self.page.unroute(url_pattern, self._header_routes[url_pattern])
        await self.page.route(url_pattern, _handler)
        self._header_routes[url_pattern] = _handler
        self.recorder.record(
            "inject_headers",
            pattern=url_pattern,
            headers=redact_header_values(headers, resolve_redaction_mode()),
        )
        return {"ok": True, "pattern": url_pattern, "headers": sorted(headers)}

    @gated_operation("browser_uninject_headers")
    async def uninject_headers(self, url_pattern: str) -> dict[str, Any]:
        """Remove a previously-installed header injection for ``url_pattern``."""
        handler = self._header_routes.pop(url_pattern, None)
        if handler is None:
            raise KeyError(f"no active header injection for pattern {url_pattern!r}")
        await self.page.unroute(url_pattern, handler)
        self.recorder.record("uninject_headers", pattern=url_pattern)
        return {"ok": True, "pattern": url_pattern}

    @gated_operation("browser_set_extra_http_headers")
    async def set_extra_http_headers(self, headers: dict[str, str]) -> dict[str, Any]:
        """Set extra HTTP headers on THIS page, overriding the launch context's.

        The launch-time ``extra_http_headers`` covers the whole browser and
        cannot change; this exists for the header a run only learns partway
        through -- log in, then carry the token. Measured page-over-context
        precedence on chromium, firefox and webkit (Playwright 1.62).

        Scope worth knowing: Playwright's page-level headers are per PAGE, so
        a popup or a new tab opened afterwards does NOT inherit them; use the
        launch-time option when a whole browser needs them.

        The page always receives the real values -- only the JSONL record is
        scrubbed, and by header NAME, so an Authorization is redacted under the
        default policy while an X-Env is left readable.
        """
        _reject_redacted_headers(headers)
        validate_extra_http_headers(headers)
        await self.page.set_extra_http_headers(dict(headers))
        self.recorder.record(
            "set_extra_http_headers",
            headers=redact_header_values(headers, resolve_redaction_mode()),
        )
        return {"ok": True, "headers": sorted(headers)}

    @gated_operation("browser_unmock_route")
    async def unmock_route(self, url_pattern: str) -> dict[str, Any]:
        """Remove a previously-installed mock for url_pattern."""
        handler = self._active_routes.pop(url_pattern, None)
        if handler is None:
            raise KeyError(f"no active mock for pattern {url_pattern!r}")
        await self.page.unroute(url_pattern, handler)
        self.recorder.record("unmock_route", pattern=url_pattern)
        return {"ok": True, "pattern": url_pattern}

    # ------------------------------------------------------------------
    # File-input upload
    # ------------------------------------------------------------------

    @gated_operation("browser_set_input_files")
    async def set_input_files(self, selector: str, paths: list[str]) -> dict[str, Any]:
        """Upload one or more files into an <input type=file> element.

        Each ``paths`` entry is funneled through
        :func:`octowright.session.upload_paths.validate_upload_path` so that
        macro replay (which calls this method directly, bypassing the MCP
        tool wrapper) is held to the same allowlist as live LLM calls.
        """
        from octowright.session.upload_paths import validate_upload_path

        if not isinstance(paths, list):
            raise ValueError("paths must be a list of file paths")
        validated = [str(validate_upload_path(p)) for p in paths]
        await self.page.set_input_files(selector, validated)
        self.recorder.record("set_input_files", selector=selector, paths=validated)
        return {"ok": True, "selector": selector, "paths": validated}
