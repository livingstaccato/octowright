# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Page-visible metrics must remain finite numbers or unavailable values."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from tests.test_emulation_primitives import _session
from tests.test_scroll_host_lifecycle import _install, _Observer, _scroll_records

pytestmark = pytest.mark.anyio

_METRICS = {"x": 0, "y": 300, "height": 4000, "innerHeight": 600}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _deliver_metrics(tmp_path, monkeypatch, raw):
    session = _session(tmp_path)
    observer = _Observer()
    observer.release.set()
    _install(session, monkeypatch, observer)

    async def metrics(expression):
        return raw

    monkeypatch.setattr(session.page, "evaluate", metrics)
    result = await session.scroll(delta_x=0, delta_y=300)
    assert result["ok"] is True
    assert observer.events.count("delivered") == 1
    assert len(_scroll_records(session)) == 1
    return result


@pytest.mark.parametrize("key", ["x", "y", "height", "innerHeight"])
@pytest.mark.parametrize(
    "invalid",
    ["injected " * 2000, {"instruction": "untrusted"}, True, False, float("inf"), float("-inf"), float("nan"), None],
    ids=["text", "object", "true", "false", "infinity", "negative_infinity", "nan", "null"],
)
async def test_invalid_metric_is_null_without_discarding_available_numbers(tmp_path, monkeypatch, key, invalid):
    raw = {**_METRICS, key: invalid}
    result = await _deliver_metrics(tmp_path, monkeypatch, raw)
    assert result["settled"] is False
    assert result[key] is None
    for other in _METRICS.keys() - {key}:
        assert result[other] == _METRICS[other]


@pytest.mark.parametrize("missing", ["x", "y", "height", "innerHeight"])
async def test_missing_metric_is_null_and_unsettled(tmp_path, monkeypatch, missing):
    raw = {key: value for key, value in _METRICS.items() if key != missing}
    result = await _deliver_metrics(tmp_path, monkeypatch, raw)
    assert result[missing] is None
    assert result["settled"] is False


@pytest.mark.parametrize("raw", [None, [], "instructions", True, 42])
async def test_non_mapping_metrics_return_all_null_without_failing_delivered_input(tmp_path, monkeypatch, raw):
    result = await _deliver_metrics(tmp_path, monkeypatch, raw)
    assert result["settled"] is False
    assert all(result[key] is None for key in _METRICS)


@pytest.mark.parametrize(
    "value", [0, -12, 2.5, -0.0, 10**1000], ids=["zero", "negative", "fraction", "negative_zero", "large_integer"]
)
async def test_finite_numeric_metrics_preserve_value_and_type(tmp_path, monkeypatch, value):
    result = await _deliver_metrics(tmp_path, monkeypatch, {key: value for key in _METRICS})
    assert result["settled"] is True
    for key in _METRICS:
        assert result[key] == value
        assert type(result[key]) is type(value)


async def test_extra_mapping_fields_cannot_override_delivery_or_expand_response(tmp_path, monkeypatch):
    raw = MappingProxyType({**_METRICS, "ok": False, "settled": False, "delta_y": 999, "instructions": "untrusted"})
    result = await _deliver_metrics(tmp_path, monkeypatch, raw)
    assert result == {"ok": True, "settled": True, "delta_x": 0, "delta_y": 300, **_METRICS}
