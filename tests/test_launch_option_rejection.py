# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A launch option octowright will not read must be refused, not dropped.

`LaunchOptions.from_mapping` reads every key by name, so anything it did not
recognise was silently discarded while the caller went on believing the option
had taken effect. The one that bit is `headless`: it is Playwright's OWN
parameter name and therefore the natural guess, so

    await pool.launch(kind="chromium", headless=True, url="about:blank")

launched a **headed** browser. It was found the expensive way -- a crash probe
written to exercise `chrome-headless-shell` drove headed Chrome instead, and the
whole run had to be thrown away and repeated.

Refusing is the repository's established answer to a flag the caller believes
took effect: `serve --wait-ready` rejects `--no-singleton` rather than quietly
ignoring it. `InvalidRequestError` specifically, because a caller's mistake must
never be filed as an engine fault -- the defect issue #214 fixed, where a bad URL
left `engine_health` reporting chromium broken. It subclasses `ValueError`, so
`POST /api/sessions` turns this into a 400 naming the key instead of a launch
that ignores half the body.

The accepted set is derived from the dataclass rather than listed, and pinned
here against the keys `from_mapping` actually reads, so an accept-list nobody
updated can neither reject a new field nor accept one that goes nowhere.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from octowright.browser_pool.options import LaunchOptions
from octowright.request_errors import InvalidRequestError

_SOURCE = Path(__file__).resolve().parents[1] / "src" / "octowright" / "browser_pool" / "options.py"


def _keys_from_mapping_reads() -> set[str]:
    """Every ``options.get("...")`` inside ``from_mapping``'s body."""
    body = _SOURCE.read_text(encoding="utf-8").split("def from_mapping", 1)[1]
    body = body.split("launch_options.validate()", 1)[0]
    return set(re.findall(r'options\.get\(\s*"([a-z_]+)"', body))


class TestTheFootgun:
    def test_headless_is_refused_rather_than_dropped(self) -> None:
        """The regression: this used to return a headed browser."""
        with pytest.raises(InvalidRequestError) as excinfo:
            LaunchOptions.from_mapping({"kind": "chromium", "headless": True})
        assert "headless" in str(excinfo.value)

    def test_the_message_names_the_option_to_use_instead(self) -> None:
        """Naming the key is not enough -- the sense is inverted, which is its own trap."""
        with pytest.raises(InvalidRequestError) as excinfo:
            LaunchOptions.from_mapping({"headless": True})
        message = str(excinfo.value)
        assert "headed" in message
        assert "inverted" in message

    def test_it_is_a_value_error_so_the_http_route_answers_400(self) -> None:
        """`POST /api/sessions` catches ValueError; a new type would 500 instead."""
        assert issubclass(InvalidRequestError, ValueError)

    def test_an_arbitrary_unknown_option_is_refused_too(self) -> None:
        with pytest.raises(InvalidRequestError, match="nonsense"):
            LaunchOptions.from_mapping({"kind": "chromium", "nonsense": 1})

    def test_every_unknown_key_is_reported_not_just_the_first(self) -> None:
        """A caller fixing one key at a time pays a round trip per key."""
        with pytest.raises(InvalidRequestError) as excinfo:
            LaunchOptions.from_mapping({"headless": True, "nonsense": 1})
        message = str(excinfo.value)
        assert "headless" in message
        assert "nonsense" in message


class TestAcceptedSet:
    def test_the_derived_set_matches_what_from_mapping_reads(self) -> None:
        """The drift guard.

        A field accepted but never read is the original bug wearing a different
        hat; a field read but rejected breaks a legitimate caller.
        """
        assert LaunchOptions.caller_settable_fields() == _keys_from_mapping_reads()

    def test_an_output_field_is_not_caller_settable(self) -> None:
        """`protected_reason` is written by resolve_protected, never supplied."""
        assert "protected_reason" not in LaunchOptions.caller_settable_fields()
        with pytest.raises(InvalidRequestError, match="protected_reason"):
            LaunchOptions.from_mapping({"protected_reason": "headed_default"})

    def test_the_pool_kwargs_round_trip_is_accepted(self) -> None:
        """to_pool_kwargs -> pool.launch -> from_mapping is the internal path.

        Every internal caller (relaunch, roster, driver_relaunch, scenarios,
        the recording replay route) reaches `pool.launch` this way, so a check
        that rejected this shape would break all of them at once.
        """
        round_tripped = LaunchOptions(kind="chromium", url="about:blank").to_pool_kwargs()
        assert LaunchOptions.from_mapping(round_tripped).url == "about:blank"

    def test_an_empty_mapping_is_still_accepted(self) -> None:
        """Every field has a default; supplying nothing is a legitimate launch."""
        assert LaunchOptions.from_mapping({}).kind == "chromium"
