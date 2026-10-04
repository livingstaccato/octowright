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
  unspecified), to `localhost` / `*.localhost` / well-known metadata
  hostnames, and -- in the resolving checks below -- to a name that resolves
  to a non-public address or does not resolve at all.

`OCTOWRIGHT_SSRF_ALLOW` is a comma-separated host allowlist that overrides the
block for legitimate internal targets. An operator who sets the policy to an
*unrecognized* token gets the protective mode (their intent was clearly to turn
something on), not a silent disable.

Three entry points, deliberately split:

* :func:`check_navigation_url` is synchronous and classifies the host *as
  spelled* -- literal IPs in every WHATWG encoding, and the known names above.
  It never touches DNS, so it is safe from any caller.
* :func:`check_navigation_url_resolved` is the async entry point every
  navigation path awaits (tool pre-flight, and in ``ssrf_guard`` every
  navigation request's own URL plus each redirect ``Location`` it follows). After the literal check it resolves a
  non-allowlisted hostname with ``getaddrinfo`` in a worker thread and refuses
  the URL if **any** answer is non-public -- a browser may connect to whichever
  address it likes from a multi-answer set. A name that does not resolve is
  refused (fail closed): an unresolvable name is exactly what a rebinding
  attacker's short-TTL record looks like between answers, and "could not
  check" must not read as "checked and public".
* :func:`check_request_url_cached` is the same check for subresources (every
  image, script, fetch/XHR and WebSocket ``ssrf_guard`` sees -- the first URL
  only: a subresource's redirect hops never reach it, see ``ssrf_guard``), with
  a short per-host verdict cache so a page's hundredth request to a CDN does not pay
  its own ``getaddrinfo``.

What this still cannot close -- the DNS-rebinding window
--------------------------------------------------------
Validation and connection are two separate lookups. octowright resolves the
name, finds it public, and hands the URL to the browser, which performs its
**own** lookup when it connects. An attacker whose record answers a public
address to the first query and ``169.254.169.254`` to the second (TTL 0) still
wins that race. Closing it needs the validated address pinned into the
browser's connection, and Playwright exposes no such control -- there is no
per-request resolver override, and Chromium's ``--host-resolver-rules`` is a
launch-time, whole-browser static map, not a per-navigation pin. So this layer
raises the bar from "name any private host" to "run a rebinding DNS server and
win a timing race"; it is not a guarantee. Deployments that need one must
enforce egress at the network layer (a firewall or an egress proxy that
resolves once and connects to what it resolved).
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import time
import unicodedata
from urllib.parse import unquote, urlsplit

from octowright.request_errors import InvalidRequestError
from octowright.safety_stop import SafetyStop


class SsrfRefusal(SafetyStop, InvalidRequestError):
    """The SSRF policy refused a URL: still the caller's own input, and a `SafetyStop`.

    An ``InvalidRequestError`` so every sink that keeps a refused request out
    of engine health still does; a `SafetyStop` so a macro's ``try`` or
    ``try_each`` cannot suppress it.
    """


# Tokens that mean "policy disabled". Empty/unset is the default → off.
_OFF = frozenset({"", "off", "0", "false", "no", "never", "none", "disabled"})

# Only IP-routable schemes can reach an internal host; data:/about:/blob: can't,
# and the dangerous file:/javascript:/chrome: schemes are already refused by
# _reject_unsafe_url before this runs. ws:/wss: dial a host exactly as http(s)
# does, so they are classified here rather than trusting every caller to
# rewrite them to http(s) first.
_CHECKED_SCHEMES = frozenset({"http", "https", "ws", "wss"})

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


def ip_is_non_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address an SSRF should not be able to reach. IPv4-mapped
    IPv6 (``::ffff:127.0.0.1``) is unwrapped first so a mapped loopback/private
    address can't slip through the v6 classification.

    ``not is_global`` is the base test, not ``is_private``: the shared address
    space 100.64.0.0/10 (RFC 6598) is neither private nor global, and it holds
    Alibaba Cloud's metadata service (100.100.100.200) and every Tailscale
    node. The explicit flags stay because ``is_global`` alone is not a superset
    of them -- on 3.11 it calls multicast 224.0.0.0/4 global. Shared by the
    opt-in navigation policy here and the always-on web discovery check
    (``server.web``), so the two cannot disagree about what "public" means.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


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


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """``host`` as an IP address in any spelling a browser accepts, else ``None``.

    Checks the strict dotted-quad/IPv6 form first, then falls back to the
    WHATWG (browser) IPv4 parser: every engine octowright drives resolves
    decimal/hex/octal/shorthand IPv4 forms (e.g. ``2130706433`` ==
    ``127.0.0.1``) before connecting, so those forms must be classified the
    same as their dotted-quad equivalent rather than mistaken for a hostname.
    The one parse serves both layers, so the literal check and the DNS check
    cannot disagree about whether a host is an address.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return _parse_whatwg_ipv4(host)


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


#: Schemes the WHATWG URL Standard calls "special". After one of them, every
#: ``/`` and ``\`` following the colon is skipped before the authority starts.
_SPECIAL_SCHEMES = frozenset({"http", "https", "ws", "wss", "ftp"})
#: Stripped from both ends before WHATWG parsing (C0 controls and space).
_C0_OR_SPACE = "".join(chr(c) for c in range(0x21))
#: Deleted from anywhere in the URL before WHATWG parsing.
_TAB_AND_NEWLINES = {0x09: None, 0x0A: None, 0x0D: None}


def _with_whatwg_authority(url: str) -> str:
    """``url`` spelled so ``urlsplit`` finds the authority a browser finds.

    For a special scheme WHATWG skips ANY run of ``/`` and ``\\`` after the
    colon -- ``http:127.0.0.1``, ``http:/169.254.169.254`` and
    ``http:///127.0.0.1`` all have a host -- and treats ``\\`` as ``/``, so it
    also ends the authority (``127.0.0.1\\@public.example`` is host
    127.0.0.1). ``urlsplit`` reports no host for the first three and
    ``public.example`` for the last, so the policy had nothing, or the wrong
    thing, to classify. Tab/CR/LF are removed and C0/space trimmed first, as
    WHATWG does, so they cannot hide a scheme or a slash.

    Deliberately the no-base reading. Against a ``base_url`` of the same
    scheme, ``http:foo`` is a relative path instead; reading it as a host here
    can only refuse a URL, never admit one.
    """
    cleaned = url.strip(_C0_OR_SPACE).translate(_TAB_AND_NEWLINES)
    scheme, sep, rest = cleaned.partition(":")
    if not sep or scheme.lower() not in _SPECIAL_SCHEMES:
        return cleaned
    return f"{scheme}://{rest.replace(chr(92), '/').lstrip('/')}"


def _policy_host(url: str) -> str | None:
    """The normalized host of ``url`` the active policy has to classify, if any.

    ``None`` when the policy is off, the scheme is not IP-routable, there is
    no host, or the host is allowlisted.

    A URL ``urlsplit`` cannot parse is REFUSED, not waved through: Python and
    the browser disagree about some of them, and ``http://x]@169.254.169.254/``
    -- which raises here -- is to a browser userinfo ``x]`` on the metadata
    address. "The navigate will fail anyway" was the old reasoning, and false.
    """
    if _policy() == "off":
        return None
    try:
        parts = urlsplit(_with_whatwg_authority(url))
    except ValueError as exc:
        raise SsrfRefusal(
            f"SSRF policy {_policy()} refuses a URL that could not be parsed ({exc}); "
            "a browser may read it as a different host than any check here would"
        ) from None
    if parts.scheme.lower() not in _CHECKED_SCHEMES:
        return None
    host = normalize_host_for_policy(parts.hostname or "")
    if not host or host in _allowlist():
        return None
    return host


def _refuse_as_spelled(host: str) -> bool:
    """Refuse ``host`` if it is a non-public literal IP or a blocked hostname
    (``localhost`` / ``*.localhost`` / a well-known metadata name).

    Returns whether ``host`` is a literal IP -- one that passed here has been
    fully classified, so there is nothing left for DNS to answer.
    """
    ip = _literal_ip(host)
    blocked = (host in _BLOCKED_HOSTNAMES or host.endswith(".localhost")) if ip is None else ip_is_non_public(ip)
    if blocked:
        raise SsrfRefusal(f"SSRF policy block-private refuses navigation to non-public host {host!r}")
    return ip is not None


def check_navigation_url(url: str) -> None:
    """Raise ``ValueError`` if the active SSRF policy refuses ``url``.

    A no-op when the policy is ``off`` (default) or the URL is not http(s) or ws(s).
    Allowlisted hosts always pass.
    """
    host = _policy_host(url)
    if host is not None:
        _refuse_as_spelled(host)


#: The resolver, held at module level so tests can substitute answers without
#: patching the process-wide ``socket`` module.
_getaddrinfo = socket.getaddrinfo


def _resolved_non_public(host: str) -> list[str]:
    """Resolve ``host`` and return every answer that is not public.

    Raises ``OSError`` (``socket.gaierror`` included) or ``UnicodeError`` when
    the name cannot be resolved; the caller turns that into a refusal. Blocking:
    run it off the event loop.
    """
    infos = _getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    flagged: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]  # drop an IPv6 zone id
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            flagged.append(address)  # an answer we cannot classify is not public
            continue
        if ip_is_non_public(ip):
            flagged.append(address)
    return flagged


async def _resolution_refusal(host: str) -> str | None:
    """Why ``host`` is refused once resolved, or ``None`` when every answer is public.

    The lookup runs in a worker thread: ``getaddrinfo`` blocks, and every
    caller is on the daemon's event loop.
    """
    try:
        flagged = await asyncio.to_thread(_resolved_non_public, host)
    except (OSError, UnicodeError) as exc:
        return (
            f"SSRF policy block-private refuses {host!r}: the host could not be resolved "
            f"({exc}); an unresolvable name is refused rather than assumed public"
        )
    if flagged:
        return (
            f"SSRF policy block-private refuses {host!r}: it resolves to non-public address(es) {sorted(set(flagged))}"
        )
    return None


async def check_navigation_url_resolved(url: str) -> None:
    """:func:`check_navigation_url`, then refuse a host that RESOLVES non-public.

    See the module docstring for the rebinding window this cannot close.
    """
    host = _policy_host(url)
    if host is None or _refuse_as_spelled(host):
        return
    refusal = await _resolution_refusal(host)
    if refusal is not None:
        raise SsrfRefusal(refusal)


#: How long a subresource host's verdict is reused. A page issues dozens of
#: requests to the same few hosts; resolving each one would put a thread-pool
#: ``getaddrinfo`` in front of every image. Short, because a cached "public"
#: is exactly what a rebinding record wants to outlive -- though the browser's
#: own lookup already leaves that window open (module docstring).
SUBRESOURCE_VERDICT_TTL_SECONDS = 30.0
#: Bound on distinct cached hosts, so a page that requests a fresh random
#: subdomain per request cannot grow the cache without limit.
SUBRESOURCE_VERDICT_MAX_HOSTS = 1024

_subresource_verdicts: dict[str, tuple[float, str | None]] = {}
_subresource_lookups: dict[str, asyncio.Task[str | None]] = {}


async def check_request_url_cached(url: str) -> None:
    """:func:`check_navigation_url_resolved` for a subresource, with a per-host TTL cache.

    Concurrent requests to a host whose verdict is not cached share one
    lookup rather than each starting their own.
    """
    host = _policy_host(url)
    if host is None or _refuse_as_spelled(host):
        return
    now = time.monotonic()
    cached = _subresource_verdicts.get(host)
    if cached is None or cached[0] <= now:
        refusal = await _shared_lookup(host)
        if len(_subresource_verdicts) >= SUBRESOURCE_VERDICT_MAX_HOSTS:
            _subresource_verdicts.pop(next(iter(_subresource_verdicts)))
        _subresource_verdicts[host] = (now + SUBRESOURCE_VERDICT_TTL_SECONDS, refusal)
    else:
        refusal = cached[1]
    if refusal is not None:
        raise SsrfRefusal(refusal)


async def _shared_lookup(host: str) -> str | None:
    task = _subresource_lookups.get(host)
    # A task left by another event loop (a test's) cannot be awaited here.
    if task is None or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.ensure_future(_resolution_refusal(host))
        _subresource_lookups[host] = task
        task.add_done_callback(
            lambda done: _subresource_lookups.pop(host, None) if _subresource_lookups.get(host) is done else None
        )
    return await asyncio.shield(task)
