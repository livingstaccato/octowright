# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A launch option octowright will not read must be refused, not dropped.

`LaunchOptions.from_mapping` read every key by name, so anything it did not
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
left `engine_health` reporting chromium broken. It subclasses `ValueError` (pinned
in `tests/test_engine_health.py`), so `POST /api/sessions` turns this into a 400
naming the key instead of a launch that ignores half the body.

The refusal is also what lets `from_mapping` splat: with nothing unknown left, it
constructs with `cls(**options)` instead of hand-writing 28 `options.get("...")`
calls that restated defaults the dataclass already declares. So "accepted" and
"read" are now the same fact, and the drift guard this file used to carry -- a
regex scrape of `from_mapping`'s own source -- has nothing left to guard.
"""

from __future__ import annotations

import pytest

from octowright.browser_pool.options import CALLER_SETTABLE_FIELDS, LaunchOptions
from octowright.request_errors import InvalidRequestError


class TestTheFootgun:
    def test_the_message_names_every_unknown_key_and_the_fix(self) -> None:
        """The regression: `headless` used to return a headed browser.

        Reporting only the first unknown key would cost a caller one round trip
        per mistake, and naming `headless` alone is not enough -- the sense is
        inverted, which is its own trap.
        """
        with pytest.raises(InvalidRequestError) as excinfo:
            LaunchOptions.from_mapping({"kind": "chromium", "headless": True, "nonsense": 1})
        message = str(excinfo.value)
        assert "headless" in message
        assert "nonsense" in message
        assert "headed" in message
        assert "inverted" in message


class TestAcceptedSet:
    def test_an_output_field_is_not_caller_settable(self) -> None:
        """`protected_reason` is written by resolve_protected, never supplied."""
        assert "protected_reason" not in CALLER_SETTABLE_FIELDS
        with pytest.raises(InvalidRequestError, match="protected_reason"):
            LaunchOptions.from_mapping({"protected_reason": "headed_default"})

    def test_the_pool_kwargs_round_trip_is_LOSSLESS(self) -> None:
        """to_pool_kwargs -> pool.launch -> from_mapping must lose nothing.

        Every internal caller (relaunch, roster, driver_relaunch, scenarios, the
        recording replay route) reaches `pool.launch` this way. The earlier
        version of this test asserted only that the round trip was *accepted*,
        and that is precisely how `base_url` hid: `to_pool_kwargs` named 27 keys
        and omitted it, while `from_mapping` reads it and `launch_execution`
        consumes it -- the same silent drop as `headless`, in the direction a
        check on incoming keys cannot see. Both sides now derive from
        CALLER_SETTABLE_FIELDS, so equality is the honest assertion.
        """
        options = LaunchOptions(
            kind="firefox",
            url="about:blank",
            base_url="https://dev.example",
            label="round-trip",
            har=True,
        )
        assert LaunchOptions.from_mapping(options.to_pool_kwargs()) == options

    def test_base_url_specifically_survives_the_pool_hop(self) -> None:
        """Named on its own because it is the field that was lost."""
        options = LaunchOptions(kind="chromium", base_url="https://dev.example")
        assert LaunchOptions.from_mapping(options.to_pool_kwargs()).base_url == "https://dev.example"

    def test_an_output_field_is_not_transported_either(self) -> None:
        """`protected_reason` is the one exclusion, and it is the same on both sides."""
        assert "protected_reason" not in LaunchOptions(kind="chromium").to_pool_kwargs()

    def test_every_accepted_key_actually_constructs(self) -> None:
        """The accepted set and the constructor cannot disagree.

        This replaces a regex scrape of `from_mapping`'s source. Since the
        construction is now a splat, the only way the two could diverge is a
        field that cannot take its own default -- which this catches by
        building from the set itself.
        """
        defaults = {f: getattr(LaunchOptions(), f) for f in CALLER_SETTABLE_FIELDS}
        assert LaunchOptions.from_mapping(defaults) == LaunchOptions()
