# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""TerminalEngine: build + drive one uterm SessionConnector in-process.

A WebSocket-free re-implementation of HostedSessionRuntime._bridge_session: a
background poll loop pumps connector.poll_messages(), translates each message to
a Recorder action, and backs off 0.05s when idle (the same anti-hot-spin sleep
the uterm runtime uses for pty/shell connectors).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any, cast

from provide.telemetry import get_logger
from provide.uterm.server.connectors import (
    build_connector,
    register_connector,
    registered_types,
)

from octowright._tracing import counter, record_exception, span
from octowright.recorder import Recorder
from octowright.terminal import redact
from octowright.terminal.errors import TerminalDisconnectedError
from octowright.terminal.supervision import poll_done_reason
from octowright.terminal.translate import MessageTranslator

log = get_logger("octowright.terminal")

# Matches HostedSessionRuntime's backoff for connectors with no internal wait.
_POLL_IDLE_SLEEP_S = 0.05
_WAIT_POLL_S = 0.05

# OTel counters (noop unless metrics are enabled). Labelled by connector_type
# (pty/ssh) — an intrinsically bounded label set.
_TERMINAL_LAUNCHED = counter(
    "octowright_terminal_launched_total",
    description="Terminal sessions launched",
)
_TERMINAL_CLOSED = counter(
    "octowright_terminal_closed_total",
    description="Terminal sessions closed",
)


def ensure_connector_registered(connector_type: str) -> None:
    """Idempotently register the uterm connector for *connector_type*.

    Connectors self-register on import, but only if their module has been
    imported. We import + register explicitly so build_connector() resolves
    regardless of import order. (Snippet established by the Task 2 spike.)
    """
    if connector_type in registered_types():
        return
    if connector_type == "pty":
        from provide.uterm.pty.connector import PTYConnector

        # PTYConnector is structurally a connector but not a nominal SessionConnector
        # subclass, so cast past register_connector's factory-type check (mypy + ty);
        # uterm's own _register() registers it the same way.
        register_connector("pty", cast(Any, PTYConnector))
    elif connector_type == "ssh":  # wired in Phase 2; harmless to register early
        from provide.uterm.server.connectors.ssh import SshSessionConnector

        register_connector("ssh", SshSessionConnector)
    elif connector_type == "telnet":
        from provide.uterm.server.connectors.telnet import TelnetSessionConnector

        register_connector("telnet", TelnetSessionConnector)


class TerminalEngine:
    def __init__(
        self,
        instance_id: str,
        label: str | None,
        connector_type: str,
        connector_config: dict[str, Any],
        recorder: Recorder,
    ) -> None:
        ensure_connector_registered(connector_type)
        cfg = dict(connector_config)
        self._instance_id = instance_id
        self._connector_type = connector_type
        self._cols = int(cfg.get("cols", 80))
        self._rows = int(cfg.get("rows", 24))
        self._connector = build_connector(instance_id, label or instance_id, connector_type, cfg)
        self._recorder = recorder
        self._translator = MessageTranslator()
        self._latest_screen = ""
        self._at_password_prompt = False
        self._poll_task: asyncio.Task[None] | None = None
        self._stop_evt = asyncio.Event()
        self._stop_recorded = False
        self._poll_error: BaseException | None = None

    async def start(self) -> None:
        with span(
            "octowright.terminal.launch",
            connector_type=self._connector_type,
            instance_id=self._instance_id,
        ) as sp:
            try:
                await self._connector.start()
            except BaseException as exc:
                record_exception(sp, exc)
                raise
            self._recorder.record(
                "terminal_start", connector_type=self._connector_type, cols=self._cols, rows=self._rows
            )
            self._poll_task = asyncio.create_task(self._poll_loop())
            # Supervise the poll task: without a done-callback a poll/recorder
            # exception would kill it silently — no stop record, no metric, the
            # session looking alive while nothing pumps it. Surface an unexpected
            # death as an 'error' stop.
            self._poll_task.add_done_callback(self._on_poll_done)
        # Count only successful launches (a raised start() skips this).
        _TERMINAL_LAUNCHED.add(1, attributes={"connector_type": self._connector_type})

    async def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            msgs = await self._connector.poll_messages()
            if msgs:
                for msg in msgs:
                    self._ingest(msg)
                continue
            if not self._connector.is_connected():
                self._record_stop("eof")
                return
            await asyncio.sleep(_POLL_IDLE_SLEEP_S)

    def _on_poll_done(self, task: asyncio.Task[None]) -> None:
        """Done-callback for the poll task. A clean return or a stop()-driven
        cancellation needs nothing; an unexpected exception is recorded as an
        'error' stop (once, via ``_record_stop``) so it does not vanish."""
        if task.cancelled():
            return
        error = task.exception()
        reason = poll_done_reason(error)
        if reason is None:
            return
        self._poll_error = error
        log.warning("terminal.poll_loop.died", instance_id=self._instance_id, error=repr(error))
        self._record_stop(reason)

    def _ingest(self, msg: dict[str, Any]) -> None:
        if msg.get("type") == "snapshot":
            self._latest_screen = str(msg.get("screen", ""))
            self._at_password_prompt = redact.is_password_prompt(self._latest_screen)
        for action, fields in self._translator.feed(msg):
            self._recorder.record(action, **fields)

    async def send_input(self, text: str, *, password: bool = False) -> None:
        with span(
            "octowright.terminal.send_input",
            connector_type=self._connector_type,
            instance_id=self._instance_id,
        ):
            if not self._connector.is_connected():
                # User-action path: the connector would drop the bytes quietly.
                # Raise so the caller learns the input was NOT delivered instead
                # of the tool falsely reporting {"ok": true}.
                log.warning("terminal.send_input.disconnected", instance_id=self._instance_id)
                raise TerminalDisconnectedError(f"terminal {self._instance_id} is disconnected; input not delivered")
            masked = redact.should_mask(at_password_prompt=self._at_password_prompt, password_source=password)
            self._recorder.record("terminal_input", **redact.input_fields(text, masked=masked))
            for msg in await self._connector.handle_input(text):
                self._ingest(msg)

    async def snapshot(self) -> dict[str, Any]:
        msg = await self._connector.get_snapshot()
        self._latest_screen = str(msg.get("screen", ""))
        # Keep the password-prompt flag fresh so a send_input right after an
        # on-demand snapshot masks correctly (defense-in-depth; the poll loop
        # otherwise refreshes it within one _POLL_IDLE_SLEEP_S tick).
        self._at_password_prompt = redact.is_password_prompt(self._latest_screen)
        return {
            "screen": self._latest_screen,
            "cursor": msg.get("cursor"),
            "cols": msg.get("cols"),
            "rows": msg.get("rows"),
        }

    async def wait_for(self, *, prompt: str | None = None, text: str | None = None, timeout: float = 10.0) -> bool:
        if prompt is None and text is None:
            raise ValueError("wait_for requires either prompt= or text=")
        pattern = re.compile(prompt) if prompt is not None else None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            screen = self._latest_screen
            if pattern is not None and pattern.search(screen):
                return True
            if text is not None and text in screen:
                return True
            await asyncio.sleep(_WAIT_POLL_S)
        return False

    def _record_stop(self, reason: str) -> None:
        if not self._stop_recorded:
            self._stop_recorded = True
            try:
                self._recorder.record("terminal_stop", reason=reason)
            except Exception as exc:
                # This method runs from an asyncio done-callback as well as
                # explicit teardown. A recorder failure must not escape the
                # callback and obscure the causal connector/poll exception.
                log.warning(
                    "terminal.stop_record.failed",
                    instance_id=self._instance_id,
                    reason=reason,
                    error=repr(exc),
                )
            # Count the terminal-ended event once, whichever path got here first
            # (explicit stop() or the poll loop's EOF detection).
            _TERMINAL_CLOSED.add(1, attributes={"connector_type": self._connector_type})

    async def stop(self) -> None:
        with span(
            "octowright.terminal.close",
            connector_type=self._connector_type,
            instance_id=self._instance_id,
        ):
            self._stop_evt.set()
            if self._poll_task is not None:
                self._poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._poll_task
                self._poll_task = None
            with contextlib.suppress(Exception):
                await self._connector.stop()
            self._record_stop("closed")
