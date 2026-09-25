# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What Chromium's DOM snapshot draws, for ``expect_no_text``'s closed-shadow-root scan.

What "drawn" means, the in-page collector, and the comparison live in
:mod:`octowright.drawn_text`, which the exported CLI shares. This module holds
only what replay adds on Chromium: script cannot reach a closed shadow root, and
``DOMSnapshot.captureSnapshot`` can.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from octowright.drawn_text import OWN_OVERLAY_ID_PREFIX


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
