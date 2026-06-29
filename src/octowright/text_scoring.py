# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Small text scoring helpers for compact discovery tools."""

from __future__ import annotations

from collections.abc import Mapping


def _contains_score(name: str, *, reduced_fields: set[str], contains_score: float, reduced_score: float) -> float:
    return reduced_score if name in reduced_fields else contains_score


def _score_exact_or_contains(
    *,
    name: str,
    value: str,
    query: str,
    reduced_fields: set[str],
    contains_score: float,
    reduced_score: float,
) -> tuple[float, str | None]:
    if not value:
        return 0.0, None
    if value == query:
        return 100.0, f"{name} exact match"
    if query in value:
        score = _contains_score(
            name,
            reduced_fields=reduced_fields,
            contains_score=contains_score,
            reduced_score=reduced_score,
        )
        return score, f"{name} contains query"
    return 0.0, None


def _query_words(query: str, separators: tuple[str, ...]) -> list[str]:
    normalized = query
    for separator in separators:
        normalized = normalized.replace(separator, " ")
    return [word for word in normalized.split() if word]


def _word_match_score(fields: Mapping[str, str], query: str, separators: tuple[str, ...]) -> tuple[float, str | None]:
    words = _query_words(query, separators)
    if not words:
        return 0.0, None
    haystack = " ".join(fields.values())
    matched = sum(1 for word in words if word in haystack)
    if not matched:
        return 0.0, None
    return matched * 10.0, f"{matched}/{len(words)} query words matched"


def weighted_text_score(
    fields: Mapping[str, str],
    query: str,
    *,
    reduced_fields: set[str] | None = None,
    contains_score: float = 60.0,
    reduced_score: float = 35.0,
    separators: tuple[str, ...] = ("/", "-"),
) -> tuple[float, str]:
    q = query.strip().lower()
    if not q:
        return 0.0, "empty query"
    reduced = reduced_fields or set()
    score = 0.0
    reasons: list[str] = []
    for name, value in fields.items():
        increment, reason = _score_exact_or_contains(
            name=name,
            value=value,
            query=q,
            reduced_fields=reduced,
            contains_score=contains_score,
            reduced_score=reduced_score,
        )
        score += increment
        if reason:
            reasons.append(reason)
    word_score, word_reason = _word_match_score(fields, q, separators)
    score += word_score
    if word_reason:
        reasons.append(word_reason)
    return score, "; ".join(reasons) or "weak match"
