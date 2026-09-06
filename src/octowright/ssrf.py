# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Opt-in SSRF policy for browser navigation (``OCTOWRIGHT_SSRF_POLICY``).

The navigation scheme guard (`session.core_page_mixin._reject_unsafe_url`) blocks
`file:`/`javascript:`/`chrome:` but lets `http(s)` reach *any* host. A real
browser plus the read tools (`browser_read_markdown`, `browser_snapshot`,
`browser_evaluate`) is therefore a clean exfiltration path to cloud metadata
(`169.254.169.254`), RFC1918 hosts, and the daemon's own loopback dashboard —
reachable by the LLM *and* by a poisoned macro/recording (replay routes through
the same guard).

This module adds an opt-in host policy, gated like the other network opt-outs:

* ``off`` (DEFAULT) — no host check.
* ``block-private`` — refuse `http(s)` to a *literal* IP in any non-public range
  (loopback, link-local incl. the metadata range, RFC1918, multicast, reserved,
  unspecified) and to `localhost` / `*.localhost` / well-known metadata
  hostnames.

`OCTOWRIGHT_SSRF_ALLOW` is a comma-separated host allowlist that overrides the
block for legitimate internal targets. An operator who sets the policy to an
*unrecognized* token gets the protective mode (their intent was clearly to turn
something on), not a silent disable.

Scope note: this guards literal-IP and known-name targets synchronously (no DNS).
A public hostname that *resolves* to a private address (DNS-rebinding SSRF) is not
covered here — that needs a resolving variant and is tracked separately.
"""

from __future__ import annotations

import ipaddress
import os
import unicodedata
from urllib.parse import unquote, urlsplit

from octowright.request_errors import InvalidRequestError

# Tokens that mean "policy disabled". Empty/unset is the default → off.
_OFF = frozenset({"", "off", "0", "false", "no", "never", "none", "disabled"})

# Only IP-routable schemes can reach an internal host; data:/about:/blob: can't,
# and the dangerous file:/javascript:/chrome: schemes are already refused by
# _reject_unsafe_url before this runs.
_CHECKED_SCHEMES = frozenset({"http", "https"})

# Hostnames that resolve to a private/metadata target by convention.
_BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata", "metadata.google.internal"})


def _policy() -> str:
    """Resolve the effective policy. Unset/falsey → ``off``; any other
    unrecognized token → ``block-private`` (honor the operator's intent to
    enable a policy rather than silently disabling on a typo)."""
    raw = os.environ.get("OCTOWRIGHT_SSRF_POLICY", "").strip().lower()
    if raw in _OFF:
        return "off"
    return "block-private"


def policy_enabled() -> bool:
    """Whether any SSRF policy is active. Public so callers that install
    enforcement machinery (the per-hop redirect guard) can skip the work
    entirely on the default ``off`` deployment."""
    return _policy() != "off"


def _allowlist() -> set[str]:
    raw = os.environ.get("OCTOWRIGHT_SSRF_ALLOW", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _ip_is_non_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address an SSRF should not be able to reach. IPv4-mapped
    IPv6 (``::ffff:127.0.0.1``) is unwrapped first so a mapped loopback/private
    address can't slip through the v6 classification."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


_HEX_DIGITS = "0123456789abcdefABCDEF"  # pragma: allowlist secret
_OCTAL_DIGITS = "01234567"
_DECIMAL_DIGITS = "0123456789"


def _digits_in(text: str, alphabet: str) -> bool:
    """True if ``text`` is non-empty and every character is in ``alphabet``."""
    return bool(text) and all(c in alphabet for c in text)


def _parse_hex_part(part: str) -> int | None:
    digits = part[2:]
    return int(digits, 16) if _digits_in(digits, _HEX_DIGITS) else None


def _parse_octal_part(part: str) -> int | None:
    return int(part, 8) if _digits_in(part, _OCTAL_DIGITS) else None


def _parse_decimal_part(part: str) -> int | None:
    return int(part) if _digits_in(part, _DECIMAL_DIGITS) else None


def _parse_ipv4_number(part: str) -> int | None:
    """Parse one dot-separated component of a WHATWG IPv4 host per the URL
    Standard's numeric-part rules: ``0x``/``0X`` prefix → hex, a leading ``0``
    with more than one digit → octal, otherwise decimal. Returns ``None`` if
    ``part`` contains a character invalid for its base (this is how a real
    hostname label like ``"example"`` is distinguished from a numeric part)."""
    if part[:2].lower() == "0x":
        return _parse_hex_part(part)
    if len(part) > 1 and part[0] == "0":
        return _parse_octal_part(part)
    return _parse_decimal_part(part)


def _expand_last_octets(numbers: list[int]) -> list[int] | None:
    """The WHATWG parser's last dotted part absorbs every remaining byte
    (e.g. ``127.1`` == ``127.0.0.1``: 2 parts, last part is a 3-byte value).
    Returns the trailing ``5 - len(numbers)`` big-endian octets, or ``None``
    if the last part doesn't fit in that many bytes."""
    remaining_bytes = 5 - len(numbers)
    last = numbers[-1]
    if last > (256**remaining_bytes) - 1:
        return None
    tail = [0] * remaining_bytes
    value = last
    for i in range(remaining_bytes - 1, -1, -1):
        tail[i] = value & 0xFF
        value >>= 8
    return tail


def _parse_ipv4_parts(host: str) -> list[int] | None:
    """Split ``host`` on ``.`` and parse each part per WHATWG numeric-part
    rules. Returns ``None`` if there are more than 4 parts, any part is
    empty, or any part isn't a valid hex/octal/decimal number."""
    parts = host.split(".")
    if not parts or len(parts) > 4 or any(p == "" for p in parts):
        return None
    numbers: list[int] = []
    for part in parts:
        number = _parse_ipv4_number(part)
        if number is None:
            return None
        numbers.append(number)
    return numbers


def _parse_whatwg_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse ``host`` as a WHATWG URL Standard IPv4 address — the parser every
    browser engine (Chromium/Firefox/WebKit) actually uses before connecting.
    Unlike ``ipaddress.ip_address``, this accepts decimal (``2130706433``),
    hex (``0x7f000001``), octal (``017700000001``), and shorthand
    (``127.1``) forms. Returns ``None`` when ``host`` is not a numeric IPv4
    host at all (e.g. a real hostname), so callers must treat ``None`` as
    "not an IP" rather than "blocked" or "allowed"."""
    numbers = _parse_ipv4_parts(host)
    if numbers is None or any(n > 255 for n in numbers[:-1]):
        return None
    tail = _expand_last_octets(numbers)
    if tail is None:
        return None
    octets = [*numbers[:-1], *tail]
    try:
        return ipaddress.IPv4Address(bytes(octets))
    except ValueError:
        return None


def _host_is_blocked(host: str) -> bool:
    """True if ``host`` is a non-public literal IP, or a blocked hostname
    (``localhost`` / ``*.localhost`` / a well-known metadata name).

    Checks the strict dotted-quad/IPv6 form first, then falls back to the
    WHATWG (browser) IPv4 parser: every engine octowright drives resolves
    decimal/hex/octal/shorthand IPv4 forms (e.g. ``2130706433`` ==
    ``127.0.0.1``) before connecting, so those forms must be classified the
    same as their dotted-quad equivalent rather than mistaken for a hostname.
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        whatwg_ip = _parse_whatwg_ipv4(host)
        if whatwg_ip is not None:
            return _ip_is_non_public(whatwg_ip)
        return host in _BLOCKED_HOSTNAMES or host.endswith(".localhost")
    return _ip_is_non_public(ip)


#: Non-ASCII code points UTS46 maps to ``.`` before a browser parses the host.
#: NFKC alone does NOT fold U+3002, so the map is explicit rather than implied.
_HOST_DOTS = {0x3002: ".", 0xFF0E: ".", 0xFF61: "."}


def normalize_host_for_policy(host: str) -> str:
    """The host the BROWSER will resolve, from the host ``urlsplit`` reports.

    ``urlsplit`` does none of what WHATWG host parsing does, and each gap was a
    live bypass of ``block-private`` (all three confirmed reaching a loopback
    server through headless Chromium while the guard said the navigation was
    allowed):

    * percent-decoding -- ``127.0.0.%31`` is ``127.0.0.1``;
    * UTS46 mapping -- non-ASCII full stops (U+3002, U+FF0E, U+FF61) and
      fullwidth digits both map to their ASCII forms, so a host spelled with
      them is the metadata IP or loopback to a browser and an opaque string
      to urlsplit;
    * the empty trailing label -- ``169.254.169.254.`` is dropped by the WHATWG
      IPv4 parser, while ``_parse_ipv4_parts`` rejected the empty part and let
      the host through as an opaque public name. ``localhost.`` likewise
      resolves to loopback without DNS (Chromium's ``net::IsLocalHostname``).

    Normalizing here rather than at each call site keeps the string test and the
    resolver agreed, which is the whole premise of a synchronous host check.
    """
    decoded = unquote(host)
    mapped = unicodedata.normalize("NFKC", decoded.translate(_HOST_DOTS))
    # One trailing dot is the FQDN root label; the browser drops it before
    # classifying. Strip only that, so `..` stays malformed rather than valid.
    if mapped.endswith(".") and not mapped.endswith(".."):
        mapped = mapped[:-1]
    return mapped.lower()


def check_navigation_url(url: str) -> None:
    """Raise ``ValueError`` if the active SSRF policy refuses ``url``.

    A no-op when the policy is ``off`` (default) or the URL is not http(s).
    Allowlisted hosts always pass.
    """
    if _policy() == "off":
        return
    try:
        parts = urlsplit(url)
    except ValueError:
        # Unparsable here means the downstream navigate will fail anyway; don't
        # mask that with an SSRF error.
        return
    if parts.scheme.lower() not in _CHECKED_SCHEMES:
        return
    host = normalize_host_for_policy(parts.hostname or "")
    if host and host not in _allowlist() and _host_is_blocked(host):
        raise InvalidRequestError(f"SSRF policy block-private refuses navigation to non-public host {host!r}")
