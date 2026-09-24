# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The text a page draws, collected for ``expect_no_text`` on every engine.

Drawn means *text a reader can see*, and it is one definition on every engine,
in the Chromium DOM snapshot and in the exported CLI. ``innerText`` alone misses
most of it: open shadow roots, drawn form values, placeholders, CSS generated
content, the alt text of a broken image and a select's option labels (Firefox's
``innerText`` leaves options out; Chromium's and WebKit's put them in, so the
labels are added explicitly). And ``body`` holds octowright's own overlays (the
corner badge shows the session label), which are not the page. The script below
walks every element that matches the selector, descends into open shadow roots,
and returns the drawn text as pieces; the comparison happens in Python with
``macros.redaction_text.normalize``, so case, whitespace and invisible
characters never matter.

Not drawn, anywhere: anything inside an element that is not rendered (computed
``display: none`` on it or an ancestor, across shadow boundaries, which also
covers ``<script>``, ``<style>`` and ``<template>`` source, and a hidden
iframe's document); ``visibility: hidden`` text; attribute text other than a
shown placeholder and a broken image's alt (a resource address, a ``title``, a
``data-`` attribute); and password values, which draw only mask characters.

Not reachable from script: a closed shadow root. Chromium's DOM snapshot reaches
it, and ``expect_no_text`` adds that scan there (:func:`snapshot_drawn_text`);
other engines cannot.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Every octowright overlay element's id starts with this (badge, macro pill,
#: viewport pill and their modals).
OWN_OVERLAY_ID_PREFIX = "__octowright_"

#: Elements inspected per frame for form values, shadow roots and generated
#: content. A scan that reaches it reports ``truncated``, and a check that found
#: nothing is then refused rather than passed on a page it only partly read.
ELEMENT_LIMIT = 20000


def resolve_element_limit(explicit: object, environ: Mapping[str, str]) -> int:
    """The element limit for one check: the step's ``element_limit``, else the daemon's, else 20000.

    ``OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT`` sets the daemon default. A value that
    is not a positive integer there keeps ``ELEMENT_LIMIT`` rather than
    removing the limit or setting one no page can pass; on a step it is refused,
    since the author asked for something specific. Self-contained (builtins and
    the module constant only): the exported CLI renders this source verbatim.
    """
    if explicit is not None:
        if isinstance(explicit, bool) or not isinstance(explicit, int) or explicit < 1:
            raise ValueError(f"expect_no_text: element_limit must be a positive integer, got {explicit!r}")
        return explicit
    raw = environ.get("OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else ELEMENT_LIMIT


#: What Playwright says when a frame detaches or navigates while it is evaluated.
#: A child frame failing this way is skipped; the main frame failing still fails.
FRAME_GONE = re.compile(r"detached|context was destroyed|navigat", re.IGNORECASE)

COLLECT_RENDERED_TEXT_JS = """({ selector, ownPrefix, limit }) => {
  const own = (el) => el.id && el.id.startsWith(ownPrefix);
  const shown = (el) => el.checkVisibility
    ? el.checkVisibility({ checkVisibilityCSS: true })
    : Boolean(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  // Not rendered: display:none on the element or any ancestor, across shadow
  // boundaries. innerText of such an element is its raw textContent, source of
  // <script>/<style> included, so it must never be read.
  const rendered = (el) => {
    for (let node = el; node; ) {
      // The element's own window's getComputedStyle: a frame asking about its
      // frameElement gets nothing useful from its own (measured on Firefox).
      const view = node.ownerDocument && node.ownerDocument.defaultView;
      if (node.nodeType === Node.ELEMENT_NODE && view && view.getComputedStyle(node).display === "none") return false;
      node = node.parentElement || (node.getRootNode && node.getRootNode().host) || null;
    }
    return true;
  };
  // A frame inside a display:none iframe is not drawn either. A cross-origin
  // parent hides frameElement, and the frame is then scanned.
  for (let w = window; w.frameElement; w = w.parent) {
    if (!rendered(w.frameElement)) return { pieces: [], matched: 0, truncated: false };
  }
  const pieces = [];
  const text = (el) => {
    if (!el.querySelector('[id^="' + ownPrefix + '"]')) return el.innerText || "";
    const parts = [];
    const visible = getComputedStyle(el).visibility === "visible";
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        if (visible) parts.push(child.textContent);
      // Skip only overlays and children that are not rendered. checkVisibility
      // is false for a display:contents wrapper, whose children are drawn, and
      // innerText already drops visibility:hidden text by itself.
      } else if (child.nodeType === Node.ELEMENT_NODE && !own(child)
          && getComputedStyle(child).display !== "none") {
        parts.push(text(child));
      }
    }
    return parts.join("\\n");
  };
  const generated = (el, which) => {
    const style = getComputedStyle(el, which);
    const content = style.content;
    if (!content || content === "none" || content === "normal") return;
    if (style.visibility !== "visible" || !rendered(el)) return;
    const quoted = content.match(/"((?:[^"\\\\]|\\\\.)*)"/g);
    if (quoted) pieces.push(quoted.map((q) => q.slice(1, -1)).join(""));
  };
  const shadow = (el) => {
    if (!el.shadowRoot || !rendered(el)) return;
    for (const child of el.shadowRoot.children) if (rendered(child)) pieces.push(text(child));
  };
  const drawn = (el) => {
    const tag = el.tagName;
    if (tag === "TEXTAREA" || (tag === "INPUT" && !["password", "hidden"].includes(el.type))) {
      if (el.value) pieces.push(el.value);
      else if (el.placeholder) pieces.push(el.placeholder);
    } else if (tag === "IMG" && el.alt && el.complete && !el.naturalWidth) {
      pieces.push(el.alt);
    } else if (tag === "SELECT") {
      for (const option of el.options) pieces.push(option.label);
    }
  };
  let seen = 0;
  let truncated = false;
  const inspect = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (++seen > limit) { truncated = true; return; }
      if (own(el) || el.closest('[id^="' + ownPrefix + '"]')) continue;
      shadow(el);
      if (el.shadowRoot) inspect(el.shadowRoot);
      if (truncated) return;
      generated(el, "::before");
      generated(el, "::after");
      if (shown(el)) drawn(el);
    }
  };
  const matches = selector === "body"
    ? [document.body].filter(Boolean)
    : Array.from(document.querySelectorAll(selector));
  for (const el of matches) {
    if (!rendered(el)) continue;
    pieces.push(text(el));
    shadow(el);
    generated(el, "::before");
    generated(el, "::after");
    if (shown(el)) drawn(el);
    inspect(el);
  }
  return { pieces, matched: matches.length, truncated };
}"""


def collect_args(selector: str, limit: int = ELEMENT_LIMIT) -> dict[str, object]:
    return {"selector": selector, "ownPrefix": OWN_OVERLAY_ID_PREFIX, "limit": limit}


def contains(pieces: Iterable[object], text: str) -> bool:
    """Whether any drawn piece holds *text*, after ``macros.redaction_text.normalize``.

    Only that plain comparison. The screenshot scanner also matches a number by
    its digits and a value spelled across unrelated boxes, erring toward refusal;
    here that would fail a page on text it does not draw (``555-013-7788`` for a
    forbidden ``5550137788``), and only on the engines that ran that scan.
    """
    # Local import: the macros package imports the session stack.
    from octowright.macros.redaction_text import normalize

    needle = normalize(text)
    return bool(needle) and any(isinstance(piece, str) and needle in normalize(piece) for piece in pieces)


def snapshot_drawn_text(snapshot: Mapping[str, Any]) -> list[str]:
    """The drawn text of each shown document in a Chromium ``DOMSnapshot.captureSnapshot``.

    Layout text only, joined in document order as ``innerText`` joins a page's
    text, with ``visibility: hidden`` text and octowright's overlays left out.
    That is the collector's definition of drawn text applied to what the
    snapshot adds: closed shadow roots. The screenshot scanner's other findings
    (resource addresses, style images, attribute text) are not text a reader
    sees, and form values, alt text and option labels are the collector's to
    judge, since the snapshot lays none of them out as text.
    """
    # Local import: the macros package imports the session stack.
    from octowright.macros.rendered_surface import _hidden_documents

    strings: Sequence[str] = snapshot.get("strings", [])

    def string(index: int) -> str:
        return strings[index] if 0 <= index < len(strings) else ""

    documents: Sequence[Mapping[str, Any]] = snapshot.get("documents", [])
    hidden = _hidden_documents(documents, strings)
    drawn: list[str] = []
    for position, document in enumerate(documents):
        if position in hidden:
            continue
        overlay = _overlay_nodes(document.get("nodes", {}), string)
        layout = document.get("layout", {})
        styles = layout.get("styles", [])
        parts = []
        for row, (node, text) in enumerate(zip(layout.get("nodeIndex", []), layout.get("text", []), strict=False)):
            style = styles[row] if row < len(styles) else []
            visibility = string(style[0]) if style else ""
            if text >= 0 and node not in overlay and visibility not in ("hidden", "collapse"):
                parts.append(string(text))
        if parts:
            drawn.append("".join(parts))
    return drawn


def _overlay_nodes(nodes: Mapping[str, Any], string: Any) -> set[int]:
    """Nodes inside an octowright overlay: an ancestor-or-self's id starts with the prefix."""
    parents: Sequence[int] = nodes.get("parentIndex", [])
    attributes: Sequence[Sequence[int]] = nodes.get("attributes", [])
    roots = {
        node
        for node, pairs in enumerate(attributes)
        for key, value in zip(pairs[::2], pairs[1::2], strict=False)
        if string(key) == "id" and string(value).startswith(OWN_OVERLAY_ID_PREFIX)
    }
    known: dict[int, bool] = dict.fromkeys(roots, True)
    for node in range(len(parents)):
        _within(node, parents, known)
    return {node for node, inside in known.items() if inside}


def _within(start: int, parents: Sequence[int], known: dict[int, bool]) -> bool:
    """Whether *start* has a known-overlay ancestor-or-self, memoising the chain walked."""
    chain: list[int] = []
    node = start
    # Walk up to a settled node or the root; a shadow root's parent is its host.
    while 0 <= node < len(parents) and node not in known and node not in chain:
        chain.append(node)
        node = parents[node]
    inside = known.get(node, False)
    known.update(dict.fromkeys(chain, inside))
    return inside
