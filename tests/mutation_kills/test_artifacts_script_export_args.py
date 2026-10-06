# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""How an exported macro CLI names, flags and defaults its arguments.

Every helper here renders source text the exported script is made of, so each
is pinned by exact equality on its output: a renamed function, a renamed flag
or a changed default is a different CLI for whoever runs the script.
"""

from __future__ import annotations

import pytest

from octowright.artifacts.script_export_args import (
    _append_parser_line,
    _args_dict,
    _call_args,
    _function_name,
    _identifier,
    _parameters,
    _parser_line,
    _parser_lines,
    _safe_default,
    _signature,
)
from octowright.macros.privacy import MacroArgPrivacy

PLAIN = MacroArgPrivacy()

EVIDENCE_LINE = (
    "    parser.add_argument('--evidence-dir', default='', help='Optional directory for result/evidence logs')"
)
TRUSTED_LINE = (
    "    parser.add_argument('--trusted-origin', action='append', default=[], "
    "help='Origin (scheme://host[:port]) the macro may send a credential header to and type a "
    "credential on; repeatable')"
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("  My Macro! ", "run_my_macro"),
        ("__login__", "run_login"),
        ("Checkout-Flow", "run_checkout_flow"),
        ("!!!", "run_macro"),
        ("", "run_macro"),
        ("2fa login", "run_macro_2fa_login"),
    ],
)
def test_function_name_is_a_clean_lowercase_identifier(name: str, expected: str) -> None:
    assert _function_name(name) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("email", "email"),
        ("  user email ", "user_email"),
        ("_private_", "private"),
        ("!!!", "arg"),
        ("1", "arg_1"),
        ("2fa", "arg_2fa"),
        ("a1", "a1"),
        ("Xray_X", "Xray_X"),
        ("class", "class_"),
        ("for", "for_"),
    ],
)
def test_identifier_repairs_only_what_python_rejects(value: str, expected: str) -> None:
    assert _identifier(value) == expected


def test_parameters_from_a_list_dedupe_their_identifiers_in_order() -> None:
    macro = {"parameters": ["a b", "a_b", "a-b", "a b!"]}

    assert _parameters(macro) == [
        ("a b", "a_b"),
        ("a_b", "a_b_2"),
        ("a-b", "a_b_3"),
        ("a b!", "a_b_4"),
    ]


def test_parameters_from_a_dict_take_its_keys() -> None:
    assert _parameters({"parameters": {"user": "", "pass word": ""}}) == [
        ("user", "user"),
        ("pass word", "pass_word"),
    ]


def test_parameters_skip_non_strings_without_stopping() -> None:
    assert _parameters({"parameters": [1, "user", None, "code"]}) == [("user", "user"), ("code", "code")]


@pytest.mark.parametrize("raw", [None, "user", 3])
def test_parameters_of_an_unusable_shape_are_empty(raw: object) -> None:
    assert _parameters({"parameters": raw}) == []


def test_parameters_absent_are_empty() -> None:
    assert _parameters({}) == []


def test_parser_line_renders_flag_dest_and_safe_default() -> None:
    assert _parser_line(("Shop Name", "Shop_Name"), {"Shop Name": "Acme"}, PLAIN) == (
        "    parser.add_argument('--Shop-Name', dest='Shop_Name', default='Acme')"
    )


def test_parser_line_trims_separators_from_the_flag() -> None:
    assert _parser_line(("-user-", "user"), None, PLAIN) == (
        "    parser.add_argument('--user', dest='user', default='')"
    )


def test_parser_line_keeps_letters_at_the_flag_edges() -> None:
    assert _parser_line(("Xray X", "Xray_X"), None, PLAIN) == (
        "    parser.add_argument('--Xray-X', dest='Xray_X', default='')"
    )


def test_parser_line_falls_back_to_the_identifier_for_a_flag() -> None:
    assert _parser_line(("!!!", "my_arg"), None, PLAIN) == (
        "    parser.add_argument('--my-arg', dest='my_arg', default='')"
    )


def test_parser_line_never_bakes_a_sensitive_default() -> None:
    assert _parser_line(("password", "password"), {"password": "zebrin4"}, PLAIN) == (  # pragma: allowlist secret
        "    parser.add_argument('--password', dest='password', default='')"
    )


def test_parser_lines_join_parameters_then_evidence_then_trusted_origin() -> None:
    params = [("region", "region"), ("code", "code")]

    assert _parser_lines(params, {"region": "eu"}, True, PLAIN) == "\n".join(
        [
            "    parser.add_argument('--region', dest='region', default='eu')",
            "    parser.add_argument('--code', dest='code', default='')",
            EVIDENCE_LINE,
            TRUSTED_LINE,
        ]
    )


def test_parser_lines_without_parameters_or_evidence_is_only_trusted_origin() -> None:
    assert _parser_lines([], None, False, PLAIN) == TRUSTED_LINE


def test_append_parser_line_does_not_lead_with_a_blank_line() -> None:
    assert _append_parser_line("", "x") == "x"
    assert _append_parser_line("a", "x") == "a\nx"


def test_signature_and_call_args() -> None:
    params = [("user", "user"), ("pass word", "pass_word")]

    assert _signature(params, True) == (
        "user: str = '', pass_word: str = '', evidence_dir: str = '', trusted_origins: tuple[str, ...] = ()"
    )
    assert _signature(params, False) == "user: str = '', pass_word: str = '', trusted_origins: tuple[str, ...] = ()"
    assert _call_args(params, True) == [
        "user=ns.user",
        "pass_word=ns.pass_word",
        "evidence_dir=ns.evidence_dir",
        "trusted_origins=tuple(ns.trusted_origin)",
    ]
    assert _call_args(params, False) == [
        "user=ns.user",
        "pass_word=ns.pass_word",
        "trusted_origins=tuple(ns.trusted_origin)",
    ]
    assert _args_dict(params) == "{'user': user, 'pass word': pass_word}"


class TestSafeDefault:
    def test_a_plain_value_is_baked(self) -> None:
        assert _safe_default("region", {"region": "eu"}, PLAIN) == "eu"

    def test_a_number_is_baked_as_its_text(self) -> None:
        assert _safe_default("count", {"count": 3}, PLAIN) == "3"

    def test_a_missing_or_none_value_is_empty(self) -> None:
        assert _safe_default("region", {}, PLAIN) == ""
        assert _safe_default("region", None, PLAIN) == ""
        assert _safe_default("region", {"region": None}, PLAIN) == ""

    def test_a_sensitive_name_is_never_baked(self) -> None:
        assert _safe_default("password", {"password": "zebrin4"}, PLAIN) == ""  # pragma: allowlist secret

    def test_an_assertion_arg_is_never_baked_even_when_unscrubbable(self) -> None:
        # A boolean is never admitted to the blind scrub, so only the positional
        # check keeps it out of the script's source.
        privacy = MacroArgPrivacy(assertion_args=frozenset({"flag"}))

        assert _safe_default("flag", {"flag": True}, privacy) == ""
        assert _safe_default("other", {"other": True}, privacy) == "True"

    def test_a_credential_arg_is_never_baked_even_when_unscrubbable(self) -> None:
        privacy = MacroArgPrivacy(credential_args=frozenset({"flag"}))

        assert _safe_default("flag", {"flag": True}, privacy) == ""
        assert _safe_default("other", {"other": True}, privacy) == "True"

    def test_a_value_carrying_another_args_secret_is_not_baked(self) -> None:
        args = {"note": "pw is zebrin4-secret", "password": "zebrin4-secret"}  # pragma: allowlist secret

        assert _safe_default("note", args, PLAIN) == ""
