# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Whether a Chromium page still renders a classified value, read from a DOM snapshot.

The in-page redaction reaches only the open DOM. What reaches a screenshot is the
rendered page, which also holds closed shadow roots, CSS generated content, text split
across nodes and the contents of same-process frames. Chrome's
``DOMSnapshot.captureSnapshot`` reports that rendered surface -- layout text for every
document including closed shadow content and pseudo-elements, form values, computed
visibility -- so a redacted screenshot is refused unless this scan finds nothing.

Matching ignores whitespace and case and runs over each document's layout text joined
together, so a value split across elements, re-cased or wrapped still matches. That can
refuse a page where unrelated adjacent text happens to spell a value, which is the
acceptable direction to err.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Parameters for ``DOMSnapshot.captureSnapshot``; ``visibility`` is the one computed style read.
SNAPSHOT_PARAMS: dict[str, Any] = {"computedStyles": ["visibility"]}

#: Elements whose pixels no text scan can read; they must be hidden in the screenshot.
OPAQUE_ELEMENTS = frozenset({"CANVAS", "EMBED", "FRAME", "IFRAME", "OBJECT", "VIDEO"})

#: Attributes that load a resource; a visible element carrying a value in one is refused.
LOADING_ATTRIBUTES = frozenset({"src", "srcset", "data", "poster"})

_WHITESPACE = re.compile(r"\s+")
_TEXT_NODE = 3


def _normalize(text: str) -> str:
    return _WHITESPACE.sub("", text).casefold()


def _visibility(layout: Mapping[str, Any], strings: Sequence[str]) -> dict[int, str]:
    """Computed visibility per node that has a layout object."""
    result: dict[int, str] = {}
    styles = layout.get("styles", [])
    for position, node in enumerate(layout.get("nodeIndex", [])):
        style = styles[position] if position < len(styles) else []
        index = style[0] if style else -1
        result[node] = strings[index] if 0 <= index < len(strings) else ""
    return result


def _hidden_documents(documents: Sequence[Mapping[str, Any]], strings: Sequence[str]) -> set[int]:
    """Documents rendered only inside a hidden frame element, transitively."""
    owners: dict[int, tuple[int, int]] = {}
    for parent, document in enumerate(documents):
        rare = document.get("nodes", {}).get("contentDocumentIndex", {})
        for node, child in zip(rare.get("index", []), rare.get("value", []), strict=False):
            owners[child] = (parent, node)
    hidden: set[int] = set()
    changed = True
    while changed:
        changed = False
        for child, (parent, node) in owners.items():
            if child in hidden:
                continue
            shown = _visibility(documents[parent].get("layout", {}), strings).get(node)
            if parent in hidden or shown is None or shown == "hidden":
                hidden.add(child)
                changed = True
    return hidden


class _Snapshot:
    """Index-safe accessors over one ``DOMSnapshot.captureSnapshot`` result."""

    def __init__(self, snapshot: Mapping[str, Any], values: Iterable[str]) -> None:
        self.strings: Sequence[str] = snapshot.get("strings", [])
        self.documents: Sequence[Mapping[str, Any]] = snapshot.get("documents", [])
        self.needles = sorted(
            {needle for needle in (_normalize(value) for value in values if isinstance(value, str)) if needle}
        )

    def text(self, index: int) -> str:
        return self.strings[index] if 0 <= index < len(self.strings) else ""

    def holds(self, value: str) -> bool:
        normalized = _normalize(value)
        return any(needle in normalized for needle in self.needles)


def _text_reasons(snap: _Snapshot, document: Mapping[str, Any]) -> set[str]:
    """Rendered text (joined, so split values match) and form values."""
    reasons: set[str] = set()
    layout = document.get("layout", {})
    if snap.holds("".join(snap.text(index) for index in layout.get("text", []))):
        reasons.add("rendered text")
    nodes = document.get("nodes", {})
    for key in ("inputValue", "textValue"):
        if any(snap.holds(snap.text(index)) for index in nodes.get(key, {}).get("value", [])):
            reasons.add("form value")
    return reasons


def _visible_element_reasons(snap: _Snapshot, nodes: Mapping[str, Any], node: int) -> set[str]:
    """A shown element that is opaque, or whose resource address holds a value."""
    reasons: set[str] = set()
    names = nodes.get("nodeName", [])
    name = snap.text(names[node]).upper()
    if name in OPAQUE_ELEMENTS:
        reasons.add(f"visible {name.lower()}")
    attributes = nodes.get("attributes", [])
    pairs = attributes[node] if node < len(attributes) else []
    for key, value in zip(pairs[::2], pairs[1::2], strict=False):
        if snap.text(key).lower() in LOADING_ATTRIBUTES and snap.holds(snap.text(value)):
            reasons.add("visible resource address")
    return reasons


def _is_option_text_leak(snap: _Snapshot, nodes: Mapping[str, Any], node: int) -> bool:
    """A text node inside an ``<option>``, whose label a select renders outside the layout text."""
    names = nodes.get("nodeName", [])
    types = nodes.get("nodeType", [])
    parents = nodes.get("parentIndex", [])
    node_values = nodes.get("nodeValue", [])
    if node >= len(types) or types[node] != _TEXT_NODE:
        return False
    parent = parents[node] if node < len(parents) else -1
    if not 0 <= parent < len(names) or snap.text(names[parent]).upper() != "OPTION":
        return False
    return snap.holds(snap.text(node_values[node] if node < len(node_values) else -1))


def _document_reasons(snap: _Snapshot, document: Mapping[str, Any]) -> set[str]:
    reasons = _text_reasons(snap, document)
    nodes = document.get("nodes", {})
    visibility = _visibility(document.get("layout", {}), snap.strings)
    for node in range(len(nodes.get("nodeName", []))):
        shown = visibility.get(node)
        if shown is not None and shown != "hidden":
            reasons |= _visible_element_reasons(snap, nodes, node)
        if _is_option_text_leak(snap, nodes, node):
            reasons.add("option text")
    return reasons


def rendered_leaks(snapshot: Mapping[str, Any], values: Iterable[str]) -> list[str]:
    """Kinds of rendered surface still holding a classified value; empty when clean.

    The returned reasons name what was found, never the value itself, so they are safe
    to put in an error message or a log line.
    """
    snap = _Snapshot(snapshot, values)
    if not snap.needles:
        return []
    hidden = _hidden_documents(snap.documents, snap.strings)
    reasons: set[str] = set()
    for position, document in enumerate(snap.documents):
        if position not in hidden:
            reasons |= _document_reasons(snap, document)
    return sorted(reasons)
