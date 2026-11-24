# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The new emulation actions must export valid code, not silently become widgets.

These pin the actual Python/TypeScript handler output of each new
session action: wheel deltas and optional locator targeting, offline toggles,
network abort reasons, and CDP safe-area insets all survive the trip, with
correct values attached to the correct page/context call.
"""

from __future__ import annotations

import ast
import textwrap

from octowright import export as py_export
from octowright import export_ts as ts_export

_ACTIONS = {
    "scroll": {"delta_x": 12, "delta_y": 480, "selector": "#feed"},
    "set_offline": {"offline": True},
    "abort_route": {"pattern": "**/api/refused", "error_code": "connectionrefused"},
    "set_safe_area": {"top": 0, "right": 0, "bottom": 34, "left": 0},
}


def test_python_emulation_handlers_generate_valid_calls() -> None:
    for kind, action in _ACTIONS.items():
        emitted = py_export._PY_HANDLERS[kind](action)
        assert emitted is not None
        ast.parse(textwrap.dedent(emitted))


def test_python_safe_area_passes_every_edge() -> None:
    emitted = py_export._PY_HANDLERS["set_safe_area"]({"bottom": 34, "top": 46})
    assert emitted is not None
    ast.parse(textwrap.dedent(emitted))
    assert "Emulation.setSafeAreaInsets" in emitted
    assert "'bottom': 34" in emitted
    assert "'top': 46" in emitted
    assert "'left': 0" in emitted


def test_python_abort_passes_the_refusal_reason_verbatim() -> None:
    emitted = py_export._PY_HANDLERS["abort_route"]({"pattern": "**/api/refused", "error_code": "connectionreset"})
    assert emitted is not None
    ast.parse(textwrap.dedent(emitted))
    assert "route.abort('connectionreset')" in emitted
    assert "'**/api/refused'" in emitted


def test_python_scroll_keeps_the_optional_locator_optional() -> None:
    both = py_export._PY_HANDLERS["scroll"]({"delta_x": 4, "delta_y": 250, "selector": "#feed"})
    wheel_only = py_export._PY_HANDLERS["scroll"]({"delta_x": 4, "delta_y": 250})
    assert both is not None and wheel_only is not None
    ast.parse(textwrap.dedent(both))
    ast.parse(textwrap.dedent(wheel_only))
    assert "#feed" in both and "scroll_into_view_if_needed" in both
    assert "scroll_into_view_if_needed" not in wheel_only
    assert "page.mouse.wheel(4, 250)" in both and "page.mouse.wheel(4, 250)" in wheel_only


def test_ts_emulation_handlers_emit_the_right_api_shapes() -> None:
    for kind, action in _ACTIONS.items():
        emitted = ts_export._TS_HANDLERS[kind](action)
        assert "throw new Error" not in emitted
    fulfill = ts_export._TS_HANDLERS["abort_route"]({"pattern": "**/api/refused", "error_code": "connectionrefused"})
    assert fulfill is not None
    assert "route.abort(" in fulfill and '"connectionrefused"' in fulfill
    safe = ts_export._TS_HANDLERS["set_safe_area"]({"bottom": 34})
    assert safe is not None
    assert "newCDPSession" in safe and "Emulation.setSafeAreaInsets" in safe
    offline = ts_export._TS_HANDLERS["set_offline"]({"offline": False})
    assert offline is not None
    assert "setOffline" in offline and "false" in offline
