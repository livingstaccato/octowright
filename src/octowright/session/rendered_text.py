# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The text a page draws, collected for ``expect_no_text`` on every engine.

``innerText`` alone misses most of what a reader can see: open shadow roots,
drawn form values, placeholders and CSS generated content. And ``body`` holds
octowright's own overlays (the corner badge shows the session label), which are
not the page. The script below walks every element that matches the selector,
descends into open shadow roots, and returns the drawn text as pieces; the
comparison happens in Python with ``macros.redaction_text.normalize``, so it is
the same comparison the screenshot scanner makes.

Not reachable from script: a closed shadow root. Chromium's DOM snapshot reaches
it, and ``expect_no_text`` adds that scan there; other engines cannot.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Every octowright overlay element's id starts with this (badge, macro pill,
#: viewport pill and their modals).
OWN_OVERLAY_ID_PREFIX = "__octowright_"

#: Elements inspected per frame for form values and generated content.
_ELEMENT_LIMIT = 20000

COLLECT_RENDERED_TEXT_JS = """({ selector, ownPrefix, limit }) => {
  const own = (el) => el.id && el.id.startsWith(ownPrefix);
  const shown = (el) => el.checkVisibility
    ? el.checkVisibility({ checkVisibilityCSS: true })
    : Boolean(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const pieces = [];
  const overlay = [];
  const text = (el) => {
    if (!el.querySelector('[id^="' + ownPrefix + '"]')) return el.innerText || "";
    const parts = [];
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) parts.push(child.textContent);
      // innerText of an element that is not rendered (display:none, <script>,
      // <style>) is its raw source, so an unrendered child contributes nothing.
      else if (child.nodeType === Node.ELEMENT_NODE && !own(child) && shown(child)) parts.push(text(child));
    }
    return parts.join("\\n");
  };
  const generated = (el, which) => {
    const content = getComputedStyle(el, which).content;
    if (!content || content === "none" || content === "normal") return;
    const quoted = content.match(/"((?:[^"\\\\]|\\\\.)*)"/g);
    if (quoted) pieces.push(quoted.map((q) => q.slice(1, -1)).join(""));
  };
  let seen = 0;
  const inspect = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (++seen > limit) return;
      if (own(el) || el.closest('[id^="' + ownPrefix + '"]')) continue;
      if (el.shadowRoot) {
        for (const child of el.shadowRoot.children) pieces.push(text(child));
        inspect(el.shadowRoot);
      }
      if (!shown(el)) continue;
      const tag = el.tagName;
      if (tag === "TEXTAREA" || (tag === "INPUT" && !["password", "hidden"].includes(el.type))) {
        if (el.value) pieces.push(el.value);
        else if (el.placeholder) pieces.push(el.placeholder);
      }
      generated(el, "::before");
      generated(el, "::after");
    }
  };
  const matches = selector === "body"
    ? [document.body].filter(Boolean)
    : Array.from(document.querySelectorAll(selector));
  for (const el of matches) {
    pieces.push(text(el));
    if (el.shadowRoot) for (const child of el.shadowRoot.children) pieces.push(text(child));
    generated(el, "::before");
    generated(el, "::after");
    inspect(el);
  }
  for (const el of document.querySelectorAll('[id^="' + ownPrefix + '"]')) overlay.push(el.innerText || "");
  return { pieces, overlay: overlay.join("\\n"), matched: matches.length };
}"""


def collect_args(selector: str) -> dict[str, object]:
    return {"selector": selector, "ownPrefix": OWN_OVERLAY_ID_PREFIX, "limit": _ELEMENT_LIMIT}


def contains(pieces: Iterable[object], text: str) -> bool:
    """Whether any drawn piece holds *text*, compared as the screenshot scanner compares."""
    # Local import: the macros package imports the session stack.
    from octowright.macros.redaction_text import normalize

    needle = normalize(text)
    return bool(needle) and any(isinstance(piece, str) and needle in normalize(piece) for piece in pieces)
