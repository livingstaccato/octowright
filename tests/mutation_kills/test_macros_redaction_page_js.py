# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The argument the in-page redaction controller is built with, key by key.

``CONTROLLER_JS`` destructures these names; a misspelled key reaches the page as
``undefined`` and the controller silently matches nothing of that kind.
"""

from __future__ import annotations

from octowright.macros.redaction_page_js import CONTROLLER_JS, controller_argument
from octowright.macros.redaction_text import JS_DIGIT_SEPARATOR_CLASS, JS_IGNORABLE_CLASS


def test_the_controller_argument_is_every_table_the_page_destructures() -> None:
    argument = controller_argument(["87654321", "quokka", "1234-5678"])

    assert argument == {
        "values": ["87654321", "quokka", "1234-5678"],
        # Longest first, then in order, so a longer spelling is replaced before its own ending.
        "digits": ["12345678", "87654321", "2345678", "7654321"],
        "ignorable": JS_IGNORABLE_CLASS,
        "separators": JS_DIGIT_SEPARATOR_CLASS,
        "loading": ["data", "poster", "src", "srcdoc", "srcset"],
        "hrefLoading": ["BASE", "FEIMAGE", "IMAGE", "LINK", "USE"],
        "hrefDrawn": ["FEIMAGE", "IMAGE", "USE"],
        "opaque": ["CANVAS", "EMBED", "FRAME", "IFRAME", "OBJECT", "VIDEO"],
        "unmaskedTypes": [
            "button",
            "checkbox",
            "color",
            "date",
            "datetime-local",
            "file",
            "hidden",
            "image",
            "month",
            "radio",
            "range",
            "reset",
            "submit",
            "time",
            "week",
        ],
    }


def test_the_page_destructures_exactly_the_keys_it_is_given() -> None:
    head = CONTROLLER_JS.split("=>", 1)[0]
    names = head.strip().removeprefix("({").removesuffix("})").split(",")

    assert sorted(name.strip() for name in names) == sorted(controller_argument([]))


def test_a_value_list_without_digits_has_no_digit_needles() -> None:
    assert controller_argument(["quokka", "12"])["digits"] == []
