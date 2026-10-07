# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The rendered-surface scan on snapshots with missing, short and out-of-range tables.

``DOMSnapshot.captureSnapshot`` leaves a table out when nothing fills it and
indexes strings and nodes by position, so the scan reads every table with a
default and every index with a bound. A scan that crashed on a sparse snapshot
would refuse every screenshot of that page; one that read past a bound would
judge the wrong string. Each test here is one such shape, with the reasons the
scan must still report. `test_macro_rendered_surface.py` covers the matching
itself on well-formed snapshots.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from octowright.macros.rendered_surface import rendered_leaks, spelled_by_pieces

VALUE = "quokka7"


class Strings:
    """The snapshot's string table, built by interning."""

    def __init__(self) -> None:
        self.table: list[str] = []

    def __call__(self, text: str) -> int:
        if text not in self.table:
            self.table.append(text)
        return self.table.index(text)


def _scan(snapshot: dict[str, Any], values: tuple[str, ...] = (VALUE,)) -> list[str]:
    """`rendered_leaks`, failing rather than hanging when the frame walk does not settle."""
    result: list[list[str] | BaseException] = []

    def scan() -> None:
        try:
            result.append(rendered_leaks(snapshot, values))
        except BaseException as exc:  # handed back to the test
            result.append(exc)

    worker = threading.Thread(target=scan, daemon=True)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive(), "the rendered-surface scan did not finish"
    if isinstance(result[0], BaseException):
        raise result[0]
    return result[0]


def _leaks(strings: Strings, *documents: dict[str, Any], values: tuple[str, ...] = (VALUE,)) -> list[str]:
    return _scan({"strings": strings.table, "documents": list(documents)}, values)


def _boxes(s: Strings, *boxes: tuple[str, list[float]]) -> dict[str, Any]:
    """A document whose layout texts are *boxes* in document order, one text box each, at the given bounds."""
    return {
        "layout": {"text": [s(text) for text, _ in boxes]},
        "textBoxes": {
            "layoutIndex": list(range(len(boxes))),
            "bounds": [bounds for _, bounds in boxes],
            "start": [0] * len(boxes),
            "length": [len(text) for text, _ in boxes],
        },
    }


# --- computed styles -------------------------------------------------------------------------


def test_a_layout_without_styles_reads_every_node_as_shown() -> None:
    s = Strings()
    doc = {"nodes": {"nodeName": [s("CANVAS")]}, "layout": {"nodeIndex": [0]}}

    assert _leaks(s, doc) == ["visible canvas"]


def test_a_node_past_the_end_of_the_styles_has_no_style() -> None:
    s = Strings()
    doc = {
        "nodes": {"nodeName": [s("CANVAS"), s("VIDEO")]},
        "layout": {"nodeIndex": [0, 1], "styles": [[s("hidden")]]},
    }

    assert _leaks(s, doc) == ["visible video"]


def test_a_style_string_index_is_read_only_within_the_string_table() -> None:
    s = Strings()
    hidden = s("hidden")  # index 0: a style naming it is hidden
    names = [s("CANVAS"), s("VIDEO"), s("IFRAME")]
    doc = {
        "nodes": {"nodeName": names},
        # The canvas is hidden; the video's index is one past the table; the iframe's is negative.
        "layout": {"nodeIndex": [0, 1, 2], "styles": [[hidden], [len(s.table)], [-1]]},
    }

    assert _leaks(s, doc) == ["visible iframe", "visible video"]


# --- form values -----------------------------------------------------------------------------


def _input(s: Strings, styles: list[list[int]], **nodes: Any) -> dict[str, Any]:
    return {
        "nodes": {"nodeName": [s("INPUT")], "inputValue": {"index": [0], "value": [s(VALUE)]}, **nodes},
        "layout": {"nodeIndex": [0], "styles": styles},
    }


def test_a_text_security_style_out_of_the_string_table_does_not_mask() -> None:
    s = Strings()
    visible = s("visible")

    assert _leaks(s, _input(s, [[visible, 99]])) == ["form value"]


def test_an_empty_text_security_style_does_not_mask() -> None:
    s = Strings()
    styles = [[s("visible"), s("")]]

    assert _leaks(s, _input(s, styles)) == ["form value"]


def test_a_control_with_only_a_visibility_style_is_not_masked() -> None:
    s = Strings()

    assert _leaks(s, _input(s, [[s("visible")]])) == ["form value"]


def test_a_hidden_control_does_not_stop_the_next_one_being_judged() -> None:
    s = Strings()
    value, name = s(VALUE), s("INPUT")
    doc = {
        "nodes": {"nodeName": [name, name], "inputValue": {"index": [0, 1], "value": [value, value]}},
        "layout": {"nodeIndex": [0, 1], "styles": [[s("hidden")], [s("visible")]]},
    }

    assert _leaks(s, doc) == ["form value"]


def test_form_value_columns_of_different_lengths_are_read_pairwise() -> None:
    s = Strings()
    doc = {
        "nodes": {"nodeName": [s("INPUT")], "inputValue": {"index": [0, 1], "value": [s(VALUE)]}},
        "layout": {"nodeIndex": [0], "styles": [[s("visible")]]},
    }

    assert _leaks(s, doc) == ["form value"]


def test_form_value_columns_of_different_lengths_are_read_to_the_shorter_end() -> None:
    s = Strings()
    doc = {
        "nodes": {"nodeName": [s("INPUT")], "inputValue": {"index": [0, 1], "value": [s("clean")]}},
        "layout": {"nodeIndex": [0], "styles": [[s("visible")]]},
    }

    assert _leaks(s, doc) == []


def test_a_control_without_a_node_name_is_not_a_maskable_kind() -> None:
    s = Strings()
    doc = {
        "nodes": {"inputValue": {"index": [0], "value": [s(VALUE)]}},
        "layout": {"nodeIndex": [0], "styles": [[s("visible"), s("disc")]]},
    }

    assert _leaks(s, doc) == ["form value"]


# --- frames ----------------------------------------------------------------------------------


def test_a_document_inside_a_frame_hidden_later_in_the_walk_is_hidden_too() -> None:
    """The frame chain is recorded out of order, so a later pass is what hides the last document."""
    s = Strings()
    iframe, hidden, visible = s("IFRAME"), s("hidden"), s("visible")

    def frames(children: list[int], visibility: int) -> dict[str, Any]:
        return {
            "nodes": {
                "nodeName": [iframe] * len(children),
                "contentDocumentIndex": {"index": list(range(len(children))), "value": children},
            },
            "layout": {"nodeIndex": list(range(len(children))), "styles": [[visibility]] * len(children)},
        }

    documents = [
        frames([4, 2], hidden),  # 0: the page; both its frames are hidden
        frames([3], visible),  # 1: shown inside document 2
        frames([1], visible),  # 2: shown inside a hidden frame of document 0
        {"layout": {"text": [s(VALUE)]}},  # 3: shown inside document 1
        {},  # 4: an empty document in a hidden frame
    ]

    assert _leaks(s, *documents) == []


def test_frame_columns_of_different_lengths_are_read_pairwise() -> None:
    s = Strings()
    page = {
        "nodes": {"nodeName": [s("IFRAME")], "contentDocumentIndex": {"index": [0, 5], "value": [1]}},
        "layout": {"nodeIndex": [0], "styles": [[s("hidden")]]},
    }

    assert _leaks(s, page, {"layout": {"text": [s(VALUE)]}}) == []


def test_a_frame_with_a_layout_object_but_no_style_is_shown() -> None:
    s = Strings()
    page = {
        "nodes": {"nodeName": [s("IFRAME")], "contentDocumentIndex": {"index": [0], "value": [1]}},
        "layout": {"nodeIndex": [0], "styles": []},
    }

    assert _leaks(s, page, {"layout": {"text": [s(VALUE)]}}) == ["rendered text", "visible iframe"]


def test_a_frame_in_a_document_without_layout_draws_nothing() -> None:
    s = Strings()
    page = {"nodes": {"nodeName": [s("IFRAME")], "contentDocumentIndex": {"index": [0], "value": [1]}}}

    assert _leaks(s, page, {"layout": {"text": [s(VALUE)]}}) == []


# --- text pieces -----------------------------------------------------------------------------


def test_a_middle_part_resets_the_gap_allowance() -> None:
    pieces = ["ab", "z1", "z2", "z3", "z4", "cd", "z5", "z6", "z7", "z8", "efgh"]

    assert spelled_by_pieces("abcdefgh", pieces, 4)


def test_the_gap_allowance_counts_each_skipped_piece_once() -> None:
    assert spelled_by_pieces("abcdefgh", ["abcd", "z1", "z2", "z3", "efgh"], 4)
    assert not spelled_by_pieces("abcdefgh", ["abcd", "z1", "z2", "z3", "z4", "z5", "efgh"], 4)


def test_a_piece_ending_with_the_first_character_starts_a_match() -> None:
    assert spelled_by_pieces("abcdef", ["xa", "bcdef"], 0)


def test_a_six_character_value_matches_across_a_gap() -> None:
    s = Strings()
    doc = _boxes(s, ("quo", [0, 0, 10, 10]), ("zz", [10, 0, 10, 10]), ("kka", [20, 0, 10, 10]))

    assert _leaks(s, doc, values=("quokka",)) == ["rendered text"]


# --- visual order ----------------------------------------------------------------------------


def test_boxes_whose_centres_are_within_half_a_line_are_one_line() -> None:
    """The second box's centre (39) is within half the first's height (20) of the first's (20)."""
    s = Strings()
    doc = _boxes(s, ("kka7", [100, 0, 50, 40]), ("quo", [0, 36, 50, 6]))

    assert _leaks(s, doc) == ["rendered text"]


def test_zero_height_boxes_a_pixel_apart_are_two_lines() -> None:
    s = Strings()
    doc = _boxes(s, ("kka7", [100, 0, 50, 0]), ("quo", [0, 1, 50, 0]))

    assert _leaks(s, doc) == []


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # No bounds at all: at the origin, left of a box half a pixel in.
        (("kka7", [0.5, 0, 1, 0]), ("quo", [])),
        # Only a left edge: at the top, on the line of a box 0.2 below it.
        (("kka7", [5, 0.2, 1, 0]), ("quo", [0])),
        # No height: a zero-height line, which a box 0.9 below is not on.
        (("kka7", [0, 0.9, 1, 0]), ("quo", [5, 0, 1])),
        # No bounds, so no height either: the same, with the box below also to the left.
        (("kka7", [-1, 0.9, 1, 0]), ("quo", [])),
    ],
    ids=["no-bounds", "left-only", "no-height", "no-bounds-height"],
)
def test_missing_bounds_are_zero(first: tuple[str, list[float]], second: tuple[str, list[float]]) -> None:
    s = Strings()

    assert _leaks(s, _boxes(s, first, second)) == ["rendered text"]


# --- text boxes ------------------------------------------------------------------------------


def test_a_snapshot_without_strings_reads_every_string_as_empty() -> None:
    assert _scan({"documents": [{"layout": {"text": [0]}}]}) == []


def test_a_snapshot_without_documents_is_clean() -> None:
    assert _scan({"strings": [VALUE]}) == []


def test_a_layout_text_index_past_the_string_table_is_empty() -> None:
    assert _scan({"strings": [VALUE], "documents": [{"layout": {"text": [1]}}]}) == []


def test_text_boxes_without_layout_text_are_empty() -> None:
    s = Strings()
    doc = {
        "layout": {},
        "textBoxes": {"layoutIndex": [0], "bounds": [[0, 0, 1, 1]], "start": [0], "length": [4]},
    }

    assert _leaks(s, doc, values=("xxxx",)) == []


def test_a_text_box_past_the_layout_texts_is_empty() -> None:
    s = Strings()
    doc = {
        "layout": {"text": [s("other")]},
        "textBoxes": {"layoutIndex": [1], "bounds": [[0, 0, 1, 1]], "start": [0], "length": [4]},
    }

    assert _leaks(s, doc, values=("xxxx",)) == []


def test_text_box_columns_of_different_lengths_are_read_pairwise() -> None:
    s = Strings()
    doc = {
        "layout": {"text": [s(VALUE)]},
        "textBoxes": {"layoutIndex": [0, 0], "bounds": [[0, 0, 1, 1]], "start": [0, 0], "length": [7, 7]},
    }

    assert _leaks(s, doc) == ["rendered text"]


def test_a_document_without_layout_still_has_its_attributes_judged() -> None:
    s = Strings()
    doc = {
        "nodes": {"nodeName": [s("INPUT")], "attributes": [[s("placeholder"), s(VALUE)]]},
        "textBoxes": {"layoutIndex": [0], "bounds": [[0, 0, 1, 1]], "start": [0], "length": [4]},
    }

    assert _leaks(s, doc) == ["visible attribute text"]


def test_a_document_without_nodes_still_has_its_text_judged() -> None:
    s = Strings()

    assert _leaks(s, {"layout": {"text": [s(VALUE)]}}) == ["rendered text"]


# --- attributes and names ---------------------------------------------------------------------


def test_an_element_without_attributes_is_judged_by_its_name_alone() -> None:
    s = Strings()
    doc = {"nodes": {"nodeName": [s("IMG")]}, "layout": {"nodeIndex": [0], "styles": [[s("visible")]]}}

    assert _leaks(s, doc) == []


def test_an_attribute_is_named_by_its_last_local_part() -> None:
    s = Strings()
    doc = {
        "nodes": {"nodeName": [s("image")], "attributes": [[s("a:xlink:href"), s(VALUE)]]},
        "layout": {"nodeIndex": [0], "styles": [[s("visible")]]},
    }

    assert _leaks(s, doc) == ["visible resource address"]


def test_every_attribute_pair_is_read() -> None:
    s = Strings()
    doc = {
        "nodes": {"nodeName": [s("INPUT")], "attributes": [[s("class"), s("x"), s("placeholder"), s(VALUE)]]},
        "layout": {"nodeIndex": [0], "styles": [[s("visible")]]},
    }

    assert _leaks(s, doc) == ["visible attribute text"]


def test_a_dangling_attribute_name_is_ignored() -> None:
    s = Strings()
    doc = {
        "nodes": {"nodeName": [s("INPUT")], "attributes": [[s("placeholder"), s(VALUE), s("dangling")]]},
        "layout": {"nodeIndex": [0], "styles": [[s("visible")]]},
    }

    assert _leaks(s, doc) == ["visible attribute text"]


def test_an_element_is_named_by_its_last_local_part() -> None:
    s = Strings()
    doc = {"nodes": {"nodeName": [s("svg:x:canvas")]}, "layout": {"nodeIndex": [0], "styles": [[s("visible")]]}}

    assert _leaks(s, doc) == ["visible canvas"]


# --- pictures and filter images --------------------------------------------------------------


def test_a_source_without_parent_links_feeds_no_picture() -> None:
    s = Strings()
    doc = {
        "nodes": {"nodeName": [s("SOURCE")], "attributes": [[s("srcset"), s(VALUE)]]},
        "layout": {"nodeIndex": [0], "styles": [[s("visible")]]},
    }

    assert _leaks(s, doc) == []


def test_a_source_past_the_end_of_the_parent_links_feeds_no_picture() -> None:
    s = Strings()
    doc = {
        "nodes": {
            "nodeName": [s("DIV"), s("PICTURE"), s("IMG"), s("SOURCE")],
            "parentIndex": [-1, 0, 1],
            "attributes": [[], [], [], [s("srcset"), s(VALUE)]],
        },
        "layout": {"nodeIndex": [2], "styles": [[s("visible")]]},
    }

    assert _leaks(s, doc) == []


def test_a_picture_image_without_a_layout_object_is_not_shown() -> None:
    s = Strings()
    doc = {
        "nodes": {
            "nodeName": [s("PICTURE"), s("SOURCE"), s("IMG")],
            "parentIndex": [-1, 0, 0],
            "attributes": [[], [s("srcset"), s(VALUE)], []],
        },
    }

    assert _leaks(s, doc) == []


def test_a_filter_image_loading_a_clean_address_is_clean() -> None:
    s = Strings()
    doc = {"nodes": {"nodeName": [s("FEIMAGE")], "attributes": [[s("href"), s("clean.png")]]}}

    assert _leaks(s, doc) == []


# --- option text -----------------------------------------------------------------------------


def test_nodes_without_types_hold_no_option_text() -> None:
    s = Strings()
    doc = {"nodes": {"nodeName": [s("OPTION"), s("#text")], "nodeValue": [-1, s(VALUE)], "parentIndex": [-1, 0]}}

    assert _leaks(s, doc) == []


def test_a_text_node_without_parent_links_is_in_no_option() -> None:
    s = Strings()
    doc = {"nodes": {"nodeName": [s("OPTION"), s("#text")], "nodeType": [1, 3], "nodeValue": [-1, s(VALUE)]}}

    assert _leaks(s, doc) == []


@pytest.mark.parametrize("node_values", [None, [-1]], ids=["no-values", "short-values"])
def test_an_option_text_without_a_value_holds_nothing(node_values: list[int] | None) -> None:
    s = Strings()
    s("OPTION")
    s(VALUE)  # index 1: what reading one past a short value table would find
    nodes: dict[str, Any] = {"nodeName": [s("OPTION"), s("#text")], "nodeType": [1, 3], "parentIndex": [-1, 0]}
    if node_values is not None:
        nodes["nodeValue"] = node_values

    assert _leaks(s, {"nodes": nodes}) == []


def test_option_text_is_a_text_node_in_an_option() -> None:
    s = Strings()
    doc = {
        "nodes": {
            "nodeName": [s("OPTION"), s("#text")],
            "nodeType": [1, 3],
            "parentIndex": [-1, 0],
            "nodeValue": [-1, s(VALUE)],
        }
    }

    assert _leaks(s, doc) == ["option text"]


def test_an_element_in_an_option_is_not_option_text_even_with_a_value() -> None:
    s = Strings()
    doc = {
        "nodes": {
            "nodeName": [s("OPTION"), s("SPAN"), s("#text")],
            # The third node has no type at all.
            "nodeType": [1, 1],
            "parentIndex": [-1, 0, 0],
            "nodeValue": [-1, s(VALUE), s(VALUE)],
        }
    }

    assert _leaks(s, doc) == []


def test_a_text_node_past_the_end_of_the_parent_links_is_in_no_option() -> None:
    s = Strings()
    doc = {
        "nodes": {
            "nodeName": [s("DIV"), s("OPTION"), s("#text")],
            "nodeType": [1, 1, 3],
            "parentIndex": [-1, 0],
            "nodeValue": [-1, -1, s(VALUE)],
        }
    }

    assert _leaks(s, doc) == []


def test_a_parent_link_past_the_node_names_names_nothing() -> None:
    s = Strings()
    doc = {
        "nodes": {
            "nodeName": [s("OPTION"), s("#text")],
            "nodeType": [1, 3],
            "parentIndex": [-1, 2],
            "nodeValue": [-1, s(VALUE)],
        }
    }

    assert _leaks(s, doc) == []
