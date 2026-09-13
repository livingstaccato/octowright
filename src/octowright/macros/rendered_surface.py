# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Whether a Chromium page still renders a classified value, read from a DOM snapshot.

The in-page redaction reaches only the open DOM. What reaches a screenshot is the
rendered page, which also holds closed shadow roots, CSS generated content, text split
across nodes and the contents of same-process frames. Chrome's
``DOMSnapshot.captureSnapshot`` reports that rendered surface: layout text and text
boxes for every document including closed shadow content and pseudo-elements, form
values, attributes and computed styles. A redacted screenshot is refused unless this
scan finds nothing.

Values compare as :func:`octowright.macros.redaction_text.normalize` defines, and
reversed, so right-to-left overrides match. Text is matched in three arrangements:

- every layout text of a document joined in document order;
- the text boxes in document order;
- the text boxes in visual order (by line, then left to right), which catches a value
  whose parts are reordered by flex ``order``, absolute positioning and the like.

Within the text boxes, a value of at least ``GAP_MIN_LENGTH`` characters also matches
when a few unrelated boxes sit between its parts, such as screen-reader-only text. A
numeric value also matches by its digits (see ``redaction_text``). Every rule errs toward
refusal: unrelated text that happens to spell a value refuses a page that was clean.

Also refused: a drawn form value holding a value, unless the control masks its text with
``-webkit-text-security``; a shown ``placeholder``, ``alt`` or ``label`` holding a value; a
shown opaque element (canvas, media, embed, frame); a shown element, a ``<picture>``'s
image or an SVG image or filter image whose resource address holds a value; and a shown
image style (``content: url()`` included) holding one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from octowright.macros.redaction_text import digit_needles, digits_form, normalize

#: Computed styles that draw an image; ``content`` also draws ``url()`` on pseudo-elements.
IMAGE_STYLES = ("background-image", "border-image-source", "content", "list-style-image", "mask-image")

#: Computed styles read per layout object: visibility, then text masking, then the image styles.
SNAPSHOT_PARAMS: dict[str, Any] = {"computedStyles": ["visibility", "-webkit-text-security", *IMAGE_STYLES]}

#: Position of the first image style in a layout object's computed styles.
_FIRST_IMAGE_STYLE = 2

#: Elements whose pixels no text scan can read; they must be hidden in the screenshot.
OPAQUE_ELEMENTS = frozenset({"CANVAS", "EMBED", "FRAME", "IFRAME", "OBJECT", "VIDEO"})

#: Attributes that load a resource; a shown element carrying a value in one is refused.
LOADING_ATTRIBUTES = frozenset({"src", "srcset", "srcdoc", "data", "poster"})

#: Attributes whose text the browser draws itself, outside the layout text.
DRAWN_ATTRIBUTES = frozenset({"alt", "label", "placeholder"})

#: Link attributes that load a resource on some elements.
HREF_ATTRIBUTES = frozenset({"href", "xlink:href"})

#: Elements that draw the resource their link attribute loads.
HREF_DRAWN_ELEMENTS = frozenset({"FEIMAGE", "IMAGE", "USE"})

#: Elements whose link attribute loads a resource, drawn or not; the page never rewrites it.
HREF_LOADING_ELEMENTS = HREF_DRAWN_ELEMENTS | {"BASE", "LINK"}

#: Input types whose value is not drawn as masked text. Every other type, an unknown one
#: included, is a text control whose value ``-webkit-text-security`` masks.
UNMASKED_INPUT_TYPES = frozenset(
    {"button", "checkbox", "color", "date", "datetime-local", "file", "hidden", "image"}
    | {"month", "radio", "range", "reset", "submit", "time", "week"}
)

#: Unrelated text boxes allowed between two parts of one value.
MAX_GAP = 4

#: Shorter values match only as contiguous text; a gap would match ordinary words.
GAP_MIN_LENGTH = 6

_TEXT_NODE = 3


def _styles(layout: Mapping[str, Any], strings: Sequence[str]) -> dict[int, list[str]]:
    """Computed style strings per node that has a layout object, in ``SNAPSHOT_PARAMS`` order."""
    result: dict[int, list[str]] = {}
    styles = layout.get("styles", [])
    for position, node in enumerate(layout.get("nodeIndex", [])):
        style = styles[position] if position < len(styles) else []
        result[node] = [strings[index] if 0 <= index < len(strings) else "" for index in style]
    return result


def _visibility(layout: Mapping[str, Any], strings: Sequence[str]) -> dict[int, str]:
    """Computed visibility per node that has a layout object."""
    return {node: (style[0] if style else "") for node, style in _styles(layout, strings).items()}


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


def spelled_by_pieces(needle: str, pieces: Sequence[str], max_gap: int) -> bool:
    """Whether consecutive ``pieces`` spell ``needle``, skipping up to ``max_gap`` pieces at a time.

    A piece may end with the start of the value and a later piece may begin with its end;
    every piece between the two that is not skipped must be a whole middle part.
    """
    states: set[tuple[int, int]] = set()
    for piece in pieces:
        if not piece:
            continue
        if needle in piece:
            return True
        following: set[tuple[int, int]] = set()
        for matched, gap in states:
            rest = needle[matched:]
            if piece.startswith(rest):
                return True
            if rest.startswith(piece):
                following.add((matched + len(piece), 0))
            if gap < max_gap:
                following.add((matched, gap + 1))
        for length in range(1, min(len(piece), len(needle)) + 1):
            if needle.startswith(piece[-length:]):
                following.add((length, 0))
        states = following
    return False


def _visual_order(fragments: Iterable[tuple[float, float, float, str]]) -> list[str]:
    """Text box strings ordered by line (vertical centre), then left to right."""
    ordered: list[str] = []
    line: list[tuple[float, str]] = []
    centre = half = 0.0
    for top, height, left, text in sorted(fragments):
        middle = top + height / 2
        if line and abs(middle - centre) > half:
            ordered.extend(text for _, text in sorted(line))
            line = []
        if not line:
            centre, half = middle, max(height / 2, 0.5)
        line.append((left, text))
    ordered.extend(text for _, text in sorted(line))
    return ordered


class ValueMatcher:
    """Whether text holds, or pieces of text spell, one of the classified values."""

    def __init__(self, values: Iterable[str]) -> None:
        texts = [value for value in values if isinstance(value, str)]
        forward = {normalize(value) for value in texts}
        forward.discard("")
        self.needles = sorted(forward | {needle[::-1] for needle in forward})
        digits = {needle for value in texts for needle in digit_needles(value)}
        self.digit_needles = sorted(digits | {needle[::-1] for needle in digits})

    def __bool__(self) -> bool:
        return bool(self.needles or self.digit_needles)

    def holds(self, value: str) -> bool:
        normalized = normalize(value)
        if any(needle in normalized for needle in self.needles):
            return True
        digits = digits_form(value)
        return any(needle in digits for needle in self.digit_needles)

    def spelled(self, texts: Iterable[str]) -> bool:
        texts = list(texts)
        return _spelled_by_any(self.needles, [normalize(text) for text in texts]) or _spelled_by_any(
            self.digit_needles, [digits_form(text) for text in texts]
        )


class _Snapshot(ValueMatcher):
    """Index-safe accessors over one ``DOMSnapshot.captureSnapshot`` result."""

    def __init__(self, snapshot: Mapping[str, Any], values: Iterable[str]) -> None:
        super().__init__(values)
        self.strings: Sequence[str] = snapshot.get("strings", [])
        self.documents: Sequence[Mapping[str, Any]] = snapshot.get("documents", [])

    def text(self, index: int) -> str:
        return self.strings[index] if 0 <= index < len(self.strings) else ""


def _spelled_by_any(needles: Iterable[str], pieces: Sequence[str]) -> bool:
    return any(spelled_by_pieces(needle, pieces, MAX_GAP if len(needle) >= GAP_MIN_LENGTH else 0) for needle in needles)


def _fragments(snap: _Snapshot, document: Mapping[str, Any]) -> list[tuple[float, float, float, str]]:
    """Each text box as ``(top, height, left, text)``, in document order."""
    texts = document.get("layout", {}).get("text", [])
    boxes = document.get("textBoxes", {})
    fragments: list[tuple[float, float, float, str]] = []
    columns = (boxes.get(key, []) for key in ("layoutIndex", "bounds", "start", "length"))
    for layout_index, bounds, start, length in zip(*columns, strict=False):
        text = snap.text(texts[layout_index]) if 0 <= layout_index < len(texts) else ""
        left, top, _width, height = [*bounds, 0.0, 0.0, 0.0, 0.0][:4]
        fragments.append((top, height, left, text[start : start + length]))
    return fragments


def _text_reasons(snap: _Snapshot, document: Mapping[str, Any]) -> set[str]:
    """Rendered text in every arrangement, and form values."""
    reasons: set[str] = set()
    joined = "".join(snap.text(index) for index in document.get("layout", {}).get("text", []))
    fragments = _fragments(snap, document)
    in_order = [fragment[3] for fragment in fragments]
    if snap.holds(joined) or snap.spelled(in_order) or snap.spelled(_visual_order(fragments)):
        reasons.add("rendered text")
    return reasons


def _masks_its_value(snap: _Snapshot, nodes: Mapping[str, Any], node: int) -> bool:
    """Whether ``-webkit-text-security`` masks the drawn value of this control's kind."""
    name = _name(snap, nodes, node)
    if name == "TEXTAREA":
        return True
    if name != "INPUT":
        return False
    return dict(_attributes(snap, nodes, node)).get("type", "").lower() not in UNMASKED_INPUT_TYPES


def _form_value_reasons(snap: _Snapshot, nodes: Mapping[str, Any], styles: Mapping[int, Sequence[str]]) -> set[str]:
    """A control that draws a value holding a classified value, legibly.

    A control without a layout object, or with hidden visibility, draws nothing; one of a
    maskable kind whose text security is not ``none`` draws only mask characters.
    """
    for key in ("inputValue", "textValue"):
        rare = nodes.get(key, {})
        for node, value in zip(rare.get("index", []), rare.get("value", []), strict=False):
            style = styles.get(node)
            if style is None or not snap.holds(snap.text(value)) or style[0] == "hidden":
                continue
            masked = len(style) > 1 and style[1] not in ("", "none")
            if not (masked and _masks_its_value(snap, nodes, node)):
                return {"form value"}
    return set()


def _attributes(snap: _Snapshot, nodes: Mapping[str, Any], node: int) -> list[tuple[str, str]]:
    attributes = nodes.get("attributes", [])
    pairs = attributes[node] if node < len(attributes) else []
    return [(snap.text(key).lower(), snap.text(value)) for key, value in zip(pairs[::2], pairs[1::2], strict=False)]


def _name(snap: _Snapshot, nodes: Mapping[str, Any], node: int) -> str:
    names = nodes.get("nodeName", [])
    return snap.text(names[node]).upper() if 0 <= node < len(names) else ""


def _visible_element_reasons(snap: _Snapshot, nodes: Mapping[str, Any], node: int, styles: Sequence[str]) -> set[str]:
    """A shown element that is opaque, or whose resource address or image style holds a value."""
    reasons: set[str] = set()
    name = _name(snap, nodes, node)
    if name in OPAQUE_ELEMENTS:
        reasons.add(f"visible {name.lower()}")
    # A ``<source>`` draws nothing itself; its picture's image is judged by ``_drawn_attribute_reasons``.
    if name != "SOURCE" and any(
        _loads(name, key) and snap.holds(value) for key, value in _attributes(snap, nodes, node)
    ):
        reasons.add("visible resource address")
    if any(snap.holds(style) for style in styles[_FIRST_IMAGE_STYLE:]):
        reasons.add("visible style image")
    return reasons


def _loads(name: str, key: str) -> bool:
    """Whether attribute ``key`` of element ``name`` is the address of a resource it draws."""
    return key in LOADING_ATTRIBUTES or (key in HREF_ATTRIBUTES and name in HREF_DRAWN_ELEMENTS)


def _feeds_shown_picture_image(
    snap: _Snapshot, nodes: Mapping[str, Any], node: int, visibility: Mapping[int, str]
) -> bool:
    """Whether ``node`` is a ``<picture>`` source whose picture has an image that is not hidden."""
    parents = nodes.get("parentIndex", [])
    parent = parents[node] if node < len(parents) else -1
    if _name(snap, nodes, node) != "SOURCE" or _name(snap, nodes, parent) != "PICTURE":
        return False
    return any(
        _name(snap, nodes, sibling) == "IMG" and visibility.get(sibling, "hidden") != "hidden"
        for sibling, owner in enumerate(parents)
        if owner == parent
    )


def _drawn_attribute_reasons(
    snap: _Snapshot, nodes: Mapping[str, Any], node: int, visibility: Mapping[int, str]
) -> set[str]:
    """Attribute text the browser draws, and a ``<picture>`` source feeding a shown image."""
    reasons: set[str] = set()
    attributes = _attributes(snap, nodes, node)
    if any(key in DRAWN_ATTRIBUTES and snap.holds(value) for key, value in attributes):
        reasons.add("visible attribute text")
    name = _name(snap, nodes, node)
    loading = any(_loads(name, key) and snap.holds(value) for key, value in attributes)
    # A filter image has no layout object of its own, so its visibility cannot clear it.
    if loading and (name == "FEIMAGE" or _feeds_shown_picture_image(snap, nodes, node, visibility)):
        reasons.add("visible resource address")
    return reasons


def _is_option_text_leak(snap: _Snapshot, nodes: Mapping[str, Any], node: int) -> bool:
    """A text node inside an ``<option>``, whose label a select renders outside the layout text."""
    types = nodes.get("nodeType", [])
    parents = nodes.get("parentIndex", [])
    node_values = nodes.get("nodeValue", [])
    if node >= len(types) or types[node] != _TEXT_NODE:
        return False
    parent = parents[node] if node < len(parents) else -1
    if _name(snap, nodes, parent) != "OPTION":
        return False
    return snap.holds(snap.text(node_values[node] if node < len(node_values) else -1))


def _document_reasons(snap: _Snapshot, document: Mapping[str, Any]) -> set[str]:
    reasons = _text_reasons(snap, document)
    nodes = document.get("nodes", {})
    styles = _styles(document.get("layout", {}), snap.strings)
    reasons |= _form_value_reasons(snap, nodes, styles)
    visibility = {node: (style[0] if style else "") for node, style in styles.items()}
    for node in range(len(nodes.get("nodeName", []))):
        shown = visibility.get(node)
        if shown is not None and shown != "hidden":
            reasons |= _visible_element_reasons(snap, nodes, node, styles[node])
        if shown != "hidden":
            reasons |= _drawn_attribute_reasons(snap, nodes, node, visibility)
        if _is_option_text_leak(snap, nodes, node):
            reasons.add("option text")
    return reasons


def rendered_leaks(snapshot: Mapping[str, Any], values: Iterable[str]) -> list[str]:
    """Kinds of rendered surface still holding a classified value; empty when clean.

    The returned reasons name what was found, never the value itself, so they are safe
    to put in an error message or a log line.
    """
    snap = _Snapshot(snapshot, values)
    if not snap:
        return []
    hidden = _hidden_documents(snap.documents, snap.strings)
    reasons: set[str] = set()
    for position, document in enumerate(snap.documents):
        if position not in hidden:
            reasons |= _document_reasons(snap, document)
    return sorted(reasons)
