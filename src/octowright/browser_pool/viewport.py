# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ViewportMode(StrEnum):
    FLUID = "fluid"
    FIXED = "fixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ViewportInfo:
    mode: ViewportMode
    width: int | None = None
    height: int | None = None

    def to_recording(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"mode": self.mode.value}
        if self.mode == ViewportMode.FIXED and self.width is not None and self.height is not None:
            payload.update({"w": self.width, "h": self.height})
        return payload

    def to_wire(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "width": self.width,
            "height": self.height,
            "fixed": self.mode == ViewportMode.FIXED,
            "fluid": self.mode == ViewportMode.FLUID,
        }
