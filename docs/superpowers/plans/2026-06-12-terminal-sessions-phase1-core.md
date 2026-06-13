# Terminal Sessions — Phase 1: Core In-Process Primitive — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python-drivable, JSONL-recording terminal session primitive for Octowright that hosts a `provide-uterm` `SessionConnector` (PTY local shell; SSH-capable) in-process, with no TermHub/WebSocket/FastAPI.

**Architecture:** A new `octowright/terminal/` package quarantines all uterm usage behind one seam. `TerminalEngine` builds a connector via `build_connector(...)` and runs its own poll loop (a WebSocket-free re-implementation of uterm's `HostedSessionRuntime._bridge_session`), translating connector worker-protocol messages into Octowright `Recorder` actions (`{ts, action, …}`). `TerminalSession`/`TerminalPool` mirror `BrowserSession`/`BrowserPool`'s minimal shape so later phases (MCP tools, scenarios, dashboard) treat terminals uniformly.

**Tech Stack:** Python 3.11+, asyncio, `provide-uterm-server` (connectors) + `provide-uterm-platform` (PTY), Octowright's existing `Recorder`. Tests use pytest with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed — `async def test_*` is collected directly).

**Scope:** This is Phase 1 of 4 (see the roadmap at the end). It delivers the primitive as an importable library with unit + real-PTY integration tests. It does **not** add MCP tools, scenario participants, or the dashboard view — those are Phases 2–4, authored after this lands.

**Design reference:** `docs/superpowers/specs/2026-06-12-terminal-sessions-design.md` (§3, §4, and the §1.1 mechanism correction).

**Conventions for every new file (verified against the repo's hooks during Task 1):**
- Start with the canonical Octowright SPDX header — **note the REQUIRED blank line after the `#` block** (the `spdx-headers` pre-commit hook fails without it; `uv run --active python scripts/normalize_spdx_headers.py` auto-fixes):
  ```python
  # SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
  # SPDX-License-Identifier: Apache-2.0
  # SPDX-Comment: Part of octowright.
  #

  ```
  Then `from __future__ import annotations` (or the module docstring, then `from __future__`).
- **Always invoke via `uv run --active`** (Makefile convention; also preserves the editable uterm install — a plain `uv sync` would prune it).
- **Per-task pytest runs: add `--no-cov`** (e.g. `uv run --active pytest tests/terminal/test_X.py --no-cov -v`). A single-file run otherwise trips the repo's `--cov-fail-under=83` gate and prints a scary "coverage not reached" line even though tests pass. There is **no pytest pre-commit hook**, so commits don't run the suite; full-suite coverage is enforced by `make test`.
- **Do NOT modify `pyproject.toml` in Phase 1.** Declaring the unpublished `provide-uterm-*` packages anywhere in pyproject (extra, dep, or dev-group) makes `uv` try to resolve them from PyPI → 404 → every hook and `uv run` fails. The editable install (header) satisfies imports; the `[terminal]` extra is declared only once uterm is published (a publish-time follow-up).
- **Commit messages:** conventional-commit format (a `commitlint` hook enforces it), e.g. `feat(terminal): …`. Never mention Claude/AI/co-authorship.
- Run `uv run --active ruff format <paths> && uv run --active ruff check <paths>` before each commit; the pre-commit hooks run the full gate automatically on `git commit`.

**Environment setup (already done at execution start — do NOT repeat unless a `uv sync` pruned it):**
The uterm packages are **not on PyPI**; they're editable-installed from the local sibling monorepo:
```bash
uv pip install -e ../provide-uterm/packages/provide-uterm \
               -e ../provide-uterm/packages/provide-uterm-server \
               -e ../provide-uterm/packages/provide-uterm-platform
```
If `make install` / `uv sync` is ever run, it will remove these — re-run the command above to restore them. `pyproject.toml` itself only **declares** the `[terminal]` extra (referencing the future-published versions); it does **not** path-source them (per the maintainer's instruction to keep pyproject clean).

**Pre-merge gate (tracked, not blocking implementation):** the GPLv3 §7 linking exception must be present in `provide-uterm` before this branch merges. The maintainer is handling it.

---

## Task 1: Dependencies, licensing gate, and package skeleton

**Files:**
- Modify: `pyproject.toml` (dependencies list, ~lines 7-20)
- Create: `src/octowright/terminal/__init__.py`
- Test: `tests/terminal/test_terminal_imports.py`

> **LICENSING GATE (blocking):** `provide-uterm` is AGPL-3.0-or-later; Octowright is Apache-2.0. Before this dependency is added, confirm the GPLv3 §7 linking exception is present in `provide-uterm`'s `LICENSES/` (spec §11). Do not merge this task until that exception exists.

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_terminal_imports.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
from __future__ import annotations


def test_uterm_connector_factory_importable() -> None:
    # The only uterm entry point Phase 1 needs: the connector factory + registry.
    from provide.uterm.server.connectors import (
        build_connector,
        register_connector,
        registered_types,
    )

    assert callable(build_connector)
    assert callable(register_connector)
    assert callable(registered_types)


def test_terminal_package_importable() -> None:
    import octowright.terminal  # noqa: F401


def test_terminal_reports_available_when_extra_installed() -> None:
    import octowright.terminal as terminal

    # The dev/test env installs the [terminal] extra, so this is True here.
    # On a core install (extra absent) it returns False — see is_available().
    assert terminal.is_available() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/terminal/test_terminal_imports.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'provide.uterm'` (dependency not yet added) and/or `octowright.terminal`.

- [ ] **Step 3: Do NOT modify pyproject in Phase 1 (extra deferred to publish time)**

Declaring the unpublished `provide-uterm-*` packages in pyproject — as an extra, dep, `[tool.uv.sources]` path, or dev-group — makes `uv` resolve them from PyPI → 404 → every pre-commit hook and `uv run` fails (confirmed during execution). So Phase 1 makes **no pyproject change**: the editable install (header) satisfies imports, and `is_available()` is the runtime detector. The user-facing `[terminal]` optional extra (`provide-uterm-server` + `provide-uterm-platform`, plus `asyncssh` for Phase 2 SSH) is added **later, once uterm is published to PyPI** — a publish-time follow-up, not a Phase 1 step.

Verify the dev env imports the connector entrypoint (already editable-installed):

Run: `uv run --active python -c "import provide.uterm.server.connectors; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Create the package init**

```python
# src/octowright/terminal/__init__.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
"""In-process terminal sessions (PTY / SSH) for Octowright.

All `provide-uterm` usage is quarantined inside this package: the rest of
Octowright sees only Octowright's generic session shape and `{ts, action, …}`
JSONL recordings. See docs/superpowers/specs/2026-06-12-terminal-sessions-design.md.
"""

from __future__ import annotations


def is_available() -> bool:
    """Return True iff the optional ``octowright[terminal]`` extra is installed.

    Import-light on purpose: imports only the uterm connector entry module —
    never ``octowright.terminal.engine``/``pool`` (which import uterm at module
    top) — so it is safe to call on a core install where the extra is absent.
    """
    try:
        import provide.uterm.server.connectors  # noqa: F401
    except ImportError:
        return False
    return True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/terminal/test_terminal_imports.py -v`
Expected: PASS (all three tests, including `is_available()` → True in the dev env).

- [ ] **Step 6: Commit**

```bash
git add src/octowright/terminal/__init__.py tests/terminal/test_terminal_imports.py
git commit -m "feat(terminal): declare optional terminal package skeleton + is_available"
```
(No `pyproject.toml` / `uv.lock` changes in Phase 1 — see Step 3.)

---

## Task 2: Characterization spike — pin the PTY connector message shape

This is a real, kept test that documents and locks the uterm contract Phase 1 depends on: the PTY connector emits **only `{"type": "snapshot", "screen": <cumulative buffer>, …}`** messages (no per-chunk raw frame), and `screen` is the cumulative decoded output (capped 32 KB). It also establishes the canonical "ensure pty registered" snippet reused by the engine.

**Files:**
- Test: `tests/terminal/test_pty_connector_contract.py`

- [ ] **Step 1: Write the test**

```python
# tests/terminal/test_pty_connector_contract.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
from __future__ import annotations

import asyncio
import sys

import pytest

from provide.uterm.server.connectors import (
    build_connector,
    register_connector,
    registered_types,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="PTY connector is POSIX-only"
)


def _ensure_pty_registered() -> None:
    """Canonical registration snippet — reused verbatim by TerminalEngine.

    The PTY connector lives in provide-uterm-platform and registers under the
    type name "pty". Importing the module + registering is idempotent-guarded
    so calling this repeatedly is safe.
    """
    if "pty" not in registered_types():
        from provide.uterm.pty.connector import PTYConnector

        register_connector("pty", PTYConnector)


async def test_pty_connector_emits_cumulative_snapshot() -> None:
    _ensure_pty_registered()
    assert "pty" in registered_types()

    conn = build_connector(
        "char-1", "char", "pty", {"command": "/bin/echo", "args": ["hello-uterm"]}
    )
    await conn.start()
    try:
        screen = ""
        for _ in range(60):
            for msg in await conn.poll_messages():
                # Contract: PTY emits ONLY snapshot frames, screen is cumulative text.
                assert msg["type"] == "snapshot"
                assert isinstance(msg["screen"], str)
                assert "cursor" in msg and "cols" in msg and "rows" in msg
                screen = msg["screen"]
            if "hello-uterm" in screen:
                break
            await asyncio.sleep(0.05)
        assert "hello-uterm" in screen
    finally:
        await conn.stop()


async def test_pty_handle_input_returns_snapshot() -> None:
    _ensure_pty_registered()
    conn = build_connector("char-2", "char", "pty", {"command": "/bin/cat"})
    await conn.start()
    try:
        msgs = await conn.handle_input("ping\n")
        assert msgs and msgs[0]["type"] == "snapshot"
        # /bin/cat echoes input back into the cumulative buffer.
        screen = ""
        for _ in range(60):
            for msg in await conn.poll_messages():
                screen = msg["screen"]
            if "ping" in screen:
                break
            await asyncio.sleep(0.05)
        assert "ping" in screen
    finally:
        await conn.stop()
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/terminal/test_pty_connector_contract.py -v`
Expected: PASS. If `build_connector("pty", …)` raises "unknown connector type", the `_ensure_pty_registered()` guard is the fix (already included). If either assertion about message shape fails, STOP — the uterm version's contract differs from the spec; reconcile `translate.py` (Task 3) to the observed shape before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/terminal/test_pty_connector_contract.py
git commit -m "test(terminal): characterize PTY connector message contract"
```

---

## Task 3: `translate.py` — message → Recorder action mapping

A stateful translator turns each connector worker-protocol message into zero-or-more `(action, fields)` pairs. It owns the **cumulative-screen delta** so only new output is recorded (and later fed to xterm.js). Identical consecutive screens produce an empty delta → no record (this also dedups the snapshot that `handle_input` returns versus the one the poll loop reads).

**Files:**
- Create: `src/octowright/terminal/translate.py`
- Test: `tests/terminal/test_translate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_translate.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
from __future__ import annotations

from octowright.terminal.translate import MessageTranslator


def _snap(screen: str) -> dict:
    return {"type": "snapshot", "screen": screen, "cursor": {"row": 0, "col": 0}, "screen_hash": "h"}


def test_first_snapshot_emits_full_screen_as_output() -> None:
    t = MessageTranslator()
    out = t.feed(_snap("hello"))
    assert out == [("terminal_output", {"data": "hello", "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})]


def test_clean_prefix_extension_emits_only_delta() -> None:
    t = MessageTranslator()
    t.feed(_snap("hello"))
    out = t.feed(_snap("hello world"))
    assert out == [("terminal_output", {"data": " world", "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})]


def test_identical_screen_emits_nothing() -> None:
    t = MessageTranslator()
    t.feed(_snap("hello"))
    assert t.feed(_snap("hello")) == []


def test_buffer_rotation_uses_suffix_overlap() -> None:
    # When the 32KB cap slides, the new buffer is not a prefix-extension; the
    # delta is the part of the new buffer past its overlap with the old one.
    t = MessageTranslator()
    t.feed(_snap("ABCDE"))
    out = t.feed(_snap("CDEFG"))  # overlap "CDE", new tail "FG"
    assert out == [("terminal_output", {"data": "FG", "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})]


def test_error_message_maps_to_terminal_error() -> None:
    t = MessageTranslator()
    assert t.feed({"type": "error", "message": "boom"}) == [("terminal_error", {"message": "boom"})]


def test_unknown_type_passes_through_as_terminal_event() -> None:
    t = MessageTranslator()
    out = t.feed({"type": "worker_hello", "input_mode": "open"})
    assert out == [("terminal_event", {"uterm_type": "worker_hello", "input_mode": "open"})]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/terminal/test_translate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.terminal.translate'`.

- [ ] **Step 3: Write the implementation**

```python
# src/octowright/terminal/translate.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
"""Translate uterm connector worker-protocol messages into Octowright Recorder
actions (`{ts, action, ...fields}`).

The PTY/SSH connectors emit `snapshot` messages whose `screen` is the *cumulative*
decoded output buffer (capped ~32KB), not per-chunk bytes. `MessageTranslator`
holds the previous screen and emits only the delta as `terminal_output.data`, so
the recording is a clean append-only stream that reconstructs the full screen by
concatenation (and feeds xterm.js incrementally in a later phase).
"""

from __future__ import annotations

from typing import Any


class MessageTranslator:
    """Stateful per-session translator. Not thread-safe; drive from one loop."""

    def __init__(self) -> None:
        self._last_screen = ""

    def feed(self, msg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        mtype = msg.get("type")
        if mtype == "snapshot":
            screen = str(msg.get("screen", ""))
            delta = self._delta(self._last_screen, screen)
            self._last_screen = screen
            if not delta:
                return []
            fields: dict[str, Any] = {"data": delta}
            if msg.get("cursor") is not None:
                fields["cursor"] = msg["cursor"]
            if msg.get("screen_hash") is not None:
                fields["screen_hash"] = msg["screen_hash"]
            return [("terminal_output", fields)]
        if mtype == "error":
            return [("terminal_error", {"message": str(msg.get("message", ""))})]
        # worker_hello / hello / any unmapped type: pass through, never drop.
        payload = {k: v for k, v in msg.items() if k != "type"}
        return [("terminal_event", {"uterm_type": mtype, **payload})]

    @staticmethod
    def _delta(prev: str, cur: str) -> str:
        if not prev:
            return cur
        if cur.startswith(prev):
            return cur[len(prev):]
        # Cap slid (output exceeded ~32KB) or buffer was cleared: the delta is
        # the new buffer past the longest suffix-of-prev that prefixes cur.
        max_overlap = min(len(prev), len(cur))
        for k in range(max_overlap, 0, -1):
            if prev[-k:] == cur[:k]:
                return cur[k:]
        return cur
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/terminal/test_translate.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/octowright/terminal/translate.py tests/terminal/test_translate.py
git commit -m "feat(terminal): worker-protocol message -> Recorder action translator"
```

---

## Task 4: `redact.py` — input redaction policy

Octowright owns send-redaction (we don't use uterm's `SessionLogger`). This re-implements the password-prompt masking from `HostedSessionRuntime._log_snapshot`/`_log_send` and reuses the existing `defaults.INPUT_REDACTION_MODE` constant (don't re-parse the env).

**Files:**
- Create: `src/octowright/terminal/redact.py`
- Test: `tests/terminal/test_redact.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_redact.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
from __future__ import annotations

import pytest

from octowright import defaults
from octowright.terminal import redact


def test_is_password_prompt_detects_trailing_prompt() -> None:
    assert redact.is_password_prompt("user@host's password: ")
    assert redact.is_password_prompt("Enter passphrase for key:")
    assert not redact.is_password_prompt("$ ls -la")


def test_should_mask_off_mode_never_masks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "INPUT_REDACTION_MODE", "off")
    assert not redact.should_mask(at_password_prompt=True, password_source=True)


def test_should_mask_all_mode_always_masks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "INPUT_REDACTION_MODE", "all")
    assert redact.should_mask(at_password_prompt=False, password_source=False)


def test_should_mask_passwords_mode_masks_credentials_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "INPUT_REDACTION_MODE", "passwords")
    assert redact.should_mask(at_password_prompt=True, password_source=False)
    assert redact.should_mask(at_password_prompt=False, password_source=True)
    assert not redact.should_mask(at_password_prompt=False, password_source=False)


def test_input_fields_masked_hides_value_keeps_byte_count() -> None:
    assert redact.input_fields("hunter2", masked=True) == {"keys": "***", "byte_count": 7}


def test_input_fields_unmasked_keeps_literal() -> None:
    assert redact.input_fields("ls\n", masked=False) == {"keys": "ls\n"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/terminal/test_redact.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.terminal.redact'`.

- [ ] **Step 3: Write the implementation**

```python
# src/octowright/terminal/redact.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
"""Record-time input redaction for terminal sessions.

Mirrors HostedSessionRuntime's password masking and honours the existing
OCTOWRIGHT_REDACT_INPUTS modes via defaults.INPUT_REDACTION_MODE:
  off       -> record literal sends
  passwords -> mask at detected password prompts + password-sourced sends (default)
  all       -> mask every send
The connector always receives the real bytes; only the recording is masked.
"""

from __future__ import annotations

import re
from typing import Any

from octowright import defaults

# Copied verbatim from provide.uterm.server.runtime (HostedSessionRuntime._log_snapshot).
_PASSWORD_PROMPT_RE = re.compile(r"(?i)(?:password|passphrase)[^\n]*:\s*$")


def is_password_prompt(screen: str) -> bool:
    return bool(_PASSWORD_PROMPT_RE.search(screen.rstrip()))


def should_mask(*, at_password_prompt: bool, password_source: bool) -> bool:
    mode = defaults.INPUT_REDACTION_MODE
    if mode == "off":
        return False
    if mode == "all":
        return True
    # "passwords" (default) and any unrecognised value: fail safe to masking creds.
    return at_password_prompt or password_source


def input_fields(text: str, *, masked: bool) -> dict[str, Any]:
    if masked:
        return {"keys": "***", "byte_count": len(text.encode("utf-8"))}
    return {"keys": text}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/terminal/test_redact.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/octowright/terminal/redact.py tests/terminal/test_redact.py
git commit -m "feat(terminal): input redaction policy (password-prompt masking)"
```

---

## Task 5: `errors.py` — protected-close error

Mirrors `browser_pool/errors.py:ProtectedBrowserCloseError` so terminal-close refusal is a typed, catchable error.

**Files:**
- Create: `src/octowright/terminal/errors.py`
- Test: `tests/terminal/test_terminal_errors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_terminal_errors.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
from __future__ import annotations

from octowright.terminal.errors import ProtectedTerminalCloseError


def test_protected_terminal_close_error_is_value_error() -> None:
    err = ProtectedTerminalCloseError("nope")
    assert isinstance(err, ValueError)
    assert "nope" in str(err)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/terminal/test_terminal_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.terminal.errors'`.

- [ ] **Step 3: Write the implementation**

```python
# src/octowright/terminal/errors.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
"""Terminal-pool-specific exception types."""

from __future__ import annotations


class ProtectedTerminalCloseError(ValueError):
    """Raised when closing a protected terminal session requires force=True."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/terminal/test_terminal_errors.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/terminal/errors.py tests/terminal/test_terminal_errors.py
git commit -m "feat(terminal): ProtectedTerminalCloseError"
```

---

## Task 6: `engine.py` — `TerminalEngine`

Builds + drives one connector. Owns the poll loop (WebSocket-free re-implementation of `HostedSessionRuntime._bridge_session`), records via the translator, applies redaction on input, caches the latest screen for `snapshot()`/`wait_for()`, and self-ends on connector EOF.

**Files:**
- Create: `src/octowright/terminal/engine.py`
- Test: `tests/terminal/test_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_engine.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from octowright.recorder import Recorder
from octowright.terminal.engine import TerminalEngine

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


def _read_actions(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


async def test_engine_records_start_output_and_eof_stop(tmp_path: Path) -> None:
    log_path = tmp_path / "t.jsonl"
    recorder = Recorder(log_path)
    engine = TerminalEngine(
        "eng-1", "echo", "pty", {"command": "/bin/echo", "args": ["hello-engine"]}, recorder
    )
    await engine.start()
    # /bin/echo writes then exits → poll loop sees EOF and records terminal_stop.
    for _ in range(60):
        actions = _read_actions(log_path)
        if any(a["action"] == "terminal_stop" for a in actions):
            break
        await asyncio.sleep(0.05)
    await engine.stop()
    recorder.close()

    actions = _read_actions(log_path)
    names = [a["action"] for a in actions]
    assert names[0] == "terminal_start"
    assert "terminal_output" in names
    assert "terminal_stop" in names
    output = "".join(a.get("data", "") for a in actions if a["action"] == "terminal_output")
    assert "hello-engine" in output
    # terminal_stop is recorded exactly once even though stop() ran after EOF.
    assert names.count("terminal_stop") == 1
    assert next(a for a in actions if a["action"] == "terminal_stop")["reason"] == "eof"


async def test_engine_send_input_and_snapshot(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path / "t.jsonl")
    engine = TerminalEngine("eng-2", "cat", "pty", {"command": "/bin/cat"}, recorder)
    await engine.start()
    try:
        await engine.send_input("marco\n")
        matched = await engine.wait_for(text="marco", timeout=5.0)
        assert matched
        snap = await engine.snapshot()
        assert "marco" in snap["screen"]
    finally:
        await engine.stop()
        recorder.close()

    actions = _read_actions(tmp_path / "t.jsonl")
    inputs = [a for a in actions if a["action"] == "terminal_input"]
    assert inputs and inputs[0]["keys"] == "marco\n"


async def test_engine_masks_password_source_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults

    monkeypatch.setattr(defaults, "INPUT_REDACTION_MODE", "passwords")
    recorder = Recorder(tmp_path / "t.jsonl")
    engine = TerminalEngine("eng-3", "cat", "pty", {"command": "/bin/cat"}, recorder)
    await engine.start()
    try:
        await engine.send_input("s3cret\n", password=True)
    finally:
        await engine.stop()
        recorder.close()

    actions = _read_actions(tmp_path / "t.jsonl")
    masked = next(a for a in actions if a["action"] == "terminal_input")
    assert masked["keys"] == "***"
    assert masked["byte_count"] == len("s3cret\n".encode())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/terminal/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.terminal.engine'`.

- [ ] **Step 3: Write the implementation**

```python
# src/octowright/terminal/engine.py
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
from typing import Any

from provide.uterm.server.connectors import (
    build_connector,
    register_connector,
    registered_types,
)

from octowright.recorder import Recorder
from octowright.terminal import redact
from octowright.terminal.translate import MessageTranslator

# Matches HostedSessionRuntime's backoff for connectors with no internal wait.
_POLL_IDLE_SLEEP_S = 0.05
_WAIT_POLL_S = 0.05


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

        register_connector("pty", PTYConnector)
    elif connector_type == "ssh":  # wired in Phase 2; harmless to register early
        from provide.uterm.server.connectors.ssh import SshSessionConnector

        register_connector("ssh", SshSessionConnector)


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

    async def start(self) -> None:
        await self._connector.start()
        self._recorder.record(
            "terminal_start", connector_type=self._connector_type, cols=self._cols, rows=self._rows
        )
        self._poll_task = asyncio.create_task(self._poll_loop())

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

    def _ingest(self, msg: dict[str, Any]) -> None:
        if msg.get("type") == "snapshot":
            self._latest_screen = str(msg.get("screen", ""))
            self._at_password_prompt = redact.is_password_prompt(self._latest_screen)
        for action, fields in self._translator.feed(msg):
            self._recorder.record(action, **fields)

    async def send_input(self, text: str, *, password: bool = False) -> None:
        masked = redact.should_mask(
            at_password_prompt=self._at_password_prompt, password_source=password
        )
        self._recorder.record("terminal_input", **redact.input_fields(text, masked=masked))
        for msg in await self._connector.handle_input(text):
            self._ingest(msg)

    async def snapshot(self) -> dict[str, Any]:
        msg = await self._connector.get_snapshot()
        self._latest_screen = str(msg.get("screen", ""))
        return {
            "screen": self._latest_screen,
            "cursor": msg.get("cursor"),
            "cols": msg.get("cols"),
            "rows": msg.get("rows"),
        }

    async def wait_for(
        self, *, prompt: str | None = None, text: str | None = None, timeout: float = 10.0
    ) -> bool:
        if prompt is None and text is None:
            raise ValueError("wait_for requires either prompt= or text=")
        pattern = re.compile(prompt) if prompt is not None else None
        loop = asyncio.get_event_loop()
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
            self._recorder.record("terminal_stop", reason=reason)

    async def stop(self) -> None:
        self._stop_evt.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        with contextlib.suppress(Exception):
            await self._connector.stop()
        self._record_stop("closed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/terminal/test_engine.py -v`
Expected: PASS (3 tests). The first proves start/output/eof-stop with single `terminal_stop`; the others prove send/wait/snapshot and password masking.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/terminal/engine.py tests/terminal/test_engine.py
git commit -m "feat(terminal): TerminalEngine drives a uterm connector + records"
```

---

## Task 7: `session.py` — `TerminalSession`

A parallel dataclass to `BrowserSession` carrying only the minimal shape the rest of Octowright depends on.

**Files:**
- Create: `src/octowright/terminal/session.py`
- Test: `tests/terminal/test_session.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_session.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from octowright.recorder import Recorder
from octowright.terminal.engine import TerminalEngine
from octowright.terminal.session import TerminalSession

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


async def test_session_close_stops_engine_and_recorder(tmp_path: Path) -> None:
    log_path = tmp_path / "t.jsonl"
    recorder = Recorder(log_path)
    engine = TerminalEngine("s-1", "cat", "pty", {"command": "/bin/cat"}, recorder)
    session = TerminalSession(
        instance_id="s-1",
        kind="terminal",
        connector_type="pty",
        label="cat",
        profile=None,
        recorder=recorder,
        log_path=log_path,
        engine=engine,
    )
    await engine.start()
    assert session.kind == "terminal"
    assert session.url is None
    await session.close()
    # recorder closed → file handle closed.
    assert recorder._fh.closed  # noqa: SLF001 — asserting teardown
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/terminal/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.terminal.session'`.

- [ ] **Step 3: Write the implementation**

```python
# src/octowright/terminal/session.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
"""TerminalSession: the per-terminal record the pool, tools, and dashboard see.

Deliberately a parallel dataclass to BrowserSession (not a subclass) — it carries
only the minimal shape the rest of Octowright depends on: instance_id, kind,
label, profile, url(None), recorder, log_path, protected, plus the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from octowright.recorder import Recorder
from octowright.terminal.engine import TerminalEngine


@dataclass
class TerminalSession:
    instance_id: str
    kind: str  # always "terminal" (browser sessions use the engine name)
    connector_type: str  # "pty" | "ssh"
    label: str | None
    profile: str | None
    recorder: Recorder
    log_path: Path
    engine: TerminalEngine
    protected: bool = False
    url: str | None = None  # always None; present so dashboard summaries are uniform

    async def close(self, *, force: bool = False) -> None:
        # Protected refusal is enforced by TerminalPool.close (which has the
        # force gate); session.close performs the actual teardown.
        await self.engine.stop()
        self.recorder.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/terminal/test_session.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/terminal/session.py tests/terminal/test_session.py
git commit -m "feat(terminal): TerminalSession dataclass"
```

---

## Task 8: `pool.py` — `TerminalPool`

Mirrors `BrowserPool`'s registry surface so the dashboard and scenario code (later phases) treat it uniformly. `list_sessions()` returns the same keys `BrowserPool.list_sessions()` returns.

**Files:**
- Create: `src/octowright/terminal/pool.py`
- Test: `tests/terminal/test_pool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_pool.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
from __future__ import annotations

import sys

import pytest

from octowright.terminal.errors import ProtectedTerminalCloseError
from octowright.terminal.pool import TerminalPool

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


async def test_launch_registers_and_lists_session() -> None:
    pool = TerminalPool()
    try:
        result = await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="cat")
        iid = result["instance_id"]
        assert result["kind"] == "terminal"
        assert result["log_path"].endswith(".jsonl")

        summaries = pool.list_sessions()
        assert len(summaries) == 1
        s = summaries[0]
        # Same keys BrowserPool.list_sessions() returns, so /api/sessions is uniform.
        assert set(s) >= {"instance_id", "kind", "label", "profile", "url", "log_path", "har_path", "protected"}
        assert s["instance_id"] == iid
        assert s["kind"] == "terminal"
        assert s["url"] is None
        assert s["har_path"] is None
    finally:
        await pool.close_all(force=True)


async def test_get_and_maybe_get() -> None:
    pool = TerminalPool()
    try:
        iid = (await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}))["instance_id"]
        assert pool.get(iid).instance_id == iid
        assert pool.maybe_get(iid) is not None
        assert pool.maybe_get("nope") is None
        with pytest.raises(KeyError):
            pool.get("nope")
    finally:
        await pool.close_all(force=True)


async def test_close_refuses_protected_without_force() -> None:
    pool = TerminalPool()
    iid = (
        await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, protected=True)
    )["instance_id"]
    try:
        with pytest.raises(ProtectedTerminalCloseError):
            await pool.close(iid)
        # still present after refused close
        assert pool.maybe_get(iid) is not None
    finally:
        await pool.close(iid, force=True)
    assert pool.maybe_get(iid) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/terminal/test_pool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.terminal.pool'`.

- [ ] **Step 3: Write the implementation**

```python
# src/octowright/terminal/pool.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
"""TerminalPool: lifecycle + registry for terminal sessions.

Mirrors BrowserPool's surface (launch/get/maybe_get/iter_sessions/list_sessions/
close/close_all) so the dashboard and scenario layers treat terminal and browser
sessions uniformly.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from octowright import defaults
from octowright.recorder import Recorder, new_log_path
from octowright.terminal.engine import TerminalEngine
from octowright.terminal.errors import ProtectedTerminalCloseError
from octowright.terminal.session import TerminalSession


class TerminalPool:
    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()

    async def launch(
        self,
        *,
        kind: str = "pty",
        connector_config: dict[str, Any],
        label: str | None = None,
        profile: str | None = None,
        protected: bool = False,
    ) -> dict[str, Any]:
        instance_id = uuid4().hex[:12]
        # kind in the FILENAME is always "terminal" so closed-session discovery
        # (which keys on the kind segment) groups terminals together.
        log_path = new_log_path(defaults.RECORDINGS_DIR, instance_id, label, "terminal")
        recorder = Recorder(log_path)
        engine = TerminalEngine(instance_id, label, kind, connector_config, recorder)
        session = TerminalSession(
            instance_id=instance_id,
            kind="terminal",
            connector_type=kind,
            label=label,
            profile=profile,
            recorder=recorder,
            log_path=log_path,
            engine=engine,
            protected=protected,
        )
        await engine.start()
        async with self._lock:
            self._sessions[instance_id] = session
        return {
            "instance_id": instance_id,
            "kind": "terminal",
            "connector_type": kind,
            "label": label,
            "profile": profile,
            "log_path": str(log_path),
        }

    def get(self, instance_id: str) -> TerminalSession:
        if instance_id not in self._sessions:
            raise KeyError(f"no terminal session {instance_id!r}")
        return self._sessions[instance_id]

    def maybe_get(self, instance_id: str) -> TerminalSession | None:
        return self._sessions.get(instance_id)

    def iter_sessions(self) -> tuple[TerminalSession, ...]:
        return tuple(self._sessions.values())

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "instance_id": s.instance_id,
                "kind": s.kind,
                "connector_type": s.connector_type,
                "label": s.label,
                "profile": s.profile,
                "url": s.url,
                "log_path": str(s.log_path),
                "har_path": None,
                "protected": s.protected,
            }
            for s in tuple(self._sessions.values())
        ]

    async def close(self, instance_id: str, *, force: bool = False) -> None:
        session = self.maybe_get(instance_id)
        if session is None:
            raise KeyError(f"no terminal session {instance_id!r}")
        if session.protected and not force:
            raise ProtectedTerminalCloseError(
                f"terminal {instance_id!r} is protected; pass force=True to close it"
            )
        await session.close(force=force)
        async with self._lock:
            self._sessions.pop(instance_id, None)

    async def close_all(self, *, force: bool = False) -> None:
        for instance_id in list(self._sessions):
            session = self._sessions.get(instance_id)
            if session is None:
                continue
            if session.protected and not force:
                continue
            await session.close(force=force)
            async with self._lock:
                self._sessions.pop(instance_id, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/terminal/test_pool.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/octowright/terminal/pool.py tests/terminal/test_pool.py
git commit -m "feat(terminal): TerminalPool lifecycle + registry"
```

---

## Task 9: Wire `terminal_pool` into shared server state

Adds the singleton next to `pool`/`scenario_pool` so Phases 2–4 (tools, scenarios, dashboard) import it the same way browser code imports `pool`.

**Files:**
- Modify: `src/octowright/server/_state.py:30-31` (after `pool` / `scenario_pool`)
- Test: `tests/terminal/test_state_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_state_wiring.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
from __future__ import annotations


def test_state_terminal_pool_matches_availability() -> None:
    from octowright.server import _state
    from octowright.terminal import is_available

    if is_available():
        from octowright.terminal.pool import TerminalPool

        assert isinstance(_state.terminal_pool, TerminalPool)
    else:
        assert _state.terminal_pool is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/terminal/test_state_wiring.py -v`
Expected: FAIL — `AttributeError: module 'octowright.server._state' has no attribute 'terminal_pool'`.

- [ ] **Step 3: Write the implementation (availability-guarded — do NOT import the pool at module top)**

In `src/octowright/server/_state.py`, add the **import-light** availability module near the other imports (line ~20-21). This does NOT import uterm or the pool:

```python
from octowright import terminal as _terminal
```

Add the heavy pool type to the existing `if TYPE_CHECKING:` block (line ~25-26) so the annotation resolves without a runtime import:

```python
if TYPE_CHECKING:
    from mcp.types import Icon, ToolAnnotations
    from octowright.terminal.pool import TerminalPool
```

Then, right after the existing `scenario_pool` line (line 31), create the singleton only when the extra is installed:

```python
pool = BrowserPool()
scenario_pool = _scenario_pool_mod.ScenarioPool()

# Terminal sessions are an optional feature (the `octowright[terminal]` extra —
# spec §3.2). `terminal_pool` is None on a core install; it is instantiated only
# when the uterm-backed extra is importable. Phase 2's terminal MCP tools register
# only when this is non-None, so on a core install they simply don't appear.
terminal_pool: TerminalPool | None = None
if _terminal.is_available():
    from octowright.terminal.pool import TerminalPool as _TerminalPool

    terminal_pool = _TerminalPool()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/terminal/test_state_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + quality gate**

Run: `uv run pytest tests/terminal/ -v && make lint`
Expected: all terminal tests PASS; lint clean (SPDX headers present, mypy/ty/ruff/vulture happy).

- [ ] **Step 6: Commit**

```bash
git add src/octowright/server/_state.py tests/terminal/test_state_wiring.py
git commit -m "feat(terminal): expose terminal_pool singleton in server state"
```

---

## Phase 1 complete — what now exists

An importable, tested terminal-session primitive: `TerminalPool.launch(kind="pty", connector_config={"command": "/bin/bash"})` starts a recording local shell session, `engine.send_input()/snapshot()/wait_for()` drive it, recordings land in `RECORDINGS_DIR` as `*-terminal-*.jsonl` in Octowright's `{ts, action, …}` format, and `terminal_pool` is wired into server state. No agent-facing surface yet.

## Roadmap — Phases 2–4 (separate plans, authored after Phase 1 lands)

**Phase 2 — Agent surface (MCP tools + SSH).** Define the terminal tools (`terminal_launch` / `terminal_send_input` / `terminal_snapshot` / `terminal_read` / `terminal_wait_for` / `terminal_close` / `terminal_list`) inside a **`register_terminal_tools(mcp)` hook** in `server/terminal/lifecycle.py` (pattern from `server/browser/lifecycle.py`, resolving `terminal_pool` from `_state`). The server calls this hook **only when `_terminal.is_available()` and `_state.terminal_pool is not None`** — so on a core install the tools never register (like a profile-filtered tool). Keeping registration in an explicit hook (not import-time `@mcp.tool` side effects) is what lets a future out-of-tree **entry-point plugin** (`octowright-terminal` distribution) call the identical hook — the "entry-point later" half of decision #6. Add the `terminals` capability profile + scenarios-profile entries in `server/profiles.py`. Extend the `[terminal]` extra with `asyncssh` and add the SSH path: `terminal_launch(kind="ssh", host=…, port=…, user=…, key_path=…, known_hosts=…, insecure_no_host_check=…)`, surfacing the connector's host-key `ValueError` as a clean tool error. Merge `terminal_pool.iter_sessions()` into `/api/sessions` (`http/routes/sessions.py:41`, guarded on `terminal_pool is not None`) so terminals appear in the session list. Telemetry spans/metrics (`octowright.terminal.*`).

**Phase 3 — Scenario participant.** Add optional terminal fields to `Participant` (`scenarios.py:36`), extend `SUPPORTED_KINDS` with `"terminal"` (`defaults.py:214`), partition the launch fan-out in `ScenarioPool.start()` (`scenarios_pool.py:178`) into browser vs terminal rosters, and resolve participant→session from either pool. Persona `ssh` defaults block + explicit-args-win resolution.

**Phase 4 — Dashboard xterm.js view.** Add `@xterm/xterm` to `packages/octowright-frontend`; render a terminal view in the session debugger for `kind === "terminal"` — live (subscribe `/tail`, write `terminal_output.data` deltas into an xterm instance) and replay (feed recorded deltas in order). Decide the PTY-`ECHO`-off input-echo behavior (spec §10 item 3). Docs: CLAUDE.md "Five Concepts" → add terminal session; env-var + profile tables.

---

## Self-review (completed)

- **Spec coverage (Phase 1 portion):** §3 package layout → Tasks 3–9; §4.3 engine → Task 6; §4.4 translate → Task 3; §4.5 redact → Task 4; §9 containment (`new_log_path` under `RECORDINGS_DIR`, EOF self-stop) → Tasks 6, 8; protected-close → Tasks 5, 8. Tools/scenarios/dashboard/telemetry are explicitly deferred to Phases 2–4 (roadmap above), not dropped.
- **Placeholder scan:** none — every code step contains full file content; every command has an expected result.
- **Type/name consistency:** `MessageTranslator.feed` returns `list[tuple[str, dict]]` consumed identically in `engine._ingest`; `ensure_connector_registered` defined in Task 6 matches the spike snippet in Task 2; `TerminalSession` fields produced by `TerminalPool.launch` match the dataclass in Task 7; `ProtectedTerminalCloseError` (Task 5) raised in Task 8; `redact.should_mask`/`input_fields`/`is_password_prompt` (Task 4) called with matching signatures in Task 6.
