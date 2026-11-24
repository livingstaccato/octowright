# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Closed, text-free interaction rows shared by capture and disk readers."""

from __future__ import annotations

import math
import re
from typing import Any

PAYLOAD_FIELDS = frozenset(
    ["v", "phase", "pointer_type", "pointer_id", "button", "x", "y", "width", "height", "at", "shown"]
)
VIDEO = re.compile(r"[A-Za-z0-9_@-]{1,200}\.(?:webm|mp4)")


def number(value: Any, low: float, high: float) -> bool:
    return type(value) in (int, float) and low <= value <= high and math.isfinite(value)


def integer(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def valid_payload(value: Any) -> bool:
    if not isinstance(value, dict) or not PAYLOAD_FIELDS <= value.keys() <= PAYLOAD_FIELDS | {"dx", "dy"}:
        return False
    enums = (
        integer(value["v"], 1, 1),
        value["phase"] in ("down", "move", "up", "cancel", "double", "wheel"),
        value["pointer_type"] in ("mouse", "touch", "pen"),
        integer(value["pointer_id"], 0, 2**31 - 1),
        integer(value["button"], 0, 2),
        type(value["shown"]) is bool,
    )
    bounds = {
        "x": (-1e6, 1e6),
        "y": (-1e6, 1e6),
        "width": (1, 1e5),
        "height": (1, 1e5),
        "at": (1e12, 4e12),
        "dx": (-1e6, 1e6),
        "dy": (-1e6, 1e6),
    }
    return all(enums) and all(number(value[k], *b) for k, b in bounds.items() if k in value)


def valid_identity(value: dict[str, Any]) -> bool:
    video = value.get("video")
    return integer(value.get("page_id"), 1, 2**53 - 1) and (
        video is None or (isinstance(video, str) and VIDEO.fullmatch(video) is not None)
    )


def valid_geometry(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or value.keys() != {
        "engine",
        "viewport_width",
        "viewport_height",
        "device_scale_factor",
    }:
        return False
    return (
        value["engine"] in ("chromium", "firefox", "webkit")
        and number(value["viewport_width"], 1, 1e5)
        and number(value["viewport_height"], 1, 1e5)
        and number(value["device_scale_factor"], 0.1, 10)
    )


def _valid_settings(row: dict[str, Any]) -> bool:
    return row.keys() == {"type", "v", "show", "record", "reset", "at"} and all(
        type(row[k]) is bool for k in ("show", "record", "reset")
    )


def _valid_page(row: dict[str, Any]) -> bool:
    return (
        row.keys() == {"type", "v", "page_id", "video", "time_origin", "time_origin_kind", "video_geometry"}
        and valid_identity(row)
        and number(row["time_origin"], 1e12, 4e12)
        and row["time_origin_kind"] == "estimate"
        and valid_geometry(row["video_geometry"])
    )


def _valid_interaction(row: dict[str, Any]) -> bool:
    identity = {"type", "page_id", "video", "t"}
    return (
        identity <= row.keys()
        and valid_identity(row)
        and number(row["t"], 0, 3e12)
        and valid_payload({k: v for k, v in row.items() if k not in identity})
    )


def _valid_reset(row: dict[str, Any]) -> bool:
    if row.get("reset") is not True:
        return False
    if row.get("reason") == "loss":
        return row.keys() == {"type", "v", "reset", "reason", "at"}
    return (
        row.keys() == {"type", "v", "reset", "reason", "page_id", "at"}
        and row["reason"] == "page_closed"
        and integer(row["page_id"], 1, 2**53 - 1)
    )


def valid_row(row: Any) -> bool:
    if not isinstance(row, dict) or not integer(row.get("v"), 1, 1):
        return False
    kind = row.get("type")
    if kind in ("settings", "reset", "truncated") and not number(row.get("at"), 1e12, 4e12):
        return False
    if kind == "truncated":
        return row.keys() == {"type", "v", "reason", "at"} and row["reason"] == "capacity"
    if kind == "reset":
        return _valid_reset(row)
    validators = {"page": _valid_page, "interaction": _valid_interaction, "settings": _valid_settings}
    return isinstance(kind, str) and kind in validators and validators[kind](row)
