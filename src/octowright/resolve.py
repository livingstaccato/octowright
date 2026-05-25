# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Suggest personas/profiles for an ambiguous URL.

When a user says "open google.com" the LLM has to choose between launching an
ephemeral browser or reusing one of N saved profiles that already log in to
that domain. This module looks at persona metadata and existing engine
profiles, scores each candidate against the requested URL, and returns a
ranked list so the LLM can ask the user instead of guessing.

Scoring (per (persona, kind) candidate):

* +3 — the URL host is listed in ``app.hosts`` of the persona's profile.yaml
* +2 — the persona's ``default_url`` host equals the requested URL host
* +1 — an engine profile dir exists for that (persona, kind) — i.e. the user
  has launched that persona on that engine before
* +0.5 (tie-break) — recency, normalised by mtime so the most recently used
  candidate wins among otherwise-equal scores

Anything <1 isn't reported as a match.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from octowright import engine_profiles as _profiles
from octowright import personas as _personas
from octowright.types import PersonaListEntry


def _host_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    host = (parsed.netloc or parsed.path).lower()
    if host.startswith("www."):
        host = host[len("www.") :]
    return host


def _hosts_match(a: str, b: str) -> bool:
    """True if two hosts share the registrable domain.

    'app.discord.com' and 'discord.com' match; 'evil-discord.com' does not.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    return a.endswith("." + b) or b.endswith("." + a)


def _persona_hosts(persona: Any) -> list[str]:
    """Extract the lower-cased host list from a persona's app config."""
    app_hosts = persona.app.get("hosts") if isinstance(persona.app, dict) else None
    if not isinstance(app_hosts, list):
        return []
    return [str(h).lower() for h in app_hosts]


def _resolve_engines(prow: PersonaListEntry, kind: str | None) -> list[str]:
    """Engines to score for this persona row.

    Empty engine list still yields a persona-level entry under "webkit"
    so the LLM gets a nudge even before any browser has been launched.
    """
    engines = prow.get("engines") or ["webkit"]
    if kind is not None:
        return [e for e in engines if e == kind]
    return list(engines)


def _score_persona_engine(
    host: str,
    prow: PersonaListEntry,
    persona_hosts: list[str],
    default_host: str,
    engine_kind: str,
    profile_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    """Score one (persona, engine) pair against the target host. Returns
    a candidate dict, or None if the score is below the inclusion threshold."""
    score = 0.0
    reasons: list[str] = []
    if any(_hosts_match(host, h) for h in persona_hosts):
        score += 3
        reasons.append(f"app.hosts contains {host}")
    if default_host and _hosts_match(host, default_host):
        score += 2
        reasons.append(f"default_url host is {default_host}")
    engine_profile = profile_index.get((prow["name"], engine_kind))
    if engine_profile is not None:
        score += 1
        reasons.append(f"{engine_kind} profile exists ({engine_profile['size_bytes']} bytes)")
    if score < 1:
        return None
    mtime = engine_profile["mtime"] if engine_profile else prow.get("mtime", 0)
    last_used = engine_profile["last_used"] if engine_profile else prow.get("last_used", "")
    return {
        "persona": prow["name"],
        "kind": engine_kind,
        "score": score,
        "reasons": reasons,
        "last_used": last_used,
        "mtime": mtime,
    }


def _collect_candidates(
    host: str,
    kind: str | None,
    persona_rows: list[PersonaListEntry],
    profile_index: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Walk each persona row x applicable engine, scoring against host."""
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for prow in persona_rows:
        try:
            persona = _personas.load_persona(prow["name"])
        except FileNotFoundError:
            continue
        ph = _persona_hosts(persona)
        default_host = _host_of(persona.default_url)
        for engine_kind in _resolve_engines(prow, kind):
            entry = _score_persona_engine(host, prow, ph, default_host, engine_kind, profile_index)
            if entry is not None:
                candidates[(prow["name"], engine_kind)] = entry
    return list(candidates.values())


def suggest_for_url(url: str, kind: str | None = None) -> dict[str, Any]:
    """Return ranked persona/profile suggestions for a URL.

    Args:
        url: Target URL (or bare host).
        kind: Optional engine filter. If the user said "open X using firefox",
            pass kind='firefox' so candidates from other engines are dropped
            from the ranked list — that turns a 7-way ambiguity into a much
            smaller set.

    Shape:
        {
          "url": str,
          "host": str,
          "kind_filter": str | None,
          "matches": [ {persona, kind, score, reasons, last_used}, ... ],
          "ambiguous": bool,            # >1 strong match (after kind filter)
          "ephemeral_ok": bool,         # 0 strong matches → fine to launch fresh
          "recommendation": str,        # human-readable next step for the LLM
        }
    """
    host = _host_of(url)
    profile_index: dict[tuple[str, str], dict[str, Any]] = {
        (p["name"], p["kind"]): p for p in _profiles.list_profiles()
    }
    candidates = _collect_candidates(host, kind, _personas.list_personas(), profile_index)

    # Sort by score desc, then mtime desc (most-recently-used wins ties).
    matches = sorted(candidates, key=lambda c: (c["score"], c["mtime"]), reverse=True)
    strong = [m for m in matches if m["score"] >= 2]
    ambiguous = len(strong) > 1
    ephemeral_ok = len(strong) == 0

    recommendation = _format_recommendation(host, matches, strong, ambiguous, ephemeral_ok, kind)

    # Strip mtime from the public payload — internal tie-breaker only.
    public_matches = [{k: v for k, v in m.items() if k != "mtime"} for m in matches]
    return {
        "url": url,
        "host": host,
        "kind_filter": kind,
        "matches": public_matches,
        "ambiguous": ambiguous,
        "ephemeral_ok": ephemeral_ok,
        "recommendation": recommendation,
    }


def _format_recommendation(
    host: str,
    matches: list[dict[str, Any]],
    strong: list[dict[str, Any]],
    ambiguous: bool,
    ephemeral_ok: bool,
    kind_filter: str | None,
) -> str:
    kind_clause = f" on {kind_filter}" if kind_filter else ""
    if ephemeral_ok:
        if matches:
            weak = matches[0]
            base = (
                f"no saved persona has {host} in its default_url or app.hosts{kind_clause}; "
                f"launching ephemeral is fine, or reuse '{weak['persona']}/{weak['kind']}' "
                f"if the user wants their existing browser state."
            )
            if kind_filter:
                base += (
                    f" (Tip: call browser_suggest_for_url(url={host!r}) without `kind` "
                    f"to see if another engine is a stronger fit.)"
                )
            return base
        if kind_filter:
            return (
                f"no saved persona matches {host} on {kind_filter}; "
                f"call browser_suggest_for_url(url={host!r}) without a kind to see other-engine matches, "
                f"or browser_launch with no profile for an ephemeral session."
            )
        return (
            f"no saved persona matches {host}; ask the user 'launch a fresh browser?' "
            f"or call browser_launch with no profile for an ephemeral session."
        )
    if ambiguous:
        names = ", ".join(f"'{m['persona']}/{m['kind']}'" for m in strong)
        return (
            f"AMBIGUOUS: {len(strong)} personas{kind_clause} have {host} as their default — {names}. "
            f"Ask the user which one to use, or pass profile=<name> kind=<kind> to "
            f"browser_launch. Don't pick silently."
        )
    only = strong[0]
    return (
        f"exactly one match{kind_clause}: '{only['persona']}/{only['kind']}'. "
        f"Pass profile='{only['persona']}' kind='{only['kind']}' to browser_launch to reuse "
        f"its saved login. Confirm with the user first if they didn't mention this persona."
    )
