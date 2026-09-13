# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The rendered-surface scan that decides whether a redacted screenshot may be kept."""

from __future__ import annotations

from typing import Any

from octowright.macros.rendered_surface import rendered_leaks

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
    return {"nodes": nodes, "layout": layout}


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


def test_form_values_are_part_of_the_rendered_surface() -> None:
    strings = [SECRET]
    doc = _document(inputValue={"index": [0], "value": [0]})
    assert rendered_leaks(_snapshot(strings, doc), [SECRET]) == ["form value"]
    doc = _document(textValue={"index": [0], "value": [0]})
    assert rendered_leaks(_snapshot(strings, doc), [SECRET]) == ["form value"]


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
