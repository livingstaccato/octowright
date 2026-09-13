# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The rendered-surface scan that decides whether a redacted screenshot may be kept."""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.rendered_surface import SNAPSHOT_PARAMS, rendered_leaks, spelled_by_pieces

SECRET = "Surface-Canary-5d1e"  # pragma: allowlist secret


def _snapshot(strings: list[str], *documents: dict[str, Any]) -> dict[str, Any]:
    return {"strings": strings, "documents": list(documents)}


def _document(
    *,
    names: list[int] | None = None,
    types: list[int] | None = None,
    parents: list[int] | None = None,
    node_values: list[int] | None = None,
    attributes: list[list[int]] | None = None,
    layout_nodes: list[int] | None = None,
    layout_styles: list[list[int]] | None = None,
    layout_text: list[int] | None = None,
    text_boxes: list[tuple[int, list[float], int, int]] | None = None,
    **rare: dict[str, list[int]],
) -> dict[str, Any]:
    nodes: dict[str, Any] = {
        "nodeName": names or [],
        "nodeType": types or [],
        "parentIndex": parents or [],
        "nodeValue": node_values or [],
        "attributes": attributes or [],
    }
    nodes.update(rare)
    layout = {"nodeIndex": layout_nodes or [], "styles": layout_styles or [], "text": layout_text or []}
    boxes = list(zip(*text_boxes, strict=True)) if text_boxes else [(), (), (), ()]
    text_box_columns = {
        key: list(column) for key, column in zip(("layoutIndex", "bounds", "start", "length"), boxes, strict=True)
    }
    return {"nodes": nodes, "layout": layout, "textBoxes": text_box_columns}


def _line(*texts: str, top: float = 0.0, lefts: list[float] | None = None) -> list[tuple[int, list[float], int, int]]:
    """Text boxes for layout texts ``0..n`` laid out on one line at the given left edges."""
    positions = lefts if lefts is not None else [index * 100.0 for index in range(len(texts))]
    return [(index, [positions[index], top, 90.0, 40.0], 0, len(text)) for index, text in enumerate(texts)]


def test_a_page_without_the_value_is_clean() -> None:
    strings = ["P", "signed in", "visible"]
    doc = _document(names=[0], types=[1], parents=[-1], layout_nodes=[0], layout_styles=[[2]], layout_text=[1])
    assert rendered_leaks(_snapshot(strings, doc), [SECRET]) == []


def test_layout_text_matches_across_case_and_whitespace() -> None:
    strings = [f"  {SECRET.upper()[:6]}\n {SECRET.upper()[6:]} "]
    assert rendered_leaks(_snapshot(strings, _document(layout_text=[0])), [SECRET]) == ["rendered text"]


def test_a_value_split_across_layout_objects_still_matches() -> None:
    strings = [SECRET[:8], SECRET[8:]]
    assert rendered_leaks(_snapshot(strings, _document(layout_text=[0, -1, 1])), [SECRET]) == ["rendered text"]


def _control(name: str, kind: str | None, visibility: str, security: str, key: str = "inputValue") -> dict[str, Any]:
    """One form control holding the value, with its type attribute, visibility and text security."""
    strings = [SECRET, name, "type", kind or "", visibility, security]
    attributes = [[2, 3]] if kind is not None else [[]]
    doc = _document(
        names=[1],
        types=[1],
        parents=[-1],
        attributes=attributes,
        layout_nodes=[0],
        layout_styles=[[4, 5]],
        **{key: {"index": [0], "value": [0]}},
    )
    return _snapshot(strings, doc)


@pytest.mark.parametrize(
    ("name", "kind", "key"),
    [
        ("INPUT", None, "inputValue"),
        ("INPUT", "email", "inputValue"),
        ("INPUT", "TeL", "inputValue"),
        ("INPUT", "no-such-type", "inputValue"),
        ("TEXTAREA", None, "textValue"),
    ],
)
def test_a_drawn_form_value_is_refused_unless_its_text_is_masked(name: str, kind: str | None, key: str) -> None:
    assert rendered_leaks(_control(name, kind, "visible", "none", key), [SECRET]) == ["form value"]
    assert rendered_leaks(_control(name, kind, "visible", "disc", key), [SECRET]) == []
    assert rendered_leaks(_control(name, kind, "hidden", "none", key), [SECRET]) == []


@pytest.mark.parametrize("kind", ["button", "submit", "reset", "hidden", "checkbox"])
def test_masking_does_not_clear_a_control_that_does_not_draw_masked_text(kind: str) -> None:
    assert rendered_leaks(_control("INPUT", kind, "visible", "disc"), [SECRET]) == ["form value"]


def test_a_masked_select_is_not_a_masked_text_control() -> None:
    assert rendered_leaks(_control("SELECT", None, "visible", "disc"), [SECRET]) == ["form value"]


def test_a_form_value_without_a_layout_object_is_not_drawn() -> None:
    doc = _document(names=[0], types=[1], parents=[-1], inputValue={"index": [0], "value": [1]})
    assert rendered_leaks(_snapshot(["INPUT", SECRET], doc), [SECRET]) == []


def test_option_text_is_part_of_the_rendered_surface() -> None:
    strings = ["OPTION", "#text", SECRET]
    doc = _document(names=[0, 1], types=[1, 3], parents=[-1, 0], node_values=[-1, 2])
    assert rendered_leaks(_snapshot(strings, doc), [SECRET]) == ["option text"]


def test_text_outside_an_option_is_judged_by_layout_not_by_the_dom() -> None:
    strings = ["DIV", "#text", SECRET]
    doc = _document(names=[0, 1], types=[1, 3], parents=[-1, 0], node_values=[-1, 2])
    assert rendered_leaks(_snapshot(strings, doc), [SECRET]) == []


def test_a_visible_canvas_is_refused_and_a_hidden_one_is_not() -> None:
    strings = ["CANVAS", "visible", "hidden"]
    visible = _document(names=[0], types=[1], parents=[-1], layout_nodes=[0], layout_styles=[[1]])
    hidden = _document(names=[0], types=[1], parents=[-1], layout_nodes=[0], layout_styles=[[2]])
    unrendered = _document(names=[0], types=[1], parents=[-1])
    assert rendered_leaks(_snapshot(strings, visible), [SECRET]) == ["visible canvas"]
    assert rendered_leaks(_snapshot(strings, hidden), [SECRET]) == []
    assert rendered_leaks(_snapshot(strings, unrendered), [SECRET]) == []


def test_a_visible_resource_address_holding_the_value_is_refused() -> None:
    strings = ["IMG", "src", f"https://app.test/avatar?user={SECRET}", "visible", "hidden"]
    visible = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]], layout_nodes=[0], layout_styles=[[3]])
    hidden = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]], layout_nodes=[0], layout_styles=[[4]])
    assert rendered_leaks(_snapshot(strings, visible), [SECRET]) == ["visible resource address"]
    assert rendered_leaks(_snapshot(strings, hidden), [SECRET]) == []


def test_a_document_inside_a_hidden_frame_is_not_rendered() -> None:
    strings = ["IFRAME", "hidden", SECRET, "visible"]
    parent = _document(
        names=[0],
        types=[1],
        parents=[-1],
        layout_nodes=[0],
        layout_styles=[[1]],
        contentDocumentIndex={"index": [0], "value": [1]},
    )
    child = _document(layout_text=[2])
    assert rendered_leaks(_snapshot(strings, parent, child), [SECRET]) == []

    shown = _document(
        names=[0],
        types=[1],
        parents=[-1],
        layout_nodes=[0],
        layout_styles=[[3]],
        contentDocumentIndex={"index": [0], "value": [1]},
    )
    assert rendered_leaks(_snapshot(strings, shown, child), [SECRET]) == ["rendered text", "visible iframe"]


def test_a_frame_hidden_anywhere_above_hides_every_document_below_it() -> None:
    strings = ["IFRAME", "hidden", "visible", SECRET]
    top = _document(
        names=[0],
        types=[1],
        parents=[-1],
        layout_nodes=[0],
        layout_styles=[[1]],
        contentDocumentIndex={"index": [0], "value": [1]},
    )
    middle = _document(
        names=[0],
        types=[1],
        parents=[-1],
        layout_nodes=[0],
        layout_styles=[[2]],
        contentDocumentIndex={"index": [0], "value": [2]},
    )
    bottom = _document(layout_text=[3])
    assert rendered_leaks(_snapshot(strings, top, middle, bottom), [SECRET]) == []


def test_reasons_never_carry_the_value_and_junk_values_are_ignored() -> None:
    strings = [SECRET]
    reasons = rendered_leaks(_snapshot(strings, _document(layout_text=[0])), [SECRET, "", "   ", None])  # type: ignore[list-item]
    assert reasons == ["rendered text"]
    assert all(SECRET.casefold() not in reason.casefold() for reason in reasons)
    assert rendered_leaks(_snapshot(strings, _document(layout_text=[0])), ["", "  "]) == []


def test_out_of_range_string_indexes_are_treated_as_empty() -> None:
    doc = _document(names=[7], types=[3], parents=[9], node_values=[8], layout_text=[5, -1])
    assert rendered_leaks(_snapshot([], doc), [SECRET]) == []


def test_visibility_then_text_security_are_the_first_computed_styles_read() -> None:
    assert SNAPSHOT_PARAMS["computedStyles"][:2] == ["visibility", "-webkit-text-security"]
    assert "content" in SNAPSHOT_PARAMS["computedStyles"][2:]


@pytest.mark.parametrize(
    ("shown", "value"),
    [
        (SECRET[:7] + "\u200b" + SECRET[7:], SECRET),
        (SECRET[:7] + "\u00ad" + SECRET[7:], SECRET),
        (SECRET[:7] + "\u2060" + SECRET[7:], SECRET),
        ("".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in SECRET), SECRET),
        ("Jose\u0301-Surface-Name", "Jos\u00e9-Surface-Name"),
        (SECRET[::-1], SECRET),
    ],
    ids=["zero-width-space", "soft-hyphen", "word-joiner", "full-width", "decomposed", "reversed"],
)
def test_a_value_spelled_invisibly_differently_or_backwards_still_matches(shown: str, value: str) -> None:
    assert rendered_leaks(_snapshot([shown], _document(layout_text=[0])), [value]) == ["rendered text"]


def test_parts_reordered_on_one_line_match_in_visual_order() -> None:
    strings = [SECRET[8:], SECRET[:8]]
    same_line = _document(layout_text=[0, 1], text_boxes=_line(*strings, lefts=[300.0, 0.0]))
    assert rendered_leaks(_snapshot(strings, same_line), [SECRET]) == ["rendered text"]

    boxes = [(0, [300.0, 0.0, 90.0, 40.0], 0, len(strings[0])), (1, [0.0, 120.0, 90.0, 40.0], 0, len(strings[1]))]
    other_lines = _document(layout_text=[0, 1], text_boxes=boxes)
    assert rendered_leaks(_snapshot(strings, other_lines), [SECRET]) == []


def test_a_few_unrelated_boxes_between_parts_still_match() -> None:
    strings = [SECRET[:8], "at", "x", SECRET[8:]]
    gapped = _document(layout_text=[0, 1, 2, 3], text_boxes=_line(*strings, top=10.0))
    assert rendered_leaks(_snapshot(strings, gapped), [SECRET]) == ["rendered text"]

    fillers = ["a", "b", "c", "d", "e"]
    too_far = [SECRET[:8], *fillers, SECRET[8:]]
    far = _document(
        layout_text=list(range(len(too_far))),
        text_boxes=[(i, [0.0, i * 50.0, 9.0, 40.0], 0, len(t)) for i, t in enumerate(too_far)],
    )
    assert rendered_leaks(_snapshot(too_far, far), [SECRET]) == []


def test_a_short_value_matches_only_contiguously() -> None:
    strings = ["B", "x", "o"]
    doc = _document(layout_text=[0, 1, 2], text_boxes=_line(*strings))
    assert rendered_leaks(_snapshot(strings, doc), ["Bo"]) == []
    assert rendered_leaks(_snapshot(["B", "o"], _document(layout_text=[0, 1], text_boxes=_line("B", "o"))), ["Bo"]) == [
        "rendered text"
    ]


@pytest.mark.parametrize(
    ("pieces", "gap", "expected"),
    [
        (["xxsurf", "ace"], 0, True),
        (["xxsurf", "q", "ace"], 0, False),
        (["xxsurf", "q", "ace"], 1, True),
        (["surf", "ac", "e"], 0, True),
        (["surf", "acx", "e"], 4, False),
        (["surf", "acezzz"], 0, True),
        (["", "surface", ""], 0, True),
        (["surf", "", "ace"], 0, True),
        (["ace", "surf"], 4, False),
        ([], 4, False),
    ],
)
def test_pieces_spell_a_value_only_in_order_and_within_the_gap(pieces: list[str], gap: int, expected: bool) -> None:
    assert spelled_by_pieces("surface", pieces, gap) is expected


@pytest.mark.parametrize("attribute", ["placeholder", "alt", "label"])
def test_drawn_attribute_text_is_refused_unless_hidden(attribute: str) -> None:
    strings = ["INPUT", attribute, SECRET, "visible", "hidden"]
    shown = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]], layout_nodes=[0], layout_styles=[[3]])
    hidden = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]], layout_nodes=[0], layout_styles=[[4]])
    without_layout = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]])
    assert rendered_leaks(_snapshot(strings, shown), [SECRET]) == ["visible attribute text"]
    assert rendered_leaks(_snapshot(strings, hidden), [SECRET]) == []
    assert rendered_leaks(_snapshot(strings, without_layout), [SECRET]) == ["visible attribute text"]


def test_an_attribute_the_browser_does_not_draw_is_not_refused() -> None:
    strings = ["DIV", "data-owner", SECRET, "visible"]
    doc = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]], layout_nodes=[0], layout_styles=[[3]])
    assert rendered_leaks(_snapshot(strings, doc), [SECRET]) == []


def test_a_picture_source_holding_the_value_is_refused_while_its_image_shows() -> None:
    strings = ["PICTURE", "SOURCE", "IMG", "srcset", f"data:image/svg+xml,{SECRET}", "visible", "hidden", "DIV"]

    def picture(owner: int, image_style: int) -> dict[str, Any]:
        return _document(
            names=[owner, 1, 2],
            types=[1, 1, 1],
            parents=[-1, 0, 0],
            attributes=[[], [3, 4], []],
            layout_nodes=[0, 2],
            layout_styles=[[5], [image_style]],
        )

    assert rendered_leaks(_snapshot(strings, picture(0, 5)), [SECRET]) == ["visible resource address"]
    assert rendered_leaks(_snapshot(strings, picture(0, 6)), [SECRET]) == []
    assert rendered_leaks(_snapshot(strings, picture(7, 5)), [SECRET]) == []


@pytest.mark.parametrize("position", range(2, len(SNAPSHOT_PARAMS["computedStyles"])))
def test_an_image_style_holding_the_value_is_refused_while_shown(position: int) -> None:
    strings = ["DIV", "visible", "hidden", "none", f'url("data:image/svg+xml,{SECRET}")']
    styles = [3] * len(SNAPSHOT_PARAMS["computedStyles"])
    styles[position] = 4
    shown = _document(names=[0], types=[1], parents=[-1], layout_nodes=[0], layout_styles=[[1, *styles[1:]]])
    hidden = _document(names=[0], types=[1], parents=[-1], layout_nodes=[0], layout_styles=[[2, *styles[1:]]])
    assert rendered_leaks(_snapshot(strings, shown), [SECRET]) == ["visible style image"]
    assert rendered_leaks(_snapshot(strings, hidden), [SECRET]) == []


def test_text_security_is_not_an_image_style() -> None:
    strings = ["DIV", "visible", f"data:image/svg+xml,{SECRET}", "none"]
    doc = _document(names=[0], types=[1], parents=[-1], layout_nodes=[0], layout_styles=[[1, 2, 3]])
    assert rendered_leaks(_snapshot(strings, doc), [SECRET]) == []


@pytest.mark.parametrize(("element", "attribute"), [("IMAGE", "href"), ("USE", "xlink:href"), ("IMAGE", "xlink:href")])
def test_an_svg_image_link_holding_the_value_is_a_resource_address(element: str, attribute: str) -> None:
    strings = [element, attribute, f"data:image/svg+xml,{SECRET}", "visible", "hidden", "A", "href"]
    shown = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]], layout_nodes=[0], layout_styles=[[3]])
    hidden = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]], layout_nodes=[0], layout_styles=[[4]])
    anchor = _document(names=[5], types=[1], parents=[-1], attributes=[[6, 2]], layout_nodes=[0], layout_styles=[[3]])
    assert rendered_leaks(_snapshot(strings, shown), [SECRET]) == ["visible resource address"]
    assert rendered_leaks(_snapshot(strings, hidden), [SECRET]) == []
    assert rendered_leaks(_snapshot(strings, anchor), [SECRET]) == []


def test_a_filter_image_link_holding_the_value_is_refused_without_a_layout_object() -> None:
    strings = ["FEIMAGE", "href", f"data:image/svg+xml,{SECRET}"]
    doc = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]])
    assert rendered_leaks(_snapshot(strings, doc), [SECRET]) == ["visible resource address"]


def test_a_picture_source_with_its_own_layout_object_is_judged_by_the_picture_image() -> None:
    """Chrome gives ``<source>`` a layout object; only the picture's image decides."""
    strings = ["PICTURE", "SOURCE", "IMG", "srcset", f"data:image/svg+xml,{SECRET}", "visible", "hidden"]

    def picture(image_style: int) -> dict[str, Any]:
        return _document(
            names=[0, 1, 2],
            types=[1, 1, 1],
            parents=[-1, 0, 0],
            attributes=[[], [3, 4], []],
            layout_nodes=[0, 1, 2],
            layout_styles=[[5], [5], [image_style]],
        )

    assert rendered_leaks(_snapshot(strings, picture(5)), [SECRET]) == ["visible resource address"]
    assert rendered_leaks(_snapshot(strings, picture(6)), [SECRET]) == []


@pytest.mark.parametrize(
    "shown",
    ["+1 (555) 013-7788", "(555) 013-7788", "013-7788", "tel:+15550137788", "5550137788"[::-1]],
    ids=["international", "national", "local", "link-text", "reversed"],
)
def test_a_numeric_value_matches_by_its_digits(shown: str) -> None:
    assert rendered_leaks(_snapshot([shown], _document(layout_text=[0])), ["+15550137788"]) == ["rendered text"]


def test_digits_split_across_boxes_match_and_too_few_digits_do_not() -> None:
    strings = ["(555) ", "013-", "7788"]
    split = _document(layout_text=[0, 1, 2], text_boxes=_line(*strings))
    assert rendered_leaks(_snapshot(strings, split), ["+15550137788"]) == ["rendered text"]
    assert rendered_leaks(_snapshot(["37788 and 013"], _document(layout_text=[0])), ["+15550137788"]) == []
    assert rendered_leaks(_control("INPUT", "tel", "visible", "none"), ["x"]) == []


def test_a_frame_document_address_is_a_resource_address() -> None:
    strings = ["DIV", "srcdoc", f"<p>{SECRET}</p>", "visible"]
    doc = _document(names=[0], types=[1], parents=[-1], attributes=[[1, 2]], layout_nodes=[0], layout_styles=[[3]])
    assert rendered_leaks(_snapshot(strings, doc), [SECRET]) == ["visible resource address"]
