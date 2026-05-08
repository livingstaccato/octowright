# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for octowright._format.

These functions are pure — no fixtures, no mocking. They format live-tool
output for human consumption, so the assertions read like the user-facing
strings: short, structural, and easy to eyeball when one regresses.
"""

from __future__ import annotations

import pytest

from octowright._format import (
    browser_summary,
    participant_summary,
    scenario_summary,
    short_id,
    short_url,
)


class TestShortUrl:
    def test_returns_empty_for_none(self) -> None:
        assert short_url(None) == ""

    def test_returns_empty_for_empty_string(self) -> None:
        assert short_url("") == ""

    def test_strips_scheme_returns_host_plus_path(self) -> None:
        assert short_url("https://example.com/some/path") == "example.com/some/path"

    def test_drops_trailing_slash_only_path(self) -> None:
        assert short_url("https://example.com/") == "example.com"

    def test_truncates_with_ellipsis_when_over_max(self) -> None:
        url = "https://example.com/" + "a" * 100
        rendered = short_url(url, max_chars=20)
        assert len(rendered) == 20
        assert rendered.endswith("…")

    def test_keeps_short_url_intact(self) -> None:
        assert short_url("https://example.com/x", max_chars=48) == "example.com/x"

    def test_unparseable_falls_back_to_truncation(self) -> None:
        # Even malformed strings should not raise.
        out = short_url("not a url at all", max_chars=10)
        assert isinstance(out, str)

    def test_urlparse_exception_falls_back_to_truncation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force urlparse to raise so the except-branch (lines 24-25) is hit.
        import octowright._format as _fmt

        def _raising_urlparse(url: str):
            raise ValueError("simulated bad parse")

        monkeypatch.setattr(_fmt, "urlparse", _raising_urlparse)
        out = short_url("https://example.com/long-path", max_chars=10)
        assert isinstance(out, str)
        assert len(out) <= 10


class TestShortId:
    def test_takes_first_six_chars(self) -> None:
        assert short_id("abcdef0123456789") == "abcdef"

    def test_short_id_passes_through_when_smaller(self) -> None:
        assert short_id("abc") == "abc"


class TestBrowserSummary:
    def test_zero_browsers(self) -> None:
        assert browser_summary([]) == "0 browsers"

    def test_singular_form_has_no_s(self) -> None:
        s = browser_summary([{"instance_id": "abc123def456", "kind": "webkit", "url": "https://x.io/"}])
        assert s.startswith("1 browser:")  # not "1 browsers"

    def test_plural_form_has_s(self) -> None:
        sessions = [
            {"instance_id": "aaa111bbb222", "kind": "webkit", "url": "https://x.io/"},
            {"instance_id": "ccc333ddd444", "kind": "firefox", "url": "https://y.io/"},
        ]
        assert browser_summary(sessions).startswith("2 browsers:")

    def test_uses_profile_first_then_label_then_short_id(self) -> None:
        sessions = [
            {"instance_id": "0123456789ab", "kind": "webkit", "profile": "dante", "label": "ignored", "url": ""},
            {"instance_id": "9876543210fe", "kind": "firefox", "profile": None, "label": "monitor", "url": ""},
            {"instance_id": "deadbeefcafe", "kind": "chromium", "profile": None, "label": None, "url": ""},
        ]
        s = browser_summary(sessions)
        assert "dante/webkit" in s
        assert "monitor/firefox" in s
        # Short id fallback uses first 6 of the instance_id.
        assert "deadbe/chromium" in s

    def test_omits_at_clause_when_no_url(self) -> None:
        s = browser_summary([{"instance_id": "abc123def456", "kind": "webkit", "url": ""}])
        assert " @ " not in s


class TestParticipantSummary:
    def test_empty_returns_empty_string(self) -> None:
        assert participant_summary([]) == ""

    def test_renders_role_persona_kind(self) -> None:
        ps = [
            {"role": "player", "persona": "dante", "kind": "webkit"},
            {"role": "monitor", "persona": "ops", "kind": "firefox"},
        ]
        out = participant_summary(ps)
        assert out == "player[dante]/webkit · monitor[ops]/firefox"


class TestScenarioSummary:
    def test_zero(self) -> None:
        assert scenario_summary([]) == "0 live scenarios"

    def test_single_scenario_inline_form(self) -> None:
        scenarios = [
            {
                "name": "mini",
                "participants": [
                    {"role": "player", "persona": "dante", "kind": "webkit"},
                    {"role": "monitor", "persona": "ops", "kind": "firefox"},
                ],
            }
        ]
        out = scenario_summary(scenarios)
        assert out.startswith("scenario 'mini' (2 participants):")
        assert "player[dante]/webkit" in out
        assert "monitor[ops]/firefox" in out
        # No newline for the single-scenario case.
        assert "\n" not in out

    def test_multi_scenario_multiline_form(self) -> None:
        scenarios = [
            {"name": "alpha", "participants": [{"role": "p", "persona": "a", "kind": "webkit"}]},
            {"name": "beta", "participants": [{"role": "p", "persona": "b", "kind": "firefox"}]},
        ]
        out = scenario_summary(scenarios)
        assert out.startswith("2 live scenarios:")
        assert "  'alpha' (1):" in out
        assert "  'beta' (1):" in out
