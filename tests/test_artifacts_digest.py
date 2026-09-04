# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

from octowright.artifacts.digest import digest_macro, digest_recording_text, truncate_text


def test_truncate_text_reports_size_and_cap() -> None:
    result = truncate_text("abcdef", max_chars=3)

    assert result == {
        "summary": "abc",
        "truncated": True,
        "source_size": 6,
        "cap": 3,
    }


def test_digest_macro_summarizes_actions_and_params() -> None:
    macro = {
        "name": "login",
        "parameters": ["email", "password"],
        "actions": [
            {"action": "navigate", "url": "https://example.test"},
            {"action": "fill", "selector": "#email", "value": "{{email}}"},
            {"action": "click", "selector": "button"},
        ],
    }

    result = digest_macro(macro, max_chars=4000)

    assert result["truncated"] is False
    assert "Macro login" in result["summary"]
    assert "parameters: email, password" in result["summary"]
    assert "navigate: 1" in result["summary"]
    assert "fill: 1" in result["summary"]


def test_digest_recording_text_counts_jsonl_actions(tmp_path: Path) -> None:
    path = tmp_path / "recording.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"action": "launch", "url": "https://example.test"}),
                json.dumps({"action": "click"}),
                "not-json",
                json.dumps({"action": "click"}),
            ]
        ),
        encoding="utf-8",
    )

    result = digest_recording_text(path.read_text(), max_chars=4000)

    assert "events: 3" in result["summary"]
    assert "malformed: 1" in result["summary"]
    assert "click: 2" in result["summary"]


def test_digest_recording_text_sanitizes_token_bearing_urls() -> None:
    text = json.dumps(
        {
            "action": "navigate",
            "url": "https://user:pass@example.test/path?token=secret#frag",
        }
    )

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: https://example.test/path" in result["summary"]
    assert "last_url: https://example.test/path" in result["summary"]
    assert "user" not in result["summary"]
    assert "pass" not in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_reports_same_first_and_last_url() -> None:
    text = json.dumps({"action": "navigate", "url": "https://example.test/path?token=secret"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: https://example.test/path" in result["summary"]
    assert "last_url: https://example.test/path" in result["summary"]


def test_digest_recording_text_handles_malformed_url_ports() -> None:
    text = json.dumps({"action": "navigate", "url": "https://example.test:bad/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_handles_no_host_userinfo_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "https://user:pass@/path?token=x#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "user" not in result["summary"]
    assert "pass" not in result["summary"]
    assert "token" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_handles_protocol_relative_no_host_userinfo_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "//user:pass@/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "user" not in result["summary"]
    assert "pass" not in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_rejects_multi_slash_userinfo_like_relative_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "////user:pass@host/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "user" not in result["summary"]
    assert "pass" not in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_rejects_long_relative_userinfo_before_truncation() -> None:
    user = "user"
    sensitive_prefix = "sec" + "ret-prefix-token"
    long_value = sensitive_prefix + ("x" * 80)
    text = json.dumps({"action": "navigate", "url": f"////{user}:{long_value}@host/path?token=query-secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert user not in result["summary"]
    assert sensitive_prefix not in result["summary"]
    assert "token" not in result["summary"]
    assert "query-secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_handles_unclosed_ipv6_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "https://[::1/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_preserves_bracketed_ipv6_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "https://[::1]/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: https://[::1]/path" in result["summary"]
    assert "last_url: https://[::1]/path" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_preserves_bracketed_ipv6_urls_with_port() -> None:
    text = json.dumps({"action": "navigate", "url": "https://[2001:db8::1]:8443/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: https://[2001:db8::1]:8443/path" in result["summary"]
    assert "last_url: https://[2001:db8::1]:8443/path" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_recording_text_handles_invalid_nfkc_urls() -> None:
    text = json.dumps({"action": "navigate", "url": "https://exa\u2100mple.test/path?token=secret#frag"})

    result = digest_recording_text(text, max_chars=4000)

    assert "first_url: (invalid-url)" in result["summary"]
    assert "last_url: (invalid-url)" in result["summary"]
    assert "token" not in result["summary"]
    assert "secret" not in result["summary"]
    assert "frag" not in result["summary"]


def test_digest_macro_uses_fallback_for_non_scalar_action_values() -> None:
    macro = {
        "name": "bad-actions",
        "actions": [
            {"action": {"nested": "secret"}},
            {"action": ["secret"]},
            {"action": "click"},
        ],
    }

    result = digest_macro(macro, max_chars=4000)

    assert "(invalid): 2" in result["summary"]
    assert "click: 1" in result["summary"]
    assert "secret" not in result["summary"]
    assert "nested" not in result["summary"]


def test_digest_macro_uses_fallback_for_non_scalar_parameters() -> None:
    macro = {
        "name": "bad-params",
        "parameters": ["email", {"credential": "hidden-value"}, ["hidden-list"]],
        "actions": [],
    }

    result = digest_macro(macro, max_chars=4000)

    assert "parameters: email, (invalid), (invalid)" in result["summary"]
    assert "credential" not in result["summary"]
    assert "hidden-value" not in result["summary"]
    assert "hidden-list" not in result["summary"]


# ─── boundaries the summariser decides by, none of which were pinned ─────────


def test_text_exactly_at_the_cap_is_not_reported_as_truncated() -> None:
    """``len(text) > cap``, not ``>=``.

    A digest is a summary shown to an agent, and ``truncated: True`` is how it
    says "there was more than this". Off-by-one in the permissive direction is
    harmless noise; here it is the opposite -- text that fits exactly would be
    labelled truncated, so a caller that reacts by re-reading at a larger cap
    loops on content that was already complete.
    """
    result = truncate_text("abc", max_chars=3)

    assert result == {"summary": "abc", "truncated": False, "source_size": 3, "cap": 3}


def test_a_blank_recording_line_is_skipped_without_counting_as_malformed() -> None:
    """Blank lines are separators, not damage.

    ``malformed`` is the digest's damage signal. A recording written with a
    trailing newline ends in a blank line, so counting it would put
    ``malformed: 1`` on essentially every healthy recording and make the
    number useless for spotting a genuinely truncated file.
    """
    text = "\n".join(['{"action":"click"}', "", "   ", '{"action":"fill"}', ""])

    summary = digest_recording_text(text)["summary"]

    assert "events: 2" in summary
    assert "malformed: 0" in summary


def test_an_unparsable_recording_line_is_counted_as_malformed() -> None:
    """The other half: real damage has to reach the counter.

    A ``recording_truncated`` cut lands mid-line, so the last line is
    unparsable JSON -- exactly this case. If it stops counting, a half-written
    recording digests as clean.
    """
    text = "\n".join(['{"action":"click"}', "{not json", '["a","list"]'])

    summary = digest_recording_text(text)["summary"]

    assert "events: 1" in summary
    assert "malformed: 2" in summary


def test_parse_recording_line_reports_damage_and_payload_independently() -> None:
    """The two tuple slots answer different questions and both are load-bearing.

    Asserted at this level rather than through ``digest_recording_text``
    because the caller reads ``malformed_line`` only when ``entry is None``:
    the flag on a SUCCESSFUL parse is unobservable from outside, so a mutation
    setting it there changes nothing a public-API test could see. It is still
    part of the contract -- the next caller to read the flag unconditionally
    would inherit a wrong value with no test objecting.
    """
    from octowright.artifacts.digest import _parse_recording_line

    assert _parse_recording_line("") == (False, None)
    assert _parse_recording_line("   ") == (False, None)
    assert _parse_recording_line("{not json") == (True, None)
    assert _parse_recording_line('["a","list"]') == (True, None)
    assert _parse_recording_line('{"action":"click"}') == (False, {"action": "click"})


def test_a_relative_url_carrying_userinfo_is_replaced_rather_than_summarised() -> None:
    """``@`` and ``:`` together mean credentials, and both terms are required.

    A single-slash path has no netloc, so ``urlsplit`` reports no hostname and
    the whole string -- credentials included -- reaches the relative-URL branch
    to be copied into the digest verbatim. (The two-slash form is a different
    path: it *does* parse a netloc, and ``urlunsplit`` drops the userinfo while
    rebuilding it.) A digest is the summary an agent reads and a report embeds,
    so this is the last place a password can be caught. The check is an
    ``and``: relaxing it to ``or`` blanks every ordinary path containing a
    colon or an ``@``.
    """
    with_credentials = digest_recording_text('{"action":"navigate","url":"/admin:hunter2@evil.test/x"}')
    assert "(invalid-url)" in with_credentials["summary"]
    assert "hunter2" not in with_credentials["summary"]

    ordinary = digest_recording_text('{"action":"navigate","url":"/orders/42"}')
    assert "/orders/42" in ordinary["summary"]


def test_an_at_sign_alone_does_not_disqualify_a_relative_url() -> None:
    """The ``:`` term is the one that says "credential", not the ``@``.

    ``/u/@handle`` is an ordinary profile path on half the web. Dropping the
    colon test blanks it as ``(invalid-url)`` and the digest loses the one
    field that says where the recording went.
    """
    summary = digest_recording_text('{"action":"navigate","url":"/u/@handle"}')["summary"]

    assert "/u/@handle" in summary
    assert "(invalid-url)" not in summary


def test_a_path_relative_url_without_a_leading_slash_is_kept() -> None:
    """The ``startswith("/")`` guard returns early for a reason.

    A recording can carry a document-relative URL (``orders/42``), which has no
    leading slash and therefore no userinfo section to find. Inverting that
    early return blanks it as ``(invalid-url)``, so the digest of a macro that
    navigated by relative link reports nothing about where it went.
    """
    summary = digest_recording_text('{"action":"navigate","url":"orders/42"}')["summary"]

    assert "orders/42" in summary
    assert "(invalid-url)" not in summary
