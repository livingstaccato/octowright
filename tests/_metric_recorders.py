# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Test doubles for OTel metric instruments.

Monkeypatch a real ``counter()`` / ``histogram()`` instrument with one of these
to assert it fired with the expected value + attributes, without standing up a
real meter/exporter."""

from __future__ import annotations

from typing import Any


class RecordingCounter:
    """Stands in for a provide.telemetry counter; captures ``.add()`` calls."""

    def __init__(self) -> None:
        self.adds: list[tuple[float, dict[str, Any]]] = []

    def add(self, value: float, attributes: dict[str, Any] | None = None, **_kwargs: Any) -> None:
        self.adds.append((value, dict(attributes or {})))

    def total(self) -> float:
        return sum(value for value, _ in self.adds)

    def attrs_for(self, key: str) -> list[Any]:
        """The values seen for attribute ``key`` across all recorded adds."""
        return [attrs.get(key) for _value, attrs in self.adds]


class RecordingHistogram:
    """Stands in for a provide.telemetry histogram; captures ``.record()`` calls."""

    def __init__(self) -> None:
        self.records: list[tuple[float, dict[str, Any]]] = []

    def record(self, value: float, attributes: dict[str, Any] | None = None, **_kwargs: Any) -> None:
        self.records.append((value, dict(attributes or {})))

    def values_for(self, key: str, match: Any) -> list[float]:
        """Recorded values whose attribute ``key`` equals ``match``."""
        return [value for value, attrs in self.records if attrs.get(key) == match]
