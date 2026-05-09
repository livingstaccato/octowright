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

from octowright.session._protocols import SessionLike

if TYPE_CHECKING:
    from octowright.session.core import BrowserSession


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

    def set_dialog_policy(self, policy: str, prompt_text: str | None = None) -> dict[str, Any]:
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
        self.recorder.record("mock_route", pattern=url_pattern, status=status, content_type=content_type)
        return {"ok": True, "pattern": url_pattern, "status": status}

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

    async def set_input_files(self, selector: str, paths: list[str]) -> dict[str, Any]:
        """Upload one or more files into an <input type=file> element."""
        await self.page.set_input_files(selector, paths)
        self.recorder.record("set_input_files", selector=selector, paths=paths)
        return {"ok": True, "selector": selector, "paths": paths}
