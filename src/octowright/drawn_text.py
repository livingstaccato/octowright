# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The portable half of ``expect_no_text``: what it collects, how it compares, what it says.

Replay (``session.core_expect_mixin``) and the exported CLI both run these
functions, and the exporter renders this module's source verbatim into every
generated script (:func:`octowright.artifacts.script_export.render_macro_cli`).
So it imports only the standard library, every name it defines is fair game in
the script's namespace, and a rule or a message changes in one place for both.
Two hand copies (the script's own frame loop, ``normalize`` and messages) had
already drifted apart before this module existed. It lives at the package root
because ``session/``, ``macros/`` and ``artifacts/`` all need it, and the macros
package imports the session stack.

Drawn means *text a reader can see*, and it is one definition on every engine,
in the Chromium DOM snapshot and in the exported CLI. ``innerText`` alone misses
most of it: open shadow roots, drawn form values, placeholders, CSS generated
content, the alt text of a broken image and a select's option labels (Firefox's
``innerText`` leaves options out; Chromium's and WebKit's put them in, so the
labels are added explicitly). And ``body`` holds octowright's own overlays (the
corner badge shows the session label), which are not the page. The collector
below walks every element that matches the selector, descends into open shadow
roots, and returns the drawn text as pieces; the comparison happens in Python
with :func:`normalize`, so case, whitespace and invisible characters never
matter.

Not drawn, anywhere: anything inside an element that is not rendered (computed
``display: none`` on it or an ancestor, across shadow boundaries, which also
covers ``<script>``, ``<style>`` and ``<template>`` source, and a hidden
iframe's document); ``visibility: hidden`` text; attribute text other than a
shown placeholder and a broken image's alt (a resource address, a ``title``, a
``data-`` attribute); and password values, which draw only mask characters.

Not reachable from script: a closed shadow root. Chromium's DOM snapshot reaches
it, and replay adds that scan there (``session.rendered_text``); other engines,
and the exported CLI, cannot.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

#: What a recorded expect_no_text holds in place of its text; replay refuses it.
REDACTED_ASSERTION_TEXT = "<redacted:forbidden-text>"

#: Characters that render as nothing: C0 and C1 controls, and Unicode
#: ``Default_Ignorable_Code_Point`` ranges from ``DerivedCoreProperties.txt``.
IGNORABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x0000, 0x001F),
    (0x007F, 0x009F),
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)

IGNORABLE_CLASS = "".join(f"\\U{low:08X}-\\U{high:08X}" for low, high in IGNORABLE_RANGES)
_INVISIBLE = re.compile("[\\s" + IGNORABLE_CLASS + "]+")


def normalize(text: str) -> str:
    """The comparable form of ``text``: NFKC, no whitespace or ignorable characters, casefolded."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text)).casefold()


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
    since the author asked for something specific.
    """
    if explicit is not None:
        if isinstance(explicit, bool) or not isinstance(explicit, int) or explicit < 1:
            raise ValueError(f"expect_no_text: element_limit must be a positive integer, got {explicit!r}")
        return explicit
    raw = environ.get("OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else ELEMENT_LIMIT


#: What every engine says when a frame navigates while it is evaluated
#: (measured on Chromium, Firefox and WebKit). A frame that detaches is asked
#: directly (``frame.is_detached()``) rather than recognised by its message.
NAVIGATED_AWAY = re.compile(r"execution context was destroyed", re.IGNORECASE)

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
    """Whether any drawn piece holds *text*, after :func:`normalize`.

    Only that plain comparison. The screenshot scanner also matches a number by
    its digits and a value spelled across unrelated boxes, erring toward refusal;
    here that would fail a page on text it does not draw (``555-013-7788`` for a
    forbidden ``5550137788``), and only on the engines that ran that scan.
    """
    needle = normalize(text)
    return bool(needle) and any(isinstance(piece, str) and needle in normalize(piece) for piece in pieces)


#: Why a recorded step that still holds :data:`REDACTED_ASSERTION_TEXT` cannot
#: run: searching for the marker would pass while the real value is on screen.
#: Replay, the exported script, lint and the export refusal all say this.
REDACTED_TEXT_REFUSAL = (
    "expect_no_text was recorded with its text redacted; set 'text' to the value or a {{parameter}} before replaying it"
)


def check_forbidden_text(text: str) -> None:
    """Refuse a text no check can mean: empty (in every page), or the recording's redaction marker."""
    if not text:
        raise ValueError("expect_no_text: text is empty, and an empty string is in every page")
    if text == REDACTED_ASSERTION_TEXT:
        raise ValueError(REDACTED_TEXT_REFUSAL)


def leak_message(text: str, selector: str, scan: str) -> str:
    """The failure for drawn *text*: its length, never the text, which is usually a secret."""
    return f'forbidden text ({len(text)} chars) is rendered in "{selector}" ({scan})'


def truncation_message(selector: str, limit: int) -> str:
    return (
        f'expect_no_text: "{selector}" holds more than {limit} elements, so it was only partly '
        "checked and cannot pass; narrow the check with a selector for the region the text would appear "
        "in, or raise element_limit (or OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT)"
    )


#: What the frame scan reports (see :func:`new_scan_summary`).
NO_TEXT_SUMMARY_KEYS = ("matched", "frames_scanned", "frames_skipped", "truncated")
#: Everything a recorded expect_no_text holds besides its inputs: the scan
#: summary, the snapshot state and the text's keyed digest. Replay drops these
#: before calling the session method, and saving a macro strips them, so a new
#: summary field is added here or every recorded step raises TypeError on replay.
NO_TEXT_OBSERVATION_KEYS = (*NO_TEXT_SUMMARY_KEYS, "snapshot", "text_digest")


def new_scan_summary() -> dict[str, Any]:
    """What a scan covered: elements matched, frames read and skipped, and whether it hit the limit."""
    summary: dict[str, Any] = dict.fromkeys(NO_TEXT_SUMMARY_KEYS, 0)
    summary["truncated"] = False
    return summary


def skip_gone_frame(summary: dict[str, Any], position: int, detached: bool, error: object) -> bool:
    """Count a child frame that detached or navigated mid-read as skipped; False if *error* must raise.

    ``position`` 0 is the page's main frame, or the one frame the check is
    scoped to, and anything wrong there fails the check. A child frame going
    away (an ad rotating, a widget reloading) must not fail a check about the
    page on a frame that no longer exists.
    """
    if position == 0 or not (detached or NAVIGATED_AWAY.search(str(error))):
        return False
    summary["frames_skipped"] += 1
    return True


def fold_frame_result(summary: dict[str, Any], position: int, found: object, text: str, selector: str) -> bool:
    """Fold one frame's collector result into *summary*; False when the frame gave nothing and was skipped.

    Raises when the frame draws *text*, and when the main frame gave no result.
    """
    if not isinstance(found, dict):
        if position == 0:
            raise RuntimeError("expect_no_text: the rendered-text scan returned no result for the page")
        summary["frames_skipped"] += 1
        return False
    if contains(found.get("pieces", []), text):
        raise RuntimeError(leak_message(text, selector, "script scan"))
    summary["frames_scanned"] += 1
    summary["matched"] += int(found.get("matched") or 0)
    summary["truncated"] = summary["truncated"] or bool(found.get("truncated"))
    return True
