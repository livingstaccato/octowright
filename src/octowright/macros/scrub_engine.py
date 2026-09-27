# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""How a classified value is found and replaced: its spellings, their patterns, the walk.

Split out of ``macros.privacy``, which decides WHICH values are classified and
re-exports everything here. Standard library only.
"""

from __future__ import annotations

import functools
import html
import json
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import quote, quote_plus

REDACTED = "<redacted>"

_MAX_ENCODING_DEPTH = 3


def _serialized_variants(value: str) -> tuple[str, ...]:
    # Rendered verbatim into every exported macro CLI (artifacts.script_export),
    # so the generated script and the live scrubber cannot drift apart. It must
    # therefore stay self-contained: stdlib json/html/quote/quote_plus and
    # _MAX_ENCODING_DEPTH only.
    variants: set[str] = {
        value,
        json.dumps(value, ensure_ascii=True)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
        # Serialized page HTML (page.content(), a raw capture) spells & < > " '
        # as entities, so a value containing them no longer matches its raw form.
        html.escape(value, quote=True),
        html.escape(value, quote=False),
    }
    frontier = set(variants)
    for _ in range(_MAX_ENCODING_DEPTH):
        frontier = {encoded for item in frontier for encoded in (quote(item, safe=""), quote_plus(item, safe=""))}
        variants.update(frontier)
    # The markdown cache is markitdown's output, and markitdown's markdownify
    # backslash-escapes ``*`` and ``_`` in text nodes (its defaults:
    # escape_asterisks and escape_underscores on, escape_misc off -- checked on
    # markitdown 0.1.8 / markdownify 1.2.3), so ``Secret_pa*ss`` reaches the
    # cache as ``Secret\_pa\*ss``. Added after the percent-encoding pass: nothing
    # percent-encodes markdown, and each variant costs a pattern per write.
    variants.add(value.replace("*", "\\*").replace("_", "\\_"))
    return tuple(sorted((item for item in variants if item), key=len, reverse=True))


def sensitive_value_variants(values: Iterable[str]) -> tuple[str, ...]:
    """Every spelling the given classified values can take in a rendered page.

    The public, multi-value form of `_serialized_variants`. A caller redacting a
    live DOM before a screenshot has to remove every encoding of every classified
    value and then assert nothing remains, which needs one flat set rather than a
    tuple per value.

    Ordered longest first, like `sensitive_arg_values` and `_serialized_variants`.
    That ordering is load-bearing for a replacing caller, not cosmetic: when one
    variant is a substring of another -- which percent-encoding routinely produces,
    since `quote` leaves short values unchanged -- replacing the shorter one first
    consumes the characters the longer match needed and leaves the rest of the
    longer spelling on the page.

    Non-string entries are skipped rather than raising: the values reach this from
    macro arguments, where a null field is ordinary, and `_serialized_variants`
    raises TypeError on one. An empty string needs no guard here -- that function
    already returns no variants for it, and an empty variant would match at every
    position.
    """
    variants: set[str] = set()
    for value in values:
        if isinstance(value, str):
            variants.update(_serialized_variants(value))
    return tuple(sorted(variants, key=len, reverse=True))


# Below this length a value is short enough to occur inside unrelated words, so
# it only matches on an alphanumeric boundary. Longer values match anywhere:
# a credential split across a word boundary must still be caught.
_WORD_BOUNDED_BELOW = 4

#: Characters that continue an identifier for a WORD-BOUNDED ledger entry (a
#: password the input classification admitted, see `admit_redacted_input`).
#: ``-`` and ``_`` are included so ``#admin-menu`` and ``admin_panel`` are one
#: identifier and survive a typed password of ``admin``.
_IDENTIFIER_CHARS = "A-Za-z0-9_-"


def _continues_identifier(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in "_-")


def _identifier_bounded(variant: str) -> str:
    """*variant* as a pattern that cannot match inside a longer identifier.

    Each edge is guarded only where the variant's own edge character would
    continue an identifier: a value starting with ``!`` cannot be the tail of
    one, so requiring a boundary before it would only miss real echoes.
    """
    before = f"(?<![{_IDENTIFIER_CHARS}])" if _continues_identifier(variant[0]) else ""
    after = f"(?![{_IDENTIFIER_CHARS}])" if _continues_identifier(variant[-1]) else ""
    return f"{before}{re.escape(variant)}{after}"


@functools.lru_cache(maxsize=64)
def _scrub_patterns(
    sensitive_values: tuple[str, ...], word_bounded: frozenset[str] = frozenset()
) -> tuple[re.Pattern[str], ...]:
    """The compiled patterns for one ledger state, in the order they must apply.

    Cached because the durable scrubber runs on every capture of a session that
    admitted a credential, and the variants (JSON, HTML, percent-encoded to a
    depth) and their regexes were re-derived on each call. The ledger only
    grows, so each state is compiled once. *word_bounded* values match only as
    a whole identifier (`_identifier_bounded`); the rest keep the length rule.
    """
    patterns: list[re.Pattern[str]] = []
    for sensitive in sensitive_values:
        for variant in _serialized_variants(sensitive):
            if sensitive in word_bounded:
                pattern = _identifier_bounded(variant)
            elif len(sensitive) < _WORD_BOUNDED_BELOW:
                pattern = rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])"
            else:
                pattern = re.escape(variant)
            # Percent-encoded spellings vary in hex case between producers.
            patterns.append(re.compile(pattern, re.IGNORECASE if "%" in variant else 0))
    return tuple(patterns)


def _scrub_text(
    text: str, sensitive_values: tuple[str, ...], marker: str, word_bounded: frozenset[str] = frozenset()
) -> str:
    for pattern in _scrub_patterns(tuple(sensitive_values), word_bounded):
        text = pattern.sub(marker, text)
    return text


def _scrub_tree(value: Any, scrub_text: Callable[[str], str]) -> Any:
    """A copy of *value* with every string, mapping keys included, passed through *scrub_text*."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, Mapping):
        return {str(scrub_text(str(key))): _scrub_tree(item, scrub_text) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_tree(item, scrub_text) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_tree(item, scrub_text) for item in value)
    return value


def scrub_sensitive_values(
    value: Any,
    sensitive_values: tuple[str, ...],
    *,
    marker: str = REDACTED,
    word_bounded: frozenset[str] = frozenset(),
) -> Any:
    """Copy a diagnostic while scrubbing raw, escaped, and URL-encoded values.

    Members of *word_bounded* are replaced only where they are not embedded in
    a longer identifier; see `PrivacyLedger.word_bounded`.
    """
    return _scrub_tree(value, lambda text: _scrub_text(text, sensitive_values, marker, word_bounded))


def _any_value_pattern(patterns: tuple[re.Pattern[str], ...]) -> re.Pattern[str]:
    """One pattern that matches wherever any of *patterns* would.

    A filter, never a replacement: a single alternation is not byte-identical
    to applying the patterns in turn (a shorter value can win at an earlier
    position than a longer one that overlaps it, and a later pattern sees the
    markers an earlier one wrote). But a string it does not match is a string
    none of them matches, so the sequential scrub would return it unchanged.
    Each alternative keeps its own case-folding.
    """
    alternatives = (
        f"(?i:{pattern.pattern})" if pattern.flags & re.IGNORECASE else f"(?:{pattern.pattern})" for pattern in patterns
    )
    return re.compile("|".join(alternatives))


def filtered_text_scrubber(
    sensitive_values: tuple[str, ...], word_bounded: frozenset[str] = frozenset()
) -> Callable[[str], str]:
    """`_scrub_text` for one ledger state, behind a single search that rules most strings out.

    Almost every string a page produces holds no value, so one search with the
    combined pattern settles it; only a string that does hold one pays for the
    per-variant passes, and gets exactly the sequential result.
    """
    present = _any_value_pattern(_scrub_patterns(sensitive_values, word_bounded)).search

    def scrub_text(text: str) -> str:
        return _scrub_text(text, sensitive_values, REDACTED, word_bounded) if present(text) else text

    return scrub_text
