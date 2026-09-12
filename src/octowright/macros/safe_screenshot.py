# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Redacted screenshots for macro runs that hold classified values.

A screenshot of a page a credential or identity was typed into is a durable copy
of it that no text scrub can reach, so a classified run refuses screenshots by
default. This module is the safe path: it removes every rendered spelling of the
run's classified values from the live DOM, proves none remain in the serialized
markup, takes the screenshot, and restores the page exactly.

Opt in per session with :func:`enable_redacted_screenshots`, or for every session
with ``OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS=redact``. A caller-installed handler
still wins and may wrap :func:`redacted_screenshot` with its own checks.

What redaction covers: text nodes, attribute values and form-control values in the
document and every open shadow root. What it cannot read: pixels drawn on a canvas,
media, embeds and cross-origin frames, which are hidden for the duration of the
screenshot instead, and closed shadow roots.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from octowright import defaults
from octowright._paths import reject_unsafe_path
from octowright.macros.privacy import sensitive_value_variants
from octowright.session.timeouts import bounded

POLICY_ENV = "OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS"
AUTHORITY_ATTR = "_octowright_sensitive_screenshot_authority"
HANDLER_ATTR = "_octowright_sensitive_screenshot_handler"
BUILT_IN_ATTR = "_octowright_redacted_screenshot_built_in"

ClassifiedScreenshotPolicy = Literal["refuse", "redact"]
ScreenshotHandler = Callable[..., Awaitable[tuple[int, int] | None]]

REDACT_RENDERED_JS = r"""(values) => {
  const stateKey = '__octowrightRedactedScreenshotState';
  if (globalThis[stateKey]) throw new Error('a redacted screenshot is already in progress');
  const secrets = values.filter((value) => typeof value === 'string' && value.length > 0);
  const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const redact = (value) => {
    let result = String(value ?? '');
    for (const secret of secrets) {
      result = secret.includes('%')
        ? result.replace(new RegExp(escapeRegex(secret), 'gi'), '<redacted>')
        : result.split(secret).join('<redacted>');
    }
    return result;
  };
  const contains = (value, secret) => secret.includes('%')
    ? new RegExp(escapeRegex(secret), 'i').test(value)
    : value.includes(secret);
  const state = [];
  globalThis[stateKey] = state;
  const restore = () => {
    for (let index = state.length - 1; index >= 0; index -= 1) {
      const change = state[index];
      if (change[0] === 'text') change[1].nodeValue = change[2];
      else if (change[0] === 'attribute') change[1].setAttribute(change[2], change[3]);
      else if (change[0] === 'value') change[1].value = change[2];
      else if (change[0] === 'style') {
        if (change[2]) change[1].setAttribute('style', change[3]);
        else change[1].removeAttribute('style');
      }
    }
    delete globalThis[stateKey];
  };
  try {
    const roots = [document];
    for (let index = 0; index < roots.length; index += 1) {
      const root = roots[index];
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const safe = redact(node.nodeValue);
        if (safe !== node.nodeValue) {
          state.push(['text', node, node.nodeValue]);
          node.nodeValue = safe;
        }
      }
      for (const element of root.querySelectorAll('*')) {
        const opaque = ['CANVAS', 'EMBED', 'IFRAME', 'OBJECT', 'VIDEO'].includes(element.tagName)
          || (element.tagName.includes('-') && !element.shadowRoot);
        if (opaque) {
          state.push(['style', element, element.hasAttribute('style'), element.getAttribute('style')]);
          element.style.setProperty('visibility', 'hidden', 'important');
        }
        for (const attribute of Array.from(element.attributes || [])) {
          const safe = redact(attribute.value);
          if (safe !== attribute.value) {
            state.push(['attribute', element, attribute.name, attribute.value]);
            element.setAttribute(attribute.name, safe);
          }
        }
        if ('value' in element && typeof element.value === 'string') {
          const safe = redact(element.value);
          if (safe !== element.value) {
            state.push(['value', element, element.value]);
            element.value = safe;
          }
        }
        if (element.shadowRoot) roots.push(element.shadowRoot);
      }
    }
    let remaining = 0;
    for (const root of roots) {
      const markup = root instanceof Document ? root.documentElement.outerHTML : root.innerHTML;
      for (const secret of secrets) if (contains(markup, secret)) remaining += 1;
    }
    return remaining;
  } catch (error) {
    restore();
    throw error;
  }
}"""

RESTORE_RENDERED_JS = r"""() => {
  const stateKey = '__octowrightRedactedScreenshotState';
  const state = globalThis[stateKey] || [];
  for (let index = state.length - 1; index >= 0; index -= 1) {
    const change = state[index];
    if (change[0] === 'text') change[1].nodeValue = change[2];
    else if (change[0] === 'attribute') change[1].setAttribute(change[2], change[3]);
    else if (change[0] === 'value') change[1].value = change[2];
    else if (change[0] === 'style') {
      if (change[2]) change[1].setAttribute('style', change[3]);
      else change[1].removeAttribute('style');
    }
  }
  delete globalThis[stateKey];
}"""


def classified_screenshot_policy() -> ClassifiedScreenshotPolicy:
    """What a classified run does with a screenshot when no handler is installed.

    ``refuse`` (the default) or ``redact``. Any other value raises, because a typo
    here would otherwise silently decide whether credentials reach a PNG.
    """
    raw = os.environ.get(POLICY_ENV, "refuse").strip().lower()
    if raw == "refuse":
        return "refuse"
    if raw == "redact":
        return "redact"
    raise ValueError(f"{POLICY_ENV} must be 'refuse' or 'redact'")


def enable_redacted_screenshots(session: Any, *, handler: ScreenshotHandler | None = None) -> None:
    """Authorize screenshots on a classified run for this session.

    Without ``handler`` the session uses :func:`redacted_screenshot`. A caller that
    needs its own checks passes a handler, which may call :func:`redacted_screenshot`
    itself; octowright then never takes a screenshot on that session by any other
    route, including the automatic artifact screenshots.
    """
    if handler is None:

        async def built_in(*, action: dict[str, Any], sensitive_values: tuple[str, ...]) -> tuple[int, int]:
            return await redacted_screenshot(session, action, sensitive_values)

        setattr(session, HANDLER_ATTR, built_in)
        setattr(session, BUILT_IN_ATTR, True)
    else:
        setattr(session, HANDLER_ATTR, handler)
        setattr(session, BUILT_IN_ATTR, False)
    setattr(session, AUTHORITY_ATTR, True)


def installed_handler(session: Any) -> ScreenshotHandler | None:
    """The explicitly authorized handler, or None when the session has none."""
    handler = getattr(session, HANDLER_ATTR, None)
    if getattr(session, AUTHORITY_ATTR, None) is True and callable(handler):
        return handler
    return None


def built_in_redaction_applies(session: Any) -> bool:
    """Whether octowright itself may take a redacted screenshot on this session.

    False whenever a caller installed its own handler: an automatic screenshot must
    never bypass that handler's checks.
    """
    if installed_handler(session) is not None:
        return getattr(session, BUILT_IN_ATTR, None) is True
    return classified_screenshot_policy() == "redact"


async def redacted_screenshot(
    session: Any,
    action: dict[str, Any],
    sensitive_values: tuple[str, ...],
    *,
    root: Path | None = None,
) -> tuple[int, int]:
    """Redact, prove clean, screenshot, restore. Returns ``(executed, skipped)``.

    No bytes are written unless redaction left no classified value in the markup.
    The page is restored whatever happens after redaction starts; if the restore
    itself fails, the screenshot is deleted, because a page that could not be
    restored is not evidence of the page the run saw.
    """
    path_value = action.get("path")
    if not path_value:
        return 0, 1
    target = reject_unsafe_path(
        Path(str(path_value)),
        root if root is not None else defaults.RECORDINGS_DIR,
        label="screenshot path",
    )
    values = list(sensitive_value_variants(sensitive_values))
    # Re-enters the caller's lease (the macro run, or an artifact run) in the same
    # task, so the redact, screenshot and restore never interleave with another
    # operation on this page.
    async with session.operation("macro_run"):
        page = session.page
        remaining = await bounded(page.evaluate(REDACT_RENDERED_JS, values), operation="macro_redacted_screenshot")
        try:
            if remaining != 0:
                raise RuntimeError("classified values are still rendered after redaction; screenshot refused")
            await session.screenshot(target)
        except BaseException:
            target.unlink(missing_ok=True)
            try:
                await bounded(page.evaluate(RESTORE_RENDERED_JS), operation="macro_redacted_screenshot")
            except Exception:
                pass
            raise
        try:
            await bounded(page.evaluate(RESTORE_RENDERED_JS), operation="macro_redacted_screenshot")
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return 1, 0
