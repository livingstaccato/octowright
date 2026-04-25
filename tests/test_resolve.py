# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for octowright.resolve.

The resolver scores saved personas/profiles against a request URL so the LLM
can ask the user 'which dante account?' instead of guessing. Tests build a
fake PROFILES_DIR with a few personas, then assert the resolver classifies
each request correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from octowright import resolve as _resolve

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_profiles_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Build a hermetic PROFILES_DIR with several personas across engines."""
    pdir = tmp_path / "profiles"
    pdir.mkdir()

    from octowright import personas as _personas
    from octowright import profiles as _profiles

    monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", pdir)

    def _make(persona: str, *, default_url: str | None, hosts: list[str] | None, engines: list[str]) -> None:
        (pdir / persona).mkdir()
        doc: dict[str, object] = {"name": persona}
        if default_url:
            doc["default_url"] = default_url
        if hosts:
            doc["app"] = {"hosts": hosts}
        (pdir / persona / "profile.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        for kind in engines:
            engine_dir = pdir / persona / kind
            engine_dir.mkdir()
            (engine_dir / "Cookies").write_bytes(b"x" * 32)

    # Two discord-default personas (dante on webkit+firefox, ops on firefox).
    _make("dante", default_url="https://discord.com/app", hosts=None, engines=["webkit", "firefox"])
    _make("ops", default_url="https://discord.com/monitor", hosts=None, engines=["firefox"])
    # A persona using app.hosts for explicit host membership (TradeWars on chromium).
    _make("commander", default_url=None, hosts=["tradewars.com"], engines=["chromium"])
    # A persona with no engine profile yet (only metadata).
    _make("freshie", default_url="https://github.com/", hosts=None, engines=[])

    return pdir


# ---------------------------------------------------------------------------
# host parsing
# ---------------------------------------------------------------------------


class TestHostMatching:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://discord.com/app", "discord.com"),
            ("https://app.discord.com/login", "app.discord.com"),
            ("discord.com", "discord.com"),  # bare host without scheme
            ("", ""),
            (None, ""),
        ],
    )
    def test_host_of(self, url: str | None, expected: str) -> None:
        assert _resolve._host_of(url) == expected

    def test_subdomains_match_parent(self) -> None:
        assert _resolve._hosts_match("app.discord.com", "discord.com") is True
        assert _resolve._hosts_match("discord.com", "app.discord.com") is True

    def test_unrelated_hosts_dont_match(self) -> None:
        assert _resolve._hosts_match("evil-discord.com", "discord.com") is False
        assert _resolve._hosts_match("discord.com.evil.org", "discord.com") is False

    def test_empty_hosts_dont_match(self) -> None:
        assert _resolve._hosts_match("", "discord.com") is False
        assert _resolve._hosts_match("discord.com", "") is False


# ---------------------------------------------------------------------------
# the four classifications
# ---------------------------------------------------------------------------


def test_no_matches_returns_ephemeral_ok(populated_profiles_dir: Path) -> None:
    """Site nobody owns — ephemeral launch is fine. Weak matches (engine
    profile exists, but no host signal) may still appear; only strong matches
    block ephemeral_ok."""
    result = _resolve.suggest_for_url("https://example.com/")
    assert result["host"] == "example.com"
    # No persona has example.com as default_url or in app.hosts.
    strong = [m for m in result["matches"] if m["score"] >= 2]
    assert strong == []
    assert result["ambiguous"] is False
    assert result["ephemeral_ok"] is True
    assert "ephemeral" in result["recommendation"].lower()


def test_single_strong_match_recommends_specific_persona(populated_profiles_dir: Path) -> None:
    result = _resolve.suggest_for_url("https://github.com/issues")
    # freshie has default_url=https://github.com/ (score 2 on persona; no engine profile = score stays 2)
    assert result["ambiguous"] is False
    assert result["ephemeral_ok"] is False
    assert any(m["persona"] == "freshie" for m in result["matches"])
    assert "exactly one match" in result["recommendation"]
    assert "freshie" in result["recommendation"]


def test_ambiguous_when_multiple_personas_share_host(populated_profiles_dir: Path) -> None:
    """dante (webkit + firefox) and ops (firefox) all default to discord.com."""
    result = _resolve.suggest_for_url("https://discord.com/channels")
    assert result["ambiguous"] is True
    assert result["ephemeral_ok"] is False

    persona_names = {m["persona"] for m in result["matches"]}
    assert {"dante", "ops"}.issubset(persona_names)

    rec = result["recommendation"]
    assert "AMBIGUOUS" in rec
    assert "dante" in rec
    assert "ops" in rec


def test_kind_filter_narrows_candidates(populated_profiles_dir: Path) -> None:
    """'open discord.com using webkit' → only dante/webkit qualifies."""
    result = _resolve.suggest_for_url("https://discord.com/", kind="webkit")
    assert result["kind_filter"] == "webkit"
    kinds = {m["kind"] for m in result["matches"]}
    assert kinds == {"webkit"}
    assert result["ambiguous"] is False
    assert "exactly one match on webkit" in result["recommendation"]
    assert "dante" in result["recommendation"]


def test_kind_filter_can_still_be_ambiguous(populated_profiles_dir: Path) -> None:
    """'using firefox' on discord still hits both dante AND ops."""
    result = _resolve.suggest_for_url("https://discord.com/", kind="firefox")
    assert result["kind_filter"] == "firefox"
    persona_names = {m["persona"] for m in result["matches"]}
    assert persona_names == {"dante", "ops"}
    assert result["ambiguous"] is True


def test_kind_filter_with_no_match_suggests_dropping_filter(populated_profiles_dir: Path) -> None:
    """User said 'using firefox' but only chromium has tradewars.com — point them at the broader query."""
    result = _resolve.suggest_for_url("https://tradewars.com/", kind="firefox")
    assert result["kind_filter"] == "firefox"
    # No firefox persona has tradewars.com — strong matches must be empty.
    strong = [m for m in result["matches"] if m["score"] >= 2]
    assert strong == []
    assert result["ephemeral_ok"] is True
    rec = result["recommendation"]
    # Any reported matches must be firefox (kind filter applies); none should be commander.
    for m in result["matches"]:
        assert m["kind"] == "firefox"
        assert m["persona"] != "commander"
    # The message points the user back at the unfiltered query.
    assert "browser_suggest_for_url" in rec


def test_app_hosts_metadata_matches(populated_profiles_dir: Path) -> None:
    """commander has app.hosts: ['tradewars.com'] but no default_url."""
    result = _resolve.suggest_for_url("https://tradewars.com/play")
    assert any(m["persona"] == "commander" for m in result["matches"])
    cmd = next(m for m in result["matches"] if m["persona"] == "commander")
    assert any("app.hosts" in r for r in cmd["reasons"])
    # app.hosts (3) + chromium engine profile (1) = 4
    assert cmd["score"] >= 3


def test_subdomain_url_matches_parent_host_default(populated_profiles_dir: Path) -> None:
    """app.discord.com should still resolve dante/ops who declared discord.com."""
    result = _resolve.suggest_for_url("https://app.discord.com/")
    persona_names = {m["persona"] for m in result["matches"]}
    assert {"dante", "ops"}.issubset(persona_names)


def test_matches_sorted_by_score_then_recency(populated_profiles_dir: Path) -> None:
    """Strong matches (score>=2) come before weak ones; among ties, most recent wins."""
    result = _resolve.suggest_for_url("https://discord.com/")
    scores = [m["score"] for m in result["matches"]]
    # Each score should be >= the next.
    assert scores == sorted(scores, reverse=True)


def test_payload_omits_internal_mtime_field(populated_profiles_dir: Path) -> None:
    """mtime is an internal sort key — shouldn't leak to the MCP caller."""
    result = _resolve.suggest_for_url("https://discord.com/")
    for m in result["matches"]:
        assert "mtime" not in m
        # public fields:
        assert {"persona", "kind", "score", "reasons", "last_used"}.issubset(m.keys())


# ---------------------------------------------------------------------------
# bare host parsing edge cases
# ---------------------------------------------------------------------------


def test_bare_host_without_scheme_is_resolved(populated_profiles_dir: Path) -> None:
    """The user often says 'discord.com' with no https://. Treat it as the host."""
    result = _resolve.suggest_for_url("discord.com")
    assert result["host"] == "discord.com"
    persona_names = {m["persona"] for m in result["matches"]}
    assert "dante" in persona_names
