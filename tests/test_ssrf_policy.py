# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""OCTOWRIGHT_SSRF_POLICY — opt-in block of http(s) navigation to non-public hosts.

The navigation scheme guard (``_reject_unsafe_url``) blocks ``file:``/``javascript:``
but allows ``http(s)`` to *any* host, so a real browser + read tools can reach
cloud metadata (``169.254.169.254``), RFC1918, and loopback and exfiltrate them.
This policy (off by default for back-compat) blocks those literal-IP / localhost /
metadata targets. The blocked IP set, allowlist, and unknown-value fail-safe are
pinned here; ``test_open_url.py`` / ``test_ssrf_navigate_live.py`` cover the wiring.
"""

from __future__ import annotations

import pytest

from octowright import ssrf

POLICY = "OCTOWRIGHT_SSRF_POLICY"
ALLOW = "OCTOWRIGHT_SSRF_ALLOW"

# Each blocked under block-private: metadata link-local, RFC1918 x3, loopback v4/v6,
# ipv4-mapped loopback, unspecified, localhost + subdomain, metadata hostname.
_BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://127.0.0.1:6286/api/sessions",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://0.0.0.0/",
    "http://localhost/",
    "http://db.localhost/",
    "http://metadata.google.internal/",
]

# Public targets — never blocked.
_ALLOWED = [
    "https://example.com/",
    "http://93.184.216.34/",
    "https://octowright.com/path?q=1",
]


class TestPolicyOffByDefault:
    def test_unset_allows_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(POLICY, raising=False)
        # No raise — opt-in, back-compat.
        ssrf.check_navigation_url("http://169.254.169.254/")

    def test_explicit_off_allows_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(POLICY, "off")
        ssrf.check_navigation_url("http://169.254.169.254/")


class TestBlockPrivate:
    @pytest.mark.parametrize("url", _BLOCKED)
    def test_blocks_non_public(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(POLICY, "block-private")
        monkeypatch.delenv(ALLOW, raising=False)
        with pytest.raises(ValueError, match="SSRF"):
            ssrf.check_navigation_url(url)

    @pytest.mark.parametrize("url", _ALLOWED)
    def test_allows_public(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(POLICY, "block-private")
        monkeypatch.delenv(ALLOW, raising=False)
        ssrf.check_navigation_url(url)  # no raise

    def test_non_http_scheme_not_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # data:/about: are scheme-handled by _reject_unsafe_url; SSRF only guards
        # IP-routable http(s). A data: URL must pass the SSRF check untouched.
        monkeypatch.setenv(POLICY, "block-private")
        ssrf.check_navigation_url("data:text/html,<h1>x</h1>")
        ssrf.check_navigation_url("about:blank")


class TestAllowlist:
    def test_allowlisted_private_host_permitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(POLICY, "block-private")
        monkeypatch.setenv(ALLOW, "10.0.0.5, internal.box")
        ssrf.check_navigation_url("http://10.0.0.5/")  # no raise
        ssrf.check_navigation_url("http://internal.box/")
        # A non-allowlisted private host is still blocked.
        with pytest.raises(ValueError, match="SSRF"):
            ssrf.check_navigation_url("http://10.0.0.6/")


class TestWhatwgIpv4Encodings:
    """Every browser engine octowright drives (Chromium/Firefox/WebKit) implements
    the WHATWG URL Standard's IPv4 parser, which accepts decimal/hex/octal/
    shorthand IPv4 forms and resolves them to the canonical address before
    connecting. ``ipaddress.ip_address`` only accepts strict dotted-quad, so
    these alternate encodings must be classified via the WHATWG fallback
    parser rather than slipping through to the hostname check."""

    # (encoded host, canonical dotted-quad it resolves to) — one per numeric
    # encoding class, plus the shorthand form.
    _NUMERIC_ENCODINGS = [
        ("2130706433", "127.0.0.1"),  # decimal
        ("0x7f000001", "127.0.0.1"),  # hex
        ("017700000001", "127.0.0.1"),  # octal
        ("0xA9FEA9FE", "169.254.169.254"),  # hex metadata
        ("2852039166", "169.254.169.254"),  # decimal metadata
        ("127.1", "127.0.0.1"),  # shorthand
        ("0", "0.0.0.0"),  # decimal, unspecified/reserved
    ]

    @pytest.mark.parametrize(("host", "canonical"), _NUMERIC_ENCODINGS)
    def test_blocks_alternate_ipv4_encodings(self, host: str, canonical: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(POLICY, "block-private")
        monkeypatch.delenv(ALLOW, raising=False)
        with pytest.raises(ValueError, match="SSRF"):
            ssrf.check_navigation_url(f"http://{host}/")
        # Sanity: the canonical dotted-quad form is blocked too (same address).
        with pytest.raises(ValueError, match="SSRF"):
            ssrf.check_navigation_url(f"http://{canonical}/")

    def test_public_ip_literal_still_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(POLICY, "block-private")
        monkeypatch.delenv(ALLOW, raising=False)
        ssrf.check_navigation_url("http://8.8.8.8/")  # no raise

    @pytest.mark.parametrize("host", ["example.com", "octowright.com"])
    def test_real_hostname_not_misclassified_as_numeric(self, host: str, monkeypatch: pytest.MonkeyPatch) -> None:
        # The most important non-regression: a real hostname must not be
        # mistaken for a numeric IPv4 host by the WHATWG fallback parser.
        monkeypatch.setenv(POLICY, "block-private")
        monkeypatch.delenv(ALLOW, raising=False)
        ssrf.check_navigation_url(f"http://{host}/")  # no raise

    @pytest.mark.parametrize("host", ["1.2.3.4.5", "999.1.1.1"])
    def test_malformed_numeric_host_does_not_crash(self, host: str, monkeypatch: pytest.MonkeyPatch) -> None:
        # Too many parts, or a part out of range — must never raise anything
        # other than the intentional SSRF ValueError, and must not crash.
        monkeypatch.setenv(POLICY, "block-private")
        monkeypatch.delenv(ALLOW, raising=False)
        ssrf.check_navigation_url(f"http://{host}/")  # no raise — falls through to hostname check, not blocked


class TestUnknownValueFailsSafe:
    def test_typo_fails_to_protective_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The operator set the var on purpose; an unrecognized token honors the
        # protective intent (block-private) rather than silently disabling.
        monkeypatch.setenv(POLICY, "block_private")  # underscore typo
        with pytest.raises(ValueError, match="SSRF"):
            ssrf.check_navigation_url("http://169.254.169.254/")


class TestWiredIntoRejectUnsafeUrl:
    """The single shared guard ``_reject_unsafe_url`` is what every navigation
    entry point (navigate / open_url / launch) and macro replay calls. Proving
    the SSRF check fires there proves it fires at all of them."""

    def test_reject_unsafe_url_enforces_ssrf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.session.core_page_mixin import _reject_unsafe_url

        monkeypatch.setenv(POLICY, "block-private")
        # Scheme-clean but SSRF-blocked → raises with the SSRF reason.
        with pytest.raises(ValueError, match="SSRF"):
            _reject_unsafe_url("http://169.254.169.254/latest/meta-data/")
        # Public host still allowed.
        _reject_unsafe_url("https://example.com/")

    def test_off_by_default_keeps_back_compat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.session.core_page_mixin import _reject_unsafe_url

        monkeypatch.delenv(POLICY, raising=False)
        _reject_unsafe_url("http://169.254.169.254/")  # no raise when policy off
