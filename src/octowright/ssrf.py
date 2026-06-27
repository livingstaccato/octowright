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

* ``off`` (DEFAULT) — no host check; full back-compat.
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
from urllib.parse import urlsplit

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


def _host_is_blocked(host: str) -> bool:
    """True if ``host`` is a non-public literal IP, or a blocked hostname
    (``localhost`` / ``*.localhost`` / a well-known metadata name)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in _BLOCKED_HOSTNAMES or host.endswith(".localhost")
    return _ip_is_non_public(ip)


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
    host = (parts.hostname or "").lower()
    if host and host not in _allowlist() and _host_is_blocked(host):
        raise ValueError(f"SSRF policy block-private refuses navigation to non-public host {host!r}")
