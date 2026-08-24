# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Credential scrubbing for accessibility-tree snapshots.

Playwright renders a text-ish form control's **value** as its accessible
name, and the accessibility tree has no notion of ``type=password`` -- a
filled password box comes back as ``- textbox: hunter2``, byte-identical in
shape to a username box. Every aria sink therefore emitted cleartext
credentials: ``browser_snapshot``, ``browser_brief`` (in the ``core``
profile), ``capture_create``, ``golden_save`` (which persists them to disk
indefinitely), ``browser_capture_and_close``, the dashboard session detail,
and -- worst -- ``_resolve_semantic_metadata``, whose parsed ``role`` lands
in the JSONL recording on every click.

``OCTOWRIGHT_REDACT_INPUTS`` did not cover any of this: it classifies a
*typed value* at the moment of ``fill``/``type`` by inspecting the target
element, and an aria snapshot is neither. The two controls now share a
policy, so ``passwords`` (the default) means the same thing on both paths.

The scrub is value-based rather than node-based on purpose: the tree is a
rendered string by the time we see it, so the only reliable join back to
"which of these names was a secret" is the value itself, read from the DOM.
Consequences worth knowing:

* Values are collected **before** the snapshot is taken. If collection
  fails we raise and never snapshot -- there is no path that produces an
  unscrubbed tree because the classifier was unavailable.
* Playwright normalizes accessible names (a newline inside a value shows up
  as a space), so each value is scrubbed in both its raw and
  whitespace-collapsed form.
* Replacement is plain substring, longest value first. A short password
  ("ab") will also blank unrelated occurrences of that substring. That is
  the safe direction to be wrong in, and the placeholder makes it obvious.
* Only light-DOM form controls are read; a value inside a closed shadow
  root is not reachable and is not scrubbed.
"""

from __future__ import annotations

import os
from typing import Any

from provide.telemetry import get_logger

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, REDACTED_INPUT_PLACEHOLDER

log = get_logger(__name__)

REDACTION_MODES: frozenset[str] = frozenset({"off", "passwords", "all"})

# Element set worth reading a value from. Kept narrow so blanket ``all`` mode
# doesn't start scrubbing <option>/<progress> values out of the tree.
_VALUE_BEARING_SELECTOR = "input, textarea, [contenteditable], [autocomplete]"

# Collect the values the active policy considers secret, scoped to the
# snapshot root so a frame-scoped snapshot reads that frame's document.
_CREDENTIAL_VALUES_JS = """
(root, mode) => {
  const CRED_AC = new Set(['current-password', 'new-password', 'one-time-code']);
  const SEL = '__SELECTOR__';
  const scope = root || document.documentElement;
  const els = [];
  if (scope.matches && scope.matches(SEL)) els.push(scope);
  if (scope.querySelectorAll) els.push(...scope.querySelectorAll(SEL));
  const out = [];
  for (const el of els) {
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    let v = '';
    if (tag === 'input' || tag === 'textarea') v = typeof el.value === 'string' ? el.value : '';
    else v = el.textContent || '';
    if (!v) continue;
    if (mode === 'all') { out.push(v); continue; }
    const type = el.type ? String(el.type).toLowerCase() : '';
    const acProp = el.autocomplete ? String(el.autocomplete).toLowerCase() : '';
    const acAttr = el.getAttribute && el.getAttribute('autocomplete')
      ? String(el.getAttribute('autocomplete')).toLowerCase() : '';
    if (type === 'password' || CRED_AC.has(acProp) || CRED_AC.has(acAttr)) out.push(v);
  }
  return out;
}
""".replace("__SELECTOR__", _VALUE_BEARING_SELECTOR)


class AriaRedactionError(RuntimeError):
    """Credential classification failed, so no snapshot was taken.

    Raised instead of returning an unscrubbed tree. Session-scoped: it says
    nothing about the daemon or the MCP transport.
    """


def resolve_redaction_mode() -> str:
    """Return the effective input-redaction mode for the current call.

    Reads ``OCTOWRIGHT_REDACT_INPUTS`` at call time so an operator can
    change policy without restarting the daemon. An unknown value falls back
    to ``passwords`` -- a typo must not silently disable redaction.
    """
    raw = os.environ.get("OCTOWRIGHT_REDACT_INPUTS", "passwords").strip().lower() or "passwords"
    if raw not in REDACTION_MODES:
        log.debug(
            "octowright.aria_redaction.unknown_mode",
            value=raw,
            fallback="passwords",
            supported=sorted(REDACTION_MODES),
        )
        return "passwords"
    return raw


def scrub_needles(values: list[str]) -> list[str]:
    """Return the distinct strings to blank, longest first.

    Each value contributes its raw form and its whitespace-collapsed form,
    because Playwright normalizes an accessible name. Longest-first ordering
    keeps a long secret from being partly eaten by a shorter one that is a
    substring of it.
    """
    seen: dict[str, None] = {}
    for value in values:
        for candidate in (value, " ".join(value.split())):
            if candidate:
                seen[candidate] = None
    return sorted(seen, key=len, reverse=True)


def scrub_credentials(aria: str, values: list[str]) -> str:
    """Replace every occurrence of *values* in *aria* with the placeholder."""
    if not aria or not values:
        return aria
    out = aria
    for needle in scrub_needles(values):
        out = out.replace(needle, REDACTED_INPUT_PLACEHOLDER)
    return out


async def collect_credential_values(
    session: Any, locator: Any, mode: str, *, timeout_ms: int | None = None
) -> list[str]:
    """Read the values *mode* considers secret from *locator*'s subtree.

    Failure raises ``AriaRedactionError`` -- the caller must not fall back to
    an unscrubbed snapshot. Re-enters the caller's lease (same task) so the
    page ``evaluate`` serializes with the session's other work.
    """
    async with session.operation("aria_snapshot"):
        # The lease is taken outside the try so a gate error (busy/closing/
        # closed) keeps its own type -- those are session-scoped signals the
        # caller acts on, not a classification failure.
        try:
            raw = await locator.first.evaluate(
                _CREDENTIAL_VALUES_JS, mode, timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS
            )
        except Exception as exc:
            raise AriaRedactionError(
                "could not classify credential fields, so no accessibility snapshot was taken"
            ) from exc
    if not isinstance(raw, list):
        raise AriaRedactionError(f"credential classification returned {type(raw).__name__}, expected list")
    return [v for v in raw if isinstance(v, str) and v]


async def _snapshot(session: Any, locator: Any, timeout_ms: int | None) -> Any:
    """``locator.aria_snapshot()``, bounded only when a caller asked for it.

    Passing ``timeout=None`` through to Playwright means "no timeout", so an
    unset budget has to omit the argument rather than forward it.

    Takes its own lease around the Playwright call, re-entrant for the
    caller's task exactly like ``collect_credential_values`` above -- both of
    ``aria_snapshot``'s call sites already hold this lease, so this nests
    rather than deadlocks. Gating it here, rather than trusting that every
    current and future caller already holds one, is what keeps this call
    site visible to the operation-gate architecture scanner as gated on its
    own terms.
    """
    async with session.operation("aria_snapshot"):
        if timeout_ms is None:
            return await locator.aria_snapshot()
        return await locator.aria_snapshot(timeout=timeout_ms)


async def aria_snapshot(session: Any, locator: Any, *, timeout_ms: int | None = None) -> str:
    """``locator.aria_snapshot()`` with credential values scrubbed.

    Every aria sink in the codebase goes through here so the policy cannot
    drift between them.

    *timeout_ms* bounds BOTH Playwright calls -- the credential scan and the
    snapshot itself. Left as None they use their own defaults, which is right
    for a deliberate snapshot of a page that is known to be there. Callers
    annotating another action should pass that action's timeout: on a selector
    that never resolves, an unbounded read here costs the full default before
    the action it describes has even started (measured: a click carrying
    ``timeout_ms=4000`` took 19.1s, 15 of them spent in the credential scan).

    Takes *session*'s operation lease around both Playwright calls. The
    credential scan is a real ``evaluate`` against the live page, so it has
    to serialize with the session's other work rather than race it. Every
    call site already holds a lease; the gate is re-entrant for the owning
    task, so this nests rather than deadlocks.
    """
    mode = resolve_redaction_mode()
    async with session.operation("aria_snapshot"):
        if mode == "off":
            return str(await _snapshot(session, locator, timeout_ms))
        values = await collect_credential_values(session, locator, mode, timeout_ms=timeout_ms)
        return scrub_credentials(str(await _snapshot(session, locator, timeout_ms)), values)
