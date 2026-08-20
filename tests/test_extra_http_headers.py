# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Launch-time extra HTTP headers (Playwright context ``extra_http_headers``).

Chosen over a route interceptor because it is the only layer that also covers
popups, new tabs and subresources, and because it was measured to apply to the
SSRF guard's own validation fetch as well -- so the hop the guard checks and
the hop the browser makes carry the same headers.
"""

from __future__ import annotations

import pytest

from octowright.browser_pool.launch_helpers import extra_http_headers_kwargs
from octowright.browser_pool.options import LaunchOptions
from octowright.http_headers import (
    MAX_EXTRA_HTTP_HEADERS,
    REDACTED_HEADER_PLACEHOLDER,
    is_credential_header,
    redact_header_values,
)
from octowright.session.core_interaction_mixin import _reject_redacted_headers


def test_headers_reach_the_pool_kwargs() -> None:
    opts = LaunchOptions(extra_http_headers={"X-Env": "staging"})

    assert opts.to_pool_kwargs()["extra_http_headers"] == {"X-Env": "staging"}


def test_a_launch_without_headers_passes_none_at_all() -> None:
    """Silent when there is nothing to say: an empty dict is not the same as
    the argument being absent, and every pre-existing launch must be untouched."""
    assert extra_http_headers_kwargs(None) == {}
    assert extra_http_headers_kwargs({}) == {}
    assert extra_http_headers_kwargs({"X-A": "1"}) == {"extra_http_headers": {"X-A": "1"}}


def test_the_context_kwargs_copy_the_mapping() -> None:
    """The context outlives the caller's dict; a later mutation must not
    retroactively change what the browser sends."""
    source = {"X-A": "1"}

    built = extra_http_headers_kwargs(source)
    source["X-B"] = "2"

    assert built["extra_http_headers"] == {"X-A": "1"}


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Bad": "value\r\nX-Injected: evil"},  # CR/LF ends the header, starts another
        {"X-Bad": "value\nX-Injected: evil"},
        {"X-Bad": "null\x00byte"},
        {"Bad Name": "v"},  # space is not an RFC 7230 token character
        {"Bad:Name": "v"},
        {"": "v"},
        {"X-Bad": 1},
        {"X-Bad": None},
    ],
)
def test_a_header_that_could_forge_a_request_is_refused(headers: dict) -> None:
    with pytest.raises(ValueError):
        LaunchOptions(extra_http_headers=headers).validate()


def test_the_map_is_bounded() -> None:
    too_many = {f"X-H{index}": "v" for index in range(MAX_EXTRA_HTTP_HEADERS + 1)}

    with pytest.raises(ValueError, match="at most"):
        LaunchOptions(extra_http_headers=too_many).validate()


def test_an_over_long_value_is_refused() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        LaunchOptions(extra_http_headers={"X-Big": "x" * 100_000}).validate()


def test_ordinary_headers_are_accepted() -> None:
    LaunchOptions(
        extra_http_headers={"Authorization": "Bearer abc.def", "X-Env": "staging", "Accept-Language": "en-GB"}
    ).validate()


def test_a_poisoned_recording_cannot_inject_headers_on_relaunch() -> None:
    """The security property, and the reason this is not simply restored like
    ``kind`` or ``har_path``: a JSONL recording is untrusted input (another
    local user, a poisoned CI step), and a header it could set would ride
    EVERY request the relaunched browser makes -- an attacker-chosen
    ``Authorization``/``Cookie`` attached to every site the user then visits.
    Same exclusion ``channel``/``executable_path``/``launch_args`` already get.
    """
    record = {
        "kind": "chromium",
        "url": "https://example.test/",
        "extra_http_headers": {"Authorization": "Bearer attacker-token"},
    }

    restored = LaunchOptions.from_launch_record(record)

    assert restored.extra_http_headers is None


# ─── page-level override (the macro action) ──────────────────────────────────


class TestPageLevelHeaders:
    """Measured page-over-context precedence on chromium, firefox and webkit
    (Playwright 1.62). Per PAGE, so a popup opened afterwards does not inherit
    them -- that is why the launch-time option exists alongside this."""

    def test_the_action_maps_to_a_method_that_exists(self) -> None:
        """The replay invariant: an entry in _ACTION_MAP whose method is missing
        makes every recorded occurrence a silent skip."""
        from octowright.macros.runtime import _ACTION_MAP
        from octowright.session.core_interaction_mixin import SessionInteractionMixin

        method = _ACTION_MAP["set_extra_http_headers"]

        assert hasattr(SessionInteractionMixin, method)

    def test_a_credential_header_is_scrubbed_under_the_default_policy(self) -> None:
        """Unlike press_key/evaluate -- selector-less sinks that cannot classify
        their own value and so are scrubbed only under `all` -- a header carries
        its NAME, and the name says whether the value is a secret."""
        scrubbed = redact_header_values({"Authorization": "Bearer s3cret", "X-Env": "staging"}, "passwords")

        assert scrubbed == {"Authorization": REDACTED_HEADER_PLACEHOLDER, "X-Env": "staging"}

    def test_all_scrubs_every_value_and_off_scrubs_none(self) -> None:
        headers = {"Authorization": "Bearer s3cret", "X-Env": "staging"}

        assert redact_header_values(headers, "all") == {
            "Authorization": REDACTED_HEADER_PLACEHOLDER,
            "X-Env": REDACTED_HEADER_PLACEHOLDER,
        }
        assert redact_header_values(headers, "off") == headers

    def test_names_are_never_scrubbed(self) -> None:
        """Which headers a run set is the diagnostic value; the name is not the secret."""
        assert sorted(redact_header_values({"Authorization": "x"}, "all")) == ["Authorization"]

    @pytest.mark.parametrize(
        "name", ["Authorization", "authorization", "Cookie", "X-Api-Key", "X-Session-Token", "proxy-authorization"]
    )
    def test_credential_header_names_are_recognised(self, name: str) -> None:
        assert is_credential_header(name)

    @pytest.mark.parametrize("name", ["X-Env", "Accept-Language", "X-Request-Id", "User-Agent"])
    def test_benign_header_names_are_not(self, name: str) -> None:
        assert not is_credential_header(name)

    def test_replaying_a_scrubbed_value_fails_with_the_fix_in_the_message(self) -> None:
        """A macro saved from a recording carries the placeholder, not the token.
        Sending it would authenticate as nobody and surface as a confusing 401
        several actions later."""
        with pytest.raises(ValueError, match="redaction placeholder"):
            _reject_redacted_headers({"Authorization": REDACTED_HEADER_PLACEHOLDER})

    def test_a_real_value_replays_normally(self) -> None:
        _reject_redacted_headers({"Authorization": "Bearer real", "X-Env": "staging"})
